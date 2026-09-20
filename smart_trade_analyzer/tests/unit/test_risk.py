"""Tests for risk/ -- stop_loss, targets, position_sizing, leverage, and
the risk_engine orchestrator.
"""
import pytest

from smart_trade_analyzer.contracts import Direction
from smart_trade_analyzer.risk.leverage import (
    COMFORT_LEVERAGE_FRACTION, LEVERAGE_SAFETY_MARGIN, MAX_LEVERAGE_HARD_CAP, MIN_LEVERAGE,
    suggest_max_safe_leverage,
)
from smart_trade_analyzer.risk.position_sizing import compute_position_size
from smart_trade_analyzer.risk.risk_engine import MIN_RR_HARD_GATE, build_risk_plan
from smart_trade_analyzer.risk.stop_loss import SL_MIN_ATR_MULTIPLE, compute_stop_loss
from smart_trade_analyzer.risk.targets import (
    TARGET_CLEARANCE_BUFFER_ATR_MULTIPLE, TP1_ATR_MULTIPLE, TP2_ATR_MULTIPLE, compute_targets,
)


# ---------------------------------------------------------------------------
# Stop loss
# ---------------------------------------------------------------------------

class TestStopLoss:
    def test_pure_atr_floor_when_no_structure(self):
        sl = compute_stop_loss(Direction.LONG, 100.0, 2.0, None, None)
        assert sl == pytest.approx(100.0 - SL_MIN_ATR_MULTIPLE * 2.0)

    def test_uses_structure_plus_buffer_when_farther_than_floor(self):
        sl = compute_stop_loss(Direction.LONG, 100.0, 2.0, swing_support=95.0, swing_resistance=None)
        assert sl == pytest.approx(95.0 - 0.2 * 2.0)

    def test_swing_sl_too_close_falls_back_to_atr_floor(self):
        # structure only 0.1 away -- floor (1xATR=2.0) must win, not the tiny structure distance
        sl = compute_stop_loss(Direction.LONG, 100.0, 2.0, swing_support=99.9, swing_resistance=None)
        assert sl == pytest.approx(100.0 - SL_MIN_ATR_MULTIPLE * 2.0)
        assert abs(100.0 - sl) >= SL_MIN_ATR_MULTIPLE * 2.0

    def test_minimum_distance_invariant_holds_across_many_inputs(self):
        for support in (None, 99.9, 99.0, 95.0, 80.0, 101.0, 150.0):  # includes wrong-side cases
            sl = compute_stop_loss(Direction.LONG, 100.0, 2.0, support, None)
            assert abs(100.0 - sl) >= SL_MIN_ATR_MULTIPLE * 2.0 - 1e-9

    def test_wrong_side_structure_is_ignored_not_used_as_a_stop(self):
        # swing_support ABOVE entry for a LONG makes no sense as a stop reference
        sl = compute_stop_loss(Direction.LONG, 100.0, 2.0, swing_support=105.0, swing_resistance=None)
        assert sl == pytest.approx(100.0 - SL_MIN_ATR_MULTIPLE * 2.0)

    def test_long_short_symmetry(self):
        long_sl = compute_stop_loss(Direction.LONG, 100.0, 2.0, swing_support=95.0, swing_resistance=None)
        short_sl = compute_stop_loss(Direction.SHORT, 100.0, 2.0, swing_support=None, swing_resistance=105.0)
        assert abs(100.0 - long_sl) == pytest.approx(abs(short_sl - 100.0))

    def test_long_sl_always_below_entry(self):
        for support in (None, 90.0, 99.99, 105.0):
            assert compute_stop_loss(Direction.LONG, 100.0, 2.0, support, None) < 100.0

    def test_short_sl_always_above_entry(self):
        for resistance in (None, 110.0, 100.01, 95.0):
            assert compute_stop_loss(Direction.SHORT, 100.0, 2.0, None, resistance) > 100.0


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class TestTargets:
    def test_no_structure_uses_raw_atr_multiples(self):
        r = compute_targets(Direction.LONG, 100.0, 2.0, None, None)
        assert r.take_profit_1 == pytest.approx(100.0 + TP1_ATR_MULTIPLE * 2.0)
        assert r.take_profit_2 == pytest.approx(100.0 + TP2_ATR_MULTIPLE * 2.0)
        assert r.target_clearance_ok is True
        assert r.warnings == []

    def test_tp1_before_tp2_long(self):
        r = compute_targets(Direction.LONG, 100.0, 2.0, None, None)
        assert r.take_profit_1 < r.take_profit_2

    def test_tp1_before_tp2_short_means_numerically_greater(self):
        r = compute_targets(Direction.SHORT, 100.0, 2.0, None, None)
        assert r.take_profit_1 > r.take_profit_2  # "before" for SHORT means closer to entry, i.e. numerically higher

    def test_target_direction_correctness(self):
        long_r = compute_targets(Direction.LONG, 100.0, 2.0, None, None)
        short_r = compute_targets(Direction.SHORT, 100.0, 2.0, None, None)
        assert long_r.take_profit_1 > 100.0 and long_r.take_profit_2 > 100.0
        assert short_r.take_profit_1 < 100.0 and short_r.take_profit_2 < 100.0

    def test_opposing_structure_caps_tp1_with_room(self):
        r = compute_targets(Direction.LONG, 100.0, 2.0, None, swing_resistance=102.0)
        expected = 102.0 - TARGET_CLEARANCE_BUFFER_ATR_MULTIPLE * 2.0
        assert r.take_profit_1 == pytest.approx(expected)
        assert any("capped" in w for w in r.warnings)

    def test_opposing_structure_too_close_blocks_without_fabricating_a_safe_number(self):
        r = compute_targets(Direction.LONG, 100.0, 2.0, None, swing_resistance=100.1)
        assert r.target_clearance_ok is False
        assert r.take_profit_1 == pytest.approx(100.0 + TP1_ATR_MULTIPLE * 2.0)  # raw, honest, flagged
        assert any("blocked" in w for w in r.warnings)

    def test_structure_between_tp1_and_tp2_only_caps_tp2(self):
        r = compute_targets(Direction.LONG, 100.0, 2.0, None, swing_resistance=104.0)
        assert r.take_profit_1 == pytest.approx(103.0)  # unaffected
        assert r.take_profit_2 == pytest.approx(104.0 - TARGET_CLEARANCE_BUFFER_ATR_MULTIPLE * 2.0)
        assert r.target_clearance_ok is True

    def test_structure_beyond_both_targets_does_not_cap_anything(self):
        r = compute_targets(Direction.LONG, 100.0, 2.0, None, swing_resistance=200.0)
        assert r.take_profit_1 == pytest.approx(103.0)
        assert r.take_profit_2 == pytest.approx(105.0)
        assert r.target_clearance_ok is True

    def test_long_short_symmetry(self):
        long_r = compute_targets(Direction.LONG, 100.0, 2.0, None, swing_resistance=102.0)
        short_r = compute_targets(Direction.SHORT, 100.0, 2.0, swing_support=98.0, swing_resistance=None)
        assert abs(long_r.take_profit_1 - 100.0) == pytest.approx(abs(100.0 - short_r.take_profit_1))
        assert long_r.target_clearance_ok == short_r.target_clearance_ok

    def test_tp_distance_never_reads_a_score_or_grade(self):
        # structural guarantee, not just behavioral: the function signature
        # itself has no score/grade/confidence parameter to read from.
        import inspect
        params = inspect.signature(compute_targets).parameters
        assert not any("score" in p or "grade" in p or "confidence" in p or "quality" in p for p in params)


