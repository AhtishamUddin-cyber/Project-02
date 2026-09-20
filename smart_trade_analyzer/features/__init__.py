"""Canonical Feature Engine.

Consumes Phase 2's canonical MarketData, produces Phase 1's canonical
FeatureSet. Exactly one implementation of every indicator (indicators.py,
volume.py) is used everywhere -- live analysis, and in a later phase,
backtesting. No Bitget-specific knowledge, no HTTP calls, no Streamlit
import anywhere in this package.
"""
from . import divergence
from . import indicators
from . import readiness
from . import structure
from . import volume
from .engine import closed_candles, compute_feature_set

__all__ = [
    "divergence",
    "indicators",
    "readiness",
    "structure",
    "volume",
    "closed_candles",
    "compute_feature_set",
]
