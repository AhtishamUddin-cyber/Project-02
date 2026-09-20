"""Entry Engine -- Section 10 of the approved specification.

Setup-aware by design: unlike the old system's universal `price +/-
0.35xATR` offset applied identically regardless of why a signal fired,
each setup family's own confirmation logic (setup/engine.py, Section 6)
directly defines its entry zone here. There is no generic fallback zone
formula shared by every family -- each of the 5 implemented families
(TREND_CONTINUATION, PULLBACK, RANGE_MEAN_REVERSION, BREAKOUT_RETEST,
REVERSAL) gets its own, reusing the SAME reference level its own detector
already confirmed against (ema21 for PULLBACK, the relevant Bollinger
boundary for RANGE_MEAN_REVERSION, the broken/rejected structural level --
already stored as `SetupCandidate.invalidation_price` -- for
BREAKOUT_RETEST and REVERSAL). TREND_CONTINUATION has no such family-
specific anchor (its own confirmation is "price is already favorably
positioned right now"), so its zone is a small band around current close
-- the one family-aware choice that is, honestly, closest to the old
system's "near market" idea, but deliberately still narrow and ATR-scaled
rather than a blanket universal offset.

No look-ahead: every input (`fs`, `closed`, `setup`) is already scoped to
`as_of` by the upstream Feature/Setup/Confluence engines -- this module
reads `fs.as_of` for `generated_at` and never touches the wall clock. See
test_no_lookahead.py's entry-specific tests.
"""
from dataclasses import replace
from datetime import datetime
from typing import List, Optional

from ..contracts import CandleData, Direction, EntryPlan, FeatureSet, SetupCandidate, SetupType, Timeframe
from ..risk.targets import TP1_ATR_MULTIPLE
from ..setup.engine import PULLBACK_ZONE_ATR_MULTIPLE

# ---------------------------------------------------------------------------
# Documented, provisional constants.
# ---------------------------------------------------------------------------

# Section H item #7: max chase distance, proposed 1xATR beyond the entry zone.
CHASE_DISTANCE_ATR_MULTIPLE = 1.0

# Section H item #7: staleness TTL, proposed "~2-3 candles' worth of time" --
# 2.5 is the documented midpoint of that range, not a separate invented value.
STALENESS_TTL_CANDLE_MULTIPLE = 2.5

TIMEFRAME_MINUTES = {
    Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.M30: 30,
    Timeframe.H1: 60, Timeframe.H2: 120, Timeframe.H4: 240, Timeframe.D1: 1440, Timeframe.W1: 10080,
}

# Half-widths for the family-specific entry zones below. PROVISIONAL,
# audited (Phase 5 audit-fix pass) and re-confirmed as implementation
# choices, not specification requirements: Section 10 names WHICH
# reference level each family's zone anchors to (the concept), and
# Section 6 defines each family's own confirmation/invalidation
# structure, but neither section states an exact zone width in ATR terms
# -- that number is this module's own reasoned choice, same discipline as
# every other implementation-level constant in this codebase
# (setup/engine.py's thresholds, confluence/evidence.py's proximity
# multiples, etc.). Each constant below has its own justification, not
# just a blanket "provisional" label. Invariant, checked directly in
# test_entry.py: none of these widths ever push a zone across its own
# setup's invalidation_price -- see _level_anchored_zone's docstring.
ENTRY_ZONE_MARKET_ATR_FRACTION = 0.25    # TREND_CONTINUATION: band around current close
# Why 0.25: TREND_CONTINUATION has no natural reference level to anchor
# to (its own thesis is "already favorably positioned now"), so this
# governs the ONLY family that gets a plain "near market" zone. Kept
# narrower than the 0.30 level-anchored fraction below on purpose: a
# market-centered zone with no structural anchor should stay tighter,
# not wider, than a zone anchored to a real, confirmed level -- there is
# no structural justification here for a wide band.

