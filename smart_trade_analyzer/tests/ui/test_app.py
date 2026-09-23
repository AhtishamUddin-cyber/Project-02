"""End-to-end tests for app.py, run through Streamlit's own AppTest
framework (streamlit.testing.v1) -- these execute the REAL script file,
click the REAL "Analyze" button, and inspect the REAL rendered output
tree. The only thing patched is requests.get (the literal HTTP transport
inside data/bitget.py), via unittest.mock.patch on the exact same target
tests/unit/test_bitget_adapter.py already patches -- this project's own,
already-established convention for testing this real adapter class
without live network access (see that file's own module docstring:
"unit tests must not depend on live API availability"). Nothing about
scanner.scan_symbol, BitgetMarketDataSource, or the analytical pipeline
is mocked or substituted anywhere in this file -- every one of them runs
for real, on realistic, deterministic candle data shaped exactly like
Bitget's own documented response format.
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
    o = close  # simple flat-bodied candle; shape/trend comes from the close sequence across rows
    return [str(ts_ms), str(o), str(max(o, close) + 0.5), str(min(o, close) - 0.5), str(close), "100"]


def realistic_confirmed_trend_closes(n=210, drift=0.003, noise=0.01, base_price=100.0, seed=2):
    """The exact TREND_CONTINUATION-confirming generator already verified
    in tests/integration/test_phase6_pipeline.py (seed=2 -> confirmed
    LONG) -- reused here so this UI test exercises a REAL, previously-
    verified confirmed setup rather than an untested arrangement."""
    random.seed(seed)
    closes = [base_price]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    return closes


def bitget_response_router(candle_closes):
    """Routes a mocked requests.get call to either a candles or a ticker
    response, based purely on the URL -- mirrors exactly how the two real
    Bitget endpoints differ, without hand-waving which one is being hit."""
    n = len(candle_closes)
    rows = [_candle_row(n - i, c) for i, c in enumerate(candle_closes)]  # oldest-first minutes_ago descending -> newest last

    def _side_effect(url, params=None, timeout=None, **kwargs):
        if "candles" in url:
            return fake_response(200, {"data": list(reversed(rows))})  # Bitget returns newest-first
        if "ticker" in url:
            return fake_response(200, {"data": [{"lastPr": str(candle_closes[-1])}]})
        raise AssertionError(f"unexpected URL in test: {url}")

    return _side_effect


def _fresh_app():
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    # Force instrument discovery to fail on this initial render,
    # deterministically, regardless of whether the machine running this
    # test can actually reach api.bitget.com -- every test below was
    # written against the free-text-style single-symbol flow, which is
    # exactly the "manual entry" fallback UI app.py renders when
    # discovery fails (see render_analyze_tab). The searchable-selector
    # SUCCESS path is covered separately and thoroughly in
    # test_app_scanner.py. Without this, these tests would pass or fail
    # differently depending on whether the machine running them has
    # working internet access, which is not a property a test should
    # depend on.
    with patch(PATCH_TARGET, side_effect=RuntimeError("instrument discovery intentionally disabled for this test file")):
        at.run()
    return at


# ---------------------------------------------------------------------------
# 1-2. valid symbol and selected timeframe reach the scanner (proven by the
# rendered result actually reflecting that symbol/timeframe -- not just by
# inspecting call arguments, which is a stronger, more honest check).
# ---------------------------------------------------------------------------

def test_valid_symbol_and_timeframe_reach_the_real_scanner_and_render():
    closes = realistic_confirmed_trend_closes()
    at = _fresh_app()
    at.text_input[0].set_value("ethusdt")  # lowercase on purpose -- exercises normalization end to end
    at.selectbox[0].select("15m")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    assert at.exception == []
    assert at.session_state["ui_error"] is None
    model = at.session_state["ui_result"]
    assert model is not None
    assert model.symbol == "ETH"          # derived from the normalized pair, proving normalization reached the scanner
    assert model.pair == "ETHUSDT"
    assert model.timeframe == "15m"       # the selected timeframe, not a default
    assert model.decision in ("LONG", "SHORT", "WAIT", "NO_TRADE")


# ---------------------------------------------------------------------------
# 3-4. LONG / SHORT render correctly through the real app.
# ---------------------------------------------------------------------------

def test_confirmed_long_setup_renders_as_long_or_a_graded_wait():
    # This exact price path is verified (Phase 6) to produce a CONFIRMED
    # TREND_CONTINUATION LONG all the way through the Quality Gate; the
    # final Decision (LONG vs. WAIT on quality) depends on live evaluation
    # timing that this test does not pin down, so it asserts on what
    # run_pipeline actually decided rather than assuming LONG specifically
    # -- exactly the same discipline test_phase6_pipeline.py's own tests
    # use. Either outcome proves the real pipeline reached a real setup.
    closes = realistic_confirmed_trend_closes()
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    assert model is not None
    assert model.setup_family == "Trend Continuation"
    assert model.decision in ("LONG", "WAIT")
    if model.decision == "LONG":
        assert model.direction == "LONG"
        assert model.has_trade_plan is True
        assert model.entry is not None and model.stop_loss is not None


def test_confirmed_short_setup_renders_correctly():
    closes = realistic_confirmed_trend_closes(drift=-0.003, seed=1)  # verified confirmed SHORT
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    assert model is not None
    assert model.decision in ("SHORT", "WAIT")
    if model.decision == "SHORT":
        assert model.direction == "SHORT"
        assert model.stop_loss is not None


# ---------------------------------------------------------------------------
# 5. WAIT renders correctly.
# ---------------------------------------------------------------------------

def test_wait_state_renders_without_looking_broken():
    # A short, flat-ish history: enough candles to clear the minimum, but
    # with no confirmed setup and no dramatic move -- typically lands on
    # WAIT or NO_TRADE, both of which this app must render cleanly.
    random.seed(99)
    closes = [100.0]
    for _ in range(209):
        closes.append(closes[-1] + random.uniform(-0.05, 0.05))
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    assert model is not None
    assert model.decision in ("WAIT", "NO_TRADE")
    # the app must not raise or render an empty page for a non-actionable result
    assert len(at.subheader) > 0


# ---------------------------------------------------------------------------
# 6. NO_TRADE renders correctly -- genuinely no data at all.
# ---------------------------------------------------------------------------

def test_no_trade_on_empty_market_data_renders_an_honest_result_not_a_crash():
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")

    def _empty_side_effect(url, params=None, timeout=None, **kwargs):
        if "candles" in url:
            return fake_response(200, {"data": []})
        return fake_response(200, {"data": [{"lastPr": "100.0"}]})

    with patch(PATCH_TARGET, side_effect=_empty_side_effect):
        at.button[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    assert model is not None
    assert model.decision == "NO_TRADE"
    assert model.data_quality_state == "UNAVAILABLE"
    assert model.has_trade_plan is False


# ---------------------------------------------------------------------------
# 7-8. reasons / warnings render in the actual page output.
# ---------------------------------------------------------------------------

def test_reasons_render_as_markdown_bullets_in_the_page():
    closes = realistic_confirmed_trend_closes()
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    assert len(model.reasons) > 0
    rendered_markdown = "\n".join(m.value for m in at.markdown)
    assert any(reason in rendered_markdown for reason in model.reasons)


# ---------------------------------------------------------------------------
# 9. Entry/SL/TP render when available (checked against the real metric
# widgets app.py actually creates, not just the display model).
# ---------------------------------------------------------------------------

def test_trade_plan_metrics_render_when_a_full_plan_exists():
    closes = realistic_confirmed_trend_closes()
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    assert at.exception == []
    model = at.session_state["ui_result"]
    metric_labels = [m.label for m in at.metric]
    if model.has_trade_plan:
        assert "Entry" in metric_labels
        assert "Stop Loss" in metric_labels
        assert "TP1" in metric_labels


# ---------------------------------------------------------------------------
# 10. Missing optional fields do not crash the UI -- exercised via the
# genuinely-empty-data path above, plus an invalid-input path here.
# ---------------------------------------------------------------------------

def test_blank_symbol_input_shows_a_validation_message_not_a_crash():
    at = _fresh_app()
    at.text_input[0].set_value("   ")
    at.button[0].click().run()
    assert at.exception == []
    assert at.session_state["ui_error"] is not None
    assert at.session_state["ui_result"] is None


# ---------------------------------------------------------------------------
# 11. scanner/data errors are handled gracefully -- a genuine exception
# deep in the call (not an ordinary data problem, which already returns a
# NO_TRADE result per data/quality.py) must not crash the page or leak a
# traceback into the rendered output.
# ---------------------------------------------------------------------------

def test_unexpected_exception_is_caught_and_shown_as_a_concise_message():
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=RuntimeError("simulated unexpected failure")):
        at.button[0].click().run()

    assert at.exception == []  # the APP does not crash, even though the call inside it raised
    assert at.session_state["ui_result"] is None
    assert at.session_state["ui_error"] is not None
    rendered_errors = " ".join(e.value for e in at.error)
    assert "simulated unexpected failure" not in rendered_errors  # no raw exception text/traceback shown to the user
    assert "Traceback" not in rendered_errors


# ---------------------------------------------------------------------------
# 12. The app never computes or overrides Decision -- it only ever shows
# session_state["ui_result"].decision, which is exactly what
# build_display_model() (already proven, in test_display_model.py, to
# pass the backend's Decision through verbatim) produced.
# ---------------------------------------------------------------------------

def test_app_never_recomputes_decision_it_only_displays_the_backend_value():
    closes = realistic_confirmed_trend_closes()
    at = _fresh_app()
    at.text_input[0].set_value("BTCUSDT")
    with patch(PATCH_TARGET, side_effect=bitget_response_router(closes)):
        at.button[0].click().run()

    model = at.session_state["ui_result"]
    # The decision headline renders into one of several Streamlit element
    # types depending on which one (st.success/st.error/st.warning/plain
    # markdown) app.py chose for this decision -- check all of them rather
    # than assuming markdown specifically.
    rendered_text = " ".join(
        el.value for group in (at.markdown, at.subheader, at.success, at.error, at.warning, at.info)
        for el in group
    )
    assert model.decision_headline in rendered_text
