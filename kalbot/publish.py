"""Write dashboard JSON for the PWA into docs/data/."""
import time

from . import config
from .store import save_json, load_json

D = config.DOCS_DATA


def _kalshi_url(p):
    series = (p.get("series") or p["ticker"].split("-")[0]).lower()
    return f"https://kalshi.com/markets/{series}"


def publish(state, strategies, calibration=None):
    pfs = state["portfolio"]
    pos = pfs["positions"]
    value = sum(p["qty"] * (p["mark"] if p.get("mark") is not None else p["cost"] / max(p["qty"], 1))
                for p in pos.values())
    equity = pfs["cash"] + value
    liq = pfs["cash"] + sum(p["qty"] * p.get("bid", p.get("mark") or 0) for p in pos.values())
    unreal = value - sum(p["cost"] for p in pos.values())
    start = config.STARTING_BANKROLL
    eq_vals = [e["equity"] for e in state["equity_curve"]] or [start]
    peak = max(max(eq_vals), start)

    strat_rows = []
    for s in strategies:
        st = state.get("strategy_stats", {}).get(s.name, {})
        mine = [p for p in pos.values() if p["strategy"] == s.name]
        u = sum(p["qty"] * (p["mark"] if p.get("mark") is not None else 0) - p["cost"] for p in mine)
        w, l = st.get("wins", 0), st.get("losses", 0)
        strat_rows.append({
            "name": s.name, "description": s.description,
            "weight": round(state["allocation"].get(s.name, 0), 4),
            "sleeve": round(state["allocation"].get(s.name, 0) * equity, 2),
            "status": state.get("strategy_status", {}).get(s.name, "active"),
            "realized": round(st.get("realized", 0), 2), "unrealized": round(u, 2),
            "total": round(st.get("realized", 0) + u, 2), "fees": round(st.get("fees", 0), 2),
            "trades": st.get("trades", 0), "wins": w, "losses": l,
            "hit_rate": round(w / (w + l), 3) if (w + l) else None,
            "open_positions": len(mine), "exposure": round(sum(p["cost"] for p in mine), 2),
            "params": state["params"].get(s.name, {}),
            "note": (state.get("last_cycle") or {}).get("notes", {}).get(s.name),
        })

    lc = dict(state.get("last_cycle") or {})
    summary = {
        "paper_trading": True, "updated": int(time.time()), "last_run": state.get("last_run"),
        "runs": state.get("runs", 0), "start_bankroll": start,
        "equity": round(equity, 2), "cash": round(pfs["cash"], 2),
        "positions_value": round(value, 2), "liquidation_equity": round(liq, 2), "realized": round(pfs["realized"], 2),
        "unrealized": round(unreal, 2), "fees": round(pfs["fees"], 2), "interest": round(pfs["interest"], 2),
        "total_pnl": round(equity - start, 2), "return_pct": round((equity / start - 1) * 100, 3),
        "peak": round(peak, 2), "drawdown_pct": round((1 - equity / peak) * 100, 3) if peak else 0,
        "open_positions": len(pos), "resting_orders": len(pfs.get("resting", [])),
        "reserve_weight": config.RESERVE_WEIGHT, "strategies": strat_rows,
        "last_cycle": lc, "calibration": {**state.get("calibration_fit", {}), "samples": len(calibration or [])},
        "cash_apy_assumption": config.CASH_APY,
    }
    save_json(D / "summary.json", summary)

    plist = []
    for p in sorted(pos.values(), key=lambda p: -p["cost"]):
        mark = p.get("mark")
        val = p["qty"] * mark if mark is not None else None
        plist.append({
            "strategy": p["strategy"], "ticker": p["ticker"], "title": p.get("title"), "event": p.get("etitle"),
            "side": p["side"], "qty": p["qty"], "avg_cost": round(p["cost"] / p["qty"], 4),
            "cost": round(p["cost"], 2), "mark": mark, "bid": p.get("bid"), "p_mkt": p.get("p_mkt"), "value": None if val is None else round(val, 2),
            "pnl": None if val is None else round(val - p["cost"], 2), "p_model": p.get("p_model"),
            "close": p.get("exp") or p.get("close"), "group": p.get("group"), "url": _kalshi_url(p),
            "category": p.get("cat"),
        })
    save_json(D / "positions.json", plist, compact=True)
    save_json(D / "trades.json", list(reversed(state.get("trades", [])[-300:])), compact=True)

    curve = list(state["equity_curve"])
    if curve and state.get("created") and state["created"] < curve[0]["ts"]:
        curve.insert(0, {"ts": state["created"], "equity": start, "cash": start, "realized": 0.0, "unrealized": 0.0})
    step = max(1, len(curve) // 1000)
    scurve = state.get("strategy_curve", [])
    sstep = max(1, len(scurve) // 400)
    save_json(D / "equity.json", {"start": start, "curve": curve[::step] + ([curve[-1]] if curve and (len(curve) - 1) % step else []),
                                  "strategies": scurve[::sstep]}, compact=True)
    if not (D / "reports" / "index.json").exists():
        save_json(D / "reports" / "index.json", [])
