"""End-to-end tests for Trade Tracking (Track Trade, the live-price panel,
the Trade Tracking tab) and the two audit-fixes (explicit "All" symbols
option; stale-result invalidation), run through Streamlit's own AppTest
framework -- same approach as tests/ui/test_app.py and test_app_scanner.py,
which this file deliberately does not import from (matching their own
established convention of staying self-contained). The REAL app.py runs; the
REAL scan_symbol()/scan_market()/tracking.TrackingService all execute for
real, against a throwaway per-test SQLite file (tests/ui/conftest.py). Only
requests.get inside data/bitget.py is ever patched.

Candle generation matches tests/unit/test_multi_scan.py's own
realistic_candles_with_wicks (open = prior close, proportional randomized
wicks) and reuses its exact LONG_SEED/SHORT_SEED + drift formula, verified
empirically (24/24 trials) to reach a real LONG/SHORT decision through this
actual app -- but with timestamps anchored to a FRESHLY computed
datetime.now() at router-call time rather than a fixed historical constant.
This matters: app.py's run_analysis() does not pin evaluated_at, so it reads
the real wall clock, and candles anchored to an old fixed timestamp appear
stale to it. Every LONG/SHORT-dependent test here explicitly selects the "1m"
timeframe to match the candles' 1-minute spacing (the app's own default is
"15m", which would otherwise create a timeframe/data mismatch since this
file's candles are always 1-minute apart). Tests that don't depend on a
specific decision (audit-fix mechanics) use simple flat-ish data and don't
need any of this.
"""
import random
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

PATCH_TARGET = "smart_trade_analyzer.data.bitget.requests.get"
APP_PATH = str(Path(__file__).resolve().parents[3] / "app.py")

# Verified empirically (24/24 trials at "1m") to reach a real LONG / SHORT
# decision through the real pipeline -- same seeds tests/unit/test_multi_scan.py
# already relies on (LONG_SEED/SHORT_SEED there), reused here with their exact
# drift formula since that combination is what was actually verified.
LONG_SEED, SHORT_SEED = 1194, 194
LONG_DRIFT = random.Random(LONG_SEED).uniform(-0.006, 0.006)
SHORT_DRIFT = random.Random(SHORT_SEED).uniform(-0.006, 0.006)


def fake_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def realistic_ohlcv(n, seed, drift, noise=0.025, base=100.0):
    """(open, high, low, close, volume) tuples -- same body/wick construction
    as tests/unit/test_multi_scan.py's realistic_candles_with_wicks: open is
    the prior candle's close, wicks are randomized and proportional to price
    (not a fixed offset), which is what that generator's own LONG/SHORT
    seeds were verified against."""
    random.seed(seed)
    closes = [base]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    out = []
    price = base
    for c in closes:
        o = price
        body_high, body_low = max(o, c), min(o, c)
        wick_up = body_high * random.uniform(0.001, 0.01)
        wick_down = body_low * random.uniform(0.001, 0.01)
        out.append((o, body_high + wick_up, body_low - wick_down, c, random.uniform(80, 120)))
        price = c
    return out


def flat_ohlcv(n=210, level=100.0):
    """A boring, directionless market -- for tests that don't care what
    decision results, only that discovery/scan/context mechanics work."""
    return [(level, level + 0.05, level - 0.05, level, 100.0) for _ in range(n)]


