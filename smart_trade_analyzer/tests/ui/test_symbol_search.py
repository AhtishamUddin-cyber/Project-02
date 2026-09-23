from smart_trade_analyzer.contracts import MarketType
from smart_trade_analyzer.data import Instrument
from smart_trade_analyzer.ui.symbol_search import default_symbol_index, search_instruments


def _instruments():
    return [
        Instrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT", market_type=MarketType.SPOT,
                   tradable=True, status="online"),
        Instrument(symbol="ETHUSDT", base_coin="ETH", quote_coin="USDT", market_type=MarketType.SPOT,
                   tradable=True, status="online"),
        Instrument(symbol="SOLUSDT", base_coin="SOL", quote_coin="USDT", market_type=MarketType.SPOT,
                   tradable=True, status="online"),
        Instrument(symbol="DOGEUSDT", base_coin="DOGE", quote_coin="USDT", market_type=MarketType.SPOT,
                   tradable=True, status="online"),
    ]


def test_search_by_base_coin_prefix():
    result = search_instruments(_instruments(), "BTC")
    assert [i.symbol for i in result] == ["BTCUSDT"]


def test_search_is_case_insensitive():
    result = search_instruments(_instruments(), "eth")
    assert [i.symbol for i in result] == ["ETHUSDT"]


def test_search_matches_full_symbol_too():
    result = search_instruments(_instruments(), "SOLUSDT")
    assert [i.symbol for i in result] == ["SOLUSDT"]


def test_empty_query_returns_everything():
    result = search_instruments(_instruments(), "")
    assert len(result) == 4
    result2 = search_instruments(_instruments(), "   ")
    assert len(result2) == 4


def test_no_match_returns_empty_not_a_fallback():
    result = search_instruments(_instruments(), "NOPE")
    assert result == []


def test_search_respects_limit():
    result = search_instruments(_instruments(), "USDT", limit=2)
    assert len(result) == 2


def test_default_symbol_index_finds_btcusdt():
    instruments = _instruments()
    assert instruments[default_symbol_index(instruments)].symbol == "BTCUSDT"


def test_default_symbol_index_falls_back_to_zero_when_default_absent():
    instruments = [i for i in _instruments() if i.symbol != "BTCUSDT"]
    assert default_symbol_index(instruments) == 0


def test_default_symbol_index_handles_empty_list():
    assert default_symbol_index([]) == 0
