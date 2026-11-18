"""One trading cycle: snapshot -> settle -> mark -> trade -> persist -> publish.

    python -m kalbot.run            # live public data, paper fills
    python -m kalbot.run --no-trade # snapshot/settle/mark only
PAPER TRADING ONLY: no credentials are used and no orders are ever sent to Kalshi.
"""
import argparse
import time

from . import config
from .api import KalshiPublic, compact_market, now_ts
from .engine import Engine
from .history import update_history, harvest_calibration, bootstrap_calibration
from .models import fit_beta
from .portfolio import Portfolio
from .publish import publish
from .store import load_json, save_json
from .strategies import build_strategies
from .strategies.arbitrage import is_exhaustive_buckets
from .exits import apply_exit_rules
from .orders import process_resting
from .variants import run_variants, run_pop_books, settle_all, analysis
from .quality import hit_stats

S = config.STATE_DIR


def init_state():
    return {
        "created": int(time.time()), "runs": 0, "last_run": None,
        "params": config.default_params(),
        "allocation": dict(config.INITIAL_ALLOCATION),
        "strategy_status": {k: "active" for k in config.INITIAL_ALLOCATION},
        "equity_curve": [], "strategy_curve": [],
    }


def load_state():
    st = load_json(S / "portfolio.json", None) or init_state()
    # forward-compat: add new strategies/params
    for k, v in config.default_params().items():
        st["params"].setdefault(k, v)
        for pk, pv in v.items():
            st["params"][k].setdefault(pk, pv)
    st.setdefault("main_rules", json_copy(config.DEFAULT_MAIN_RULES))
    if "min_pop" not in st["main_rules"]:
        st["main_rules"]["min_pop"] = config.SELECT["min_pop"]
        st["main_rules"]["pop_since"] = int(time.time())
    for b, w in config.DEFAULT_MAIN_RULES["bucket_weights"].items():
        st["main_rules"]["bucket_weights"].setdefault(b, w)
    for k, w in config.INITIAL_ALLOCATION.items():
        st["allocation"].setdefault(k, config.ALLOC_FLOOR)
        st.setdefault("strategy_status", {}).setdefault(k, "active")
    return st


def json_copy(o):
    import json
    return json.loads(json.dumps(o))


def series_fee_info(client, force=False):
    """Non-default fee settings by series (refreshed at most daily)."""
    cache = load_json(S / "series_fees.json", None)
    if cache and not force and time.time() - cache.get("ts", 0) < 86400:
        return {k: tuple(v) for k, v in cache["fees"].items()}
    fees = {}
    try:
        for s in client.all_series():
            ft, mult = s.get("fee_type") or "quadratic", float(s.get("fee_multiplier") if s.get("fee_multiplier") is not None else 1)
            if ft != "quadratic" or mult != 1:
                fees[s["ticker"]] = (ft, mult)
    except Exception as e:
        print("series fee fetch failed:", e)
        if cache:
            return {k: tuple(v) for k, v in cache["fees"].items()}
    save_json(S / "series_fees.json", {"ts": int(time.time()), "fees": fees})
    return fees


def build_universe(client, fee_info, now):
    U = config.UNIVERSE
    markets, meta = {}, {}
    n_events = n_raw = 0
    for e in client.iter_open_events():
        n_events += 1
        raw = e.get("markets") or []
        n_raw += len(raw)
        cms = [compact_market(m, e, fee_info) for m in raw if m.get("status") == "active"]
        cms = [m for m in cms if m["ya"] > 0 and m["close"] and m["close"] > now]
        exhaustive = bool(e.get("mutually_exclusive")) and len(cms) >= 3 and is_exhaustive_buckets(cms)
        keep = []
        for m in cms:
            days = (m["close"] - now) / 86400
            liquid = (m["v24"] >= U["min_volume_24h"] or m["oi"] >= U["min_open_interest"]) and m["yb"] > 0
            if exhaustive and days <= U["max_days_to_close"]:
                keep.append(m)          # keep full bucket sets for exhaustive-basket checks
            elif liquid and days <= U["max_days_to_close"]:
                keep.append(m)
        for m in keep:
            markets[m["t"]] = m
        if keep:
            meta[e["event_ticker"]] = {"n": len(keep) if exhaustive else len(cms), "exhaustive": exhaustive}
    return markets, meta, n_events, n_raw


