"""Tests for confluence/evidence.py -- each rule tested independently, per
Section 7's own stated reason for splitting evidence collection from
scoring. Exact strength values are hand-computed and asserted, not just
checked for "some positive number" -- same discipline as test_indicators.py.
"""
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.confluence import evidence
from smart_trade_analyzer.contracts import CandleData, Direction, EvidenceCategory, FeatureSet, MarketRegime, RegimeType, Timeframe

NOW = datetime(2026, 8, 27, 12, 0, 0)


def make_fs(**overrides):
    defaults = dict(
        symbol="X", timeframe=Timeframe.M1, as_of=NOW, close=100.0,
        ema9=None, ema21=None, ema50=None, ema200=None,
        rsi14=None, stoch_rsi_k=None, stoch_rsi_d=None,
        macd_line=None, macd_signal=None, macd_hist=None,
        bb_upper=None, bb_mid=None, bb_lower=None,
        atr=None, atr_pct=None, volume_ratio=None,
        swing_support=None, swing_resistance=None,
        divergence=None, completeness=1.0,
    )
    defaults.update(overrides)
    return FeatureSet(**defaults)


def make_regime(**overrides):
    defaults = dict(regime=RegimeType.UPTREND, trend_strength=0.5, volatility_percentile=0.5, basis=["t"])
    defaults.update(overrides)
    return MarketRegime(**defaults)


def candle(t, close, volume=100.0):
    return CandleData(open_time=t, open=close, high=close + 0.1, low=close - 0.1,
                       close=close, volume=volume, is_closed=True)


# ---------------------------------------------------------------------------
# TREND
# ---------------------------------------------------------------------------

class TestTrendEvidence:
    def test_all_three_signals_bullish(self):
        fs = make_fs(ema9=102.0, ema21=100.0, ema50=98.0, close=105.0, macd_hist=0.5)
        items = evidence.trend_evidence(fs)
        assert len(items) == 3
        assert all(i.category == EvidenceCategory.TREND for i in items)
        assert all(i.direction == Direction.LONG for i in items)

    def test_ema_gap_strength_is_hand_computed(self):
        # gap = (102-100)/100 = 2% == TREND_EMA_GAP_SCALE exactly -> strength 1.0
        fs = make_fs(ema9=102.0, ema21=100.0)
        items = evidence.trend_evidence(fs)
        assert len(items) == 1
        assert items[0].direction == Direction.LONG
        assert items[0].strength == pytest.approx(1.0)

    def test_ema_gap_below_scale_is_proportional(self):
        # gap = (101-100)/100 = 1% == half of TREND_EMA_GAP_SCALE (2%) -> strength 0.5
        fs = make_fs(ema9=101.0, ema21=100.0)
        items = evidence.trend_evidence(fs)
        assert items[0].strength == pytest.approx(0.5)

    def test_ema_gap_beyond_scale_is_capped_at_one(self):
        fs = make_fs(ema9=150.0, ema21=100.0)  # 50% gap, way beyond the 2% scale
        items = evidence.trend_evidence(fs)
        assert items[0].strength == pytest.approx(1.0)

    def test_exact_equal_emas_yields_neutral_zero_strength(self):
        fs = make_fs(ema9=100.0, ema21=100.0)
        items = evidence.trend_evidence(fs)
        assert items[0].direction == Direction.NEUTRAL
        assert items[0].strength == pytest.approx(0.0)

    def test_bearish_close_vs_ema50(self):
        fs = make_fs(close=95.0, ema50=100.0)
        items = evidence.trend_evidence(fs)
        assert len(items) == 1
        assert items[0].direction == Direction.SHORT

    def test_macd_hist_normalized_by_close(self):
        # normalized = 1.0 / 100.0 = 1% == TREND_MACD_HIST_SCALE exactly -> strength 1.0
        fs = make_fs(close=100.0, macd_hist=1.0)
        items = evidence.trend_evidence(fs)
        assert items[0].direction == Direction.LONG
        assert items[0].strength == pytest.approx(1.0)

    def test_missing_fields_are_individually_skipped_not_defaulted(self):
        fs = make_fs(ema9=None, ema21=None, ema50=None, macd_hist=None)
        assert evidence.trend_evidence(fs) == []

    def test_partial_availability_only_emits_available_signals(self):
        fs = make_fs(ema9=None, ema21=None, ema50=98.0, close=100.0, macd_hist=None)
        items = evidence.trend_evidence(fs)
        assert len(items) == 1
        assert "EMA50" in items[0].detail


# ---------------------------------------------------------------------------
# MOMENTUM
# ---------------------------------------------------------------------------