# ---------------------------------------------------------------------------
# Position sizing
#
# CONVENTION (audited and fixed -- see HANDOFF.md's Phase 5 audit-fix
# record): risk_pct is a PERCENTAGE NUMBER, not a fraction. risk_pct=1.0
# means "risk 1% of the account", never "risk 100%". Every test name below
# says so explicitly, on purpose -- this is the exact class of bug the
# audit caught, and it should be impossible to misread these tests the
# same way the original implementation was.
# ---------------------------------------------------------------------------

class TestPositionSizing:
    def test_risk_pct_one_point_zero_means_one_percent_not_one_hundred_percent(self):
        # The audit's own worked example: 10,000 balance, risk_pct=1.0,
        # 2-unit stop distance -> risk exactly $100 (1%), sized as 50 units
        # -- NOT $10,000 (100%), which the original Phase 5 formula would
        # have produced.
        result = compute_position_size(10_000, 1.0, 100.0, 98.0)
        assert result == pytest.approx(50.0)
        catastrophic_100_pct_reading = 10_000 / 2.0  # what risk_pct=1.0-as-fraction would wrongly give
        assert result != pytest.approx(catastrophic_100_pct_reading)
        assert result == pytest.approx(catastrophic_100_pct_reading / 100.0)

    def test_half_percent_risk(self):
        # risk_pct=0.5 -> 0.5% of 10,000 = $50 risk / 2-unit stop = 25 units
        assert compute_position_size(10_000, 0.5, 100.0, 98.0) == pytest.approx(25.0)

    def test_one_percent_risk(self):
        assert compute_position_size(10_000, 1.0, 100.0, 98.0) == pytest.approx(50.0)

    def test_two_percent_risk(self):
        # 2% of 10,000 = $200 / 2-unit stop = 100 units
        assert compute_position_size(10_000, 2.0, 100.0, 98.0) == pytest.approx(100.0)

    def test_risk_pct_scales_linearly(self):
        one_pct = compute_position_size(10_000, 1.0, 100.0, 98.0)
        two_pct = compute_position_size(10_000, 2.0, 100.0, 98.0)
        assert two_pct == pytest.approx(one_pct * 2)

    def test_zero_stop_distance_returns_none(self):
        assert compute_position_size(10_000, 1.0, 100.0, 100.0) is None

    def test_non_positive_balance_or_risk_returns_none(self):
        assert compute_position_size(0, 1.0, 100.0, 98.0) is None
        assert compute_position_size(-500, 1.0, 100.0, 98.0) is None
        assert compute_position_size(10_000, 0.0, 100.0, 98.0) is None
        assert compute_position_size(10_000, -1.0, 100.0, 98.0) is None

    def test_boundary_risk_pct_at_exactly_one_hundred(self):
        # 100% is the absolute maximum sane value -- still accepted (it's
        # the boundary, not beyond it), but produces the entire balance as
        # the risk amount, which is a legitimate (if aggressive) input.
        result = compute_position_size(10_000, 100.0, 100.0, 98.0)
        assert result == pytest.approx(10_000 / 2.0)

    def test_boundary_risk_pct_beyond_one_hundred_is_rejected(self):
        # risking more than 100% of the account on a single stop-loss is
        # not a sane input -- must not silently size an impossible position.
        assert compute_position_size(10_000, 100.01, 100.0, 98.0) is None
        assert compute_position_size(10_000, 500.0, 100.0, 98.0) is None

    def test_never_reads_score_or_grade(self):
        import inspect
        params = inspect.signature(compute_position_size).parameters
        assert not any("score" in p or "grade" in p or "confidence" in p for p in params)

    def test_wider_stop_yields_smaller_position(self):
        tight = compute_position_size(10_000, 1.0, 100.0, 99.0)
        wide = compute_position_size(10_000, 1.0, 100.0, 90.0)
        assert wide < tight

    def test_long_short_symmetry(self):
        # position sizing only depends on |entry - stop|, not direction --
        # LONG (stop below) and SHORT (stop above) by the same distance
        # must size identically.
        long_size = compute_position_size(10_000, 1.0, 100.0, 98.0)
        short_size = compute_position_size(10_000, 1.0, 100.0, 102.0)
        assert long_size == pytest.approx(short_size)


