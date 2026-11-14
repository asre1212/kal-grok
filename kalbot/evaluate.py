"""Weekly strategy re-evaluation and capital reallocation.

Per strategy: weekly P&L (realized + mark-to-market), return on sleeve, Sharpe & Sortino
(annualised from per-run returns), hit rate, Brier score & calibration gap on settled
trades, max drawdown, trade count.

Reallocation = blend of
  * Multiplicative weights (Hedge):  w_i <- w_i * exp(eta * score_i), score in [-1, 1]
  * Thompson sampling: draw theta_i ~ Beta(1 + wins_i, 1 + losses_i) on recent closed
    trades; the TS weight is proportional to the draw.
  w = normalise((1-l) * MW + l * TS), l = 0.3*min(1, closed/100), then floors/caps, a cash reserve, and probation
  (tiny weight) for strategies that keep losing; probationers that recover are reinstated.
Each strategy's tune() then adjusts its own parameters inside hard bounds.

    python -m kalbot.evaluate
"""
import json
import math
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .metrics import sharpe, sortino, max_drawdown, returns_from_pnl, periods_per_year
from .models import brier, fit_beta
from .publish import publish
from .store import load_json, save_json
from .strategies import build_strategies
from .strategies.drift import pooled_autocorr

S = config.STATE_DIR
NY = ZoneInfo("America/New_York")
WEEK = 7 * 86400


def strategy_metrics(state, name, now):
    curve = [c for c in state.get("strategy_curve", []) if name in c["s"]]
    wk = [c for c in curve if c["ts"] >= now - WEEK]
    ts = [c["ts"] for c in curve]
    pnl = [c["s"][name][0] + c["s"][name][1] for c in curve]
    cap = [c["s"][name][2] for c in curve]
    rets = returns_from_pnl(pnl, cap)
    ppy = periods_per_year(ts)
    base_idx = len(curve) - len(wk) - 1
    start_pnl = pnl[base_idx] if base_idx >= 0 else 0.0
    week_pnl = (pnl[-1] - start_pnl) if pnl else 0.0
    avg_cap = (sum(cap[-len(wk):]) / len(wk)) if wk else (cap[-1] if cap else 0)
    closed = [c for c in state.get("closed", []) if c["strategy"] == name]
    closed_wk = [c for c in closed if c["ts"] >= now - WEEK]
    settled = [c for c in closed if c.get("outcome") is not None and c.get("p_model") is not None]
    eq_series = [c + p for c, p in zip(cap, pnl)] if cap else []
    m = {
        "week_pnl": round(week_pnl, 2),
        "week_return": round(week_pnl / avg_cap, 5) if avg_cap else 0.0,
        "total_pnl": round(pnl[-1], 2) if pnl else 0.0,
        "sharpe": None if (v := sharpe(rets[-60:], ppy)) is None else round(v, 3),
        "sortino": None if (v := sortino(rets[-60:], ppy)) is None else round(v, 3),
        "max_drawdown": round(max_drawdown([x - pnl[0] + (cap[0] or 1) for x in pnl]) if pnl else 0.0, 4),
        "closed_trades": len(closed_wk), "closed_all": len(closed),
        "hit_rate": round(sum(c["pnl"] > 0 for c in closed_wk) / len(closed_wk), 3) if closed_wk else None,
        "settled_trades": len(settled),
        "brier": None if not settled else round(brier([c["p_model"] for c in settled], [c["outcome"] for c in settled]), 4),
        "calibration_gap": None if not settled else round(
            sum(c["p_model"] for c in settled) / len(settled) - sum(c["outcome"] for c in settled) / len(settled), 4),
        "brier_market": (lambda xs: None if not xs else round(brier([c["p_mkt"] for c in xs], [c["outcome"] for c in xs]), 4))(
            [c for c in settled if c.get("p_mkt") is not None]),
        "wins_recent": sum(c["pnl"] > 0 for c in closed[-100:]),
        "losses_recent": sum(c["pnl"] <= 0 for c in closed[-100:]),
    }
    return m


def score(m):
    """Bounded performance score in [-1, 1]: weekly return (scaled) plus a Sharpe kicker."""
    r = m["week_return"] or 0.0
    s = (m["sharpe"] or 0.0)
    return max(-1.0, min(1.0, math.tanh(r * 20) + 0.1 * max(-3, min(3, s))))