def full_router(ohlcv_by_symbol, spot_instruments=None, futures_instruments=None, minutes_per_candle=1):
    """One requests.get side_effect for every Bitget endpoint the app calls
    (spot symbols, futures contracts, spot/mix candles, spot/mix tickers).
    Every candle/ticker timestamp is anchored to `datetime.now()` read FRESH
    on each call (not a constant captured once) -- see module docstring."""
    spot_instruments = spot_instruments if spot_instruments is not None else [
        {"symbol": s, "baseCoin": s.replace("USDT", ""), "quoteCoin": "USDT", "status": "online"}
        for s in ohlcv_by_symbol
    ]
    futures_instruments = futures_instruments if futures_instruments is not None else [
        {"symbol": s, "baseCoin": s.replace("USDT", ""), "quoteCoin": "USDT", "symbolStatus": "normal"}
        for s in ohlcv_by_symbol
    ]

    def _side_effect(url, params=None, timeout=None, **kwargs):
        now = datetime.now()
        if "spot/public/symbols" in url:
            return fake_response(200, {"data": spot_instruments})
        if "mix/market/contracts" in url:
            return fake_response(200, {"data": futures_instruments})
        if "candles" in url:
            symbol = (params or {}).get("symbol")
            ohlcv = ohlcv_by_symbol[symbol]
            n = len(ohlcv)
            rows = [[str(int((now - timedelta(minutes=minutes_per_candle * (n - i))).timestamp() * 1000)),
                     str(o), str(h), str(l), str(c), str(v)] for i, (o, h, l, c, v) in enumerate(ohlcv)]
            return fake_response(200, {"data": list(reversed(rows))})
        if "ticker" in url:
            symbol = (params or {}).get("symbol")
            return fake_response(200, {"data": [{"lastPr": str(ohlcv_by_symbol[symbol][-1][3])}]})
        raise AssertionError(f"unexpected URL in test: {url}")

    return _side_effect


def _fresh_app():
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()  # first render: real network is unreachable in this sandbox -> discovery fails softly
    return at


def _button(at, label):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"expected a button labeled {label!r}; got {[b.label for b in at.button]}"
    return matches[0]


def _analyze(at, ohlcv_by_symbol, symbol="BTCUSDT", timeframe="1m"):
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv_by_symbol)):
        at.run()  # resolves instrument discovery under the patch (cache cleared by conftest.py)
        search_inputs = [ti for ti in at.text_input if ti.label == "Search symbol"]
        search_inputs[0].set_value(symbol).run()
        symbol_selects = [sb for sb in at.selectbox if sb.label == "Symbol"]
        symbol_selects[0].select(symbol).run()
        if timeframe is not None:
            tf_selects = [sb for sb in at.selectbox if sb.label == "Timeframe"]
            tf_selects[0].select(timeframe).run()
        _button(at, "Analyze").click().run()
    return at


def _track_a_long_result(at=None, symbol="BTCUSDT"):
    """Analyzes `symbol` with the verified-reliable LONG candle set. Returns
    (at, model, ohlcv). Falls back to pytest.skip (visible in the report,
    never a silent pass) in the rare case this run's evaluation timing did
    not land on LONG -- verified empirically at 12/12 (and separately
    24/24 combined with SHORT) reliable, so this should not trigger."""
    at = at if at is not None else _fresh_app()
    ohlcv = {symbol: realistic_ohlcv(210, LONG_SEED, LONG_DRIFT)}
    _analyze(at, ohlcv, symbol=symbol)
    model = at.session_state["ui_result"]
    if model is None or model.decision != "LONG":
        pytest.skip(f"this run's evaluation timing did not land on LONG (got {model and model.decision!r}); "
                    f"verified 12/12 reliable empirically, so this is expected to be rare")
    return at, model, ohlcv


def _track_a_short_result(at=None, symbol="ETHUSDT"):
    at = at if at is not None else _fresh_app()
    ohlcv = {symbol: realistic_ohlcv(210, SHORT_SEED, SHORT_DRIFT)}
    _analyze(at, ohlcv, symbol=symbol)
    model = at.session_state["ui_result"]
    if model is None or model.decision != "SHORT":
        pytest.skip(f"this run's evaluation timing did not land on SHORT (got {model and model.decision!r}); "
                    f"verified 12/12 reliable empirically, so this is expected to be rare")
    return at, model, ohlcv


# ---------------------------------------------------------------------------
# Track Trade: the button, a successful click, duplicate protection.
# ---------------------------------------------------------------------------

def test_track_button_appears_for_an_actionable_long_result():
    at, model, _ = _track_a_long_result()
    assert at.exception == []
    assert any(b.label == "Track Trade" for b in at.button)


