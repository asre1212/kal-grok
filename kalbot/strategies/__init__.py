from .arbitrage import EventArb, LadderArb
from .longshot import LongshotFade, ExpiryConvergence
from .drift import Drift
from .market_making import MarketMaking
from .weather import WeatherNWS


def build_strategies():
    return [
        EventArb(), LadderArb(), LongshotFade(), ExpiryConvergence(),
        Drift("momentum"), Drift("mean_reversion"), MarketMaking(), WeatherNWS(),
    ]
