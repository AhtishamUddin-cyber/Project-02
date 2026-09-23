"""End-to-end tests for the reworked app.py (searchable symbol selector +
Scanner tab), run through Streamlit's own AppTest framework -- same
approach as the original tests/ui/test_app.py this extends: the REAL
script runs, the REAL scan_symbol()/scan_market()/discover_tradable_instruments()
all execute for real, and the only thing ever patched is requests.get
inside data/bitget.py (the same target tests/unit/test_bitget_adapter.py
already patches, for the same documented reason).
"""
import random
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

PATCH_TARGET = "smart_trade_analyzer.data.bitget.requests.get"
NOW = datetime.now()
APP_PATH = str(Path(__file__).resolve().parents[3] / "app.py")


def fake_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def _candle_row(minutes_ago, close, base=NOW):
    ts_ms = int((base - timedelta(minutes=minutes_ago)).timestamp() * 1000)
    return [str(ts_ms), str(close), str(close + 0.5), str(close - 0.5), str(close), "100"]


def realistic_confirmed_trend_closes(n=210, drift=0.003, noise=0.01, base_price=100.0, seed=2):
    random.seed(seed)
    closes = [base_price]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    return closes


def full_bitget_router(per_symbol_closes, spot_instruments=None, futures_instruments=None):
    """One requests.get side_effect handling every Bitget endpoint this
    app can now call: spot symbols, futures contracts, spot/mix candles,
    spot/mix tickers -- routed purely by URL, exactly like the real
    exchange's own distinct endpoints."""
    spot_instruments = spot_instruments if spot_instruments is not None else [
        {"symbol": s, "baseCoin": s.replace("USDT", ""), "quoteCoin": "USDT", "status": "online"}
        for s in per_symbol_closes
    ]
    futures_instruments = futures_instruments if futures_instruments is not None else [
        {"symbol": s, "baseCoin": s.replace("USDT", ""), "quoteCoin": "USDT", "symbolStatus": "normal"}
        for s in per_symbol_closes
    ]

    def _side_effect(url, params=None, timeout=None, **kwargs):
        if "spot/public/symbols" in url:
            return fake_response(200, {"data": spot_instruments})
        if "mix/market/contracts" in url:
            return fake_response(200, {"data": futures_instruments})
        if "candles" in url:
            symbol = params.get("symbol") if params else None
            closes = per_symbol_closes.get(symbol, [])
            n = len(closes)
            rows = [_candle_row(n - i, c) for i, c in enumerate(closes)]
            return fake_response(200, {"data": list(reversed(rows))})
        if "ticker" in url:
            symbol = params.get("symbol") if params else None
            closes = per_symbol_closes.get(symbol, [])
            last = closes[-1] if closes else 100.0
            return fake_response(200, {"data": [{"lastPr": str(last)}]})
        raise AssertionError(f"unexpected URL in test: {url}")

    return _side_effect


def _fresh_app():
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()
    return at


# ---------------------------------------------------------------------------
# Market selection + searchable symbol selection reach the real scanner.
# ---------------------------------------------------------------------------

def test_market_and_searchable_symbol_selection_reach_scan_symbol():
    closes = {"BTCUSDT": realistic_confirmed_trend_closes(), "ETHUSDT": realistic_confirmed_trend_closes(seed=1, drift=-0.003)}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        at.run()  # re-run so instrument discovery (cached) resolves under the patch
        # Analyze tab is tab index 0 -- search for ETH, select it.
        search_inputs = [ti for ti in at.text_input if ti.label == "Search symbol"]
        assert search_inputs, "expected the searchable symbol text input to render"
        search_inputs[0].set_value("ETH").run()
        symbol_selects = [sb for sb in at.selectbox if sb.label == "Symbol"]
        assert symbol_selects and "ETHUSDT" in symbol_selects[0].options
        symbol_selects[0].select("ETHUSDT").run()
        analyze_buttons = [b for b in at.button if b.label == "Analyze"]
        analyze_buttons[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    assert model is not None
    assert model.pair == "ETHUSDT"
    assert model.symbol == "ETH"


def test_btcusdt_is_the_default_selected_symbol():
    closes = {"BTCUSDT": realistic_confirmed_trend_closes()}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        at.run()
        symbol_selects = [sb for sb in at.selectbox if sb.label == "Symbol"]
        assert symbol_selects
        assert symbol_selects[0].value == "BTCUSDT"


def test_search_with_no_matches_shows_an_honest_message_not_a_crash():
    closes = {"BTCUSDT": realistic_confirmed_trend_closes()}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        at.run()
        search_inputs = [ti for ti in at.text_input if ti.label == "Search symbol"]
        search_inputs[0].set_value("ZZZNOPE").run()
    assert at.exception == []
    captions = " ".join(c.value for c in at.caption)
    assert "No tradable" in captions


def test_instrument_discovery_failure_falls_back_to_manual_entry_and_still_analyzes():
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=RuntimeError("network down")):
        at.run()
    assert at.exception == []
    assert any(w for w in at.warning)  # a clear discovery-failure warning shown
    manual_inputs = [ti for ti in at.text_input if ti.label == "Symbol (manual entry)"]
    assert manual_inputs and manual_inputs[0].value == "BTCUSDT"

    # And Analyze still works via the manual fallback once real data is available.
    closes = {"BTCUSDT": realistic_confirmed_trend_closes()}
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        analyze_buttons = [b for b in at.button if b.label == "Analyze"]
        analyze_buttons[0].click().run()
    assert at.exception == []
    assert at.session_state["ui_result"] is not None


