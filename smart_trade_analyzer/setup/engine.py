"""Setup Candidate Engine: (closed candles, FeatureSet, MarketRegime) ->
SetupCandidate | None.

Phase 4 update: BREAKOUT_RETEST and REVERSAL are now implemented, using the
swing structure and divergence evidence Phase 4 added to the Feature Engine
(features/structure.py, features/divergence.py). All five setup families
from the approved specification's Section 6 are implemented as of this
phase.

Approved conflict rule (REVERSAL vs. TREND_CONTINUATION): REVERSAL's own
prerequisites require an established regime OPPOSING its proposed
direction (e.g. REVERSAL SHORT needs an established UPTREND) -- which is
structurally the SAME regime state TREND_CONTINUATION's opposite-direction
case reads as its own confirming evidence (TREND_CONTINUATION LONG also
needs an established UPTREND). The two can therefore both reach
confirmation_met=True at once, proposing opposite directions, from the
same detect_setup() call. When that happens, TREND_CONTINUATION wins --
the currently-classified regime is the directly observed fact; REVERSAL is
a thesis contesting it, not an equally-weighted alternative reading of the
same evidence. This is implemented as an explicit, named, directly-tested
step (_resolve_reversal_vs_trend_continuation below), not as an implicit
side effect of _DETECTORS' tuple order -- reordering that tuple in the
future must not silently change this specific rule's behavior. The fact
that REVERSAL also qualified is never hidden: it is provable by calling
_reversal() directly (see test_setup.py's conflict tests), even though the
SetupCandidate contract itself (frozen, Phase 1) has no field for "other
candidates that also qualified" and none was added for this. Deliberately
scoped to ONLY this one pair, per explicit instruction -- not generalized
into a broader cross-family priority system. (The same regime-overlap
exists between REVERSAL and PULLBACK, since PULLBACK shares
TREND_CONTINUATION's regime prerequisite -- but PULLBACK is positioned
before REVERSAL in _DETECTORS, so first-confirmed-wins-by-tuple-order
already resolves it the same way, without needing its own explicit rule.)

Each implemented detector below evaluates ONLY LONG and ONLY SHORT as
candidate outcomes -- a SetupCandidate's direction is never
Direction.NEUTRAL (Phase 1's frozen contract does not allow it; see
setup.py's own validation). If neither side's prerequisites are met, the
detector contributes nothing; `detect_setup` returns None (SCANNER-visible
NO_TRADE) rather than forcing a choice.

confirmation_met=True never coexists with prerequisites_met=False in any
detector here (Phase 1's frozen SetupCandidate enforces this at
construction time regardless, but each detector's own logic is also
structured so confirmation is checked only once prerequisites already hold,
not as two independently-arrived-at booleans that could disagree).

BREAKOUT_RETEST and REVERSAL both follow the specification's "fails to"
language literally: a break without above-average volume, or a divergence
read that isn't present, are NOT reported as a weaker/forming candidate --
they mean this family does not apply at all this cycle (None), the same
way "prerequisites not met" means None for the other three families. The
only "forming, not yet confirmed" state either of these two new families
can report is prerequisites genuinely met (a real break with real volume;
a real divergence at a real level) with confirmation specifically still
pending (no retest yet; no short-term rejection yet).
"""
from typing import List, Optional

from ..contracts import CandleData, Direction, FeatureSet, MarketRegime, RegimeType, SetupCandidate, SetupType
from ..features import structure as struct
from ..features import volume as vol

# ---------------------------------------------------------------------------
# Documented, provisional thresholds.
# ---------------------------------------------------------------------------

TREND_CONTINUATION_RSI_LONG_MIN = 45.0
TREND_CONTINUATION_RSI_LONG_MAX = 80.0
TREND_CONTINUATION_RSI_SHORT_MIN = 20.0
TREND_CONTINUATION_RSI_SHORT_MAX = 55.0

PULLBACK_ZONE_ATR_MULTIPLE = 1.0   # how close to EMA21 counts as "in the pullback zone"

RANGE_BAND_PROXIMITY_PCT = 0.15    # within 15% of the BB band width counts as "at the boundary"
RANGE_STOCH_EXTREME = 20.0         # StochRSI %K at/below this (LONG) or at/above 100-this (SHORT)

