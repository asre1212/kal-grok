import shutil
import subprocess
from pathlib import Path

from conftest import mk
from kalbot import config
from kalbot.engine import Engine
from kalbot.exits import apply_exit_rules
from kalbot.orders import entry_price, process_resting, post
from kalbot.portfolio import Portfolio, pos_key
from kalbot.variants import run_variants, analysis, robust_winner, adopt, book_stats

NOW = 1_800_000_000


def fresh():
    st = {"params": config.default_params(), "allocation": dict(config.INITIAL_ALLOCATION)}
    return st, Portfolio(st)


def test_bucket_of():
    assert config.bucket_of(mk("A", .5, .51, exp_in=3600 * 20, now=NOW), NOW) == "1d"
    assert config.bucket_of(mk("A", .5, .51, exp_in=86400 * 10, now=NOW), NOW) == "1m"
    assert config.bucket_of(mk("A", .5, .51, exp_in=86400 * 90, now=NOW), NOW) == "6m"
    assert config.bucket_of(mk("A", .5, .51, exp_in=86400 * 300, now=NOW), NOW) == "6m+"


def test_entry_price_never_crosses():
    assert entry_price(mk("A", .40, .45), "yes") == .41        # improve bid by 1c
    assert entry_price(mk("A", .40, .41), "yes") == .40        # 1c spread: join bid
    assert entry_price(mk("A", .40, .45), "no") == .56          # NO bid .55 -> .56 < NO ask .60


def test_limit_buy_needs_trade_through_and_haircut():
    st, pf = fresh()
    m = mk("A", .40, .45, now=NOW)
    eng = Engine(st, pf, {"A": m}, now=NOW)
    post(eng, "longshot_fade", m, "yes", "buy", .41, 500, p_model=.5)
    # next snapshot: no trade-through -> nothing fills, order survives (age 1 < max_age 2)
    m2 = dict(m, vol=m["vol"] + 1000)
    eng2 = Engine(st, pf, {"A": m2}, now=NOW + 3600)
    process_resting(eng2)
    assert pf.positions == {} and len(pf.p["resting"]) == 1
    # ask dropped to our price with 1000 new contracts traded -> 10% queue haircut = 100
    m3 = dict(m, ya=.41, nb=.59, vol=m["vol"] + 2000)
    eng3 = Engine(st, pf, {"A": m3}, now=NOW + 7200)
    process_resting(eng3)
    pos = pf.positions[pos_key("longshot_fade", "A", "yes")]
    assert pos["qty"] == 200 and abs(pos["cost"] - 200 * .41) < 1e-9     # maker fee 0 on quadratic series
    assert pf.p["resting"] == []                                           # age 2 -> remainder cancelled
    o = pf.p["orders"]
    assert o["filled_qty"] == 200 and o["cancelled_qty"] == 300
    assert o["fees_saved"] > 0 and o["price_improvement"] > 0


def test_maker_fee_on_maker_series():
    st, pf = fresh()
    m = mk("A", .40, .45, now=NOW)
    m["ft"] = "quadratic_with_maker_fees"
    eng = Engine(st, pf, {"A": m}, now=NOW)
    post(eng, "market_making", m, "yes", "buy", .41, 100, max_age=1)
    m2 = dict(m, ya=.38, nb=.62, vol=m["vol"] + 1000)      # deep trade-through -> 35%
    process_resting(Engine(st, pf, {"A": m2}, now=NOW + 1))
    pos = pf.positions[pos_key("market_making", "A", "yes")]
    assert pos["qty"] == 100
    assert abs(pos["cost"] - (41.0 + 0.43)) < 1e-9          # 0.0175*100*.41*.59 = .4233 -> .43


def test_take_profit_limit_then_taker_and_stop_loss():
    st, pf = fresh()
    m = mk("A", .40, .42, now=NOW)
    pf.buy("longshot_fade", m, "yes", 100, 40.0, 0.0, p_model=.5)
    k = pos_key("longshot_fade", "A", "yes")
    up = mk("A", .55, .57, now=NOW)        # mid .56 -> +40%
    pf.mark(k, up)
    eng = Engine(st, pf, {"A": up}, now=NOW)
    apply_exit_rules(eng, tp=0.25)
    r = pf.p["resting"]
    assert len(r) == 1 and r[0]["action"] == "sell" and r[0]["price"] >= .56   # never below ask-1c / target
    # exit order expires unfilled -> next time sell at bid as taker (net of fee)
    pf.positions[k]["exit_expired"] = 1
    pf.p["resting"] = []
    apply_exit_rules(Engine(st, pf, {"A": up}, now=NOW), tp=0.25)
    assert k not in pf.positions and pf.p["n_tp"] == 1
    sell = [t for t in st["trades"] if t["action"] == "SELL"][-1]
    assert sell["price"] == .55 and sell["fee"] == 1.74      # 0.07*100*.55*.45 = 1.7325 -> 1.74
    # stop loss is always taker
    pf.buy("longshot_fade", m, "no", 100, 58.0, 0.0)
    k2 = pos_key("longshot_fade", "A", "no")
    dn = mk("A", .70, .72, now=NOW)        # NO mid .29 -> -50%
    pf.mark(k2, dn)
    apply_exit_rules(Engine(st, pf, {"A": dn}, now=NOW), tp=0.25, sl=0.25)
    assert k2 not in pf.positions and pf.p["n_sl"] == 1


