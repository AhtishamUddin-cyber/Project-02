"""Tests for smart_trade_analyzer.features.engine (compute_feature_set,
closed_candles). Covers readiness (10.READINESS TESTS) and data-integrity
(10.DATA INTEGRITY) requirements from the Phase 3 brief.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import CandleData, DataQualityState, MarketData, MarketType, Timeframe
from smart_trade_analyzer.features import closed_candles, compute_feature_set
from smart_trade_analyzer.features.readiness import (
    MIN_CANDLES_EMA200,
    MIN_CANDLES_FULL_FEATURE_SET,
)

NOW = datetime(2026, 8, 27, 12, 0, 0)


def realistic_closes(n, drift=0.002, noise=0.01, base=100.0, seed=1):
    random.seed(seed)
    out = [base]
    for _ in range(n - 1):
        out.append(max(out[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    return out


def make_candles(n, seed=1, last_closed=True, base=100.0):
    closes = realistic_closes(n, base=base, seed=seed)
    out = []
    price = base
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = price
        is_closed = last_closed or i < n - 1
        out.append(CandleData(open_time=t, open=o, high=max(o, c) * 1.005, low=min(o, c) * 0.995,
                               close=c, volume=100.0, is_closed=is_closed))
        price = c
    return out


def make_candles_with_independent_wicks(n, seed=1, base=100.0, drift=0.0, noise=0.025):
    """Like make_candles, but with an independent random high/low wick per
    candle instead of a fixed max(o,c)*1.005 / min(o,c)*0.995 shape. That
    fixed shape ties adjacent highs together at every local price peak,
    which (correctly, per structure.py's strict tie rule) suppresses every
    pivot -- a property of that specific synthetic OHLC construction, not
    of real market data. Used wherever a test needs swing/divergence to
    genuinely activate rather than reliably read None."""
    random.seed(seed)
    closes = [base]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    out = []
    price = base
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = price
        body_high, body_low = max(o, c), min(o, c)
        wick_up = body_high * random.uniform(0.001, 0.01)
        wick_down = body_low * random.uniform(0.001, 0.01)
        out.append(CandleData(open_time=t, open=o, high=body_high + wick_up, low=body_low - wick_down,
                               close=c, volume=100.0, is_closed=True))
        price = c
    return out


def build_md(candles):
    return MarketData(symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
                       as_of=NOW, candles=candles, live_price=candles[-1].close if candles else None,
                       price_source="fake", price_quality=DataQualityState.VALID)


# ---------------------------------------------------------------------------
# closed_candles
# ---------------------------------------------------------------------------

def test_closed_candles_excludes_forming_candle():
    candles = make_candles(10, last_closed=False)
    closed = closed_candles(build_md(candles))
    assert len(closed) == 9
    assert all(c.is_closed for c in closed)


def test_closed_candles_empty_when_all_forming():
    c = make_candles(1, last_closed=False)
    md = build_md(c)
    assert closed_candles(md) == []


# ---------------------------------------------------------------------------
# READINESS TESTS
# ---------------------------------------------------------------------------

def test_insufficient_candles_yields_none_feature_set_fields():
    candles = make_candles(30, last_closed=True)
    fs = compute_feature_set(build_md(candles))
    assert fs is not None
    assert fs.ema9 is not None      # 9 <= 30
    assert fs.ema50 is None         # 50 > 30
    assert fs.ema200 is None        # 200 > 30
    assert 0.0 < fs.completeness < 1.0


def test_exact_minimum_candles_for_ema200():
    below = make_candles(MIN_CANDLES_EMA200 - 1, last_closed=True)
    at_min = make_candles(MIN_CANDLES_EMA200, last_closed=True)
    fs_below = compute_feature_set(build_md(below))
    fs_at = compute_feature_set(build_md(at_min))
    assert fs_below.ema200 is None
    assert fs_at.ema200 is not None


def test_sufficient_candles_yields_full_completeness():
    candles = make_candles(MIN_CANDLES_FULL_FEATURE_SET + 10, last_closed=True)
    fs = compute_feature_set(build_md(candles))
    assert fs.completeness == 1.0
    for field in (fs.ema9, fs.ema21, fs.ema50, fs.ema200, fs.rsi14, fs.macd_line, fs.macd_signal,
                  fs.macd_hist, fs.stoch_rsi_k, fs.stoch_rsi_d, fs.bb_upper, fs.bb_mid, fs.bb_lower,
                  fs.atr, fs.atr_pct, fs.volume_ratio):
        assert field is not None


def test_structural_fields_do_not_affect_completeness_even_when_none():
    # This fixture's OHLC construction (max(o,c)*1.005 / min(o,c)*0.995)
    # happens to create a tie between adjacent highs at every local price
    # peak, which correctly yields zero pivots under this module's strict
    # tie-disqualification rule (see features/structure.py) -- so this
    # fixture reliably gives None for all three structural fields. That
    # makes it a good vehicle for exactly one claim: completeness must
    # stay unaffected by that, not a claim that these fields are always
    # None in general (see the next test for the opposite case).
    candles = make_candles(MIN_CANDLES_FULL_FEATURE_SET + 10, last_closed=True)
    fs = compute_feature_set(build_md(candles))
    assert fs.swing_support is None
    assert fs.swing_resistance is None
    assert fs.divergence is None
    assert fs.completeness == 1.0  # sixteen-field completeness is unaffected either way


def test_structural_fields_are_genuinely_populated_when_price_structure_supports_it():
    # Independent random high/low wicks (unlike the fixture above) --
    # verified to produce real, non-None values for all three fields.
    candles = make_candles_with_independent_wicks(210, seed=2, noise=0.025)
    fs = compute_feature_set(build_md(candles))
    assert fs.swing_support is not None
    assert fs.swing_resistance is not None
    assert fs.divergence in ("BULLISH", "BEARISH")
    assert fs.completeness == 1.0  # still unaffected, in either direction


def test_readiness_covers_all_nineteen_fields_including_structural_ones():
    from smart_trade_analyzer.features import readiness as rdns

    flags = rdns.readiness(rdns.STRUCTURAL_MIN_CANDLES["divergence"])
    assert set(flags.keys()) == set(rdns.FEATURE_MIN_CANDLES) | set(rdns.STRUCTURAL_MIN_CANDLES)
    assert flags["swing_support"] is True
    assert flags["swing_resistance"] is True
    assert flags["divergence"] is True

    below_minimum = rdns.readiness(rdns.STRUCTURAL_MIN_CANDLES["divergence"] - 100)
    # not necessarily False for every one of the 16 original fields at this
    # count, but the three structural fields specifically must read False
    # far enough below their own minimum
    tiny = rdns.readiness(1)
    assert tiny["swing_support"] is False
    assert tiny["swing_resistance"] is False
    assert tiny["divergence"] is False


def test_no_nan_leakage_anywhere_in_feature_set():
    import math
    candles = make_candles(MIN_CANDLES_FULL_FEATURE_SET + 10, last_closed=True)
    fs = compute_feature_set(build_md(candles))
    for name in ("close", "ema9", "ema21", "ema50", "ema200", "rsi14", "stoch_rsi_k", "stoch_rsi_d",
                 "macd_line", "macd_signal", "macd_hist", "bb_upper", "bb_mid", "bb_lower",
                 "atr", "atr_pct", "volume_ratio", "completeness"):
        value = getattr(fs, name)
        if value is not None:
            assert math.isfinite(value), f"{name}={value} is not finite"


def test_no_infinity_leakage_when_input_contains_extreme_but_finite_values():
    import math
    candles = make_candles(MIN_CANDLES_FULL_FEATURE_SET + 10, last_closed=True, base=1e6)
    fs = compute_feature_set(build_md(candles))
    for name in ("ema9", "ema200", "rsi14", "atr", "volume_ratio"):
        value = getattr(fs, name)
        if value is not None:
            assert math.isfinite(value)


# ---------------------------------------------------------------------------
# DATA INTEGRITY
# ---------------------------------------------------------------------------

def test_empty_market_data_yields_none():
    md = build_md([])
    assert compute_feature_set(md) is None


def test_all_forming_no_closed_candles_yields_none():
    candles = make_candles(50, last_closed=False)
    only_forming_md = MarketData(
        symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
        as_of=NOW, candles=[candles[-1]], live_price=candles[-1].close,
        price_source="fake", price_quality=DataQualityState.VALID,
    )
    assert compute_feature_set(only_forming_md) is None


def test_incomplete_current_candle_is_excluded_not_treated_as_completed():
    candles = make_candles(60, last_closed=False)  # last one is_closed=False
    fs_with_unclosed_present = compute_feature_set(build_md(candles))
    fs_without_it_at_all = compute_feature_set(build_md(candles[:-1]))
    assert fs_with_unclosed_present == fs_without_it_at_all


def test_duplicate_timestamps_in_candles_does_not_crash_the_engine():
    # Phase 2's validator is the one that FLAGS duplicate timestamps as a
    # DataQuality concern -- this is a defensive check that the Feature
    # Engine itself doesn't crash or behave pathologically if handed such
    # data anyway (e.g. a caller bypassing the normal Phase 2 pipeline).
    candles = make_candles(60, last_closed=True)
    dup = list(candles)
    dup[30] = CandleData(open_time=dup[29].open_time, open=dup[30].open, high=dup[30].high,
                          low=dup[30].low, close=dup[30].close, volume=dup[30].volume, is_closed=True)
    fs = compute_feature_set(build_md(dup))  # must not raise
    assert fs is not None


def test_single_closed_candle_produces_a_minimal_but_valid_feature_set():
    candles = make_candles(1, last_closed=True)
    fs = compute_feature_set(build_md(candles))
    assert fs is not None
    assert fs.close == candles[0].close
    assert fs.completeness == 0.0  # nothing has enough history yet, but it's a real, valid FeatureSet
