"""Favorite-longshot bias strategies.

True prob ~ sigmoid(alpha + beta * logit(mid)), beta > 1 (fitted weekly from settlements).
  LongshotFade: buy NO on overpriced longshots (YES mid <= p_max) and YES on the mirror-image
                favorites, horizon 2..max_days days.
  ExpiryConvergence: near expiry (<= max_hours) buy the favorite side (mid >= p_min) — prices
                of near-certain outcomes converge to $1 and favorites are underpriced.
"""
from .base import Strategy, generic_tune
from ..models import debias, fit_beta
from ..config import STRATEGY_PARAMS


def _mid(m):
    if m["yb"] <= 0 or m["ya"] <= 0 or m["ya"] >= 1:
        return None
    return (m["yb"] + m["ya"]) / 2


class LongshotFade(Strategy):
    name = "longshot_fade"
    description = "Fade overpriced longshots (buy NO) / buy underpriced favorites, net of fees."
    max_new = 25

    def candidates(self, eng):
        P = eng.params[self.name]
        alpha = eng.state.get("calibration_fit", {}).get("alpha", 0.0)
        out = []
        for m in eng.markets.values():
            mid = _mid(m)
            if mid is None or m["ya"] - m["yb"] > 0.04 or not m.get("exp"):
                continue
            days = (m["exp"] - eng.now) / 86400
            if days < 2 or days > P["max_days"]:
                continue
            p = debias(mid, P["beta"], alpha)
            if 0.02 <= mid <= P["p_max"]:
                out.append(((1 - p) - m["na"], m, "no", 1 - p))
            elif 1 - P["p_max"] <= mid <= 0.98:
                out.append((p - m["ya"], m, "yes", p))
        out.sort(key=lambda t: -t[0])
        return out

    def run(self, eng):
        P = eng.params[self.name]
        n = 0
        for edge, m, side, p in self.candidates(eng):
            if n >= self.max_new or edge < P["min_edge"]:
                break
            if eng.take(self.name, m, side, p, P["min_edge"], note="FLB"):
                n += 1

    def tune(self, params, metrics, ctx):
        new, reasons = generic_tune(self.name, params, metrics)
        cal = ctx.get("calibration", [])
        rows = [r for r in cal if r.get("h", 999) > 48]
        a, b, n = fit_beta([r["p"] for r in rows], [r["y"] for r in rows], prior_beta=params["beta"])
        lo, hi = STRATEGY_PARAMS[self.name]["beta"][1:]
        if n >= 30:
            nb = round(min(hi, max(lo, b)), 3)
            reasons.append(f"beta {params['beta']} -> {nb}: refit on {n} settled markets (alpha={a:.3f})")
            new["beta"] = nb
        else:
            reasons.append(f"beta unchanged: only {n} calibration samples")
        return new, reasons


class ExpiryConvergence(Strategy):
    name = "expiry_convergence"
    description = "Buy near-certain favorites shortly before expiry (time-decay convergence to $1)."
    max_new = 30

    def run(self, eng):
        P = eng.params[self.name]
        alpha = eng.state.get("calibration_fit", {}).get("alpha_short", 0.0)
        cands = []
        for m in eng.markets.values():
            mid = _mid(m)
            if mid is None or not m.get("exp") or m["ya"] - m["yb"] > 0.03:
                continue
            hrs = (m["exp"] - eng.now) / 3600
            if hrs < 1 or hrs > P["max_hours"]:
                continue
            p = debias(mid, P["beta"], alpha)
            if mid >= P["p_min"] and m["ya"] < 0.99:
                cands.append((p - m["ya"], m, "yes", p))
            elif mid <= 1 - P["p_min"] and m["na"] < 0.99:
                cands.append(((1 - p) - m["na"], m, "no", 1 - p))
        cands.sort(key=lambda t: -t[0])
        n = 0
        for edge, m, side, p in cands:
            if n >= self.max_new or edge < P["min_edge"]:
                break
            if eng.take(self.name, m, side, p, P["min_edge"], note=f"exp<{P['max_hours']}h", urgent=True):
                n += 1

    def tune(self, params, metrics, ctx):
        new, reasons = generic_tune(self.name, params, metrics)
        rows = [r for r in ctx.get("calibration", []) if r.get("h", 999) <= 48]
        a, b, n = fit_beta([r["p"] for r in rows], [r["y"] for r in rows], prior_beta=params["beta"])
        lo, hi = STRATEGY_PARAMS[self.name]["beta"][1:]
        if n >= 30:
            nb = round(min(hi, max(lo, b)), 3)
            reasons.append(f"beta {params['beta']} -> {nb}: refit on {n} short-horizon settlements (alpha={a:.3f})")
            new["beta"] = nb
        else:
            reasons.append(f"beta unchanged: only {n} short-horizon samples")
        return new, reasons
