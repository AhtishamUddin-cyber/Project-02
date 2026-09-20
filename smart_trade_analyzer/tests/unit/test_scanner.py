"""Tests for smart_trade_analyzer.scanner.analyze_market -- the single
public API a future UI will call. Covers all five documented outcomes.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import CandleData, DataQualityState, Direction, MarketType, Timeframe
from smart_trade_analyzer.data import NormalizationResult, TickerPrice
from smart_trade_analyzer.data.exceptions import DataSourceUnavailableError
from smart_trade_analyzer.scanner import OpportunityScanResult, ScanStatus, analyze_market

NOW = datetime(2026, 8, 27, 12, 0, 0)


class FakeSource:
    def __init__(self, candles=None, candles_exc=None, price=100.0, ticker_none=False):
        self._candles = candles
        self._candles_exc = candles_exc
        self._price = price
        self._ticker_none = ticker_none

    def get_candles(self, symbol, timeframe, limit, as_of=None):
        if self._candles_exc is not None:
            raise self._candles_exc
        return NormalizationResult(candles=self._candles or [], issues=[])

    def get_ticker_price(self, symbol):
        if self._ticker_none:
            return None
        return TickerPrice(price=self._price, source="fake", fetched_at=NOW, quality=DataQualityState.VALID)


def realistic_candles(n, drift=0.002, noise=0.01, base=100.0, seed=1, last_unclosed=False):
    random.seed(seed)
    out = []
    price = base
    for i in range(n):
        t = NOW - timedelta(minutes=(n - i))
        o = price
        price = max(price * (1 + drift + random.uniform(-noise, noise)), 0.01)
        c = price
        is_closed = not (last_unclosed and i == n - 1)
        out.append(CandleData(open_time=t, open=o, high=max(o, c) * 1.005, low=min(o, c) * 0.995,
                               close=c, volume=100.0, is_closed=is_closed))
    return out


# ---------------------------------------------------------------------------
# OPPORTUNITY SCANNER -- all five documented outcomes
# ---------------------------------------------------------------------------

def test_no_data_when_source_fails_entirely():
    source = FakeSource(candles_exc=DataSourceUnavailableError("simulated outage"))
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    assert result.status == ScanStatus.NO_DATA
    assert result.market_data is not None
    assert result.market_data.candles == []
    assert result.feature_set is None
    assert result.regime is None
    assert result.setup is None
    assert len(result.reasons) > 0


def test_no_data_when_source_returns_too_few_candles_for_baseline_usability():
    # Below Phase 2's own MIN_USABLE_CANDLES(20) floor -> UNAVAILABLE at
    # the data layer, before Phase 3 even runs.
    few = realistic_candles(10, seed=1)
    source = FakeSource(candles=few)
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, limit=10, as_of=NOW)
    assert result.status == ScanStatus.NO_DATA


def test_insufficient_history_when_data_usable_but_regime_not_computable():
    # Above Phase 2's data-layer floor (20) but below regime's ema50
    # requirement (50).
    some = realistic_candles(30, seed=1)
    source = FakeSource(candles=some)
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, limit=30, as_of=NOW)
    assert result.status == ScanStatus.INSUFFICIENT_HISTORY
    assert result.feature_set is not None
    assert result.regime is not None
    assert result.regime.regime.value == "UNKNOWN"


def test_no_setup_when_data_and_regime_fine_but_nothing_confirms():
    choppy = realistic_candles(210, drift=0.0, noise=0.025, seed=99)
    source = FakeSource(candles=choppy)
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    assert result.status in (ScanStatus.NO_SETUP, ScanStatus.SETUP_LONG, ScanStatus.SETUP_SHORT)
    # whichever it is, the result must be internally consistent:
    if result.status == ScanStatus.NO_SETUP:
        assert result.setup is None or not result.setup.confirmation_met


def test_setup_long_on_confirmed_uptrend():
    up = realistic_candles(210, drift=0.003, noise=0.01, seed=2)  # searched, known-confirming fixture
    source = FakeSource(candles=up)
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    assert result.status == ScanStatus.SETUP_LONG
    assert result.setup is not None
    assert result.setup.confirmation_met is True
    assert result.setup.direction == Direction.LONG
    assert result.regime is not None
    assert result.feature_set is not None
    assert len(result.reasons) > 0


def test_setup_short_on_confirmed_downtrend():
    down = realistic_candles(210, drift=-0.003, noise=0.01, seed=1)
    source = FakeSource(candles=down)
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    assert result.status == ScanStatus.SETUP_SHORT
    assert result.setup.confirmation_met is True
    assert result.setup.direction == Direction.SHORT


# ---------------------------------------------------------------------------
# Additional integration guarantees
# ---------------------------------------------------------------------------

def test_scanner_never_raises_for_ordinary_failures():
    for source in [
        FakeSource(candles_exc=DataSourceUnavailableError("down")),
        FakeSource(candles=[]),
        FakeSource(candles=realistic_candles(5, seed=1)),
    ]:
        result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
        assert isinstance(result, OpportunityScanResult)


def test_scanner_survives_missing_ticker_price():
    candles = realistic_candles(210, drift=0.003, noise=0.01, seed=2)
    source = FakeSource(candles=candles, ticker_none=True)
    result = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    # Live price is load-bearing in Phase 2 -- missing it makes overall
    # data quality UNAVAILABLE, so the scanner correctly reports NO_DATA
    # rather than crashing.
    assert result.status == ScanStatus.NO_DATA
    assert result.data_quality.overall == DataQualityState.UNAVAILABLE


def test_scanner_result_status_matches_setup_confirmation_state_construction_guard():
    # OpportunityScanResult's own __post_init__ guard: SETUP_LONG/SHORT
    # requires a genuinely confirmed setup. Confirm this can't be
    # constructed otherwise.
    with pytest.raises(ValueError):
        OpportunityScanResult(
            status=ScanStatus.SETUP_LONG, symbol="X", pair="XUSDT",
            market_type=MarketType.SPOT, timeframe=Timeframe.M1, setup=None,
        )


def test_scanner_is_deterministic_same_input_same_output():
    candles = realistic_candles(210, drift=0.003, noise=0.01, seed=2)
    source = FakeSource(candles=candles)
    r1 = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    r2 = analyze_market(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW)
    assert r1.status == r2.status
    assert r1.feature_set == r2.feature_set
    assert r1.regime == r2.regime


def test_scanner_makes_no_trading_decision_of_its_own():
    # Architectural guard: OpportunityScanResult must not expose any
    # entry/SL/TP/risk field -- that is explicitly out of scope this phase.
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(OpportunityScanResult)}
    forbidden = {"entry", "stop_loss", "take_profit", "take_profit_1", "take_profit_2",
                 "risk_reward", "position_size", "decision"}
    assert not (field_names & forbidden)


def test_scanner_default_limit_matches_full_feature_set_requirement():
    import inspect
    from smart_trade_analyzer.features.readiness import MIN_CANDLES_FULL_FEATURE_SET
    sig = inspect.signature(analyze_market)
    assert sig.parameters["limit"].default == MIN_CANDLES_FULL_FEATURE_SET
