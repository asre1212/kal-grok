"""Take-profit / stop-loss exit rules (shadow variants and, if tuned on, the main book).

Trigger: the position's mid-mark value >= cost x (1 + tp)   (take profit)
         the position's mid-mark value <= cost x (1 - sl)   (stop loss)
Take-profit execution (ORDER_MODE='limit'): first post a passive limit SELL 1c inside the ask
(never below the per-contract target price), maker fee; if that order expires unfilled
(LIMIT['max_age'] snapshots) and the trigger still holds, sell at the BID as a taker. A taker
take-profit only executes if net proceeds (after the taker fee) still beat the cost basis.
Stop-losses are time-critical: always taker at the bid. Fees are charged on every exit, so all
variant results are NET of entry and exit fees. Arbitrage basket legs are exempt (held to settle).
"""
import math

from . import config
from .execution import simulate_sell
from .fees import fee_per_contract
from .orders import post as post_order, has_resting, side_px


def _taker_tp(eng, k, pos, m, tp):
    fill = simulate_sell(eng.book(m), pos["side"], pos["qty"], 0.0, pos.get("fm", 1.0))
    sold_cost = pos["cost"] * fill["qty"] / pos["qty"] if fill["qty"] else 0
    if fill["qty"] and fill["proceeds"] - fill["fee"] > sold_cost:
        if eng.exit_position(k, f"take-profit +{tp:.0%} (taker)") is not None:
            eng.pf.p["n_tp"] = eng.pf.p.get("n_tp", 0) + 1
            return True
    return False


def apply_exit_rules(eng, tp=None, sl=None):
    if tp is None and sl is None:
        return 0
    n = 0
    for k, pos in list(eng.pf.positions.items()):
        if pos.get("group") or pos["qty"] <= 0 or pos.get("mark") is None:
            continue
        m = eng.markets.get(pos["ticker"])
        if not m or has_resting(eng.pf, key=k):
            continue
        cost, val = pos["cost"], pos["qty"] * pos["mark"]
        if tp is not None and val >= cost * (1 + tp):
            bid, ask, _ = side_px(m, pos["side"])
            if config.ORDER_MODE == "limit" and not pos.get("exit_expired"):
                fm, ft = pos.get("fm", 1.0), pos.get("ft", "quadratic")
                target = cost * (1 + tp) / pos["qty"]
                target += fee_per_contract(min(target, 0.99), fm) * (0.25 if "maker" in (ft or "") else 0)
                px = max(math.ceil(target * 100 - 1e-9) / 100, round(ask - 0.01, 2) if 0 < ask < 1 else 0)
                px = min(px, 0.99)
                if px > bid:
                    post_order(eng, pos["strategy"], m, pos["side"], "sell", px, pos["qty"], kind="exit",
                               key=k, note=f"TP +{tp:.0%}")
                    eng.log.append(f"{pos['strategy']}: POST LIMIT SELL {pos['qty']} {pos['side'].upper()} "
                                   f"{pos['ticker']} @ {px:.2f} (take-profit +{tp:.0%})")
                    n += 1
                    continue
            if _taker_tp(eng, k, pos, m, tp):
                n += 1
        elif sl is not None and val <= cost * (1 - sl):
            if eng.exit_position(k, f"stop-loss -{sl:.0%}") is not None:
                eng.pf.p["n_sl"] = eng.pf.p.get("n_sl", 0) + 1
                n += 1
    return n