def run_cycle(trade=True, client=None, verbose=True):
    t0 = time.time()
    client = client or KalshiPublic()
    now = now_ts()
    state = load_state()
    history = load_json(S / "history.json.gz", {})
    calibration = load_json(S / "calibration.json.gz", [])
    pf = Portfolio(state)
    vstate = load_json(S / "variants.json.gz", {})
    vpfs = [Portfolio(vs) for vs in vstate.values()]

    fee_info = series_fee_info(client)
    markets, meta, n_events, n_raw = build_universe(client, fee_info, now)
    state["_event_meta"] = meta
    settled = settle_all([pf] + vpfs, client, markets, fee_info)
    n_settled = settled.get(0, 0)
    main_markets = {t: m for t, m in markets.items()
                    if m.get("_held_only") or (m["close"] - now) / 86400 <= config.MAIN_MAX_DAYS}

    if state.get("last_run"):
        pf.accrue_interest(now - state["last_run"])
    for k, p in pf.positions.items():
        if p["ticker"] in markets:
            pf.mark(k, markets[p["ticker"]])

    # calibration bootstrap + fit
    if not calibration:
        try:
            nb = bootstrap_calibration(client, calibration, now)
            if verbose:
                print(f"bootstrapped {nb} calibration samples")
        except Exception as e:
            print("calibration bootstrap failed:", e)
    if "calibration_fit" not in state:
        short = [r for r in calibration if r["h"] <= 48]
        a_s, b_s, n_s = fit_beta([r["p"] for r in short], [r["y"] for r in short],
                                 prior_beta=state["params"]["expiry_convergence"]["beta"])
        lo, hi = config.STRATEGY_PARAMS["expiry_convergence"]["beta"][1:]
        state["calibration_fit"] = {"alpha": 0.0, "alpha_short": round(a_s, 4), "beta_short": round(b_s, 4),
                                    "n_short": n_s, "ts": int(now)}
        if n_s >= 30:
            state["params"]["expiry_convergence"]["beta"] = round(min(hi, max(lo, b_s)), 3)

    state["_run_notes"] = {}
    shared = {"hit_stats": hit_stats(state.get("closed", []))}   # main book's track record, shared by all books
    rules = state["main_rules"]
    eng = Engine(state, pf, dict(main_markets), client=client, history=history, now=now,
                 shared=shared, rules=rules)
    strategies = build_strategies()
    if trade:
        process_resting(eng)
        apply_exit_rules(eng, rules.get("tp"), rules.get("sl"))
        for s in strategies:
            try:
                s.manage(eng)
            except Exception as e:
                eng.log.append(f"{s.name}: manage error {e!r}")
        eng.refresh_exposure()
        tradable = {t: m for t, m in main_markets.items() if not m.get("_held_only")}
        eng.markets = tradable
        eng.events.clear()
        for m in tradable.values():
            eng.events[m["e"]].append(m)
        for s in strategies:
            try:
                s.run(eng)
            except Exception as e:
                eng.log.append(f"{s.name}: run error {e!r}")
        eng.flush_pending()
        g = eng.gate_summary()
        eng.log.append(f"POP gate (min {g['min_pop']}): {g['seen']} candidates, {g['passed']} passed, "
                       f"{g['taken']} opened, {g['skipped']} skipped {g['rejected']}; "
                       f"MM quotes skipped {g['quotes_skipped']}/{g['quotes_seen']}")
    for k, p in pf.positions.items():
        if p["ticker"] in markets:
            pf.mark(k, markets[p["ticker"]])

    # shadow variants (expiry bucket x exit rule), same snapshot + shared orderbook cache
    vsum = run_variants(vstate, markets, state, meta, client, history, now, shared, strategies,
                        trade=trade, last_run=state.get("last_run"))
    # POP comparison books (all trades vs POP >= 0.60 / 0.70 / 0.80) on the main book's universe
    psum = run_pop_books(vstate, markets, main_markets, state, meta, client, history, now, shared, strategies,
                         trade=trade, last_run=state.get("last_run"))

    # history / calibration (main universe only, so the main book's inputs are unchanged)
    main_markets = {t: m for t, m in main_markets.items() if not m.get("_held_only")}
    held = {p["ticker"] for p in pf.positions.values()}
    try:
        n_cal = harvest_calibration(history, main_markets, client, calibration, now)
    except Exception as e:
        n_cal = 0
        print("calibration harvest failed:", e)
    update_history(history, main_markets, held, now)

    cd = pf.p.get("cooldown") or {}
    pf.p["cooldown"] = {t: ts for t, ts in cd.items() if now - ts < 2 * config.EXIT_COOLDOWN_HOURS * 3600}
    equity = pf.equity()
    pf.p["peak_equity"] = max(pf.p["peak_equity"], equity)
    state["equity_curve"].append({"ts": int(now), "equity": round(equity, 2), "cash": round(pf.cash, 2),
                                  "realized": round(pf.p["realized"], 2), "unrealized": round(pf.unrealized(), 2)})
    if len(state["equity_curve"]) > 3000:
        state["equity_curve"] = state["equity_curve"][::2]
    sc = {}
    for s in strategies:
        r, u = pf.strategy_pnl(s.name)
        sc[s.name] = [round(r, 2), round(u, 2), round(state["allocation"].get(s.name, 0) * equity, 2)]
    state["strategy_curve"].append({"ts": int(now), "s": sc})
    state["strategy_curve"] = state["strategy_curve"][-1500:]

    state["runs"] += 1
    state["last_run"] = int(now)
    state["last_cycle"] = {
        "ts": int(now), "events_scanned": n_events, "markets_scanned": n_raw,
        "universe": len([m for m in markets.values() if not m.get("_held_only")]),
        "orderbooks": eng.ob_calls, "api_calls": client.calls, "settled": n_settled,
        "calibration_added": n_cal, "new_trades": sum(1 for x in eng.log if " BUY " in x or "BASKET" in x or "FILL" in x),
        "log": eng.log[-200:], "notes": state.pop("_run_notes", {}),
        "selection": eng.gate_summary(), "pop_books": psum,
        "drawdown": round(eng.drawdown, 4), "halted": eng.halt_directional,
        "seconds": round(time.time() - t0, 1),
        "variants": {"books": len(vsum), "new_trades": sum(v["new"] for v in vsum.values()),
                     "exits": sum(v["exits"] for v in vsum.values()),
                     "settled": sum(v for i, v in settled.items() if i > 0)},
    }
    state.pop("_event_meta", None)
    save_json(S / "portfolio.json", state, compact=True)
    save_json(S / "history.json.gz", history)
    save_json(S / "calibration.json.gz", calibration)
    save_json(S / "variants.json.gz", vstate)
    publish(state, strategies, calibration)
    save_json(config.DOCS_DATA / "analysis.json", analysis(vstate, state, now), compact=True)
    if verbose:
        lc = state["last_cycle"]
        print(f"events={n_events} markets={n_raw} universe={lc['universe']} trades={lc['new_trades']} "
              f"settled={n_settled} equity=${equity:,.2f} cash=${pf.cash:,.2f} api_calls={client.calls} "
              f"({lc['seconds']}s)")
        for line in eng.log[:80]:
            print("  ", line)
        print("variants:", state["last_cycle"]["variants"])
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-trade", action="store_true")
    a = ap.parse_args()
    run_cycle(trade=not a.no_trade)


if __name__ == "__main__":
    main()