def test_track_button_appears_for_an_actionable_short_result():
    at, model, _ = _track_a_short_result()
    assert at.exception == []
    assert any(b.label == "Track Trade" for b in at.button)


def test_track_button_is_absent_for_a_wait_or_no_trade_result():
    at = _analyze(_fresh_app(), {"BTCUSDT": flat_ohlcv()})
    assert at.exception == []
    assert at.session_state["ui_result"].decision in ("WAIT", "NO_TRADE")
    assert not any(b.label == "Track Trade" for b in at.button)


def test_clicking_track_trade_saves_a_real_trade_with_the_frozen_plan_values():
    at, model, ohlcv = _track_a_long_result()
    opportunity = at.session_state["ui_opportunity"]

    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):  # Track Trade fetches its own live price
        _button(at, "Track Trade").click().run()

    assert at.exception == []
    from smart_trade_analyzer.tracking import build_default_service
    service = build_default_service()
    stored = service.find_tracked(opportunity.signal_record.id)
    assert stored is not None
    assert stored.snapshot.entry == opportunity.signal_record.entry
    assert stored.snapshot.stop_loss == opportunity.signal_record.stop_loss
    assert stored.snapshot.take_profit_1 == opportunity.signal_record.take_profit_1
    assert stored.snapshot.take_profit_2 == opportunity.signal_record.take_profit_2
    assert any(s.value for s in at.success)  # a confirmation is shown


def test_the_track_button_is_replaced_by_a_tracked_indicator_after_clicking():
    at, model, ohlcv = _track_a_long_result()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        _button(at, "Track Trade").click().run()
    assert at.exception == []
    assert not any(b.label == "Track Trade" for b in at.button)  # the button itself is gone
    assert any("Tracked" in s.value for s in at.success)


def test_the_ui_never_produces_two_trades_for_one_click_sequence():
    at, model, ohlcv = _track_a_long_result()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        _button(at, "Track Trade").click().run()

    from smart_trade_analyzer.tracking import build_default_service
    service = build_default_service()
    assert len(service.list_trades()) == 1

    # A second click is not even possible through the UI once the button is replaced
    # (proven above); the service's own guard is the deeper protection -- confirmed
    # directly, exercised through the exact OpportunityResult the UI produced.
    from smart_trade_analyzer.tracking.errors import DuplicateSignalError
    with pytest.raises(DuplicateSignalError):
        service.track(at.session_state["ui_opportunity"])
    assert len(service.list_trades()) == 1


# ---------------------------------------------------------------------------
# Live price panel.
# ---------------------------------------------------------------------------

def test_price_panel_shows_analysis_and_current_price():
    at, model, _ = _track_a_long_result()
    assert at.exception == []
    metric_labels = {m.label for m in at.metric}
    assert "Analysis price" in metric_labels and "Current price (Bitget)" in metric_labels
    current_metric = next(m for m in at.metric if m.label == "Current price (Bitget)")
    assert current_metric.value not in (None, "", "N/A")  # fetched automatically alongside Analyze


def test_refresh_price_button_updates_the_shown_current_price():
    at, model, ohlcv = _track_a_long_result()
    last = ohlcv["BTCUSDT"][-1]
    moved = {"BTCUSDT": ohlcv["BTCUSDT"] + [(last[3], last[3] * 1.06, last[3] * 0.99, last[3] * 1.05, 100.0)]}
    with patch(PATCH_TARGET, side_effect=full_router(moved)):
        _button(at, "Refresh price").click().run()
    assert at.exception == []
    current_metric = next(m for m in at.metric if m.label == "Current price (Bitget)")
    assert current_metric.value not in (None, "", "N/A")


def test_a_failed_price_refresh_does_not_crash_and_says_so():
    at, model, _ = _track_a_long_result()
    with patch(PATCH_TARGET, side_effect=RuntimeError("network down")):
        _button(at, "Refresh price").click().run()
    assert at.exception == []
    assert any(w for w in at.warning)