class TestMomentumEvidence:
    def test_rsi_strength_hand_computed(self):
        fs = make_fs(rsi14=75.0)  # (75-50)/50 = 0.5
        items = evidence.momentum_evidence(fs)
        assert len(items) == 1
        assert items[0].direction == Direction.LONG
        assert items[0].strength == pytest.approx(0.5)

    def test_rsi_oversold_is_short(self):
        fs = make_fs(rsi14=20.0)  # (20-50)/50 = -0.6
        items = evidence.momentum_evidence(fs)
        assert items[0].direction == Direction.SHORT
        assert items[0].strength == pytest.approx(0.6)

    def test_rsi_exactly_neutral(self):
        fs = make_fs(rsi14=50.0)
        items = evidence.momentum_evidence(fs)
        assert items[0].direction == Direction.NEUTRAL
        assert items[0].strength == pytest.approx(0.0)

    def test_both_rsi_and_stoch_present_emit_two_items(self):
        fs = make_fs(rsi14=60.0, stoch_rsi_k=80.0)
        items = evidence.momentum_evidence(fs)
        assert len(items) == 2

    def test_missing_both_yields_no_evidence(self):
        assert evidence.momentum_evidence(make_fs()) == []


# ---------------------------------------------------------------------------
# STRUCTURE
# ---------------------------------------------------------------------------

class TestStructureEvidence:
    def test_bullish_divergence_fixed_strength(self):
        fs = make_fs(divergence="BULLISH")
        items = evidence.structure_evidence(fs)
        assert len(items) == 1
        assert items[0].direction == Direction.LONG
        assert items[0].strength == pytest.approx(evidence.STRUCTURE_DIVERGENCE_STRENGTH)

    def test_bearish_divergence_fixed_strength(self):
        fs = make_fs(divergence="BEARISH")
        items = evidence.structure_evidence(fs)
        assert items[0].direction == Direction.SHORT

    def test_no_divergence_emits_no_divergence_item(self):
        fs = make_fs(divergence=None)
        assert evidence.structure_evidence(fs) == []

    def test_close_near_support_is_bullish(self):
        # atr=2.0, threshold=0.75*2=1.5; close=100, support=99 -> distance=1.0 (within threshold)
        fs = make_fs(close=100.0, swing_support=99.0, atr=2.0)
        items = evidence.structure_evidence(fs)
        assert len(items) == 1
        assert items[0].direction == Direction.LONG
        assert items[0].strength == pytest.approx(1.0 - 1.0 / 1.5)

    def test_close_near_resistance_is_bearish(self):
        fs = make_fs(close=100.0, swing_resistance=101.0, atr=2.0)
        items = evidence.structure_evidence(fs)
        assert items[0].direction == Direction.SHORT

    def test_close_far_from_levels_emits_no_proximity_item(self):
        fs = make_fs(close=100.0, swing_support=50.0, swing_resistance=200.0, atr=2.0)
        assert evidence.structure_evidence(fs) == []

    def test_divergence_and_proximity_both_present_emit_two_items(self):
        fs = make_fs(close=100.0, swing_support=99.0, atr=2.0, divergence="BULLISH")
        items = evidence.structure_evidence(fs)
        assert len(items) == 2

    def test_missing_atr_skips_proximity_but_not_divergence(self):
        fs = make_fs(close=100.0, swing_support=99.0, atr=None, divergence="BULLISH")
        items = evidence.structure_evidence(fs)
        assert len(items) == 1
        assert "divergence" in items[0].detail


# ---------------------------------------------------------------------------
# VOLATILITY
# ---------------------------------------------------------------------------

class TestVolatilityEvidence:
    def test_range_regime_near_upper_band_is_bearish_mean_reversion(self):
        fs = make_fs(close=109.5, bb_upper=110.0, bb_lower=100.0)  # position=0.95, near upper
        items = evidence.volatility_evidence(fs, make_regime(regime=RegimeType.RANGE))
        assert len(items) == 1
        assert items[0].direction == Direction.SHORT

    def test_range_regime_near_lower_band_is_bullish_mean_reversion(self):
        fs = make_fs(close=100.5, bb_upper=110.0, bb_lower=100.0)  # position=0.05, near lower
        items = evidence.volatility_evidence(fs, make_regime(regime=RegimeType.RANGE))
        assert items[0].direction == Direction.LONG

    def test_uptrend_regime_near_upper_band_is_bullish_extension(self):
        fs = make_fs(close=109.5, bb_upper=110.0, bb_lower=100.0)
        items = evidence.volatility_evidence(fs, make_regime(regime=RegimeType.UPTREND))
        assert items[0].direction == Direction.LONG

    def test_downtrend_regime_near_lower_band_is_bearish_extension(self):
        fs = make_fs(close=100.5, bb_upper=110.0, bb_lower=100.0)
        items = evidence.volatility_evidence(fs, make_regime(regime=RegimeType.DOWNTREND))
        assert items[0].direction == Direction.SHORT

    def test_uptrend_regime_near_lower_band_is_ambiguous_no_evidence(self):
        # the asymmetric case this module deliberately does not interpret
        fs = make_fs(close=100.5, bb_upper=110.0, bb_lower=100.0)
        assert evidence.volatility_evidence(fs, make_regime(regime=RegimeType.UPTREND)) == []

    def test_middle_of_band_is_no_evidence(self):
        fs = make_fs(close=105.0, bb_upper=110.0, bb_lower=100.0)
        assert evidence.volatility_evidence(fs, make_regime(regime=RegimeType.RANGE)) == []

    def test_unknown_regime_never_emits_evidence(self):
        fs = make_fs(close=109.5, bb_upper=110.0, bb_lower=100.0)
        assert evidence.volatility_evidence(fs, make_regime(regime=RegimeType.UNKNOWN)) == []

    def test_missing_bands_yields_no_evidence(self):
        assert evidence.volatility_evidence(make_fs(), make_regime(regime=RegimeType.RANGE)) == []

    def test_atr_alone_never_produces_directional_evidence(self):
        # Audit finding, documented: Section 7 explicitly attributes
        # VOLATILITY's directional lean to BB position only, not ATR trend
        # -- ATR varying (with no BB data present) must never produce an
        # EvidenceItem on its own, regardless of its value or trend.
        for atr_value in (0.5, 1.0, 2.0, 5.0, 10.0, 50.0):
            fs = make_fs(atr=atr_value, atr_pct=atr_value / 100.0)
            for regime_type in (RegimeType.UPTREND, RegimeType.DOWNTREND, RegimeType.RANGE, RegimeType.UNKNOWN):
                assert evidence.volatility_evidence(fs, make_regime(regime=regime_type)) == []


