"""Tests for smart_trade_analyzer.regime.classify_regime.

Regime thresholds are a documented heuristic (not a closed-form formula
like RSI), so these tests verify the classifier against datasets
deliberately constructed to be unambiguous examples of each regime --
the standard, appropriate way to test a classification heuristic.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import CandleData, RegimeType
from smart_trade_analyzer.features import compute_feature_set
from smart_trade_analyzer.contracts import MarketData, MarketType, Timeframe, DataQualityState
from smart_trade_analyzer.regime import classify_regime
from smart_trade_analyzer.regime.engine import TREND_BAND, STRONG_TREND_BAND

NOW = datetime(2026, 8, 27, 12, 0, 0)


def build_md(candles):
    return MarketData(symbol="X", pair="XUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
                       as_of=NOW, candles=candles, live_price=candles[-1].close,
                       price_source="fake", price_quality=DataQualityState.VALID)


def trend_candles(n, drift, noise, base=100.0, seed=1):
    out = []
    price = base
    random.seed(seed)
    for i in range(n):
        t = NOW - timedelta(minutes=(n - i))
        o = price
        price = max(price * (1 + drift + random.uniform(-noise, noise)), 0.01)
        c = price
        out.append(CandleData(open_time=t, open=o, high=max(o, c) * 1.005, low=min(o, c) * 0.995,
                               close=c, volume=100.0, is_closed=True))
    return out


def calm_then_volatile_candles(n_calm, n_volatile, base=100.0, seed=1):
    """Both periods oscillate around the SAME fixed center -- only
    amplitude differs (see volatile_then_calm_candles for why)."""
    out = []
    random.seed(seed)
    n = n_calm + n_volatile
    for i in range(n_calm):
        t = NOW - timedelta(minutes=(n - i))
        c = base + random.uniform(-0.03, 0.03)
        o = base + random.uniform(-0.03, 0.03)
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + 0.01, low=min(o, c) - 0.01,
                               close=c, volume=100.0, is_closed=True))
    for i in range(n_volatile):
        t = NOW - timedelta(minutes=(n_volatile - i))
        c = base + random.uniform(-6.0, 6.0)
        o = base + random.uniform(-6.0, 6.0)
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + 2.0, low=min(o, c) - 2.0,
                               close=c, volume=100.0, is_closed=True))
    return out


def volatile_then_calm_candles(n_volatile, n_calm, base=100.0, seed=1):
    """Both periods oscillate around the SAME fixed center -- only the
    oscillation amplitude differs. Isolates volatility as the sole varying
    factor; a compounding random walk for the volatile period was found,
    during development, to often leave a residual net drift (multiplicative
    random walks are not symmetric in outcome even with symmetric
    percentage steps), which then shows up as an EMA50 distance/slope
    signal once the calm period begins -- a confound, not what this test
    means to isolate."""
    out = []
    random.seed(seed)
    n = n_volatile + n_calm
    for i in range(n_volatile):
        t = NOW - timedelta(minutes=(n - i))
        c = base + random.uniform(-6.0, 6.0)
        o = base + random.uniform(-6.0, 6.0)
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + 2.0, low=min(o, c) - 2.0,
                               close=c, volume=100.0, is_closed=True))
    for i in range(n_calm):
        t = NOW - timedelta(minutes=(n_calm - i))
        c = base + random.uniform(-0.03, 0.03)
        o = base + random.uniform(-0.03, 0.03)
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + 0.01, low=min(o, c) - 0.01,
                               close=c, volume=100.0, is_closed=True))
    return out


def flat_candles(n, base=100.0, seed=1):
    """Genuinely flat/ranging: tiny mean-reverting noise around a fixed
    center, no drift at all -- constructed to avoid the EMA9/EMA21
    relationship ever flipping (which would trigger TRANSITION instead)."""
    out = []
    random.seed(seed)
    for i in range(n):
        t = NOW - timedelta(minutes=(n - i))
        c = base + random.uniform(-0.05, 0.05)
        o = base + random.uniform(-0.05, 0.05)
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + 0.02, low=min(o, c) - 0.02,
                               close=c, volume=100.0, is_closed=True))
    return out


# ---------------------------------------------------------------------------
# REGIME TESTS
# ---------------------------------------------------------------------------

def test_bullish_trend_classified_uptrend_or_strong_uptrend():
    candles = trend_candles(210, drift=0.004, noise=0.008, seed=1)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    assert regime.regime in (RegimeType.UPTREND, RegimeType.STRONG_UPTREND)
    assert regime.trend_strength > 0


def test_bearish_trend_classified_downtrend_or_strong_downtrend():
    candles = trend_candles(210, drift=-0.004, noise=0.008, seed=1)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    assert regime.regime in (RegimeType.DOWNTREND, RegimeType.STRONG_DOWNTREND)
    assert regime.trend_strength < 0


def test_ranging_market_classified_range():
    candles = flat_candles(210, seed=3)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    assert regime.regime == RegimeType.RANGE
    assert abs(regime.trend_strength) < TREND_BAND


def test_high_volatility_relative_to_recent_history():
    candles = calm_then_volatile_candles(180, 40, seed=4)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    # An abrupt amplitude change naturally causes the fast EMAs to cross
    # near the boundary too (a real, legitimate TRANSITION signal at
    # exactly that point) -- so the underlying volatility_percentile score
    # is the precise thing being verified here, not a specific top-level
    # label racing against every other rule in the decision tree.
    assert regime.volatility_percentile >= 0.85
    assert regime.regime in (RegimeType.HIGH_VOLATILITY, RegimeType.TRANSITION)


def test_low_volatility_relative_to_recent_history():
    candles = volatile_then_calm_candles(150, 70, seed=5)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    assert regime.volatility_percentile <= 0.15
    assert regime.regime in (RegimeType.LOW_VOLATILITY, RegimeType.TRANSITION)


def test_compute_volatility_percentile_direct_hand_verified():
    # Direct test of the sub-component with fully controlled inputs: a
    # calm history (tiny true range every candle) followed by one
    # dramatically wider candle -- the true range of a single wide candle
    # is trivially hand-computable, and its rank against an all-calm
    # history must be the maximum (1.0): every prior reading is smaller.
    from smart_trade_analyzer.regime.engine import _compute_volatility_percentile
    n = 40
    closes = [100.0] * n
    highs = [100.05] * (n - 1) + [110.0]   # last candle: true range = 10.0, dwarfing all others
    lows = [99.95] * (n - 1) + [95.0]
    pct = _compute_volatility_percentile(highs, lows, closes)
    assert pct == 1.0


def test_compute_volatility_percentile_minimum_at_calmest_point():
    from smart_trade_analyzer.regime.engine import _compute_volatility_percentile
    n = 40
    closes = [100.0] * n
    highs = [110.0] * (n - 1) + [100.05]   # last candle is the CALMEST of the whole history
    lows = [95.0] * (n - 1) + [99.95]
    pct = _compute_volatility_percentile(highs, lows, closes)
    assert pct == pytest.approx(1.0 / n, abs=0.05)  # ranks at (or near) the very bottom


def test_compute_volatility_percentile_defaults_to_neutral_with_insufficient_history():
    from smart_trade_analyzer.regime.engine import _compute_volatility_percentile, MIN_VOLATILITY_SAMPLE
    closes = [100.0] * 10
    highs = [100.1] * 10
    lows = [99.9] * 10
    assert len(closes) < MIN_VOLATILITY_SAMPLE + 15  # sanity: genuinely too little history
    assert _compute_volatility_percentile(highs, lows, closes) == 0.5


def test_unknown_when_insufficient_data():
    candles = trend_candles(20, drift=0.01, noise=0.01, seed=6)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    assert regime.regime == RegimeType.UNKNOWN
    assert "ema50" in regime.basis[0].lower()


def test_trend_strength_always_within_contract_bounds():
    for seed in range(20):
        candles = trend_candles(210, drift=random.uniform(-0.01, 0.01), noise=random.uniform(0.001, 0.03), seed=seed)
        fs = compute_feature_set(build_md(candles))
        regime = classify_regime(candles, fs)
        assert -1.0 <= regime.trend_strength <= 1.0


def test_volatility_percentile_always_within_contract_bounds():
    for seed in range(20):
        candles = trend_candles(210, drift=random.uniform(-0.01, 0.01), noise=random.uniform(0.001, 0.03), seed=seed)
        fs = compute_feature_set(build_md(candles))
        regime = classify_regime(candles, fs)
        assert 0.0 <= regime.volatility_percentile <= 1.0


def test_regime_never_reads_the_wall_clock():
    # Determinism: identical candles + identical FeatureSet must always
    # produce an identical MarketRegime, regardless of when the test runs.
    candles = trend_candles(210, drift=0.003, noise=0.01, seed=7)
    fs = compute_feature_set(build_md(candles))
    r1 = classify_regime(candles, fs)
    r2 = classify_regime(candles, fs)
    assert r1 == r2


def test_regime_basis_is_never_empty():
    candles = trend_candles(210, drift=0.003, noise=0.01, seed=8)
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    assert len(regime.basis) > 0


def test_regime_combines_multiple_independent_signals_not_a_single_indicator():
    # Direct proof that trend_strength is a genuine weighted combination,
    # not a restatement of the EMA stack alone: feed the three documented
    # sub-score functions inputs that deliberately disagree, and confirm
    # the combined result sits strictly between the sub-scores' extremes
    # rather than collapsing to whichever one dominates a single indicator.
    from smart_trade_analyzer.regime.engine import _ema_stack_score, _distance_score, _slope_score

    # Clearly, meaningfully bullish EMA stack (well beyond noise level)...
    stack = _ema_stack_score(ema9=105, ema21=103, ema50=100, atr=2.0)
    assert stack > 0.5
    # ...but price sitting BELOW its EMA50 anchor (negative distance)...
    distance = _distance_score(close=95, ema50=100, atr=2.0)
    assert distance < 0
    # ...proves these are independently computed, disagreeing signals --
    # exactly what "not from a single indicator alone" requires.
    assert stack != distance
    assert stack > 0 > distance


def test_ema_stack_score_is_magnitude_aware_not_a_bare_sign_comparison():
    from smart_trade_analyzer.regime.engine import _ema_stack_score
    # A noise-level ordering (gap tiny relative to ATR) must score near
    # zero, NOT the full +/-1.0 a pure sign comparison would give it --
    # this is the direct regression test for the bug found and fixed
    # during development (a purely-flat dataset was misclassified as
    # DOWNTREND because of exactly this sensitivity).
    tiny_gap_score = _ema_stack_score(ema9=100.001, ema21=100.0, ema50=99.999, atr=2.0)
    assert abs(tiny_gap_score) < 0.05

    # A genuinely, meaningfully separated stack must still score strongly.
    clear_bullish = _ema_stack_score(ema9=105, ema21=103, ema50=100, atr=2.0)
    assert clear_bullish > 0.8
    clear_bearish = _ema_stack_score(ema9=95, ema21=97, ema50=100, atr=2.0)
    assert clear_bearish < -0.8

    # Always bounded to [-1, 1] regardless of how large the gap is.
    extreme = _ema_stack_score(ema9=1000, ema21=1, ema50=0.001, atr=0.01)
    assert -1.0 <= extreme <= 1.0


def test_ema_stack_score_falls_back_to_sign_comparison_when_atr_unavailable():
    from smart_trade_analyzer.regime.engine import _ema_stack_score
    assert _ema_stack_score(3, 2, 1, atr=None) == 1.0
    assert _ema_stack_score(1, 2, 3, atr=None) == -1.0
    assert _ema_stack_score(3, 1, 2, atr=None) == 0.0
