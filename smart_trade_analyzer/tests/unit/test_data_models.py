"""Tests for smart_trade_analyzer.data.models -- the data-layer-internal
types (TickerPrice, NormalizationIssue/Result, ValidationIssue/Result,
FreshnessResult) and the shared timeframe-duration constant / utc_now helper.
"""
from datetime import datetime, timedelta, timezone

import pytest

from smart_trade_analyzer.contracts import DataQualityState, Timeframe
from smart_trade_analyzer.data.models import (
    TIMEFRAME_DURATION_SECONDS,
    FreshnessResult,
    NormalizationIssue,
    NormalizationResult,
    TickerPrice,
    ValidationIssue,
    ValidationResult,
    timeframe_duration_seconds,
    utc_now,
)


# ---------------------------------------------------------------------------
# TIMEFRAME_DURATION_SECONDS / timeframe_duration_seconds
# ---------------------------------------------------------------------------

def test_all_nine_phase1_timeframes_have_a_duration():
    for tf in Timeframe:
        assert tf in TIMEFRAME_DURATION_SECONDS
        assert timeframe_duration_seconds(tf) > 0


def test_timeframe_durations_are_correct_and_monotonically_increasing():
    assert timeframe_duration_seconds(Timeframe.M1) == 60
    assert timeframe_duration_seconds(Timeframe.M5) == 300
    assert timeframe_duration_seconds(Timeframe.M15) == 900
    assert timeframe_duration_seconds(Timeframe.M30) == 1800
    assert timeframe_duration_seconds(Timeframe.H1) == 3600
    assert timeframe_duration_seconds(Timeframe.H2) == 7200
    assert timeframe_duration_seconds(Timeframe.H4) == 14400
    assert timeframe_duration_seconds(Timeframe.D1) == 86400
    assert timeframe_duration_seconds(Timeframe.W1) == 604800

    ordered = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30,
               Timeframe.H1, Timeframe.H2, Timeframe.H4, Timeframe.D1, Timeframe.W1]
    durations = [timeframe_duration_seconds(tf) for tf in ordered]
    assert durations == sorted(durations)


# ---------------------------------------------------------------------------
# utc_now
# ---------------------------------------------------------------------------

def test_utc_now_is_naive_but_represents_utc():
    now = utc_now()
    assert now.tzinfo is None  # naive, per the documented convention
    real_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now - real_utc).total_seconds()) < 2  # sane, not offset by a timezone


# ---------------------------------------------------------------------------
# TickerPrice
# ---------------------------------------------------------------------------

def test_ticker_price_valid_construction():
    tp = TickerPrice(price=100.5, source="bitget_ticker", fetched_at=utc_now(),
                      quality=DataQualityState.VALID)
    assert tp.price == 100.5


def test_ticker_price_nonpositive_price_rejected():
    with pytest.raises(ValueError):
        TickerPrice(price=0.0, source="bitget_ticker", fetched_at=utc_now(),
                    quality=DataQualityState.VALID)


def test_ticker_price_empty_source_rejected():
    with pytest.raises(ValueError):
        TickerPrice(price=1.0, source="", fetched_at=utc_now(), quality=DataQualityState.VALID)


# ---------------------------------------------------------------------------
# NormalizationIssue / NormalizationResult
# ---------------------------------------------------------------------------

def test_normalization_result_attempted_count_sums_candles_and_issues():
    result = NormalizationResult(
        candles=[object(), object()],
        issues=[NormalizationIssue(index=2, raw=["bad"], reason="could not parse")],
    )
    assert result.attempted_count == 3


def test_normalization_result_defaults_to_no_issues():
    result = NormalizationResult(candles=[])
    assert result.issues == []
    assert result.attempted_count == 0


# ---------------------------------------------------------------------------
# ValidationIssue / ValidationResult
# ---------------------------------------------------------------------------

def test_validation_result_is_clean_true_when_no_issues():
    result = ValidationResult()
    assert result.is_clean is True
    assert result.duplicate_count == 0
    assert result.gap_count == 0


def test_validation_result_is_clean_false_when_issues_present():
    result = ValidationResult(issues=[ValidationIssue("DUPLICATE_TIMESTAMP", 3, "dup")])
    assert result.is_clean is False


# ---------------------------------------------------------------------------
# FreshnessResult
# ---------------------------------------------------------------------------

def test_freshness_result_valid_construction():
    now = utc_now()
    fr = FreshnessResult(
        state=DataQualityState.VALID, latest_closed_candle_time=now - timedelta(minutes=1),
        age_seconds=60.0, max_age_for_valid_seconds=150.0, max_age_for_degraded_seconds=330.0,
        detail="fresh",
    )
    assert fr.state == DataQualityState.VALID
    assert fr.age_seconds == 60.0


def test_freshness_result_allows_none_for_unavailable_case():
    fr = FreshnessResult(
        state=DataQualityState.UNAVAILABLE, latest_closed_candle_time=None, age_seconds=None,
        max_age_for_valid_seconds=150.0, max_age_for_degraded_seconds=330.0,
        detail="no closed candle available",
    )
    assert fr.latest_closed_candle_time is None
    assert fr.age_seconds is None
