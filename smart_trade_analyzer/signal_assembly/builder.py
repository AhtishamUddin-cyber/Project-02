"""Signal Assembly -- Phase 6.

Constructs the frozen SignalRecord contract (contracts/signal_record.py)
out of one pipeline/orchestrator.py PipelineResult. This module makes no
trading decision of any kind -- it only reads signal_decision.decision
(already produced by quality_gate/gate.py, the sole authority for
Decision) and copies it verbatim onto the record. There is no branch
anywhere in this file that inspects a score or a category and picks a
decision; SignalRecord.decision and .direction are always exactly
pipeline_result.signal_decision.decision / .direction, nothing else.

build_signal_record() honestly refuses to build a record at all (returns
None) when the pipeline never reached a real SetupCandidate/
ConfluenceResult -- SignalRecord.confluence, .feature_snapshot, and
.regime_snapshot are non-Optional fields on the frozen contract, and
confluence/confluence_engine.py itself requires a real SetupCandidate to
run, so there is no honest ConfluenceResult to embed when no candidate was
ever detected (or data was too thin to even attempt detection). This is
not a Phase 6 gap -- it is what the frozen contract's own field shape
already implies, and matches the project's "never fabricate on
insufficient evidence" mandate: better to omit a SignalRecord than embed a
made-up ConfluenceResult in one. scanner/models.py::OpportunityResult
(the caller of this function) is exactly the envelope that carries a
Decision -- and reasons/warnings -- even on the runs where no
SignalRecord could honestly be built.
"""
import hashlib
from typing import List, Optional, Tuple

from ..contracts import DataQualityState, SignalRecord
from ..pipeline import PipelineResult


