"""Execution context shared by strategies: risk limits, sizing, order simulation."""
import math
from collections import defaultdict

from . import config
from .execution import Book, simulate_buy, simulate_sell
from .fees import fee_per_contract
from .sizing import kelly_contracts, cap_contracts
from .orders import entry_price, post as post_order, has_resting, stats as order_stats
from . import quality


class Engine:
    def __init__(self, state, portfolio, markets, client=None, history=None, now=None, dry=False,
                 shared=None, rules=None):
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
        # one orderbook cache / fetch budget shared by main + shadow variants per cycle
        self.shared = shared if shared is not None else {}
        self.shared.setdefault("books", {})
        self.shared.setdefault("ob_calls", 0)
        self.books = self.shared["books"]
        self.rules = rules or {}
        # POP selectivity: None = take every candidate (old behaviour); else gate + rank + top-N
        self.min_pop = self.rules.get("min_pop")
        self.hit_stats = self.shared.get("hit_stats") or {}
        self.pending = []
        self.gate = {"min_pop": self.min_pop, "seen": 0, "passed": 0, "taken": 0, "skipped": 0,
                     "rejected": {}, "quotes_seen": 0, "quotes_skipped": 0, "avg_pop_taken": None}
        self._pops_taken = []
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

    @property
    def ob_calls(self):
        return self.shared["ob_calls"]

    def bucket_weight(self, m):
        bw = self.rules.get("bucket_weights") or {}
        if not bw or self.now is None:
            return 1.0
        return float(bw.get(config.bucket_of(m, self.now), 1.0))

    def cooling(self, ticker):
        cd = self.pf.p.get("cooldown") or {}
        ts = cd.get(ticker)
        return ts is not None and self.now is not None and self.now - ts < config.EXIT_COOLDOWN_HOURS * 3600

    def refresh_exposure(self):
        self.ex = self.pf.exposures()
        self.equity = self.pf.equity()

    # ---------- books ----------
    def book(self, m):
        t = m["t"]
        if t in self.books:
            return self.books[t]
        b = None
        if self.client is not None and self.shared["ob_calls"] < config.RISK["max_orderbooks_per_run"]:
            try:
                self.shared["ob_calls"] += 1
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

    # ---------- POP scoring / gating ----------
    def _early_exit(self):
        return bool(self.rules.get("tp") or self.rules.get("sl"))

    def _entry_quote(self, m, side, urgent):
        """(mode, price, entry fee per contract) we'd use for this entry right now."""
        hours = ((m.get("exp") or m.get("close") or 0) - (self.now or 0)) / 3600
        if config.ORDER_MODE == "limit" and not urgent and hours > config.LIMIT["taker_hours"]:
            q = entry_price(m, side)
            if q is None:
                return None
            ft = m.get("ft", "quadratic")
            mfpc = fee_per_contract(q, m.get("fm", 1.0)) * (config.MAKER_RATE / config.TAKER_RATE if "maker" in (ft or "") else 0.0)
            return "limit", q, mfpc
        ask = m["ya"] if side == "yes" else m["na"]
        if not (0 < ask < 1):
            return None
        return "taker", ask, fee_per_contract(ask, m.get("fm", 1.0))

    def score(self, strat, m, side, p_model, urgent=False):
        eq = self._entry_quote(m, side, urgent)
        if eq is None:
            return None
        mode, px, fpc = eq
        q = quality.estimate(strat, m, side, p_model, px, fpc, now=self.now, stats=self.hit_stats,
                             early_exit=self._early_exit())
        q["mode"], q["price"] = mode, px
        return q

    def _reject(self, reason, n=1):
        r = self.gate["rejected"]
        r[reason] = r.get(reason, 0) + n
        self.gate["skipped"] += n

    def quote_ok(self, strat, m, side, px):
        """Market-making quotes in selective books: only the side whose POP clears the bar."""
        if self.min_pop is None:
            return True
        self.gate["quotes_seen"] += 1
        q = quality.estimate(strat, m, side, quality.side_mid(m, side), px, 0.0,
                             now=self.now, stats=self.hit_stats, early_exit=self._early_exit())
        if quality.gate(q, self.min_pop):
            self.gate["quotes_skipped"] += 1
            return False
        return True

    # ---------- directional trade ----------
    def take(self, strat, m, side, p_model, min_edge, note="", urgent=False):
        """Kelly-sized buy of `side` if edge after fees >= min_edge. Posts a passive limit order
        (maker) unless the trade is time-critical (urgent / expiring within LIMIT['taker_hours']).
        In a selective book (rules['min_pop'] set) the candidate is only scored and queued here;
        flush_pending() opens the best ones after all strategies have run."""
        if self.halt_directional or not self.active(strat) or self.conflict(m, side):
            return None
        if self.holds(strat, ticker=m["t"]) or self.cooling(m["t"]) or has_resting(self.pf, strat, m["t"]):
            return None   # don't re-buy the same displayed liquidity / just-exited markets
        q = self.score(strat, m, side, p_model, urgent)
        if q is None:
            return None
        self.gate["seen"] += 1
        if self.min_pop is None:
            pos = self._execute(strat, m, side, p_model, min_edge, note, urgent, q["pop"], 1.0)
            if pos:
                self._took(q["pop"])
            return pos
        reason = quality.gate(q, self.min_pop)
        if reason:
            self._reject(reason)
            return None
        if any(c["m"]["t"] == m["t"] and c["strat"] == strat for c in self.pending):
            return None
        self.gate["passed"] += 1
        c = {"strat": strat, "m": m, "side": side, "p_model": p_model, "min_edge": min_edge,
             "note": note, "urgent": urgent, "q": q, "score": quality.score(q)}
        self.pending.append(c)
        return c

    def _took(self, pop):
        self.gate["taken"] += 1
        if pop is not None:
            self._pops_taken.append(pop)
            self.gate["avg_pop_taken"] = round(sum(self._pops_taken) / len(self._pops_taken), 4)

    def flush_pending(self):
        """Open queued candidates best-first (EV x POP), at most SELECT['max_new_per_cycle']."""
        cands = sorted(self.pending, key=lambda c: -c["score"])
        self.pending = []
        cap = config.SELECT["max_new_per_cycle"]
        opened = 0
        for c in cands:
            if opened >= cap:
                self._reject("rank_cap")
                continue
            if self.conflict(c["m"], c["side"]) or self.holds(c["strat"], ticker=c["m"]["t"]):
                self._reject("conflict")
                continue
            pop = c["q"]["pop"]
            res = self._execute(c["strat"], c["m"], c["side"], c["p_model"], c["min_edge"],
                                (c["note"] + f" POP={pop:.2f}").strip(), c["urgent"], pop,
                                quality.kelly_mult(pop, self.min_pop))
            if res:
                opened += 1
                self._took(pop)
            else:
                self._reject("no_room_or_fill")
        return opened

    def gate_summary(self):
        g = dict(self.gate)
        g["rejected"] = dict(sorted(g["rejected"].items(), key=lambda kv: -kv[1]))
        return g

    def _execute(self, strat, m, side, p_model, min_edge, note="", urgent=False, pop=None, kmult=1.0):
        hours = ((m.get("exp") or m.get("close") or 0) - (self.now or 0)) / 3600
        if config.ORDER_MODE == "limit" and not urgent and hours > config.LIMIT["taker_hours"]:
            return self.post_entry(strat, m, side, p_model, min_edge, note, pop=pop, kmult=kmult)
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
        qty = int(qty * self.bucket_weight(m) * kmult)
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
        pos = self.pf.buy(strat, m, side, fill["qty"], fill["cost"], fill["fee"], p_model, note=note, p_mkt=p_mkt, pop=pop)
        if pos:
            st = order_stats(self.pf)
            st["taker_fills"] += 1
            st["taker_fees"] = round(st["taker_fees"] + fill["fee"], 4)
            self._book_exposure(strat, m, fill["cost"] + fill["fee"])
            self.claimed[m["t"]] = side
            self.log.append(f"{strat}: BUY {fill['qty']} {side.upper()} {m['t']} @ {fill['avg']:.2f} "
                            f"(p={p_model:.3f}, edge={real_edge:.3f}"
                            f"{'' if pop is None else f', POP={pop:.2f}'}) {note}")
        return pos

    def post_entry(self, strat, m, side, p_model, min_edge, note="", pop=None, kmult=1.0):
        q = entry_price(m, side)
        if q is None:
            return None
        fm, ft = m.get("fm", 1.0), m.get("ft", "quadratic")
        mfpc = fee_per_contract(q, fm) * (config.MAKER_RATE / config.TAKER_RATE if "maker" in (ft or "") else 0.0)
        gross = p_model - q
        edge = gross - mfpc
        if edge < min_edge or (gross > 0 and mfpc / gross > config.RISK["max_fee_to_edge"]):
            return None
        qty = kelly_contracts(p_model, q, mfpc, self.sleeve(strat), config.RISK["kelly_fraction"], self.dd_mult)
        qty = int(qty * self.bucket_weight(m) * kmult)
        qty = cap_contracts(qty, q + mfpc, self.room(strat, m))
        if qty < config.RISK["min_contracts"]:
            return None
        o = post_order(self, strat, m, side, "buy", q, qty, p_model=p_model, note=note, pop=pop)
        self._book_exposure(strat, m, qty * q)
        self.claimed[m["t"]] = side
        self.log.append(f"{strat}: POST LIMIT BUY {qty} {side.upper()} {m['t']} @ {q:.2f} "
                        f"(ask {m['ya'] if side == 'yes' else m['na']:.2f}, p={p_model:.3f}, edge={edge:.3f}) {note}")
        return o

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
                    arb_pop = config.SELECT["arb_pop"]
                    self.gate["seen"] += 1
                    self.gate["passed"] += 1
                    self._took(arb_pop)
                    for (m, side, b), fl in zip(books, fills):
                        self.pf.buy(strat, m, side, q, fl["cost"], fl["fee"], p_model=None, group=group, note=note,
                                    pop=arb_pop)
                        st = order_stats(self.pf)
                        st["taker_fills"] += 1
                        st["taker_fees"] = round(st["taker_fees"] + fl["fee"], 4)
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
        st = order_stats(self.pf)
        st["taker_fills"] += 1
        st["taker_fees"] = round(st["taker_fees"] + fill["fee"], 4)
        if self.now is not None:
            cd = self.pf.p.setdefault("cooldown", {})
            cd[pos["ticker"]] = int(self.now)
        self.log.append(f"{pos['strategy']}: SELL {fill['qty']} {pos['side'].upper()} {pos['ticker']} "
                        f"@ {fill['avg']:.2f} pnl={pnl:+.2f} ({reason})")
        return pnl
