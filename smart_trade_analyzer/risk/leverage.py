"""Leverage safety -- Section 11 of the approved specification.

Same provenance note as position_sizing.py: the legacy
`suggest_max_safe_leverage()` this preserves the reasoning of is not
available to port verbatim in this environment, so this is a fresh
implementation of the described behavior, not a literal port.

The specification's own text (Section 19's table) confirms the core
mechanism this module already used is the *intended* one, not a
simplification to be replaced: "preserve `suggest_max_safe_leverage()`'s
reasoning, but with the hard/advisory split... made explicit: the
liquidation-vs-SL safety margin BECOMES a hard ceiling... a lower
'comfort' leverage remains advisory only." So the liquidation-distance
calculation below is correct per the specification -- what the Phase 5
audit-fix pass found genuinely missing is an ABSOLUTE cap layered on top
of it (see MAX_LEVERAGE_HARD_CAP) and a floor beneath it
(MIN_LEVERAGE): the original, unaudited formula had no upper bound at
all, so an extremely tight stop-loss (a fraction of a percent away) could
have produced a mathematically-derived but practically absurd suggestion
(hundreds of times leverage) with nothing to stop it. That is now fixed.

`maintenance margin rate` is a real, exchange- and instrument-specific
number this codebase has no data source for (no FeatureSet/MarketData
field carries it, and adding one would be new scope, not a fix) --
LEVERAGE_SAFETY_MARGIN is a documented, provisional APPROXIMATION standing
in for the combined effect of maintenance margin plus slippage, not a
literal per-exchange lookup. Flagged explicitly rather than silently
presented as more precise than it is.

MAX_LEVERAGE_HARD_CAP=20.0 is used PROVISIONALLY, following this audit
pass's own instruction to use the safer existing behavior when the
specification and an unavailable legacy reference cannot be directly
reconciled: the audit describes the previously-validated ceiling as
"around 20x" but this session has no way to confirm that number against
the actual original source. Recorded as an explicitly unresolved decision
in HANDOFF.md, not silently finalized.
"""
from typing import NamedTuple

# How much farther than the stop-loss the liquidation price must sit,
# expressed as a fraction (0.2 = liquidation must be at least 20% farther
# away than the stop, room for slippage/fees on a fast move). Provisional,
# documented, not a Section H item -- same discipline as every other
# implementation-level constant in this codebase. Serves as an
# APPROXIMATION for maintenance-margin effects this codebase has no data
# source to compute precisely -- see module docstring.
LEVERAGE_SAFETY_MARGIN = 0.20

# The advisory "comfort" leverage is a fraction of the hard ceiling --
# deliberately conservative, informational only.
COMFORT_LEVERAGE_FRACTION = 0.5

# Absolute ceiling, regardless of how tight the stop-loss is -- audit
# finding #2 fix. PROVISIONAL, unconfirmed against the actual legacy
# value; see module docstring and HANDOFF.md.
MAX_LEVERAGE_HARD_CAP = 20.0

# A leverage suggestion below 1x is not meaningful in this system's model
# (1x = no leverage, i.e. spot) -- a very wide stop-loss could otherwise
# drive the liquidation-based formula below 1. Floor, not a Section H item.
MIN_LEVERAGE = 1.0


class LeverageSuggestion(NamedTuple):
    max_safe_leverage: float   # hard ceiling -- never suggest or allow more than this
    comfort_leverage: float    # advisory only, always <= max_safe_leverage


def suggest_max_safe_leverage(entry_reference: float, stop_loss: float) -> LeverageSuggestion:
    """Both returned values are derived purely from how far the stop is
    from entry, expressed as a fraction of entry price -- no account
    balance or position size needed for a leverage CEILING (those matter
    for how much to actually put on, which is position_sizing.py's job,
    not this one's). `max_safe_leverage` is always the smaller of the
    liquidation-distance-based ceiling and MAX_LEVERAGE_HARD_CAP, and
    always at least MIN_LEVERAGE -- both bounds hold unconditionally,
    regardless of how tight or wide the stop is.

    A zero (or ~zero) SL distance -- entry equal to its own stop -- is a
    degenerate input with no real protection at all, not merely a "very
    tight" one: it is floored at MIN_LEVERAGE, the most conservative
    result this function can return, rather than let the formula's
    division blow up toward the hard cap or crash outright (found during
    the Phase 5 audit-fix pass; risk_engine.py's own compute_stop_loss can
    never actually produce this case, since it always enforces a positive
    ATR-based minimum distance, but this function is public and must not
    crash on a directly-supplied degenerate input either).
    """
    sl_distance_pct = abs(entry_reference - stop_loss) / entry_reference
    if sl_distance_pct <= 0:
        return LeverageSuggestion(max_safe_leverage=MIN_LEVERAGE, comfort_leverage=MIN_LEVERAGE * COMFORT_LEVERAGE_FRACTION)
    # If liquidation must sit at least LEVERAGE_SAFETY_MARGIN farther away
    # than the stop, then leverage L must satisfy 1/L >= sl_distance_pct * (1 + margin).
    liquidation_based_ceiling = 1.0 / (sl_distance_pct * (1.0 + LEVERAGE_SAFETY_MARGIN))
    max_safe_leverage = max(MIN_LEVERAGE, min(liquidation_based_ceiling, MAX_LEVERAGE_HARD_CAP))
    comfort_leverage = max_safe_leverage * COMFORT_LEVERAGE_FRACTION
    return LeverageSuggestion(max_safe_leverage=max_safe_leverage, comfort_leverage=comfort_leverage)
