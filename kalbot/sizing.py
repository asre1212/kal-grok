"""Position sizing: fractional Kelly for binary contracts plus exposure caps."""
import math


def kelly_fraction_binary(p: float, cost: float) -> float:
    """Full-Kelly bankroll fraction for buying a $1 binary at all-in `cost` with win prob p.
    Payoff per $ staked: b = (1-cost)/cost.  f* = (b p - q)/b = (p - cost)/(1 - cost)."""
    if cost <= 0 or cost >= 1 or p <= cost:
        return 0.0
    return (p - cost) / (1 - cost)


def kelly_contracts(p: float, price: float, fee_pc: float, bankroll: float,
                    kelly_mult: float = 0.25, dd_mult: float = 1.0) -> int:
    cost = price + fee_pc
    f = kelly_fraction_binary(p, cost) * kelly_mult * dd_mult
    if f <= 0 or bankroll <= 0:
        return 0
    return int(math.floor(f * bankroll / cost + 1e-9))


def cap_contracts(qty: int, price: float, *room_dollars: float) -> int:
    """Clip qty so qty*price fits inside every remaining-room budget."""
    if price <= 0:
        return 0
    room = min(room_dollars) if room_dollars else float("inf")
    if room <= 0:
        return 0
    return max(0, min(qty, int(math.floor(room / price))))
