"""Tests for smart_trade_analyzer.data.quality.

Covers section 10.C (VALID / DEGRADED / UNAVAILABLE classification,
including stale data and a missing required source) plus the
fetch_canonical_market_data orchestration function.

Uses a small in-memory FakeMarketDataSource (defined below) rather than HTTP
mocking -- quality.py depends only on the MarketDataSource interface, so its
tests should not need to know anything about Bitget. HTTP-level testing of
the real adapter lives in test_bitget_adapter.py.
"""
from datetime import datetime, timedelta
from typing import List, Optional

import pytest

from smart_trade_analyzer.contracts import CandleData, DataQualityState, MarketType, Timeframe
from smart_trade_analyzer.data.exceptions import DataSourceUnavailableError
from smart_trade_analyzer.data.models import NormalizationIssue, NormalizationResult, TickerPrice, timeframe_duration_seconds
from smart_trade_analyzer.data.quality import (
    MIN_CANDLES_FOR_DEGRADED,
    MIN_USABLE_CANDLES,
    REQUIRED_CANDLES_FOR_FULL,
    closed_candles_only,
    evaluate_candle_quality,
    evaluate_data_quality,
    fetch_canonical_market_data,
)
from smart_trade_analyzer.data.validator import check_freshness, validate_sequence

NOW = datetime(2026, 8, 22, 12, 0, 0)


def make_candles(timeframe: Timeframe, count: int, ends_at=NOW, last_closed=False):
    duration = timeframe_duration_seconds(timeframe)
    out = []
    for i in range(count):
        offset = (count - 1 - i) * duration
        t = ends_at - timedelta(seconds=offset)
        is_closed = True if i < count - 1 else last_closed
        out.append(CandleData(open_time=t, open=100.0, high=101.0, low=99.0,
                               close=100.5, volume=10.0, is_closed=is_closed))
    return out


def make_issues(n, reason="row has only 1 field(s), expected at least 6"):
    return [NormalizationIssue(index=i, raw=["x"], reason=reason) for i in range(n)]


class FakeMarketDataSource:
    """A minimal, in-memory MarketDataSource test double.

    get_candles() returns a NormalizationResult (matching the real
    MarketDataSource interface, review fix Issue 2) -- pass
    `normalization_issues` to simulate rows that failed to parse.
    """

    def __init__(self, candles=None, candles_exc=None, ticker=None, ticker_exc=None,
                 normalization_issues=None):
        self._candles = candles
        self._candles_exc = candles_exc
        self._ticker = ticker
        self._ticker_exc = ticker_exc
        self._normalization_issues = normalization_issues or []

    def get_candles(self, symbol, timeframe, limit, as_of=None):
        if self._candles_exc is not None:
            raise self._candles_exc
        return NormalizationResult(candles=self._candles or [], issues=self._normalization_issues)

    def get_ticker_price(self, symbol):
        if self._ticker_exc is not None:
            raise self._ticker_exc
        return self._ticker


def default_ticker():
    return TickerPrice(price=100.0, source="bitget_ticker", fetched_at=NOW, quality=DataQualityState.VALID)


# ---------------------------------------------------------------------------
# 10.C -- classification
# ---------------------------------------------------------------------------

def test_full_valid_dataset_classified_valid():
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    assert dq.overall == DataQualityState.VALID
    assert dq.candle_count == REQUIRED_CANDLES_FOR_FULL


