"""Confluence Evidence layer -- Section 7 of the approved specification.

This is the ONLY layer where thresholds/rules turn a raw FeatureSet value
into a directional EvidenceItem. confluence_engine.py never applies a
threshold of its own; it only combines what this module already decided.
That three-tier discipline (raw feature -> evidence -> score) is what
makes each individual rule here independently unit-testable (see
test_confluence_evidence.py), and is the specification's own stated reason
for the split.

Category mapping (Section 7's table, reused as-is):
  TREND      EMA9-vs-EMA21, close-vs-EMA50, MACD histogram
  MOMENTUM   RSI14, StochRSI %K
  STRUCTURE  RSI divergence, swing support/resistance proximity
  VOLATILITY Bollinger Band position (regime-dependent lean -- see below)
  VOLUME     volume_ratio combined with the last closed candle's direction
  HTF        no data source exists yet (approved decision D) -- always []
  FLOW       no data source exists yet (approved decision D) -- always []
  SENTIMENT  no data source exists yet (approved decision D) -- always []

Every function below takes plain FeatureSet/MarketRegime/candle inputs and
returns List[EvidenceItem] for exactly one category -- never a score,
never a category it doesn't own. HTF/FLOW/SENTIMENT's functions exist (not
omitted) specifically so "real data sources can be added later without
redesigning the engine" (approved decision D) -- their current body is a
one-line, clearly documented stub, not a placeholder pretending to be
real evidence.

NEUTRAL vs. no EvidenceItem at all (see contracts/confluence.py's own
docstring on this exact distinction): for CONTINUOUS signals that are
always computable once the underlying FeatureSet field is present (RSI,
StochRSI, EMA gap, close-vs-EMA50, MACD histogram), this module always
emits an item -- Direction.NEUTRAL falls out of the math naturally at the
exact midpoint (rsi14==50, etc.), rather than being special-cased. That IS
real information ("checked, no lean"), not silence. For DISCRETE,
conditional patterns (divergence, swing-level proximity, a BB-edge
touch, elevated volume), an item is emitted ONLY when the pattern is
actually present -- the pattern's ABSENCE is not a measurement sitting at
a neutral point, it is simply nothing to report, so no item is emitted at
all (silence, not a fabricated NEUTRAL reading). This split is a
deliberate, documented interpretation of the contract's own NEUTRAL-vs-
absent distinction, not a spec-mandated rule -- flagged here so it is easy
to revisit.

All thresholds/scaling constants below are documented, provisional
constants -- the specification's Section 7 defines WHICH raw signals feed
WHICH category, not the exact per-signal strength formula -- same
"reasoned starting point, not yet validated" discipline as every other
provisional constant in this codebase (setup/engine.py's thresholds,
features/structure.py's pivot window, etc.).
"""
from typing import List, Optional, Sequence

from ..contracts import CandleData, Direction, EvidenceCategory, EvidenceItem, FeatureSet, MarketRegime, RegimeType

# ---------------------------------------------------------------------------
# Documented, provisional constants.
# ---------------------------------------------------------------------------

TREND_EMA_GAP_SCALE = 0.02      # a 2% EMA9-vs-EMA21 gap reaches full strength
TREND_CLOSE_EMA50_SCALE = 0.03  # a 3% close-vs-EMA50 gap reaches full strength
TREND_MACD_HIST_SCALE = 0.01    # |macd_hist| / close reaching 1% reaches full strength

STRUCTURE_DIVERGENCE_STRENGTH = 0.8     # divergence is a discrete pattern, not a continuous magnitude
STRUCTURE_PROXIMITY_ATR_MULTIPLE = 0.75  # how close to a swing level counts as "at" it

BB_EDGE_PROXIMITY_PCT = 0.1     # within 10% of the band width from an edge counts as "at" it
BB_EDGE_STRENGTH = 0.4          # deliberately modest -- Section 7 calls this a "modest directional lean"

VOLUME_ELEVATED_RATIO_MIN = 1.2  # below this, volume is unremarkable -- no evidence either way


def _signed(direction: Direction, magnitude: float) -> float:
    if direction == Direction.LONG:
        return magnitude
    if direction == Direction.SHORT:
        return -magnitude
    return 0.0


def _direction_and_strength_from_signed_gap(signed_gap: float, scale: float) -> tuple:
    direction = Direction.LONG if signed_gap > 0 else (Direction.SHORT if signed_gap < 0 else Direction.NEUTRAL)
    strength = min(abs(signed_gap) / scale, 1.0) if scale > 0 else 0.0
    return direction, strength


# ---------------------------------------------------------------------------
# TREND
# ---------------------------------------------------------------------------

