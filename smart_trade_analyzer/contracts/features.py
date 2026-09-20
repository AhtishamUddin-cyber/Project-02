"""The canonical FeatureSet -- see Section 4 of the approved specification.

This module holds no indicator math (design rule 2): it only defines the
shape that features/feature_engine.py (a later phase) must populate, as the
single source of truth every other stage reads from. Approved decision #8
("every technical indicator must have exactly one canonical implementation")
is enforced by that future module's design, not by this contract -- but this
contract's shape is what makes that discipline possible, since there is
exactly one FeatureSet field per indicator, not one per consumer.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import Timeframe
from ._serde import JSONSerializable

_VALID_DIVERGENCE = ("BULLISH", "BEARISH")


@dataclass(frozen=True)
class FeatureSet(JSONSerializable):
    symbol: str
    timeframe: Timeframe
    as_of: datetime
    close: float

    ema9: Optional[float]
    ema21: Optional[float]
    ema50: Optional[float]
    ema200: Optional[float]

    rsi14: Optional[float]
    stoch_rsi_k: Optional[float]
    stoch_rsi_d: Optional[float]

    macd_line: Optional[float]
    macd_signal: Optional[float]
    macd_hist: Optional[float]

    bb_upper: Optional[float]
    bb_mid: Optional[float]
    bb_lower: Optional[float]

    atr: Optional[float]
    atr_pct: Optional[float]

    volume_ratio: Optional[float]

    swing_support: Optional[float]
    swing_resistance: Optional[float]

    divergence: Optional[str]  # "BULLISH" | "BEARISH" | None

    completeness: float  # 0.0-1.0, fraction of the above fields that are non-None

    def __post_init__(self):
        if self.close <= 0:
            raise ValueError(f"FeatureSet.close must be positive, got {self.close}")
        if not (0.0 <= self.completeness <= 1.0):
            raise ValueError(
                f"FeatureSet.completeness must be within [0.0, 1.0], got {self.completeness}"
            )
        if self.rsi14 is not None and not (0.0 <= self.rsi14 <= 100.0):
            raise ValueError(f"FeatureSet.rsi14 must be within [0, 100], got {self.rsi14}")
        for name, value in (("stoch_rsi_k", self.stoch_rsi_k), ("stoch_rsi_d", self.stoch_rsi_d)):
            if value is not None and not (0.0 <= value <= 100.0):
                raise ValueError(f"FeatureSet.{name} must be within [0, 100], got {value}")
        for name, value in (("atr", self.atr), ("atr_pct", self.atr_pct), ("volume_ratio", self.volume_ratio)):
            if value is not None and value < 0:
                raise ValueError(f"FeatureSet.{name} cannot be negative, got {value}")
        if self.divergence is not None and self.divergence not in _VALID_DIVERGENCE:
            raise ValueError(
                f"FeatureSet.divergence must be one of {_VALID_DIVERGENCE} or None, "
                f"got {self.divergence!r}"
            )