def test_recoverable_anomaly_classified_degraded():
    # Below the full-feature-set requirement but above the degraded floor.
    count = MIN_CANDLES_FOR_DEGRADED + 5
    candles = make_candles(Timeframe.M1, count, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    assert dq.overall == DataQualityState.DEGRADED


def test_duplicate_timestamps_downgrade_an_otherwise_full_dataset_to_degraded():
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    dup = list(candles)
    dup[10] = CandleData(open_time=dup[9].open_time, open=100, high=101, low=99,
                          close=100.5, volume=10, is_closed=True)
    validation = validate_sequence(dup, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(dup, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(dup, validation, freshness, default_ticker())
    assert dq.overall == DataQualityState.DEGRADED


def test_no_usable_data_classified_unavailable():
    candles = make_candles(Timeframe.M1, MIN_USABLE_CANDLES - 1, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    assert dq.overall == DataQualityState.UNAVAILABLE


def test_empty_candles_classified_unavailable():
    validation = validate_sequence([], Timeframe.M1, as_of=NOW)
    freshness = check_freshness([], Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality([], validation, freshness, default_ticker())
    assert dq.overall == DataQualityState.UNAVAILABLE
    assert dq.candle_count == 0


def test_stale_data_classified_degraded_at_moderate_staleness():
    duration = timeframe_duration_seconds(Timeframe.M1)
    # See test_validator.py's identical fix for why 4.5 periods back (not 3)
    # is used to land in the DEGRADED band: age is measured from close time
    # (open_time + 1 duration), so ends_at=N periods back yields age=(N-1) periods.
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL,
                            ends_at=NOW - timedelta(seconds=4.5 * duration), last_closed=True)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    assert dq.overall == DataQualityState.DEGRADED


def test_severely_stale_data_classified_unavailable_even_with_full_candle_count():
    duration = timeframe_duration_seconds(Timeframe.M1)
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL,
                            ends_at=NOW - timedelta(seconds=20 * duration), last_closed=True)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    # A full candle count must NOT be able to override a severely stale
    # freshness read -- staleness can only ever pull the state DOWN.
    assert dq.overall == DataQualityState.UNAVAILABLE


def test_missing_required_price_source_classified_unavailable():
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, ticker=None)  # price unavailable
    assert dq.overall == DataQualityState.UNAVAILABLE
    assert "live_price" in dq.excluded_sources


def test_excluded_sources_only_ever_contains_genuinely_unavailable_sources():
    # This is enforced by Phase 1's own DataQuality.__post_init__ -- if this
    # module ever tried to exclude a source that wasn't UNAVAILABLE, Phase 1's
    # frozen contract itself would reject the construction.
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    for source in dq.excluded_sources:
        assert dq.per_source[source] == DataQualityState.UNAVAILABLE


def test_quality_reasons_are_never_empty():
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker())
    assert len(dq.reasons) > 0  # even a VALID result explains itself


# ---------------------------------------------------------------------------
# fetch_canonical_market_data -- the full orchestration
# ---------------------------------------------------------------------------

def test_orchestration_happy_path_produces_valid_market_data():
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=default_ticker())
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, as_of=NOW,
    )
    assert dq.overall == DataQualityState.VALID
    assert md.symbol == "BTC"
    assert md.live_price == 100.0
    assert len(md.candles) == REQUIRED_CANDLES_FOR_FULL


def test_orchestration_never_raises_on_total_source_failure():
    source = FakeMarketDataSource(candles_exc=DataSourceUnavailableError("simulated outage"))
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, 200, as_of=NOW,
    )
    assert dq.overall == DataQualityState.UNAVAILABLE
    assert md.candles == []
    assert md.live_price is None
    assert "simulated outage" in dq.reasons[0]


def test_orchestration_never_fabricates_candles_on_failure():
    source = FakeMarketDataSource(candles_exc=DataSourceUnavailableError("down"))
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, 200, as_of=NOW,
    )
    assert md.candles == []  # never a fabricated placeholder list


def test_orchestration_survives_ticker_failure_and_marks_price_unavailable():
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=None)  # ticker genuinely unavailable
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, as_of=NOW,
    )
    assert md.live_price is None
    assert md.price_quality == DataQualityState.UNAVAILABLE
    assert dq.overall == DataQualityState.UNAVAILABLE  # price is load-bearing


def test_orchestration_survives_ticker_raising_unexpectedly():
    # Defense in depth: even if a source implementation doesn't honor the
    # documented "get_ticker_price never raises" contract, orchestration
    # must not crash.
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker_exc=RuntimeError("unexpected"))
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, as_of=NOW,
    )
    assert md.live_price is None  # degraded gracefully, did not raise


def test_orchestration_does_not_decide_a_trading_direction():
    # Architectural guard: the returned objects must not expose or imply any
    # LONG/SHORT/WAIT/NO_TRADE-shaped field anywhere.
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=default_ticker())
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, as_of=NOW,
    )
    import dataclasses
    md_fields = {f.name for f in dataclasses.fields(md)}
    dq_fields = {f.name for f in dataclasses.fields(dq)}
    forbidden = {"decision", "direction", "signal", "long", "short"}
    assert not (md_fields & forbidden)
    assert not (dq_fields & forbidden)


