"""Tests for smart_trade_analyzer.data.normalizer.

Covers section 10.A (valid data), 10.B (invalid data -- the OHLC/volume/
timestamp variants), 10.D (incomplete candle identification), and 10.G
(normalization against a real, representative Bitget response shape).
"""
from datetime import datetime, timedelta, timezone

import pytest

from smart_trade_analyzer.contracts import Timeframe
from smart_trade_analyzer.data.exceptions import DataNormalizationError
from smart_trade_analyzer.data.normalizer import normalize_candles, normalize_ticker_price

NOW = datetime(2026, 8, 22, 12, 0, 0)  # naive-but-UTC, matching utc_now()'s convention


def row(minutes_ago, o=100.0, h=101.0, l=99.0, c=100.5, v=10.0, base=NOW):
    """Build one raw Bitget-shaped row: [ts_ms_str, open, high, low, close, volume]."""
    ts_ms = int((base - timedelta(minutes=minutes_ago)).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return [str(ts_ms), str(o), str(h), str(l), str(c), str(v)]


# ---------------------------------------------------------------------------
# 10.A -- valid data
# ---------------------------------------------------------------------------

def test_valid_rows_all_normalize_successfully():
    raw = [row(m) for m in range(0, 10)]  # newest-first, as Bitget returns
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW)
    assert len(result.candles) == 10
    assert len(result.issues) == 0


def test_normalized_candles_are_ordered_oldest_first():
    raw = [row(m) for m in range(0, 10)]
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW)
    times = [c.open_time for c in result.candles]
    assert times == sorted(times)


def test_valid_volume_and_ohlc_preserved_exactly():
    raw = [row(0, o=100.0, h=105.0, l=95.0, c=102.0, v=1234.5)]
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW, reverse_input=False)
    c = result.candles[0]
    assert c.open == 100.0
    assert c.high == 105.0
    assert c.low == 95.0
    assert c.close == 102.0
    assert c.volume == 1234.5


def test_market_values_are_not_altered_during_normalization():
    # Normalization must not round, rescale, or otherwise change actual values.
    raw = [row(0, o=12345.6789, h=12399.9999, l=12300.0001, c=12350.5555, v=0.00012345)]
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW, reverse_input=False)
    c = result.candles[0]
    assert c.open == 12345.6789
    assert c.high == 12399.9999
    assert c.low == 12300.0001
    assert c.close == 12350.5555
    assert c.volume == 0.00012345


# ---------------------------------------------------------------------------
# 10.B -- invalid data: every OHLC/volume/timestamp violation, none of which
# should crash normalize_candles -- each becomes a NormalizationIssue.
# ---------------------------------------------------------------------------