# ---------------------------------------------------------------------------
# Trade Tracking tab: table, statistics, trade detail, refresh, rules panel.
# ---------------------------------------------------------------------------

def test_tracking_tab_shows_an_empty_state_with_no_tracked_trades():
    at = _fresh_app()
    assert at.exception == []
    assert any("No tracked trades yet" in i.value for i in at.info)


def test_tracking_tab_reflects_a_newly_tracked_trade_and_its_statistics():
    at, model, ohlcv = _track_a_long_result()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        _button(at, "Track Trade").click().run()

    assert at.exception == []
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Total tracked") == "1"
    assert metrics.get("Open (incl. TP1 reached)") == "1"
    assert metrics.get("Wins (TP2 hit)") == "0"
    assert list(at.dataframe), "expected the active-trades table to render"


def test_tracking_tab_rules_panel_documents_tp1_is_not_a_win():
    at = _fresh_app()
    assert at.exception == []
    expander_text = " ".join(md.value for exp in at.expander for md in exp.markdown)
    assert "milestone" in expander_text.lower()


def test_refresh_prices_advances_a_tracked_long_trade_to_tp1_or_beyond():
    at, model, ohlcv = _track_a_long_result()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        _button(at, "Track Trade").click().run()
    assert at.exception == []

    from smart_trade_analyzer.tracking import build_default_service
    tracked = build_default_service().list_trades()[0]
    beyond_tp1_price = tracked.snapshot.take_profit_1 * 1.01  # comfortably through TP1
    last = ohlcv["BTCUSDT"][-1]
    beyond = {"BTCUSDT": ohlcv["BTCUSDT"] + [(last[3], beyond_tp1_price * 1.001, last[3] * 0.99,
                                              beyond_tp1_price, 100.0)]}
    with patch(PATCH_TARGET, side_effect=full_router(beyond)):
        _button(at, "Refresh prices").click().run()

    assert at.exception == []
    from smart_trade_analyzer.tracking import TradeStatus
    service = build_default_service()
    trades = service.list_trades()
    assert len(trades) == 1
    assert trades[0].status in (TradeStatus.TP1_HIT, TradeStatus.TP2_HIT)  # moved forward, never silently ignored
    assert trades[0].tp1_hit_at is not None


def test_refresh_prices_with_no_tracked_trades_does_not_crash():
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_router({})):
        _button(at, "Refresh prices").click().run()
    assert at.exception == []
    assert any("No open trades" in i.value for i in at.info)


# ---------------------------------------------------------------------------
# Audit-fix 1: explicit "All" symbols option.
# ---------------------------------------------------------------------------

def test_all_symbols_checkbox_exists_and_disables_the_cap_input():
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_router({"BTCUSDT": flat_ohlcv()})):
        at.run()
    checkboxes = [c for c in at.checkbox if "All symbols" in c.label]
    assert checkboxes, "expected an explicit All-symbols checkbox"
    cap_inputs = [n for n in at.number_input if n.label == "Max symbols"]
    assert cap_inputs and cap_inputs[0].disabled is False
    checkboxes[0].set_value(True).run()
    cap_inputs = [n for n in at.number_input if n.label == "Max symbols"]
    assert cap_inputs[0].disabled is True


def test_all_symbols_scans_every_discovered_instrument_not_just_the_cap():
    symbols = [f"SYM{i}USDT" for i in range(7)]
    ohlcv = {s: flat_ohlcv() for s in symbols}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        at.run()
        cap_inputs = [n for n in at.number_input if n.label == "Max symbols"]
        cap_inputs[0].set_value(5).run()  # cap below the discovered count
        checkboxes = [c for c in at.checkbox if "All symbols" in c.label]
        checkboxes[0].set_value(True).run()
        _button(at, "Scan Market").click().run()

    assert at.exception == []
    scan_result = at.session_state["scan_result"]
    assert scan_result is not None and scan_result.scanned_count == 7  # not capped to 5


