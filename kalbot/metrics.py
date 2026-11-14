"""Performance statistics."""
import math
import numpy as np


def returns_from_pnl(pnl_series, capital_series):
    out = []
    for i in range(1, len(pnl_series)):
        cap = capital_series[i - 1] or 0
        if cap > 0:
            out.append((pnl_series[i] - pnl_series[i - 1]) / cap)
    return out


def sharpe(rets, periods_per_year):
    if len(rets) < 3:
        return None
    sd = float(np.std(rets, ddof=1))
    if sd < 1e-12:
        return None
    return float(np.mean(rets) / sd * math.sqrt(periods_per_year))


def sortino(rets, periods_per_year):
    if len(rets) < 3:
        return None
    downside = [min(0.0, r) for r in rets]
    dd = math.sqrt(sum(d * d for d in downside) / len(rets))
    if dd < 1e-12:
        return None
    return float(np.mean(rets) / dd * math.sqrt(periods_per_year))


def max_drawdown(equity):
    peak = -float("inf")
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd


def periods_per_year(timestamps):
    if len(timestamps) < 2:
        return 365 * 24 / 7
    span = (timestamps[-1] - timestamps[0]) / (len(timestamps) - 1)
    return 365 * 86400 / max(span, 3600)
