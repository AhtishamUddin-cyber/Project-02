"""Audit-fix tests: the explicit 'All' symbols option, and invalidating a
displayed Analyze/Scanner result when its context changes. Streamlit-free:
session_state is stood in with a plain dict, exactly as ui/context.py's
module docstring says it may be."""
import pytest

from smart_trade_analyzer.ui.context import (
    ANALYZE_STATE_KEYS, SCAN_STATE_KEYS, AnalyzeContext, ScanContext, invalidate_stale_analyze_state,
    invalidate_stale_scan_state, is_context_current, resolve_max_symbols,
)

# -- "All" symbols option (audit-fix #1) --------------------------------------

def test_all_symbols_resolves_to_none_for_scan_market():
    assert resolve_max_symbols(scan_all=True, cap=30) is None


def test_unchecked_all_uses_the_numeric_cap():
    assert resolve_max_symbols(scan_all=False, cap=30) == 30


def test_all_wins_over_whatever_the_cap_widget_holds():
    assert resolve_max_symbols(scan_all=True, cap=5) is None


@pytest.mark.parametrize("cap", [0, -1, -100])
def test_a_non_positive_cap_is_rejected_when_all_is_off(cap):
    with pytest.raises(ValueError):
        resolve_max_symbols(scan_all=False, cap=cap)


# -- stale-result invalidation (audit-fix #2) --------------------------------

def test_context_is_not_current_when_nothing_was_stored():
    assert is_context_current(None, AnalyzeContext("spot", "BTCUSDT", "15m")) is False


def test_context_is_current_only_when_it_matches_exactly():
    ctx = AnalyzeContext("spot", "BTCUSDT", "15m")
    assert is_context_current(ctx, ctx) is True
    assert is_context_current(ctx, AnalyzeContext("spot", "BTCUSDT", "1h")) is False
    assert is_context_current(ctx, AnalyzeContext("futures", "BTCUSDT", "15m")) is False
    assert is_context_current(ctx, AnalyzeContext("spot", "ETHUSDT", "15m")) is False


class TestAnalyzeInvalidation:
    def _state_with_result(self, context):
        return {"ui_result": object(), "ui_opportunity": object(), "ui_error": None,
                "ui_result_context": context, "ui_current_price": object()}

    def test_matching_context_is_left_alone(self):
        ctx = AnalyzeContext("spot", "BTCUSDT", "15m")
        state = self._state_with_result(ctx)
        before = dict(state)
        assert invalidate_stale_analyze_state(state, ctx) is False
        assert state == before

    def test_changed_symbol_clears_every_analyze_key(self):
        state = self._state_with_result(AnalyzeContext("spot", "BTCUSDT", "15m"))
        cleared = invalidate_stale_analyze_state(state, AnalyzeContext("spot", "ETHUSDT", "15m"))
        assert cleared is True
        assert all(state[k] is None for k in ANALYZE_STATE_KEYS)

    def test_changed_timeframe_clears_state(self):
        state = self._state_with_result(AnalyzeContext("spot", "BTCUSDT", "15m"))
        assert invalidate_stale_analyze_state(state, AnalyzeContext("spot", "BTCUSDT", "1h")) is True

    def test_changed_market_clears_state(self):
        state = self._state_with_result(AnalyzeContext("spot", "BTCUSDT", "15m"))
        assert invalidate_stale_analyze_state(state, AnalyzeContext("futures", "BTCUSDT", "15m")) is True

    def test_an_error_with_no_context_recorded_is_treated_as_stale(self):
        state = {"ui_result": None, "ui_opportunity": None, "ui_error": "boom",
                 "ui_result_context": None, "ui_current_price": None}
        assert invalidate_stale_analyze_state(state, AnalyzeContext("spot", "BTCUSDT", "15m")) is True
        assert state["ui_error"] is None

    def test_empty_state_is_a_no_op(self):
        state = {k: None for k in ANALYZE_STATE_KEYS}
        assert invalidate_stale_analyze_state(state, AnalyzeContext("spot", "BTCUSDT", "15m")) is False

    def test_price_panel_state_is_cleared_alongside_the_result(self):
        """The current-price cache belongs to a specific analysis; a changed
        selection must clear it too, not just the trade plan."""
        state = self._state_with_result(AnalyzeContext("spot", "BTCUSDT", "15m"))
        invalidate_stale_analyze_state(state, AnalyzeContext("spot", "ETHUSDT", "15m"))
        assert state["ui_current_price"] is None


class TestScanInvalidation:
    def _state_with_result(self, context):
        return {"scan_result": object(), "scan_error": None, "scan_result_context": context,
                "scan_price_cache": {"BTCUSDT": object()}}

    def test_matching_context_including_max_symbols_is_left_alone(self):
        ctx = ScanContext("spot", "15m", 30)
        state = self._state_with_result(ctx)
        assert invalidate_stale_scan_state(state, ctx) is False
        assert state["scan_result"] is not None

    def test_changed_max_symbols_clears_state(self):
        state = self._state_with_result(ScanContext("spot", "15m", 30))
        assert invalidate_stale_scan_state(state, ScanContext("spot", "15m", 50)) is True

    def test_switching_to_all_symbols_clears_a_capped_result(self):
        state = self._state_with_result(ScanContext("spot", "15m", 30))
        assert invalidate_stale_scan_state(state, ScanContext("spot", "15m", None)) is True

    def test_switching_from_all_back_to_a_cap_clears_state(self):
        state = self._state_with_result(ScanContext("spot", "15m", None))
        assert invalidate_stale_scan_state(state, ScanContext("spot", "15m", 30)) is True

    def test_same_cap_value_is_not_stale(self):
        state = self._state_with_result(ScanContext("spot", "15m", None))
        assert invalidate_stale_scan_state(state, ScanContext("spot", "15m", None)) is False

    def test_price_cache_is_cleared_alongside_the_result(self):
        state = self._state_with_result(ScanContext("spot", "15m", 30))
        invalidate_stale_scan_state(state, ScanContext("futures", "15m", 30))
        assert state["scan_price_cache"] is None

    def test_analyze_and_scan_invalidation_do_not_touch_each_others_keys(self):
        state = self._state_with_result(ScanContext("spot", "15m", 30))
        state.update({"ui_result": object(), "ui_result_context": AnalyzeContext("spot", "BTCUSDT", "15m")})
        invalidate_stale_scan_state(state, ScanContext("futures", "15m", 30))
        assert state["ui_result"] is not None  # untouched by scan invalidation
        assert set(SCAN_STATE_KEYS).isdisjoint(ANALYZE_STATE_KEYS)
