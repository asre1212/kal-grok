from conftest import mk
from kalbot.strategies.arbitrage import (no_basket_candidates, yes_basket_profit,
                                        is_exhaustive_buckets, ladder_violations, ladder_groups)


def test_no_basket_detects_sum_of_bids_over_one():
    ms = [mk("A", 0.40, 0.42, me=True), mk("B", 0.35, 0.37, me=True), mk("C", 0.33, 0.35, me=True)]
    legs, profit = no_basket_candidates(ms)
    assert len(legs) == 3
    # (3-1) - sum(no asks .60+.65+.67) - fees ~ 0.08 - ~0.046
    assert 0.02 < profit < 0.08


def test_no_basket_absent_when_fair():
    ms = [mk("A", 0.30, 0.32, me=True), mk("B", 0.30, 0.32, me=True), mk("C", 0.30, 0.32, me=True)]
    _, profit = no_basket_candidates(ms)
    assert profit < 0


def buckets():
    return [mk("T49", 0.1, 0.11, st="less", cap=49), mk("B49.5", 0.2, 0.21, st="between", fl=49, cap=50),
            mk("B51.5", 0.3, 0.31, st="between", fl=51, cap=52), mk("T52", 0.2, 0.21, st="greater", fl=52)]


def test_exhaustive_detection():
    assert is_exhaustive_buckets(buckets())
    gap = buckets()
    gap[2]["fl"] = 53
    assert not is_exhaustive_buckets(gap)


def test_yes_basket_profit():
    b = buckets()   # asks sum 0.84 -> ~0.16 minus fees
    p = yes_basket_profit(b)
    assert 0.10 < p < 0.16


def test_ladder_violation():
    lo = mk("X-T10", 0.30, 0.32, st="greater", fl=10)
    hi = mk("X-T20", 0.40, 0.42, st="greater", fl=20)   # P(>20) bid > P(>10) ask: violation
    v = ladder_violations([lo, hi])
    assert v and v[0][0]["t"] == "X-T10" and v[0][1]["t"] == "X-T20" and v[0][2] > 0.04
    ok = mk("X-T20", 0.20, 0.22, st="greater", fl=20)
    assert ladder_violations([lo, ok]) == []


def test_ladder_groups_split_by_underlying():
    a = mk("EV-P1-10", 0.3, 0.32, st="greater", fl=10, cs="player=1")
    b = mk("EV-P2-20", 0.4, 0.42, st="greater", fl=20, cs="player=2")
    g = ladder_groups([a, b])
    assert all(len(v) == 1 for v in g.values())
