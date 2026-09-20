"""Swing support/resistance -- pivot-based structural levels.

See Section 4 of the approved specification: "Swing support/resistance |
features/structure.py::swing_levels() | Pivot-based, existing algorithm
preserved as-is (audit found no issues)". The legacy `analyzer.py` the
specification refers to is not present in this environment (this rewrite
was continued in a fresh session, with only the approved contracts/spec as
input, not the original source tree) -- so this is a fresh, standard
pivot-based implementation written directly from the specification's own
description (pivot-based, closed-candle-only), not a byte-for-byte port of
code that could not actually be inspected here. Documented explicitly per
this project's standing rule against silently claiming more than what was
actually done.

Definitions
-----------
A pivot HIGH at index i is a CLOSED candle whose high is strictly greater
than every other high in the window [i-left, i+right]. A pivot LOW is the
symmetric strict minimum on `low`. Ties within the window disqualify a
candle as a pivot (no ambiguous "which one is the real extreme" case is
ever reported as a confirmed pivot).

A pivot at index i cannot be CONFIRMED until `right` further closed candles
exist after it -- this is a structural property of pivot detection itself
(a bar's status as a local extreme is only knowable once you've seen what
came after it), not an incidental implementation choice. Consequently the
most recent `right` closed candles can never be reported as a confirmed
pivot. This is NOT a lookahead violation: every candle used to confirm a
pivot is itself already closed as of `as_of`, and confirmation never
reaches into the still-forming candle or anything beyond it -- see
test_no_lookahead.py for the direct causality proof specific to this
module.

swing_resistance is the nearest CONFIRMED pivot high strictly above the
current close, within the trailing lookback window. swing_support is the
nearest CONFIRMED pivot low strictly below the current close. Either, or
both, may legitimately be None -- e.g. a strong monotonic uptrend can have
no qualifying pivot low below the current (higher) price within the
lookback window. This is an honest reading of current market structure,
not a data-availability gap, and it is why this field is treated
differently from the deterministic indicators in FeatureSet.completeness
(see features/readiness.py and features/engine.py for the reasoning).

PIVOT_LEFT / PIVOT_RIGHT / SWING_LOOKBACK_CANDLES are documented,
provisional constants -- same discipline as setup/engine.py's thresholds:
a reasoned starting point, not yet validated against real historical
outcomes.
"""
import math
from typing import List, NamedTuple, Optional, Sequence, Tuple

PIVOT_LEFT = 3
PIVOT_RIGHT = 3
SWING_LOOKBACK_CANDLES = 100  # how far back (in closed candles) to search for a qualifying pivot

# The minimum number of closed candles for even a single pivot to be
# structurally identifiable at all (i-left >= 0 and i+right <= n-1 must
# hold for at least one index). This is a count-based lower bound, not a
# guarantee -- see the module docstring's note on legitimate None results.
MIN_CANDLES_SWING = PIVOT_LEFT + PIVOT_RIGHT + 1


class Pivot(NamedTuple):
    index: int
    price: float


def _all_finite(values: Sequence[float]) -> bool:
    return all(math.isfinite(v) for v in values)


def _is_pivot_high(values: Sequence[float], i: int, left: int, right: int) -> bool:
    center = values[i]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if values[j] >= center:
            return False
    return True


def _is_pivot_low(values: Sequence[float], i: int, left: int, right: int) -> bool:
    center = values[i]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if values[j] <= center:
            return False
    return True


def find_pivot_highs(
    highs: Sequence[float], left: int = PIVOT_LEFT, right: int = PIVOT_RIGHT
) -> List[int]:
    """Indices of every CONFIRMED pivot high in `highs` (closed candles
    only, caller's responsibility -- see features/engine.py's
    closed_candles()). Returns indices in ascending (chronological) order.
    """
    n = len(highs)
    if left < 1 or right < 1:
        raise ValueError(f"left/right must be >= 1, got left={left}, right={right}")
    if n < left + right + 1 or not _all_finite(highs):
        return []
    return [i for i in range(left, n - right) if _is_pivot_high(highs, i, left, right)]


def find_pivot_lows(
    lows: Sequence[float], left: int = PIVOT_LEFT, right: int = PIVOT_RIGHT
) -> List[int]:
    """Indices of every CONFIRMED pivot low in `lows`. Symmetric to
    find_pivot_highs -- see that function's docstring."""
    n = len(lows)
    if left < 1 or right < 1:
        raise ValueError(f"left/right must be >= 1, got left={left}, right={right}")
    if n < left + right + 1 or not _all_finite(lows):
        return []
    return [i for i in range(left, n - right) if _is_pivot_low(lows, i, left, right)]


def swing_levels(
    highs: Sequence[float],
    lows: Sequence[float],
    current_close: float,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
    lookback: int = SWING_LOOKBACK_CANDLES,
) -> Tuple[Optional[float], Optional[float]]:
    """Returns (swing_support, swing_resistance) for `current_close` given
    closed-candle `highs`/`lows` of equal length. Either element may be
    None -- see module docstring. `highs`/`lows` must already be filtered
    to closed candles by the caller (this module performs no candle-status
    filtering of its own, matching every other feature module's
    convention).
    """
    n = len(highs)
    if len(lows) != n:
        raise ValueError(f"highs and lows must be the same length, got {n} and {len(lows)}")
    if n < left + right + 1 or not math.isfinite(current_close):
        return (None, None)

    window_start = max(0, n - lookback)

    high_idxs = [i for i in find_pivot_highs(highs, left, right) if i >= window_start]
    low_idxs = [i for i in find_pivot_lows(lows, left, right) if i >= window_start]

    resistance_candidates = [highs[i] for i in high_idxs if highs[i] > current_close]
    support_candidates = [lows[i] for i in low_idxs if lows[i] < current_close]

    swing_resistance = min(resistance_candidates) if resistance_candidates else None
    swing_support = max(support_candidates) if support_candidates else None
    return (swing_support, swing_resistance)
