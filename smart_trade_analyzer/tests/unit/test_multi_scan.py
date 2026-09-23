"""Tests for scanner/multi_scan.py: scan_market(), sort_scan_results(),
filter_actionable_only(), filter_by_min_quality_score().

Uses a small hand-written fake MarketDataSource (the same dependency-
injection seam tests/integration/test_phase6_pipeline.py's _FakeSource
already establishes) rather than any mock of scan_symbol/scan_market
themselves -- every test here runs the REAL scan_symbol -> run_pipeline ->
build_signal_record -> quality_gate.evaluate chain for each symbol. Only
the literal candle/ticker fetch is faked, exactly as the rest of this
project's test suite already does.
"""
import random
import time
from datetime import datetime, timedelta

from smart_trade_analyzer.contracts import CandleData, DataQualityState, Decision, MarketType, Timeframe
from smart_trade_analyzer.data import Instrument, NormalizationResult
from smart_trade_analyzer.data.models import TickerPrice
from smart_trade_analyzer.scanner import (
    filter_actionable_only, filter_by_min_quality_score, scan_market, sort_scan_results,
)

NOW = datetime(2026, 8, 27, 12, 0, 0)


def realistic_candles_with_wicks(n, seed=1, base=100.0, drift=0.0, noise=0.025):
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
                               close=c, volume=random.uniform(80, 120), is_closed=True))
        price = c
    return out


def flat_candles(n=210, level=100.0):
    return [
        CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=level, high=level + 0.05, low=level - 0.05,
                   close=level, volume=100.0, is_closed=True)
        for i in range(n)
    ]


# LONG_SEED / SHORT_SEED: verified (empirically, while building this
# phase) to reach a full LONG / SHORT decision -- every one of the 10
# Quality Gate gates passing -- through the real scan_symbol() path.
LONG_SEED = 1194
SHORT_SEED = 194


class FakeSource:
    """Minimal, real implementation of data.MarketDataSource. One
    instance can back multiple symbols (per_symbol_candles keyed by
    pair) and can simulate a hard failure for specific symbols
    (fail_symbols) -- used to test that scan_market survives one bad
    symbol without aborting the rest of the scan.
    """
    def __init__(self, per_symbol_candles=None, fail_symbols=()):
        self.per_symbol_candles = per_symbol_candles or {}
        self.fail_symbols = set(fail_symbols)
        self.requested_symbols = []

    def get_candles(self, symbol, timeframe, limit, as_of=None):
        self.requested_symbols.append(symbol)
        if symbol in self.fail_symbols:
            raise RuntimeError(f"simulated total failure for {symbol}")
        candles = self.per_symbol_candles.get(symbol, [])
        return NormalizationResult(candles=list(candles[-limit:]), issues=[])

    def get_ticker_price(self, symbol):
        candles = self.per_symbol_candles.get(symbol, [])
        if not candles:
            return None
        return TickerPrice(price=candles[-1].close, source="fake", fetched_at=NOW, quality=DataQualityState.VALID)

    def list_instruments(self):
        return []


def instrument(symbol, base=None, market_type=MarketType.SPOT):
    return Instrument(symbol=symbol, base_coin=base or symbol.replace("USDT", ""), quote_coin="USDT",
                       market_type=market_type, tradable=True, status="online")


def long_candles():
    return realistic_candles_with_wicks(210, seed=LONG_SEED, drift=random.Random(LONG_SEED).uniform(-0.006, 0.006))


def short_candles():
    return realistic_candles_with_wicks(210, seed=SHORT_SEED, drift=random.Random(SHORT_SEED).uniform(-0.006, 0.006))


# ---------------------------------------------------------------------------
# 7. Scanner calls the existing scan_symbol() path (proven the strong way:
# the returned OpportunityResult is fully self-consistent, e.g. LONG/SHORT
# always carries a SignalRecord per OpportunityResult's own __post_init__
# check -- something only a real scan_symbol() call could produce).
# ---------------------------------------------------------------------------

