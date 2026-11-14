"""Shadow paper portfolios: every (expiry bucket x exit rule) combo runs the SAME strategies,
parameters, allocation and risk rules as the main book, but
  * may only OPEN positions in markets whose expected expiration falls in its bucket, and
  * exits with its own take-profit / stop-loss rule (sell at the bid, fees charged).
Each starts with its own $100k. They reuse the cycle's single market snapshot and the shared
orderbook cache, so they add almost no API load. State lives in state/variants.json.gz.
"""
import time
from collections import defaultdict

from . import config
from .engine import Engine
from .exits import apply_exit_rules
from .orders import process_resting
from .metrics import sharpe, max_drawdown, periods_per_year
from .portfolio import Portfolio

TRADES_KEEP = 40
CLOSED_KEEP = 400
CURVE_KEEP = 1200


def variant_ids():
    for b, bl, lo, hi in config.EXPIRY_BUCKETS:
        for r, rl, tp, sl in config.EXIT_RULES:
            yield f"{b}|{r}", b, bl, r, rl, tp, sl


def init_variant(now):
    return {"created": int(now), "runs": 0, "equity_curve": [], "trades": [], "closed": [],
            "strategy_stats": {}}


def _view(vs, main_state, meta):
    """Attach the main book's (shared, tuned) params/allocation for this cycle only."""
    vs["params"] = main_state["params"]
    vs["allocation"] = main_state["allocation"]
    vs["strategy_status"] = main_state.get("strategy_status", {})
    vs["calibration_fit"] = main_state.get("calibration_fit", {})
    vs["_event_meta"] = meta
    return vs


def _strip(vs):
    for k in ("params", "allocation", "strategy_status", "calibration_fit", "_event_meta", "_run_notes", "nws_cache"):
        vs.pop(k, None)
    vs["trades"] = vs.get("trades", [])[-TRADES_KEEP:]
    vs["closed"] = vs.get("closed", [])[-CLOSED_KEEP:]
    if len(vs["equity_curve"]) > CURVE_KEEP:
        vs["equity_curve"] = vs["equity_curve"][::2]
    cd = vs.get("portfolio", {}).get("cooldown")
    if cd:
        now = time.time()
        vs["portfolio"]["cooldown"] = {t: ts for t, ts in cd.items() if now - ts < 2 * config.EXIT_COOLDOWN_HOURS * 3600}


def run_variants(vstate, markets, main_state, meta, client, history, now, shared, strategies,
                 trade=True, last_run=None):
    """markets: full universe (+ held-only refreshes). Mutates vstate in place."""
    by_bucket = defaultdict(dict)
    for t, m in markets.items():
        if m.get("_held_only"):
            continue
        b = config.bucket_of(m, now)
        if b:
            by_bucket[b][t] = m
    summary = {}
    for vid, b, bl, r, rl, tp, sl in variant_ids():
        vs = vstate.setdefault(vid, init_variant(now))
        _view(vs, main_state, meta)
        pf = Portfolio(vs)
        if last_run and vs["runs"] > 0:
            pf.accrue_interest(now - last_run)
        for k, p in pf.positions.items():
            if p["ticker"] in markets:
                pf.mark(k, markets[p["ticker"]])
        eng = Engine(vs, pf, markets, client=client, history=history, now=now, shared=shared,
                     rules={"tp": tp, "sl": sl})
        n_exit = 0
        if trade:
            process_resting(eng)
            n_exit = apply_exit_rules(eng, tp, sl)
            for s in strategies:
                try:
                    s.manage(eng)
                except Exception as e:
                    eng.log.append(f"{s.name}: manage error {e!r}")
            eng.refresh_exposure()
            eng.markets = by_bucket.get(b, {})
            eng.events = defaultdict(list)
            for m in eng.markets.values():
                eng.events[m["e"]].append(m)
            for s in strategies:
                try:
                    s.run(eng)
                except Exception as e:
                    eng.log.append(f"{s.name}: run error {e!r}")
        for k, p in pf.positions.items():
            if p["ticker"] in markets:
                pf.mark(k, markets[p["ticker"]])
        eq = pf.equity()
        pf.p["peak_equity"] = max(pf.p["peak_equity"], eq)
        vs["equity_curve"].append({"ts": int(now), "equity": round(eq, 2), "realized": round(pf.p["realized"], 2),
                                   "unrealized": round(pf.unrealized(), 2)})
        vs["runs"] += 1
        vs["last_log"] = eng.log[-15:]
        summary[vid] = {"new": sum(1 for x in eng.log if " BUY " in x or "BASKET" in x or "FILL" in x),
                        "exits": n_exit, "equity": round(eq, 2)}
        _strip(vs)
    return summary