# ---------------------------------------------------------------------------
# closed_candles_only
# ---------------------------------------------------------------------------

def test_closed_candles_only_excludes_the_forming_candle():
    candles = make_candles(Timeframe.M1, 10, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=default_ticker())
    md, _ = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, 10, as_of=NOW,
    )
    closed = closed_candles_only(md)
    assert len(closed) == 9
    assert all(c.is_closed for c in closed)


def test_closed_candles_only_does_not_modify_the_original_market_data():
    candles = make_candles(Timeframe.M1, 10, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=default_ticker())
    md, _ = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, 10, as_of=NOW,
    )
    closed_candles_only(md)
    assert len(md.candles) == 10  # unchanged -- Phase 1's MarketData is untouched


# ---------------------------------------------------------------------------
# Review fix, Issue 2 -- normalization issues must not disappear before
# reaching DataQuality. Requirements A-F from the review, in order.
# (G -- retry/API behavior -- is re-verified in test_bitget_adapter.py.
#  H -- Phase 1 tests still pass -- is verified at the full-suite level.)
# ---------------------------------------------------------------------------

def _dq_for(candle_count, issue_count, ticker=default_ticker()):
    """Helper: build a DataQuality directly through evaluate_data_quality
    for a given number of good candles vs. normalization issues, holding
    everything else (freshness, validation) at a clean baseline."""
    candles = make_candles(Timeframe.M1, candle_count, last_closed=False) if candle_count else []
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    issues = make_issues(issue_count)
    return evaluate_data_quality(candles, validation, freshness, ticker, normalization_issues=issues)


# --- A. Non-finite values are rejected (integration-level: through the
# full quality pipeline, not just normalizer.py in isolation -- see
# test_normalizer.py for the exhaustive per-field unit tests) -----------

