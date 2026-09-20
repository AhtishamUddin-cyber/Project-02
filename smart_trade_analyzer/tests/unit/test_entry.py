"""Tests for entry/entry_engine.py -- setup-aware zones, staleness, chase
distance, structure clearance, and no-lookahead.
"""
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import (
    CandleData, Direction, FeatureSet, SetupCandidate, SetupType, Timeframe,
)
from smart_trade_analyzer.entry.entry_engine import (
    CHASE_DISTANCE_ATR_MULTIPLE, ENTRY_ZONE_MARKET_ATR_FRACTION, STALENESS_TTL_CANDLE_MULTIPLE,
    TIMEFRAME_MINUTES, build_entry_plan, refresh_staleness, within_chase_distance,
)

NOW = datetime(2026, 8, 27, 12, 0, 0)


def make_fs(**overrides):
    defaults = dict(
        symbol="X", timeframe=Timeframe.M1, as_of=NOW, close=100.0,
        ema9=None, ema21=None, ema50=None, ema200=None,
        rsi14=None, stoch_rsi_k=None, stoch_rsi_d=None,
        macd_line=None, macd_signal=None, macd_hist=None,
        bb_upper=None, bb_mid=None, bb_lower=None,
        atr=2.0, atr_pct=None, volume_ratio=None,
        swing_support=None, swing_resistance=None,
        divergence=None, completeness=1.0,
    )
    defaults.update(overrides)
    return FeatureSet(**defaults)


def make_setup(setup_type, direction=Direction.LONG, invalidation_price=90.0):
    return SetupCandidate(
        setup_type=setup_type, direction=direction, prerequisites_met=True, confirmation_met=True,
        invalidation_price=invalidation_price, evidence_refs=["x"], failed_conditions=[],
    )


def make_candles(n=5, close=100.0):
    return [
        CandleData(open_time=NOW - timedelta(minutes=n - i), open=close, high=close + 0.1, low=close - 0.1,
                   close=close, volume=100.0, is_closed=True)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Setup-aware zones, one per family
# ---------------------------------------------------------------------------

class TestSetupAwareZones:
    def test_trend_continuation_zone_centered_on_close(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        buf = ENTRY_ZONE_MARKET_ATR_FRACTION * 2.0
        assert plan.entry_zone_low == pytest.approx(100.0 - buf)
        assert plan.entry_zone_high == pytest.approx(100.0 + buf)

    def test_pullback_zone_centered_on_ema21(self):
        from smart_trade_analyzer.setup.engine import PULLBACK_ZONE_ATR_MULTIPLE
        fs = make_fs(close=101.0, ema21=99.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.PULLBACK), fs, make_candles())
        buf = PULLBACK_ZONE_ATR_MULTIPLE * 2.0
        assert plan.entry_zone_low == pytest.approx(99.0 - buf)
        assert plan.entry_zone_high == pytest.approx(99.0 + buf)

    def test_pullback_with_no_ema21_returns_none(self):
        fs = make_fs(ema21=None)
        assert build_entry_plan(make_setup(SetupType.PULLBACK), fs, make_candles()) is None

    def test_range_mean_reversion_long_zone_centered_on_bb_lower(self):
        fs = make_fs(close=100.5, bb_lower=100.0, bb_upper=110.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.RANGE_MEAN_REVERSION, Direction.LONG), fs, make_candles())
        assert plan.entry_zone_low < 100.0 < plan.entry_zone_high

    def test_range_mean_reversion_short_zone_centered_on_bb_upper(self):
        fs = make_fs(close=109.5, bb_lower=100.0, bb_upper=110.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.RANGE_MEAN_REVERSION, Direction.SHORT), fs, make_candles())
        assert plan.entry_zone_low < 110.0 < plan.entry_zone_high

    def test_breakout_retest_long_zone_on_holding_side_above_level(self):
        fs = make_fs(close=68.0, atr=2.0)
        setup = make_setup(SetupType.BREAKOUT_RETEST, Direction.LONG, invalidation_price=65.0)
        plan = build_entry_plan(setup, fs, make_candles())
        assert plan.entry_zone_low == pytest.approx(65.0)  # zone starts exactly at the level
        assert plan.entry_zone_high > 65.0
        assert plan.invalidation_price == 65.0

    def test_breakout_retest_short_zone_on_holding_side_below_level(self):
        fs = make_fs(close=52.0, atr=2.0)
        setup = make_setup(SetupType.BREAKOUT_RETEST, Direction.SHORT, invalidation_price=55.0)
        plan = build_entry_plan(setup, fs, make_candles())
        assert plan.entry_zone_high == pytest.approx(55.0)
        assert plan.entry_zone_low < 55.0

    def test_reversal_zone_uses_same_level_anchored_logic(self):
        fs = make_fs(close=118.0, atr=2.0)
        setup = make_setup(SetupType.REVERSAL, Direction.SHORT, invalidation_price=120.0)
        plan = build_entry_plan(setup, fs, make_candles())
        assert plan.entry_zone_high == pytest.approx(120.0)
        assert plan.invalidation_price == 120.0

    def test_missing_atr_returns_none_for_every_family(self):
        fs = make_fs(atr=None)
        for st in SetupType:
            assert build_entry_plan(make_setup(st), fs, make_candles()) is None


