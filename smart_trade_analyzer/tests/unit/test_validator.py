"""Tests for smart_trade_analyzer.data.validator.

Covers section 10.B (duplicate timestamps, unsorted candles -- the
sequence-level half of "invalid data"), 10.D (incomplete-candle sanity
check), and 10.E (freshness across 1m/5m/15m/1h/4h, verifying the threshold
scales with timeframe rather than using one universal cutoff).
"""
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import CandleData, DataQualityState, Timeframe
from smart_trade_analyzer.data.exceptions import DataValidationError
from smart_trade_analyzer.data.models import (
    ISSUE_DUPLICATE_TIMESTAMP,
    ISSUE_FUTURE_TIMESTAMP,
    ISSUE_GAP,
    ISSUE_UNEXPECTED_UNCLOSED_CANDLE,
    ISSUE_UNSORTED,
    timeframe_duration_seconds,
)
from smart_trade_analyzer.data.validator import check_freshness, validate_sequence

NOW = datetime(2026, 8, 22, 12, 0, 0)


def make_sequence(timeframe: Timeframe, count: int, ends_at=NOW, last_closed=False):
    """Build `count` candles spaced exactly one `timeframe` period apart,
    ending at `ends_at`. The last candle is marked closed unless
    `last_closed=False` (the realistic default -- the most recent candle in
    a live fetch is normally still forming)."""
    duration = timeframe_duration_seconds(timeframe)
    out = []
    for i in range(count):
        offset = (count - 1 - i) * duration
        t = ends_at - timedelta(seconds=offset)
        is_closed = True if i < count - 1 else last_closed
        out.append(CandleData(open_time=t, open=100.0, high=101.0, low=99.0,
                               close=100.5, volume=10.0, is_closed=is_closed))
    return out


# ---------------------------------------------------------------------------
# 10.A / clean sequence -> zero issues
# ---------------------------------------------------------------------------

def test_clean_sorted_sequence_has_no_issues():
    seq = make_sequence(Timeframe.M1, 20)
    result = validate_sequence(seq, Timeframe.M1, as_of=NOW)
    assert result.is_clean
    assert result.duplicate_count == 0
    assert result.gap_count == 0


# ---------------------------------------------------------------------------
# 10.B -- duplicate timestamps
# ---------------------------------------------------------------------------

def test_duplicate_timestamp_detected():
    seq = make_sequence(Timeframe.M1, 20)
    dup = list(seq)
    dup[5] = CandleData(open_time=dup[4].open_time, open=100, high=101, low=99,
                         close=100.5, volume=10, is_closed=True)
    result = validate_sequence(dup, Timeframe.M1, as_of=NOW)
    dup_issues = [i for i in result.issues if i.issue_type == ISSUE_DUPLICATE_TIMESTAMP]
    assert len(dup_issues) == 1
    assert result.duplicate_count == 1


def test_multiple_duplicates_all_counted():
    seq = make_sequence(Timeframe.M1, 20)
    dup = list(seq)
    dup[5] = CandleData(open_time=dup[4].open_time, open=100, high=101, low=99,
                         close=100.5, volume=10, is_closed=True)
    dup[10] = CandleData(open_time=dup[9].open_time, open=100, high=101, low=99,
                          close=100.5, volume=10, is_closed=True)
    result = validate_sequence(dup, Timeframe.M1, as_of=NOW)
    assert result.duplicate_count == 2


# ---------------------------------------------------------------------------
# 10.B -- unsorted candles
# ---------------------------------------------------------------------------

def test_unsorted_sequence_detected():
    seq = make_sequence(Timeframe.M1, 20)
    unsorted = list(seq)
    unsorted[3], unsorted[10] = unsorted[10], unsorted[3]
    result = validate_sequence(unsorted, Timeframe.M1, as_of=NOW)
    assert any(i.issue_type == ISSUE_UNSORTED for i in result.issues)


