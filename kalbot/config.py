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
    max_days_to_close=730,      # universe (variants); main portfolio limited by MAIN_MAX_DAYS
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
    max_orderbooks_per_run=450, # shared by main + all shadow variants
)

MAIN_MAX_DAYS = 120

# ---------- Order placement ----------
# "limit": post passive maker orders where the edge isn't time-critical (see kalbot/orders.py);
# "taker": always cross the spread (the old behaviour).
ORDER_MODE = "limit"
LIMIT = dict(
    max_age=2,              # snapshots a resting order lives before being cancelled
    fill_frac=0.10,         # queue-position haircut: share of new volume we get
    deep_fill_frac=0.35,    # when price moved >= 2c through our limit
    taker_hours=24,         # markets expiring sooner than this are traded as taker (time-critical)
)

# ---------- Shadow variants: expiry bucket x exit rule (each its own $100k paper book) ----------
# (id, label, min_days, max_days) on expected expiration
EXPIRY_BUCKETS = [
    ("1d", "≤1 day (<48h)", 0, 2),
    ("1m", "2–31 days", 2, 32),
    ("6m", "32–183 days", 32, 184),
    ("6m+", ">6 months", 184, 731),
]
# (id, label, take_profit, stop_loss) as fractions of position cost
EXIT_RULES = [
    ("hold", "Hold to settle", None, None),
    ("tp10", "+10%", 0.10, None),
    ("tp25", "+25%", 0.25, None),
    ("tp50", "+50%", 0.50, None),
    ("tp100", "+100%", 1.00, None),
    ("tp25sl25", "+25% / −25% SL", 0.25, 0.25),
]
MAIN_TP_STEPS = [None, 0.10, 0.25, 0.50, 1.00]
DEFAULT_MAIN_RULES = {"tp": None, "sl": None, "min_pop": 0.65,
                      "bucket_weights": {"1d": 1.0, "1m": 1.0, "6m": 1.0, "6m+": 1.0}}
BUCKET_WEIGHT_BOUNDS = (0.5, 1.5)
EXIT_COOLDOWN_HOURS = 24


# ---------- Selectivity: only open high probability-of-profit (POP) trades (kalbot/quality.py) ----------
# The main book gates every new entry on these; the weekly review may move min_pop (main_rules)
# one step at a time inside MIN_POP_BOUNDS when the POP shadow books show robust evidence.
SELECT = dict(
    min_pop=0.65,             # estimated probability the trade ends in profit, after fees
    min_ev=0.02,              # min expected net profit per contract ($) after entry + exit fees
    min_edge_to_cost=0.04,    # min expected net profit / all-in cost per contract
    max_new_per_cycle=12,     # top-N candidates (ranked by EV x POP) opened per cycle
    kelly_boost_max=1.5,      # Kelly multiplier at kelly_boost_pop and above (1.0 at min_pop)
    kelly_boost_pop=0.90,
    model_trust=0.80,         # share of a strategy's claimed edge over the market that we believe
    time_shrink=0.30,         # extra edge haircut, ramping from 7 to 120 days to close
    spread_penalty=0.50,      # POP haircut per $ of spread beyond 3c
    thin_penalty=0.02,        # POP haircut for thin markets (24h vol < 50 and OI < 500)
    horizon_sigma=0.06,       # price-move sd over a drift trade's holding period
    prior_strength=20,        # Beta prior pseudo-trades for each strategy's realized hit rate
    hit_window=300,           # most recent closed trades used for hit rates
    hit_factor_bounds=(0.70, 1.10),
    arb_pop=0.99,             # locked-in baskets
)
MIN_POP_BOUNDS = (0.55, 0.85)
MIN_POP_STEP = 0.05
# POP comparison books: (id, label, min_pop) -- each its own $100k, main-book universe and rules
POP_BOOKS = [
    ("all", "All trades (no POP filter)", None),
    ("60", "POP ≥ 0.60", 0.60),
    ("70", "POP ≥ 0.70", 0.70),
    ("80", "POP ≥ 0.80", 0.80),
]


def bucket_of(m, now):
    t = m.get("exp") or m.get("close")
    if not t:
        return None
    days = (t - now) / 86400
    for bid, _, lo, hi in EXPIRY_BUCKETS:
        if lo <= days < hi:
            return bid
    return None


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
