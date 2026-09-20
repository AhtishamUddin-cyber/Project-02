"""Stop-loss computation -- Section 11 of the approved specification.

Preserves the audit-validated approach largely as-is (Section 11: "prefer a
nearby structure (swing) level with an ATR buffer, floor at 1xATR minimum
distance from entry"). This module has exactly one public function and no
score/grade/confidence input of any kind, the same discipline as
risk/targets.py.
"""
from typing import Optional

from ..contracts import Direction

SL_MIN_ATR_MULTIPLE = 1.0  # Section 11's explicit floor -- not a Section H item, stated directly
SL_STRUCTURE_BUFFER_ATR_MULTIPLE = 0.2  # room past the structure level itself, same discipline as targets.py


def compute_stop_loss(
    direction: Direction,
    entry_reference: float,
    atr: float,
    swing_support: Optional[float],
    swing_resistance: Optional[float],
) -> float:
    """Structure-aware stop loss with an ATR floor. `entry_reference` is
    the price the stop distance is measured from (the RiskPlan builder's
    choice -- see risk_engine.py). Returns a single price level, always on
    the protective side of `entry_reference` (below it for LONG, above it
    for SHORT) and always at least `SL_MIN_ATR_MULTIPLE * atr` away --
    the 1xATR floor and correct-side placement both hold unconditionally,
    regardless of what structure data is or isn't available.
    """
    min_distance = SL_MIN_ATR_MULTIPLE * atr
    protective_level = swing_support if direction == Direction.LONG else swing_resistance

    distance = min_distance
    if protective_level is not None:
        # Positive only if the level genuinely sits on the protective side
        # of entry (below for LONG, above for SHORT) -- a level on the
        # wrong side (e.g. a stale swing_support sitting above current
        # price) is not usable as a stop reference at all.
        structure_distance = (
            (entry_reference - protective_level) if direction == Direction.LONG
            else (protective_level - entry_reference)
        )
        if structure_distance > 0:
            distance = max(structure_distance + SL_STRUCTURE_BUFFER_ATR_MULTIPLE * atr, min_distance)

    return entry_reference - distance if direction == Direction.LONG else entry_reference + distance
