"""Market Regime Engine: (closed candles, FeatureSet) -> MarketRegime.

Per the Phase 3 brief: regime must never be classified from a single
indicator alone, and trend_strength/volatility_percentile must be used
according to their documented Phase 1 semantics (continuous scores, not a
fabricated confidence number). This module computes both as genuine,
independently-meaningful measurements -- not just a restatement of the
final categorical label -- and combines a documented mix of trend
structure, EMA relationship, momentum, and volatility into one
deterministic decision.

This mirrors the design already reasoned through in the approved Phase 0
specification, Section 5, made concrete here with real, documented
formulas and thresholds (all explicitly flagged as reasoned starting
points, consistent with the "provisional, documented" discipline applied
throughout this project to every other hand-picked threshold).

trend_strength -- in [-1.0, +1.0], a weighted combination of:
  - EMA stack score (40%): magnitude-aware, ATR-normalized comparison of
    each adjacent EMA pair (ema9-vs-ema21, ema21-vs-ema50), NOT a bare sign
    check -- see _ema_stack_score's docstring for why a pure sign
    comparison was found, during development, to be too sensitive to
    noise-level orderings on a genuinely flat series.
  - Distance score (35%): (close - ema50) / atr, in "how many ATRs is
    price away from its own trend anchor," clamped to [-1,+1] by dividing
    by 3.0 (documented choice: being 3+ ATRs from EMA50 is treated as
    "as extended as this sub-score can register").
  - Slope score (25%): EMA50's average per-candle change over the last 10
    candles, normalized by ATR and clamped to [-1,+1] by dividing by 0.1
    (documented choice: EMA50 moving an average of 0.1 ATR per candle over
    that window is treated as "as steep as this sub-score can register").
  Distance/slope default to 0.0 (neutral, not blocking) if ATR or a long
  enough EMA50 history isn't available -- see classify_regime's docstring.

volatility_percentile -- in [0.0, 1.0], the current ATR% (ATR as a percent
  of price) ranked against its own trailing history: (count of historical
  ATR% readings <= the current one) / (total historical readings). Needs at
  least MIN_VOLATILITY_SAMPLE readings to be considered meaningful (below
  that, defaults to 0.5 -- the neutral midpoint, not a guess in either
  direction -- so HIGH/LOW_VOLATILITY simply cannot fire on too little
  history, without forcing the whole regime to UNKNOWN over it).

Regime label selection, in priority order:
  1. UNKNOWN     -- ema50 isn't available at all (not enough data to
                     classify honestly).
  2. TRANSITION  -- the EMA9-vs-EMA21 relationship's sign flipped within
                     the last TRANSITION_LOOKBACK_CANDLES candles.
  3. HIGH_VOLATILITY / LOW_VOLATILITY -- volatility_percentile is extreme
                     (>= 0.85 or <= 0.15) AND trend is weak/ambiguous
                     (|trend_strength| < RANGE_BAND) -- volatility becomes
                     the dominant, decision-relevant fact precisely when
                     trend isn't offering a clear one.
  4. STRONG_UPTREND / UPTREND / RANGE / DOWNTREND / STRONG_DOWNTREND --
                     from trend_strength via the documented bands below.
"""
from typing import List

from ..contracts import CandleData, FeatureSet, MarketRegime, RegimeType
from ..features import indicators as ind

# ---------------------------------------------------------------------------
# Documented, provisional thresholds -- reasoned starting points, not yet
# validated against real historical outcome data (same discipline applied
# to every other hand-picked threshold in this project).
# ---------------------------------------------------------------------------

EMA_STACK_WEIGHT = 0.40
DISTANCE_WEIGHT = 0.35
SLOPE_WEIGHT = 0.25

EMA_STACK_PAIR_NORMALIZER_ATR = 0.3  # a 0.3xATR gap between adjacent EMAs maxes out that pair's score
DISTANCE_NORMALIZER_ATR = 3.0     # +/-3 ATR from EMA50 maxes out the distance sub-score
SLOPE_LOOKBACK_CANDLES = 10
SLOPE_NORMALIZER_ATR_PER_CANDLE = 0.1  # EMA50 moving 0.1 ATR/candle (avg, over the lookback) maxes out the slope sub-score

