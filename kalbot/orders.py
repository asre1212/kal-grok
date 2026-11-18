"""Passive limit orders (maker side) and their conservative fill model.

Posting: buys rest at the best bid + 1c (inside the spread) when the spread is >= 2c, otherwise
they join the best bid; they never cross. Sells (take-profit exits) rest 1c inside the ask.

Fill model, evaluated on the NEXT snapshot (6-7h later, so fills are coarse):
  * A resting BUY at q on side s fills only if the market traded through it:
      - the side's ask is now <= q (sellers came down to/through our bid), or
      - there was new volume AND the side's last trade is <= q - 1c.
  * A resting SELL at q fills only if the side's bid is now >= q, or new volume traded >= q + 1c.
  * Queue haircut / partial fills: fill qty = min(remaining, frac x new_volume), where
    frac = LIMIT['fill_frac'] (10%), or LIMIT['deep_fill_frac'] (35%) if price moved >= 2c through.
  * Unfilled remainder is cancelled after `max_age` snapshots (default 2; market making 1).
Fill price is our limit price; the maker fee applies (0 on most series; 0.0175 x C x P x (1-P)
on series with fee_type 'quadratic_with_maker_fees').
For each maker fill we also record what an all-taker baseline would have paid: the taker fee at
the ask (bid for sells) seen when the order was posted, plus the price improvement.
"""
import itertools

from . import config
from .fees import maker_fee, taker_fee

_ids = itertools.count(1)


def stats(pf):
    o = pf.p.setdefault("orders", {})
    for k in ("posted", "posted_qty", "filled_qty", "cancelled_qty", "maker_fills", "taker_fills",
              "maker_fees", "taker_fees", "fees_saved", "price_improvement"):
        o.setdefault(k, 0)
    return o


def side_px(m, side):
    if side == "yes":
        return m["yb"], m["ya"], m["lp"]
    lp = (1 - m["lp"]) if m.get("lp", 0) > 0 else 0
    return m["nb"], m["na"], lp


def entry_price(m, side):
    bid, ask, _ = side_px(m, side)
    if not (0 < ask < 1):
        return None
    q = round(bid + 0.01, 2) if ask - bid >= 0.02 else round(bid, 2)
    if q <= 0:
        q = 0.01
    return q if q < ask else None


def post(eng, strat, m, side, action, price, qty, p_model=None, kind="entry", key=None,
         max_age=None, ff=None, note="", pop=None):
    bid, ask, _ = side_px(m, side)
    o = {"id": f"{int(eng.now or 0)}-{next(_ids)}", "strategy": strat, "ticker": m["t"], "side": side,
         "action": action, "price": round(price, 2), "qty": int(qty), "filled": 0, "vol": m.get("vol", 0),
         "ts": int(eng.now or 0), "age": 0, "kind": kind, "max_age": max_age or config.LIMIT["max_age"],
         "p_model": p_model, "ref": ask if action == "buy" else bid, "key": key, "note": note,
         "event": m.get("e"), "series": m.get("s"), "cat": m.get("cat")}
    if ff is not None:
        o["ff"] = ff
    if pop is not None:
        o["pop"] = round(pop, 4)
    mid = (m["yb"] + m["ya"]) / 2
    o["p_mkt"] = round(mid if side == "yes" else 1 - mid, 4)
    eng.pf.p["resting"].append(o)
    s = stats(eng.pf)
    s["posted"] += 1
    s["posted_qty"] += int(qty)
    return o


def _fill_qty(o, m, through_px):
    """Return qty that fills this snapshot under the conservative model."""
    remaining = o["qty"] - o.get("filled", 0)
    if remaining <= 0:
        return 0
    dv = max(0.0, m.get("vol", 0) - o["vol"])
    bid, ask, last = side_px(m, o["side"])
    q = o["price"]
    if o.get("action", "buy") == "buy":
        crossed = (0 < ask <= q)
        traded = dv > 0 and 0 < last <= q - 0.01
        deep = (0 < ask <= q - 0.02) or (dv > 0 and 0 < last <= q - 0.02)
    else:
        crossed = bid >= q > 0
        traded = dv > 0 and last >= q + 0.01
        deep = bid >= q + 0.02 or (dv > 0 and last >= q + 0.02)
    if not (crossed or traded) or dv <= 0:
        return 0
    frac = config.LIMIT["deep_fill_frac"] if deep else o.get("ff", config.LIMIT["fill_frac"])
    return int(min(remaining, frac * dv))


def process_resting(eng):
    """Check every resting order of this portfolio against the current snapshot."""
    pf = eng.pf
    s = stats(pf)
    keep = []
    for o in pf.p.get("resting", []):
        o.setdefault("action", "buy")
        o.setdefault("max_age", 1)
        o.setdefault("filled", 0)
        o["age"] = o.get("age", 0) + 1
        m = eng.markets.get(o["ticker"])
        q = _fill_qty(o, m, None) if m else 0
        if q > 0:
            px = o["price"]
            mf = maker_fee(q, px, m.get("fm", 1.0), m.get("ft", "quadratic"))
            base_fee = taker_fee(q, o.get("ref") or px, m.get("fm", 1.0))
            if o["action"] == "buy":
                ok = pf.buy(o["strategy"], m, o["side"], q, round(q * px, 4), mf, p_model=o.get("p_model"),
                            maker=True, note=f"limit fill {o.get('note', '')}".strip(), p_mkt=o.get("p_mkt"),
                            pop=o.get("pop"))
                improve = ((o.get("ref") or px) - px) * q
            else:
                pos = pf.positions.get(o.get("key"))
                q = min(q, pos["qty"]) if pos else 0
                ok = bool(q) and pf.sell(o["key"], q, round(q * px, 4), mf, f"limit exit {o.get('note', '')}".strip()) is not None
                improve = (px - (o.get("ref") or px)) * q
                if ok and o.get("kind") == "exit":
                    pf.p["n_tp"] = pf.p.get("n_tp", 0) + 1
                    pf.p.setdefault("cooldown", {})[o["ticker"]] = int(eng.now or 0)
            if ok:
                o["filled"] += q
                s["filled_qty"] += q
                s["maker_fills"] += 1
                s["maker_fees"] = round(s["maker_fees"] + mf, 4)
                s["fees_saved"] = round(s["fees_saved"] + base_fee - mf, 4)
                s["price_improvement"] = round(s["price_improvement"] + improve, 4)
                eng.log.append(f"{o['strategy']}: LIMIT FILL {q} {o['action'].upper()} {o['side'].upper()} "
                               f"{o['ticker']} @ {px:.2f} (maker fee {mf:.2f})")
        remaining = o["qty"] - o["filled"]
        exit_alive = o.get("action") != "sell" or o.get("key") in pf.positions
        if remaining > 0 and o["age"] < o["max_age"] and m and exit_alive:
            keep.append(o)
        else:
            s["cancelled_qty"] += max(0, remaining)
            if o.get("kind") == "exit" and remaining > 0 and o.get("key") in pf.positions:
                pf.positions[o["key"]]["exit_expired"] = pf.positions[o["key"]].get("exit_expired", 0) + 1
    pf.p["resting"] = keep


def has_resting(pf, strat=None, ticker=None, key=None):
    for o in pf.p.get("resting", []):
        if (strat is None or o["strategy"] == strat) and (ticker is None or o["ticker"] == ticker) \
                and (key is None or o.get("key") == key):
            return True
    return False
