"""Setup candidate contract -- see Section 6 of the approved specification.

A SetupCandidate always proposes a side. If a detector cannot identify a
clear LONG or SHORT case, the correct result is no candidate at all (the
future setup_engine.py returns None) -- not a candidate with an ambiguous
direction. That rule is enforced here at construction time.
"""
from dataclasses import dataclass
from typing import List

from .enums import Direction, SetupType
from ._serde import JSONSerializable


@dataclass(frozen=True)
class SetupCandidate(JSONSerializable):
    setup_type: SetupType
    direction: Direction
    prerequisites_met: bool
    confirmation_met: bool
    invalidation_price: float
    evidence_refs: List[str]     # which features/regime facts justified detection
    failed_conditions: List[str]  # populated even on success -- what almost disqualified this

    def __post_init__(self):
        if self.direction == Direction.NEUTRAL:
            raise ValueError(
                "SetupCandidate.direction cannot be NEUTRAL -- a setup candidate "
                "always proposes a side (LONG or SHORT). If there is no clear "
                "side, no candidate should be constructed at all."
            )
        if self.invalidation_price <= 0:
            raise ValueError(
                f"SetupCandidate.invalidation_price must be positive, "
                f"got {self.invalidation_price}"
            )
        if self.confirmation_met and not self.prerequisites_met:
            raise ValueError(
                "SetupCandidate cannot have confirmation_met=True while "
                "prerequisites_met=False -- confirmation implies prerequisites"
            )