ENTRY_ZONE_RANGE_ATR_FRACTION = 0.25     # RANGE_MEAN_REVERSION: band around the BB boundary
# Why 0.25: matches the market-band fraction above -- a Bollinger
# boundary is itself already a somewhat "soft" statistical level (not a
# discrete, confirmed swing point the way BREAKOUT_RETEST/REVERSAL's
# level is), so it gets the same, tighter treatment rather than the wider
# level-anchored fraction.

ENTRY_ZONE_LEVEL_ATR_FRACTION = 0.30     # BREAKOUT_RETEST / REVERSAL: band on the holding side of the level
# Why 0.30, wider than the two above: this is the only case where the
# zone is anchored to a genuinely confirmed structural level (the exact
# broken/rejected swing price already stored as
# SetupCandidate.invalidation_price) rather than an indicator value or
# current price -- a somewhat wider allowance for the natural give in
# exactly where a retest/rejection actually completes is justified
# precisely because the anchor itself is more structurally meaningful,
# not because these two families are treated as more or less reliable.

# Preliminary structure-clearance estimate (see _structure_clearance_ok):
# reuses risk/targets.py's own TP1 multiple so this early check is
# consistent with what Risk Engine will actually target later, rather
# than inventing a second, unrelated distance.
_STRUCTURE_CLEARANCE_ATR_MULTIPLE = TP1_ATR_MULTIPLE


def _level_anchored_zone(direction: Direction, level: float, atr: float) -> tuple:
    """The zone sits on the HOLDING side of `level` -- above it for LONG
    (price expected to hold above a former-resistance-now-support, or
    reject upward off a support level), below it for SHORT (the mirror).
    `level` is always `SetupCandidate.invalidation_price` for the two
    families that use this helper (BREAKOUT_RETEST, REVERSAL) -- entry and
    invalidation deliberately sit on OPPOSITE sides of the same level, not
    the same point.

    Invariant (audited and verified, see test_entry.py): the zone edge
    nearer to `level` is `level` itself, never past it -- ENTRY_ZONE_LEVEL_
    ATR_FRACTION only ever widens the zone AWAY from level, so this zone
    can never include a price on the wrong side of the setup's own
    invalidation_price.
    """
    buf = ENTRY_ZONE_LEVEL_ATR_FRACTION * atr
    return (level, level + buf) if direction == Direction.LONG else (level - buf, level)


def _entry_zone_for_setup(setup: SetupCandidate, fs: FeatureSet) -> Optional[tuple]:
    """Returns (zone_low, zone_high) or None if the required reference
    level for this setup's family isn't available on `fs`."""
    d = setup.direction
    if setup.setup_type == SetupType.TREND_CONTINUATION:
        buf = ENTRY_ZONE_MARKET_ATR_FRACTION * fs.atr
        return (fs.close - buf, fs.close + buf)

    if setup.setup_type == SetupType.PULLBACK:
        if fs.ema21 is None:
            return None
        buf = PULLBACK_ZONE_ATR_MULTIPLE * fs.atr
        return (fs.ema21 - buf, fs.ema21 + buf)

    if setup.setup_type == SetupType.RANGE_MEAN_REVERSION:
        boundary = fs.bb_lower if d == Direction.LONG else fs.bb_upper
        if boundary is None:
            return None
        buf = ENTRY_ZONE_RANGE_ATR_FRACTION * fs.atr
        return (boundary - buf, boundary + buf)

    if setup.setup_type in (SetupType.BREAKOUT_RETEST, SetupType.REVERSAL):
        # Both families already store their governing structural level as
        # invalidation_price (the broken level for BREAKOUT_RETEST, the
        # rejected swing level for REVERSAL) -- see setup/engine.py.
        return _level_anchored_zone(d, setup.invalidation_price, fs.atr)

    return None  # no setup-specific logic exists for a family not listed above -- never guess


def _confirmation_price(direction: Direction, zone_low: float, zone_high: float) -> float:
    """The zone edge that, once closed through, confirms continuation in
    the trade's own direction -- the top of the zone for LONG, the bottom
    for SHORT. A single, uniform rule reusing the zone this module already
    computed, rather than a new per-family indicator threshold."""
    return zone_high if direction == Direction.LONG else zone_low