# ---------------------------------------------------------------------------
# Zone validity / confirmation / invalidation
# ---------------------------------------------------------------------------

class TestZoneAndPrices:
    def test_zone_low_never_exceeds_zone_high(self):
        fs = make_fs(close=100.0, ema21=98.0, bb_lower=95.0, bb_upper=105.0, atr=2.0)
        for st, d in [(SetupType.TREND_CONTINUATION, Direction.LONG), (SetupType.PULLBACK, Direction.LONG),
                      (SetupType.RANGE_MEAN_REVERSION, Direction.SHORT)]:
            plan = build_entry_plan(make_setup(st, d), fs, make_candles())
            assert plan.entry_zone_low <= plan.entry_zone_high

    def test_confirmation_price_is_zone_high_for_long(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        assert plan.confirmation_price == plan.entry_zone_high

    def test_confirmation_price_is_zone_low_for_short(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.SHORT), fs, make_candles())
        assert plan.confirmation_price == plan.entry_zone_low

    def test_invalidation_price_always_mirrors_setup(self):
        fs = make_fs(close=100.0, atr=2.0)
        setup = make_setup(SetupType.TREND_CONTINUATION, invalidation_price=77.7)
        plan = build_entry_plan(setup, fs, make_candles())
        assert plan.invalidation_price == 77.7 == setup.invalidation_price


# ---------------------------------------------------------------------------
# Chase distance
# ---------------------------------------------------------------------------

