"""Result types for the Opportunity Scanner.

OpportunityScanResult (Phase 3, unmodified) carries the already-computed
MarketData/DataQuality/FeatureSet/MarketRegime/SetupCandidate straight
through, plus one status enum describing which of five scanner outcomes
applies -- no score, no confidence number, no entry/SL/TP, since Phase 3's
scanner never ran past SetupCandidate. It answers exactly one question,
honestly: is there a confirmed trade setup here, or not, and if not, why
not.

OpportunityResult (Phase 6, added below) is the fuller result for the
scanner entry point that runs the complete chain through the Quality
Gate -- see its own docstring for how it relates to the frozen
SignalRecord contract. Both types coexist; the second does not replace
or modify the first.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..contracts import (
    ConfluenceResult, DataQuality, Decision, EntryPlan, FeatureSet, MarketData, MarketRegime,
    MarketType, RiskPlan, SetupCandidate, SignalDecision, SignalRecord, Timeframe,
)


class ScanStatus(str, Enum):
    NO_DATA = "NO_DATA"                              # DataQuality.overall == UNAVAILABLE
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"     # data usable, but too little history to
                                                       # compute a meaningful feature set / regime
    NO_SETUP = "NO_SETUP"                             # regime computed, but no setup is CONFIRMED
                                                       # (covers both "no candidate at all" and "a
                                                       # candidate is forming but unconfirmed" -- the
                                                       # raw `setup` field below still exposes which)
    SETUP_LONG = "SETUP_LONG"                         # a confirmed LONG setup candidate
    SETUP_SHORT = "SETUP_SHORT"                       # a confirmed SHORT setup candidate


@dataclass(frozen=True)
class OpportunityScanResult:
    status: ScanStatus
    symbol: str
    pair: str
    market_type: MarketType
    timeframe: Timeframe
    reasons: List[str] = field(default_factory=list)

    market_data: Optional[MarketData] = None
    data_quality: Optional[DataQuality] = None
    feature_set: Optional[FeatureSet] = None
    regime: Optional[MarketRegime] = None
    setup: Optional[SetupCandidate] = None

    def __post_init__(self):
        if self.status in (ScanStatus.SETUP_LONG, ScanStatus.SETUP_SHORT):
            if self.setup is None or not self.setup.confirmation_met:
                raise ValueError(
                    f"OpportunityScanResult.status={self.status} requires a confirmed "
                    f"SetupCandidate, got setup={self.setup!r}"
                )


@dataclass(frozen=True)
class OpportunityResult:
    """Phase 6's public pipeline result -- the full chain through the
    Quality Gate, returned by scanner/engine.py::scan_symbol. Wraps
    SignalRecord (contracts/signal_record.py) rather than competing with
    it: `decision` is quality_gate/gate.py's own Decision, copied
    verbatim off PipelineResult.signal_decision (see
    pipeline/orchestrator.py -- the Gate is invoked exactly once per
    scan, on whatever stage outputs were actually reached, and nothing in
    this class or in scan_symbol() re-derives or overrides it).
    `signal_record` is populated only when the pipeline reached a real
    SetupCandidate/ConfluenceResult -- see signal_assembly/builder.py's
    module docstring for exactly why it is honestly None otherwise rather
    than a fabricated stand-in; `decision` and `reasons`/`warnings` above
    remain meaningful even then, since the Gate can (and does) reach
    NO_TRADE via G1/G2 with nothing else populated.

    Deliberately NOT a Phase 1 frozen contract, for the same reason
    OpportunityScanResult (Phase 3, left untouched above) isn't: this is
    the outer envelope a caller needs before there is necessarily enough
    evidence to build the one frozen contract that represents an actual
    analytical result. `signal_record`, `confluence`, `entry_plan`, and
    `risk_plan` are all the exact frozen-contract objects from
    contracts/ -- nothing here reimplements or competes with them, and
    nothing on OpportunityScanResult above was changed to make room for
    this (existing callers of analyze_market/OpportunityScanResult are
    unaffected).
    """
    decision: Decision
    symbol: str
    pair: str
    market_type: MarketType
    timeframe: Timeframe
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    market_data: Optional[MarketData] = None
    data_quality: Optional[DataQuality] = None
    feature_set: Optional[FeatureSet] = None
    regime: Optional[MarketRegime] = None
    setup: Optional[SetupCandidate] = None
    confluence: Optional[ConfluenceResult] = None
    entry_plan: Optional[EntryPlan] = None
    risk_plan: Optional[RiskPlan] = None
    signal_decision: Optional[SignalDecision] = None
    signal_record: Optional[SignalRecord] = None

    def __post_init__(self):
        if self.decision in (Decision.LONG, Decision.SHORT) and self.signal_record is None:
            raise ValueError(
                f"OpportunityResult.decision={self.decision} requires a SignalRecord -- "
                f"the Quality Gate cannot reach LONG/SHORT without a confirmed, scored "
                f"setup (see signal_assembly/builder.py), got signal_record=None"
            )


@dataclass(frozen=True)
class ScanFailure:
    """One symbol that could not be scanned at all -- scan_symbol() (or
    something it called) raised an unexpected exception for it. This is
    deliberately NOT turned into a fake WAIT/NO_TRADE OpportunityResult
    (Section: Scanner Result Rules / Error Handling -- "do not turn the
    failed symbol into a fake WAIT or NO_TRADE"): a real OpportunityResult
    always carries a real Decision the Quality Gate actually reached, and
    for a symbol that crashed mid-scan, no such Decision exists. `error`
    is a concise, human-readable summary only -- never a raw traceback
    (matching app.py's own "never leak a traceback to the user" rule);
    the full exception is available to whoever is running the scan via
    the same `log` callback BitgetMarketDataSource already uses, if one
    was supplied to scan_market().
    """
    symbol: str
    pair: str
    error: str


@dataclass(frozen=True)
class MarketScanResult:
    """The outcome of one scanner/multi_scan.py::scan_market() call --
    every symbol that was actually scanned (as a real OpportunityResult,
    whatever Decision the Quality Gate reached for it) plus every symbol
    that failed to scan at all (as a ScanFailure, never a fabricated
    result). `results` is in the SAME order symbols were scanned in --
    scanner/multi_scan.py's sort_scan_results()/filter_* functions are
    separate, explicit, presentation-only steps a caller applies to this
    list; scan_market() itself does not sort or filter anything (Section:
    Sorting -- "sorting must NOT affect the trading decision", kept true
    by construction: sorting happens only after every result here is
    already final).
    """
    market_type: MarketType
    timeframe: Timeframe
    results: List[OpportunityResult] = field(default_factory=list)
    failures: List[ScanFailure] = field(default_factory=list)
    requested_count: int = 0
    scanned_count: int = 0
