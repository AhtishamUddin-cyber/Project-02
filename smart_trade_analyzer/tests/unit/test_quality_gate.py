"""Tests for quality_gate/gate.py -- the sole authority for constructing a
directional Decision. Every one of the 10 gates gets at least one isolated
PASS and one isolated FAIL test, plus integration scenarios spanning the
whole gate sequence and the critical regression that no Decision.LONG/
SHORT can ever be produced without every gate passing.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import (
    CandleData, DataQuality, DataQualityState, Decision, Direction, EntryPlan, EvidenceCategory,
    FeatureSet, QualityGrade, RiskPlan, SetupCandidate, SetupType, Timeframe,
)
from smart_trade_analyzer.confluence import compute_confluence
from smart_trade_analyzer.contracts import ConfluenceResult
from smart_trade_analyzer.quality_gate.gate import GateContext, evaluate

NOW = datetime(2026, 8, 27, 12, 0, 0)


# ---------------------------------------------------------------------------
# Fixture builders -- everything defaults to a fully-passing scenario so
# each test only needs to override the single field it's testing.
# ---------------------------------------------------------------------------

def make_data_quality(overall=DataQualityState.VALID):
    return DataQuality(overall=overall, candle_count=210, candle_count_required=200,
                        per_source={}, excluded_sources=[], reasons=[])


def make_setup(prerequisites_met=True, confirmation_met=True, direction=Direction.LONG, invalidation_price=90.0):
    return SetupCandidate(
        setup_type=SetupType.TREND_CONTINUATION, direction=direction,
        prerequisites_met=prerequisites_met, confirmation_met=confirmation_met,
        invalidation_price=invalidation_price, evidence_refs=["x"], failed_conditions=[],
    )


def make_confluence(direction=Direction.LONG, score=80.0, conflicts=None, categories_available=None,
                     category_scores=None):
    categories_available = categories_available if categories_available is not None else [EvidenceCategory.TREND]
    category_scores = category_scores if category_scores is not None else {EvidenceCategory.TREND: 0.8}
    categories_excluded = [c for c in EvidenceCategory if c not in categories_available]
    return ConfluenceResult(
        proposed_direction=direction, category_scores=category_scores,
        categories_available=categories_available, categories_excluded=categories_excluded,
        setup_quality_score=score, evidence=[], conflicts=conflicts or [],
    )


def make_entry_plan(direction=Direction.LONG, structure_clearance_ok=True, is_stale=False,
                     zone_low=99.0, zone_high=100.0, max_chase_distance=2.0):
    return EntryPlan(
        direction=direction, entry_zone_low=zone_low, entry_zone_high=zone_high,
        confirmation_price=zone_high if direction == Direction.LONG else zone_low,
        invalidation_price=90.0, max_chase_distance=max_chase_distance,
        structure_clearance_ok=structure_clearance_ok, generated_at=NOW,
        staleness_ttl_minutes=5.0, is_stale=is_stale,
    )


def make_risk_plan(meets_min_rr=True, target_clearance_ok=True):
    return RiskPlan(
        stop_loss=95.0, take_profit_1=105.0, take_profit_2=110.0,
        risk_reward_1=2.0, risk_reward_2=3.0, meets_min_rr=meets_min_rr,
        target_clearance_ok=target_clearance_ok, position_size=None,
        max_safe_leverage=20.0, warnings=[],
    )


def make_context(**overrides):
    defaults = dict(
        data_quality=make_data_quality(),
        as_of=NOW,
        timeframe=Timeframe.M1,
        setup=make_setup(),
        confluence=make_confluence(),
        entry_plan=make_entry_plan(),
        risk_plan=make_risk_plan(),
        evaluated_at=NOW,
        current_price=99.5,
    )
    defaults.update(overrides)
    return GateContext(**defaults)


# ---------------------------------------------------------------------------
# G1 -- Data Valid
# ---------------------------------------------------------------------------

class TestG1DataValid:
    def test_pass(self):
        result = evaluate(make_context(data_quality=make_data_quality(DataQualityState.VALID)))
        assert result.gates["G1_DATA_VALID"] is True

    def test_fail_unavailable_routes_to_no_trade(self):
        result = evaluate(make_context(data_quality=make_data_quality(DataQualityState.UNAVAILABLE)))
        assert result.gates["G1_DATA_VALID"] is False
        assert result.decision == Decision.NO_TRADE
        assert result.direction is None
        assert "data unavailable" in result.reasons

    def test_degraded_still_passes_g1(self):
        result = evaluate(make_context(data_quality=make_data_quality(DataQualityState.DEGRADED)))
        assert result.gates["G1_DATA_VALID"] is True


# ---------------------------------------------------------------------------
# G2 -- Setup Exists
# ---------------------------------------------------------------------------

class TestG2SetupExists:
    def test_pass(self):
        result = evaluate(make_context(setup=make_setup(prerequisites_met=True)))
        assert result.gates["G2_SETUP_EXISTS"] is True

    def test_fail_no_setup_routes_to_no_trade(self):
        result = evaluate(make_context(setup=None, confluence=None, entry_plan=None, risk_plan=None))
        assert result.gates["G2_SETUP_EXISTS"] is False
        assert result.decision == Decision.NO_TRADE
        assert result.direction is None


# ---------------------------------------------------------------------------
# G3 -- Setup Confirmed
# ---------------------------------------------------------------------------

class TestG3SetupConfirmed:
    def test_pass(self):
        result = evaluate(make_context(setup=make_setup(confirmation_met=True)))
        assert result.gates["G3_SETUP_CONFIRMED"] is True

    def test_fail_forming_routes_to_wait(self):
        result = evaluate(make_context(setup=make_setup(confirmation_met=False)))
        assert result.gates["G3_SETUP_CONFIRMED"] is False
        assert result.decision == Decision.WAIT
        assert result.direction is None
        assert "setup forming but not yet confirmed" in result.reasons


# ---------------------------------------------------------------------------
# G4 -- Direction Clear
# ---------------------------------------------------------------------------

class TestG4DirectionClear:
    def test_pass(self):
        result = evaluate(make_context(setup=make_setup(direction=Direction.LONG),
                                        confluence=make_confluence(direction=Direction.LONG)))
        assert result.gates["G4_DIRECTION_CLEAR"] is True

    def test_fail_mismatched_direction_routes_to_wait(self):
        # A defensive integrity check: confluence built for a DIFFERENT
        # direction than the setup being gated.
        result = evaluate(make_context(setup=make_setup(direction=Direction.LONG),
                                        confluence=make_confluence(direction=Direction.SHORT)))
        assert result.gates["G4_DIRECTION_CLEAR"] is False
        assert result.decision == Decision.WAIT

    def test_fail_missing_confluence_routes_to_wait(self):
        result = evaluate(make_context(confluence=None))
        assert result.gates["G4_DIRECTION_CLEAR"] is False
        assert result.decision == Decision.WAIT


# ---------------------------------------------------------------------------
# G5 -- No Major Conflict
# ---------------------------------------------------------------------------

class TestG5NoMajorConflict:
    def test_pass_zero_conflicts(self):
        result = evaluate(make_context(confluence=make_confluence(conflicts=[])))
        assert result.gates["G5_NO_MAJOR_CONFLICT"] is True

    def test_pass_one_conflict_still_ok(self):
        result = evaluate(make_context(confluence=make_confluence(conflicts=[EvidenceCategory.MOMENTUM])))
        assert result.gates["G5_NO_MAJOR_CONFLICT"] is True

    def test_fail_two_conflicts_routes_to_no_trade(self):
        result = evaluate(make_context(
            confluence=make_confluence(conflicts=[EvidenceCategory.MOMENTUM, EvidenceCategory.VOLUME])
        ))
        assert result.gates["G5_NO_MAJOR_CONFLICT"] is False
        assert result.decision == Decision.NO_TRADE
        assert result.direction is None


# ---------------------------------------------------------------------------
# G6 -- HTF Acceptable (lenient default)
# ---------------------------------------------------------------------------

class TestG6HtfAcceptable:
    def test_pass_no_htf_conflict(self):
        result = evaluate(make_context(confluence=make_confluence(conflicts=[])))
        assert result.gates["G6_HTF_ACCEPTABLE"] is True

    def test_pass_htf_simply_excluded_lenient_default(self):
        # HTF has no data source this project (Phase 4) -- always excluded,
        # never conflicting. Must not be treated as a veto.
        confluence = make_confluence(conflicts=[])
        assert EvidenceCategory.HTF in confluence.categories_excluded  # confirms the fixture's premise
        result = evaluate(make_context(confluence=confluence))
        assert result.gates["G6_HTF_ACCEPTABLE"] is True

    def test_fail_htf_conflict_routes_to_wait(self):
        result = evaluate(make_context(confluence=make_confluence(
            conflicts=[EvidenceCategory.HTF], categories_available=[EvidenceCategory.TREND, EvidenceCategory.HTF],
            category_scores={EvidenceCategory.TREND: 0.8, EvidenceCategory.HTF: -0.9},
        )))
        assert result.gates["G6_HTF_ACCEPTABLE"] is False
        assert result.decision == Decision.WAIT
        assert "HTF conflict" in result.reasons


# ---------------------------------------------------------------------------
# G7 -- Entry Valid
# ---------------------------------------------------------------------------

class TestG7EntryValid:
    def test_pass(self):
        result = evaluate(make_context(entry_plan=make_entry_plan(structure_clearance_ok=True, is_stale=False)))
        assert result.gates["G7_ENTRY_VALID"] is True

    def test_fail_no_entry_plan_routes_to_wait(self):
        result = evaluate(make_context(entry_plan=None))
        assert result.gates["G7_ENTRY_VALID"] is False
        assert result.decision == Decision.WAIT
        assert "entry plan unavailable" in result.reasons

    def test_fail_stale_entry_routes_to_wait(self):
        result = evaluate(make_context(entry_plan=make_entry_plan(is_stale=True)))
        assert result.gates["G7_ENTRY_VALID"] is False
        assert result.decision == Decision.WAIT
        assert "entry stale" in result.reasons

    def test_fail_structure_clearance_routes_to_wait(self):
        result = evaluate(make_context(entry_plan=make_entry_plan(structure_clearance_ok=False)))
        assert result.gates["G7_ENTRY_VALID"] is False
        assert result.decision == Decision.WAIT
        assert "structure clearance failed" in result.reasons

    def test_fail_chase_distance_exceeded_routes_to_wait(self):
        plan = make_entry_plan(zone_low=99.0, zone_high=100.0, max_chase_distance=1.0)
        result = evaluate(make_context(entry_plan=plan, current_price=105.0))  # way beyond zone+chase
        assert result.gates["G7_ENTRY_VALID"] is False
        assert result.decision == Decision.WAIT
        assert "chase distance exceeded" in result.reasons

    def test_pass_within_chase_distance(self):
        plan = make_entry_plan(zone_low=99.0, zone_high=100.0, max_chase_distance=1.0)
        result = evaluate(make_context(entry_plan=plan, current_price=100.5))
        assert result.gates["G7_ENTRY_VALID"] is True


# ---------------------------------------------------------------------------
# G8 -- Risk Valid
# ---------------------------------------------------------------------------

class TestG8RiskValid:
    def test_pass(self):
        result = evaluate(make_context(risk_plan=make_risk_plan(meets_min_rr=True, target_clearance_ok=True)))
        assert result.gates["G8_RISK_VALID"] is True

    def test_fail_no_risk_plan_routes_to_no_trade(self):
        result = evaluate(make_context(risk_plan=None))
        assert result.gates["G8_RISK_VALID"] is False
        assert result.decision == Decision.NO_TRADE
        assert "risk plan unavailable" in result.reasons

    def test_fail_min_rr_routes_to_no_trade(self):
        result = evaluate(make_context(risk_plan=make_risk_plan(meets_min_rr=False)))
        assert result.gates["G8_RISK_VALID"] is False
        assert result.decision == Decision.NO_TRADE
        assert "minimum R:R failed" in result.reasons

    def test_fail_target_clearance_routes_to_no_trade(self):
        result = evaluate(make_context(risk_plan=make_risk_plan(target_clearance_ok=False)))
        assert result.gates["G8_RISK_VALID"] is False
        assert result.decision == Decision.NO_TRADE
        assert "TP blocked by opposing structure" in result.reasons


# ---------------------------------------------------------------------------
# G9 -- Quality Threshold
# ---------------------------------------------------------------------------

class TestG9QualityThreshold:
    def test_pass_grade_a(self):
        result = evaluate(make_context(confluence=make_confluence(score=80.0)))
        assert result.gates["G9_QUALITY_THRESHOLD"] is True
        assert result.quality_grade == QualityGrade.A

    def test_pass_grade_b_boundary(self):
        result = evaluate(make_context(confluence=make_confluence(score=60.0)))
        assert result.gates["G9_QUALITY_THRESHOLD"] is True
        assert result.quality_grade == QualityGrade.B

    def test_fail_grade_c_routes_to_wait(self):
        result = evaluate(make_context(confluence=make_confluence(score=50.0)))
        assert result.gates["G9_QUALITY_THRESHOLD"] is False
        assert result.decision == Decision.WAIT
        assert result.quality_grade == QualityGrade.C

    def test_fail_grade_f_routes_to_no_trade(self):
        result = evaluate(make_context(confluence=make_confluence(score=20.0)))
        assert result.gates["G9_QUALITY_THRESHOLD"] is False
        assert result.decision == Decision.NO_TRADE
        assert result.quality_grade == QualityGrade.F
        assert "insufficient quality score" in result.reasons

    def test_score_never_treated_as_probability_in_reasons_or_warnings(self):
        result = evaluate(make_context(confluence=make_confluence(score=80.0)))
        full_text = " ".join(result.reasons + result.warnings).lower()
        assert "probability" not in full_text
        assert "confidence" not in full_text
        assert "win rate" not in full_text and "win-rate" not in full_text


# ---------------------------------------------------------------------------
# G10 -- Signal Fresh
# ---------------------------------------------------------------------------

class TestG10SignalFresh:
    def test_pass_evaluated_immediately(self):
        result = evaluate(make_context(as_of=NOW, evaluated_at=NOW))
        assert result.gates["G10_SIGNAL_FRESH"] is True

    def test_fail_evaluated_long_after_routes_to_no_trade(self):
        result = evaluate(make_context(as_of=NOW, evaluated_at=NOW + timedelta(hours=5), timeframe=Timeframe.M1))
        assert result.gates["G10_SIGNAL_FRESH"] is False
        assert result.decision == Decision.NO_TRADE
        assert "signal data no longer fresh" in result.reasons

    def test_pass_shortly_after(self):
        result = evaluate(make_context(as_of=NOW, evaluated_at=NOW + timedelta(minutes=1), timeframe=Timeframe.M1))
        assert result.gates["G10_SIGNAL_FRESH"] is True

    def test_freshness_is_timeframe_aware(self):
        # the same elapsed time is fresh on H1 but stale on M1
        elapsed = NOW + timedelta(minutes=10)
        m1_result = evaluate(make_context(as_of=NOW, evaluated_at=elapsed, timeframe=Timeframe.M1))
        h1_result = evaluate(make_context(as_of=NOW, evaluated_at=elapsed, timeframe=Timeframe.H1))
        assert m1_result.gates["G10_SIGNAL_FRESH"] is False
        assert h1_result.gates["G10_SIGNAL_FRESH"] is True


# ---------------------------------------------------------------------------
# Successful LONG/SHORT decisions
# ---------------------------------------------------------------------------

class TestCleanDecisions:
    def test_clean_confirmed_long(self):
        result = evaluate(make_context(setup=make_setup(direction=Direction.LONG),
                                        confluence=make_confluence(direction=Direction.LONG),
                                        entry_plan=make_entry_plan(direction=Direction.LONG)))
        assert result.decision == Decision.LONG
        assert result.direction == Direction.LONG
        assert all(result.gates.values())

    def test_clean_confirmed_short(self):
        result = evaluate(make_context(setup=make_setup(direction=Direction.SHORT),
                                        confluence=make_confluence(direction=Direction.SHORT),
                                        entry_plan=make_entry_plan(direction=Direction.SHORT)))
        assert result.decision == Decision.SHORT
        assert result.direction == Direction.SHORT
        assert all(result.gates.values())


# ---------------------------------------------------------------------------
# Integration scenarios (the 11 required cases)
# ---------------------------------------------------------------------------

class TestIntegrationScenarios:
    def test_1_clean_confirmed_long(self):
        result = evaluate(make_context())
        assert result.decision == Decision.LONG

    def test_2_clean_confirmed_short(self):
        result = evaluate(make_context(setup=make_setup(direction=Direction.SHORT),
                                        confluence=make_confluence(direction=Direction.SHORT),
                                        entry_plan=make_entry_plan(direction=Direction.SHORT)))
        assert result.decision == Decision.SHORT

    def test_3_forming_setup_waits(self):
        result = evaluate(make_context(setup=make_setup(confirmation_met=False)))
        assert result.decision == Decision.WAIT

    def test_4_no_setup_no_trade(self):
        result = evaluate(make_context(setup=None, confluence=None, entry_plan=None, risk_plan=None))
        assert result.decision == Decision.NO_TRADE

    def test_5_unavailable_data_no_trade(self):
        result = evaluate(make_context(data_quality=make_data_quality(DataQualityState.UNAVAILABLE)))
        assert result.decision == Decision.NO_TRADE

    def test_6_strong_conflict_no_trade(self):
        result = evaluate(make_context(
            confluence=make_confluence(conflicts=[EvidenceCategory.MOMENTUM, EvidenceCategory.STRUCTURE])
        ))
        assert result.decision == Decision.NO_TRADE

    def test_7_stale_entry_waits(self):
        result = evaluate(make_context(entry_plan=make_entry_plan(is_stale=True)))
        assert result.decision == Decision.WAIT

    def test_8_invalid_rr_no_trade(self):
        result = evaluate(make_context(risk_plan=make_risk_plan(meets_min_rr=False)))
        assert result.decision == Decision.NO_TRADE

    def test_9_low_quality_score_routing(self):
        # 40-59 -> WAIT
        result = evaluate(make_context(confluence=make_confluence(score=45.0)))
        assert result.decision == Decision.WAIT
        # <40 -> NO_TRADE
        result2 = evaluate(make_context(confluence=make_confluence(score=10.0)))
        assert result2.decision == Decision.NO_TRADE

    def test_10_htf_disagreement_current_lenient_policy(self):
        result = evaluate(make_context(confluence=make_confluence(
            conflicts=[EvidenceCategory.HTF], categories_available=[EvidenceCategory.TREND, EvidenceCategory.HTF],
            category_scores={EvidenceCategory.TREND: 0.8, EvidenceCategory.HTF: -0.9},
        )))
        assert result.decision == Decision.WAIT  # lenient default: conflict -> WAIT, not NO_TRADE

    def test_11_adversarial_no_edge_scenario_never_manufactures_direction(self):
        # Every gate individually near a boundary/failing, direction data
        # deliberately absent/contradictory -- must never produce LONG/SHORT.
        result = evaluate(make_context(
            setup=make_setup(prerequisites_met=True, confirmation_met=True),
            confluence=make_confluence(score=41.0, conflicts=[EvidenceCategory.MOMENTUM]),
            entry_plan=make_entry_plan(structure_clearance_ok=True, is_stale=False),
            risk_plan=make_risk_plan(meets_min_rr=True, target_clearance_ok=True),
        ))
        assert result.decision in (Decision.WAIT, Decision.NO_TRADE)
        assert result.direction is None


# ---------------------------------------------------------------------------
# Critical regressions
# ---------------------------------------------------------------------------

class TestCriticalRegressions:
    def test_no_long_or_short_without_every_gate_passing(self):
        # Fuzz: randomly fail exactly one gate's underlying condition at a
        # time (holding everything else at a passing default) and confirm
        # Decision is never LONG/SHORT.
        overrides_that_should_block = [
            dict(data_quality=make_data_quality(DataQualityState.UNAVAILABLE)),
            dict(setup=None, confluence=None, entry_plan=None, risk_plan=None),
            dict(setup=make_setup(confirmation_met=False)),
            dict(confluence=make_confluence(direction=Direction.SHORT)),  # mismatched vs LONG setup
            dict(confluence=make_confluence(conflicts=[EvidenceCategory.MOMENTUM, EvidenceCategory.VOLUME])),
            dict(confluence=make_confluence(
                conflicts=[EvidenceCategory.HTF], categories_available=[EvidenceCategory.TREND, EvidenceCategory.HTF],
                category_scores={EvidenceCategory.TREND: 0.8, EvidenceCategory.HTF: -0.9},
            )),
            dict(entry_plan=None),
            dict(entry_plan=make_entry_plan(is_stale=True)),
            dict(risk_plan=make_risk_plan(meets_min_rr=False)),
            dict(confluence=make_confluence(score=10.0)),
            dict(evaluated_at=NOW + timedelta(hours=10)),
        ]
        for overrides in overrides_that_should_block:
            result = evaluate(make_context(**overrides))
            assert result.decision not in (Decision.LONG, Decision.SHORT), f"unexpectedly decided with {overrides}"
            assert result.direction is None

    def test_no_ch24h_or_any_price_change_tiebreak_exists(self):
        import inspect
        source = inspect.getsource(evaluate)
        assert "ch_24h" not in source
        assert "price_change" not in source.replace("current_price", "")

    def test_no_vote_margin_or_majority_fallback(self):
        # Checks evaluate()'s own function body specifically, not the
        # module docstring -- the docstring deliberately explains, in
        # prose, that these patterns don't exist (which would otherwise
        # make a naive whole-module text search self-defeating).
        import inspect
        function_source = inspect.getsource(evaluate)
        for forbidden in ("vote_margin", "majority", "default_long", "default_short", "best_available"):
            assert forbidden not in function_source.lower().replace(" ", "_")

    def test_decision_direction_consistency_across_random_fuzzing(self):
        rng = random.Random(7)
        violations = 0
        for _ in range(300):
            ctx = make_context(
                data_quality=make_data_quality(rng.choice(list(DataQualityState))),
                setup=rng.choice([None, make_setup(confirmation_met=rng.choice([True, False]),
                                                     direction=rng.choice([Direction.LONG, Direction.SHORT]))]),
                confluence=make_confluence(
                    direction=rng.choice([Direction.LONG, Direction.SHORT]),
                    score=rng.uniform(0, 100),
                    conflicts=rng.sample(list(EvidenceCategory), k=rng.randint(0, 3)),
                ),
                entry_plan=rng.choice([None, make_entry_plan(
                    structure_clearance_ok=rng.choice([True, False]), is_stale=rng.choice([True, False])
                )]),
                risk_plan=rng.choice([None, make_risk_plan(
                    meets_min_rr=rng.choice([True, False]), target_clearance_ok=rng.choice([True, False])
                )]),
                evaluated_at=NOW + timedelta(minutes=rng.choice([0, 1, 500])),
            )
            result = evaluate(ctx)
            if result.decision in (Decision.WAIT, Decision.NO_TRADE) and result.direction is not None:
                violations += 1
            if result.decision in (Decision.LONG, Decision.SHORT):
                if result.direction is None or not all(result.gates.values()):
                    violations += 1
        assert violations == 0

    def test_gates_dict_never_empty(self):
        result = evaluate(make_context())
        assert len(result.gates) > 0
        result2 = evaluate(make_context(data_quality=make_data_quality(DataQualityState.UNAVAILABLE)))
        assert len(result2.gates) > 0  # at least G1 recorded, even on the earliest possible failure

    def test_quality_grade_always_present_even_on_early_failure(self):
        result = evaluate(make_context(data_quality=make_data_quality(DataQualityState.UNAVAILABLE)))
        assert result.quality_grade is not None