def test_arb_legs_exempt_from_exits():
    st, pf = fresh()
    m = mk("A", .40, .42, now=NOW)
    pf.buy("event_arb", m, "yes", 100, 40.0, 0.0, group="NOB:X")
    k = pos_key("event_arb", "A", "yes")
    pf.mark(k, mk("A", .80, .82, now=NOW))
    apply_exit_rules(Engine(st, pf, {"A": m}, now=NOW), tp=0.10)
    assert k in pf.positions and pf.p["resting"] == []


class Buyer:
    name = "expiry_convergence"

    def manage(self, eng):
        pass

    def run(self, eng):
        for m in eng.markets.values():
            eng.take(self.name, m, "yes", 0.9, 0.01, urgent=True)


def test_variants_only_trade_their_bucket_and_report_net():
    main = {"params": config.default_params(), "allocation": dict(config.INITIAL_ALLOCATION),
            "equity_curve": [], "strategy_stats": {}}
    Portfolio(main)
    markets = {"D": mk("D", .60, .61, e="E1", exp_in=3600 * 30, now=NOW),
               "M": mk("M", .60, .61, e="E2", exp_in=86400 * 10, now=NOW),
               "Y": mk("Y", .60, .61, e="E3", exp_in=86400 * 300, now=NOW)}
    vs = {}
    run_variants(vs, markets, main, {}, None, {}, NOW, {}, [Buyer()])
    assert len(vs) == len(config.EXPIRY_BUCKETS) * len(config.EXIT_RULES)
    held = lambda vid: {p["ticker"] for p in vs[vid]["portfolio"]["positions"].values()}
    assert held("1d|hold") == {"D"} and held("1m|tp25") == {"M"} and held("6m+|tp100") == {"Y"}
    assert held("6m|hold") == set()
    assert "params" not in vs["1d|hold"]                       # shared params not persisted per variant
    an = analysis(vs, main, NOW)
    c = next(x for x in an["cells"] if x["id"] == "1d|hold")
    assert c["fees"] > 0 and abs(c["gross_pnl"] - (c["net_pnl"] + c["fees"])) < 0.02
    assert an["small_sample"] and an["caveat"]


def test_robust_winner_and_adopt():
    base = {"closed": 50, "settled": 30, "points": 30, "max_dd_pct": 3.0, "adequate": True, "trades": 80}
    win = {**base, "id": "1m|tp25", "bucket": "1m", "tp": 0.25, "sl": None, "rule_label": "+25%",
           "sharpe": 2.0, "net_return_pct": 4.0}
    an = {"main": {**base, "sharpe": 1.0, "net_return_pct": 1.0}, "ranking": ["1m|tp25"], "cells": [win]}
    w, top, why = robust_winner(an)
    assert w is win and not why
    rules = {"tp": None, "sl": None, "bucket_weights": {"1d": 1.0, "1m": 1.0, "6m": 1.0, "6m+": 1.0}}
    ch = adopt(rules, w)
    assert rules["tp"] == 0.10 and rules["bucket_weights"]["1m"] == 1.25 and rules["bucket_weights"]["1d"] == 0.9 and ch
    small = {**win, "closed": 5}
    w2, _, why2 = robust_winner({**an, "cells": [small]})
    assert w2 is None and "sample too small" in why2


def test_stamp_build(tmp_path):
    root = Path(__file__).resolve().parent.parent
    dst = tmp_path / "repo"
    shutil.copytree(root / "docs", dst / "docs", ignore=shutil.ignore_patterns("data"))
    shutil.copytree(root / "scripts", dst / "scripts")
    out = subprocess.run(["bash", str(dst / "scripts" / "stamp_build.sh")], capture_output=True, text=True, check=True)
    build = out.stdout.strip()
    assert build and "__BUILD__" not in build
    for f in ("index.html", "app.js", "sw.js"):
        txt = (dst / "docs" / f).read_text()
        assert "__BUILD__" not in txt and build in txt
    man = (dst / "docs" / "manifest.json").read_text()
    assert '"start_url": "./index.html"' in man and '"scope": "./"' in man