def reallocate(alloc, metrics, status, rng, eta=0.6):
    names = list(alloc)
    mw = {n: alloc[n] * math.exp(eta * score(metrics[n])) for n in names}
    tot = sum(mw.values()) or 1
    mw = {n: v / tot for n, v in mw.items()}
    draws = {n: rng.betavariate(1 + metrics[n]["wins_recent"], 1 + metrics[n]["losses_recent"]) for n in names}
    tot = sum(draws.values()) or 1
    ts = {n: v / tot for n, v in draws.items()}
    # Thompson weight grows with evidence (0 with no closed trades, up to 0.3 at >= 100)
    n_ev = sum(metrics[n]["wins_recent"] + metrics[n]["losses_recent"] for n in names)
    lam = 0.3 * min(1.0, n_ev / 100)
    w = {n: (1 - lam) * mw[n] + lam * ts[n] for n in names}
    budget = 1 - config.RESERVE_WEIGHT
    fixed = {n: config.PROBATION_WEIGHT for n in names if status.get(n) == "probation"}
    free = [n for n in names if n not in fixed]
    tot = sum(w[n] for n in free) or 1
    out = dict(fixed)
    rem = budget - sum(fixed.values())
    for n in free:
        out[n] = w[n] / tot * rem
    # floors/caps via iterative clipping
    for _ in range(20):
        over = {n: v for n, v in out.items() if n in free and v > config.ALLOC_CAP}
        under = {n: v for n, v in out.items() if n in free and v < config.ALLOC_FLOOR}
        if not over and not under:
            break
        for n in over:
            out[n] = config.ALLOC_CAP
        for n in under:
            out[n] = config.ALLOC_FLOOR
        clipped = set(over) | set(under)
        rest = [n for n in free if n not in clipped]
        if not rest:
            break
        target = rem - sum(out[n] for n in free if n in clipped)
        cur = sum(out[n] for n in rest) or 1
        for n in rest:
            out[n] = out[n] * target / cur
    return {n: round(v, 4) for n, v in out.items()}


def update_status(state, name, m, history_scores):
    st = state["strategy_status"].get(name, "active")
    last2 = history_scores[-2:]
    if st == "active" and m["closed_all"] >= 20 and m["total_pnl"] < -0.08 * max(1.0, state["allocation"][name] * config.STARTING_BANKROLL) \
            and len(last2) == 2 and all(s < 0 for s in last2):
        return "probation", "two straight losing weeks and cumulative loss > 8% of sleeve"
    if st == "probation" and history_scores and history_scores[-1] > 0.1:
        return "active", "recovered while on probation"
    return st, None


