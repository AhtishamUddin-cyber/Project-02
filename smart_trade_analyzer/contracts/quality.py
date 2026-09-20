"""Data quality contract -- see Section 3 of the approved specification.

Governing rule (approved decision #9): missing/unavailable data must never
silently become directional or neutral evidence. This module doesn't enforce
that rule by itself (confluence/, a later phase, is where evidence actually
gets constructed) -- but it DOES enforce, at the model level, that a source
can only ever be listed as excluded because it is genuinely UNAVAILABLE,
never because of a DEGRADED or VALID reading that someone decided to drop.
"""
from dataclasses import dataclass
from typing import Dict, List

from .enums import DataQualityState
from ._serde import JSONSerializable


@dataclass(frozen=True)
class DataQuality(JSONSerializable):
    overall: DataQualityState
    candle_count: int
    candle_count_required: int
    per_source: Dict[str, DataQualityState]
    excluded_sources: List[str]
    reasons: List[str]

    def __post_init__(self):
        if self.candle_count < 0:
            raise ValueError(
                f"DataQuality.candle_count cannot be negative, got {self.candle_count}"
            )
        if self.candle_count_required < 0:
            raise ValueError(
                "DataQuality.candle_count_required cannot be negative, "
                f"got {self.candle_count_required}"
            )
        for source in self.excluded_sources:
            state = self.per_source.get(source)
            if state is not None and state != DataQualityState.UNAVAILABLE:
                raise ValueError(
                    f"'{source}' is listed in excluded_sources but its per_source "
                    f"state is {state}, not UNAVAILABLE -- a source may only be "
                    f"excluded from evidence because it is UNAVAILABLE (decision #9)"
                )
