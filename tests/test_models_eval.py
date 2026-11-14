import random
from kalbot.models import temp_bucket_prob, debias, fit_beta, sigmoid, logit
from kalbot.evaluate import reallocate
from kalbot.strategies.drift import drift_signal
from kalbot import config


def test_temp_buckets_sum_to_one():
    mu, sd = 50.3, 2.8
    ps = [temp_bucket_prob(mu, sd, "less", None, 47)]
    ps += [temp_bucket_prob(mu, sd, "between", lo, lo + 1) for lo in range(47, 55, 2)]
    ps += [temp_bucket_prob(mu, sd, "greater", 54, None)]
    assert abs(sum(ps) - 1) < 1e-9


def test_debias_sharpens_extremes():
    assert debias(0.05, 1.2) < 0.05 and debias(0.95, 1.2) > 0.95
    assert abs(debias(0.5, 1.2) - 0.5) < 1e-12


def test_fit_beta_recovers_bias():
    rng = random.Random(1)
    ps, ys = [], []
    for _ in range(4000):
        p = rng.uniform(0.03, 0.97)
        ps.append(p)
        ys.append(1 if rng.random() < sigmoid(1.4 * logit(p)) else 0)
    a, b, n = fit_beta(ps, ys, prior_beta=1.0, ridge=1.0)
    assert 1.25 < b < 1.55 and abs(a) < 0.15


def test_reallocate_respects_floor_cap_reserve():
    alloc = dict(config.INITIAL_ALLOCATION)
    m0 = {"week_return": 0.0, "sharpe": None, "wins_recent": 0, "losses_recent": 0}
    metrics = {n: dict(m0) for n in alloc}
    metrics["event_arb"].update(week_return=0.2, sharpe=3, wins_recent=40)
    metrics["momentum"].update(week_return=-0.2, sharpe=-3, losses_recent=40)
    new = reallocate(alloc, metrics, {"weather_nws": "probation"}, random.Random(0))
    assert abs(sum(new.values()) - (1 - config.RESERVE_WEIGHT)) < 1e-3
    assert new["weather_nws"] == config.PROBATION_WEIGHT
    assert all(v <= config.ALLOC_CAP + 1e-9 for v in new.values())
    assert all(v >= config.ALLOC_FLOOR - 1e-9 for k, v in new.items() if k != "weather_nws")
    assert new["event_arb"] > alloc["event_arb"] and new["momentum"] < alloc["momentum"]


def test_drift_signal_direction():
    hist = [[i, 40, 42, 0] for i in range(5)] + [[5, 50, 52, 0]]   # jump +10c
    assert drift_signal(hist, 0.5) > 0.51       # momentum extrapolates up
    assert drift_signal(hist, -0.5) < 0.51      # reversion fades
    flat = [[i, 40, 42, 0] for i in range(6)]
    assert drift_signal(flat, 0.5) is None
