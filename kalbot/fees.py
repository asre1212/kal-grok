"""Kalshi trading-fee math.

fee = round_up(M * rate * C * P * (1 - P))
  rate = 0.07 for takers (all series); 0.0175 for makers on series whose fee_type is
  'quadratic_with_maker_fees' (makers pay 0 elsewhere). M = series fee_multiplier.
Rounded UP to FEE_ROUND_UNIT (default $0.01, the conservative classic rule).
"""
import math
from . import config


def _round_up(x: float, unit: float = None) -> float:
    unit = unit or config.FEE_ROUND_UNIT
    # guard float noise: 0.07*100*0.5*0.5 = 1.7500000000000002
    n = math.ceil(round(x / unit, 6))
    return round(n * unit, 6)


def taker_fee(contracts: float, price: float, multiplier: float = 1.0) -> float:
    if contracts <= 0 or multiplier <= 0:
        return 0.0
    return _round_up(multiplier * config.TAKER_RATE * contracts * price * (1 - price))


def maker_fee(contracts: float, price: float, multiplier: float = 1.0,
              fee_type: str = "quadratic") -> float:
    if contracts <= 0 or multiplier <= 0 or "maker" not in (fee_type or ""):
        return 0.0
    return _round_up(multiplier * config.MAKER_RATE * contracts * price * (1 - price))


def fee_per_contract(price: float, multiplier: float = 1.0) -> float:
    """Marginal (un-rounded) taker fee per contract; used for edge screening."""
    return multiplier * config.TAKER_RATE * price * (1 - price)
