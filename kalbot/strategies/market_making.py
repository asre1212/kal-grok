"""Simulated passive market making on wide-spread markets.

Each run we post a 1-cent-improved bid on BOTH sides (YES bid at yes_bid+1c and NO bid at
no_bid+1c). If both fill, YES+NO nets to a guaranteed $1 (Kalshi nets opposite positions),
locking in roughly spread - 2c. Inventory is skewed: the side that flattens inventory is
quoted larger. Fills use the shared conservative limit-order model in kalbot/orders.py (trade-through
required, queue haircut = fill_frac x new volume, partial fills); quotes live one snapshot and
are re-posted each run. Maker fees are charged on series that have them.
"""
from .base import Strategy
from ..orders import post as post_order


class MarketMaking(Strategy):
    name = "market_making"
    description = "Passive two-sided quoting on wide spreads with conservative fill model."
    max_markets = 20

    def manage(self, eng):
        # fills of resting quotes are handled by kalbot.orders.process_resting (shared model)
        self.net_pairs(eng)

    def net_pairs(self, eng):
        """YES+NO in the same market net to $1 (Kalshi auto-nets opposite positions)."""
        pos = self.my_positions(eng)
        by_t = {}
        for k, p in pos.items():
            by_t.setdefault(p["ticker"], {})[p["side"]] = k
        for t, sides in by_t.items():
            if "yes" in sides and "no" in sides:
                ky, kn = sides["yes"], sides["no"]
                q = min(eng.pf.positions[ky]["qty"], eng.pf.positions[kn]["qty"])
                if q > 0:
                    py = eng.pf.sell(ky, q, round(q * 1.0, 4), 0.0, "netted YES+NO -> $1")
                    pn = eng.pf.sell(kn, q, 0.0, 0.0, "netted YES+NO -> $1")
                    eng.log.append(f"market_making: NET {q} pairs in {t} pnl={py + pn:+.2f}")

    def run(self, eng):
        P = eng.params[self.name]
        if not eng.active(self.name) or eng.halt_directional:
            return
        inv = {}
        for p in self.my_positions(eng).values():
            inv.setdefault(p["ticker"], {})[p["side"]] = p["qty"]
        cands = []
        for m in eng.markets.values():
            if m["yb"] <= 0 or m["ya"] <= 0:
                continue
            spread = m["ya"] - m["yb"]
            mid = (m["ya"] + m["yb"]) / 2
            if spread < P["min_spread"] or not (0.10 <= mid <= 0.90) or m["v24"] < 200:
                continue
            if not m.get("exp") or m["exp"] - eng.now < 86400:
                continue
            cands.append((spread * min(m["v24"], 5000), m))
        cands.sort(key=lambda c: -c[0])
        chosen = [m for _, m in cands[: self.max_markets]]
        # always keep quoting markets where we hold inventory
        chosen_t = {m["t"] for m in chosen}
        for t in inv:
            if t not in chosen_t and t in eng.markets:
                chosen.append(eng.markets[t])
        base = int(P["quote_qty"])
        for m in chosen:
            held = inv.get(m["t"], {})
            for side in ("yes", "no"):
                bid = m["yb"] if side == "yes" else m["nb"]
                ask = m["ya"] if side == "yes" else m["na"]
                px = round(bid + 0.01, 2)
                if px >= ask or px <= 0 or px >= 0.99:
                    continue
                other = "no" if side == "yes" else "yes"
                qty = base + held.get(other, 0) - held.get(side, 0)   # skew toward flattening
                room = eng.room(self.name, m)
                qty = int(min(max(qty, 0), room / px))
                if qty < 5:
                    continue
                post_order(eng, self.name, m, side, "buy", px, qty, kind="entry", max_age=1,
                           ff=P["fill_frac"], note="MM quote")
                eng.ex["strategy"][self.name] += qty * px
                eng.ex["market"][m["t"]] += qty * px
        eng.state.setdefault("_run_notes", {})[self.name] = f"quoting {len(chosen)} markets"
