"""Unit tests for live instrument discovery: BitgetMarketDataSource.list_instruments()
(data/bitget.py) and discover_tradable_instruments() (data/discovery.py).

Same mocking convention as tests/unit/test_bitget_adapter.py (patch
requests.get directly -- "unit tests must not depend on live API
availability", that file's own module docstring) applied to the two new
Bitget endpoints this phase adds: /api/v2/spot/public/symbols and
/api/v2/mix/market/contracts.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from smart_trade_analyzer.contracts import MarketType
from smart_trade_analyzer.data import (
    DataSourceUnavailableError, Instrument, discover_tradable_instruments,
)
from smart_trade_analyzer.data.bitget import (
    BitgetMarketDataSource, MIX_CONTRACTS_URL, SPOT_SYMBOLS_URL,
)

PATCH_TARGET = "smart_trade_analyzer.data.bitget.requests.get"


def fake_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def spot_source(**kwargs):
    return BitgetMarketDataSource(market_type=MarketType.SPOT, sleep_fn=lambda s: None, **kwargs)


def futures_source(**kwargs):
    return BitgetMarketDataSource(market_type=MarketType.FUTURES, sleep_fn=lambda s: None, **kwargs)


# ---------------------------------------------------------------------------
# 1. Bitget instrument parsing (spot + futures field shapes).
# ---------------------------------------------------------------------------

def test_spot_instrument_parsing_happy_path():
    data = {"data": [{"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"}]}
    with patch(PATCH_TARGET, return_value=fake_response(data)) as mock_get:
        instruments = spot_source().list_instruments()
    assert len(instruments) == 1
    inst = instruments[0]
    assert inst == Instrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT",
                               market_type=MarketType.SPOT, tradable=True, status="online")
    assert mock_get.call_args[0][0] == SPOT_SYMBOLS_URL


def test_futures_instrument_parsing_happy_path():
    data = {"data": [{"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "symbolStatus": "normal"}]}
    with patch(PATCH_TARGET, return_value=fake_response(data)) as mock_get:
        instruments = futures_source().list_instruments()
    assert len(instruments) == 1
    assert instruments[0] == Instrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT",
                                         market_type=MarketType.FUTURES, tradable=True, status="normal")
    assert mock_get.call_args[0][0] == MIX_CONTRACTS_URL
    assert mock_get.call_args[1]["params"] == {"productType": "usdt-futures"}


def test_malformed_rows_are_skipped_not_raised():
    data = {"data": [
        {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "MISSINGFIELDS"},                       # missing baseCoin/quoteCoin/status
        {"baseCoin": "ETH", "quoteCoin": "USDT", "status": "online"},  # missing symbol
        "not even a dict",
        123,
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        instruments = spot_source().list_instruments()
    assert len(instruments) == 1
    assert instruments[0].symbol == "BTCUSDT"


# ---------------------------------------------------------------------------
# 2. Online/tradable filtering.
# ---------------------------------------------------------------------------

def test_spot_tradable_flag_only_true_for_exactly_online():
    data = {"data": [
        {"symbol": "AUSDT", "baseCoin": "A", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "BUSDT", "baseCoin": "B", "quoteCoin": "USDT", "status": "offline"},
        {"symbol": "CUSDT", "baseCoin": "C", "quoteCoin": "USDT", "status": "halt"},
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        instruments = spot_source().list_instruments()
    tradable = {i.symbol: i.tradable for i in instruments}
    assert tradable == {"AUSDT": True, "BUSDT": False, "CUSDT": False}


def test_futures_tradable_flag_only_true_for_exactly_normal():
    data = {"data": [
        {"symbol": "AUSDT", "baseCoin": "A", "quoteCoin": "USDT", "symbolStatus": "normal"},
        {"symbol": "BUSDT", "baseCoin": "B", "quoteCoin": "USDT", "symbolStatus": "restrictedAPI"},
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        instruments = futures_source().list_instruments()
    tradable = {i.symbol: i.tradable for i in instruments}
    assert tradable == {"AUSDT": True, "BUSDT": False}


def test_discover_tradable_instruments_filters_out_non_tradable():
    data = {"data": [
        {"symbol": "AUSDT", "baseCoin": "A", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "BUSDT", "baseCoin": "B", "quoteCoin": "USDT", "status": "offline"},
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        result = discover_tradable_instruments(spot_source())
    assert [i.symbol for i in result] == ["AUSDT"]


# ---------------------------------------------------------------------------
# 3. Spot/Futures separation.
# ---------------------------------------------------------------------------

def test_spot_and_futures_are_fetched_from_different_endpoints_with_different_market_type():
    spot_data = {"data": [{"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"}]}
    futures_data = {"data": [{"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "symbolStatus": "normal"}]}
    with patch(PATCH_TARGET, return_value=fake_response(spot_data)):
        spot_instruments = spot_source().list_instruments()
    with patch(PATCH_TARGET, return_value=fake_response(futures_data)):
        futures_instruments = futures_source().list_instruments()
    assert spot_instruments[0].market_type == MarketType.SPOT
    assert futures_instruments[0].market_type == MarketType.FUTURES


# ---------------------------------------------------------------------------
# 14. Empty instrument universe handled safely.
# ---------------------------------------------------------------------------

def test_empty_instrument_list_returns_empty_not_an_error():
    with patch(PATCH_TARGET, return_value=fake_response({"data": []})):
        instruments = spot_source().list_instruments()
    assert instruments == []
    with patch(PATCH_TARGET, return_value=fake_response({"data": []})):
        assert discover_tradable_instruments(spot_source()) == []


# ---------------------------------------------------------------------------
# 15. Instrument API failure handled safely (raises, never fabricates).
# ---------------------------------------------------------------------------

def test_instrument_endpoint_failure_raises_not_fabricates():
    with patch(PATCH_TARGET, side_effect=requests.exceptions.ConnectionError("boom")):
        with pytest.raises(DataSourceUnavailableError):
            spot_source().list_instruments()


def test_instrument_endpoint_malformed_json_raises():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    with patch(PATCH_TARGET, return_value=resp):
        with pytest.raises(DataSourceUnavailableError):
            spot_source().list_instruments()


def test_discover_tradable_instruments_propagates_source_failure():
    with patch(PATCH_TARGET, side_effect=requests.exceptions.ConnectionError("boom")):
        with pytest.raises(DataSourceUnavailableError):
            discover_tradable_instruments(spot_source())


# ---------------------------------------------------------------------------
# 17. No duplicate symbols.
# ---------------------------------------------------------------------------

def test_discover_tradable_instruments_deduplicates_by_symbol():
    data = {"data": [
        {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"},
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        result = discover_tradable_instruments(spot_source())
    assert len(result) == 1


# ---------------------------------------------------------------------------
# 16. Deterministic result collection/sorting (for discovery specifically --
# scanner/multi_scan.py's own sort is tested separately).
# ---------------------------------------------------------------------------

def test_discover_tradable_instruments_sorted_alphabetically_by_symbol():
    data = {"data": [
        {"symbol": "ZUSDT", "baseCoin": "Z", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "AUSDT", "baseCoin": "A", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "MUSDT", "baseCoin": "M", "quoteCoin": "USDT", "status": "online"},
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        result = discover_tradable_instruments(spot_source())
    assert [i.symbol for i in result] == ["AUSDT", "MUSDT", "ZUSDT"]


# ---------------------------------------------------------------------------
# 5-6 (selectability, checked at the data layer): BTCUSDT and another real
# symbol both come through the same, uniform parsing path -- no special-
# casing of BTCUSDT anywhere.
# ---------------------------------------------------------------------------

def test_btcusdt_and_another_symbol_both_come_through_uniformly():
    data = {"data": [
        {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"},
        {"symbol": "ETHUSDT", "baseCoin": "ETH", "quoteCoin": "USDT", "status": "online"},
    ]}
    with patch(PATCH_TARGET, return_value=fake_response(data)):
        result = discover_tradable_instruments(spot_source())
    symbols = [i.symbol for i in result]
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