def trend_evidence(fs: FeatureSet) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []

    if fs.ema9 is not None and fs.ema21 is not None and fs.ema21 != 0:
        gap = (fs.ema9 - fs.ema21) / fs.ema21
        direction, strength = _direction_and_strength_from_signed_gap(gap, TREND_EMA_GAP_SCALE)
        items.append(EvidenceItem(
            category=EvidenceCategory.TREND, direction=direction, strength=strength,
            detail=f"EMA9 vs EMA21 gap {gap:+.2%}",
        ))

    if fs.ema50 is not None and fs.ema50 != 0:
        gap = (fs.close - fs.ema50) / fs.ema50
        direction, strength = _direction_and_strength_from_signed_gap(gap, TREND_CLOSE_EMA50_SCALE)
        items.append(EvidenceItem(
            category=EvidenceCategory.TREND, direction=direction, strength=strength,
            detail=f"close vs EMA50 gap {gap:+.2%}",
        ))

    if fs.macd_hist is not None and fs.close:
        normalized = fs.macd_hist / fs.close
        direction, strength = _direction_and_strength_from_signed_gap(normalized, TREND_MACD_HIST_SCALE)
        items.append(EvidenceItem(
            category=EvidenceCategory.TREND, direction=direction, strength=strength,
            detail=f"MACD histogram {fs.macd_hist:+.4f} ({normalized:+.2%} of price)",
        ))

    return items


# ---------------------------------------------------------------------------
# MOMENTUM
# ---------------------------------------------------------------------------

def momentum_evidence(fs: FeatureSet) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []

    if fs.rsi14 is not None:
        signed = (fs.rsi14 - 50.0) / 50.0
        direction, strength = _direction_and_strength_from_signed_gap(signed, 1.0)
        items.append(EvidenceItem(
            category=EvidenceCategory.MOMENTUM, direction=direction, strength=strength,
            detail=f"RSI14={fs.rsi14:.1f}",
        ))

    if fs.stoch_rsi_k is not None:
        signed = (fs.stoch_rsi_k - 50.0) / 50.0
        direction, strength = _direction_and_strength_from_signed_gap(signed, 1.0)
        items.append(EvidenceItem(
            category=EvidenceCategory.MOMENTUM, direction=direction, strength=strength,
            detail=f"StochRSI %K={fs.stoch_rsi_k:.1f}",
        ))

    return items


# ---------------------------------------------------------------------------
# STRUCTURE
# ---------------------------------------------------------------------------

def structure_evidence(fs: FeatureSet) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []

    if fs.divergence == "BULLISH":
        items.append(EvidenceItem(
            category=EvidenceCategory.STRUCTURE, direction=Direction.LONG,
            strength=STRUCTURE_DIVERGENCE_STRENGTH, detail="bullish RSI divergence",
        ))
    elif fs.divergence == "BEARISH":
        items.append(EvidenceItem(
            category=EvidenceCategory.STRUCTURE, direction=Direction.SHORT,
            strength=STRUCTURE_DIVERGENCE_STRENGTH, detail="bearish RSI divergence",
        ))

    if fs.atr is not None and fs.atr > 0:
        threshold = STRUCTURE_PROXIMITY_ATR_MULTIPLE * fs.atr
        if fs.swing_support is not None:
            distance = fs.close - fs.swing_support
            if 0 <= distance <= threshold:
                strength = max(0.0, 1.0 - distance / threshold)
                items.append(EvidenceItem(
                    category=EvidenceCategory.STRUCTURE, direction=Direction.LONG, strength=strength,
                    detail=f"close within {distance:.4f} of swing support {fs.swing_support:.4f}",
                ))
        if fs.swing_resistance is not None:
            distance = fs.swing_resistance - fs.close
            if 0 <= distance <= threshold:
                strength = max(0.0, 1.0 - distance / threshold)
                items.append(EvidenceItem(
                    category=EvidenceCategory.STRUCTURE, direction=Direction.SHORT, strength=strength,
                    detail=f"close within {distance:.4f} of swing resistance {fs.swing_resistance:.4f}",
                ))

    return items


# ---------------------------------------------------------------------------
# VOLATILITY
# ---------------------------------------------------------------------------

_RANGE_REGIMES = (RegimeType.RANGE,)
_UP_REGIMES = (RegimeType.UPTREND, RegimeType.STRONG_UPTREND)
_DOWN_REGIMES = (RegimeType.DOWNTREND, RegimeType.STRONG_DOWNTREND)


