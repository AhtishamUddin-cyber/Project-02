"""Trade snapshot creation from REAL analyzer output, and the guarantees
around it: values copied verbatim, nothing mutated, non-plans refused."""
import copy
import dataclasses

import pytest

from smart_trade_analyzer.contracts import Decision
from smart_trade_analyzer.tests.tracking_support import (
    real_long_opportunity, real_no_trade_opportunity, real_short_opportunity,
)
from smart_trade_analyzer.tracking import NotTrackableError, build_trade_snapshot, is_trackable, trackability_problem

REAL = [pytest.param(real_long_opportunity, id="LONG"), pytest.param(real_short_opportunity, id="SHORT")]


@pytest.mark.parametrize("factory", REAL)
def test_real_actionable_result_is_trackable(factory):
    result = factory()
    assert result.decision in (Decision.LONG, Decision.SHORT)
    assert is_trackable(result) and trackability_problem(result) is None


@pytest.mark.parametrize("factory", REAL)
def test_snapshot_copies_every_plan_value_verbatim(factory):
    result = factory()
    record, snap = result.signal_record, build_trade_snapshot(result)
    assert snap.signal_id == record.id
    assert (snap.symbol, snap.pair) == (result.symbol, result.pair)
    assert snap.market is result.market_type and snap.timeframe is result.timeframe
    assert snap.direction is record.direction and snap.setup == record.setup_type.value
    assert snap.quality_score == record.setup_score and snap.grade == record.quality_grade.value
    assert snap.entry == record.entry and snap.confirmation_price == record.confirmation_price
    assert (snap.entry_zone_low, snap.entry_zone_high) == record.entry_zone
    assert snap.invalidation_price == record.invalidation_price
    assert snap.stop_loss == record.stop_loss
    assert (snap.take_profit_1, snap.take_profit_2) == (record.take_profit_1, record.take_profit_2)
    assert snap.risk_reward_1 == result.risk_plan.risk_reward_1 == record.risk_reward
    assert snap.risk_reward_2 == result.risk_plan.risk_reward_2
    assert snap.signal_generated_at == record.timestamp


@pytest.mark.parametrize("factory", REAL)
def test_snapshot_analysis_price_is_the_ticker_price_captured_during_analysis(factory):
    result = factory()
    snap = build_trade_snapshot(result)
    assert snap.analysis_price == result.market_data.live_price
    assert snap.analysis_price_source == result.market_data.price_source


def test_missing_analysis_ticker_is_recorded_as_unavailable_not_substituted():
    result = real_long_opportunity()
    no_ticker = dataclasses.replace(
        result, market_data=dataclasses.replace(result.market_data, live_price=None, price_source="unavailable"))
    snap = build_trade_snapshot(no_ticker)
    assert snap.analysis_price is None
    assert snap.analysis_price_source == "unavailable"
    assert snap.entry == result.signal_record.entry  # the plan itself is untouched


@pytest.mark.parametrize("factory", REAL)
def test_building_a_snapshot_never_mutates_the_opportunity_result(factory):
    result = factory()
    before_repr, before_copy = repr(result), copy.deepcopy(result)
    for _ in range(3):
        build_trade_snapshot(result)
    assert repr(result) == before_repr
    assert result == before_copy


def test_result_and_snapshot_are_both_frozen():
    result = real_long_opportunity()
    snap = build_trade_snapshot(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.decision = Decision.SHORT
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.entry = 1.0


def test_non_actionable_result_is_not_trackable_and_says_why():
    result = real_no_trade_opportunity()
    assert not is_trackable(result)
    assert "LONG/SHORT" in trackability_problem(result)
    with pytest.raises(NotTrackableError) as info:
        build_trade_snapshot(result)
    assert info.value.reason == trackability_problem(result)


def test_incomplete_plan_is_refused_not_completed():
    result = real_long_opportunity()
    broken = dataclasses.replace(result, signal_record=dataclasses.replace(result.signal_record, take_profit_2=None))
    assert "take_profit_2" in trackability_problem(broken)
    with pytest.raises(NotTrackableError):
        build_trade_snapshot(broken)


def test_geometrically_inconsistent_plan_is_refused():
    result = real_long_opportunity()
    record = result.signal_record
    flipped = dataclasses.replace(result, signal_record=dataclasses.replace(record, stop_loss=record.entry + 1.0))
    with pytest.raises(NotTrackableError) as info:
        build_trade_snapshot(flipped)
    assert "stop_loss < entry" in info.value.reason


def test_real_plans_have_the_geometry_the_tracker_relies_on():
    long_snap = build_trade_snapshot(real_long_opportunity())
    short_snap = build_trade_snapshot(real_short_opportunity())
    assert long_snap.stop_loss < long_snap.entry < long_snap.take_profit_1 < long_snap.take_profit_2
    assert short_snap.take_profit_2 < short_snap.take_profit_1 < short_snap.entry < short_snap.stop_loss
    assert long_snap.entry == long_snap.confirmation_price and short_snap.entry == short_snap.confirmation_price