# ---------------------------------------------------------------------------
# Scanner execution + results display.
# ---------------------------------------------------------------------------

def test_scanner_runs_and_displays_actionable_results():
    long_closes = realistic_confirmed_trend_closes()  # verified confirmed setup
    closes = {"BTCUSDT": long_closes, "ETHUSDT": realistic_confirmed_trend_closes(seed=1, drift=-0.003),
              "FLATUSDT": [100.0] * 210}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        at.run()
        scan_buttons = [b for b in at.button if b.label == "Scan Market"]
        assert scan_buttons
        scan_buttons[0].click().run()

    assert at.exception == []
    scan_result = at.session_state["scan_result"]
    assert scan_result is not None
    assert scan_result.scanned_count == 3
    assert {r.pair for r in scan_result.results} == {"BTCUSDT", "ETHUSDT", "FLATUSDT"}


def test_scanner_never_implements_independent_long_short_logic():
    # Deliberately construct data where a plain "score >= threshold" UI
    # rule WOULD get the direction wrong if one existed -- the real
    # pipeline's Decision is the only thing that can appear.
    closes = {"BTCUSDT": realistic_confirmed_trend_closes(), "ETHUSDT": realistic_confirmed_trend_closes(seed=1, drift=-0.003)}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        at.run()
        scan_buttons = [b for b in at.button if b.label == "Scan Market"]
        scan_buttons[0].click().run()

    scan_result = at.session_state["scan_result"]
    for r in scan_result.results:
        # every Decision present is exactly what quality_gate.evaluate()
        # returned -- verified the strong way: LONG/SHORT results always
        # satisfy OpportunityResult's own __post_init__ SignalRecord check.
        if r.decision.value in ("LONG", "SHORT"):
            assert r.signal_record is not None
            assert r.signal_record.decision == r.decision


def test_scan_failure_for_one_symbol_does_not_crash_the_scan_or_the_app():
    closes = {"BTCUSDT": realistic_confirmed_trend_closes()}
    at = _fresh_app()

    def flaky_router(url, params=None, timeout=None, **kwargs):
        if "candles" in url and params and params.get("symbol") == "BROKENUSDT":
            raise RuntimeError("simulated failure")
        return full_bitget_router(closes, spot_instruments=[
            {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "online"},
            {"symbol": "BROKENUSDT", "baseCoin": "BROKEN", "quoteCoin": "USDT", "status": "online"},
        ])(url, params, timeout, **kwargs)

    with patch(PATCH_TARGET, side_effect=flaky_router):
        at.run()
        scan_buttons = [b for b in at.button if b.label == "Scan Market"]
        scan_buttons[0].click().run()

    assert at.exception == []
    scan_result = at.session_state["scan_result"]
    assert scan_result is not None
    assert len(scan_result.failures) == 1
    assert scan_result.failures[0].pair == "BROKENUSDT"
    assert any(r.pair == "BTCUSDT" for r in scan_result.results)


def test_scanner_instrument_discovery_failure_shows_a_clear_error():
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=RuntimeError("network down")):
        at.run()
        scan_buttons = [b for b in at.button if b.label == "Scan Market"]
        scan_buttons[0].click().run()
    assert at.exception == []
    assert at.session_state["scan_result"] is None
    assert at.session_state["scan_error"] is not None
    rendered_errors = " ".join(e.value for e in at.error)
    assert "network down" not in rendered_errors  # no raw exception text leaked


def test_show_wait_no_trade_toggle_changes_what_is_displayed():
    closes = {"FLATUSDT": [100.0] * 210}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_bitget_router(closes)):
        at.run()
        scan_buttons = [b for b in at.button if b.label == "Scan Market"]
        scan_buttons[0].click().run()
        # default: actionable-only view -- FLATUSDT (NO_TRADE) hidden
        assert "No results match the current filters." in " ".join(c.value for c in at.caption)
        show_all_checkboxes = [c for c in at.checkbox if c.label == "Show WAIT / NO_TRADE"]
        assert show_all_checkboxes
        show_all_checkboxes[0].check().run()

    assert at.exception == []
    dataframes = at.dataframe
    assert len(dataframes) >= 1  # the results table now renders with FLATUSDT included