BREAKOUT_VOLUME_RATIO_MIN = 1.2            # "above-average volume" on the break candle
RETEST_PROXIMITY_ATR_MULTIPLE = 0.5        # how close price must come back to the broken level to count as a retest

REVERSAL_LEVEL_PROXIMITY_ATR_MULTIPLE = 0.75  # how close to the structural level counts as "at" it


def _trend_continuation(closed: List[CandleData], regime: MarketRegime, fs: FeatureSet) -> Optional[SetupCandidate]:
    if fs.ema50 is None or fs.atr is None:
        return None

    for direction, trend_regimes, price_ok, rsi_lo, rsi_hi, macd_ok in (
        (Direction.LONG, (RegimeType.UPTREND, RegimeType.STRONG_UPTREND),
         fs.close > fs.ema50, TREND_CONTINUATION_RSI_LONG_MIN, TREND_CONTINUATION_RSI_LONG_MAX,
         lambda h: h is not None and h > 0),
        (Direction.SHORT, (RegimeType.DOWNTREND, RegimeType.STRONG_DOWNTREND),
         fs.close < fs.ema50, TREND_CONTINUATION_RSI_SHORT_MIN, TREND_CONTINUATION_RSI_SHORT_MAX,
         lambda h: h is not None and h < 0),
    ):
        prerequisites_met = regime.regime in trend_regimes and price_ok
        if not prerequisites_met:
            continue

        evidence_refs = [
            f"regime={regime.regime.value}",
            f"close {'>' if direction == Direction.LONG else '<'} ema50 ({fs.close:.4f} vs {fs.ema50:.4f})",
        ]
        failed_conditions: List[str] = []

        rsi_ok = fs.rsi14 is not None and rsi_lo <= fs.rsi14 <= rsi_hi
        if not rsi_ok:
            failed_conditions.append(
                f"rsi14={fs.rsi14} not in healthy-continuation range [{rsi_lo}, {rsi_hi}]"
            )
        macd_confirms = macd_ok(fs.macd_hist)
        if not macd_confirms:
            failed_conditions.append(f"macd_hist={fs.macd_hist} does not confirm {direction.value.lower()} momentum")

        confirmation_met = rsi_ok and macd_confirms
        if confirmation_met:
            evidence_refs.append(f"rsi14={fs.rsi14:.2f} in [{rsi_lo}, {rsi_hi}]")
            evidence_refs.append(f"macd_hist={fs.macd_hist:.4f} confirms momentum")

        invalidation_price = fs.ema50
        return SetupCandidate(
            setup_type=SetupType.TREND_CONTINUATION, direction=direction,
            prerequisites_met=prerequisites_met, confirmation_met=confirmation_met,
            invalidation_price=invalidation_price, evidence_refs=evidence_refs,
            failed_conditions=failed_conditions,
        )
    return None


