"""Read-only client for Kalshi's PUBLIC market-data API (no auth, no orders)."""
import time
from datetime import datetime, timezone
import requests

from . import config


def f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class KalshiPublic:
    """Polite client: <= ~8 req/s, retries with backoff on 429/5xx."""

    def __init__(self, base=config.API_BASE, min_interval=0.12, session=None):
        self.base = base
        self.min_interval = min_interval
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        self._last = 0.0
        self.calls = 0

    def get(self, path, params=None, tries=6):
        for i in range(tries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(self.base + path, params=params, timeout=30)
                self.calls += 1
            except requests.RequestException:
                time.sleep(1.5 * (i + 1))
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30, 1.5 * 2 ** i))
                continue
            r.raise_for_status()
        raise RuntimeError(f"Kalshi API failed after retries: {path}")

    # ---- endpoints ----
    def iter_open_events(self, page_limit=200, max_pages=400):
        """Yield open events with nested markets (cursor paging)."""
        cursor = None
        for _ in range(max_pages):
            p = {"status": "open", "limit": page_limit, "with_nested_markets": "true"}
            if cursor:
                p["cursor"] = cursor
            d = self.get("/events", p) or {}
            evs = d.get("events") or []
            for e in evs:
                yield e
            cursor = d.get("cursor")
            if not cursor or not evs:
                break

    def markets_by_tickers(self, tickers, chunk=100):
        out = {}
        tickers = list(tickers)
        for i in range(0, len(tickers), chunk):
            d = self.get("/markets", {"tickers": ",".join(tickers[i:i + chunk]), "limit": 1000}) or {}
            for m in d.get("markets") or []:
                out[m["ticker"]] = m
        return out

    def orderbook(self, ticker):
        d = self.get(f"/markets/{ticker}/orderbook") or {}
        ob = d.get("orderbook_fp") or d.get("orderbook") or {}
        def lv(key_new, key_old):
            raw = ob.get(key_new)
            if raw is not None:
                return sorted(((f(p), f(q)) for p, q in raw), key=lambda t: -t[0])
            raw = ob.get(key_old) or []   # legacy cents format
            return sorted(((f(p) / 100, f(q)) for p, q in raw), key=lambda t: -t[0])
        return {"yes_bids": lv("yes_dollars", "yes"), "no_bids": lv("no_dollars", "no")}

    def all_series(self):
        d = self.get("/series", {"limit": 10000}) or {}
        return d.get("series") or []

    def settled_markets(self, min_close_ts, max_pages=25):
        cursor = None
        for _ in range(max_pages):
            p = {"status": "settled", "limit": 1000, "mve_filter": "exclude", "min_close_ts": int(min_close_ts)}
            if cursor:
                p["cursor"] = cursor
            d = self.get("/markets", p) or {}
            ms = d.get("markets") or []
            for m in ms:
                yield m
            cursor = d.get("cursor")
            if not cursor or not ms:
                break


def compact_market(m, event=None, fee_info=None):
    """Reduce a raw market dict to the fields the bot uses (prices in dollars)."""
    e = event or {}
    series = e.get("series_ticker") or m.get("event_ticker", "").split("-")[0]
    ft, mult = (fee_info or {}).get(series, ("quadratic", 1.0))
    exp_ts = parse_ts(m.get("expected_expiration_time")) or parse_ts(m.get("close_time"))
    title = m.get("title") or ""
    sub = m.get("yes_sub_title") or ""
    if sub and sub not in title:
        title = f"{title} — {sub}"
    return {
        "t": m["ticker"], "e": m.get("event_ticker"), "s": series,
        "cat": e.get("category") or "Other", "ti": title[:140],
        "et": (e.get("title") or "")[:140],
        "yb": f(m.get("yes_bid_dollars")), "ya": f(m.get("yes_ask_dollars")),
        "nb": f(m.get("no_bid_dollars")), "na": f(m.get("no_ask_dollars")),
        "ybs": f(m.get("yes_bid_size_fp")), "yas": f(m.get("yes_ask_size_fp")),
        "lp": f(m.get("last_price_dollars")), "v24": f(m.get("volume_24h_fp")),
        "vol": f(m.get("volume_fp")), "oi": f(m.get("open_interest_fp")),
        "close": parse_ts(m.get("close_time")), "exp": exp_ts,
        "st": m.get("strike_type"), "fl": m.get("floor_strike"), "cap": m.get("cap_strike"),
        "me": bool(e.get("mutually_exclusive")), "status": m.get("status"),
        "ft": ft, "fm": mult, "result": m.get("result") or "",
        "sv": m.get("settlement_value_dollars"),
        "cs": _cs(m.get("custom_strike")),
    }


def _cs(c):
    if not c or not isinstance(c, dict):
        return ""
    return "|".join(f"{k}={c[k]}" for k in sorted(c))[:80]


def now_ts():
    return datetime.now(timezone.utc).timestamp()
