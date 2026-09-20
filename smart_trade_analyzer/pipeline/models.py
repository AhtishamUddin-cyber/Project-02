"""PipelineResult -- Phase 6.

A phase-local bundle of everything one run_pipeline() call computed, in
the same spirit as scanner/models.py's OpportunityScanResult (Phase 3):
NOT a Phase 1 frozen contract (deliberately not added to contracts/, for
the same reason OpportunityScanResult wasn't -- Phase 1 is frozen and this
shape is allowed to keep evolving as later phases add fields), but built
entirely OUT OF frozen contracts and standard types, never inventing a
competing representation for anything a frozen contract already owns.

Every field below is Optional except the three that are unconditionally
available immediately after a data fetch (market_data, data_quality,
evaluated_at) and current_price (always resolved to *something*, even if
that something is None -- see pipeline/orchestrator.py's
_resolve_current_price). Which fields end up populated tells you exactly
how far the pipeline got for this symbol -- the same "how far did we get"
signal OpportunityScanResult's ScanStatus already gives, but expressed
structurally (is the field None or not) rather than as a separate status
enum, since Phase 6 has a real Decision (via signal_decision.decision)
to serve that role instead.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..contracts import (
    ConfluenceResult, DataQuality, EntryPlan, FeatureSet, MarketData, MarketRegime, RiskPlan,
    SetupCandidate, SignalDecision,
)


@dataclass(frozen=True)
class PipelineResult:
    """Everything one full pipeline run produced, stage by stage. See
    pipeline/orchestrator.py::run_pipeline for how this gets built --
    this class holds data, it does not compute anything itself.
    """
    market_data: MarketData
    data_quality: DataQuality
    evaluated_at: datetime
    current_price: Optional[float]

    feature_set: Optional[FeatureSet] = None
    regime: Optional[MarketRegime] = None
    setup: Optional[SetupCandidate] = None
    confluence: Optional[ConfluenceResult] = None
    entry_plan: Optional[EntryPlan] = None
    risk_plan: Optional[RiskPlan] = None
    signal_decision: Optional[SignalDecision] = None

    # Explanations for early exits that happened BEFORE there was enough
    # to hand the Quality Gate anything meaningful to evaluate beyond G1/
    # G2 (e.g. "no closed candle available", or MarketRegime.basis when
    # regime classification itself couldn't proceed). The Gate's own
    # SignalDecision.reasons already explains what IT decided and why;
    # this list captures the stages upstream of the Gate that it has no
    # visibility into at all (GateContext carries no FeatureSet/
    # MarketRegime field, by design -- see quality_gate/gate.py).
    stage_reasons: List[str] = field(default_factory=list)
