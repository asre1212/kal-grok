"""Paper portfolio: cash, positions, P&L accounting, settlement."""
import time
from collections import defaultdict

from . import config


def pos_key(strategy, ticker, side):
    return f"{strategy}|{ticker}|{side}"


class Portfolio:
    def __init__(self, state):
        self.st = state
        p = state.setdefault("portfolio", {})
        p.setdefault("cash", config.STARTING_BANKROLL)
        p.setdefault("peak_equity", config.STARTING_BANKROLL)
        p.setdefault("realized", 0.0)
        p.setdefault("fees", 0.0)
        p.setdefault("interest", 0.0)
        p.setdefault("positions", {})
        p.setdefault("resting", [])
        p.setdefault("seq", 0)
        state.setdefault("trades", [])
        state.setdefault("closed", [])
        state.setdefault("strategy_stats", {})
        self.p = p

    # ---------- accessors ----------
    @property
    def cash(self):
        return self.p["cash"]

    @property
    def positions(self):
        return self.p["positions"]

    def stats(self, strat):
        return self.st["strategy_stats"].setdefault(
            strat, {"realized": 0.0, "fees": 0.0, "trades": 0, "wins": 0, "losses": 0})

    def _log(self, rec):
        self.p["seq"] += 1
        rec["id"] = self.p["seq"]
        rec["ts"] = rec.get("ts") or int(time.time())
        self.st["trades"].append(rec)
        self.st["trades"] = self.st["trades"][-1500:]

    # ---------- trading ----------
    def buy(self, strategy, m, side, qty, cost, fee, p_model=None, group=None, maker=False, note="", p_mkt=None):
        if qty <= 0:
            return None
        total = cost + fee
        if total > self.p["cash"] + 1e-9:
            return None
        self.p["cash"] = round(self.p["cash"] - total, 4)
        self.p["fees"] = round(self.p["fees"] + fee, 4)
        k = pos_key(strategy, m["t"], side)
        pos = self.positions.get(k)
        if pos is None:
            pos = self.positions[k] = {
                "strategy": strategy, "ticker": m["t"], "side": side, "qty": 0, "cost": 0.0,
                "event": m.get("e"), "series": m.get("s"), "cat": m.get("cat"),
                "title": m.get("ti"), "etitle": m.get("et"), "close": m.get("close"), "exp": m.get("exp"),
                "opened": int(time.time()), "p_model": p_model, "group": group, "mark": None,
                "fm": m.get("fm", 1.0), "ft": m.get("ft", "quadratic"), "runs_held": 0, "p_mkt": p_mkt,
            }
        # quantity-weighted model prob
        if p_model is not None and pos.get("p_model") is not None and pos["qty"] > 0:
            pos["p_model"] = (pos["p_model"] * pos["qty"] + p_model * qty) / (pos["qty"] + qty)
        elif p_model is not None:
            pos["p_model"] = p_model
        pos["qty"] += int(qty)
        pos["cost"] = round(pos["cost"] + total, 4)
        s = self.stats(strategy)
        s["fees"] = round(s["fees"] + fee, 4)
        s["trades"] += 1
        self._log({"strategy": strategy, "ticker": m["t"], "title": m.get("ti"), "action": "BUY",
                   "side": side, "qty": int(qty), "price": round(cost / qty, 4), "fee": round(fee, 4),
                   "p_model": None if p_model is None else round(p_model, 4), "maker": maker,
                   "group": group, "note": note})
        return pos

    def sell(self, key, qty, proceeds, fee, reason=""):
        pos = self.positions.get(key)
        if not pos or qty <= 0:
            return 0.0
        qty = min(qty, pos["qty"])
        basis = pos["cost"] * qty / pos["qty"]
        pnl = proceeds - fee - basis
        self.p["cash"] = round(self.p["cash"] + proceeds - fee, 4)
        self.p["fees"] = round(self.p["fees"] + fee, 4)
        self._realize(pos, pnl, fee, qty, outcome=None)
        pos["cost"] = round(pos["cost"] - basis, 4)
        pos["qty"] -= qty
        self._log({"strategy": pos["strategy"], "ticker": pos["ticker"], "title": pos.get("title"),
                   "action": "SELL", "side": pos["side"], "qty": int(qty),
                   "price": round(proceeds / qty, 4), "fee": round(fee, 4), "pnl": round(pnl, 2),
                   "note": reason})
        if pos["qty"] <= 0:
            del self.positions[key]
        return pnl

    def settle(self, key, yes_value, result):
        """Settle at yes_value (1.0/0.0, or scalar settlement value) per YES contract."""
        pos = self.positions.get(key)
        if not pos:
            return 0.0
        per = yes_value if pos["side"] == "yes" else 1 - yes_value
        payout = round(pos["qty"] * per, 4)
        pnl = payout - pos["cost"]
        self.p["cash"] = round(self.p["cash"] + payout, 4)
        outcome = 1 if per >= 0.5 else 0
        self._realize(pos, pnl, 0.0, pos["qty"], outcome=outcome)
        self._log({"strategy": pos["strategy"], "ticker": pos["ticker"], "title": pos.get("title"),
                   "action": "SETTLE", "side": pos["side"], "qty": pos["qty"], "price": per,
                   "fee": 0.0, "pnl": round(pnl, 2), "note": f"result={result}"})
        del self.positions[key]
        return pnl

    def _realize(self, pos, pnl, fee, qty, outcome):
        self.p["realized"] = round(self.p["realized"] + pnl, 4)
        s = self.stats(pos["strategy"])
        s["realized"] = round(s["realized"] + pnl, 4)
        if pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1
        self.st["closed"].append({
            "strategy": pos["strategy"], "ticker": pos["ticker"], "side": pos["side"],
            "qty": int(qty), "pnl": round(pnl, 2), "p_model": pos.get("p_model"),
            "outcome": outcome, "cat": pos.get("cat"), "ts": int(time.time()), "group": pos.get("group"),
            "p_mkt": pos.get("p_mkt"),
        })
        self.st["closed"] = self.st["closed"][-3000:]

    # ---------- valuation ----------
    def mark(self, key, m):
        """Mark at the mid of the held side (fair value); also keep the bid (liquidation value)."""
        pos = self.positions[key]
        yb, ya = m["yb"], m["ya"]
        if ya <= 0 or ya >= 1:
            ya = max(yb, 0.01) if ya >= 1 else ya
        mid_yes = (yb + ya) / 2 if ya > 0 else yb
        bid = yb if pos["side"] == "yes" else m["nb"]
        pos["mark"] = round(mid_yes if pos["side"] == "yes" else 1 - mid_yes, 4)
        pos["bid"] = round(bid, 4)
        pos["close"] = m.get("close") or pos.get("close")
        pos["exp"] = m.get("exp") or pos.get("exp")

    def position_value(self, pos):
        mk = pos["mark"] if pos.get("mark") is not None else pos["cost"] / max(pos["qty"], 1)
        return pos["qty"] * mk

    def equity(self):
        return round(self.p["cash"] + sum(self.position_value(p) for p in self.positions.values()), 4)

    def liquidation_value(self):
        return round(self.p["cash"] + sum(p["qty"] * p.get("bid", p.get("mark") or 0) for p in self.positions.values()), 4)

    def unrealized(self):
        return round(sum(self.position_value(p) - p["cost"] for p in self.positions.values()), 4)

    def exposures(self):
        ex = {k: defaultdict(float) for k in ("market", "event", "series", "cat", "strategy")}
        for p in self.positions.values():
            c = p["cost"]
            ex["market"][p["ticker"]] += c
            ex["event"][p["event"]] += c
            ex["series"][p["series"]] += c
            ex["cat"][p["cat"]] += c
            ex["strategy"][p["strategy"]] += c
        for r in self.p["resting"]:
            if r.get("kind") == "entry" and r.get("action", "buy") == "buy":
                c = (r["qty"] - r.get("filled", 0)) * r["price"]
                ex["strategy"][r["strategy"]] += c
                ex["market"][r["ticker"]] += c
                for k, f in (("event", "event"), ("series", "series"), ("cat", "cat")):
                    if r.get(f):
                        ex[k][r[f]] += c
        return ex

    def strategy_pnl(self, strat):
        s = self.stats(strat)
        unr = sum(self.position_value(p) - p["cost"] for p in self.positions.values() if p["strategy"] == strat)
        return s["realized"], unr

    def accrue_interest(self, seconds):
        if config.CASH_APY <= 0 or seconds <= 0:
            return 0.0
        amt = round(max(self.p["cash"], 0) * config.CASH_APY * seconds / (365 * 86400), 4)
        self.p["cash"] = round(self.p["cash"] + amt, 4)
        self.p["interest"] = round(self.p["interest"] + amt, 4)
        return amt
