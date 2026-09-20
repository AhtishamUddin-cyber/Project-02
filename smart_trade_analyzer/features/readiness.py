"""Warm-up / minimum-data-requirement handling for the Feature Engine.

Every constant below was determined empirically (found the exact boundary
by testing N-1 vs. N candles), not just derived by hand-arithmetic -- see
test_indicators.py for the specific tests that pin each one down. This is
the single place these numbers live; nothing else in this codebase should
hard-code a minimum-candle-count for any indicator.

Governing rule (from the Phase 3 brief): "do not convert missing values
into zero... do not treat NaN as a valid trading feature." Every indicator
in indicators.py already returns None (never 0, never NaN, never a
fabricated placeholder) when it doesn't have enough data -- this module
exists so that fact is documented in ONE place with real numbers, rather
than only being implicit in each function's internal length check.

Phase 4 update -- two dicts, two different jobs, deliberately not merged:

  FEATURE_MIN_CANDLES  -- unchanged since Phase 3, byte-for-byte. Backs
                          compute_completeness() below, which continues to
                          mean exactly what it meant in Phase 3.

  STRUCTURAL_MIN_CANDLES -- swing_support/swing_resistance/divergence.
                          These fields are fundamentally different from
                          every field in FEATURE_MIN_CANDLES: given enough
                          candles, a None in FEATURE_MIN_CANDLES's fields
                          can only mean insufficient or non-finite data,
                          but a None swing level or divergence read is the
                          NORMAL, expected outcome even with abundant,
                          perfectly clean data (most candles have no active
                          divergence pattern; a strong monotonic trend can
                          legitimately have no qualifying level on one
                          side). Folding these into compute_completeness
                          would make a data-quality metric swing with
                          ordinary market structure instead of with data
                          availability -- see features/engine.py's module
                          docstring for the full reasoning. They get their
                          own dict, included in readiness() (a purely
                          count-based "has enough history accumulated to
                          attempt this" answer, which is genuinely useful
                          for all 19 fields), but excluded from
                          compute_completeness().
"""
from typing import Dict

from . import divergence as _div
from . import structure as _struct

# ---------------------------------------------------------------------------
# Per-field minimum closed-candle counts.
# ---------------------------------------------------------------------------

MIN_CANDLES_EMA9 = 9
MIN_CANDLES_EMA21 = 21
MIN_CANDLES_EMA50 = 50
MIN_CANDLES_EMA200 = 200
MIN_CANDLES_RSI14 = 15          # 14 deltas need 15 closes
MIN_CANDLES_MACD_LINE = 26      # needs EMA26 to have its first value
MIN_CANDLES_MACD_SIGNAL = 34    # 26 (line) + 9 (signal EMA) - 1
MIN_CANDLES_STOCH_RSI_K = 30    # verified empirically (test_indicators.py) -- not just the
                                 # naive 14+14+3-2=29 hand estimate, which is one short
MIN_CANDLES_STOCH_RSI_D = 32    # %K's minimum + 2 more candles for the %D SMA(3)
MIN_CANDLES_BOLLINGER = 20
MIN_CANDLES_ATR14 = 15          # 14 true ranges need 15 closes (first TR needs a previous close)
MIN_CANDLES_VOLUME_RATIO = 20   # current + 19 preceding

# The count needed for EVERY field this phase's engine populates to have a
# value -- matches the approved Phase 0 specification's Section 3 reasoning
# ("required count for the longest-lookback feature in use (200 for EMA200)").
MIN_CANDLES_FULL_FEATURE_SET = 200

# Fields whose None-state is a pure data-sufficiency signal. Unchanged
# since Phase 3 -- see the module docstring above for why
# swing_support/swing_resistance/divergence are deliberately kept out of
# this specific dict (they live in STRUCTURAL_MIN_CANDLES instead).
FEATURE_MIN_CANDLES: Dict[str, int] = {
    "ema9": MIN_CANDLES_EMA9,
    "ema21": MIN_CANDLES_EMA21,
    "ema50": MIN_CANDLES_EMA50,
    "ema200": MIN_CANDLES_EMA200,
    "rsi14": MIN_CANDLES_RSI14,
    "macd_line": MIN_CANDLES_MACD_LINE,
    "macd_signal": MIN_CANDLES_MACD_SIGNAL,
    "macd_hist": MIN_CANDLES_MACD_SIGNAL,
    "stoch_rsi_k": MIN_CANDLES_STOCH_RSI_K,
    "stoch_rsi_d": MIN_CANDLES_STOCH_RSI_D,
    "bb_upper": MIN_CANDLES_BOLLINGER,
    "bb_mid": MIN_CANDLES_BOLLINGER,
    "bb_lower": MIN_CANDLES_BOLLINGER,
    "atr": MIN_CANDLES_ATR14,
    "atr_pct": MIN_CANDLES_ATR14,
    "volume_ratio": MIN_CANDLES_VOLUME_RATIO,
}

# Structural fields (Phase 4): the count at which the computation can
# first possibly produce a value -- reusing each module's own constant
# directly (not an independently-redeclared copy) since structure.py and
# divergence.py already expose these as named constants, unlike
# indicators.py's internal, unexported per-function length checks.
STRUCTURAL_MIN_CANDLES: Dict[str, int] = {
    "swing_support": _struct.MIN_CANDLES_SWING,
    "swing_resistance": _struct.MIN_CANDLES_SWING,
    "divergence": _div.MIN_CANDLES_DIVERGENCE,
}


def readiness(candle_count: int) -> Dict[str, bool]:
    """For a given number of closed candles, which of all 19 FeatureSet
    fields are EXPECTED to be computable (True) vs. must remain None
    (False) purely on a count basis.

    This is a count-based expectation, not a guarantee. For
    FEATURE_MIN_CANDLES's fields, a field can still end up None even above
    its minimum if the underlying data contains a non-finite value
    anywhere in its lookback window (see indicators.py). For
    STRUCTURAL_MIN_CANDLES's fields the gap is wider still: crossing the
    minimum only means a value is STRUCTURALLY POSSIBLE, not that the
    actual price/RSI history will produce one -- see features/structure.py
    and features/divergence.py's module docstrings. Actual FeatureSet
    completeness (compute_completeness below) counts the real constructed
    values for the FEATURE_MIN_CANDLES fields only, not this expectation
    and not the structural fields -- see this module's top docstring.
    """
    combined = {**FEATURE_MIN_CANDLES, **STRUCTURAL_MIN_CANDLES}
    return {name: candle_count >= minimum for name, minimum in combined.items()}


def compute_completeness(feature_values: Dict[str, object]) -> float:
    """Fraction of the fields in FEATURE_MIN_CANDLES that are non-None in
    `feature_values` (expected to be a dict of the actual computed values,
    keyed the same way -- see features/engine.py). This is what
    FeatureSet.completeness holds: a measurement of what was ACTUALLY
    computed, not an assumption based on candle count alone.
    """
    keys = FEATURE_MIN_CANDLES.keys()
    populated = sum(1 for k in keys if feature_values.get(k) is not None)
    return populated / len(keys)
