from smart_trade_analyzer.ui.inputs import derive_display_symbol, normalize_pair, validate_pair_input


def test_normalize_pair_strips_and_uppercases():
    assert normalize_pair("  btcusdt  ") == "BTCUSDT"
    assert normalize_pair("EthUsdt") == "ETHUSDT"


def test_derive_display_symbol_strips_known_quote_suffix():
    assert derive_display_symbol("BTCUSDT") == "BTC"
    assert derive_display_symbol("ETHUSDT") == "ETH"
    assert derive_display_symbol("SOLUSDT") == "SOL"
    assert derive_display_symbol("ETHBTC") == "ETH"


def test_derive_display_symbol_falls_back_to_full_pair_when_unknown():
    assert derive_display_symbol("WEIRDPAIR") == "WEIRDPAIR"


def test_validate_pair_input_rejects_empty():
    assert validate_pair_input("") is not None
    assert validate_pair_input("   ") is not None
    assert validate_pair_input(None) is not None


def test_validate_pair_input_rejects_non_alnum():
    assert validate_pair_input("BTC-USDT") is not None
    assert validate_pair_input("BTC USDT") is not None


def test_validate_pair_input_accepts_normal_symbols():
    assert validate_pair_input("BTCUSDT") is None
    assert validate_pair_input("  ethusdt  ") is None
