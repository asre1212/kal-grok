"""Static configuration. Tunable strategy parameters live in state (state/portfolio.json)
and are adjusted by the weekly evaluator within the bounds defined here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
DOCS_DATA = ROOT / "docs" / "data"
REPORTS_DIR = DOCS_DATA / "reports"

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
NWS_BASE = "https://api.weather.gov"
USER_AGENT = "kal-grok-paper-bot/1.0 (paper trading research; github.com/kal-grok)"

STARTING_BANKROLL = 100_000.00
# Kalshi pays a variable APY on cash/positions for eligible accounts. Treated as an
# ASSUMPTION; set to 0.0 to disable. Applied to idle cash only (conservative).
CASH_APY = 0.035

# ---------- Fees (Kalshi fee schedule, quadratic) ----------
TAKER_RATE = 0.07
MAKER_RATE = 0.0175          # only for series with fee_type 'quadratic_with_maker_fees'
FEE_ROUND_UNIT = 0.01        # conservative: round up to the cent (schedule says centicent)

# ---------- Universe filter ----------
UNIVERSE = dict(
    min_volume_24h=20.0,
    min_open_interest=200.0,
    max_days_to_close=120,
    history_max_markets=800,    # markets with rolling price history kept
    history_len=28,             # snapshots kept per market (~7 days at 4/day)
)

# ---------- Risk ----------
RISK = dict(
    kelly_fraction=0.25,
    max_market_frac=0.025,      # of total equity per market (all strategies)
    max_event_frac=0.05,        # per event (correlated outcomes)
    max_arb_event_frac=0.08,    # per event for risk-free baskets
    max_series_frac=0.10,
    max_category_frac=0.30,
    min_cash_reserve_frac=0.05,
    dd_soft=0.15,               # drawdown -> halve sizes
    dd_hard=0.25,               # drawdown -> no new directional trades
    min_contracts=5,
    max_fee_to_edge=0.5,        # skip trades where fees eat > 50% of gross edge
    max_orderbooks_per_run=250,
)

INITIAL_ALLOCATION = {
    "event_arb": 0.14,
    "ladder_arb": 0.10,
    "longshot_fade": 0.15,
    "expiry_convergence": 0.15,
    "momentum": 0.07,
    "mean_reversion": 0.07,
    "market_making": 0.12,
    "weather_nws": 0.15,
}
RESERVE_WEIGHT = 0.05
ALLOC_FLOOR = 0.03
ALLOC_CAP = 0.30
PROBATION_WEIGHT = 0.01

# Default params and (lo, hi) bounds for the weekly tuner.
STRATEGY_PARAMS = {
    "event_arb": {"min_profit": (0.01, 0.005, 0.05)},
    "ladder_arb": {"min_profit": (0.01, 0.005, 0.05)},
    "longshot_fade": {
        "min_edge": (0.015, 0.008, 0.06), "beta": (1.12, 1.0, 1.6),
        "p_max": (0.20, 0.08, 0.30), "max_days": (60, 7, 120),
    },
    "expiry_convergence": {
        "min_edge": (0.012, 0.006, 0.05), "beta": (1.20, 1.0, 1.8),
        "p_min": (0.85, 0.75, 0.95), "max_hours": (48, 12, 96),
    },
    "momentum": {"kappa": (0.35, 0.05, 0.9), "min_edge": (0.03, 0.015, 0.08), "hold": (4, 2, 10)},
    "mean_reversion": {"kappa": (-0.35, -0.9, -0.05), "min_edge": (0.03, 0.015, 0.08), "hold": (4, 2, 10)},
    "market_making": {"min_spread": (0.06, 0.03, 0.15), "fill_frac": (0.10, 0.03, 0.25), "quote_qty": (100, 20, 400)},
    "weather_nws": {"sigma_scale": (1.0, 0.7, 1.6), "min_edge": (0.05, 0.02, 0.12), "model_weight": (0.35, 0.1, 0.8)},
}


def default_params():
    return {s: {k: v[0] for k, v in p.items()} for s, p in STRATEGY_PARAMS.items()}