MIN_VOLATILITY_SAMPLE = 20        # minimum ATR% history readings before percentile is trusted
HIGH_VOLATILITY_PERCENTILE = 0.85
LOW_VOLATILITY_PERCENTILE = 0.15

TRANSITION_LOOKBACK_CANDLES = 3   # how recently the EMA9/EMA21 relationship must have flipped

STRONG_TREND_BAND = 0.6           # |trend_strength| >= this -> STRONG_UPTREND/STRONG_DOWNTREND
TREND_BAND = 0.2                  # this <= |trend_strength| < STRONG_TREND_BAND -> UPTREND/DOWNTREND
                                   # |trend_strength| < TREND_BAND -> RANGE (subject to the volatility check above)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ema_stack_score(ema9, ema21, ema50, atr) -> float:
    """Magnitude-aware, ATR-normalized comparison of each adjacent EMA
    pair -- NOT a bare sign comparison. A purely binary "which is bigger"
    check was found, during development, to be too sensitive: on a
    genuinely flat/noisy series, pure randomness can still produce a
    razor-thin ema9<ema21<ema50 ordering with an economically meaningless
    gap, which a sign-only comparison would score as a full -1.0 ("fully
    bearish") despite the underlying data having no real trend at all.
    Normalizing each pairwise gap by ATR (documented choice:
    EMA_STACK_PAIR_NORMALIZER_ATR = 0.3 -- a 0.3xATR gap between adjacent
    EMAs maxes out that pair's contribution) makes a noise-level ordering
    correctly score near zero, while a genuinely separated stack (as in a
    real trend) still scores strongly."""
    if atr is None or atr == 0:
        a = 1.0 if ema9 > ema21 else (-1.0 if ema9 < ema21 else 0.0)
        b = 1.0 if ema21 > ema50 else (-1.0 if ema21 < ema50 else 0.0)
        return (a + b) / 2.0
    a = _clamp((ema9 - ema21) / atr / EMA_STACK_PAIR_NORMALIZER_ATR)
    b = _clamp((ema21 - ema50) / atr / EMA_STACK_PAIR_NORMALIZER_ATR)
    return (a + b) / 2.0


def _distance_score(close: float, ema50, atr) -> float:
    if ema50 is None or atr is None or atr == 0:
        return 0.0
    return _clamp((close - ema50) / atr / DISTANCE_NORMALIZER_ATR)


def _slope_score(closes: List[float], atr) -> float:
    if atr is None or atr == 0 or len(closes) < 50 + SLOPE_LOOKBACK_CANDLES:
        return 0.0
    series = ind.ema_series(closes, 50)
    now = series[-1]
    then = series[-1 - SLOPE_LOOKBACK_CANDLES]
    if now is None or then is None:
        return 0.0
    avg_change_per_candle = (now - then) / SLOPE_LOOKBACK_CANDLES
    return _clamp((avg_change_per_candle / atr) / SLOPE_NORMALIZER_ATR_PER_CANDLE)


def _compute_trend_strength(closes: List[float], feature_set: FeatureSet) -> float:
    stack = _ema_stack_score(feature_set.ema9, feature_set.ema21, feature_set.ema50, feature_set.atr)
    distance = _distance_score(feature_set.close, feature_set.ema50, feature_set.atr)
    slope = _slope_score(closes, feature_set.atr)
    return _clamp(EMA_STACK_WEIGHT * stack + DISTANCE_WEIGHT * distance + SLOPE_WEIGHT * slope)


def _compute_volatility_percentile(highs: List[float], lows: List[float], closes: List[float]) -> float:
    atr_vals = ind.atr_series(highs, lows, closes, 14)
    atr_pct_history = [
        (a / c * 100.0) for a, c in zip(atr_vals, closes) if a is not None and c > 0
    ]
    if len(atr_pct_history) < MIN_VOLATILITY_SAMPLE:
        return 0.5
    current = atr_pct_history[-1]
    rank = sum(1 for v in atr_pct_history if v <= current)
    return rank / len(atr_pct_history)