def _make_signal_id(pipeline_result: PipelineResult, setup_type, direction) -> str:
    """Deterministic, not random: the same (symbol, timeframe,
    market_type, as_of, setup_type, direction) tuple always yields the
    same id. Keeps two independent runs against the exact same snapshot
    idempotent (a scanner re-polling before new data arrives, or a test
    re-running the same fixture, gets the same id rather than a fresh one
    each time) -- which is also what Phase 6's determinism requirement
    (Section 12) expects from every other field on the record. Not a
    cryptographic use of sha256, just a convenient, collision-resistant,
    dependency-free way to compress the identity tuple into a short,
    opaque, stable string -- no wall-clock read, no randomness.
    """
    market_data = pipeline_result.market_data
    raw = "|".join([
        market_data.symbol, market_data.market_type.value, market_data.timeframe.value,
        market_data.as_of.isoformat(), setup_type.value, direction.value if direction is not None else "NONE",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def compile_reasons_and_warnings(pipeline_result: PipelineResult) -> Tuple[List[str], List[str]]:
    """Positive reasons and warnings, built ONLY by aggregating strings
    other, already-approved modules already produced:

      reasons  <- signal_decision.reasons (the Gate's own gate-by-gate
                  trail), setup.evidence_refs (the detector's
                  family-specific justification), a mechanical
                  "<category> supports <direction>" line per confluence
                  category with a positive score, and an R:R line when
                  risk_plan.meets_min_rr is True.
      warnings <- setup.failed_conditions (what almost disqualified this
                  setup), risk_plan.warnings (structure-clearance capping
                  notes), data_quality.reasons (when quality is reduced),
                  and a mechanical "<category> conflicts with <direction>"
                  line per confluence.conflicts.

    Nothing here computes a NEW judgment call -- every line traces back to
    a value some other module already calculated (Phase 6 Section 7: "Do
    not manufacture reasons that are not supported by actual evidence").
    Exact-duplicate lines are collapsed (order-preserving) as a cheap
    safety net, not because duplication was expected.

    Public (not a leading-underscore helper) because scanner/engine.py
    also calls this directly for the early-exit cases where
    build_signal_record() below honestly returns None -- reusing this
    exact function there, rather than writing a second, similar
    aggregation, is what keeps reasons/warnings compilation in exactly
    one place (Phase 6 Section 17: "Do not duplicate business logic
    between scanner, orchestrator, and signal builder").
    """
    reasons: List[str] = []
    warnings: List[str] = []

    signal_decision = pipeline_result.signal_decision
    setup = pipeline_result.setup
    confluence = pipeline_result.confluence
    risk_plan = pipeline_result.risk_plan
    data_quality = pipeline_result.data_quality

    reasons.extend(pipeline_result.stage_reasons)

    if signal_decision is not None:
        reasons.extend(signal_decision.reasons)
        warnings.extend(signal_decision.warnings)  # always [] today (gate.py never populates it) -- kept for forward compatibility, not relied upon

    if data_quality.overall != DataQualityState.VALID:
        warnings.extend(data_quality.reasons)

    if setup is not None:
        reasons.extend(setup.evidence_refs)
        warnings.extend(setup.failed_conditions)

    if confluence is not None:
        direction_label = confluence.proposed_direction.value
        conflict_set = set(confluence.conflicts)
        for category in confluence.categories_available:
            score = confluence.category_scores.get(category, 0.0)
            if category in conflict_set:
                warnings.append(f"{category.value} conflicts with proposed direction ({direction_label})")
            elif score > 0:
                reasons.append(f"{category.value} supports proposed direction ({direction_label})")

    if risk_plan is not None:
        warnings.extend(risk_plan.warnings)
        if risk_plan.meets_min_rr:
            reasons.append(f"risk/reward acceptable ({risk_plan.risk_reward_1:.2f}:1 on TP1)")

    return list(dict.fromkeys(reasons)), list(dict.fromkeys(warnings))


def build_signal_record(pipeline_result: PipelineResult) -> Optional[SignalRecord]:
    """Assemble a SignalRecord from a completed PipelineResult, or return
    None when there isn't enough to honestly build one (see module
    docstring). symbol/timeframe/market_type/timestamp are all read off
    pipeline_result.market_data -- the same MarketData every stage in
    this pipeline run already shares, never re-supplied separately (a
    second copy could silently drift out of sync with what was actually
    analyzed).
    """
    setup = pipeline_result.setup
    confluence = pipeline_result.confluence
    feature_set = pipeline_result.feature_set
    regime = pipeline_result.regime
    signal_decision = pipeline_result.signal_decision

    if setup is None or confluence is None or feature_set is None or regime is None or signal_decision is None:
        return None

    entry_plan = pipeline_result.entry_plan
    risk_plan = pipeline_result.risk_plan
    market_data = pipeline_result.market_data

    # entry / confirmation_price are deliberately the SAME value: this
    # implementation has exactly one canonical "entry reference" concept
    # (EntryPlan.confirmation_price), used both as the trader-facing
    # entry price and as the anchor risk_engine.py measures stop/target
    # distance from (see tests/integration/test_phase5_pipeline.py's
    # run_full_chain, the established convention this phase integrates
    # rather than reinvents). Both frozen-contract fields exist because
    # SignalRecord's spec names them separately, not because this system
    # computes two different numbers.
    entry_reference = entry_plan.confirmation_price if entry_plan is not None else None
    entry_zone = (entry_plan.entry_zone_low, entry_plan.entry_zone_high) if entry_plan is not None else None
    # entry_plan.invalidation_price is always a verbatim copy of
    # setup.invalidation_price (see entry/entry_engine.py::build_entry_plan)
    # -- falling back to the setup's own value when no zone could be built
    # is not a different number, just the same fact read from the object
    # that's actually available.
    invalidation_price = entry_plan.invalidation_price if entry_plan is not None else setup.invalidation_price

    stop_loss = risk_plan.stop_loss if risk_plan is not None else None
    take_profit_1 = risk_plan.take_profit_1 if risk_plan is not None else None
    take_profit_2 = risk_plan.take_profit_2 if risk_plan is not None else None
    # risk_reward maps to risk_reward_1 specifically: TP1's R:R is the one
    # MIN_RR_HARD_GATE / RiskPlan.meets_min_rr actually gates (see
    # risk/risk_engine.py's own docstring -- "meets_min_rr only gates
    # TP1"), so it is the single R:R value that is actually decision-
    # relevant. TP2's R:R remains available via take_profit_2 + stop_loss
    # + entry for anyone who wants to derive it themselves.
    risk_reward = risk_plan.risk_reward_1 if risk_plan is not None else None

    reasons, warnings = compile_reasons_and_warnings(pipeline_result)

    return SignalRecord(
        id=_make_signal_id(pipeline_result, setup.setup_type, signal_decision.direction),
        symbol=market_data.symbol,
        timeframe=market_data.timeframe,
        market_type=market_data.market_type,
        timestamp=market_data.as_of,
        decision=signal_decision.decision,
        direction=signal_decision.direction,
        setup_type=setup.setup_type,
        market_regime=regime.regime,
        setup_score=confluence.setup_quality_score,
        historical_probability=None,  # never fabricated -- no calibration/shadow-outcome phase yet; see SignalRecord's own docstring on decisions #4/#5
        entry=entry_reference,
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward=risk_reward,
        confirmation_price=entry_reference,
        invalidation_price=invalidation_price,
        quality_grade=signal_decision.quality_grade,
        reasons=reasons,
        warnings=warnings,
        data_quality=pipeline_result.data_quality,
        confluence=confluence,
        entry_plan=entry_plan,
        risk_plan=risk_plan,
        feature_snapshot=feature_set,
        regime_snapshot=regime,
        status="PENDING",
    )