def test_a_non_finite_row_becomes_a_normalization_issue_visible_in_quality():
    from smart_trade_analyzer.data.normalizer import normalize_candles
    ts_ms = int(NOW.timestamp() * 1000)
    good_rows = [[str(ts_ms - i * 60_000), "100", "101", "99", "100.5", "10"] for i in range(60)]
    nan_row = [str(ts_ms + 60_000), "100", "101", "99", "nan", "10"]  # close=NaN
    result = normalize_candles(good_rows + [nan_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 60
    assert len(result.issues) == 1

    validation = validate_sequence(result.candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(result.candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(result.candles, validation, freshness, default_ticker(),
                                normalization_issues=result.issues)
    assert any("not a finite number" in r or "1 of 61" in r for r in dq.reasons)


# --- B. A small number of malformed rows is explicitly degraded/accounted for --

def test_b_small_number_of_malformed_rows_yields_degraded_not_valid_not_unavailable():
    dq = _dq_for(candle_count=100, issue_count=2)
    assert dq.overall == DataQualityState.DEGRADED


def test_b_a_single_malformed_row_among_many_good_ones_is_degraded():
    # Explicit regression test for requirement 2: "Do NOT automatically mark
    # the entire dataset UNAVAILABLE because one row is malformed."
    dq = _dq_for(candle_count=199, issue_count=1)
    assert dq.overall == DataQualityState.DEGRADED
    assert dq.overall != DataQualityState.UNAVAILABLE


# --- C. Malformed rows dominating the dataset is unavailable -----------

def test_c_malformed_rows_dominating_yields_unavailable():
    dq = _dq_for(candle_count=2, issue_count=50)
    assert dq.overall == DataQualityState.UNAVAILABLE


def test_c_issues_exactly_equal_to_candles_yields_unavailable():
    # Boundary case: issue_count == candle_count (tie) is treated as
    # "meets or exceeds" -> UNAVAILABLE, not a borderline DEGRADED.
    dq = _dq_for(candle_count=25, issue_count=25)
    assert dq.overall == DataQualityState.UNAVAILABLE


def test_c_all_rows_malformed_and_zero_candles_is_unavailable():
    dq = _dq_for(candle_count=0, issue_count=30)
    assert dq.overall == DataQualityState.UNAVAILABLE
    assert dq.candle_count == 0


# --- D. DataQuality.reasons contains meaningful normalization information --

def test_d_reasons_contains_the_exact_issue_count():
    dq = _dq_for(candle_count=100, issue_count=7)
    assert any("7 of 107" in r for r in dq.reasons)


def test_d_reasons_contains_actual_failure_text_not_just_a_count():
    candles = make_candles(Timeframe.M1, 50, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    issues = [NormalizationIssue(index=0, raw=["x"], reason="volume is not a finite number: nan")]
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker(), normalization_issues=issues)
    assert any("volume is not a finite number" in r for r in dq.reasons)


def test_d_reasons_summarizes_rather_than_listing_hundreds_of_issues():
    dq = _dq_for(candle_count=2, issue_count=200)
    # Must mention the total count and stay bounded -- not one line per issue.
    matching = [r for r in dq.reasons if "200" in r or "and " in r and "more" in r]
    assert matching
    assert all(len(r) < 2000 for r in dq.reasons)  # sanity bound, not a wall of text


def test_d_zero_issues_produces_no_normalization_reason_text():
    dq = _dq_for(candle_count=100, issue_count=0)
    assert not any("failed normalization" in r for r in dq.reasons)


# --- E. A completely clean dataset remains VALID ------------------------

def test_e_clean_dataset_with_explicit_empty_issue_list_is_valid():
    dq = _dq_for(candle_count=REQUIRED_CANDLES_FOR_FULL, issue_count=0)
    assert dq.overall == DataQualityState.VALID


def test_e_clean_dataset_with_issues_omitted_entirely_is_still_valid():
    # normalization_issues defaults to None -- must behave identically to [].
    candles = make_candles(Timeframe.M1, REQUIRED_CANDLES_FOR_FULL, last_closed=False)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    state, reasons = evaluate_candle_quality(candles, validation, freshness)  # no 4th arg at all
    assert state == DataQualityState.VALID


# --- F. Existing stale/gap/duplicate/future-timestamp behavior still works,
# now in combination with normalization issues (not just in isolation) ---

def test_f_stale_data_plus_minor_normalization_issues_is_still_correctly_unavailable():
    # Freshness UNAVAILABLE must still override everything, even a small,
    # otherwise-recoverable number of normalization issues.
    duration = timeframe_duration_seconds(Timeframe.M1)
    candles = make_candles(Timeframe.M1, 100, ends_at=NOW - timedelta(seconds=20 * duration), last_closed=True)
    validation = validate_sequence(candles, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(candles, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(candles, validation, freshness, default_ticker(),
                                normalization_issues=make_issues(2))
    assert dq.overall == DataQualityState.UNAVAILABLE


def test_f_duplicate_timestamps_plus_normalization_issues_both_surface_in_reasons():
    candles = make_candles(Timeframe.M1, 100, last_closed=False)
    dup = list(candles)
    dup[10] = CandleData(open_time=dup[9].open_time, open=100, high=101, low=99,
                          close=100.5, volume=10, is_closed=True)
    validation = validate_sequence(dup, Timeframe.M1, as_of=NOW)
    freshness = check_freshness(dup, Timeframe.M1, as_of=NOW)
    dq = evaluate_data_quality(dup, validation, freshness, default_ticker(),
                                normalization_issues=make_issues(2))
    assert dq.overall == DataQualityState.DEGRADED
    assert any("duplicate" in r.lower() for r in dq.reasons)
    assert any("normalization" in r.lower() for r in dq.reasons)


def test_f_end_to_end_orchestration_with_minority_issues_matches_direct_evaluation():
    # Confirms fetch_canonical_market_data (the full path) and
    # evaluate_data_quality (the direct path) agree.
    candles = make_candles(Timeframe.M1, 100, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=default_ticker(),
                                   normalization_issues=make_issues(3))
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, 103, as_of=NOW,
    )
    assert dq.overall == DataQualityState.DEGRADED
    assert any("3 of 103" in r for r in dq.reasons)
    assert len(md.candles) == 100  # MarketData carries only successfully-parsed candles


def test_f_end_to_end_orchestration_with_dominant_issues_is_unavailable():
    candles = make_candles(Timeframe.M1, 3, last_closed=False)
    source = FakeMarketDataSource(candles=candles, ticker=default_ticker(),
                                   normalization_issues=make_issues(20))
    md, dq = fetch_canonical_market_data(
        source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, 23, as_of=NOW,
    )
    assert dq.overall == DataQualityState.UNAVAILABLE