def settle_all(portfolios, client, markets, fee_info):
    """Settle / refresh every held market missing from the universe across all portfolios."""
    from .api import compact_market
    held = {}
    for pf in portfolios:
        for p in pf.positions.values():
            held.setdefault(p["ticker"], p)
    missing = sorted(set(held) - set(markets))
    settled = defaultdict(int)
    if not missing:
        return settled
    info = client.markets_by_tickers(missing)
    for t in missing:
        m = info.get(t)
        if not m:
            continue
        status, result = m.get("status"), (m.get("result") or "").lower()
        if status in ("settled", "finalized") and result in ("yes", "no", "scalar", "void", "all_no", "all_yes"):
            if result == "yes":
                yv = 1.0
            elif result == "no":
                yv = 0.0
            else:
                try:
                    yv = float(m.get("settlement_value_dollars"))
                except (TypeError, ValueError):
                    continue
            for i, pf in enumerate(portfolios):
                for k in [k for k, p in pf.positions.items() if p["ticker"] == t]:
                    pf.settle(k, yv, result)
                    pf.p["n_settled"] = pf.p.get("n_settled", 0) + 1
                    settled[i] += 1
        elif status != "active":
            if result in ("yes", "no"):
                for pf in portfolios:
                    for p in pf.positions.values():
                        if p["ticker"] == t:
                            p["mark"] = p["bid"] = 1.0 if result == p["side"] else 0.0
        else:
            cm = compact_market(m, None, fee_info)
            any_pos = held[t]
            cm.update({"e": any_pos["event"], "s": any_pos["series"], "cat": any_pos["cat"]})
            cm["_held_only"] = True
            markets[t] = cm
    return settled


# ---------------- analysis ----------------
MIN_POINTS_SHARPE = 8
ADEQUATE = dict(points=12, closed=20, trades=10)


def book_stats(state_like, start=config.STARTING_BANKROLL):
    p = state_like.get("portfolio", {})
    curve = state_like.get("equity_curve", [])
    eq = curve[-1]["equity"] if curve else start
    ss = state_like.get("strategy_stats", {})
    wins = sum(s.get("wins", 0) for s in ss.values())
    losses = sum(s.get("losses", 0) for s in ss.values())
    fills = sum(s.get("trades", 0) for s in ss.values())
    vals = [start] + [c["equity"] for c in curve]
    rets = [(b - a) / a for a, b in zip(vals[1:], vals[2:]) if a > 0]
    ts = [c["ts"] for c in curve]
    sh = sharpe(rets, periods_per_year(ts)) if len(curve) >= MIN_POINTS_SHARPE else None
    closed = wins + losses
    o = p.get("orders", {})
    fees = p.get("fees", 0.0)
    net_pnl = eq - start
    resolved = o.get("filled_qty", 0) + o.get("cancelled_qty", 0)
    adequate = len(curve) >= ADEQUATE["points"] and closed >= ADEQUATE["closed"] and fills >= ADEQUATE["trades"]
    return {
        "equity": round(eq, 2), "return_pct": round((eq / start - 1) * 100, 3),
        "realized": round(p.get("realized", 0.0), 2),
        "unrealized": round(curve[-1].get("unrealized", 0.0), 2) if curve else 0.0,
        "fees": round(fees, 2), "interest": round(p.get("interest", 0.0), 2),
        "net_pnl": round(net_pnl, 2), "gross_pnl": round(net_pnl + fees, 2),
        "net_return_pct": round(net_pnl / start * 100, 3), "gross_return_pct": round((net_pnl + fees) / start * 100, 3),
        "fill_rate": round(o.get("filled_qty", 0) / resolved, 3) if resolved else None,
        "orders_posted": o.get("posted", 0), "resting_qty": sum(r["qty"] - r.get("filled", 0) for r in p.get("resting", [])),
        "maker_fills": o.get("maker_fills", 0), "taker_fills": o.get("taker_fills", 0),
        "maker_fees": round(o.get("maker_fees", 0), 2), "taker_fees": round(o.get("taker_fees", 0), 2),
        "fees_saved": round(o.get("fees_saved", 0), 2), "price_improvement": round(o.get("price_improvement", 0), 2),
        "trades": fills, "closed": closed, "wins": wins,
        "win_rate": round(wins / closed, 3) if closed else None,
        "settled": p.get("n_settled", 0), "tp_exits": p.get("n_tp", 0), "sl_exits": p.get("n_sl", 0),
        "open_positions": len(p.get("positions", {})),
        "max_dd_pct": round(max_drawdown(vals) * 100, 3),
        "sharpe": None if sh is None else round(sh, 2), "points": len(curve), "adequate": adequate,
    }


def rank_key(s):
    """Adequate samples first, ranked by Sharpe; otherwise by return. Books that barely
    traded can't 'win' on idle-cash interest alone."""
    if s["trades"] < 5:
        return (0, -1e9)
    if s["adequate"] and s["sharpe"] is not None:
        return (2, s["sharpe"])
    return (1, s["return_pct"])


