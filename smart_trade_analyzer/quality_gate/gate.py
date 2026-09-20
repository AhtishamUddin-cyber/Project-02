"""Quality Gate -- Section 12 of the approved specification.

THE SOLE AUTHORITY FOR CONSTRUCTING A DIRECTIONAL DECISION. No other module
in this codebase may produce `Decision.LONG` or `Decision.SHORT` --
confluence/, setup/, entry/, and risk/ each report their own honest,
partial reading (a score, a candidate, a plan) and none of them decide
anything. `evaluate()` below is the only function anywhere in this project
that constructs a `SignalDecision` with a non-None `direction`, and it only
does so after all 10 gates in this module pass, in order. There is no
fallback branch anywhere in this file that infers a direction from
anything other than an already-confirmed `SetupCandidate.direction` that
survived every gate -- no ch_24h tiebreak, no vote-margin fallback, no
majority-vote fallback, no default LONG or SHORT, no "best available
direction". A failed gate always resolves to exactly WAIT or NO_TRADE,
per the frozen `Decision` enum's own docstring (WAIT = forming but
unconfirmed; NO_TRADE = no valid setup or a critical failure) -- this
module's gate-to-outcome mapping is a direct implementation of that
existing, already-approved distinction, not a new invention.

Gates run in strict numeric order and short-circuit: the first gate that
fails determines the outcome and no later gate is evaluated for that call
(a later gate's boolean is simply absent from `gates` when this happens --
`SignalDecision.gates` is a record of what was actually checked, not a
padded fixed-size table of gates that were never reached).

G6 (HTF Acceptable) follows the specification's proposed LENIENT default:
an HTF conflict routes to WAIT, not NO_TRADE, and no stricter veto is
invented (Section H item #9 is explicitly still open; the lenient reading
is the specification's own stated default, used here as PROVISIONAL, not
as a final policy decision on your behalf).

G9 (Quality Threshold) uses confluence/confluence_engine.py's existing,
already-centralized GRADE_THRESHOLDS via grade_for_score() -- not a
second, separately-invented set of numbers.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from ..confluence.confluence_engine import grade_for_score
from ..contracts import (
    ConfluenceResult, DataQuality, DataQualityState, Decision, Direction, EntryPlan, EvidenceCategory,
    QualityGrade, RiskPlan, SetupCandidate, SignalDecision, Timeframe,
)
from ..entry.entry_engine import TIMEFRAME_MINUTES, within_chase_distance

# Section H item #9: HTF strictness -- lenient default per the specification's
# own stated proposal, PROVISIONAL, not a final policy decision.
HTF_STRICT_MODE = False

# G5: fewer than this many strongly-conflicting categories required to pass.
MAX_ALLOWED_STRONG_CONFLICTS = 2

# G9: score bands (Section 8, already centralized in confluence_engine.py's
# GRADE_THRESHOLDS -- this is only the pass/fail cut for the *gate*, not a
# duplicate of the grade thresholds themselves. <40 -> NO_TRADE (handled
# via GRADE_THRESHOLDS["C"] below), 40-59 -> WAIT, >=60 -> passes.
QUALITY_GATE_MIN_SCORE = 60.0

# G10: signal freshness, expressed the same way as entry/entry_engine.py's
# staleness TTL (candles-worth-of-time, timeframe-aware) -- provisional,
# not a separate Section H item, same discipline as every other
# implementation-level constant in this codebase.
SIGNAL_FRESHNESS_CANDLE_MULTIPLE = 2.5


@dataclass
class GateContext:
    """Everything evaluate() needs, bundled for readability. Every field
    is exactly what the corresponding gate checks -- nothing is fetched or
    computed inside this module; it only reads what it's given."""
    data_quality: DataQuality
    as_of: datetime
    timeframe: Timeframe
    setup: Optional[SetupCandidate]
    confluence: Optional[ConfluenceResult]
    entry_plan: Optional[EntryPlan]
    risk_plan: Optional[RiskPlan]
    evaluated_at: datetime
    current_price: Optional[float] = None


def _decision(
    decision: Decision,
    direction,
    gates: Dict[str, bool],
    reasons: List[str],
    warnings: List[str],
    confluence: Optional[ConfluenceResult],
    grade: Optional[QualityGrade] = None,
) -> SignalDecision:
    if grade is None:
        grade = grade_for_score(confluence.setup_quality_score) if confluence is not None else QualityGrade.F
    return SignalDecision(
        decision=decision, direction=direction, quality_grade=grade,
        gates=dict(gates), reasons=list(reasons), warnings=list(warnings),
    )


