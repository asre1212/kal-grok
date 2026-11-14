"""Dry run against the live public API (skipped with KALGROK_OFFLINE=1). Writes nothing."""
import os
import pytest

from kalbot.api import KalshiPublic, compact_market
from kalbot.engine import Engine
from kalbot.portfolio import Portfolio
from kalbot.strategies import build_strategies
from kalbot import config

pytestmark = pytest.mark.skipif(os.environ.get("KALGROK_OFFLINE") == "1", reason="offline")


def test_live_dry_run():
    c = KalshiPublic()
    markets = {}
    for i, e in enumerate(c.iter_open_events(max_pages=3)):
        for m in e.get("markets") or []:
            cm = compact_market(m, e)
            if cm["yb"] > 0 and cm["ya"] > 0 and cm["close"]:
                markets[cm["t"]] = cm
    assert len(markets) > 20
    t = next(iter(markets))
    ob = c.orderbook(t)
    assert "yes_bids" in ob and "no_bids" in ob
    st = {"params": config.default_params(), "allocation": dict(config.INITIAL_ALLOCATION)}
    pf = Portfolio(st)
    import time
    eng = Engine(st, pf, markets, client=c, now=time.time())
    for s in build_strategies():
        if s.name == "weather_nws":
            continue
        s.manage(eng)
        s.run(eng)
    assert pf.cash >= 0
    assert pf.equity() > 0
