"""Opportunity UI support code -- pure, testable formatting and
display-model construction, with no Streamlit import anywhere in this
package. `app.py` at the repository root is the only place Streamlit
itself is imported; it consumes this package's output to render widgets,
and calls scanner.scan_symbol()/scan_market() directly for real analysis.
"""
from .display_model import OpportunityDisplayModel, build_display_model
from .inputs import derive_display_symbol, normalize_pair, validate_pair_input
from .scanner_display import ScanResultRow, build_scan_result_row, build_scan_result_rows
from .symbol_search import DEFAULT_SYMBOL, default_symbol_index, search_instruments

__all__ = [
    "OpportunityDisplayModel", "build_display_model",
    "derive_display_symbol", "normalize_pair", "validate_pair_input",
    "ScanResultRow", "build_scan_result_row", "build_scan_result_rows",
    "DEFAULT_SYMBOL", "default_symbol_index", "search_instruments",
]