def weekly(now=None, write=True):
    now = now or time.time()
    state = load_json(S / "portfolio.json", None)
    if not state:
        raise SystemExit("no state yet; run kalbot.run first")
    calibration = load_json(S / "calibration.json.gz", [])
    history = load_json(S / "history.json.gz", {})
    strategies = build_strategies()
    rng = random.Random(int(now // WEEK))
    metrics = {s.name: strategy_metrics(state, s.name, now) for s in strategies}
    scores_hist = state.setdefault("score_history", {})
    changes = []
    for s in strategies:
        scores_hist.setdefault(s.name, []).append(round(score(metrics[s.name]), 4))
        scores_hist[s.name] = scores_hist[s.name][-12:]
        new_status, why = update_status(state, s.name, metrics[s.name], scores_hist[s.name])
        if why:
            changes.append(f"**{s.name}** status {state['strategy_status'].get(s.name)} -> {new_status}: {why}")
            state["strategy_status"][s.name] = new_status

    old_alloc = dict(state["allocation"])
    state["allocation"] = reallocate(old_alloc, metrics, state["strategy_status"], rng)
    for n in old_alloc:
        if abs(state["allocation"][n] - old_alloc[n]) >= 0.005:
            changes.append(f"**{n}** allocation {old_alloc[n]:.1%} -> {state['allocation'][n]:.1%} "
                           f"(score {score(metrics[n]):+.2f}, week P&L ${metrics[n]['week_pnl']:+,.2f})")

    # global calibration refit (alpha used by FLB strategies)
    long_rows = [r for r in calibration if r.get("h", 0) > 48]
    short_rows = [r for r in calibration if r.get("h", 999) <= 48]
    a_l, b_l, n_l = fit_beta([r["p"] for r in long_rows], [r["y"] for r in long_rows],
                             prior_beta=state["params"]["longshot_fade"]["beta"])
    a_s, b_s, n_s = fit_beta([r["p"] for r in short_rows], [r["y"] for r in short_rows],
                             prior_beta=state["params"]["expiry_convergence"]["beta"])
    state["calibration_fit"] = {"alpha": round(a_l if n_l >= 30 else 0.0, 4), "beta_long": round(b_l, 4), "n_long": n_l,
                                "alpha_short": round(a_s if n_s >= 30 else 0.0, 4), "beta_short": round(b_s, 4),
                                "n_short": n_s, "ts": int(now)}
    rho = pooled_autocorr(history)
    ctx = {"calibration": calibration, "autocorr": rho}
    param_changes = {}
    for s in strategies:
        old = dict(state["params"][s.name])
        new, reasons = s.tune(old, metrics[s.name], ctx)
        state["params"][s.name] = new
        param_changes[s.name] = {"old": old, "new": new, "reasons": reasons}
        for k in new:
            if new[k] != old.get(k):
                changes.append(f"**{s.name}** {k}: {old.get(k)} -> {new[k]}")

    eq = state["equity_curve"]
    wk_eq = [e for e in eq if e["ts"] >= now - WEEK]
    base = [e for e in eq if e["ts"] < now - WEEK]
    start_eq = base[-1]["equity"] if base else config.STARTING_BANKROLL
    end_eq = eq[-1]["equity"] if eq else config.STARTING_BANKROLL
    date = datetime.fromtimestamp(now, NY).strftime("%Y-%m-%d")
    # ---- expiry-bucket x exit-rule shadow books ----
    from .variants import analysis as variant_analysis, robust_winner, adopt
    vstate = load_json(S / "variants.json.gz", {})
    combos = None
    if vstate:
        an = variant_analysis(vstate, state, now)
        win, top, why_not = robust_winner(an)
        state.setdefault("main_rules", json.loads(json.dumps(config.DEFAULT_MAIN_RULES)))
        adopted = []
        if win:
            adopted = adopt(state["main_rules"], win)
            changes += [f"**main rules** {c} — {win['id']} beat main on net return "
                        f"({win['net_return_pct']:+.2f}% vs {an['main']['net_return_pct']:+.2f}%) and Sharpe "
                        f"({win['sharpe']} vs {an['main']['sharpe']}) with {win['closed']} closed trades" for c in adopted]
        rows = [next(x for x in an["cells"] if x["id"] == vid) for vid in an["ranking"]]
        combos = {"ranking": [{k: c[k] for k in ("id", "bucket_label", "rule_label", "net_return_pct", "gross_return_pct",
                                                  "fees", "fees_saved", "fill_rate", "trades", "closed", "win_rate",
                                                  "max_dd_pct", "sharpe", "adequate")} for c in rows],
                  "main": {k: an["main"][k] for k in ("net_return_pct", "gross_return_pct", "fees", "sharpe", "max_dd_pct", "closed")},
                  "top": top["id"] if top else None, "adopted": adopted,
                  "not_adopted_because": None if win else why_not, "main_rules": state.get("main_rules")}

    report = {
        "date": date, "generated": int(now), "equity_start": start_eq, "equity_end": end_eq,
        "week_pnl": round(end_eq - start_eq, 2), "week_return_pct": round((end_eq / start_eq - 1) * 100, 3),
        "total_return_pct": round((end_eq / config.STARTING_BANKROLL - 1) * 100, 3),
        "max_drawdown_week": round(max_drawdown([e["equity"] for e in wk_eq]) if wk_eq else 0, 4),
        "metrics": metrics, "scores": {n: round(score(metrics[n]), 3) for n in metrics},
        "allocation_old": old_alloc, "allocation_new": state["allocation"],
        "status": state["strategy_status"], "param_changes": param_changes,
        "calibration_fit": state["calibration_fit"], "autocorr": rho, "changes": changes, "combos": combos,
    }
    md = render_md(report)
    if write:
        R = config.REPORTS_DIR
        save_json(R / f"{date}.json", report)
        (R / f"{date}.md").write_text(md)
        save_json(R / "latest.json", report)
        (R / "latest.md").write_text(md)
        idx = load_json(R / "index.json", [])
        idx = [i for i in idx if i["date"] != date] + [{"date": date, "week_pnl": report["week_pnl"],
                                                         "return_pct": report["week_return_pct"]}]
        save_json(R / "index.json", sorted(idx, key=lambda i: i["date"])[-104:])
        state["last_weekly"] = int(now)
        save_json(S / "portfolio.json", state, compact=True)
        publish(state, strategies, calibration)
    return report, md


def _f(v, fmt="{:.2f}"):
    return "—" if v is None else fmt.format(v)


def render_md(r):
    L = [f"# Weekly strategy review — {r['date']}", "",
         "_Paper trading only. Hypothetical results; no real orders._", "",
         f"- Equity: **${r['equity_end']:,.2f}** (week {r['week_pnl']:+,.2f} / {r['week_return_pct']:+.2f}%, "
         f"since start {r['total_return_pct']:+.2f}%)",
         f"- Max drawdown this week: {r['max_drawdown_week']:.2%}",
         f"- Price-change autocorrelation (lag 1): {_f(r['autocorr'], '{:+.3f}')}",
         f"- Calibration fit: long beta={r['calibration_fit']['beta_long']:.3f} (n={r['calibration_fit']['n_long']}), "
         f"short beta={r['calibration_fit']['beta_short']:.3f} (n={r['calibration_fit']['n_short']})", "",
         "## Scorecard", "",
         "| Strategy | Week P&L | Total P&L | Ret/sleeve | Sharpe | Sortino | Hit | Brier | MaxDD | Score | Alloc |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n, m in r["metrics"].items():
        L.append(f"| {n} | {m['week_pnl']:+,.2f} | {m['total_pnl']:+,.2f} | {m['week_return']:+.2%} | "
                 f"{_f(m['sharpe'])} | {_f(m['sortino'])} | {_f(m['hit_rate'], '{:.0%}')} | {_f(m['brier'], '{:.3f}')} | "
                 f"{m['max_drawdown']:.1%} | {r['scores'][n]:+.2f} | {r['allocation_old'].get(n, 0):.1%} → {r['allocation_new'].get(n, 0):.1%} |")
    L += ["", "## What changed and why", ""]
    L += [f"- {c}" for c in r["changes"]] or ["- No changes (insufficient evidence)."]
    cb = r.get("combos")
    if cb:
        mn = cb["main"]
        L += ["", "## Expiry bucket × take-profit combos (net of all fees)", "",
              f"Main book: net {mn['net_return_pct']:+.2f}% (gross {mn['gross_return_pct']:+.2f}%), fees ${mn['fees']:,.2f}, "
              f"Sharpe {_f(mn['sharpe'])}, max DD {mn['max_dd_pct']:.2f}%, {mn['closed']} closed.", "",
              "| # | Expiry | Exit rule | Net ret | Gross ret | Fees | Saved vs taker | Fill rate | Trades | Closed | Win | MaxDD | Sharpe |",
              "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for i, c in enumerate(cb["ranking"][:12], 1):
            L.append(f"| {i} | {c['bucket_label']} | {c['rule_label']} | {c['net_return_pct']:+.2f}% | {c['gross_return_pct']:+.2f}% | "
                     f"${c['fees']:,.2f} | ${c['fees_saved']:,.2f} | {_f(c['fill_rate'], '{:.0%}')} | {c['trades']} | {c['closed']} | "
                     f"{_f(c['win_rate'], '{:.0%}')} | {c['max_dd_pct']:.1f}% | {_f(c['sharpe'])}{'' if c['adequate'] else ' *'} |")
        L.append("")
        L.append("_* small sample_")
        if cb["adopted"]:
            L += ["", "Adopted into the main book:"] + [f"- {a}" for a in cb["adopted"]]
        else:
            L += ["", f"Main rules unchanged — top combo `{cb['top']}` not adopted: {', '.join(cb['not_adopted_because'] or [])}."]
    L += ["", "## Tuning notes", ""]
    for n, pc in r["param_changes"].items():
        for why in pc["reasons"]:
            L.append(f"- {n}: {why}")
    return "\n".join(L) + "\n"


def main():
    rep, md = weekly()
    print(md)


if __name__ == "__main__":
    main()
