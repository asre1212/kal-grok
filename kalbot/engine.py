"""Execution context shared by strategies: risk limits, sizing, order simulation."""
import math
from collections import defaultdict

from . import config
from .execution import Book, simulate_buy, simulate_sell
from .fees import fee_per_contract
from .sizing import kelly_contracts, cap_contracts


class Engine:
    def __init__(self, state, portfolio, markets, client=None, history=None, now=None, dry=False):
        self.state = state
        self.pf = portfolio
        self.markets = markets              # ticker -> compact market
        self.events = defaultdict(list)
        for m in markets.values():
            self.events[m["e"]].append(m)
        self.client = client
        self.history = history or {}
        self.now = now
        self.dry = dry
        self.books = {}
        self.ob_calls = 0
        self.log = []                        # human-readable decisions
        self.params = state["params"]
        self.alloc = state["allocation"]
        self.status = state.setdefault("strategy_status", {})
        self.equity = portfolio.equity()
        peak = max(portfolio.p["peak_equity"], self.equity)
        self.drawdown = 1 - self.equity / peak if peak > 0 else 0
        r = config.RISK
        self.dd_mult = 1.0
        if self.drawdown >= r["dd_soft"]:
            self.dd_mult = 0.5
        self.halt_directional = self.drawdown >= r["dd_hard"]
        self.ex = portfolio.exposures()
        self.claimed = {}                    # ticker -> side chosen this run (ensemble conflict guard)
        for p in portfolio.positions.values():
            self.claimed.setdefault(p["ticker"], p["side"])

    def refresh_exposure(self):
        self.ex = self.pf.exposures()
        self.equity = self.pf.equity()

    # ---------- books ----------
    def book(self, m):
        t = m["t"]
        if t in self.books:
            return self.books[t]
        b = None
        if self.client is not None and self.ob_calls < config.RISK["max_orderbooks_per_run"]:
            try:
                self.ob_calls += 1
                b = Book.from_api(self.client.orderbook(t))
            except Exception:
                b = None
        if b is None or (not b.yes_bids and not b.no_bids):
            b = Book.from_top(m)
        self.books[t] = b
        return b

    # ---------- risk ----------
    def sleeve(self, strat):
        return self.alloc.get(strat, 0.0) * self.equity

    def room(self, strat, m, arb=False):
        r = config.RISK
        E = self.equity
        ev_cap = r["max_arb_event_frac"] if arb else r["max_event_frac"]
        rooms = [
            r["max_market_frac"] * E * (2 if arb else 1) - self.ex["market"][m["t"]],
            ev_cap * E - self.ex["event"][m["e"]],
            r["max_series_frac"] * E - self.ex["series"][m["s"]],
            r["max_category_frac"] * E - self.ex["cat"][m["cat"]],
            self.sleeve(strat) - self.ex["strategy"][strat],
            self.pf.cash - r["min_cash_reserve_frac"] * E,
        ]
        return max(0.0, min(rooms))

    def active(self, strat):
        return self.status.get(strat, "active") != "retired" and self.alloc.get(strat, 0) > 0

    def _book_exposure(self, strat, m, dollars):
        for k, v in (("market", m["t"]), ("event", m["e"]), ("series", m["s"]),
                     ("cat", m["cat"]), ("strategy", strat)):
            self.ex[k][v] += dollars

    def holds(self, strat, ticker=None, event=None, group=None):
        for p in self.pf.positions.values():
            if p["strategy"] != strat:
                continue
            if (ticker and p["ticker"] == ticker) or (event and p["event"] == event) or (group and p.get("group") == group):
                return True
        return False

    def conflict(self, m, side):
        c = self.claimed.get(m["t"])
        return c is not None and c != side

    # ---------- directional taker trade ----------
    def take(self, strat, m, side, p_model, min_edge, note=""):
        """Kelly-sized taker buy of `side` if edge after fees >= min_edge."""
        if self.halt_directional or not self.active(strat) or self.conflict(m, side):
            return None
        if self.holds(strat, ticker=m["t"]):
            return None   # don't re-buy the same displayed liquidity every snapshot
        ask = m["ya"] if side == "yes" else m["na"]
        if not (0 < ask < 1):
            return None
        fpc = fee_per_contract(ask, m.get("fm", 1.0))
        gross = p_model - ask
        edge = gross - fpc
        if edge < min_edge or (gross > 0 and fpc / gross > config.RISK["max_fee_to_edge"]):
            return None
        qty = kelly_contracts(p_model, ask, fpc, self.sleeve(strat),
                              config.RISK["kelly_fraction"], self.dd_mult)
        qty = cap_contracts(qty, ask + fpc, self.room(strat, m))
        if qty < config.RISK["min_contracts"]:
            return None
        # limit price: highest price that still clears min_edge
        limit = math.floor((p_model - min_edge - fpc) * 100 + 1e-9) / 100
        fill = simulate_buy(self.book(m), side, qty, max(limit, ask), m.get("fm", 1.0))
        if fill["qty"] < config.RISK["min_contracts"]:
            return None
        # re-check realised edge with actual avg price and fees
        real_edge = p_model - (fill["cost"] + fill["fee"]) / fill["qty"]
        if real_edge < min_edge * 0.5:
            return None
        mid_yes = (m["yb"] + m["ya"]) / 2
        p_mkt = round(mid_yes if side == "yes" else 1 - mid_yes, 4)
        pos = self.pf.buy(strat, m, side, fill["qty"], fill["cost"], fill["fee"], p_model, note=note, p_mkt=p_mkt)
        if pos:
            self._book_exposure(strat, m, fill["cost"] + fill["fee"])
            self.claimed[m["t"]] = side
            self.log.append(f"{strat}: BUY {fill['qty']} {side.upper()} {m['t']} @ {fill['avg']:.2f} "
                            f"(p={p_model:.3f}, edge={real_edge:.3f}) {note}")
        return pos

    # ---------- multi-leg basket (arbitrage) ----------
    def take_basket(self, strat, legs, guaranteed_payout, min_profit, group, note=""):
        """legs: list of (m, side). Buys equal qty of every leg if the locked-in profit per
        basket (guaranteed_payout - cost - fees) >= min_profit after walking real depth."""
        if not self.active(strat) or self.holds(strat, group=group):
            return None
        books = [(m, side, self.book(m)) for m, side in legs]
        # max qty with top-of-book depth on every leg
        depths = []
        for m, side, b in books:
            px, sz = b.best_ask(side)
            if px is None or px >= 1:
                return None
            depths.append(sz)
        q = int(min(depths))
        unit_cost = sum(b.best_ask(side)[0] for m, side, b in books)
        if unit_cost <= 0:
            return None
        room = min(self.room(strat, m, arb=True) for m, _ in legs)
        q = min(q, int(room / max(unit_cost / len(legs), 0.01) / 1))
        q = min(q, int(self.sleeve(strat) * 0.25 / unit_cost))  # diversify across baskets
        while q >= config.RISK["min_contracts"]:
            fills = [simulate_buy(b, side, q, 0.99, m.get("fm", 1.0)) for m, side, b in books]
            if all(fl["qty"] == q for fl in fills):
                cost = sum(fl["cost"] + fl["fee"] for fl in fills)
                profit = guaranteed_payout * q - cost
                if profit / q >= min_profit:
                    if cost > self.pf.cash - config.RISK["min_cash_reserve_frac"] * self.equity:
                        return None
                    for (m, side, b), fl in zip(books, fills):
                        self.pf.buy(strat, m, side, q, fl["cost"], fl["fee"], p_model=None, group=group, note=note)
                        self._book_exposure(strat, m, fl["cost"] + fl["fee"])
                        self.claimed.setdefault(m["t"], side)
                    self.log.append(f"{strat}: BASKET x{q} {group} cost={cost:.2f} locked profit={profit:.2f} {note}")
                    return {"qty": q, "cost": cost, "profit": profit}
                return None
            q = int(q * 0.6)
        return None

    # ---------- exits ----------
    def exit_position(self, key, reason, min_price=0.0):
        pos = self.pf.positions.get(key)
        m = self.markets.get(pos["ticker"]) if pos else None
        if not pos or not m:
            return None
        fill = simulate_sell(self.book(m), pos["side"], pos["qty"], min_price, pos.get("fm", 1.0))
        if fill["qty"] <= 0:
            return None
        pnl = self.pf.sell(key, fill["qty"], fill["proceeds"], fill["fee"], reason)
        self.log.append(f"{pos['strategy']}: SELL {fill['qty']} {pos['side'].upper()} {pos['ticker']} "
                        f"@ {fill['avg']:.2f} pnl={pnl:+.2f} ({reason})")
        return pnl