# ---------------------------------------------------------------------------
# Leverage
#
# Audit finding #2 fix: the liquidation-vs-SL formula itself is confirmed
# by the specification's own text as the intended mechanism (see
# leverage.py's module docstring) -- what was genuinely missing was an
# absolute upper cap (MAX_LEVERAGE_HARD_CAP) and a lower floor
# (MIN_LEVERAGE), both now enforced unconditionally.
# ---------------------------------------------------------------------------

class TestLeverage:
    def test_normal_sl_distance_uses_the_formula_under_the_cap(self):
        lev = suggest_max_safe_leverage(100.0, 95.0)  # 5% distance, well under the cap
        expected = 1.0 / (0.05 * (1.0 + LEVERAGE_SAFETY_MARGIN))
        assert lev.max_safe_leverage == pytest.approx(expected)
        assert lev.max_safe_leverage < MAX_LEVERAGE_HARD_CAP

    def test_very_tight_sl_hits_the_hard_cap_not_an_unbounded_formula_value(self):
        # 0.1% distance would formula-compute to roughly 416x -- must be
        # capped, not returned as-is. This is the exact defect the audit
        # found: the original formula had no upper bound at all.
        lev = suggest_max_safe_leverage(100.0, 99.9)
        assert lev.max_safe_leverage == pytest.approx(MAX_LEVERAGE_HARD_CAP)

    def test_hard_cap_is_never_exceeded_across_a_wide_sweep_of_sl_distances(self):
        for pct in (0.0001, 0.0005, 0.001, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1):
            lev = suggest_max_safe_leverage(100.0, 100.0 * (1 - pct))
            assert lev.max_safe_leverage <= MAX_LEVERAGE_HARD_CAP + 1e-9
            assert lev.comfort_leverage <= MAX_LEVERAGE_HARD_CAP

    def test_very_wide_sl_hits_the_minimum_leverage_floor(self):
        # 90% distance would formula-compute to below 1x -- floored, since
        # sub-1x leverage is not a meaningful suggestion in this model.
        lev = suggest_max_safe_leverage(100.0, 10.0)
        assert lev.max_safe_leverage == pytest.approx(MIN_LEVERAGE)

    def test_minimum_leverage_never_violated_across_a_wide_sweep(self):
        for pct in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99):
            lev = suggest_max_safe_leverage(100.0, 100.0 * (1 - pct))
            assert lev.max_safe_leverage >= MIN_LEVERAGE - 1e-9

    def test_invalid_zero_sl_distance_does_not_crash_or_return_infinity(self):
        # entry == stop_loss -- degenerate input, no real protection at
        # all; must resolve to a finite value and must not crash. Floored
        # at MIN_LEVERAGE (the conservative choice), not the hard cap --
        # a zero-distance stop is not "very tight protection", it's no
        # protection, so the safest possible suggestion applies.
        import math
        lev = suggest_max_safe_leverage(100.0, 100.0)
        assert math.isfinite(lev.max_safe_leverage)
        assert lev.max_safe_leverage == pytest.approx(MIN_LEVERAGE)

    def test_comfort_is_a_fraction_of_ceiling(self):
        lev = suggest_max_safe_leverage(100.0, 95.0)
        assert lev.comfort_leverage == pytest.approx(lev.max_safe_leverage * COMFORT_LEVERAGE_FRACTION)

    def test_comfort_never_exceeds_ceiling_including_at_both_bounds(self):
        for sl in (99.9, 99.0, 95.0, 80.0, 50.0, 10.0):  # spans hard-cap, normal, and floor regions
            lev = suggest_max_safe_leverage(100.0, sl)
            assert lev.comfort_leverage <= lev.max_safe_leverage

    def test_tighter_stop_allows_more_leverage_within_the_uncapped_region(self):
        tight = suggest_max_safe_leverage(100.0, 97.0)   # 3% -- still under the cap
        wide = suggest_max_safe_leverage(100.0, 90.0)    # 10% -- also under the cap
        assert tight.max_safe_leverage > wide.max_safe_leverage

    def test_symmetric_for_short(self):
        long_lev = suggest_max_safe_leverage(100.0, 98.0)
        short_lev = suggest_max_safe_leverage(100.0, 102.0)
        assert long_lev.max_safe_leverage == pytest.approx(short_lev.max_safe_leverage)

    def test_symmetric_for_short_at_the_hard_cap(self):
        long_lev = suggest_max_safe_leverage(100.0, 99.9)
        short_lev = suggest_max_safe_leverage(100.0, 100.1)
        assert long_lev.max_safe_leverage == pytest.approx(short_lev.max_safe_leverage)
        assert long_lev.max_safe_leverage == pytest.approx(MAX_LEVERAGE_HARD_CAP)


