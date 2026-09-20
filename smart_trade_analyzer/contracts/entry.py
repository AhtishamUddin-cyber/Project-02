"""Entry plan contract -- see Section 10 of the approved specification."""
from dataclasses import dataclass
from datetime import datetime

from .enums import Direction
from ._serde import JSONSerializable


@dataclass(frozen=True)
class EntryPlan(JSONSerializable):
    direction: Direction
    entry_zone_low: float
    entry_zone_high: float
    confirmation_price: float
    invalidation_price: float
    max_chase_distance: float
    structure_clearance_ok: bool
    generated_at: datetime
    staleness_ttl_minutes: float
    is_stale: bool

    def __post_init__(self):
        if self.direction == Direction.NEUTRAL:
            raise ValueError(
                "EntryPlan.direction cannot be NEUTRAL -- an entry plan always has a side"
            )
        if self.entry_zone_low > self.entry_zone_high:
            raise ValueError(
                f"EntryPlan.entry_zone_low ({self.entry_zone_low}) cannot exceed "
                f"entry_zone_high ({self.entry_zone_high})"
            )
        for name, value in (
            ("entry_zone_low", self.entry_zone_low),
            ("entry_zone_high", self.entry_zone_high),
            ("confirmation_price", self.confirmation_price),
            ("invalidation_price", self.invalidation_price),
        ):
            if value <= 0:
                raise ValueError(f"EntryPlan.{name} must be positive, got {value}")
        if self.max_chase_distance < 0:
            raise ValueError(
                f"EntryPlan.max_chase_distance cannot be negative, got {self.max_chase_distance}"
            )
        if self.staleness_ttl_minutes <= 0:
            raise ValueError(
                f"EntryPlan.staleness_ttl_minutes must be positive, got {self.staleness_ttl_minutes}"
            )