def test_validator_does_not_mutate_or_reorder_input():
    seq = make_sequence(Timeframe.M1, 20)
    unsorted = list(seq)
    unsorted[3], unsorted[10] = unsorted[10], unsorted[3]
    original_order = list(unsorted)
    validate_sequence(unsorted, Timeframe.M1, as_of=NOW)
    assert unsorted == original_order  # unchanged -- pure analysis, no side effects


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def test_gap_detected_when_candles_missing():
    seq = make_sequence(Timeframe.M1, 20)
    with_gap = seq[:10] + seq[15:]  # remove 5 candles from the middle
    result = validate_sequence(with_gap, Timeframe.M1, as_of=NOW)
    gap_issues = [i for i in result.issues if i.issue_type == ISSUE_GAP]
    assert len(gap_issues) == 1
    assert result.gap_count == 1
    assert result.missing_candles_estimate == 5


def test_no_gap_reported_for_normal_minor_timing_jitter():
    duration = timeframe_duration_seconds(Timeframe.M1)
    seq = [
        CandleData(open_time=NOW - timedelta(seconds=2 * duration), open=100, high=101, low=99,
                    close=100.5, volume=10, is_closed=True),
        # 1.2x the expected step -- within GAP_TOLERANCE_MULTIPLIER (1.5x), should NOT flag
        CandleData(open_time=NOW - timedelta(seconds=2 * duration - int(1.2 * duration)),
                    open=100, high=101, low=99, close=100.5, volume=10, is_closed=True),
    ]
    result = validate_sequence(seq, Timeframe.M1, as_of=NOW)
    assert not any(i.issue_type == ISSUE_GAP for i in result.issues)


# ---------------------------------------------------------------------------
# Future timestamps
# ---------------------------------------------------------------------------

def test_future_timestamp_detected():
    seq = make_sequence(Timeframe.M1, 20)
    future = list(seq)
    future[-1] = CandleData(open_time=NOW + timedelta(hours=2), open=100, high=101, low=99,
                             close=100.5, volume=10, is_closed=False)
    result = validate_sequence(future, Timeframe.M1, as_of=NOW)
    assert any(i.issue_type == ISSUE_FUTURE_TIMESTAMP for i in result.issues)


def test_timestamp_within_clock_skew_tolerance_not_flagged():
    seq = make_sequence(Timeframe.M1, 5, ends_at=NOW + timedelta(seconds=2))  # small skew
    result = validate_sequence(seq, Timeframe.M1, as_of=NOW)
    assert not any(i.issue_type == ISSUE_FUTURE_TIMESTAMP for i in result.issues)


# ---------------------------------------------------------------------------
# 10.D -- unexpected unclosed candle
# ---------------------------------------------------------------------------

def test_unclosed_candle_in_the_middle_is_flagged():
    seq = make_sequence(Timeframe.M1, 20)
    bad = list(seq)
    bad[5] = CandleData(open_time=bad[5].open_time, open=100, high=101, low=99,
                         close=100.5, volume=10, is_closed=False)
    result = validate_sequence(bad, Timeframe.M1, as_of=NOW)
    unexpected = [i for i in result.issues if i.issue_type == ISSUE_UNEXPECTED_UNCLOSED_CANDLE]
    assert len(unexpected) == 1
    assert unexpected[0].index == 5


def test_only_the_last_candle_unclosed_is_not_flagged():
    seq = make_sequence(Timeframe.M1, 20, last_closed=False)
    result = validate_sequence(seq, Timeframe.M1, as_of=NOW)
    assert not any(i.issue_type == ISSUE_UNEXPECTED_UNCLOSED_CANDLE for i in result.issues)


# ---------------------------------------------------------------------------
# Caller-error guard
# ---------------------------------------------------------------------------

def test_unknown_timeframe_raises_data_validation_error():
    with pytest.raises(DataValidationError):
        validate_sequence([], "not-a-real-timeframe", as_of=NOW)


def test_check_freshness_unknown_timeframe_raises():
    with pytest.raises(DataValidationError):
        check_freshness([], "not-a-real-timeframe", as_of=NOW)


