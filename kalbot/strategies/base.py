class Strategy:
    name = "base"
    description = ""

    def manage(self, eng):
        """Exits / housekeeping for existing positions (runs before new entries)."""

    def run(self, eng):
        """Find and execute new trades."""
        raise NotImplementedError

    def tune(self, params, metrics, ctx):
        """Weekly: return (new_params, [reasons]). Default: generic edge-threshold tuning."""
        return generic_tune(self.name, params, metrics)

    def my_positions(self, eng):
        return {k: p for k, p in eng.pf.positions.items() if p["strategy"] == self.name}


def generic_tune(name, params, metrics, key="min_edge"):
    from ..config import STRATEGY_PARAMS
    new = dict(params)
    reasons = []
    if key not in params:
        return new, reasons
    lo, hi = STRATEGY_PARAMS[name][key][1:]
    n = metrics.get("closed_trades", 0)
    if n < 5:
        return new, [f"{key}: unchanged (only {n} closed trades)"]
    ret = metrics.get("week_return", 0.0)
    sharpe = metrics.get("sharpe") or 0.0
    if ret > 0 and sharpe > 0.5:
        new[key] = max(lo, round(params[key] * 0.9, 4))
        reasons.append(f"{key} {params[key]} -> {new[key]}: profitable (Sharpe {sharpe:.2f}), loosen to trade more")
    elif ret < 0:
        new[key] = min(hi, round(params[key] * 1.2, 4))
        reasons.append(f"{key} {params[key]} -> {new[key]}: losing week, demand more edge")
    return new, reasons