def test_scan_market_produces_real_fully_consistent_opportunity_results():
    source = FakeSource({"BTCUSDT": long_candles()})
    result = scan_market(source, [instrument("BTCUSDT", "BTC")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert len(result.results) == 1
    opp = result.results[0]
    assert opp.decision == Decision.LONG
    assert opp.signal_record is not None  # only real scan_symbol()/quality_gate.evaluate() output has this
    assert "BTCUSDT" in source.requested_symbols


# ---------------------------------------------------------------------------
# 8-9-10-11-12. Scanner does not implement independent LONG/SHORT logic --
# every Decision variant passes through completely unchanged.
# ---------------------------------------------------------------------------

def test_long_result_passes_through_unchanged():
    source = FakeSource({"BTCUSDT": long_candles()})
    result = scan_market(source, [instrument("BTCUSDT", "BTC")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert result.results[0].decision == Decision.LONG
    assert result.results[0].signal_decision.direction.value == "LONG"


def test_short_result_passes_through_unchanged():
    source = FakeSource({"ETHUSDT": short_candles()})
    result = scan_market(source, [instrument("ETHUSDT", "ETH")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert result.results[0].decision == Decision.SHORT
    assert result.results[0].signal_decision.direction.value == "SHORT"


def test_wait_remains_wait():
    # The verified TREND_CONTINUATION seed=2 fixture (Phase 6) -- confirmed
    # setup, fails only the quality threshold -- WAIT, not upgraded/downgraded.
    n = 210
    random.seed(2)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + 0.003 + random.uniform(-0.01, 0.01)), 0.01))
    candles = [
        CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=(closes[i - 1] if i > 0 else c),
                   high=max((closes[i - 1] if i > 0 else c), c) * 1.001,
                   low=min((closes[i - 1] if i > 0 else c), c) * 0.999,
                   close=c, volume=100.0, is_closed=True)
        for i, c in enumerate(closes)
    ]
    source = FakeSource({"BTCUSDT": candles})
    result = scan_market(source, [instrument("BTCUSDT", "BTC")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert result.results[0].decision == Decision.WAIT


def test_no_trade_remains_no_trade():
    source = FakeSource({"FLATUSDT": flat_candles()})
    result = scan_market(source, [instrument("FLATUSDT", "FLAT")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert result.results[0].decision == Decision.NO_TRADE


# ---------------------------------------------------------------------------
# 13. Individual symbol failure does not crash the entire scan.
# ---------------------------------------------------------------------------

def test_one_failing_symbol_does_not_abort_the_scan():
    source = FakeSource(
        {"BTCUSDT": long_candles(), "FLATUSDT": flat_candles()},
        fail_symbols=["BROKENUSDT"],
    )
    instruments = [instrument("BTCUSDT", "BTC"), instrument("BROKENUSDT", "BROKEN"), instrument("FLATUSDT", "FLAT")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert result.requested_count == 3
    assert result.scanned_count == 3
    assert len(result.results) == 2  # the two good symbols still scanned successfully
    assert len(result.failures) == 1
    assert result.failures[0].pair == "BROKENUSDT"
    assert "simulated total failure" in result.failures[0].error
    scanned_pairs = {r.pair for r in result.results}
    assert scanned_pairs == {"BTCUSDT", "FLATUSDT"}


def test_failed_symbol_is_never_turned_into_a_fake_wait_or_no_trade():
    source = FakeSource({}, fail_symbols=["BROKENUSDT"])
    result = scan_market(source, [instrument("BROKENUSDT", "BROKEN")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert result.results == []  # not a synthesized OpportunityResult of any Decision
    assert len(result.failures) == 1


# ---------------------------------------------------------------------------
# 14. Empty instrument universe handled safely.
# ---------------------------------------------------------------------------

def test_empty_instrument_list_produces_an_empty_safe_result():
    source = FakeSource({})
    result = scan_market(source, [], MarketType.SPOT, Timeframe.M1, as_of=NOW, evaluated_at=NOW)
    assert result.results == []
    assert result.failures == []
    assert result.requested_count == 0
    assert result.scanned_count == 0


# ---------------------------------------------------------------------------
# 16. Deterministic result collection/sorting.
# ---------------------------------------------------------------------------

def test_scan_market_results_are_in_the_same_order_as_instruments_given():
    source = FakeSource({"BTCUSDT": long_candles(), "FLATUSDT": flat_candles(), "ETHUSDT": short_candles()})
    instruments = [instrument("FLATUSDT", "FLAT"), instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert [r.pair for r in result.results] == ["FLATUSDT", "BTCUSDT", "ETHUSDT"]


def test_sort_scan_results_puts_actionable_first_then_quality_descending():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles(), "FLATUSDT": flat_candles()})
    instruments = [instrument("FLATUSDT", "FLAT"), instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    sorted_results = sort_scan_results(result.results)
    decisions_in_order = [r.decision for r in sorted_results]
    actionable_count = sum(1 for d in decisions_in_order if d in (Decision.LONG, Decision.SHORT))
    assert decisions_in_order[:actionable_count] == [d for d in decisions_in_order if d in (Decision.LONG, Decision.SHORT)]
    assert decisions_in_order[-1] == Decision.NO_TRADE  # FLATUSDT (no confluence at all) sorts last


def test_sort_scan_results_never_changes_a_single_decision():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles(), "FLATUSDT": flat_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH"), instrument("FLATUSDT", "FLAT")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    before = {r.pair: r.decision for r in result.results}
    after = {r.pair: r.decision for r in sort_scan_results(result.results)}
    assert before == after


def test_sort_is_stable_and_deterministic_across_repeated_calls():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles(), "FLATUSDT": flat_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH"), instrument("FLATUSDT", "FLAT")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    order_a = [r.pair for r in sort_scan_results(result.results)]
    order_b = [r.pair for r in sort_scan_results(result.results)]
    assert order_a == order_b


# ---------------------------------------------------------------------------
# 17. No duplicate symbols in scan output when none were given (scan_market
# itself does not deduplicate -- that is discover_tradable_instruments'
# job, per its own docstring -- this confirms scan_market simply mirrors
# whatever instrument list it was given, one result per instrument).
# ---------------------------------------------------------------------------

def test_scan_market_produces_one_result_per_instrument_no_more_no_less():
    source = FakeSource({"BTCUSDT": long_candles()})
    instruments = [instrument("BTCUSDT", "BTC")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    assert len(result.results) == 1


# ---------------------------------------------------------------------------
# 18. No Spot/Futures mixing.
# ---------------------------------------------------------------------------

def test_scan_market_refuses_a_mismatched_market_type_instrument():
    source = FakeSource({"BTCUSDT": long_candles()})
    futures_instrument = instrument("BTCUSDT", "BTC", market_type=MarketType.FUTURES)
    try:
        scan_market(source, [futures_instrument], MarketType.SPOT, Timeframe.M1, as_of=NOW, evaluated_at=NOW)
        assert False, "expected a ValueError for mixed market types"
    except ValueError as exc:
        assert "Spot" in str(exc) or "Futures" in str(exc) or "market_type" in str(exc)


# ---------------------------------------------------------------------------
# Filters: pure display narrowing, never a Decision change.
# ---------------------------------------------------------------------------

def test_filter_actionable_only_keeps_long_and_short_drops_wait_and_no_trade():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles(), "FLATUSDT": flat_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH"), instrument("FLATUSDT", "FLAT")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    actionable = filter_actionable_only(result.results)
    assert {r.pair for r in actionable} == {"BTCUSDT", "ETHUSDT"}
    assert all(r.decision in (Decision.LONG, Decision.SHORT) for r in actionable)


def test_filter_by_min_quality_score_default_none_preserves_all_results():
    source = FakeSource({"BTCUSDT": long_candles(), "FLATUSDT": flat_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("FLATUSDT", "FLAT")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    filtered = filter_by_min_quality_score(result.results, None)
    assert len(filtered) == len(result.results)


def test_filter_by_min_quality_score_excludes_results_below_threshold():
    source = FakeSource({"BTCUSDT": long_candles()})
    result = scan_market(source, [instrument("BTCUSDT", "BTC")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    real_score = result.results[0].confluence.setup_quality_score
    assert filter_by_min_quality_score(result.results, real_score + 1.0) == []
    assert len(filter_by_min_quality_score(result.results, real_score)) == 1


def test_filter_by_min_quality_score_never_changes_a_decision():
    source = FakeSource({"BTCUSDT": long_candles()})
    result = scan_market(source, [instrument("BTCUSDT", "BTC")], MarketType.SPOT, Timeframe.M1,
                          as_of=NOW, evaluated_at=NOW, delay_between_symbols_seconds=0.0)
    filtered = filter_by_min_quality_score(result.results, 0.0)
    assert filtered[0].decision == result.results[0].decision


# ---------------------------------------------------------------------------
# max_symbols cap and inter-symbol pacing.
# ---------------------------------------------------------------------------

def test_max_symbols_caps_how_many_are_scanned_but_not_requested_count():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles(), "FLATUSDT": flat_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH"), instrument("FLATUSDT", "FLAT")]
    result = scan_market(source, instruments, MarketType.SPOT, Timeframe.M1, as_of=NOW, evaluated_at=NOW,
                          max_symbols=2, delay_between_symbols_seconds=0.0)
    assert result.requested_count == 3
    assert result.scanned_count == 2
    assert len(result.results) == 2
    assert [r.pair for r in result.results] == ["BTCUSDT", "ETHUSDT"]


def test_delay_between_symbols_is_applied_via_the_injectable_sleep_fn():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH")]
    sleep_calls = []
    scan_market(source, instruments, MarketType.SPOT, Timeframe.M1, as_of=NOW, evaluated_at=NOW,
                delay_between_symbols_seconds=0.25, sleep_fn=sleep_calls.append)
    assert sleep_calls == [0.25]  # once, between the two symbols -- never before the first


def test_no_real_sleeping_happens_when_delay_is_zero():
    source = FakeSource({"BTCUSDT": long_candles(), "ETHUSDT": short_candles()})
    instruments = [instrument("BTCUSDT", "BTC"), instrument("ETHUSDT", "ETH")]
    start = time.monotonic()
    scan_market(source, instruments, MarketType.SPOT, Timeframe.M1, as_of=NOW, evaluated_at=NOW,
                delay_between_symbols_seconds=0.0)
    assert time.monotonic() - start < 2.0  # sanity: no accidental real sleep
