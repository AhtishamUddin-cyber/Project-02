"""Opportunity Scanner -- the public entry points a future UI calls.

analyze_market() (Phase 3, unmodified) composes data/, features/,
regime/, and setup/ into an honest answer that stops at "is there a
confirmed SetupCandidate": no entry/SL/TP/risk/decision math.

scan_symbol() (Phase 6) composes the same data layer with pipeline/ and
signal_assembly/ to run the FULL chain through the Quality Gate, returning
an OpportunityResult that wraps the frozen SignalRecord contract. Both
entry points coexist; scan_symbol is additive, not a replacement.
"""
from .engine import analyze_market, scan_symbol
from .models import OpportunityResult, OpportunityScanResult, ScanStatus

__all__ = ["analyze_market", "scan_symbol", "OpportunityResult", "OpportunityScanResult", "ScanStatus"]