def _pullback(closed: List[CandleData], regime: MarketRegime, fs: FeatureSet) -> Optional[SetupCandidate]:
    if fs.ema21 is None or fs.ema50 is None or fs.atr is None or fs.atr == 0:
        return None

    for direction, trend_regimes, structure_ok in (
        (Direction.LONG, (RegimeType.UPTREND, RegimeType.STRONG_UPTREND), fs.close > fs.ema50),
        (Direction.SHORT, (RegimeType.DOWNTREND, RegimeType.STRONG_DOWNTREND), fs.close < fs.ema50),
    ):
        in_zone = abs(fs.close - fs.ema21) <= PULLBACK_ZONE_ATR_MULTIPLE * fs.atr
        prerequisites_met = regime.regime in trend_regimes and structure_ok and in_zone
        if not prerequisites_met:
            continue

        evidence_refs = [
            f"regime={regime.regime.value}",
            f"trend structure intact (close vs ema50: {fs.close:.4f} vs {fs.ema50:.4f})",
            f"price within {PULLBACK_ZONE_ATR_MULTIPLE}xATR of ema21 ({fs.close:.4f} vs {fs.ema21:.4f}, atr={fs.atr:.4f})",
        ]
        failed_conditions: List[str] = []

        stoch_turning = (
            fs.stoch_rsi_k is not None and fs.stoch_rsi_d is not None
            and ((direction == Direction.LONG and fs.stoch_rsi_k > fs.stoch_rsi_d)
                 or (direction == Direction.SHORT and fs.stoch_rsi_k < fs.stoch_rsi_d))
        )
        if not stoch_turning:
            failed_conditions.append(
                f"stoch_rsi %K/%D ({fs.stoch_rsi_k}/{fs.stoch_rsi_d}) has not yet turned back "
                f"in the {direction.value.lower()} direction"
            )
        else:
            evidence_refs.append(f"stoch_rsi %K={fs.stoch_rsi_k:.2f} %D={fs.stoch_rsi_d:.2f} turning {direction.value.lower()}")

        confirmation_met = stoch_turning
        invalidation_price = fs.ema50
        return SetupCandidate(
            setup_type=SetupType.PULLBACK, direction=direction,
            prerequisites_met=prerequisites_met, confirmation_met=confirmation_met,
            invalidation_price=invalidation_price, evidence_refs=evidence_refs,
            failed_conditions=failed_conditions,
        )
    return None


def _range_mean_reversion(closed: List[CandleData], regime: MarketRegime, fs: FeatureSet) -> Optional[SetupCandidate]:
    if fs.bb_upper is None or fs.bb_lower is None or fs.atr is None or regime.regime != RegimeType.RANGE:
        return None

    band_width = fs.bb_upper - fs.bb_lower
    if band_width <= 0:
        return None

    for direction, near_boundary, stoch_extreme in (
        (Direction.LONG,
         (fs.close - fs.bb_lower) <= RANGE_BAND_PROXIMITY_PCT * band_width,
         fs.stoch_rsi_k is not None and fs.stoch_rsi_k <= RANGE_STOCH_EXTREME),
        (Direction.SHORT,
         (fs.bb_upper - fs.close) <= RANGE_BAND_PROXIMITY_PCT * band_width,
         fs.stoch_rsi_k is not None and fs.stoch_rsi_k >= (100.0 - RANGE_STOCH_EXTREME)),
    ):
        prerequisites_met = near_boundary
        if not prerequisites_met:
            continue

        boundary = fs.bb_lower if direction == Direction.LONG else fs.bb_upper
        evidence_refs = [
            f"regime=RANGE",
            f"close near {'lower' if direction == Direction.LONG else 'upper'} Bollinger boundary "
            f"({fs.close:.4f} vs {boundary:.4f})",
        ]
        failed_conditions: List[str] = []
        if not stoch_extreme:
            failed_conditions.append(f"stoch_rsi %K={fs.stoch_rsi_k} not yet at a momentum extreme")
        else:
            evidence_refs.append(f"stoch_rsi %K={fs.stoch_rsi_k:.2f} confirms momentum exhaustion")

        confirmation_met = stoch_extreme
        invalidation_buffer = 0.5 * fs.atr
        invalidation_price = (
            fs.bb_lower - invalidation_buffer if direction == Direction.LONG
            else fs.bb_upper + invalidation_buffer
        )
        return SetupCandidate(
            setup_type=SetupType.RANGE_MEAN_REVERSION, direction=direction,
            prerequisites_met=prerequisites_met, confirmation_met=confirmation_met,
            invalidation_price=invalidation_price, evidence_refs=evidence_refs,
            failed_conditions=failed_conditions,
        )
    return None