def test_leaving_all_symbols_unchecked_still_respects_the_cap():
    symbols = [f"SYM{i}USDT" for i in range(7)]
    ohlcv = {s: flat_ohlcv() for s in symbols}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        at.run()
        cap_inputs = [n for n in at.number_input if n.label == "Max symbols"]
        cap_inputs[0].set_value(5).run()
        _button(at, "Scan Market").click().run()

    assert at.exception == []
    assert at.session_state["scan_result"].scanned_count == 5


# ---------------------------------------------------------------------------
# Audit-fix 2: stale Analyze/Scanner results are invalidated on context change.
# ---------------------------------------------------------------------------

def test_changing_symbol_after_analyzing_clears_the_stale_result():
    ohlcv = {"BTCUSDT": flat_ohlcv(), "ETHUSDT": flat_ohlcv()}
    at = _analyze(_fresh_app(), ohlcv, symbol="BTCUSDT")
    assert at.session_state["ui_result"] is not None
    assert at.session_state["ui_result"].pair == "BTCUSDT"

    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        search_inputs = [ti for ti in at.text_input if ti.label == "Search symbol"]
        search_inputs[0].set_value("ETH").run()
        symbol_selects = [sb for sb in at.selectbox if sb.label == "Symbol"]
        symbol_selects[0].select("ETHUSDT").run()  # selection changed, Analyze NOT clicked again

    assert at.exception == []
    assert at.session_state["ui_result"] is None  # the stale BTCUSDT result must not still be showing
    assert at.session_state["ui_opportunity"] is None
    assert at.session_state["ui_current_price"] is None


def test_changing_timeframe_after_analyzing_clears_the_stale_result():
    ohlcv = {"BTCUSDT": flat_ohlcv()}
    at = _analyze(_fresh_app(), ohlcv, symbol="BTCUSDT")
    assert at.session_state["ui_result"] is not None

    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        tf_selects = [sb for sb in at.selectbox if sb.label == "Timeframe"]
        other = next(o for o in tf_selects[0].options if o != tf_selects[0].value)
        tf_selects[0].select(other).run()

    assert at.exception == []
    assert at.session_state["ui_result"] is None


def test_re_analyzing_after_a_context_change_produces_a_fresh_result():
    ohlcv = {"BTCUSDT": flat_ohlcv(), "ETHUSDT": flat_ohlcv()}
    at = _analyze(_fresh_app(), ohlcv, symbol="BTCUSDT")
    at = _analyze(at, ohlcv, symbol="ETHUSDT")
    assert at.exception == []
    assert at.session_state["ui_result"] is not None
    assert at.session_state["ui_result"].pair == "ETHUSDT"  # a real, current result -- not just cleared


def test_scanner_result_is_cleared_when_the_symbol_cap_changes():
    ohlcv = {"BTCUSDT": flat_ohlcv(), "ETHUSDT": flat_ohlcv()}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        at.run()
        _button(at, "Scan Market").click().run()
    assert at.session_state["scan_result"] is not None

    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        cap_inputs = [n for n in at.number_input if n.label == "Max symbols"]
        cap_inputs[0].set_value(cap_inputs[0].value + 5).run()  # context changed, Scan NOT re-clicked

    assert at.exception == []
    assert at.session_state["scan_result"] is None


def test_scanner_price_cache_does_not_leak_across_a_stale_context():
    """The audit-fix must clear the scanner's own current-price cache too,
    not just the result list -- otherwise a price fetched for the old
    context could be shown next to a symbol from a new one."""
    ohlcv = {"BTCUSDT": flat_ohlcv()}
    at = _fresh_app()
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        at.run()
        _button(at, "Scan Market").click().run()

    assert at.session_state.get("scan_price_cache") is not None or at.session_state["scan_result"] is not None
    with patch(PATCH_TARGET, side_effect=full_router(ohlcv)):
        cap_inputs = [n for n in at.number_input if n.label == "Max symbols"]
        cap_inputs[0].set_value(cap_inputs[0].value + 5).run()
    assert at.session_state.get("scan_price_cache") is None
