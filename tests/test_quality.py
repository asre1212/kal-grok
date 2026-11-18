from conftest import mk
from kalbot import config, quality
from kalbot.engine import Engine
from kalbot.portfolio import Portfolio
from kalbot.variants import tune_min_pop

NOW = 1_800_000_000


def fresh(rules=None, shared=None):
    st = {"params": config.default_params(), "allocation": dict(config.INITIAL_ALLOCATION)}
    return st, Portfolio(st)


def test_settlement_pop_is_adjusted_win_prob():
    m = mk("A", .84, .85, exp_in=86400 * 3, now=NOW)        # tight spread, liquid, near expiry
    q = quality.estimate("longshot_fade", m, "yes", .95, .85, 0.0, now=NOW)
    # p = mid + 0.8 * (0.95 - 0.845) = 0.929 ; no time haircut (< 7d), no spread penalty
    assert abs(q["pop"] - 0.929) < 1e-3
    assert abs(q["ev"] - (0.929 - 0.85)) < 1e-3
    assert q["exit_fee"] == 0.0


def test_fees_and_exit_fee_reduce_ev():
    m = mk("A", .49, .50, exp_in=86400 * 3, now=NOW)
    base = quality.estimate("longshot_fade", m, "yes", .60, .50, 0.0, now=NOW)
    taker = quality.estimate("longshot_fade", m, "yes", .60, .50, 0.0175, now=NOW)
    early = quality.estimate("longshot_fade", m, "yes", .60, .50, 0.0175, now=NOW, early_exit=True)
    assert taker["ev"] < base["ev"] and early["ev"] < taker["ev"] and early["exit_fee"] > 0


def test_spread_thin_and_time_haircuts():
    tight = mk("A", .79, .80, exp_in=86400 * 3, now=NOW)
    wide = mk("A", .72, .80, exp_in=86400 * 3, now=NOW)
    far = mk("A", .79, .80, exp_in=86400 * 120, now=NOW)
    thin = dict(tight, v24=10, oi=100)
    p = lambda m: quality.estimate("longshot_fade", m, "yes", .90, .80, 0.0, now=NOW)["pop"]
    assert p(wide) < p(tight) and p(thin) < p(tight) and p(far) < p(tight)


def test_hit_rate_shrinkage():
    assert quality.hit_factor("x", {}) == 1.0
    # 5 trades predicted at 0.8, all lost: shrunk well short of 0 (prior of 20 pseudo-trades)
    st = quality.hit_stats([{"strategy": "x", "pop": .8, "pnl": -1}] * 5)
    hf = quality.hit_factor("x", st)
    assert 0.7 <= hf < 1.0 and abs(hf - max(0.7, (0 + 16) / 25 / .8)) < 1e-9
    # strong track record raises, capped at 1.10
    st = quality.hit_stats([{"strategy": "x", "pop": .6, "pnl": 1}] * 200)
    assert quality.hit_factor("x", st) == 1.10


def test_drift_pop_uses_normal_approx():
    m = mk("A", .49, .51, exp_in=86400 * 3, now=NOW)
    q = quality.estimate("momentum", m, "yes", .60, .50, 0.0, now=NOW)
    assert 0.5 < q["pop"] < 0.75 and q["exit_fee"] > 0      # 1-sigma-ish call can't be a sure thing


def test_gate_and_kelly_mult():
    ok = {"pop": .8, "ev": .05, "edge_to_cost": .07}
    assert quality.gate(ok, .65) is None
    assert quality.gate(dict(ok, pop=.6), .65) == "low_pop"
    assert quality.gate(dict(ok, ev=.01), .65) == "low_ev"
    assert quality.gate(dict(ok, edge_to_cost=.01), .65) == "low_edge_to_cost"
    assert quality.kelly_mult(.65, .65) == 1.0
    assert abs(quality.kelly_mult(.775, .65) - 1.25) < 1e-9
    assert quality.kelly_mult(.95, .65) == 1.5
    assert quality.kelly_mult(.9, None) == 1.0


def test_engine_gates_ranks_and_caps(monkeypatch):
    monkeypatch.setitem(config.SELECT, "max_new_per_cycle", 1)
    st, pf = fresh()
    good = mk("G", .84, .85, e="EG", exp_in=3600 * 10, now=NOW)       # expiring soon -> taker path
    ok = mk("O", .80, .81, e="EO", exp_in=3600 * 10, now=NOW)
    bad = mk("B", .49, .50, e="EB", exp_in=3600 * 10, now=NOW)
    eng = Engine(st, pf, {m["t"]: m for m in (good, ok, bad)}, now=NOW, rules={"min_pop": .65})
    assert eng.take("expiry_convergence", bad, "yes", .56, .01) is None      # POP ~0.54 -> rejected
    assert eng.take("expiry_convergence", ok, "yes", .90, .01)               # queued
    assert eng.take("expiry_convergence", good, "yes", .97, .01)             # queued, better EV x POP
    assert pf.positions == {}                                                # nothing opened before flush
    assert eng.flush_pending() == 1
    held = {p["ticker"]: p for p in pf.positions.values()}
    assert list(held) == ["G"] and held["G"]["pop"] >= .9
    g = eng.gate_summary()
    assert g["seen"] == 3 and g["taken"] == 1 and g["rejected"] == {"low_pop": 1, "rank_cap": 1}
    assert pf.st["trades"][-1]["pop"] == held["G"]["pop"]


def test_high_pop_sized_up():
    def qty(min_pop):
        st, pf = fresh()
        m = mk("G", .84, .85, e="EG", exp_in=3600 * 10, now=NOW)
        m["yas"] = 100000
        eng = Engine(st, pf, {"G": m}, now=NOW, rules={"min_pop": min_pop})
        eng.take("expiry_convergence", m, "yes", .97, .01)
        eng.flush_pending()
        return sum(p["qty"] for p in pf.positions.values())
    q_ungated_like = qty(0.95)   # POP below threshold -> nothing
    assert q_ungated_like == 0
    assert qty(0.65) >= qty(0.90)        # boost relative to threshold: lower threshold -> bigger multiplier


def test_ungated_engine_unchanged():
    st, pf = fresh()
    m = mk("B", .49, .50, e="EB", exp_in=3600 * 10, now=NOW)
    eng = Engine(st, pf, {"B": m}, now=NOW)                 # no min_pop -> old behaviour, immediate
    assert eng.take("expiry_convergence", m, "yes", .60, .01)
    assert pf.positions and eng.pending == []


def _book(label, mp, ret, sharpe, n=40):
    return {"id": label, "label": label, "min_pop": mp, "net_return_pct": ret, "sharpe": sharpe, "max_dd_pct": 1.0,
            "closed": n, "settled": n, "points": n, "trades": n, "adequate": True, "return_pct": ret}


def test_weekly_min_pop_tuning_guardrails():
    rules = {"min_pop": 0.65}
    an = {"pop_books": [_book("all", None, 0.0, 0.1), _book("60", .6, 1.0, 0.5), _book("70", .7, 4.0, 2.0),
                        _book("80", .8, 0.5, 0.2)]}
    ch, why = tune_min_pop(an, rules)
    assert ch and rules["min_pop"] == 0.7 and not why
    # small samples -> no change
    rules = {"min_pop": 0.65}
    an["pop_books"] = [dict(b, closed=3) for b in an["pop_books"]]
    ch, why = tune_min_pop(an, rules)
    assert not ch and rules["min_pop"] == 0.65 and any("sample" in w for w in why)
