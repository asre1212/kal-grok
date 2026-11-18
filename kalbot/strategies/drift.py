"""Price-drift strategies on snapshot-to-snapshot changes.

fair = mid_t + kappa * (mid_t - mid_{t-1}), only when |change| is large vs the market's own
volatility (z >= 1.5). kappa > 0 = momentum (under-reaction), kappa < 0 = mean reversion
(over-reaction). The weekly evaluator sets kappa from the pooled lag-1 autocorrelation of
price changes (the optimal linear one-step predictor) within each variant's bounds, and the
allocation bandit decides which variant deserves capital.
"""
import statistics

from .base import Strategy, generic_tune
from ..config import STRATEGY_PARAMS


def mids(hist):
    out = []
    for row in hist:
        _, yb, ya = row[0], row[1], row[2]
        if yb > 0 and 0 < ya < 100:
            out.append((yb + ya) / 200)
    return out


def drift_signal(hist, kappa, z_min=1.5, min_move=0.03):
    ms = mids(hist)
    if len(ms) < 4:
        return None
    diffs = [b - a for a, b in zip(ms, ms[1:])]
    r = diffs[-1]
    sd = statistics.pstdev(diffs[:-1]) or 0.01
    if abs(r) < min_move or abs(r) / max(sd, 0.005) < z_min:
        return None
    return min(0.98, max(0.02, ms[-1] + kappa * r))


def pooled_autocorr(history, min_len=5):
    num = den = 0.0
    for hist in history.values():
        ms = mids(hist)
        if len(ms) < min_len:
            continue
        d = [b - a for a, b in zip(ms, ms[1:])]
        for a, b in zip(d, d[1:]):
            num += a * b
        den += sum(x * x for x in d[:-1])
    return (num / den) if den > 0 else None


class Drift(Strategy):
    max_new = 15

    def __init__(self, name):
        self.name = name
        self.description = ("Momentum: follow large moves between snapshots." if name == "momentum"
                            else "Mean reversion: fade large moves between snapshots.")

    def manage(self, eng):
        P = eng.params[self.name]
        for k, pos in list(self.my_positions(eng).items()):
            pos["runs_held"] = pos.get("runs_held", 0) + 1
            m = eng.markets.get(pos["ticker"])
            if not m:
                continue
            if pos["runs_held"] >= P["hold"]:
                eng.exit_position(k, f"hold {pos['runs_held']} runs")

    def run(self, eng):
        P = eng.params[self.name]
        if not eng.history:
            return
        cands = []
        for t, hist in eng.history.items():
            m = eng.markets.get(t)
            if not m or not m.get("exp") or (m["exp"] - eng.now) < 86400:
                continue
            # include the CURRENT snapshot: history is only updated after trading, so without it the
            # "latest move" is a cycle old and fair value is anchored to a stale mid
            cur = [int((eng.now or 0) // 3600), int(round(m["yb"] * 100)), int(round(m["ya"] * 100)), int(m.get("vol", 0))]
            fair = drift_signal(list(hist) + [cur] if not hist or hist[-1][0] != cur[0] else list(hist[:-1]) + [cur],
                                P["kappa"])
            if fair is None:
                continue
            if fair - m["ya"] > 0:
                cands.append((fair - m["ya"], m, "yes", fair))
            elif (1 - fair) - m["na"] > 0:
                cands.append(((1 - fair) - m["na"], m, "no", 1 - fair))
        cands.sort(key=lambda c: -c[0])
        n = 0
        for edge, m, side, p in cands:
            if n >= self.max_new or edge < P["min_edge"]:
                break
            if eng.take(self.name, m, side, p, P["min_edge"], note=f"kappa={P['kappa']:+.2f}"):
                n += 1

    def tune(self, params, metrics, ctx):
        new, reasons = generic_tune(self.name, params, metrics)
        rho = ctx.get("autocorr")
        lo, hi = STRATEGY_PARAMS[self.name]["kappa"][1:]
        if rho is not None:
            k = round(min(hi, max(lo, rho)), 3)
            reasons.append(f"kappa {params['kappa']} -> {k}: pooled lag-1 autocorr of price changes = {rho:+.3f}")
            new["kappa"] = k
        return new, reasons
