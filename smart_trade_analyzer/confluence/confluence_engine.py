"""Confluence Engine -- Sections 7-8 of the approved specification.

Combines per-category EvidenceItems (confluence/evidence.py -- the only
place a raw-feature threshold is applied) into the frozen ConfluenceResult
contract. This module applies NO thresholds of its own to raw FeatureSet
values -- it only nets and weights what evidence.py already decided. That
split is what keeps each evidence rule independently testable and this
aggregation step a pure, mechanical combination step.

proposed_direction is NEVER computed here -- it is read, unchanged, from
`setup_candidate.direction` (see test_confluence_engine.py's dedicated
test for this). This module has no code path that assigns
proposed_direction any other way.

category_scores is signed RELATIVE TO proposed_direction, not on an
absolute LONG/SHORT axis (audit fix -- see HANDOFF.md's audit-fix record
for the concrete before/after). Positive means a category's evidence
SUPPORTS whichever direction this specific candidate proposed; negative
means it OPPOSES that direction -- regardless of whether proposed_direction
itself is LONG or SHORT. This is what makes a well-supported SHORT
candidate score exactly as well as an equally-well-supported LONG one, and
it's why `conflicts` (below) is now a single, direction-independent
threshold check rather than its own separate LONG/SHORT branch: once
category_scores is relative, negative always means "against," full stop.
evidence.py's individual EvidenceItems are NOT changed by this and stay
direction-agnostic/absolute (a reusable "what does this raw feature
suggest" read, independent of any one candidate) -- the relative
transformation happens ONLY in this module's netting step, exactly once,
where a specific candidate's direction first enters the computation. See
`_signed_relative_to_proposed_direction` below.

Anti-double-counting (approved decision A / Section 18's explicit test
case: "RSI+StochRSI must not simply add"): within a category, the signed
(relative-to-proposed-direction) values of every EvidenceItem are
AVERAGED, not summed. Two correlated signals that agree (RSI and StochRSI
both moderately supporting the same direction; EMA-gap and MACD both
moderately supporting it) land at roughly the SAME netted category score a
single one of them would have produced alone, not double it. This is the
mechanical implementation of category_scores' own contract docstring:
"ALREADY netted within-category." It also means genuine internal
disagreement partially cancels, which is the correct, honest reading of a
category whose own signals disagree.

Missing categories (evidence.py returned zero items -- no data source, not
a netted-to-zero read) go to categories_excluded and contribute exactly
ZERO to the weighted sum below -- they are not renormalized away, so
having fewer available categories mechanically pulls setup_quality_score
toward 50 (the neutral midpoint), never expands what the remaining
categories can achieve. This is the exact, approved Section 8 formula:

    setup_quality_score = 50 + (sum of available_weight * category_score) / 2,
    clipped to [0, 100]

Because category_scores is now relative to proposed_direction, this
formula correctly rewards a well-supported SHORT exactly as much as a
well-supported LONG -- before the audit fix, it did not (see HANDOFF.md).

CATEGORY_WEIGHTS and GRADE_THRESHOLDS are the exact approved Section 8
values, kept in this ONE location so they can later be calibrated against
the 153 shadow outcomes without touching any other file -- explicitly
PROPOSED, not empirically validated (approved decision A). No calibration
of any kind happens in this phase. setup_quality_score is a score, never
referred to or treated as a probability or confidence value anywhere in
this module (approved decision A / the specification's own repeated
warning against the original system's score-vs-probability conflation).

GRADE_THRESHOLDS is defined here (the single obvious Section 8 location)
but NOT consumed by anything in this phase: ConfluenceResult (frozen,
Phase 1) has no grade field -- turning a score into a QualityGrade is the
future quality_gate/ phase's job (explicitly out of scope this phase; see
contracts/enums.py's own QualityGrade docstring). Defining the constant
now, unused, means quality_gate/ has one obvious place to import it from
later rather than re-deriving or re-approving the same numbers again.

CONFLICT_THRESHOLD (0.5) is the specification's Section 8 value for
"strongly-conflicting" categories -- same PROPOSED, centralized treatment
as the weights and grade bands.
"""
from typing import Dict, List

from . import evidence
from ..contracts import CandleData, ConfluenceResult, Direction, EvidenceCategory, EvidenceItem, FeatureSet, MarketRegime, QualityGrade, SetupCandidate

# ---------------------------------------------------------------------------
# Section 8 constants -- approved, but explicitly PROPOSED / not yet
# empirically validated. Centralized here, nowhere else in this codebase.
# ---------------------------------------------------------------------------

CATEGORY_WEIGHTS: Dict[EvidenceCategory, float] = {
    EvidenceCategory.TREND: 20,
    EvidenceCategory.MOMENTUM: 15,
    EvidenceCategory.STRUCTURE: 15,
    EvidenceCategory.HTF: 15,
    EvidenceCategory.FLOW: 10,
    EvidenceCategory.SENTIMENT: 10,
    EvidenceCategory.VOLUME: 10,
    EvidenceCategory.VOLATILITY: 5,
}
assert sum(CATEGORY_WEIGHTS.values()) == 100, "CATEGORY_WEIGHTS must sum to 100 for the /2 formula to map to 0-100"

