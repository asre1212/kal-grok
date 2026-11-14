"""Conservative fill simulation.

Taker buys fill at the ASK (walking displayed depth up to a limit price), taker sells
fill at the BID. Size is capped to displayed depth. Fees are charged per price level
(each level rounded up separately — conservative). Kalshi orderbooks only show BIDS:
a YES ask at price p is a NO bid at (1 - p) and vice-versa.
"""
from .fees import taker_fee


class Book:
    def __init__(self, yes_bids, no_bids):
        self.yes_bids = sorted(yes_bids, key=lambda t: -t[0])
        self.no_bids = sorted(no_bids, key=lambda t: -t[0])

    @classmethod
    def from_api(cls, ob):
        return cls(ob.get("yes_bids", []), ob.get("no_bids", []))

    @classmethod
    def from_top(cls, m):
        """Fallback when no orderbook was fetched: top of book from the market snapshot."""
        yb = [(m["yb"], m.get("ybs") or 0)] if m.get("yb", 0) > 0 else []
        # no bid size isn't in the snapshot; YES ask size == size of best NO bid
        nb = [(m["nb"], m.get("yas") or 0)] if m.get("nb", 0) > 0 else []
        return cls(yb, nb)

    def bids(self, side):
        return self.yes_bids if side == "yes" else self.no_bids

    def asks(self, side):
        opp = self.no_bids if side == "yes" else self.yes_bids
        return sorted(((round(1 - p, 4), q) for p, q in opp), key=lambda t: t[0])

    def best_ask(self, side):
        a = self.asks(side)
        return a[0] if a else (None, 0)

    def best_bid(self, side):
        b = self.bids(side)
        return b[0] if b else (None, 0)


def simulate_buy(book, side, max_qty, limit_price, fee_mult=1.0, depth_frac=1.0):
    """Returns dict(qty, cost, fee, avg) for a marketable limit buy."""
    left = int(max_qty)
    qty = cost = fee = 0.0
    for price, size in book.asks(side):
        if left <= 0 or price > limit_price + 1e-9 or price >= 1:
            break
        take = int(min(left, size * depth_frac))
        if take <= 0:
            continue
        qty += take
        cost += take * price
        fee += taker_fee(take, price, fee_mult)
        left -= take
    return {"qty": int(qty), "cost": round(cost, 4), "fee": round(fee, 4),
            "avg": round(cost / qty, 4) if qty else None}


def simulate_sell(book, side, qty, min_price=0.0, fee_mult=1.0, depth_frac=1.0):
    left = int(qty)
    sold = proceeds = fee = 0.0
    for price, size in book.bids(side):
        if left <= 0 or price < min_price - 1e-9:
            break
        take = int(min(left, size * depth_frac))
        if take <= 0:
            continue
        sold += take
        proceeds += take * price
        fee += taker_fee(take, price, fee_mult)
        left -= take
    return {"qty": int(sold), "proceeds": round(proceeds, 4), "fee": round(fee, 4),
            "avg": round(proceeds / sold, 4) if sold else None}
