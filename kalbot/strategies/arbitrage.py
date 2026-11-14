"""Risk-free (modulo settlement/rule risk) arbitrage strategies.

EventArb (mutually-exclusive events):
  * NO basket: buying NO on any k outcomes of a mutually exclusive set pays >= k-1
    (at most one can be YES). Profit/basket = sum(yes_bid_i) - 1 - fees, i.e. it exists
    whenever the YES *bids* of a mutually-exclusive set sum to more than $1.
  * YES basket: only when the set is provably EXHAUSTIVE (contiguous integer buckets
    'less X' .. 'between' .. 'greater Y', e.g. weather highs): payout exactly 1, so profit
    = 1 - sum(yes_ask_i) - fees.

LadderArb (cross-market consistency): for 'greater' threshold ladders on the same
underlying, P(>a) >= P(>b) for a < b. Buying YES(>a) + NO(>b) pays >= $1 in every state,
so if yes_bid(>b) > yes_ask(>a) + fees the pair is a locked profit (mirror for 'less').
"""
from collections import defaultdict

from .base import Strategy
from ..fees import fee_per_contract


def is_exhaustive_buckets(markets):
    less = [m for m in markets if m["st"] == "less" and m["cap"] is not None]
    gr = [m for m in markets if m["st"] == "greater" and m["fl"] is not None]
    bt = sorted([m for m in markets if m["st"] == "between" and m["fl"] is not None and m["cap"] is not None],
                key=lambda m: m["fl"])
    if len(less) != 1 or len(gr) != 1 or len(bt) + 2 != len(markets) or not bt:
        return False
    if abs(less[0]["cap"] - bt[0]["fl"]) > 1e-9 or abs(bt[-1]["cap"] - gr[0]["fl"]) > 1e-9:
        return False
    for a, b in zip(bt, bt[1:]):
        if abs(a["cap"] + 1 - b["fl"]) > 1e-9:
            return False
    return True


def no_basket_candidates(markets):
    """Subset of ME markets whose YES bid exceeds the NO-leg fee; returns (legs, profit_per_basket)."""
    legs = []
    for m in markets:
        if m["yb"] <= 0 or m["na"] <= 0 or m["na"] >= 1:
            continue
        if m["yb"] - fee_per_contract(m["na"], m.get("fm", 1.0)) > 0:
            legs.append(m)
    if len(legs) < 2:
        return [], 0.0
    profit = (len(legs) - 1) - sum(m["na"] + fee_per_contract(m["na"], m.get("fm", 1.0)) for m in legs)
    return legs, profit


def yes_basket_profit(markets):
    if any(not (0 < m["ya"] < 1) for m in markets):
        return None
    return 1 - sum(m["ya"] + fee_per_contract(m["ya"], m.get("fm", 1.0)) for m in markets)


class EventArb(Strategy):
    name = "event_arb"
    description = "Mutually-exclusive outcome sets: NO baskets when YES bids sum > $1, YES baskets when exhaustive asks sum < $1."

    def run(self, eng):
        mp = eng.params[self.name]["min_profit"]
        meta = eng.state.get("_event_meta", {})
        found = 0
        for ev, ms in eng.events.items():
            if not ms or not ms[0]["me"] or len(ms) < 2:
                continue
            legs, profit = no_basket_candidates(ms)
            if legs and profit >= mp:
                found += 1
                eng.take_basket(self.name, [(m, "no") for m in legs], len(legs) - 1, mp,
                                group=f"NOB:{ev}", note=f"ME NO-basket k={len(legs)}")
            info = meta.get(ev, {})
            if info.get("exhaustive") and info.get("n") == len(ms):
                yp = yes_basket_profit(ms)
                if yp is not None and yp >= mp:
                    found += 1
                    eng.take_basket(self.name, [(m, "yes") for m in ms], 1.0, mp,
                                    group=f"YESB:{ev}", note="exhaustive YES-basket")
        eng.state.setdefault("_run_notes", {})[self.name] = f"{found} candidate baskets"


def ladder_groups(markets):
    g = defaultdict(list)
    for m in markets:
        if m["st"] in ("greater", "greater_or_equal") and m["fl"] is not None:
            g[(m["t"].rsplit("-", 1)[0], m["st"], m["cs"])].append(m)
        elif m["st"] in ("less", "less_or_equal") and m["cap"] is not None:
            g[(m["t"].rsplit("-", 1)[0], m["st"], m["cs"])].append(m)
    return g


def ladder_violations(group, min_profit=0.0):
    """Return list of (buy_yes_market, buy_no_market, profit_per_pair)."""
    out = []
    if not group:
        return out
    up = group[0]["st"].startswith("greater")
    key = (lambda m: m["fl"]) if up else (lambda m: m["cap"])
    ms = sorted(group, key=key)
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            if key(ms[i]) == key(ms[j]):
                continue
            # greater: YES on lower strike i, NO on higher strike j; less: YES on j, NO on i
            y, n = (ms[i], ms[j]) if up else (ms[j], ms[i])
            if not (0 < y["ya"] < 1 and 0 < n["na"] < 1):
                continue
            cost = y["ya"] + n["na"] + fee_per_contract(y["ya"], y.get("fm", 1)) + fee_per_contract(n["na"], n.get("fm", 1))
            if 1 - cost >= min_profit:
                out.append((y, n, round(1 - cost, 4)))
    out.sort(key=lambda t: -t[2])
    return out


class LadderArb(Strategy):
    name = "ladder_arb"
    description = "Monotonicity violations in threshold ladders (P(>a) must be >= P(>b) for a<b)."

    def run(self, eng):
        mp = eng.params[self.name]["min_profit"]
        found = 0
        for key, grp in ladder_groups(eng.markets.values()).items():
            if len(grp) < 2:
                continue
            used = set()
            for y, n, prof in ladder_violations(grp, mp):
                if y["t"] in used or n["t"] in used:
                    continue
                found += 1
                r = eng.take_basket(self.name, [(y, "yes"), (n, "no")], 1.0, mp,
                                    group=f"LAD:{y['t']}/{n['t']}", note=f"ladder pair edge {prof:.2f}")
                if r:
                    used.update((y["t"], n["t"]))
        eng.state.setdefault("_run_notes", {})[self.name] = f"{found} ladder violations"