def evaluate(ctx: GateContext) -> SignalDecision:
    gates: Dict[str, bool] = {}
    reasons: List[str] = []
    warnings: List[str] = []

    # G1 -- Data Valid
    g1 = ctx.data_quality.overall != DataQualityState.UNAVAILABLE
    gates["G1_DATA_VALID"] = g1
    if not g1:
        reasons.append("data unavailable")
        return _decision(Decision.NO_TRADE, None, gates, reasons, warnings, None)

    # G2 -- Setup Exists
    g2 = ctx.setup is not None and ctx.setup.prerequisites_met
    gates["G2_SETUP_EXISTS"] = g2
    if not g2:
        reasons.append("no setup candidate found")
        return _decision(Decision.NO_TRADE, None, gates, reasons, warnings, None)

    # G3 -- Setup Confirmed
    g3 = ctx.setup.confirmation_met
    gates["G3_SETUP_CONFIRMED"] = g3
    if not g3:
        reasons.append("setup forming but not yet confirmed")
        return _decision(Decision.WAIT, None, gates, reasons, warnings, ctx.confluence)
    reasons.append("setup confirmed")

    # G4 -- Direction Clear. ConfluenceResult.proposed_direction is
    # ALWAYS inherited unchanged from SetupCandidate.direction by
    # construction (confluence/confluence_engine.py never computes it any
    # other way) -- this check is a defensive integrity check that the
    # objects passed together actually belong together, not a search for
    # ambiguity that could not otherwise exist once confluence was built
    # correctly from this exact setup.
    g4 = ctx.confluence is not None and ctx.confluence.proposed_direction == ctx.setup.direction
    gates["G4_DIRECTION_CLEAR"] = g4
    if not g4:
        reasons.append("direction ambiguous or confluence unavailable")
        return _decision(Decision.WAIT, None, gates, reasons, warnings, ctx.confluence)
    reasons.append("direction aligned")

    # G5 -- No Major Conflict
    strong_conflicts = len(ctx.confluence.conflicts)
    g5 = strong_conflicts < MAX_ALLOWED_STRONG_CONFLICTS
    gates["G5_NO_MAJOR_CONFLICT"] = g5
    if not g5:
        reasons.append(f"{strong_conflicts} categories strongly conflict with the proposed direction")
        return _decision(Decision.NO_TRADE, None, gates, reasons, warnings, ctx.confluence)
    if EvidenceCategory.STRUCTURE in ctx.confluence.categories_available:
        reasons.append("strong structure support" if ctx.confluence.category_scores.get(EvidenceCategory.STRUCTURE, 0) > 0 else "no major category conflict")
    else:
        reasons.append("no major category conflict")

    # G6 -- HTF Acceptable (lenient default -- see module docstring)
    htf_conflict = EvidenceCategory.HTF in ctx.confluence.conflicts
    g6 = (not htf_conflict) if not HTF_STRICT_MODE else (EvidenceCategory.HTF in ctx.confluence.categories_available and not htf_conflict)
    gates["G6_HTF_ACCEPTABLE"] = g6
    if not g6:
        reasons.append("HTF conflict")
        return _decision(Decision.WAIT, None, gates, reasons, warnings, ctx.confluence)

    # G7 -- Entry Valid
    g7 = False
    if ctx.entry_plan is None:
        reasons.append("entry plan unavailable")
    else:
        chase_ok = ctx.current_price is None or within_chase_distance(ctx.entry_plan, ctx.current_price)
        g7 = ctx.entry_plan.structure_clearance_ok and (not ctx.entry_plan.is_stale) and chase_ok
        if ctx.entry_plan.is_stale:
            reasons.append("entry stale")
        if not ctx.entry_plan.structure_clearance_ok:
            reasons.append("structure clearance failed")
        if not chase_ok:
            reasons.append("chase distance exceeded")
    gates["G7_ENTRY_VALID"] = g7
    if not g7:
        return _decision(Decision.WAIT, None, gates, reasons, warnings, ctx.confluence)
    reasons.append("entry valid")

    # G8 -- Risk Valid
    g8 = False
    if ctx.risk_plan is None:
        reasons.append("risk plan unavailable")
    else:
        g8 = ctx.risk_plan.meets_min_rr and ctx.risk_plan.target_clearance_ok
        if not ctx.risk_plan.meets_min_rr:
            reasons.append("minimum R:R failed")
        if not ctx.risk_plan.target_clearance_ok:
            reasons.append("TP blocked by opposing structure")
    gates["G8_RISK_VALID"] = g8
    if not g8:
        return _decision(Decision.NO_TRADE, None, gates, reasons, warnings, ctx.confluence)
    reasons.append("risk valid")

    # G9 -- Quality Threshold (uses confluence_engine.py's centralized
    # GRADE_THRESHOLDS via grade_for_score -- setup_quality_score is a
    # score, never a probability/confidence/win-rate).
    score = ctx.confluence.setup_quality_score
    grade = grade_for_score(score)
    g9 = score >= QUALITY_GATE_MIN_SCORE
    gates["G9_QUALITY_THRESHOLD"] = g9
    if not g9:
        if grade == QualityGrade.F:
            reasons.append("insufficient quality score")
            return _decision(Decision.NO_TRADE, None, gates, reasons, warnings, ctx.confluence, grade)
        reasons.append("quality score marginal")
        return _decision(Decision.WAIT, None, gates, reasons, warnings, ctx.confluence, grade)
    reasons.append("quality score sufficient")

    # G10 -- Signal Fresh
    data_age_minutes = (ctx.evaluated_at - ctx.as_of).total_seconds() / 60.0
    freshness_limit = SIGNAL_FRESHNESS_CANDLE_MULTIPLE * TIMEFRAME_MINUTES[ctx.timeframe]
    g10 = 0 <= data_age_minutes <= freshness_limit
    gates["G10_SIGNAL_FRESH"] = g10
    if not g10:
        reasons.append("signal data no longer fresh")
        return _decision(Decision.NO_TRADE, None, gates, reasons, warnings, ctx.confluence, grade)
    reasons.append("signal fresh")

    # All 10 gates passed -- the ONLY place in this codebase a directional
    # Decision is constructed, and only ever equal to the already-
    # confirmed SetupCandidate's own direction.
    final_decision = Decision.LONG if ctx.setup.direction == Direction.LONG else Decision.SHORT
    return _decision(final_decision, ctx.setup.direction, gates, reasons, warnings, ctx.confluence, grade)