# ---------------------------------------------------------------------------
# 10.E -- freshness across multiple timeframes, verifying the threshold
# scales with the timeframe rather than using one fixed universal cutoff.
# ---------------------------------------------------------------------------

REQUIRED_FRESHNESS_TIMEFRAMES = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4]


@pytest.mark.parametrize("timeframe", REQUIRED_FRESHNESS_TIMEFRAMES)
def test_freshness_valid_when_latest_closed_candle_just_ended(timeframe):
    seq = make_sequence(timeframe, 10, last_closed=True)
    result = check_freshness(seq, timeframe, as_of=NOW)
    assert result.state == DataQualityState.VALID


@pytest.mark.parametrize("timeframe", REQUIRED_FRESHNESS_TIMEFRAMES)
def test_freshness_degraded_at_roughly_three_periods_stale(timeframe):
    duration = timeframe_duration_seconds(timeframe)
    # Freshness age is measured from the candle's CLOSE time (open_time +
    # one duration), not its open_time -- so setting ends_at N periods back
    # yields an age of (N-1) periods. Using N=4.5 -> age=3.5 periods, which
    # sits comfortably inside (FRESH_MAX_PERIODS=2, DEGRADED_MAX_PERIODS=5)
    # for every timeframe under test.
    seq = make_sequence(timeframe, 10, ends_at=NOW - timedelta(seconds=4.5 * duration), last_closed=True)
    result = check_freshness(seq, timeframe, as_of=NOW)
    assert result.state == DataQualityState.DEGRADED


@pytest.mark.parametrize("timeframe", REQUIRED_FRESHNESS_TIMEFRAMES)
def test_freshness_unavailable_when_severely_stale(timeframe):
    duration = timeframe_duration_seconds(timeframe)
    seq = make_sequence(timeframe, 10, ends_at=NOW - timedelta(seconds=10 * duration), last_closed=True)
    result = check_freshness(seq, timeframe, as_of=NOW)
    assert result.state == DataQualityState.UNAVAILABLE


def test_freshness_thresholds_scale_with_timeframe_not_a_fixed_universal_value():
    # The absolute VALID cutoff for 1m must be far smaller than for 4h --
    # this is the crux of "avoid hardcoding one universal stale threshold."
    r_1m = check_freshness(make_sequence(Timeframe.M1, 5, last_closed=True), Timeframe.M1, as_of=NOW)
    r_4h = check_freshness(make_sequence(Timeframe.H4, 5, last_closed=True), Timeframe.H4, as_of=NOW)
    assert r_1m.max_age_for_valid_seconds < r_4h.max_age_for_valid_seconds
    assert r_1m.max_age_for_degraded_seconds < r_4h.max_age_for_degraded_seconds
    # And the ratio should be substantial -- not just marginally different.
    assert r_4h.max_age_for_valid_seconds > r_1m.max_age_for_valid_seconds * 50


def test_freshness_unavailable_when_no_closed_candle_at_all():
    seq = make_sequence(Timeframe.M1, 5, last_closed=False)  # only the unclosed current candle
    only_unclosed = [seq[-1]]
    result = check_freshness(only_unclosed, Timeframe.M1, as_of=NOW)
    assert result.state == DataQualityState.UNAVAILABLE
    assert result.latest_closed_candle_time is None
    assert result.age_seconds is None


def test_freshness_ignores_the_unclosed_current_candle_as_the_anchor():
    # A very-fresh unclosed candle must not mask a genuinely stale closed history.
    duration = timeframe_duration_seconds(Timeframe.M1)
    stale_closed = CandleData(
        open_time=NOW - timedelta(seconds=20 * duration), open=100, high=101, low=99,
        close=100.5, volume=10, is_closed=True,
    )
    fresh_unclosed = CandleData(
        open_time=NOW, open=100, high=101, low=99, close=100.5, volume=10, is_closed=False,
    )
    result = check_freshness([stale_closed, fresh_unclosed], Timeframe.M1, as_of=NOW)
    assert result.state == DataQualityState.UNAVAILABLE
