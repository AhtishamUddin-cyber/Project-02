"""Sequence-level candle validation and freshness checking.

This module deliberately does NOT re-check per-candle OHLC sanity (low<=open
<=high, low<=close<=high, volume>=0) -- Phase 1's CandleData already
enforces every one of those at construction time, so any CandleData handed
to this module has already passed them (see normalizer.py for where a
violation gets caught and turned into a NormalizationIssue instead of a
ValidationIssue). Re-checking them here would be exactly the duplicated
validation logic the approved spec's code-quality rules forbid.

What this module DOES check are properties of the SEQUENCE that no single
CandleData's own validation could ever catch: duplicate timestamps, ordering,
future timestamps, gaps, an unexpected extra "unclosed" candle, and
freshness relative to the current time.

Pure analysis only: `validate_sequence` never modifies, reorders, or
deduplicates the candles it is given -- it only reports what it finds. See
this module's own tests for why (in short: silently "fixing" a sequence by
picking which duplicate to keep, or how to treat an out-of-order candle, is
a judgment call that belongs to a visible, later stage -- not something to
hide inside a function whose job is supposed to be detection).
"""
from datetime import timedelta
from typing import List, Optional

from ..contracts import CandleData, DataQualityState, Timeframe
from .exceptions import DataValidationError
from .models import (
    FreshnessResult,
    ValidationIssue,
    ValidationResult,
    ISSUE_DUPLICATE_TIMESTAMP,
    ISSUE_FUTURE_TIMESTAMP,
    ISSUE_GAP,
    ISSUE_UNEXPECTED_UNCLOSED_CANDLE,
    ISSUE_UNSORTED,
    timeframe_duration_seconds,
    utc_now,
)

# ---------------------------------------------------------------------------
# Centralized, documented thresholds -- see check_freshness()'s docstring for
# the exact rule these implement. All are provisional starting points
# (matching the same "configurable, explicitly provisional" discipline
# applied to the confluence scoring weights in the approved specification),
# not values validated against real outage data yet.
# ---------------------------------------------------------------------------

GAP_TOLERANCE_MULTIPLIER = 1.5
# A step between consecutive candles is only reported as a genuine gap once
# it exceeds 1.5x the expected duration -- tolerates minor exchange timing
# jitter without flagging every candle as a "gap."

FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 5
# Small allowance for clock skew between this system and the exchange.

FRESH_MAX_PERIODS = 2
# The latest CLOSED candle may be up to 2 full periods + GRACE_SECONDS old
# and still be considered VALID/fresh -- generous enough to tolerate normal
# refresh-interval and processing lag without being trivially always-true.

DEGRADED_MAX_PERIODS = 5
# Beyond 2 periods but within 5 periods + GRACE_SECONDS -> DEGRADED (usable,
# but visibly stale). Beyond 5 periods -> UNAVAILABLE (the feed has likely
# actually stopped updating, not just lagging normally).

GRACE_SECONDS = 30
# Flat, timeframe-independent buffer for network/processing latency, added
# on top of the period-based multiplier above.


