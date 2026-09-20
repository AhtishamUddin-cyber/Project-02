"""Opportunity UI support code -- pure, testable formatting and
display-model construction, with no Streamlit import anywhere in this
package. `app.py` at the repository root is the only place Streamlit
itself is imported; it consumes build_display_model()'s output to render
widgets, and calls scanner.scan_symbol() directly for the real analysis.
"""
from .display_model import OpportunityDisplayModel, build_display_model
from .inputs import derive_display_symbol, normalize_pair, validate_pair_input

__all__ = [
    "OpportunityDisplayModel", "build_display_model",
    "derive_display_symbol", "normalize_pair", "validate_pair_input",
]
