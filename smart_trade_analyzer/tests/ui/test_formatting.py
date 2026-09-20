"""Unit tests for ui/formatting.py -- pure functions, no Streamlit."""
from datetime import datetime

from smart_trade_analyzer.ui.formatting import (
    format_price, format_price_range, format_quality_score, format_risk_reward, format_timestamp,
    title_case_label,
)


def test_format_price_none_is_none():
    assert format_price(None) is None


def test_format_price_scales_decimals_by_magnitude():
    assert format_price(105432.567) == "105,432.57"     # large value -> 2 decimals
    assert format_price(4.56789) == "4.5679"             # mid value -> 4 decimals
    assert format_price(0.05123456) == "0.051235"        # small value -> 6 decimals
    assert format_price(0.00001234) == "0.00001234"      # very small value -> 8 decimals, not "0.00"


def test_format_price_range_none_when_either_side_missing():
    assert format_price_range(None, 105.0) is None
    assert format_price_range(100.0, None) is None
    assert format_price_range(50.0, 55.0) == "50.0000 – 55.0000"


def test_format_quality_score_rounds_to_integer_string():
    assert format_quality_score(67.8) == "68"
    assert format_quality_score(None) is None


def test_format_risk_reward_shape():
    assert format_risk_reward(1.8) == "1 : 1.80"
    assert format_risk_reward(None) is None


def test_format_timestamp_includes_utc_label():
    assert format_timestamp(datetime(2026, 8, 27, 12, 0, 0)) == "2026-08-27 12:00:00 UTC"
    assert format_timestamp(None) is None


def test_title_case_label():
    assert title_case_label("TREND_CONTINUATION") == "Trend Continuation"
    assert title_case_label(None) is None
