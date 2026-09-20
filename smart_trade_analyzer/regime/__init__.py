"""Market Regime Engine -- deterministic (closed candles, FeatureSet) ->
MarketRegime classification. See engine.py for the documented formula and
thresholds."""
from .engine import classify_regime

__all__ = ["classify_regime"]
