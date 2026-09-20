"""Tests for smart_trade_analyzer.data.bitget.BitgetMarketDataSource.

All network access is mocked via unittest.mock.patch on requests.get --
these tests never touch the live Bitget API (see section 11 of the Phase 2
brief: unit tests must not depend on live API availability). Response
shapes used here match the existing project's own verified Bitget usage
(see bitget.py's module docstring for exactly what was checked and where).

Covers section 10.F (API failure: timeout, HTTP error, malformed response,
empty response, invalid payload -- verifying no fake data is ever produced)
plus retry behavior, non-retryable errors, product-type fallback, and
correct timeframe-casing selection.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from smart_trade_analyzer.contracts import MarketType, Timeframe
from smart_trade_analyzer.data.bitget import (
    BitgetMarketDataSource,
    FUTURES_PRODUCT_TYPES,
    MIX_CANDLES_URL,
    MIX_TICKER_URL,
    SPOT_CANDLES_URL,
    SPOT_TICKER_URL,
    TIMEFRAME_GRANULARITY_FUTURES,
    TIMEFRAME_GRANULARITY_SPOT,
)
from smart_trade_analyzer.data.exceptions import (
    DataSourceTimeoutError,
    DataSourceUnavailableError,
    InvalidSymbolOrTimeframeError,
)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
PATCH_TARGET = "smart_trade_analyzer.data.bitget.requests.get"


def fake_response(status_code=200, json_data=None, raise_on_json=False):
    resp = MagicMock()
    resp.status_code = status_code
    if raise_on_json:
        resp.json.side_effect = ValueError("not JSON")
    else:
        resp.json.return_value = json_data
    return resp


def raw_row(minutes_ago, price=100.0, base=NOW):
    ts_ms = int((base - timedelta(minutes=minutes_ago)).timestamp() * 1000)
    return [str(ts_ms), str(price), str(price + 1), str(price - 1), str(price + 0.5), "10"]


def rows(n=20, base=NOW):
    return [raw_row(m, base=base) for m in range(n)]  # newest-first


def no_sleep(_seconds):
    pass


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_spot_candles_happy_path_uses_correct_url_and_ordering():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": rows(20)})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        result = source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    candles = result.candles
    assert len(candles) == 20
    assert len(result.issues) == 0
    assert candles[0].open_time < candles[-1].open_time  # oldest-first
    assert mock_get.call_args[0][0] == SPOT_CANDLES_URL


def test_no_authentication_header_is_sent():
    # These are public market-data endpoints in the existing project --
    # verified no Authorization/API-key header is ever attached.
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": rows(20)})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    call_kwargs = mock_get.call_args[1]
    assert "headers" not in call_kwargs


@pytest.mark.parametrize("timeframe,expected_spot,expected_futures", [
    (Timeframe.M1, "1min", "1m"),
    (Timeframe.H1, "1h", "1H"),
    (Timeframe.H4, "4h", "4H"),
    (Timeframe.D1, "1day", "1D"),
    (Timeframe.W1, "1week", "1W"),
])
def test_spot_vs_futures_use_different_granularity_casing(timeframe, expected_spot, expected_futures):
    assert TIMEFRAME_GRANULARITY_SPOT[timeframe] == expected_spot
    assert TIMEFRAME_GRANULARITY_FUTURES[timeframe] == expected_futures

    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": rows(20)})
        BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep).get_candles("BTCUSDT", timeframe, 20, as_of=NOW)
    assert mock_get.call_args[1]["params"]["granularity"] == expected_spot

    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": rows(20)})
        BitgetMarketDataSource(MarketType.FUTURES, sleep_fn=no_sleep).get_candles("BTCUSDT", timeframe, 20, as_of=NOW)
    assert mock_get.call_args[1]["params"]["granularity"] == expected_futures


# ---------------------------------------------------------------------------
# 10.F -- API failure: timeout
# ---------------------------------------------------------------------------

def test_timeout_is_retried_then_succeeds():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.side_effect = [
            requests.exceptions.Timeout("t1"),
            requests.exceptions.Timeout("t2"),
            fake_response(200, {"data": rows(20)}),
        ]
        sleeps = []
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=3, sleep_fn=lambda s: sleeps.append(s))
        result = source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    assert len(result.candles) == 20
    assert mock_get.call_count == 3
    assert len(sleeps) == 2


def test_timeout_exhausting_all_retries_raises_data_source_timeout_error():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout("always")
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=3, sleep_fn=no_sleep)
        with pytest.raises(DataSourceTimeoutError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    assert mock_get.call_count == 3


def test_no_fake_data_produced_on_timeout():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout("always")
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=1, sleep_fn=no_sleep)
        with pytest.raises(DataSourceTimeoutError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
        # get_ticker_price must return None, never fabricate a price
        assert source.get_ticker_price("BTCUSDT") is None


# ---------------------------------------------------------------------------
# 10.F -- API failure: HTTP error
# ---------------------------------------------------------------------------

def test_http_404_raises_immediately_without_retry():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(404, {})
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=3, sleep_fn=no_sleep)
        with pytest.raises(InvalidSymbolOrTimeframeError):
            source.get_candles("NOTASYMBOL", Timeframe.M1, 20, as_of=NOW)
    assert mock_get.call_count == 1  # non-retryable -- must not waste retries


def test_http_500_is_retried_then_raises_data_source_unavailable():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(500, {})
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=3, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    assert mock_get.call_count == 3  # 5xx is retryable


def test_http_429_is_retried():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.side_effect = [fake_response(429, {}), fake_response(200, {"data": rows(20)})]
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=3, sleep_fn=no_sleep)
        result = source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    assert len(result.candles) == 20
    assert mock_get.call_count == 2


def test_connection_error_is_retryable():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("refused"),
            fake_response(200, {"data": rows(20)}),
        ]
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=3, sleep_fn=no_sleep)
        result = source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)
    assert len(result.candles) == 20


# ---------------------------------------------------------------------------
# 10.F -- malformed / empty / invalid responses
# ---------------------------------------------------------------------------

def test_malformed_non_json_response_raises_data_source_unavailable():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, raise_on_json=True)
        source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=1, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)


def test_empty_data_list_raises_data_source_unavailable_no_fake_data():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": []})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)


def test_missing_data_key_raises_data_source_unavailable():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"code": "00000", "msg": "success"})  # no "data"
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)


def test_data_field_not_a_list_raises_data_source_unavailable():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": {"unexpected": "shape"}})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)


def test_response_not_a_json_object_raises_data_source_unavailable():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, ["not", "an", "object"])
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("BTCUSDT", Timeframe.M1, 20, as_of=NOW)


def test_majority_malformed_rows_returned_as_data_with_issues_not_raised():
    # Review fix, Issue 2: the adapter no longer decides "this is too
    # corrupted to use" -- it faithfully reports what it fetched and parsed,
    # even when malformed rows dominate. Grading DEGRADED vs. UNAVAILABLE is
    # the quality layer's job now (see test_quality.py for that half of the
    # behavior, exercised through fetch_canonical_market_data).
    good = rows(2)
    bad = [["x"], ["y"], ["z"]]  # 3 malformed rows, more than the 2 good ones
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": good + bad})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        result = source.get_candles("BTCUSDT", Timeframe.M1, 5, as_of=NOW)  # must NOT raise
    assert len(result.candles) == 2
    assert len(result.issues) == 3


def test_minority_malformed_rows_still_returns_the_good_ones_and_surfaces_the_issue():
    good = rows(10)
    bad = [["x"]]  # 1 malformed row, far fewer than the 10 good ones
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": good + bad})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        result = source.get_candles("BTCUSDT", Timeframe.M1, 11, as_of=NOW)
    assert len(result.candles) == 10
    assert len(result.issues) == 1  # visible, not silently dropped


def test_every_row_malformed_still_returned_as_data_not_raised():
    # Review fix, Issue 2: even when 100% of rows fail to normalize, as long
    # as the FETCH itself produced rows to attempt (raw_rows was non-empty),
    # the adapter returns candles=[] + issues=[...] rather than raising --
    # the quality layer's MIN_USABLE_CANDLES threshold (0 < 20) is what
    # classifies this as UNAVAILABLE, now with full reasons attached,
    # instead of a generic exception message losing that detail.
    all_bad = [["x"], ["y"], ["z"]]
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": all_bad})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        result = source.get_candles("BTCUSDT", Timeframe.M1, 3, as_of=NOW)  # must NOT raise
    assert result.candles == []
    assert len(result.issues) == 3


# ---------------------------------------------------------------------------
# Futures product-type fallback
# ---------------------------------------------------------------------------

def test_futures_tries_each_product_type_until_one_has_data():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.side_effect = [
            fake_response(200, {"data": []}),
            fake_response(200, {"data": []}),
            fake_response(200, {"data": rows(20)}),
        ]
        source = BitgetMarketDataSource(MarketType.FUTURES, sleep_fn=no_sleep)
        result = source.get_candles("ETHUSDT", Timeframe.H1, 20, as_of=NOW)
    assert len(result.candles) == 20
    assert mock_get.call_count == 3
    product_types_tried = [c[1]["params"]["productType"] for c in mock_get.call_args_list]
    assert product_types_tried == FUTURES_PRODUCT_TYPES[:3]
    assert all(c[0][0] == MIX_CANDLES_URL for c in mock_get.call_args_list)


def test_futures_raises_unavailable_when_every_product_type_is_empty():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": []})
        source = BitgetMarketDataSource(MarketType.FUTURES, sleep_fn=no_sleep)
        with pytest.raises(DataSourceUnavailableError):
            source.get_candles("NOSUCHCOIN", Timeframe.H1, 20, as_of=NOW)
    assert mock_get.call_count == len(FUTURES_PRODUCT_TYPES)


def test_futures_product_type_order_matches_existing_project():
    assert FUTURES_PRODUCT_TYPES == ["usdt-futures", "susdt-futures", "usdc-futures", "coin-futures"]


# ---------------------------------------------------------------------------
# Unsupported timeframe
# ---------------------------------------------------------------------------

def test_unsupported_timeframe_raises_invalid_symbol_or_timeframe_error():
    source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
    with pytest.raises(InvalidSymbolOrTimeframeError):
        source.get_candles("BTCUSDT", "not-a-real-timeframe", 20, as_of=NOW)


# ---------------------------------------------------------------------------
# get_ticker_price
# ---------------------------------------------------------------------------

def test_ticker_price_happy_path_spot():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": [{"lastPr": "65432.1"}]})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        tp = source.get_ticker_price("BTCUSDT")
    assert tp.price == 65432.1
    assert tp.source == "bitget_ticker"
    assert mock_get.call_args[0][0] == SPOT_TICKER_URL


def test_ticker_price_happy_path_futures():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": [{"lastPr": "3456.7"}]})
        source = BitgetMarketDataSource(MarketType.FUTURES, sleep_fn=no_sleep)
        tp = source.get_ticker_price("ETHUSDT")
    assert tp.price == 3456.7
    assert mock_get.call_args[0][0] == MIX_TICKER_URL


def test_ticker_price_never_raises_returns_none_on_any_failure():
    for side_effect in [
        requests.exceptions.Timeout("t"),
        requests.exceptions.ConnectionError("c"),
    ]:
        with patch(PATCH_TARGET) as mock_get:
            mock_get.side_effect = side_effect
            source = BitgetMarketDataSource(MarketType.SPOT, max_attempts=1, sleep_fn=no_sleep)
            assert source.get_ticker_price("BTCUSDT") is None  # never raises


def test_ticker_price_returns_none_on_empty_data():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": []})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        assert source.get_ticker_price("BTCUSDT") is None


def test_ticker_price_returns_none_on_unparseable_price():
    with patch(PATCH_TARGET) as mock_get:
        mock_get.return_value = fake_response(200, {"data": [{"lastPr": None, "last": None}]})
        source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
        assert source.get_ticker_price("BTCUSDT") is None


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

def test_max_attempts_must_be_at_least_one():
    with pytest.raises(ValueError):
        BitgetMarketDataSource(MarketType.SPOT, max_attempts=0)


def test_negative_limit_rejected():
    source = BitgetMarketDataSource(MarketType.SPOT, sleep_fn=no_sleep)
    with pytest.raises(ValueError):
        source.get_candles("BTCUSDT", Timeframe.M1, 0, as_of=NOW)
