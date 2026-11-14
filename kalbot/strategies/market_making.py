"""Simulated passive market making on wide-spread markets.

Each run we post a 1-cent-improved bid on BOTH sides (YES bid at yes_bid+1c and NO bid at
no_bid+1c). If both fill, YES+NO nets to a guaranteed $1 (Kalshi nets opposite positions),
locking in roughly spread - 2c. Inventory is skewed: the side that flattens inventory is
quoted larger. Fills are CONSERVATIVE and checked on the next snapshot:
  * a resting buy at price q fills only if the market traded through it (current ask <= q,
    or last trade <= q - 1c with new volume), and
  * at most fill_frac x (new volume since posting) contracts fill (queue position haircut).
Unfilled quotes are cancelled each run. Maker fees are charged on series that have them.
"""
from .base import Strategy
from ..fees import maker_fee


class MarketMaking(Strategy):
    name = "market_making"
    description = "Passive two-sided quoting on wide spreads with conservative fill model."
    max_markets = 20

    def manage(self, eng):
        P = eng.params[self.name]
        still = []
        for r in eng.pf.p["resting"]:
            if r["strategy"] != self.name:
                still.append(r)
                continue
            m = eng.markets.get(r["ticker"])
            if not m:
                continue  # market gone / filtered: cancel
            dv = max(0.0, m["vol"] - r["vol"])
            ask = m["ya"] if r["side"] == "yes" else m["na"]
            last_side = m["lp"] if r["side"] == "yes" else (1 - m["lp"] if m["lp"] > 0 else 1)
            through = (0 < ask <= r["price"]) or (dv > 0 and 0 < last_side <= r["price"] - 0.01)
            if not through or dv <= 0:
                continue  # cancel (re-quoted below)
            q = int(min(r["qty"], P["fill_frac"] * dv))
            if q < 1:
                continue
            fee = maker_fee(q, r["price"], m.get("fm", 1.0), m.get("ft", "quadratic"))
            if eng.pf.buy(self.name, m, r["side"], q, round(q * r["price"], 4), fee,
                          p_model=None, maker=True, note="passive fill"):
                eng.log.append(f"market_making: FILL {q} {r['side'].upper()} {m['t']} @ {r['price']:.2f} (maker)")
        eng.pf.p["resting"] = still
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
                eng.pf.p["resting"].append({"strategy": self.name, "ticker": m["t"], "side": side,
                                            "price": px, "qty": qty, "vol": m["vol"], "ts": int(eng.now),
                                            "kind": "entry"})
                eng.ex["strategy"][self.name] += qty * px
                eng.ex["market"][m["t"]] += qty * px
        eng.state.setdefault("_run_notes", {})[self.name] = f"quoting {len(chosen)} markets"
