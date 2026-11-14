"""Rolling price history + calibration samples harvested from settlements."""
from . import config


def update_history(history, markets, held, now):
    """Append [hour, yes_bid_c, yes_ask_c, volume] for the top-N liquid markets + held ones."""
    N = config.UNIVERSE["history_max_markets"]
    L = config.UNIVERSE["history_len"]
    ranked = sorted(markets.values(), key=lambda m: -m["v24"])
    keep = {m["t"] for m in ranked[:N]} | set(held) | set(history.keys())
    h = int(now // 3600)
    for t in keep:
        m = markets.get(t)
        if not m:
            continue
        row = [h, int(round(m["yb"] * 100)), int(round(m["ya"] * 100)), int(m["vol"])]
        rows = history.setdefault(t, [])
        if rows and rows[-1][0] == h:
            rows[-1] = row
        else:
            rows.append(row)
        del rows[:-L]
    # bound size: drop least-liquid tracked markets beyond 1.3N
    if len(history) > int(N * 1.3):
        live = sorted((t for t in history if t in markets), key=lambda t: -markets[t]["v24"])
        allowed = set(live[:N]) | set(held)
        for t in list(history):
            if t in markets and t not in allowed:
                del history[t]
    return history


def harvest_calibration(history, markets, client, calibration, now, max_rows=20000):
    """Markets that left the open universe: if settled, turn their recorded mids into
    (price, outcome, hours-to-close) samples. Returns number of samples added."""
    gone = [t for t in history if t not in markets]
    if not gone:
        return 0
    info = client.markets_by_tickers(gone) if client else {}
    added = 0
    for t in gone:
        m = info.get(t)
        rows = history.get(t, [])
        if m and m.get("status") in ("settled", "finalized") and m.get("result") in ("yes", "no"):
            y = 1 if m["result"] == "yes" else 0
            from .api import parse_ts
            close = parse_ts(m.get("close_time")) or now
            long_rows = [r for r in rows if (close - r[0] * 3600) / 3600 > 48 and r[1] > 0 and r[2] < 100]
            short_rows = [r for r in rows if 1 <= (close - r[0] * 3600) / 3600 <= 48 and r[1] > 0 and r[2] < 100]
            for pick in (long_rows[-1:] + short_rows[-1:]):
                mid = (pick[1] + pick[2]) / 200
                if 0.01 <= mid <= 0.99:
                    calibration.append({"p": round(mid, 3), "y": y, "h": round((close - pick[0] * 3600) / 3600, 1),
                                        "t": t, "src": "hist"})
                    added += 1
            del history[t]
        elif m and m.get("status") in ("active", "open"):
            if rows and now / 3600 - rows[-1][0] > 72:
                del history[t]
        elif not m or (rows and now / 3600 - rows[-1][0] > 24 * 14):
            history.pop(t, None)
    del calibration[:-max_rows]
    return added


def bootstrap_calibration(client, calibration, now, days=4, max_pages=12):
    """Seed short-horizon calibration from recently settled markets' previous-day quotes."""
    n = 0
    from .api import f
    for m in client.settled_markets(now - days * 86400, max_pages=max_pages):
        if m.get("result") not in ("yes", "no"):
            continue
        pb, pa = f(m.get("previous_yes_bid_dollars")), f(m.get("previous_yes_ask_dollars"))
        if pb <= 0 or pa >= 1 or pa - pb > 0.05 or f(m.get("volume_fp")) < 100:
            continue
        calibration.append({"p": round((pb + pa) / 2, 3), "y": 1 if m["result"] == "yes" else 0,
                            "h": 12, "t": m["ticker"], "src": "prev"})
        n += 1
    return n