def _structure_clearance_ok(direction: Direction, zone_reference: float, fs: FeatureSet) -> bool:
    """A preliminary estimate of whether there is likely to be room for AT
    LEAST a typical TP1-sized move before known opposing structure --
    Risk Engine later performs the precise, authoritative version against
    its own actually-computed TP1/TP2 (Section 11: "TP1/TP2 checked
    against the SAME structure-clearance logic as the Entry Engine").
    Interpretive choice, documented: the specification names this concept
    for both Entry and Risk but Entry necessarily runs before Risk has
    computed real targets, so this is Entry's own estimate using the same
    ATR multiple Risk will use, not a fabricated unrelated distance.

    Conservative when it cannot be evaluated at all (ATR missing): reports
    NOT cleared rather than silently assuming clearance, since this is a
    safety check, not a convenience default.
    """
    if fs.atr is None or fs.atr <= 0:
        return False
    projected = zone_reference + (1 if direction == Direction.LONG else -1) * _STRUCTURE_CLEARANCE_ATR_MULTIPLE * fs.atr
    opposing = fs.swing_resistance if direction == Direction.LONG else fs.swing_support
    if opposing is None:
        return True
    blocked = (
        zone_reference < opposing < projected if direction == Direction.LONG
        else projected < opposing < zone_reference
    )
    return not blocked


def build_entry_plan(setup: SetupCandidate, fs: FeatureSet, closed: List[CandleData]) -> Optional[EntryPlan]:
    """Returns None if this setup's family has no entry-zone logic
    (should not happen for any of the 5 implemented families) or if the
    family-specific reference level this setup needs isn't available on
    `fs` -- never fabricates a zone from an unrelated fallback.
    """
    if fs.atr is None or fs.atr <= 0:
        return None

    zone = _entry_zone_for_setup(setup, fs)
    if zone is None:
        return None
    zone_low, zone_high = zone

    confirmation_price = _confirmation_price(setup.direction, zone_low, zone_high)
    zone_reference = zone_high if setup.direction == Direction.LONG else zone_low  # the "chase-from" edge
    structure_clearance_ok = _structure_clearance_ok(setup.direction, zone_reference, fs)
    max_chase_distance = CHASE_DISTANCE_ATR_MULTIPLE * fs.atr
    staleness_ttl_minutes = STALENESS_TTL_CANDLE_MULTIPLE * TIMEFRAME_MINUTES[fs.timeframe]

    return EntryPlan(
        direction=setup.direction,
        entry_zone_low=zone_low,
        entry_zone_high=zone_high,
        confirmation_price=confirmation_price,
        invalidation_price=setup.invalidation_price,
        max_chase_distance=max_chase_distance,
        structure_clearance_ok=structure_clearance_ok,
        generated_at=fs.as_of,
        staleness_ttl_minutes=staleness_ttl_minutes,
        is_stale=False,  # freshly generated, by construction, as of fs.as_of
    )


def refresh_staleness(plan: EntryPlan, current_as_of: datetime) -> EntryPlan:
    """Re-evaluates `is_stale` for an EXISTING plan against a later
    reference time, without recomputing anything else -- EntryPlan is
    frozen/immutable, so this returns a new instance rather than mutating
    the original. `current_as_of` must be supplied by the caller (never
    read from the wall clock here) -- see test_no_lookahead.py.
    """
    age_minutes = (current_as_of - plan.generated_at).total_seconds() / 60.0
    is_stale = age_minutes > plan.staleness_ttl_minutes
    return replace(plan, is_stale=is_stale)


def within_chase_distance(plan: EntryPlan, current_price: float) -> bool:
    """Whether `current_price` is still within the plan's zone plus its
    max_chase_distance allowance -- the Quality Gate's G7 uses this
    directly rather than re-deriving chase logic itself."""
    if plan.direction == Direction.LONG:
        return current_price <= plan.entry_zone_high + plan.max_chase_distance
    return current_price >= plan.entry_zone_low - plan.max_chase_distance