def _breakout_retest_one_direction(
    direction: Direction, closed: List[CandleData], highs: List[float], lows: List[float],
    volumes: List[float], fs: FeatureSet,
) -> Optional[SetupCandidate]:
    n = len(closed)
    all_pivot_idxs = struct.find_pivot_highs(highs) if direction == Direction.LONG else struct.find_pivot_lows(lows)
    window_start = max(0, n - struct.SWING_LOOKBACK_CANDLES)
    pivot_idxs = [i for i in all_pivot_idxs if i >= window_start]
    if not pivot_idxs:
        return None

    # Search from the MOST RECENT pivot backward for the first one that has
    # ACTUALLY been broken. A newer, not-yet-broken pivot -- e.g. a fresh
    # swing high formed during the breakout run itself, after the level
    # that was actually broken -- must not shadow the older level that is
    # the one genuinely being retested. Only the nearest-in-time BROKEN
    # level is considered (a deliberate, documented scope choice, same
    # discipline as divergence.py's "only the two most recent pivots").
    pivot_idx = None
    break_idx = None
    for candidate_pivot_idx in reversed(pivot_idxs):
        level = highs[candidate_pivot_idx] if direction == Direction.LONG else lows[candidate_pivot_idx]
        for i in range(candidate_pivot_idx + 1, n):
            close_i = closed[i].close
            broke = (close_i > level) if direction == Direction.LONG else (close_i < level)
            if broke:
                pivot_idx, break_idx = candidate_pivot_idx, i
                break
        if break_idx is not None:
            break
    if break_idx is None:
        return None  # no pivot in the lookback window has been broken -- nothing to report

    level = highs[pivot_idx] if direction == Direction.LONG else lows[pivot_idx]
    volume_at_break = vol.volume_ratio(volumes[: break_idx + 1])
    if volume_at_break is None or volume_at_break < BREAKOUT_VOLUME_RATIO_MIN:
        # Spec's own "fails to" condition: a break without above-average
        # volume is a fakeout, NOT this family -- no candidate at all, not
        # a weaker/forming one.
        return None

    current_close = fs.close
    already_invalidated = (current_close <= level) if direction == Direction.LONG else (current_close >= level)
    if already_invalidated:
        return None  # price has already closed back into the prior range -- this thesis already failed

    retested = any(
        (lows[i] <= level + RETEST_PROXIMITY_ATR_MULTIPLE * fs.atr) if direction == Direction.LONG
        else (highs[i] >= level - RETEST_PROXIMITY_ATR_MULTIPLE * fs.atr)
        for i in range(break_idx + 1, n)
    )

    evidence_refs = [
        f"confirmed pivot {'high' if direction == Direction.LONG else 'low'} at index {pivot_idx} "
        f"(level={level:.4f}) broken at index {break_idx} (close={closed[break_idx].close:.4f})",
        f"volume_ratio at break={volume_at_break:.2f} >= {BREAKOUT_VOLUME_RATIO_MIN}",
    ]
    failed_conditions: List[str] = []
    if retested:
        evidence_refs.append(f"retest of level {level:.4f} held (current close={current_close:.4f})")
    else:
        failed_conditions.append(f"no retest of broken level {level:.4f} yet since the break")

    return SetupCandidate(
        setup_type=SetupType.BREAKOUT_RETEST, direction=direction,
        prerequisites_met=True, confirmation_met=retested,
        invalidation_price=level, evidence_refs=evidence_refs,
        failed_conditions=failed_conditions,
    )


def _breakout_retest(closed: List[CandleData], regime: MarketRegime, fs: FeatureSet) -> Optional[SetupCandidate]:
    if fs.atr is None or fs.atr == 0 or fs.close is None or len(closed) < struct.MIN_CANDLES_SWING + 1:
        return None

    highs = [c.high for c in closed]
    lows = [c.low for c in closed]
    volumes = [c.volume for c in closed]

    for direction in (Direction.LONG, Direction.SHORT):
        candidate = _breakout_retest_one_direction(direction, closed, highs, lows, volumes, fs)
        if candidate is not None:
            return candidate
    return None


