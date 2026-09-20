"""SignalRecord -- the single canonical final output object.

See Section 13 of the approved specification for the full field-by-field
rationale. This is the only object persisted to the shadow log and shown to
the user; every other contract in this package ultimately feeds into one.

Carries the same decision/direction safety validation as SignalDecision (see
decision.py's module docstring) directly on this model too, since
SignalRecord does not embed a SignalDecision -- it is validated
independently so this guarantee holds no matter how a SignalRecord gets
constructed.

setup_score (0-100) and historical_probability (0.0-1.0 or None) are kept
deliberately distinct in both name and type -- this is the direct, structural
answer to approved decisions #4 and #5: nothing named or shaped like a score
can be mistaken for a probability, and historical_probability simply cannot
hold a value at all below whatever minimum-sample-size rule a later
calibration/ phase enforces (this contract only guarantees it's None or a
valid 0.0-1.0 probability -- the "is there enough data" decision itself is
out of scope for a pure data shape, per design rule 2).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from .enums import Decision, Direction, MarketType, QualityGrade, RegimeType, SetupType, Timeframe
from .quality import DataQuality
from .confluence import ConfluenceResult
from .entry import EntryPlan
from .risk import RiskPlan
from .features import FeatureSet
from .regime import MarketRegime
from ._serde import JSONSerializable

VALID_STATUSES = (
    "PENDING", "OPEN", "TP1_HIT", "TP2_HIT", "SL_HIT", "INVALIDATED", "EXPIRED",
)


@dataclass(frozen=True)
class SignalRecord(JSONSerializable):
    # --- identity ---
    id: str
    symbol: str
    timeframe: Timeframe
    market_type: MarketType
    timestamp: datetime

    # --- decision (verbatim top-level field list from the approved spec) ---
    decision: Decision
    direction: Optional[Direction]
    setup_type: Optional[SetupType]
    market_regime: RegimeType
    setup_score: float                          # 0.0-100.0 -- NOT a probability
    historical_probability: Optional[float]      # 0.0-1.0, or None -- never fabricated
    entry: Optional[float]
    entry_zone: Optional[Tuple[float, float]]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    risk_reward: Optional[float]
    confirmation_price: Optional[float]
    invalidation_price: Optional[float]
    quality_grade: QualityGrade
    reasons: List[str]
    warnings: List[str]
    data_quality: DataQuality

    # --- full detail, needed for shadow validation and debugging ---
    confluence: ConfluenceResult
    entry_plan: Optional[EntryPlan]
    risk_plan: Optional[RiskPlan]
    feature_snapshot: FeatureSet
    regime_snapshot: MarketRegime
    status: str

    legacy: bool = False       # True only for migrated pre-rewrite records
    schema_version: int = 1

    def __post_init__(self):
        if not isinstance(self.decision, Decision):
            raise ValueError(
                f"SignalRecord.decision must be a Decision enum member, got {self.decision!r}"
            )

        if self.decision in (Decision.LONG, Decision.SHORT):
            expected = Direction.LONG if self.decision == Decision.LONG else Direction.SHORT
            if self.direction != expected:
                raise ValueError(
                    f"SignalRecord.decision={self.decision} requires "
                    f"direction={expected}, got {self.direction!r}"
                )
        else:  # WAIT or NO_TRADE
            if self.direction is not None:
                raise ValueError(
                    f"SignalRecord.decision={self.decision} must have "
                    f"direction=None (no lean is asserted), got {self.direction!r}"
                )

        if self.status not in VALID_STATUSES:
            raise ValueError(f"SignalRecord.status {self.status!r} not in {VALID_STATUSES}")

        if not self.id:
            raise ValueError("SignalRecord.id must be a non-empty string")

        if not (0.0 <= self.setup_score <= 100.0):
            raise ValueError(
                f"SignalRecord.setup_score must be within [0.0, 100.0], got {self.setup_score}"
            )

        if self.historical_probability is not None and not (0.0 <= self.historical_probability <= 1.0):
            raise ValueError(
                f"SignalRecord.historical_probability must be within [0.0, 1.0] "
                f"or None, got {self.historical_probability}"
            )

        if self.entry_zone is not None:
            low, high = self.entry_zone
            if low > high:
                raise ValueError(
                    f"SignalRecord.entry_zone low ({low}) cannot exceed high ({high})"
                )

        for name, value in (
            ("entry", self.entry),
            ("stop_loss", self.stop_loss),
            ("take_profit_1", self.take_profit_1),
            ("take_profit_2", self.take_profit_2),
            ("confirmation_price", self.confirmation_price),
            ("invalidation_price", self.invalidation_price),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"SignalRecord.{name} must be positive if known, got {value}")

        if self.risk_reward is not None and self.risk_reward < 0:
            raise ValueError(
                f"SignalRecord.risk_reward cannot be negative, got {self.risk_reward}"
            )

        if self.schema_version < 1:
            raise ValueError(
                f"SignalRecord.schema_version must be >= 1, got {self.schema_version}"
            )
