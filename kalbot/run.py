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
    for k, w in config.INITIAL_ALLOCATION.items():
        st["allocation"].setdefault(k, config.ALLOC_FLOOR)
        st.setdefault("strategy_status", {}).setdefault(k, "active")
    return st


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


def settle_and_refresh(pf, client, markets, fee_info):
    """Held markets missing from the universe: settle if finalized, else load quotes for marking."""
    missing = sorted({p["ticker"] for p in pf.positions.values()} - set(markets))
    settled = 0
    if not missing:
        return 0
    info = client.markets_by_tickers(missing)
    for t in missing:
        m = info.get(t)
        if not m:
            continue
        status, result = m.get("status"), (m.get("result") or "").lower()
        if status in ("settled", "finalized") and result in ("yes", "no", "scalar", "void", "all_no", "all_yes"):
            sv = m.get("settlement_value_dollars")
            if result == "yes":
                yv = 1.0
            elif result == "no":
                yv = 0.0
            else:
                try:
                    yv = float(sv)
                except (TypeError, ValueError):
                    continue
            for k in [k for k, p in pf.positions.items() if p["ticker"] == t]:
                pf.settle(k, yv, result)
                settled += 1
        elif status != "active":
            # closed / determined but not yet paid: mark at the known result if any, else hold last mark
            if result in ("yes", "no"):
                for p in pf.positions.values():
                    if p["ticker"] == t:
                        p["mark"] = p["bid"] = 1.0 if result == p["side"] else 0.0
        else:
            cm = compact_market(m, None, fee_info)
            any_pos = next(p for p in pf.positions.values() if p["ticker"] == t)
            cm.update({"e": any_pos["event"], "s": any_pos["series"], "cat": any_pos["cat"]})
            cm["_held_only"] = True
            markets[t] = cm
    return settled


def run_cycle(trade=True, client=None, verbose=True):
    t0 = time.time()
    client = client or KalshiPublic()
    now = now_ts()
    state = load_state()
    history = load_json(S / "history.json.gz", {})
    calibration = load_json(S / "calibration.json.gz", [])
    pf = Portfolio(state)

    fee_info = series_fee_info(client)
    markets, meta, n_events, n_raw = build_universe(client, fee_info, now)
    state["_event_meta"] = meta
    n_settled = settle_and_refresh(pf, client, markets, fee_info)

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
    eng = Engine(state, pf, {t: m for t, m in markets.items()}, client=client,
                 history=history, now=now)
    strategies = build_strategies()
    if trade:
        for s in strategies:
            try:
                s.manage(eng)
            except Exception as e:
                eng.log.append(f"{s.name}: manage error {e!r}")
        eng.refresh_exposure()
        tradable = {t: m for t, m in markets.items() if not m.get("_held_only")}
        eng.markets = tradable
        eng.events.clear()
        for m in tradable.values():
            eng.events[m["e"]].append(m)
        for s in strategies:
            try:
                s.run(eng)
            except Exception as e:
                eng.log.append(f"{s.name}: run error {e!r}")
    for k, p in pf.positions.items():
        if p["ticker"] in markets:
            pf.mark(k, markets[p["ticker"]])

    # history / calibration
    held = {p["ticker"] for p in pf.positions.values()}
    try:
        n_cal = harvest_calibration(history, {t: m for t, m in markets.items() if not m.get("_held_only")},
                                    client, calibration, now)
    except Exception as e:
        n_cal = 0
        print("calibration harvest failed:", e)
    update_history(history, {t: m for t, m in markets.items() if not m.get("_held_only")}, held, now)

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
        "drawdown": round(eng.drawdown, 4), "halted": eng.halt_directional,
        "seconds": round(time.time() - t0, 1),
    }
    state.pop("_event_meta", None)
    save_json(S / "portfolio.json", state, compact=True)
    save_json(S / "history.json.gz", history)
    save_json(S / "calibration.json.gz", calibration)
    publish(state, strategies, calibration)
    if verbose:
        lc = state["last_cycle"]
        print(f"events={n_events} markets={n_raw} universe={lc['universe']} trades={lc['new_trades']} "
              f"settled={n_settled} equity=${equity:,.2f} cash=${pf.cash:,.2f} api_calls={client.calls} "
              f"({lc['seconds']}s)")
        for line in eng.log[:80]:
            print("  ", line)
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-trade", action="store_true")
    a = ap.parse_args()
    run_cycle(trade=not a.no_trade)


if __name__ == "__main__":
    main()
