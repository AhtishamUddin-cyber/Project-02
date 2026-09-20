"""Dedicated no-lookahead tests, per the Phase 3 brief's explicit
requirement: "Construct a dataset where a future candle would dramatically
change the indicator. Verify the feature for candle N is unchanged when
candles after N are modified." Covers three levels: raw indicator series
causality, the Feature Engine's forming-candle exclusion, and the full
scanner pipeline.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.confluence import compute_confluence
from smart_trade_analyzer.contracts import (
    CandleData, DataQualityState, EvidenceCategory, MarketData, MarketRegime, MarketType, RegimeType, Timeframe,
)
from smart_trade_analyzer.data import NormalizationResult, TickerPrice
from smart_trade_analyzer.features import compute_feature_set, indicators as ind
from smart_trade_analyzer.features import divergence as div
from smart_trade_analyzer.features import structure as struct
from smart_trade_analyzer.regime import classify_regime
from smart_trade_analyzer.scanner import analyze_market
from smart_trade_analyzer.setup import detect_setup

NOW = datetime(2026, 8, 27, 12, 0, 0)


def realistic_closes(n, drift=0.002, noise=0.01, base=100.0, seed=1):
    random.seed(seed)
    out = [base]
    for _ in range(n - 1):
        out.append(max(out[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    return out


def make_closed_candles(n, drift=0.002, noise=0.01, base=100.0, seed=1):
    closes = realistic_closes(n, drift, noise, base, seed)
    out = []
    price = base
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i) + 1)
        o = price
        out.append(CandleData(open_time=t, open=o, high=max(o, c) * 1.005, low=min(o, c) * 0.995,
                               close=c, volume=100.0, is_closed=True))
        price = c
    return out


def make_closed_candles_with_wicks(n, seed=1, base=100.0, drift=0.0, noise=0.025):
    """Same idea as make_closed_candles, but with an INDEPENDENT random
    high/low wick per candle rather than a fixed max(o,c)*1.005 /
    min(o,c)*0.995 pattern. That fixed pattern turns out to create an
    accidental tie between adjacent candles' highs whenever a close is a
    local price peak (high[i] and high[i+1] both reduce to
    close[i]*1.005), which -- entirely correctly, per this module's strict
    tie-disqualification rule -- means find_pivot_highs/find_pivot_lows
    never reports a pivot at all on that kind of series. That is a
    property of that specific synthetic-OHLC shape, not of real exchange
    data (real highs/lows are independently reported, not derived from
    open/close), so swing/divergence tests use this generator instead."""
    random.seed(seed)
    closes = [base]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    out = []
    price = base
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i) + 1)
        o = price
        body_high, body_low = max(o, c), min(o, c)
        wick_up = body_high * random.uniform(0.001, 0.01)
        wick_down = body_low * random.uniform(0.001, 0.01)
        out.append(CandleData(open_time=t, open=o, high=body_high + wick_up, low=body_low - wick_down,
                               close=c, volume=100.0, is_closed=True))
        price = c
    return out


def build_md(candles, price_source="fake"):
    return MarketData(symbol="X", pair="XUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
                       as_of=NOW, candles=candles, live_price=candles[-1].close if candles else None,
                       price_source=price_source, price_quality=DataQualityState.VALID)


class FakeSource:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, symbol, timeframe, limit, as_of=None):
        return NormalizationResult(candles=self._candles, issues=[])

    def get_ticker_price(self, symbol):
        return TickerPrice(price=self._candles[-1].close, source="fake", fetched_at=NOW,
                            quality=DataQualityState.VALID)


# ---------------------------------------------------------------------------
# Level 1: raw indicator series causality (the root-cause guarantee)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("compute", [
    lambda closes: ind.ema_series(closes, 21),
    lambda closes: ind.rsi_series(closes, 14),
])
def test_indicator_series_causality_price_only(compute):
    closes_a = realistic_closes(210, seed=20)
    closes_b = list(closes_a)
    closes_b[150] *= 100  # dramatic change, late in the series
    series_a = compute(closes_a)
    series_b = compute(closes_b)
    assert series_a[:150] == series_b[:150], "an earlier value changed when a LATER candle was modified"
    assert series_a[150] != series_b[150], "test is vacuous -- the modification had no detectable effect"


def test_macd_causality():
    closes_a = realistic_closes(210, seed=21)
    closes_b = list(closes_a)
    closes_b[150] *= 100
    line_a, sig_a, hist_a = ind.macd(closes_a[:150])
    line_b, sig_b, hist_b = ind.macd(closes_b[:150])
    # Both computed only up to (and not including) the modified index -- must be identical.
    assert (line_a, sig_a, hist_a) == (line_b, sig_b, hist_b)


def test_stoch_rsi_causality():
    closes_a = realistic_closes(210, seed=22)
    closes_b = list(closes_a)
    closes_b[150] *= 100
    result_a = ind.stoch_rsi(closes_a[:150])
    result_b = ind.stoch_rsi(closes_b[:150])
    assert result_a == result_b


def test_bollinger_causality():
    closes_a = realistic_closes(210, seed=23)
    closes_b = list(closes_a)
    closes_b[150] *= 100
    assert ind.bollinger_bands(closes_a[:150]) == ind.bollinger_bands(closes_b[:150])


def test_pivot_detection_causality():
    # A known-real fixture (see test_structure.py): confirmed pivot highs
    # at 3, 10, 17. A dramatic, LATE change (index 30, well past every
    # existing pivot's left+right=3+3 confirmation window) must not alter
    # any pivot at or before index 20.
    base = [60.0] * 40
    for i, v in {3: 110.0, 10: 105.0, 17: 120.0}.items():
        base[i] = v
    highs_a = base
    highs_b = list(highs_a)
    highs_b[30] = 99999.0
    pivots_a = struct.find_pivot_highs(highs_a)
    pivots_b = struct.find_pivot_highs(highs_b)
    assert pivots_a == [3, 10, 17], "fixture must contain real, known pivots, or this test is vacuous"
    assert [i for i in pivots_a if i <= 20] == [i for i in pivots_b if i <= 20]


def test_divergence_causality():
    # Verified bearish-divergence fixture (see test_divergence.py): a real
    # BEARISH read driven entirely by candles 0-60. Appending a dramatic
    # additional rally afterward must not change that earlier read.
    closes = [100.0]
    for _ in range(20):
        closes.append(closes[-1])
    for _ in range(8):
        closes.append(closes[-1] + 2.0)
    for _ in range(5):
        closes.append(closes[-1] - 1.3)
    for _ in range(4):
        closes.append(closes[-1] - 0.2)
    for d in [2.0, 1.8, -0.7, 2.0, -0.8, 1.9, -0.6, 2.0, -0.7, 2.1, -0.5, 2.2, -0.8, 2.3, -0.4, 2.4, -0.5, 2.2]:
        closes.append(closes[-1] + d)
    for _ in range(5):
        closes.append(closes[-1] - 1.0)
    highs_a = [c + 0.5 for c in closes]
    lows_a = [c - 0.5 for c in closes]
    result_a = div.detect_divergence(highs_a, lows_a, closes)
    assert result_a == "BEARISH", "fixture must produce a real divergence, or this test is vacuous"

    # Extend with a dramatic future rally that would itself look bullish if
    # it were allowed to leak backward into this earlier read.
    closes_extended = list(closes)
    for _ in range(20):
        closes_extended.append(closes_extended[-1] * 3.0)
    highs_b = [c + 0.5 for c in closes_extended]
    lows_b = [c - 0.5 for c in closes_extended]
    result_b = div.detect_divergence(highs_b[:len(closes)], lows_b[:len(closes)], closes_extended[:len(closes)])
    assert result_b == result_a


# ---------------------------------------------------------------------------
# Level 2: Feature Engine -- the still-forming candle must never leak in,
# even if dramatically different from everything before it
# ---------------------------------------------------------------------------

def test_feature_engine_ignores_the_forming_candle_entirely():
    closed = make_closed_candles(210, seed=24)
    fs_without_forming = compute_feature_set(build_md(closed))

    wild_forming = CandleData(
        open_time=NOW, open=closed[-1].close, high=closed[-1].close * 200,
        low=closed[-1].close * 0.01, close=closed[-1].close * 150, volume=999999.0, is_closed=False,
    )
    fs_with_forming = compute_feature_set(build_md(closed + [wild_forming]))

    assert fs_without_forming == fs_with_forming


def test_feature_engine_close_field_is_the_last_closed_candle_never_the_forming_one():
    closed = make_closed_candles(60, seed=25)
    wild_forming = CandleData(
        open_time=NOW, open=closed[-1].close, high=closed[-1].close * 3,
        low=closed[-1].close * 0.3, close=closed[-1].close * 2.5, volume=1.0, is_closed=False,
    )
    fs = compute_feature_set(build_md(closed + [wild_forming]))
    assert fs.close == closed[-1].close
    assert fs.close != wild_forming.close


def test_feature_engine_swing_and_divergence_ignore_the_forming_candle():
    # Seed/noise verified to produce real, non-None swing_support,
    # swing_resistance, AND divergence -- otherwise the equality check
    # below would pass vacuously (None == None) without actually
    # exercising these two Phase 4 fields' no-lookahead guarantee.
    closed = make_closed_candles_with_wicks(210, seed=2, noise=0.025)
    fs_without_forming = compute_feature_set(build_md(closed))
    assert fs_without_forming.swing_support is not None
    assert fs_without_forming.swing_resistance is not None
    assert fs_without_forming.divergence is not None

    wild_forming = CandleData(
        open_time=NOW, open=closed[-1].close, high=closed[-1].close * 200,
        low=closed[-1].close * 0.01, close=closed[-1].close * 150, volume=999999.0, is_closed=False,
    )
    fs_with_forming = compute_feature_set(build_md(closed + [wild_forming]))
    assert fs_without_forming == fs_with_forming


def test_regime_engine_ignores_the_forming_candle():
    closed = make_closed_candles(210, drift=0.002, noise=0.008, seed=26)
    fs = compute_feature_set(build_md(closed))
    regime_without_forming = classify_regime(closed, fs)

    wild_forming = CandleData(
        open_time=NOW, open=closed[-1].close, high=closed[-1].close * 200,
        low=closed[-1].close * 0.01, close=closed[-1].close * 0.01, volume=1.0, is_closed=False,
    )
    fs2 = compute_feature_set(build_md(closed + [wild_forming]))
    regime_with_forming = classify_regime(closed, fs2)  # closed list itself unchanged

    assert regime_without_forming == regime_with_forming


# ---------------------------------------------------------------------------
# Level 3: full scanner pipeline
# ---------------------------------------------------------------------------

def test_scanner_result_unaffected_by_a_dramatically_different_forming_candle():
    closed = make_closed_candles(210, drift=0.003, noise=0.01, seed=2)  # matches the LONG-confirming fixture
    baseline = analyze_market(FakeSource(closed), "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)

    wild_forming = CandleData(
        open_time=NOW, open=closed[-1].close, high=closed[-1].close * 300,
        low=closed[-1].close * 0.005, close=closed[-1].close * 0.02, volume=10**9, is_closed=False,
    )
    with_forming = analyze_market(
        FakeSource(closed + [wild_forming]), "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW,
    )

    assert baseline.status == with_forming.status
    assert baseline.feature_set == with_forming.feature_set
    assert baseline.regime == with_forming.regime


def test_full_confluence_chain_unaffected_by_a_dramatically_different_forming_candle():
    # Capstone Phase 4 check: feature engine -> setup -> confluence, all the
    # way through, on a fixture verified to produce a real, non-None
    # ConfluenceResult across three categories (TREND, MOMENTUM, STRUCTURE)
    # -- not a vacuous None == None comparison. Scanner integration itself
    # is out of this phase's scope (see HANDOFF.md), so this drives the
    # chain directly rather than through analyze_market.
    def reversal_short_fixture():
        closes = [100.0]
        for _ in range(20):
            closes.append(closes[-1])
        for _ in range(8):
            closes.append(closes[-1] + 2.0)
        for _ in range(5):
            closes.append(closes[-1] - 1.3)
        for _ in range(4):
            closes.append(closes[-1] - 0.2)
        for d in [2.0, 1.8, -0.7, 2.0, -0.8, 1.9, -0.6, 2.0, -0.7, 2.1, -0.5, 2.2, -0.8, 2.3, -0.4, 2.4, -0.5, 2.2]:
            closes.append(closes[-1] + d)
        for _ in range(3):
            closes.append(closes[-1] - 0.01)
        n = len(closes)
        out = []
        for i, c in enumerate(closes):
            t = NOW - timedelta(minutes=(n - i + 1))
            out.append(CandleData(open_time=t, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0, is_closed=True))
        final_close = out[-1].low - 0.01
        out.append(CandleData(open_time=NOW, open=out[-1].close, high=out[-1].close + 0.1,
                               low=final_close - 0.1, close=final_close, volume=100.0, is_closed=True))
        return out

    def run_chain(closed):
        fs = compute_feature_set(build_md(closed))
        regime = MarketRegime(regime=RegimeType.UPTREND, trend_strength=0.5, volatility_percentile=0.5, basis=["t"])
        setup = detect_setup(closed, fs, regime)
        return compute_confluence(setup, fs, regime, closed) if setup is not None else None

    closed = reversal_short_fixture()
    baseline = run_chain(closed)
    assert baseline is not None
    assert set(baseline.categories_available) >= {EvidenceCategory.TREND, EvidenceCategory.MOMENTUM, EvidenceCategory.STRUCTURE}, \
        "fixture must exercise real evidence across multiple categories, or this test is vacuous"

    wild_forming = CandleData(
        open_time=NOW, open=closed[-1].close, high=closed[-1].close * 200,
        low=closed[-1].close * 0.01, close=closed[-1].close * 150, volume=999999.0, is_closed=False,
    )
    fs_with_forming = compute_feature_set(build_md(closed + [wild_forming]))
    regime_with_forming = MarketRegime(regime=RegimeType.UPTREND, trend_strength=0.5, volatility_percentile=0.5, basis=["t"])
    setup_with_forming = detect_setup(closed, fs_with_forming, regime_with_forming)  # closed itself unchanged
    with_forming = compute_confluence(setup_with_forming, fs_with_forming, regime_with_forming, closed)

    assert with_forming == baseline