def volatility_evidence(fs: FeatureSet, regime: MarketRegime) -> List[EvidenceItem]:
    """Bollinger Band position gives a "modest directional lean" (Section
    7) -- and only a directional one at all when regime disambiguates
    which reading applies: near the UPPER band means "extended, likely to
    continue" (bullish) in a trending regime, but "overbought, likely to
    revert" (bearish) in a ranging one -- the same band-edge-proximity
    fact means opposite things depending on regime, exactly as
    setup/engine.py's _trend_continuation vs. _range_mean_reversion
    already treat it. In an UNKNOWN or ambiguous regime, or away from
    either edge, no evidence is emitted -- there is no principled reading
    to report.

    ATR trend (the specification's OTHER named VOLATILITY input) is
    deliberately NOT implemented as directional evidence here -- audited
    and re-confirmed, not an oversight. Section 7's own VOLATILITY row
    states its role is regime/risk sizing rather than casting a
    directional vote, and explicitly attributes VOLATILITY's one
    directional lean to BB position specifically, not to ATR trend.
    Section 8's weighting rationale repeats this: VOLATILITY is weighted
    lowest precisely because its primary role is regime/risk sizing, not
    direction. Consistent with that: the canonical ATR series
    (indicators.py::atr_series, reused rather than reimplemented) already
    drives MarketRegime.volatility_percentile (see regime/engine.py's
    _compute_volatility_percentile) -- ATR trend's specified role is
    already served there, and by the future risk/ phase's position
    sizing, not by a directional EvidenceItem here. Forcing a rising- or
    falling-ATR reading into Direction.LONG/SHORT would mean inventing a
    market-behavior claim ("expanding volatility favors direction X") the
    specification does not make and does not support -- explicitly the
    kind of fabrication this evidence layer exists to avoid. If a future
    phase's review of the full specification surfaces a different reading,
    this is the one function to revisit.
    """
    if fs.bb_upper is None or fs.bb_lower is None or fs.bb_upper <= fs.bb_lower:
        return []
    width = fs.bb_upper - fs.bb_lower
    position = (fs.close - fs.bb_lower) / width  # 0.0 at lower band, 1.0 at upper band
    near_upper = position >= (1.0 - BB_EDGE_PROXIMITY_PCT)
    near_lower = position <= BB_EDGE_PROXIMITY_PCT

    if near_upper and regime.regime in _RANGE_REGIMES:
        return [EvidenceItem(category=EvidenceCategory.VOLATILITY, direction=Direction.SHORT,
                              strength=BB_EDGE_STRENGTH, detail="close near upper BB band in a RANGE regime")]
    if near_lower and regime.regime in _RANGE_REGIMES:
        return [EvidenceItem(category=EvidenceCategory.VOLATILITY, direction=Direction.LONG,
                              strength=BB_EDGE_STRENGTH, detail="close near lower BB band in a RANGE regime")]
    if near_upper and regime.regime in _UP_REGIMES:
        return [EvidenceItem(category=EvidenceCategory.VOLATILITY, direction=Direction.LONG,
                              strength=BB_EDGE_STRENGTH, detail="close near upper BB band, extending with the uptrend")]
    if near_lower and regime.regime in _DOWN_REGIMES:
        return [EvidenceItem(category=EvidenceCategory.VOLATILITY, direction=Direction.SHORT,
                              strength=BB_EDGE_STRENGTH, detail="close near lower BB band, extending with the downtrend")]
    return []


# ---------------------------------------------------------------------------
# VOLUME
# ---------------------------------------------------------------------------

def volume_evidence(fs: FeatureSet, closed: Sequence[CandleData]) -> List[EvidenceItem]:
    """volume_ratio alone is not directional -- elevated volume can
    accompany a move in either direction. Combined with the last closed
    candle's own direction (the only price-direction fact this evidence
    layer needs, and the only reason `closed` is passed in here at all),
    elevated volume becomes a conviction signal on whichever direction the
    price actually moved. Below VOLUME_ELEVATED_RATIO_MIN, volume is
    unremarkable -- no evidence either way, not weak evidence.
    """
    if fs.volume_ratio is None or fs.volume_ratio < VOLUME_ELEVATED_RATIO_MIN or len(closed) < 2:
        return []
    last, prev = closed[-1], closed[-2]
    if last.close == prev.close:
        return []
    direction = Direction.LONG if last.close > prev.close else Direction.SHORT
    strength = min(fs.volume_ratio - 1.0, 1.0)
    return [EvidenceItem(
        category=EvidenceCategory.VOLUME, direction=direction, strength=strength,
        detail=f"volume_ratio={fs.volume_ratio:.2f} on a {'up' if direction == Direction.LONG else 'down'} candle",
    )]


# ---------------------------------------------------------------------------
# HTF / FLOW / SENTIMENT -- no data source exists yet (approved decision D).
# These exist so the engine has real structural support for all 8
# categories; each is a one-line stub, not a placeholder computation.
# ---------------------------------------------------------------------------

def htf_evidence() -> List[EvidenceItem]:
    return []  # no second-timeframe data source this phase


def flow_evidence() -> List[EvidenceItem]:
    return []  # no order-book/whale data source this phase


def sentiment_evidence() -> List[EvidenceItem]:
    return []  # no Fear & Greed/news/funding data source this phase
