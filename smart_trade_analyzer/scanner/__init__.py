"""Opportunity Scanner -- the public entry points a future UI calls.

analyze_market() (Phase 3, unmodified) composes data/, features/,
regime/, and setup/ into an honest answer that stops at "is there a
confirmed SetupCandidate": no entry/SL/TP/risk/decision math.

scan_symbol() (Phase 6) composes the same data layer with pipeline/ and
signal_assembly/ to run the FULL chain through the Quality Gate, returning
an OpportunityResult that wraps the frozen SignalRecord contract.

scan_market() (this phase) loops scan_symbol() over a caller-supplied
List[Instrument] -- orchestration only, never a second decision path; see
multi_scan.py's module docstring. sort_scan_results()/
filter_actionable_only()/filter_by_min_quality_score() are separate,
optional, presentation-only helpers for a caller (typically the UI) to
apply to a MarketScanResult's already-final results.

All entry points coexist; each addition is additive, never a replacement.
"""
from .engine import analyze_market, scan_symbol
from .models import MarketScanResult, OpportunityResult, OpportunityScanResult, ScanFailure, ScanStatus
from .multi_scan import filter_actionable_only, filter_by_min_quality_score, scan_market, sort_scan_results

__all__ = [
    "analyze_market", "scan_symbol", "scan_market",
    "OpportunityResult", "OpportunityScanResult", "ScanStatus", "MarketScanResult", "ScanFailure",
    "sort_scan_results", "filter_actionable_only", "filter_by_min_quality_score",
]