def test_invalid_timestamp_reported_not_raised():
    ts_ms = int(NOW.replace(tzinfo=timezone.utc).timestamp() * 1000)
    bad_row = [str(ts_ms).replace("1", "x", 1), "100", "101", "99", "100.5", "10"]
    result = normalize_candles([bad_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0
    assert len(result.issues) == 1


def _bad_ohlc_row(o, h, l, c, v="10"):
    ts_ms = int(NOW.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return [str(ts_ms), str(o), str(h), str(l), str(c), str(v)]


def test_low_greater_than_open_reported_not_raised():
    bad = _bad_ohlc_row(o=100, h=105, l=101, c=102)  # low(101) > open(100)
    result = normalize_candles([bad], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1
    assert "low" in result.issues[0].reason.lower() or "open" in result.issues[0].reason.lower()


def test_low_greater_than_close_reported_not_raised():
    bad = _bad_ohlc_row(o=100, h=105, l=101, c=95)  # low(101) > close(95)
    result = normalize_candles([bad], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1


def test_high_less_than_open_reported_not_raised():
    bad = _bad_ohlc_row(o=110, h=105, l=95, c=100)  # high(105) < open(110)
    result = normalize_candles([bad], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1


def test_high_less_than_close_reported_not_raised():
    bad = _bad_ohlc_row(o=100, h=105, l=95, c=110)  # high(105) < close(110)
    result = normalize_candles([bad], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1


def test_high_less_than_low_reported_not_raised():
    bad = _bad_ohlc_row(o=100, h=90, l=95, c=92)  # high(90) < low(95)
    result = normalize_candles([bad], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1
    assert "high" in result.issues[0].reason.lower() and "low" in result.issues[0].reason.lower()


def test_negative_volume_reported_not_raised():
    bad = _bad_ohlc_row(o=100, h=101, l=99, c=100, v=-5)
    result = normalize_candles([bad], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1
    assert "volume" in result.issues[0].reason.lower()


def test_row_too_short_reported_not_raised():
    result = normalize_candles([["100", "101"]], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1
    assert "6" in result.issues[0].reason


def test_non_numeric_field_reported_not_raised():
    ts_ms = int(NOW.replace(tzinfo=timezone.utc).timestamp() * 1000)
    result = normalize_candles(
        [[str(ts_ms), "not-a-number", "101", "99", "100", "10"]],
        Timeframe.M1, as_of=NOW, reverse_input=False,
    )
    assert len(result.candles) == 0 and len(result.issues) == 1


def test_none_row_reported_not_raised():
    result = normalize_candles([None], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0 and len(result.issues) == 1


def test_a_mix_of_good_and_bad_rows_keeps_all_good_ones_and_reports_all_bad_ones():
    good = [row(m) for m in range(0, 5)]
    bad = [_bad_ohlc_row(o=100, h=90, l=95, c=92), ["short"]]
    result = normalize_candles(good + bad, Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 5
    assert len(result.issues) == 2
    assert result.attempted_count == 7


def test_duplicate_rows_both_normalize_successfully_deduplication_is_not_normalizers_job():
    ts_ms = int(NOW.replace(tzinfo=timezone.utc).timestamp() * 1000)
    same_row = [str(ts_ms), "100", "101", "99", "100.5", "10"]
    result = normalize_candles([same_row, same_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    # Normalizer parses both -- duplicate DETECTION is validator.py's job (a
    # sequence-level concern), not normalizer's (a per-row concern). See
    # test_validator.py for the corresponding duplicate-detection tests.
    assert len(result.candles) == 2
    assert len(result.issues) == 0


def test_raw_rows_not_a_list_raises_data_normalization_error():
    with pytest.raises(DataNormalizationError):
        normalize_candles("not a list", Timeframe.M1, as_of=NOW)


def test_raw_rows_none_raises_data_normalization_error():
    with pytest.raises(DataNormalizationError):
        normalize_candles(None, Timeframe.M1, as_of=NOW)


# ---------------------------------------------------------------------------
# 10.D -- incomplete / current candle identification
# ---------------------------------------------------------------------------

def test_most_recent_candle_is_marked_not_closed():
    raw = [row(m) for m in range(0, 5)]  # newest-first; index 0 = "0 minutes ago"
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW)
    # After reversal, the most recent candle (0 min ago) is LAST in the list.
    assert result.candles[-1].is_closed is False


def test_earlier_candles_are_marked_closed():
    raw = [row(m) for m in range(0, 5)]
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW)
    for c in result.candles[:-1]:
        assert c.is_closed is True


def test_incomplete_candle_is_not_silently_dropped_or_relabeled():
    raw = [row(m) for m in range(0, 3)]
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW)
    # It is still present in the output, just correctly flagged -- never
    # silently excluded and never silently presented as a completed candle.
    assert len(result.candles) == 3
    assert result.candles[-1].is_closed is False
    assert result.candles[-1].close is not None  # still fully populated


def test_a_candle_exactly_at_the_boundary_is_considered_closed():
    # open_time + duration == as_of exactly -> closed (the period has fully elapsed)
    ts_ms = int((NOW - timedelta(minutes=1)).replace(tzinfo=timezone.utc).timestamp() * 1000)
    raw_row = [str(ts_ms), "100", "101", "99", "100.5", "10"]
    result = normalize_candles([raw_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert result.candles[0].is_closed is True


# ---------------------------------------------------------------------------
# 10.G -- representative Bitget response shape
# ---------------------------------------------------------------------------

def test_realistic_bitget_spot_candle_payload_shape():
    # Field order and string-encoded numerics verified directly against the
    # existing project's own usage: c[0]=ts_ms, c[1]=open, c[2]=high,
    # c[3]=low, c[4]=close, c[5]=volume -- see bitget.py's module docstring.
    payload = {
        "code": "00000", "msg": "success",
        "data": [
            ["1735689600000", "42150.5", "42300.0", "42050.25", "42275.75", "153.442"],
            ["1735689660000", "42275.75", "42400.0", "42200.0", "42350.1", "98.117"],
            ["1735689720000", "42350.1", "42500.0", "42300.0", "42480.9", "210.884"],
        ],
    }
    result = normalize_candles(payload["data"], Timeframe.M1, as_of=NOW)
    assert len(result.candles) == 3
    assert len(result.issues) == 0
    assert result.candles[0].close == 42480.9  # oldest-first after reversal


def test_ticker_price_normalizes_prefers_lastpr_over_last():
    tp = normalize_ticker_price({"lastPr": "100.5", "last": "999.9"}, source="bitget_ticker", as_of=NOW)
    assert tp.price == 100.5


def test_ticker_price_falls_back_to_last_when_lastpr_missing():
    tp = normalize_ticker_price({"last": "88.8"}, source="bitget_ticker", as_of=NOW)
    assert tp.price == 88.8


def test_ticker_price_raises_normalization_error_when_unusable():
    with pytest.raises(DataNormalizationError):
        normalize_ticker_price({"lastPr": None, "last": None}, source="bitget_ticker", as_of=NOW)


def test_ticker_price_raises_on_non_dict_input():
    with pytest.raises(DataNormalizationError):
        normalize_ticker_price(["not", "a", "dict"], source="bitget_ticker", as_of=NOW)


# ---------------------------------------------------------------------------
# Review fix, Issue 1 -- non-finite numeric values (NaN, +Infinity,
# -Infinity) must be rejected at the normalization boundary, not silently
# become a CandleData. Verified empirically that Phase 1's CandleData
# OHLC checks alone do NOT reliably catch these -- volume=NaN, high=+inf,
# low=-inf, and volume=+inf all pass those checks uncaught (NaN/Infinity
# comparisons are always False, and "anything <= +inf" / "anything >= -inf"
# are trivially true). This module now checks math.isfinite() on every
# numeric field explicitly, before construction is even attempted.
# ---------------------------------------------------------------------------

def _row_with_field(index, value, base=NOW):
    ts_ms = int(base.replace(tzinfo=timezone.utc).timestamp() * 1000)
    r = [str(ts_ms), "100", "101", "99", "100.5", "10"]
    r[index] = str(value)
    return r


@pytest.mark.parametrize("field_index,field_name,value", [
    (1, "open", "nan"),
    (2, "high", "nan"),
    (3, "low", "nan"),
    (4, "close", "nan"),
    (5, "volume", "nan"),
    (1, "open", "inf"),
    (2, "high", "inf"),
    (3, "low", "-inf"),
    (4, "close", "inf"),
    (5, "volume", "inf"),
])
def test_non_finite_value_rejected_as_normalization_issue(field_index, field_name, value):
    bad_row = _row_with_field(field_index, value)
    result = normalize_candles([bad_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0
    assert len(result.issues) == 1
    assert field_name in result.issues[0].reason
    assert "not a finite number" in result.issues[0].reason


def test_non_finite_value_does_not_raise_an_uncaught_exception():
    # The whole point: normalize_candles itself must never raise for a
    # per-row non-finite value -- it must be reported as an issue, not crash.
    bad_row = _row_with_field(5, "nan")
    result = normalize_candles([bad_row], Timeframe.M1, as_of=NOW, reverse_input=False)  # must not raise
    assert result.candles == []


def test_a_mix_of_finite_and_non_finite_rows_keeps_the_finite_ones():
    good = [row(m) for m in range(0, 5)]
    bad = [_row_with_field(5, "nan"), _row_with_field(2, "inf")]
    result = normalize_candles(good + bad, Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 5
    assert len(result.issues) == 2


def test_overflowing_timestamp_reported_as_normalization_issue_not_raised():
    # Verified empirically this raises OverflowError/OSError, not just
    # ValueError -- both are now caught.
    huge_ts_row = ["99999999999999999999999999", "100", "101", "99", "100.5", "10"]
    result = normalize_candles([huge_ts_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0
    assert len(result.issues) == 1


def test_negative_overflowing_timestamp_reported_as_normalization_issue_not_raised():
    huge_negative_ts_row = ["-99999999999999999999", "100", "101", "99", "100.5", "10"]
    result = normalize_candles([huge_negative_ts_row], Timeframe.M1, as_of=NOW, reverse_input=False)
    assert len(result.candles) == 0
    assert len(result.issues) == 1


def test_valid_finite_data_still_normalizes_correctly_no_regression():
    raw = [row(m) for m in range(0, 10)]
    result = normalize_candles(raw, Timeframe.M1, as_of=NOW)
    assert len(result.candles) == 10
    assert len(result.issues) == 0


def test_ticker_price_nan_rejected():
    with pytest.raises(DataNormalizationError, match="not a finite number"):
        normalize_ticker_price({"lastPr": "nan"}, source="bitget_ticker", as_of=NOW)


def test_ticker_price_infinity_rejected():
    with pytest.raises(DataNormalizationError, match="not a finite number"):
        normalize_ticker_price({"lastPr": "inf"}, source="bitget_ticker", as_of=NOW)


def test_ticker_price_negative_infinity_rejected():
    with pytest.raises(DataNormalizationError, match="not a finite number"):
        normalize_ticker_price({"lastPr": "-inf"}, source="bitget_ticker", as_of=NOW)