def validate_sequence(
    candles: List[CandleData], timeframe: Timeframe, as_of=None,
) -> ValidationResult:
    """Analyze `candles` (assumed already normalized) for sequence-level
    anomalies. Does not raise for anomalies found WITHIN a legitimately-
    shaped sequence -- see this module's docstring. Raises DataValidationError
    only for a caller-error-shaped input (e.g. an unknown timeframe)."""
    if timeframe not in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30,
                          Timeframe.H1, Timeframe.H2, Timeframe.H4, Timeframe.D1, Timeframe.W1):
        raise DataValidationError(f"unknown Timeframe {timeframe!r} -- no known candle duration")
    if as_of is None:
        as_of = utc_now()

    duration = timeframe_duration_seconds(timeframe)
    issues: List[ValidationIssue] = []
    duplicate_count = 0
    gap_count = 0
    missing_estimate = 0

    seen_timestamps = {}
    for i, c in enumerate(candles):
        if c.open_time in seen_timestamps:
            duplicate_count += 1
            issues.append(ValidationIssue(
                ISSUE_DUPLICATE_TIMESTAMP, i,
                f"candle at index {i} shares open_time {c.open_time} with index "
                f"{seen_timestamps[c.open_time]}",
            ))
        else:
            seen_timestamps[c.open_time] = i

    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        delta = (cur.open_time - prev.open_time).total_seconds()
        if delta < 0:
            issues.append(ValidationIssue(
                ISSUE_UNSORTED, i,
                f"candle at index {i} (open_time={cur.open_time}) comes before "
                f"index {i - 1} (open_time={prev.open_time})",
            ))
        elif delta == 0:
            pass  # already reported as a duplicate above
        elif delta > duration * GAP_TOLERANCE_MULTIPLIER:
            gap_count += 1
            periods_missing = round(delta / duration) - 1
            missing_estimate += max(periods_missing, 0)
            issues.append(ValidationIssue(
                ISSUE_GAP, i,
                f"gap between index {i - 1} and {i}: {delta:.0f}s elapsed, expected "
                f"~{duration}s ({periods_missing} candle(s) likely missing)",
            ))

    for i, c in enumerate(candles):
        if c.open_time > as_of + timedelta(seconds=FUTURE_TIMESTAMP_TOLERANCE_SECONDS):
            issues.append(ValidationIssue(
                ISSUE_FUTURE_TIMESTAMP, i,
                f"candle at index {i} has open_time {c.open_time}, which is after "
                f"as_of={as_of}",
            ))

    for i, c in enumerate(candles[:-1]):  # every candle except the last
        if not c.is_closed:
            issues.append(ValidationIssue(
                ISSUE_UNEXPECTED_UNCLOSED_CANDLE, i,
                f"candle at index {i} is marked not-closed, but it is not the "
                f"last candle in the sequence -- only the most recent candle "
                f"should ever legitimately be still forming",
            ))

    return ValidationResult(
        issues=issues, duplicate_count=duplicate_count,
        gap_count=gap_count, missing_candles_estimate=missing_estimate,
    )


def check_freshness(
    candles: List[CandleData], timeframe: Timeframe, as_of=None,
) -> FreshnessResult:
    """Determine whether the most recent CLOSED candle is recent enough.

    Rule (see the module-level constants above for the exact numbers):
        age = as_of - (latest_closed_candle.open_time + one period)
        VALID     if age <= FRESH_MAX_PERIODS * period + GRACE_SECONDS
        DEGRADED  if age <= DEGRADED_MAX_PERIODS * period + GRACE_SECONDS
        UNAVAILABLE otherwise (or if there is no closed candle at all)

    Deliberately keyed off the LATEST CLOSED candle, not the sequence's
    absolute latest entry -- the most recent entry is very often the
    still-forming current candle (see CandleData.is_closed), which is always
    "new" almost by definition and would make staleness impossible to ever
    detect if used as the freshness anchor.
    """
    if timeframe not in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30,
                          Timeframe.H1, Timeframe.H2, Timeframe.H4, Timeframe.D1, Timeframe.W1):
        raise DataValidationError(f"unknown Timeframe {timeframe!r} -- no known candle duration")
    if as_of is None:
        as_of = utc_now()

    duration = timeframe_duration_seconds(timeframe)
    max_valid = FRESH_MAX_PERIODS * duration + GRACE_SECONDS
    max_degraded = DEGRADED_MAX_PERIODS * duration + GRACE_SECONDS

    closed = [c for c in candles if c.is_closed]
    if not closed:
        return FreshnessResult(
            state=DataQualityState.UNAVAILABLE,
            latest_closed_candle_time=None, age_seconds=None,
            max_age_for_valid_seconds=max_valid, max_age_for_degraded_seconds=max_degraded,
            detail="no closed candle available to evaluate freshness against",
        )

    latest_closed = max(closed, key=lambda c: c.open_time)
    close_time = latest_closed.open_time + timedelta(seconds=duration)
    age = (as_of - close_time).total_seconds()

    if age <= max_valid:
        state = DataQualityState.VALID
    elif age <= max_degraded:
        state = DataQualityState.DEGRADED
    else:
        state = DataQualityState.UNAVAILABLE

    return FreshnessResult(
        state=state,
        latest_closed_candle_time=latest_closed.open_time,
        age_seconds=age,
        max_age_for_valid_seconds=max_valid,
        max_age_for_degraded_seconds=max_degraded,
        detail=(
            f"latest closed candle ended {age:.0f}s ago "
            f"(valid<= {max_valid:.0f}s, degraded<= {max_degraded:.0f}s)"
        ),
    )