# ---------------------------------------------------------------------------
# Risk Engine orchestrator
# ---------------------------------------------------------------------------

class TestRiskEngine:
    def test_valid_rr_passes(self):
        # SL floor 1xATR=2, TP1=1.5xATR=3 -> R:R=1.5 >= MIN_RR_HARD_GATE(1.2)
        plan = build_risk_plan(Direction.LONG, 100.0, 2.0, None, None)
        assert plan.meets_min_rr is True
        assert plan.risk_reward_1 == pytest.approx(TP1_ATR_MULTIPLE / SL_MIN_ATR_MULTIPLE)

    def test_invalid_rr_fails(self):
        # widen SL via close structure to push R:R below the gate
        plan2 = build_risk_plan(Direction.LONG, 100.0, 2.0, swing_support=90.0, swing_resistance=None)
        # SL = 90 - 0.4 = 89.6, distance=10.4, TP1=103, reward=3, R:R=3/10.4 << 1.2
        assert plan2.meets_min_rr is False
        assert any("R:R" in w for w in plan2.warnings)

    def test_exact_boundary(self):
        assert MIN_RR_HARD_GATE == pytest.approx(1.2)

    def test_long_short_symmetry(self):
        long_plan = build_risk_plan(Direction.LONG, 100.0, 2.0, swing_support=97.0, swing_resistance=None)
        short_plan = build_risk_plan(Direction.SHORT, 100.0, 2.0, swing_support=None, swing_resistance=103.0)
        assert long_plan.risk_reward_1 == pytest.approx(short_plan.risk_reward_1)
        assert long_plan.meets_min_rr == short_plan.meets_min_rr

    def test_position_size_none_when_account_info_not_supplied(self):
        plan = build_risk_plan(Direction.LONG, 100.0, 2.0, None, None)
        assert plan.position_size is None

    def test_position_size_present_when_account_info_supplied(self):
        plan = build_risk_plan(Direction.LONG, 100.0, 2.0, None, None, account_balance=10_000, risk_pct=1.0)
        assert plan.position_size is not None
        assert plan.position_size > 0

    def test_position_size_uses_percentage_convention_end_to_end(self):
        # 1.0 through the full risk_engine orchestrator must mean 1%, same
        # as calling compute_position_size directly -- no re-interpretation
        # happens at the orchestration layer.
        plan = build_risk_plan(Direction.LONG, 100.0, 2.0, None, None, account_balance=10_000, risk_pct=1.0)
        direct = compute_position_size(10_000, 1.0, 100.0, plan.stop_loss)
        assert plan.position_size == pytest.approx(direct)

    def test_position_size_never_influenced_by_a_score(self):
        import inspect
        params = inspect.signature(build_risk_plan).parameters
        assert not any("score" in p or "grade" in p or "confidence" in p for p in params)

    def test_max_safe_leverage_never_none(self):
        plan = build_risk_plan(Direction.LONG, 100.0, 2.0, None, None)
        assert plan.max_safe_leverage is not None and plan.max_safe_leverage > 0

    def test_max_safe_leverage_respects_the_hard_cap(self):
        from smart_trade_analyzer.risk.leverage import MAX_LEVERAGE_HARD_CAP
        # a very tight ATR-floor stop relative to entry should still be capped
        plan = build_risk_plan(Direction.LONG, 100.0, 0.05, None, None)  # tiny ATR -> tiny SL distance
        assert plan.max_safe_leverage <= MAX_LEVERAGE_HARD_CAP + 1e-9

    def test_target_clearance_propagates_from_targets_module(self):
        plan = build_risk_plan(Direction.LONG, 100.0, 2.0, None, swing_resistance=100.1)
        assert plan.target_clearance_ok is False
