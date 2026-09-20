"""Take-profit target computation -- Section 11 of the approved specification.

Hard rule, repeated because it is the single most important constraint in
this module (Section 11, the direct fix for audit finding `H-2`, and the
frozen `RiskPlan` contract's own docstring already structurally enforces
it): **target distance never depends on `setup_quality_score`, grade, or
any confidence/probability value.** This module's public function does not
accept a score as an argument at all -- there is no parameter it could even
read one from by accident.

TP1_ATR_MULTIPLE / TP2_ATR_MULTIPLE are the specification's proposed fixed
starting multiples (Section 11: "e.g. TP1=1.5xATR, TP2=2.5xATR") --
PROVISIONAL, not a Section H item itself but stated as an example/starting
point pending the measured-typical-move migration the spec describes once
enough `ShadowOutcome.mfe_pct` history exists (a future phase; not this one).

Realistic-target validation (Section 11): a raw ATR-multiple target that
sits beyond a known, nearby opposing structure level (`swing_resistance`
for LONG, `swing_support` for SHORT -- reusing the canonical Phase 4
Feature Engine fields, not a new computation) is capped just short of that
level rather than presented as-is. If even a capped target would not sit
strictly beyond entry (i.e. there is no realistic room at all), the raw
ATR-based value is returned unchanged but `target_clearance_ok=False` is
set with a specific, deterministic warning -- this module reports honestly
rather than manufacturing a falsely-safe number; the Quality Gate's G8 is
where that honesty actually blocks a trade.
"""
from typing import List, NamedTuple, Optional

from ..contracts import Direction

TP1_ATR_MULTIPLE = 1.5  # PROVISIONAL -- Section 11's proposed starting multiple
TP2_ATR_MULTIPLE = 2.5  # PROVISIONAL -- Section 11's proposed starting multiple

# How much room to leave short of an opposing structure level when capping
# a target at it -- a target placed exactly AT a known resistance/support
# level is optimistic (that is precisely where price is expected to react),
# so the capped target sits a small buffer short of it. Provisional,
# documented, not a Section H item -- an implementation-level choice in the
# same spirit as setup/engine.py's own internal thresholds.
TARGET_CLEARANCE_BUFFER_ATR_MULTIPLE = 0.2


class Targets(NamedTuple):
    take_profit_1: float
    take_profit_2: float
    target_clearance_ok: bool
    warnings: List[str]


def _strictly_between(a: float, level: float, b: float) -> bool:
    return min(a, b) < level < max(a, b)


def compute_targets(
    direction: Direction,
    entry_reference: float,
    atr: float,
    swing_support: Optional[float],
    swing_resistance: Optional[float],
) -> Targets:
    """`entry_reference` is the price targets are measured from (the
    RiskPlan builder's job to decide what that is -- see risk_engine.py;
    this function only does the ATR-multiple + structure-clearance math).
    `direction` must be LONG or SHORT (never NEUTRAL -- enforced by every
    upstream contract already, not re-validated here).
    """
    sign = 1.0 if direction == Direction.LONG else -1.0
    raw_tp1 = entry_reference + sign * TP1_ATR_MULTIPLE * atr
    raw_tp2 = entry_reference + sign * TP2_ATR_MULTIPLE * atr
    opposing_level = swing_resistance if direction == Direction.LONG else swing_support

    tp1, tp2 = raw_tp1, raw_tp2
    warnings: List[str] = []
    target_clearance_ok = True

    if opposing_level is not None:
        buffer = TARGET_CLEARANCE_BUFFER_ATR_MULTIPLE * atr
        capped_level = opposing_level - sign * buffer  # a bit short of the level, on the entry side

        if _strictly_between(entry_reference, opposing_level, raw_tp1):
            has_room = (capped_level > entry_reference) if direction == Direction.LONG else (capped_level < entry_reference)
            if has_room:
                tp1 = capped_level
                warnings.append(
                    f"TP1 capped at {tp1:.4f} -- opposing structure at {opposing_level:.4f} sits before "
                    f"the raw {TP1_ATR_MULTIPLE}xATR target of {raw_tp1:.4f}"
                )
            else:
                target_clearance_ok = False
                warnings.append(
                    f"TP1 blocked by opposing structure at {opposing_level:.4f} -- no realistic room "
                    f"between entry ({entry_reference:.4f}) and that level"
                )

        # Checked against tp1 (possibly just capped above, not the raw
        # value) -- if the SAME opposing level already capped TP1 right up
        # to it, there is no additional room for a second, farther target,
        # and that must be flagged rather than silently duplicating TP1's
        # level as TP2 or leaving an unrealistic raw TP2 unflagged.
        if _strictly_between(entry_reference, opposing_level, raw_tp2):
            has_room = (capped_level > tp1) if direction == Direction.LONG else (capped_level < tp1)
            if has_room:
                tp2 = capped_level
                warnings.append(
                    f"TP2 capped at {tp2:.4f} -- opposing structure at {opposing_level:.4f} sits before "
                    f"the raw {TP2_ATR_MULTIPLE}xATR target of {raw_tp2:.4f}"
                )
            else:
                target_clearance_ok = False
                warnings.append(
                    f"TP2 blocked by opposing structure at {opposing_level:.4f} -- no realistic room "
                    f"beyond TP1 ({tp1:.4f}) before that level"
                )

    # TP1 must remain strictly before TP2 regardless of any capping above --
    # if capping inverted or collapsed the order, TP2 cannot be trusted as
    # a second, farther target and clearance must be flagged.
    tp1_before_tp2 = (tp1 < tp2) if direction == Direction.LONG else (tp1 > tp2)
    if not tp1_before_tp2:
        target_clearance_ok = False
        warnings.append(
            f"TP1 ({tp1:.4f}) and TP2 ({tp2:.4f}) are no longer correctly ordered after "
            f"structure-clearance capping -- targets not realistic"
        )

    return Targets(take_profit_1=tp1, take_profit_2=tp2, target_clearance_ok=target_clearance_ok, warnings=warnings)
