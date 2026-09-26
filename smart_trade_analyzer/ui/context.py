"""Streamlit-free helpers that keep a displayed result honest about the
context it was produced under (audit-fix: stale results).

A result is only valid for the exact (market, symbol, timeframe[, scan size])
selection that produced it. When any of those change, the old result must not
keep rendering under the new selection -- so it is discarded, never "kept but
relabelled". Only the CONTEXT fields count: purely presentational controls
(sorting, WAIT/NO_TRADE visibility, quality filter, symbol search text that
does not change the selected symbol) are deliberately not part of it.

These functions take any MutableMapping (st.session_state in the app, a plain
dict in unit tests), so the invalidation rule is testable without Streamlit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import MutableMapping, Optional

# Every session_state key that belongs to one Analyze result / one Scan result.
ANALYZE_STATE_KEYS = ("ui_result", "ui_opportunity", "ui_error", "ui_result_context", "ui_current_price")
SCAN_STATE_KEYS = ("scan_result", "scan_error", "scan_result_context", "scan_price_cache")


@dataclass(frozen=True)
class AnalyzeContext:
    market: str      # MarketType.value
    pair: str        # normalized exchange symbol as currently selected/typed
    timeframe: str   # Timeframe.value


@dataclass(frozen=True)
class ScanContext:
    market: str
    timeframe: str
    max_symbols: Optional[int]   # None means "All"


def is_context_current(stored: object, current: object) -> bool:
    """True only if a context was stored AND equals the current one. A result
    whose context is unknown is treated as stale (fail safe)."""
    return stored is not None and stored == current


def resolve_max_symbols(scan_all: bool, cap: int) -> Optional[int]:
    """The max_symbols argument for scan_market(): None when the explicit
    "All" option is on (scan every tradable symbol), else the positive cap."""
    if scan_all:
        return None
    cap = int(cap)
    if cap < 1:
        raise ValueError(f"the symbol cap must be >= 1, got {cap}")
    return cap


def _invalidate(state: MutableMapping, keys, result_keys, stored_key: str, current: object) -> bool:
    has_something = any(state.get(k) is not None for k in result_keys)
    if not has_something:
        return False
    if is_context_current(state.get(stored_key), current):
        return False
    for key in keys:
        state[key] = None
    return True


def invalidate_stale_analyze_state(state: MutableMapping, current: AnalyzeContext) -> bool:
    """Discard the Analyze result/error (and its price panel state) if it was
    produced under a different context. Returns True if anything was cleared."""
    return _invalidate(state, ANALYZE_STATE_KEYS, ("ui_result", "ui_opportunity", "ui_error"),
                       "ui_result_context", current)


def invalidate_stale_scan_state(state: MutableMapping, current: ScanContext) -> bool:
    """Discard the Scanner result/error (and its price cache) if it was
    produced under a different context. Returns True if anything was cleared."""
    return _invalidate(state, SCAN_STATE_KEYS, ("scan_result", "scan_error"), "scan_result_context", current)