def _reversal(closed: List[CandleData], regime: MarketRegime, fs: FeatureSet) -> Optional[SetupCandidate]:
    if fs.atr is None or fs.atr == 0 or len(closed) < 2:
        return None

    for direction, opposing_regimes, divergence_needed, level in (
        (Direction.SHORT, (RegimeType.UPTREND, RegimeType.STRONG_UPTREND), "BEARISH", fs.swing_resistance),
        (Direction.LONG, (RegimeType.DOWNTREND, RegimeType.STRONG_DOWNTREND), "BULLISH", fs.swing_support),
    ):
        if regime.regime not in opposing_regimes:
            continue
        if fs.divergence != divergence_needed:
            continue  # spec's own "fails to: no divergence present" -- not this family, not a forming one
        if level is None:
            continue  # spec's own "fails to: not at any meaningful structural level"
        at_level = abs(fs.close - level) <= REVERSAL_LEVEL_PROXIMITY_ATR_MULTIPLE * fs.atr
        if not at_level:
            continue

        evidence_refs = [
            f"regime={regime.regime.value} (opposing {direction.value.lower()} reversal)",
            f"divergence={fs.divergence}",
            f"close near structural level ({fs.close:.4f} vs {level:.4f})",
        ]
        failed_conditions: List[str] = []

        stoch_turning = (
            fs.stoch_rsi_k is not None and fs.stoch_rsi_d is not None
            and ((direction == Direction.SHORT and fs.stoch_rsi_k < fs.stoch_rsi_d)
                 or (direction == Direction.LONG and fs.stoch_rsi_k > fs.stoch_rsi_d))
        )
        rejection_candle = (
            (fs.close < closed[-2].low) if direction == Direction.SHORT else (fs.close > closed[-2].high)
        )
        if not stoch_turning:
            failed_conditions.append(f"stoch_rsi %K/%D ({fs.stoch_rsi_k}/{fs.stoch_rsi_d}) has not crossed back yet")
        else:
            evidence_refs.append(f"stoch_rsi %K={fs.stoch_rsi_k:.2f} %D={fs.stoch_rsi_d:.2f} crossing back")
        if not rejection_candle:
            failed_conditions.append("no short-term rejection candle closing past the prior candle's extreme yet")
        else:
            evidence_refs.append("short-term rejection candle confirmed")

        confirmation_met = stoch_turning and rejection_candle
        return SetupCandidate(
            setup_type=SetupType.REVERSAL, direction=direction,
            prerequisites_met=True, confirmation_met=confirmation_met,
            invalidation_price=level, evidence_refs=evidence_refs,
            failed_conditions=failed_conditions,
        )
    return None


# Priority order: an established, clean trend is checked first, then a
# pullback within a trend, then a distinctly different regime context
# (range-bound mean reversion), then a structural break, then a countertrend
# thesis last -- REVERSAL is deliberately positioned last because it
# contests whatever regime is currently established, so first-confirmed-
# wins-by-tuple-order already favors every regime-aligned family over it in
# any conflict this module doesn't explicitly resolve (see module
# docstring). Documented, fixed, deterministic.
_DETECTORS = (_trend_continuation, _pullback, _range_mean_reversion, _breakout_retest, _reversal)


def _resolve_reversal_vs_trend_continuation(confirmed: List[SetupCandidate]) -> List[SetupCandidate]:
    """Approved, explicit conflict rule -- see module docstring. Only
    applies when BOTH a TREND_CONTINUATION and a REVERSAL candidate are
    present in `confirmed` (i.e. both independently reached
    confirmation_met=True) AND they propose opposite directions. Returns
    `confirmed` unchanged in every other case."""
    trend = next((c for c in confirmed if c.setup_type == SetupType.TREND_CONTINUATION), None)
    reversal = next((c for c in confirmed if c.setup_type == SetupType.REVERSAL), None)
    if trend is not None and reversal is not None and trend.direction != reversal.direction:
        return [c for c in confirmed if c is not reversal]
    return confirmed


def detect_setup(closed: List[CandleData], fs: FeatureSet, regime: MarketRegime) -> Optional[SetupCandidate]:
    """Run every implemented detector in priority order. Returns the first
    CONFIRMED candidate found (after the REVERSAL/TREND_CONTINUATION
    conflict rule is applied -- see module docstring); if none are
    confirmed but at least one has prerequisites met (a forming
    candidate), returns the first such one. Returns None if nothing is
    even forming -- never forces a choice.
    """
    candidates = [c for c in (detector(closed, regime, fs) for detector in _DETECTORS) if c is not None]
    if not candidates:
        return None
    confirmed = _resolve_reversal_vs_trend_continuation([c for c in candidates if c.confirmation_met])
    if confirmed:
        return confirmed[0]
    return candidates[0]