def analysis(vstate, main_state, now=None):
    now = now or time.time()
    cells = []
    for vid, b, bl, r, rl, tp, sl in variant_ids():
        vs = vstate.get(vid)
        if not vs:
            continue
        st = book_stats(vs)
        curve = vs["equity_curve"]
        step = max(1, len(curve) // 120)
        pts = [[c["ts"], c["equity"]] for c in curve[::step]]
        if curve and (not pts or pts[-1][0] != curve[-1]["ts"]):
            pts.append([curve[-1]["ts"], curve[-1]["equity"]])
        cells.append({"id": vid, "bucket": b, "bucket_label": bl, "rule": r, "rule_label": rl,
                      "tp": tp, "sl": sl, **st, "created": vs.get("created"), "curve": pts})
    main = book_stats(main_state)
    mc = main_state.get("equity_curve", [])
    step = max(1, len(mc) // 120)
    main["curve"] = [[c["ts"], c["equity"]] for c in mc[::step]]
    ranked = sorted(cells, key=rank_key, reverse=True)
    best = ranked[0] if ranked else None
    small = (not best) or (not best["adequate"])
    caveat = None
    if small:
        caveat = ("Small sample: fewer than %d snapshots / %d closed trades. Rankings mostly reflect "
                  "entry costs (spread + fees) and open marks, not skill yet." % (ADEQUATE["points"], ADEQUATE["closed"]))
    return {
        "generated": int(now), "start_bankroll": config.STARTING_BANKROLL,
        "buckets": [{"id": b, "label": l} for b, l, _, _ in config.EXPIRY_BUCKETS],
        "rules": [{"id": r, "label": l, "tp": tp, "sl": sl} for r, l, tp, sl in config.EXIT_RULES],
        "cells": cells, "main": main, "main_rules": main_state.get("main_rules", config.DEFAULT_MAIN_RULES),
        "ranking": [c["id"] for c in ranked],
        "best": None if not best else {"id": best["id"], "bucket_label": best["bucket_label"],
                                       "rule_label": best["rule_label"], "return_pct": best["return_pct"],
                                       "sharpe": best["sharpe"], "trades": best["trades"],
                                       "closed": best["closed"], "adequate": best["adequate"]},
        "small_sample": small, "caveat": caveat,
    }


# ---------------- weekly: should the main book adopt a winning combo? ----------------
ROBUST = dict(closed=30, settled=15, points=20, sharpe_margin=0.5, return_margin_pct=1.0, dd_slack_pct=5.0)


def robust_winner(an):
    """Best combo that clearly and robustly beats the main book on NET, risk-adjusted terms."""
    m = an["main"]
    for vid in an["ranking"]:
        c = next(x for x in an["cells"] if x["id"] == vid)
        reasons = []
        if c["closed"] < ROBUST["closed"] or c["settled"] < ROBUST["settled"] or c["points"] < ROBUST["points"]:
            reasons.append("sample too small")
        if c["sharpe"] is None or m["sharpe"] is None or c["sharpe"] < m["sharpe"] + ROBUST["sharpe_margin"]:
            reasons.append("Sharpe edge < %.1f" % ROBUST["sharpe_margin"])
        if c["net_return_pct"] < m["net_return_pct"] + ROBUST["return_margin_pct"]:
            reasons.append("net return edge < %.1f pts" % ROBUST["return_margin_pct"])
        if c["max_dd_pct"] > m["max_dd_pct"] + ROBUST["dd_slack_pct"]:
            reasons.append("drawdown too deep")
        return (c if not reasons else None), c, reasons
    return None, None, ["no variants yet"]


def adopt(main_rules, winner):
    """Nudge main rules one step toward the winner (bounded). Returns list of change strings."""
    changes = []
    steps = config.MAIN_TP_STEPS
    cur = main_rules.get("tp")
    ci = steps.index(cur) if cur in steps else 0
    ti = steps.index(winner["tp"]) if winner["tp"] in steps else 0
    if ti != ci:
        ni = ci + (1 if ti > ci else -1)
        main_rules["tp"] = steps[ni]
        changes.append(f"main take-profit {cur} -> {steps[ni]} (one step toward winner {winner['rule_label']})")
    if winner.get("sl") and main_rules.get("sl") != winner["sl"]:
        changes.append(f"main stop-loss {main_rules.get('sl')} -> {winner['sl']}")
        main_rules["sl"] = winner["sl"]
    lo, hi = config.BUCKET_WEIGHT_BOUNDS
    bw = main_rules.setdefault("bucket_weights", dict(config.DEFAULT_MAIN_RULES["bucket_weights"]))
    for b in list(bw):
        old = bw[b]
        new = min(hi, old + 0.25) if b == winner["bucket"] else max(lo, old - 0.10)
        new = round(new, 2)
        if new != old:
            bw[b] = new
            changes.append(f"main expiry weight {b}: {old} -> {new}")
    return changes
