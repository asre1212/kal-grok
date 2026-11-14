from conftest import mk
from kalbot.portfolio import Portfolio, pos_key
from kalbot.execution import Book, simulate_buy, simulate_sell
from kalbot.engine import Engine
from kalbot import config


def fresh():
    st = {"params": config.default_params(), "allocation": dict(config.INITIAL_ALLOCATION)}
    return st, Portfolio(st)


def test_settlement_yes_and_no():
    st, pf = fresh()
    m = mk("M1", 0.60, 0.62)
    pf.buy("longshot_fade", m, "yes", 100, 62.0, 1.65, p_model=0.7)
    pf.buy("longshot_fade", m, "no", 50, 20.0, 0.84, p_model=0.3)
    cash0 = pf.cash
    pnl_y = pf.settle(pos_key("longshot_fade", "M1", "yes"), 1.0, "yes")
    pnl_n = pf.settle(pos_key("longshot_fade", "M1", "no"), 1.0, "yes")
    assert round(pnl_y, 2) == round(100 - 63.65, 2)
    assert round(pnl_n, 2) == round(-20.84, 2)
    assert round(pf.cash - cash0, 2) == 100.0
    assert pf.positions == {}
    assert round(pf.p["realized"], 2) == round(pnl_y + pnl_n, 2)
    assert st["closed"][0]["outcome"] == 1 and st["closed"][1]["outcome"] == 0


def test_scalar_settlement():
    st, pf = fresh()
    pf.buy("event_arb", mk("S", 0.4, 0.42), "no", 10, 5.8, 0.0)
    pnl = pf.settle(pos_key("event_arb", "S", "no"), 0.3, "scalar")
    assert round(pnl, 2) == round(10 * 0.7 - 5.8, 2)


def test_book_walk_and_depth_cap():
    b = Book(yes_bids=[(0.40, 100), (0.39, 50)], no_bids=[(0.55, 30), (0.54, 40)])
    # YES asks = 1 - NO bids: 0.45 x30, 0.46 x40
    f = simulate_buy(b, "yes", 1000, 0.46)
    assert f["qty"] == 70 and abs(f["cost"] - (30 * 0.45 + 40 * 0.46)) < 1e-9
    f2 = simulate_buy(b, "yes", 1000, 0.45)
    assert f2["qty"] == 30
    s = simulate_sell(b, "yes", 120)
    assert s["qty"] == 120 and abs(s["proceeds"] - (100 * 0.40 + 20 * 0.39)) < 1e-9


def test_engine_take_respects_caps_and_fees():
    st, pf = fresh()
    now = 1_800_000_000
    m = mk("M2", 0.50, 0.51, now=now)
    eng = Engine(st, pf, {"M2": m}, client=None, now=now)
    pos = eng.take("longshot_fade", m, "yes", 0.70, 0.02, urgent=True)
    assert pos is not None
    assert pos["cost"] <= config.RISK["max_market_frac"] * 100_000 + 1
    # no edge after fees -> no trade
    m3 = mk("M3", 0.50, 0.51, now=now)
    eng.markets["M3"] = m3
    assert eng.take("longshot_fade", m3, "yes", 0.52, 0.02, urgent=True) is None
