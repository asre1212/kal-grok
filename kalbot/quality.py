"""Probability-of-profit (POP) scoring and entry gating.

For every candidate entry we estimate the chance the trade ends in profit AFTER fees, plus its
expected net profit per contract (EV). The main book only opens candidates that clear
SELECT['min_pop'], SELECT['min_ev'] and SELECT['min_edge_to_cost'], ranks the survivors by
EV x POP, opens the top SELECT['max_new_per_cycle'], and sizes high-POP trades larger.

POP for one candidate (side s of market m, entry price c, entry fee f_in, exit fee f_out):
  1. p_mkt   = market mid for side s.  p_model = the strategy's win probability
               (NWS model for weather; implied probability + estimated edge for the others).
  2. Trust:  p = p_mkt + w * (p_model - p_mkt)          w = model_trust (weather: 1.0, already blended)
  3. Track record: p *= h, where h = posterior / predicted hit rate of the strategy's recent
               closed trades with a Beta prior of `prior_strength` pseudo-trades centred on what
               was predicted (so h -> 1 with little data), clipped to hit_factor_bounds.
  4. Time:   the edge (p - p_mkt) is cut by up to time_shrink, ramping from 7 to 120 days to close.
  5. Liquidity: p -= spread_penalty * max(0, spread - 3c) (+ thin_penalty for thin markets).
  6. Settlement trades profit iff side s wins (cost < $1), so POP = p.
     Drift trades exit at the mark a few snapshots later, so POP = Phi(EV / sigma) with
     sigma = max(horizon_sigma, |p_model - p_mkt|) (and w = 0.5 for drift signals).
  EV = p - c - f_in - f_out, where f_out = taker fee at p when the trade is exited before
  settlement (always for drift trades; half-weighted when a take-profit/stop rule is active;
  settlement itself is free). Locked-in arbitrage baskets get POP = arb_pop (0.99).
"""
import math

from . import config
from .fees import fee_per_contract

HORIZON_STRATS = {"momentum", "mean_reversion"}
# weather is already blended with the market; drift signals are noisy one-step predictions
MODEL_TRUST = {"weather_nws": 1.0, "momentum": 0.5, "mean_reversion": 0.5}


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def hit_stats(closed, window=None):
    """Per strategy: n trades with a prediction, hits (pnl > 0) and summed predicted POP."""
    window = window or config.SELECT["hit_window"]
    out = {}
    for c in (closed or [])[-window:]:
        pred = c.get("pop") if c.get("pop") is not None else c.get("p_model")
        if pred is None:
            continue
        s = out.setdefault(c["strategy"], {"n": 0, "hits": 0, "pred": 0.0})
        s["n"] += 1
        s["hits"] += 1 if c.get("pnl", 0) > 0 else 0
        s["pred"] += float(pred)
    return out


def hit_factor(strat, stats):
    """Shrunk realized/predicted hit-rate ratio (Beta prior centred on the prediction)."""
    s = (stats or {}).get(strat)
    if not s or s["n"] <= 0:
        return 1.0
    Q = config.SELECT
    pbar = _clip(s["pred"] / s["n"], 0.02, 0.98)
    k = Q["prior_strength"]
    post = (s["hits"] + k * pbar) / (s["n"] + k)
    lo, hi = Q["hit_factor_bounds"]
    return _clip(post / pbar, lo, hi)


def side_mid(m, side):
    yb, ya = m.get("yb", 0), m.get("ya", 0)
    mid = (yb + ya) / 2 if (yb > 0 and 0 < ya < 1) else (ya or yb)
    return mid if side == "yes" else 1 - mid


def estimate(strat, m, side, p_model, price, entry_fee, now=None, stats=None, early_exit=False):
    """Return dict(pop, ev, edge_to_cost, p_adj, cost, exit_fee, hf)."""
    Q = config.SELECT
    p_mkt = side_mid(m, side)
    w = MODEL_TRUST.get(strat, Q["model_trust"])
    p = p_mkt + w * (p_model - p_mkt)
    hf = hit_factor(strat, stats)
    p = p * hf
    t = m.get("exp") or m.get("close")
    days = ((t - now) / 86400) if (t and now) else 0
    tf = 1 - Q["time_shrink"] * _clip((days - 7) / 113, 0, 1)
    p = p_mkt + (p - p_mkt) * tf
    spread = max(0.0, m.get("ya", 0) - m.get("yb", 0)) if m.get("yb", 0) > 0 else 0.10
    p -= Q["spread_penalty"] * max(0.0, spread - 0.03)
    if m.get("v24", 0) < 50 and m.get("oi", 0) < 500:
        p -= Q["thin_penalty"]
    p = _clip(p, 0.01, 0.99)
    fm = m.get("fm", 1.0)
    if strat in HORIZON_STRATS:
        exit_fee = fee_per_contract(p, fm)
    elif early_exit:
        exit_fee = 0.5 * fee_per_contract(p, fm)
    else:
        exit_fee = 0.0
    cost = price + entry_fee
    ev = p - cost - exit_fee
    if strat in HORIZON_STRATS:
        # the predicted move's own size sets the noise scale: a 1-sigma call can't be a sure thing
        sigma = max(Q["horizon_sigma"], abs(p_model - p_mkt))
        pop = norm_cdf(ev / sigma)
    else:
        pop = p if cost < 1 else 0.0
    return {"pop": round(_clip(pop, 0.0, 1.0), 4), "ev": round(ev, 4),
            "edge_to_cost": round(ev / cost, 4) if cost > 0 else 0.0,
            "p_adj": round(p, 4), "cost": round(cost, 4), "exit_fee": round(exit_fee, 4), "hf": round(hf, 3)}


def gate(q, min_pop):
    """None if the candidate passes, else the rejection reason."""
    Q = config.SELECT
    if q["pop"] < min_pop:
        return "low_pop"
    if q["ev"] < Q["min_ev"]:
        return "low_ev"
    if q["edge_to_cost"] < Q["min_edge_to_cost"]:
        return "low_edge_to_cost"
    return None


def kelly_mult(pop, min_pop):
    """1.0 at the threshold, rising linearly to kelly_boost_max at kelly_boost_pop and above."""
    Q = config.SELECT
    if min_pop is None or pop is None:
        return 1.0
    top = Q["kelly_boost_pop"]
    if top <= min_pop:
        return Q["kelly_boost_max"] if pop >= min_pop else 1.0
    return 1.0 + (Q["kelly_boost_max"] - 1.0) * _clip((pop - min_pop) / (top - min_pop), 0.0, 1.0)


def score(q):
    return q["ev"] * q["pop"]


def calibration(closed):
    """Avg predicted POP vs realized hit rate on closed trades that carried a POP."""
    xs = [c for c in (closed or []) if c.get("pop") is not None]
    if not xs:
        return {"n": 0, "avg_pop": None, "hit_rate": None}
    return {"n": len(xs), "avg_pop": round(sum(c["pop"] for c in xs) / len(xs), 4),
            "hit_rate": round(sum(1 for c in xs if c.get("pnl", 0) > 0) / len(xs), 4)}