def _is_transitioning(closes: List[float]) -> bool:
    lookback = TRANSITION_LOOKBACK_CANDLES
    if len(closes) < 21 + lookback:
        return False
    ema9_series = ind.ema_series(closes, 9)
    ema21_series = ind.ema_series(closes, 21)
    now9, now21 = ema9_series[-1], ema21_series[-1]
    then9, then21 = ema9_series[-1 - lookback], ema21_series[-1 - lookback]
    if None in (now9, now21, then9, then21):
        return False
    now_sign = now9 > now21
    then_sign = then9 > then21
    return now_sign != then_sign


def classify_regime(closed: List[CandleData], feature_set: FeatureSet) -> MarketRegime:
    """Classify the current regime from `closed` candles and the already-
    computed `feature_set` (expected to have been built from the SAME
    closed-candle list -- see features/engine.py). Never reads the wall
    clock, never makes an HTTP call, and never decides LONG/SHORT/WAIT/
    NO_TRADE -- this is a description of market context, nothing more.
    """
    if feature_set.ema50 is None:
        return MarketRegime(
            regime=RegimeType.UNKNOWN, trend_strength=0.0, volatility_percentile=0.5,
            basis=["ema50 unavailable -- insufficient closed candles to classify honestly"],
        )

    closes = [c.close for c in closed]
    highs = [c.high for c in closed]
    lows = [c.low for c in closed]

    trend_strength = _compute_trend_strength(closes, feature_set)
    volatility_percentile = _compute_volatility_percentile(highs, lows, closes)

    basis: List[str] = [
        f"ema9={feature_set.ema9:.4f} ema21={feature_set.ema21:.4f} ema50={feature_set.ema50:.4f}"
        if feature_set.ema9 is not None and feature_set.ema21 is not None else f"ema50={feature_set.ema50:.4f}",
        f"trend_strength={trend_strength:.3f}",
        f"volatility_percentile={volatility_percentile:.3f}",
    ]

    if _is_transitioning(closes):
        basis.append(f"EMA9/EMA21 relationship flipped within the last {TRANSITION_LOOKBACK_CANDLES} candles")
        return MarketRegime(regime=RegimeType.TRANSITION, trend_strength=trend_strength,
                             volatility_percentile=volatility_percentile, basis=basis)

    if abs(trend_strength) < TREND_BAND:
        if volatility_percentile >= HIGH_VOLATILITY_PERCENTILE:
            basis.append(f"volatility_percentile >= {HIGH_VOLATILITY_PERCENTILE} with weak trend")
            return MarketRegime(regime=RegimeType.HIGH_VOLATILITY, trend_strength=trend_strength,
                                 volatility_percentile=volatility_percentile, basis=basis)
        if volatility_percentile <= LOW_VOLATILITY_PERCENTILE:
            basis.append(f"volatility_percentile <= {LOW_VOLATILITY_PERCENTILE} with weak trend")
            return MarketRegime(regime=RegimeType.LOW_VOLATILITY, trend_strength=trend_strength,
                                 volatility_percentile=volatility_percentile, basis=basis)
        basis.append(f"|trend_strength| < {TREND_BAND}")
        return MarketRegime(regime=RegimeType.RANGE, trend_strength=trend_strength,
                             volatility_percentile=volatility_percentile, basis=basis)

    if trend_strength >= STRONG_TREND_BAND:
        regime = RegimeType.STRONG_UPTREND
    elif trend_strength >= TREND_BAND:
        regime = RegimeType.UPTREND
    elif trend_strength <= -STRONG_TREND_BAND:
        regime = RegimeType.STRONG_DOWNTREND
    else:
        regime = RegimeType.DOWNTREND
    basis.append(f"trend_strength banding -> {regime.value}")
    return MarketRegime(regime=regime, trend_strength=trend_strength,
                         volatility_percentile=volatility_percentile, basis=basis)