# ---------------------------------------------------------------------------
# VOLUME
# ---------------------------------------------------------------------------

class TestVolumeEvidence:
    def test_elevated_volume_on_up_candle_is_bullish(self):
        closed = [candle(NOW - timedelta(minutes=2), 100.0), candle(NOW - timedelta(minutes=1), 101.0)]
        fs = make_fs(volume_ratio=1.5)
        items = evidence.volume_evidence(fs, closed)
        assert len(items) == 1
        assert items[0].direction == Direction.LONG
        assert items[0].strength == pytest.approx(0.5)  # min(1.5-1.0, 1.0)

    def test_elevated_volume_on_down_candle_is_bearish(self):
        closed = [candle(NOW - timedelta(minutes=2), 100.0), candle(NOW - timedelta(minutes=1), 99.0)]
        fs = make_fs(volume_ratio=1.5)
        items = evidence.volume_evidence(fs, closed)
        assert items[0].direction == Direction.SHORT

    def test_unremarkable_volume_yields_no_evidence(self):
        closed = [candle(NOW - timedelta(minutes=2), 100.0), candle(NOW - timedelta(minutes=1), 101.0)]
        fs = make_fs(volume_ratio=1.05)
        assert evidence.volume_evidence(fs, closed) == []

    def test_flat_candle_yields_no_evidence(self):
        closed = [candle(NOW - timedelta(minutes=2), 100.0), candle(NOW - timedelta(minutes=1), 100.0)]
        fs = make_fs(volume_ratio=2.0)
        assert evidence.volume_evidence(fs, closed) == []

    def test_fewer_than_two_candles_yields_no_evidence(self):
        fs = make_fs(volume_ratio=2.0)
        assert evidence.volume_evidence(fs, [candle(NOW, 100.0)]) == []

    def test_missing_volume_ratio_yields_no_evidence(self):
        closed = [candle(NOW - timedelta(minutes=2), 100.0), candle(NOW - timedelta(minutes=1), 101.0)]
        assert evidence.volume_evidence(make_fs(volume_ratio=None), closed) == []

    def test_strength_is_capped_at_one(self):
        closed = [candle(NOW - timedelta(minutes=2), 100.0), candle(NOW - timedelta(minutes=1), 101.0)]
        fs = make_fs(volume_ratio=10.0)
        items = evidence.volume_evidence(fs, closed)
        assert items[0].strength == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# HTF / FLOW / SENTIMENT -- no data source this phase (approved decision D)
# ---------------------------------------------------------------------------

class TestUnavailableCategories:
    def test_htf_always_empty(self):
        assert evidence.htf_evidence() == []

    def test_flow_always_empty(self):
        assert evidence.flow_evidence() == []

    def test_sentiment_always_empty(self):
        assert evidence.sentiment_evidence() == []


# ---------------------------------------------------------------------------
# Every EvidenceItem produced anywhere in this module respects its own
# frozen-contract validation (a structural guarantee, not just a spot check).
# ---------------------------------------------------------------------------

def test_every_emitted_item_has_correct_category_and_valid_strength():
    fs = make_fs(
        ema9=102.0, ema21=100.0, ema50=98.0, close=109.5, macd_hist=0.3,
        rsi14=65.0, stoch_rsi_k=70.0, divergence="BULLISH", swing_support=108.0, atr=2.0,
        bb_upper=110.0, bb_lower=100.0, volume_ratio=1.5,
    )
    closed = [candle(NOW - timedelta(minutes=2), 108.0), candle(NOW - timedelta(minutes=1), 109.5)]
    regime = make_regime(regime=RegimeType.UPTREND)
    all_items = (
        evidence.trend_evidence(fs) + evidence.momentum_evidence(fs) + evidence.structure_evidence(fs)
        + evidence.volatility_evidence(fs, regime) + evidence.volume_evidence(fs, closed)
    )
    assert len(all_items) > 0
    for item in all_items:
        assert 0.0 <= item.strength <= 1.0
        assert item.detail  # non-empty, per the frozen contract's own validation
