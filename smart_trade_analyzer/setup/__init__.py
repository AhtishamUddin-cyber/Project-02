"""Setup Candidate Engine -- (closed candles, FeatureSet, MarketRegime) ->
SetupCandidate | None. See engine.py's module docstring for exactly which
setup families are implemented this phase and why."""
from .engine import detect_setup

__all__ = ["detect_setup"]