# A: 75-100, B: 60-74, C: 40-59, F: below 40 -- expressed as continuous
# lower bounds so setup_quality_score (a float) always lands in exactly
# one band with no gap. NOT consumed this phase -- see module docstring.
GRADE_THRESHOLDS: Dict[str, float] = {"A": 75.0, "B": 60.0, "C": 40.0, "F": 0.0}

CONFLICT_THRESHOLD = 0.5  # category_score beyond this, against proposed_direction, counts as a conflict


def grade_for_score(setup_quality_score: float) -> QualityGrade:
    """Maps a setup_quality_score to a QualityGrade using GRADE_THRESHOLDS
    above. Added in Phase 5 -- GRADE_THRESHOLDS itself was defined back in
    Phase 4 specifically for this future consumer (see this module's
    docstring); quality_gate/gate.py is the first caller. Purely additive:
    does not change anything about how setup_quality_score itself is
    computed.
    """
    if setup_quality_score >= GRADE_THRESHOLDS["A"]:
        return QualityGrade.A
    if setup_quality_score >= GRADE_THRESHOLDS["B"]:
        return QualityGrade.B
    if setup_quality_score >= GRADE_THRESHOLDS["C"]:
        return QualityGrade.C
    return QualityGrade.F


def _signed_relative_to_proposed_direction(item: EvidenceItem, proposed_direction: Direction) -> float:
    """Signed strength of `item` RELATIVE TO `proposed_direction` -- positive
    means this item SUPPORTS the candidate's proposed direction, negative
    means it OPPOSES it, regardless of whether that direction is LONG or
    SHORT. This is the layer where a specific candidate's direction enters
    the computation; evidence.py's items stay direction-agnostic/absolute
    (a reusable "what does this raw feature suggest" read, independent of
    any one candidate) -- see this module's docstring.
    """
    if item.direction == Direction.NEUTRAL:
        return 0.0
    return item.strength if item.direction == proposed_direction else -item.strength


def compute_confluence(
    setup_candidate: SetupCandidate,
    fs: FeatureSet,
    regime: MarketRegime,
    closed: List[CandleData],
) -> ConfluenceResult:
    """The one function this package exposes. Pure: no data fetching, no
    randomness, no wall-clock reads -- a deterministic function of its four
    arguments, matching every prior phase's engines.
    """
    proposed_direction = setup_candidate.direction  # inherited, unchanged -- never computed here

    per_category_items: Dict[EvidenceCategory, List[EvidenceItem]] = {
        EvidenceCategory.TREND: evidence.trend_evidence(fs),
        EvidenceCategory.MOMENTUM: evidence.momentum_evidence(fs),
        EvidenceCategory.STRUCTURE: evidence.structure_evidence(fs),
        EvidenceCategory.VOLATILITY: evidence.volatility_evidence(fs, regime),
        EvidenceCategory.VOLUME: evidence.volume_evidence(fs, closed),
        EvidenceCategory.HTF: evidence.htf_evidence(),
        EvidenceCategory.FLOW: evidence.flow_evidence(),
        EvidenceCategory.SENTIMENT: evidence.sentiment_evidence(),
    }

    categories_available: List[EvidenceCategory] = []
    categories_excluded: List[EvidenceCategory] = []
    category_scores: Dict[EvidenceCategory, float] = {}
    all_evidence: List[EvidenceItem] = []

    # Iterate CATEGORY_WEIGHTS' own order so every output list has a fixed,
    # deterministic category ordering regardless of dict-construction order.
    for category in CATEGORY_WEIGHTS:
        items = per_category_items[category]
        if not items:
            categories_excluded.append(category)
            continue
        categories_available.append(category)
        all_evidence.extend(items)
        netted = sum(_signed_relative_to_proposed_direction(item, proposed_direction) for item in items) / len(items)
        category_scores[category] = max(-1.0, min(1.0, netted))  # AVERAGE, not sum -- anti-double-counting

    weighted_sum = sum(CATEGORY_WEIGHTS[cat] * score for cat, score in category_scores.items())
    setup_quality_score = 50.0 + weighted_sum / 2.0
    setup_quality_score = max(0.0, min(100.0, setup_quality_score))

    conflicts: List[EvidenceCategory] = []
    for category in CATEGORY_WEIGHTS:
        score = category_scores.get(category)
        if score is None:
            continue  # excluded categories cannot conflict -- there is nothing to conflict with
        if score < -CONFLICT_THRESHOLD:  # category_scores is ALREADY relative to proposed_direction
            conflicts.append(category)

    return ConfluenceResult(
        proposed_direction=proposed_direction,
        category_scores=category_scores,
        categories_available=categories_available,
        categories_excluded=categories_excluded,
        setup_quality_score=setup_quality_score,
        evidence=all_evidence,
        conflicts=conflicts,
    )
