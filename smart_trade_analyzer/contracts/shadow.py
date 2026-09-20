"""ShadowOutcome -- real-world outcome tracking for a SignalRecord.

See Section 15 of the approved specification. MAE/MFE are stored as
non-negative magnitudes (how far price moved against / in favor before
close, as a percentage) -- the sign is implied by the field name, not by the
number itself.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ._serde import JSONSerializable
from .signal_record import VALID_STATUSES


@dataclass(frozen=True)
class ShadowOutcome(JSONSerializable):
    signal_id: str  # foreign key to SignalRecord.id
    opened_at: datetime
    closed_at: Optional[datetime]
    exit_price: Optional[float]
    status: str
    pnl_pct: Optional[float]
    mae_pct: Optional[float]                     # max adverse excursion, magnitude >= 0
    mfe_pct: Optional[float]                      # max favorable excursion, magnitude >= 0
    time_to_outcome_minutes: Optional[float]

    def __post_init__(self):
        if not self.signal_id:
            raise ValueError("ShadowOutcome.signal_id must be a non-empty string")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"ShadowOutcome.status {self.status!r} not in {VALID_STATUSES}")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("ShadowOutcome.closed_at cannot be before opened_at")
        if self.exit_price is not None and self.exit_price <= 0:
            raise ValueError(
                f"ShadowOutcome.exit_price must be positive if known, got {self.exit_price}"
            )
        if self.mae_pct is not None and self.mae_pct < 0:
            raise ValueError(
                f"ShadowOutcome.mae_pct is a magnitude and cannot be negative, got {self.mae_pct}"
            )
        if self.mfe_pct is not None and self.mfe_pct < 0:
            raise ValueError(
                f"ShadowOutcome.mfe_pct is a magnitude and cannot be negative, got {self.mfe_pct}"
            )
        if self.time_to_outcome_minutes is not None and self.time_to_outcome_minutes < 0:
            raise ValueError("ShadowOutcome.time_to_outcome_minutes cannot be negative")
