"""RSI divergence -- a price-vs-indicator structural relationship over
pivots, not a raw momentum level.

See Section 4 of the approved specification: "RSI divergence |
features/divergence.py::detect_divergence() | Pivot-based bullish/bearish
search, imports rsi() from momentum.py rather than recomputing it -- direct
fix for the 'NOT the same Wilder's calculation' issue". This module imports
`rsi_series` from features/indicators.py -- the codebase's actual canonical
RSI location (the specification's assumed `momentum.py` does not exist in
the approved Phase 3 structure, which consolidated all indicators into
indicators.py; see that module's and features/engine.py's docstrings for
the same "actual structure, not the spec's assumed one" note, per the
explicitly approved decision to keep the existing structure rather than
restructure it to match the specification's conceptual tree). RSI is never
recomputed here in any form.

Also reuses features/structure.py's pivot detection directly -- divergence
is fundamentally "price pivots vs. RSI at those same pivots," so it shares
its structural-extreme definition with swing_levels() exactly, rather than
defining its own notion of a price extreme.

Why divergence lives in STRUCTURE, not MOMENTUM (Section 7): it measures a
price-vs-indicator relationship anchored to confirmed pivot points, a
different kind of evidence from "is RSI high or low right now." Category
placement is confluence/'s concern, not this module's -- this module only
detects the pattern and reports which side it favors.

Definitions
-----------
BEARISH divergence: the two most recent CONFIRMED pivot highs in price show
a HIGHER high, while RSI at those same two candle indices shows a LOWER
high -- upside momentum fading even as price extends higher.

BULLISH divergence: the two most recent CONFIRMED pivot lows in price show
a LOWER low, while RSI at those same two candle indices shows a HIGHER low
-- downside momentum fading even as price extends lower.

Only the two most recent qualifying pivots of the relevant type are
compared -- a deliberate, documented scope choice (one clear, current
divergence read), not a search across every historical pivot pair. If a
pivot has no RSI value at its index (still inside RSI's warm-up window),
that pivot is skipped when selecting the "two most recent" -- an
unconfirmable comparison is not reported as a divergence.

None is returned when no qualifying pattern is present -- which is the
overwhelmingly common case. This is a genuine, informative "no divergence
right now" reading, not a data-insufficiency signal, and is treated
differently from every other FeatureSet field for that reason -- see
features/readiness.py and features/engine.py.

If both a bearish and a bullish read are structurally present at once
(rare, but possible when both a qualifying high-pair and low-pair exist in
the same lookback window), BEARISH is reported first -- an arbitrary but
deterministic, documented tie-break, since FeatureSet.divergence holds a
single value. This scope-limited tie-break is unrelated to, and far
narrower than, the setup-level REVERSAL/TREND_CONTINUATION conflict
resolved in setup/engine.py.
"""
from typing import List, Optional, Sequence

from . import structure as struct
from .indicators import rsi_series

MIN_CANDLES_DIVERGENCE = struct.MIN_CANDLES_SWING + 1
# A pivot pair needs two confirmed pivots (struct.MIN_CANDLES_SWING gets
# you the first one); the "+1" is a deliberately loose lower bound for
# readiness purposes only -- whether an actual SECOND confirmed pivot, and
# a non-None RSI value at both, exist is data-dependent, not just
# count-dependent (see module docstring). readiness() treats this as a
# count-based expectation, not a guarantee, exactly like every other field.


def detect_divergence(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    left: int = struct.PIVOT_LEFT,
    right: int = struct.PIVOT_RIGHT,
    lookback: int = struct.SWING_LOOKBACK_CANDLES,
) -> Optional[str]:
    """Returns "BEARISH", "BULLISH", or None. `highs`/`lows`/`closes` must
    already be filtered to closed candles, of equal length, and index-
    aligned (features/engine.py's convention for every feature module).
    """
    n = len(closes)
    if len(highs) != n or len(lows) != n:
        raise ValueError("highs, lows, and closes must be the same length")
    if n < left + right + 1:
        return None

    rsi_vals = rsi_series(closes)  # same length as closes, None during warm-up

    if _check_bearish(highs, rsi_vals, left, right, lookback):
        return "BEARISH"
    if _check_bullish(lows, rsi_vals, left, right, lookback):
        return "BULLISH"
    return None


def _two_most_recent_confirmed(
    idxs: List[int], rsi_vals: Sequence[Optional[float]], window_start: int
) -> Optional[List[int]]:
    usable = [i for i in idxs if i >= window_start and rsi_vals[i] is not None]
    if len(usable) < 2:
        return None
    return usable[-2:]  # [earlier, more_recent] -- find_pivot_* returns ascending order


def _check_bearish(
    highs: Sequence[float],
    rsi_vals: Sequence[Optional[float]],
    left: int,
    right: int,
    lookback: int,
) -> bool:
    n = len(highs)
    window_start = max(0, n - lookback)
    pair = _two_most_recent_confirmed(struct.find_pivot_highs(highs, left, right), rsi_vals, window_start)
    if pair is None:
        return False
    i_earlier, i_recent = pair
    price_higher_high = highs[i_recent] > highs[i_earlier]
    rsi_lower_high = rsi_vals[i_recent] < rsi_vals[i_earlier]
    return price_higher_high and rsi_lower_high


def _check_bullish(
    lows: Sequence[float],
    rsi_vals: Sequence[Optional[float]],
    left: int,
    right: int,
    lookback: int,
) -> bool:
    n = len(lows)
    window_start = max(0, n - lookback)
    pair = _two_most_recent_confirmed(struct.find_pivot_lows(lows, left, right), rsi_vals, window_start)
    if pair is None:
        return False
    i_earlier, i_recent = pair
    price_lower_low = lows[i_recent] < lows[i_earlier]
    rsi_higher_low = rsi_vals[i_recent] > rsi_vals[i_earlier]
    return price_lower_low and rsi_higher_low