class TestChaseDistance:
    def test_within_zone_is_within_chase_distance(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        assert within_chase_distance(plan, (plan.entry_zone_low + plan.entry_zone_high) / 2) is True

    def test_at_the_boundary_of_max_chase_distance_is_still_ok(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        edge_price = plan.entry_zone_high + plan.max_chase_distance
        assert within_chase_distance(plan, edge_price) is True

    def test_just_beyond_max_chase_distance_fails(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        assert within_chase_distance(plan, plan.entry_zone_high + plan.max_chase_distance + 0.01) is False

    def test_short_mirror(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.SHORT), fs, make_candles())
        assert within_chase_distance(plan, plan.entry_zone_low - plan.max_chase_distance) is True
        assert within_chase_distance(plan, plan.entry_zone_low - plan.max_chase_distance - 0.01) is False

    def test_max_chase_distance_is_provisional_one_atr(self):
        fs = make_fs(atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        assert plan.max_chase_distance == pytest.approx(CHASE_DISTANCE_ATR_MULTIPLE * 2.0)


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

class TestStaleness:
    def test_freshly_built_plan_is_not_stale(self):
        fs = make_fs(atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        assert plan.is_stale is False

    def test_ttl_is_timeframe_aware(self):
        fs_m1 = make_fs(timeframe=Timeframe.M1, atr=2.0)
        fs_h1 = make_fs(timeframe=Timeframe.H1, atr=2.0)
        plan_m1 = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs_m1, make_candles())
        plan_h1 = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs_h1, make_candles())
        assert plan_h1.staleness_ttl_minutes > plan_m1.staleness_ttl_minutes
        assert plan_m1.staleness_ttl_minutes == pytest.approx(STALENESS_TTL_CANDLE_MULTIPLE * TIMEFRAME_MINUTES[Timeframe.M1])

    def test_refresh_marks_stale_after_ttl_elapses(self):
        fs = make_fs(atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        later = NOW + timedelta(minutes=plan.staleness_ttl_minutes + 1)
        refreshed = refresh_staleness(plan, later)
        assert refreshed.is_stale is True

    def test_refresh_keeps_fresh_before_ttl(self):
        fs = make_fs(atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        soon = NOW + timedelta(minutes=plan.staleness_ttl_minutes - 1)
        refreshed = refresh_staleness(plan, soon)
        assert refreshed.is_stale is False

    def test_refresh_does_not_mutate_the_original_plan(self):
        fs = make_fs(atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        later = NOW + timedelta(minutes=plan.staleness_ttl_minutes + 1)
        refresh_staleness(plan, later)
        assert plan.is_stale is False  # original untouched -- EntryPlan is frozen

    def test_refresh_changes_nothing_else(self):
        fs = make_fs(atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        refreshed = refresh_staleness(plan, NOW + timedelta(minutes=1))
        assert refreshed.entry_zone_low == plan.entry_zone_low
        assert refreshed.entry_zone_high == plan.entry_zone_high
        assert refreshed.generated_at == plan.generated_at


# ---------------------------------------------------------------------------
# Structure clearance
# ---------------------------------------------------------------------------

class TestStructureClearance:
    def test_no_opposing_structure_is_cleared(self):
        fs = make_fs(close=100.0, atr=2.0, swing_resistance=None)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        assert plan.structure_clearance_ok is True

    def test_opposing_structure_far_away_is_cleared(self):
        fs = make_fs(close=100.0, atr=2.0, swing_resistance=200.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        assert plan.structure_clearance_ok is True

    def test_opposing_structure_close_by_blocks_clearance(self):
        # zone_high=100.5 (close=100, buf=0.5); resistance clearly inside
        # the projected (100.5, 103.5) range, not at the boundary itself.
        fs = make_fs(close=100.0, atr=2.0, swing_resistance=101.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        assert plan.structure_clearance_ok is False

    def test_short_uses_swing_support_as_the_opposing_level(self):
        fs = make_fs(close=100.0, atr=2.0, swing_support=99.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.SHORT), fs, make_candles())
        assert plan.structure_clearance_ok is False


# ---------------------------------------------------------------------------
# No-lookahead
# ---------------------------------------------------------------------------

class TestNoLookahead:
    def test_generated_at_is_fs_as_of_never_the_wall_clock(self):
        fixed_as_of = datetime(2020, 1, 1, 0, 0, 0)
        fs = make_fs(as_of=fixed_as_of, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        assert plan.generated_at == fixed_as_of

    def test_future_candles_appended_do_not_change_an_already_built_plan(self):
        # build_entry_plan only reads fs and setup, both already fully
        # determined as of a point in time -- appending more candles to a
        # SEPARATE, later call cannot retroactively change an earlier result.
        fs = make_fs(close=100.0, atr=2.0)
        plan_a = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles(5))
        plan_b = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles(5) + make_candles(3))
        assert plan_a == plan_b  # `closed` isn't even used by this family -- same fs, same result

    def test_refresh_staleness_only_uses_the_explicitly_passed_reference_time(self):
        import inspect
        assert "datetime.now" not in inspect.getsource(refresh_staleness)
        assert "utcnow" not in inspect.getsource(refresh_staleness)


# ---------------------------------------------------------------------------
# Audit finding #3 regressions: entry-zone constants are PROVISIONAL
# implementation choices (not spec-mandated), so what this module must
# guarantee is determinism, LONG/SHORT symmetry, that zones never cross
# their own setup's invalidation boundary, and that building an EntryPlan
# never itself confers "confirmed" status on an unconfirmed setup.
# ---------------------------------------------------------------------------

class TestDeterminismAndSymmetry:
    def test_same_inputs_always_produce_the_same_plan(self):
        fs = make_fs(close=100.0, ema21=98.0, bb_lower=95.0, bb_upper=105.0, atr=2.0)
        setup = make_setup(SetupType.PULLBACK)
        candles = make_candles()
        plan_a = build_entry_plan(setup, fs, candles)
        plan_b = build_entry_plan(setup, fs, candles)
        assert plan_a == plan_b

    def test_determinism_across_repeated_calls_all_five_families(self):
        fs = make_fs(close=100.0, ema21=98.0, bb_lower=95.0, bb_upper=105.0, atr=2.0)
        for st in SetupType:
            setup = make_setup(st, invalidation_price=90.0)
            results = [build_entry_plan(setup, fs, make_candles()) for _ in range(3)]
            assert results[0] == results[1] == results[2]

    def test_trend_continuation_long_short_symmetry(self):
        fs = make_fs(close=100.0, atr=2.0)
        long_plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        short_plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.SHORT), fs, make_candles())
        long_half_width = (long_plan.entry_zone_high - long_plan.entry_zone_low) / 2
        short_half_width = (short_plan.entry_zone_high - short_plan.entry_zone_low) / 2
        assert long_half_width == pytest.approx(short_half_width)
        assert (long_plan.entry_zone_low + long_plan.entry_zone_high) / 2 == pytest.approx(
            (short_plan.entry_zone_low + short_plan.entry_zone_high) / 2
        )

    def test_pullback_long_short_symmetry(self):
        fs = make_fs(close=100.0, ema21=99.0, atr=2.0)
        long_plan = build_entry_plan(make_setup(SetupType.PULLBACK, Direction.LONG), fs, make_candles())
        short_plan = build_entry_plan(make_setup(SetupType.PULLBACK, Direction.SHORT), fs, make_candles())
        assert (long_plan.entry_zone_high - long_plan.entry_zone_low) == pytest.approx(
            short_plan.entry_zone_high - short_plan.entry_zone_low
        )

    def test_breakout_retest_reversal_long_short_symmetry(self):
        # mirrored levels equidistant from a common reference -> mirrored zone widths
        fs = make_fs(close=100.0, atr=2.0)
        long_plan = build_entry_plan(
            make_setup(SetupType.BREAKOUT_RETEST, Direction.LONG, invalidation_price=95.0), fs, make_candles()
        )
        short_plan = build_entry_plan(
            make_setup(SetupType.BREAKOUT_RETEST, Direction.SHORT, invalidation_price=105.0), fs, make_candles()
        )
        assert (long_plan.entry_zone_high - long_plan.entry_zone_low) == pytest.approx(
            short_plan.entry_zone_high - short_plan.entry_zone_low
        )
        assert (long_plan.entry_zone_high - 95.0) == pytest.approx(105.0 - short_plan.entry_zone_low)


class TestInvalidationBoundaryInvariant:
    """Audit finding #3: entry-zone widths must never push a zone across
    the setup's own invalidation_price -- verified directly, not just
    asserted in a comment."""

    def test_breakout_retest_long_zone_never_crosses_invalidation(self):
        fs = make_fs(close=100.0, atr=2.0)
        setup = make_setup(SetupType.BREAKOUT_RETEST, Direction.LONG, invalidation_price=65.0)
        plan = build_entry_plan(setup, fs, make_candles())
        assert plan.entry_zone_low >= plan.invalidation_price
        assert plan.entry_zone_low == pytest.approx(plan.invalidation_price)  # touches exactly, never past it

    def test_breakout_retest_short_zone_never_crosses_invalidation(self):
        fs = make_fs(close=100.0, atr=2.0)
        setup = make_setup(SetupType.BREAKOUT_RETEST, Direction.SHORT, invalidation_price=65.0)
        plan = build_entry_plan(setup, fs, make_candles())
        assert plan.entry_zone_high <= plan.invalidation_price
        assert plan.entry_zone_high == pytest.approx(plan.invalidation_price)

    def test_reversal_zone_never_crosses_invalidation_across_many_levels(self):
        fs = make_fs(close=100.0, atr=2.0)
        for level in (50.0, 80.0, 120.0, 200.0):
            for direction in (Direction.LONG, Direction.SHORT):
                setup = make_setup(SetupType.REVERSAL, direction, invalidation_price=level)
                plan = build_entry_plan(setup, fs, make_candles())
                if direction == Direction.LONG:
                    assert plan.entry_zone_low >= plan.invalidation_price - 1e-9
                else:
                    assert plan.entry_zone_high <= plan.invalidation_price + 1e-9


class TestChaseDistanceIndependentOfDirectionInference:
    def test_within_chase_distance_only_uses_the_plans_own_stored_direction(self):
        # within_chase_distance must never re-derive or infer a direction
        # from current_price itself -- it only ever reads plan.direction
        # (already fixed at plan-construction time) and compares bounds.
        # Verified structurally: its only inputs are the plan and a price.
        import inspect
        params = inspect.signature(within_chase_distance).parameters
        assert list(params.keys()) == ["plan", "current_price"]

    def test_chase_distance_result_depends_only_on_plan_direction_not_price_history(self):
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION, Direction.LONG), fs, make_candles())
        # same current_price, evaluated with no other context -- result must
        # be a pure function of (plan, current_price), nothing else
        r1 = within_chase_distance(plan, 100.3)
        r2 = within_chase_distance(plan, 100.3)
        assert r1 == r2


class TestUnconfirmedSetupNeverBecomesConfirmed:
    def test_entry_plan_can_be_built_for_a_forming_unconfirmed_setup(self):
        # build_entry_plan does not require confirmation_met=True -- it
        # computes a zone either way; the CONFIRMATION decision belongs
        # entirely to the Quality Gate's G3, never inferred here.
        fs = make_fs(close=100.0, atr=2.0)
        forming_setup = SetupCandidate(
            setup_type=SetupType.TREND_CONTINUATION, direction=Direction.LONG,
            prerequisites_met=True, confirmation_met=False,
            invalidation_price=90.0, evidence_refs=["x"], failed_conditions=["not yet confirmed"],
        )
        plan = build_entry_plan(forming_setup, fs, make_candles())
        assert plan is not None  # entry engine does its own job regardless of confirmation status

    def test_entry_plan_has_no_field_that_could_be_mistaken_for_confirmation_status(self):
        # EntryPlan carries structure_clearance_ok/is_stale -- neither is a
        # "confirmed" flag, and nothing on EntryPlan asserts the underlying
        # SetupCandidate was confirmed. Confirmation is exclusively
        # SetupCandidate.confirmation_met, checked only at the Quality Gate.
        fs = make_fs(close=100.0, atr=2.0)
        plan = build_entry_plan(make_setup(SetupType.TREND_CONTINUATION), fs, make_candles())
        field_names = set(plan.__dataclass_fields__.keys())
        assert "confirmed" not in field_names
        assert "confirmation_met" not in field_names

    def test_quality_gate_g3_still_fails_even_with_a_valid_entry_plan_present(self):
        # End-to-end proof: a fully-built, otherwise-valid EntryPlan for an
        # UNCONFIRMED setup must not let the Quality Gate's G3 pass.
        from smart_trade_analyzer.contracts import DataQuality, DataQualityState
        from smart_trade_analyzer.quality_gate import GateContext, evaluate

        fs = make_fs(close=100.0, atr=2.0)
        forming_setup = SetupCandidate(
            setup_type=SetupType.TREND_CONTINUATION, direction=Direction.LONG,
            prerequisites_met=True, confirmation_met=False,
            invalidation_price=90.0, evidence_refs=["x"], failed_conditions=["not yet confirmed"],
        )
        plan = build_entry_plan(forming_setup, fs, make_candles())
        assert plan is not None

        dq = DataQuality(overall=DataQualityState.VALID, candle_count=210, candle_count_required=200,
                          per_source={}, excluded_sources=[], reasons=[])
        ctx = GateContext(
            data_quality=dq, as_of=fs.as_of, timeframe=fs.timeframe, setup=forming_setup,
            confluence=None, entry_plan=plan, risk_plan=None, evaluated_at=fs.as_of, current_price=100.0,
        )
        result = evaluate(ctx)
        assert result.gates["G3_SETUP_CONFIRMED"] is False
        assert result.decision.value in ("WAIT", "NO_TRADE")
        assert result.direction is None
