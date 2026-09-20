"""Market regime contract -- see Section 5 of the approved specification.

No classification LOGIC lives here (that's regime/regime_engine.py, a later
phase) -- only the shape of a classification result. Both continuous scores
are always present alongside the single chosen label, so downstream code
never has to guess whether a RANGE label means "no trend" or "trend not yet
computed" -- trend_strength answers that directly.
"""
from dataclasses import dataclass
from typing import List

from .enums import RegimeType
from ._serde import JSONSerializable


@dataclass(frozen=True)
class MarketRegime(JSONSerializable):
    regime: RegimeType
    trend_strength: float          # -1.0 (strong down) .. +1.0 (strong up)
    volatility_percentile: float   # 0.0 (calmest in trailing window) .. 1.0 (most volatile)
    basis: List[str]               # explainability -- which facts drove this label

    def __post_init__(self):
        if not (-1.0 <= self.trend_strength <= 1.0):
            raise ValueError(
                f"MarketRegime.trend_strength must be within [-1.0, 1.0], "
                f"got {self.trend_strength}"
            )
        if not (0.0 <= self.volatility_percentile <= 1.0):
            raise ValueError(
                f"MarketRegime.volatility_percentile must be within [0.0, 1.0], "
                f"got {self.volatility_percentile}"
            )
