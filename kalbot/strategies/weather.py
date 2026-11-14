"""External-signal model: Kalshi daily-high temperature markets vs the free NWS forecast.

For each KXHIGH* event (tomorrow or later), the NWS point forecast high at the settlement
station gives mu; forecast error is modelled as Normal(0, sigma(lead) * sigma_scale), with
sigma ~2.8F day-ahead growing with lead time. Bucket probabilities use a continuity
correction on integer temperatures. Trades only where model prob beats ask by min_edge.
"""
import re
from datetime import datetime, timedelta, timezone

import requests

from .base import Strategy, generic_tune
from ..config import NWS_BASE, USER_AGENT, STRATEGY_PARAMS
from ..models import temp_bucket_prob

# settlement station (NWS CLI site) -> approx lat, lon
STATIONS = {
    "KXHIGHNY": (40.7790, -73.9692), "KXHIGHCHI": (41.7868, -87.7522), "KXHIGHAUS": (30.1945, -97.6699),
    "KXHIGHDEN": (39.8466, -104.6562), "KXHIGHLAX": (33.9382, -118.3866), "KXHIGHMIA": (25.7881, -80.3169),
    "KXHIGHPHIL": (39.8733, -75.2268), "KXHIGHTATL": (33.6301, -84.4418), "KXHIGHTBOS": (42.3606, -71.0097),
    "KXHIGHTDAL": (32.8998, -97.0403), "KXHIGHTDC": (38.8483, -77.0342), "KXHIGHTEWR": (40.6825, -74.1694),
    "KXHIGHTHOU": (29.6375, -95.2825), "KXHIGHTLV": (36.0719, -115.1634), "KXHIGHTMIN": (44.8831, -93.2289),
    "KXHIGHTNOLA": (29.9933, -90.2511), "KXHIGHTOKC": (35.3889, -97.6006), "KXHIGHTPHX": (33.4278, -112.0037),
    "KXHIGHTSAN": (32.7336, -117.1831), "KXHIGHTSATX": (29.5443, -98.4839), "KXHIGHTSDF": (38.1811, -85.7392),
    "KXHIGHTSEA": (47.4447, -122.3144), "KXHIGHTSFO": (37.6197, -122.3656), "KXHIGHTTTN": (40.2767, -74.8156),
}
SIGMA_BY_LEAD = {1: 2.8, 2: 3.3, 3: 3.9, 4: 4.5}
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def event_date(event_ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not m or m.group(2) not in MONTHS:
        return None
    return datetime(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))).date()


class NWS:
    def __init__(self, cache):
        self.cache = cache            # persisted: series -> forecast url
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
        self.forecasts = {}

    def highs(self, series):
        """{date: (temp_F, local_today_date)} from daytime forecast periods."""
        if series in self.forecasts:
            return self.forecasts[series]
        out = {}
        try:
            url = self.cache.get(series)
            if not url:
                lat, lon = STATIONS[series]
                r = self.s.get(f"{NWS_BASE}/points/{lat},{lon}", timeout=20)
                r.raise_for_status()
                url = r.json()["properties"]["forecast"]
                self.cache[series] = url
            r = self.s.get(url, timeout=20)
            r.raise_for_status()
            props = r.json()["properties"]
            for per in props.get("periods", []):
                if not per.get("isDaytime"):
                    continue
                st = datetime.fromisoformat(per["startTime"])
                t = per["temperature"]
                if per.get("temperatureUnit") == "C":
                    t = t * 9 / 5 + 32
                out[st.date()] = float(t)
            gen = props.get("generatedAt") or props.get("updateTime")
            tz = datetime.fromisoformat(props["periods"][0]["startTime"]).tzinfo
            out["_today"] = datetime.now(tz).date()
            out["_gen"] = gen
        except Exception as e:  # network/API issues: skip this city
            out = {"_error": str(e)[:120]}
        self.forecasts[series] = out
        return out


class WeatherNWS(Strategy):
    name = "weather_nws"
    description = "Daily high-temperature buckets priced from the free NWS forecast (Normal error model)."

    def run(self, eng, nws=None):
        P = eng.params[self.name]
        nws = nws or NWS(eng.state.setdefault("nws_cache", {}))
        trades = evaluated = 0
        notes = []
        for ev, ms in eng.events.items():
            series = ms[0]["s"]
            if series not in STATIONS:
                continue
            d = event_date(ev)
            fc = nws.highs(series)
            if not d or "_today" not in fc or d not in fc:
                continue
            lead = (d - fc["_today"]).days
            if lead < 1 or eng.holds(self.name, event=ev):
                continue  # same-day: the observed high so far dominates; skip
            sigma = SIGMA_BY_LEAD.get(lead, 5.0) * P["sigma_scale"]
            mu = fc[d]
            evaluated += 1
            best = []
            for m in ms:
                p_model = temp_bucket_prob(mu, sigma, m["st"], m["fl"], m["cap"])
                if p_model is None or m["yb"] <= 0 or not (0 < m["ya"] < 1):
                    continue
                # ensemble: shrink the NWS model toward the market mid (the market aggregates
                # other forecasts/obs); weight learned weekly from Brier(model) vs Brier(market)
                w = P["model_weight"]
                p = w * p_model + (1 - w) * (m["yb"] + m["ya"]) / 2
                p = min(0.97, max(0.03, p))
                if 0 < m["ya"] < 1:
                    best.append((p - m["ya"], m, "yes", p))
                if 0 < m["na"] < 1:
                    best.append(((1 - p) - m["na"], m, "no", 1 - p))
            best.sort(key=lambda b: -b[0])
            n_ev = 0
            for edge, m, side, p in best:
                if n_ev >= 2 or edge < P["min_edge"]:
                    break
                if eng.take(self.name, m, side, p, P["min_edge"], note=f"NWS {mu:.0f}F sd{sigma:.1f} lead{lead}d"):
                    n_ev += 1
                    trades += 1
        eng.state.setdefault("_run_notes", {})[self.name] = f"{evaluated} city-days evaluated, {trades} trades"

    def tune(self, params, metrics, ctx):
        new, reasons = generic_tune(self.name, params, metrics)
        cal = metrics.get("calibration_gap")   # mean(p_model) - hit rate on settled trades
        lo, hi = STRATEGY_PARAMS[self.name]["sigma_scale"][1:]
        if cal is not None and metrics.get("settled_trades", 0) >= 8:
            if cal > 0.05:
                new["sigma_scale"] = round(min(hi, params["sigma_scale"] * 1.1), 3)
                reasons.append(f"sigma_scale -> {new['sigma_scale']}: overconfident by {cal:.2f}")
            elif cal < -0.05:
                new["sigma_scale"] = round(max(lo, params["sigma_scale"] * 0.93), 3)
                reasons.append(f"sigma_scale -> {new['sigma_scale']}: underconfident by {-cal:.2f}")
        bm, bk = metrics.get("brier"), metrics.get("brier_market")
        wlo, whi = STRATEGY_PARAMS[self.name]["model_weight"][1:]
        if bm is not None and bk is not None and metrics.get("settled_trades", 0) >= 8:
            if bm < bk - 0.005:
                new["model_weight"] = round(min(whi, params["model_weight"] + 0.05), 3)
                reasons.append(f"model_weight -> {new['model_weight']}: blended forecast Brier {bm:.3f} beat market {bk:.3f}")
            elif bm > bk + 0.005:
                new["model_weight"] = round(max(wlo, params["model_weight"] - 0.05), 3)
                reasons.append(f"model_weight -> {new['model_weight']}: market Brier {bk:.3f} beat forecast {bm:.3f}")
        return new, reasons
