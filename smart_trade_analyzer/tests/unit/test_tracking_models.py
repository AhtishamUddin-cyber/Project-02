"""Domain-model tests: status vocabulary, contract mapping, and the
invariants that stop an internally inconsistent trade from existing."""
import dataclasses
from datetime import timedelta

import pytest

from smart_trade_analyzer.contracts import Direction
from smart_trade_analyzer.contracts.signal_record import VALID_STATUSES
from smart_trade_analyzer.tests.tracking_support import T0, fresh_trade, make_snapshot
from smart_trade_analyzer.tracking import (
    ACTIVE_STATUSES, TERMINAL_STATUSES, PriceObservation, TrackedTrade, TradeOutcome, TradeStatus,
    to_contract_status,
)
from smart_trade_analyzer.tracking.models import STATUS_TO_OUTCOME


def test_status_vocabulary_is_exactly_the_agreed_six():
    assert {s.value for s in TradeStatus} == {"OPEN", "TP1_HIT", "TP2_HIT", "STOP_LOSS_HIT", "EXPIRED", "INVALIDATED"}


def test_active_and_terminal_statuses_partition_the_vocabulary():
    assert set(ACTIVE_STATUSES) | set(TERMINAL_STATUSES) == set(TradeStatus)
    assert not set(ACTIVE_STATUSES) & set(TERMINAL_STATUSES)
    assert set(ACTIVE_STATUSES) == {TradeStatus.OPEN, TradeStatus.TP1_HIT}


@pytest.mark.parametrize("status,outcome", [
    (TradeStatus.OPEN, TradeOutcome.OPEN), (TradeStatus.TP1_HIT, TradeOutcome.OPEN),
    (TradeStatus.TP2_HIT, TradeOutcome.WIN), (TradeStatus.STOP_LOSS_HIT, TradeOutcome.LOSS),
    (TradeStatus.EXPIRED, TradeOutcome.EXPIRED), (TradeStatus.INVALIDATED, TradeOutcome.INVALIDATED),
])
def test_status_to_outcome_classification(status, outcome):
    assert STATUS_TO_OUTCOME[status] is outcome


def test_tp1_alone_is_never_a_win():
    assert STATUS_TO_OUTCOME[TradeStatus.TP1_HIT] is not TradeOutcome.WIN


def test_every_tracking_status_maps_into_the_frozen_contract_vocabulary():
    for status in TradeStatus:
        assert to_contract_status(status) in VALID_STATUSES


def test_only_stop_loss_differs_by_name_from_the_frozen_contract():
    differing = {s for s in TradeStatus if to_contract_status(s) != s.value}
    assert differing == {TradeStatus.STOP_LOSS_HIT}
    assert to_contract_status(TradeStatus.STOP_LOSS_HIT) == "SL_HIT"


# -- TradeSnapshot ----------------------------------------------------------

@pytest.mark.parametrize("direction,bad", [
    (Direction.LONG, dict(stop_loss=101.0)),            # SL above entry
    (Direction.LONG, dict(take_profit_1=99.0)),         # TP1 below entry
    (Direction.LONG, dict(take_profit_2=102.0)),        # TP2 below TP1
    (Direction.SHORT, dict(stop_loss=99.0)),            # SL below entry
    (Direction.SHORT, dict(take_profit_1=101.0)),       # TP1 above entry
    (Direction.SHORT, dict(take_profit_2=98.0)),        # TP2 above TP1
])
def test_snapshot_rejects_inconsistent_plan_geometry(direction, bad):
    with pytest.raises(ValueError):
        make_snapshot(direction, **bad)


@pytest.mark.parametrize("field,value", [("entry", 0.0), ("stop_loss", -1.0), ("take_profit_1", float("nan")),
                                         ("risk_reward_2", float("inf")), ("analysis_price", 0.0)])
def test_snapshot_rejects_non_positive_or_non_finite_prices(field, value):
    with pytest.raises(ValueError):
        make_snapshot(Direction.LONG, **{field: value})


def test_snapshot_requires_naive_utc_datetime():
    from datetime import timezone
    with pytest.raises(ValueError):
        make_snapshot(Direction.LONG, signal_generated_at=T0.replace(tzinfo=timezone.utc))


def test_snapshot_zone_bounds_must_be_paired_and_ordered():
    with pytest.raises(ValueError):
        make_snapshot(Direction.LONG, entry_zone_high=None)
    with pytest.raises(ValueError):
        make_snapshot(Direction.LONG, entry_zone_low=100.5, entry_zone_high=99.5)


def test_snapshot_is_frozen():
    snapshot = make_snapshot(Direction.LONG)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.stop_loss = 50.0


def test_plan_fingerprint_ignores_signal_identity_and_time():
    a = make_snapshot(Direction.LONG, signal_id="a", signal_generated_at=T0)
    b = make_snapshot(Direction.LONG, signal_id="b", signal_generated_at=T0 + timedelta(hours=1), analysis_price=None)
    assert a.plan_fingerprint == b.plan_fingerprint
    assert a.plan_fingerprint != make_snapshot(Direction.LONG, take_profit_2=107.0).plan_fingerprint


# -- PriceObservation ---------------------------------------------------------

def test_tick_observation_is_a_point_sample():
    obs = PriceObservation.tick(101.5, T0)
    assert obs.low == obs.high == obs.price == 101.5


def test_observation_range_must_bracket_the_price():
    with pytest.raises(ValueError):
        PriceObservation(observed_at=T0, price=100.0, low=101.0, high=102.0)


def test_observation_rejects_non_positive_price_and_aware_datetimes():
    from datetime import timezone
    with pytest.raises(ValueError):
        PriceObservation.tick(0.0, T0)
    with pytest.raises(ValueError):
        PriceObservation.tick(100.0, T0.replace(tzinfo=timezone.utc))


# -- TrackedTrade invariants ---------------------------------------------------

def test_fresh_trade_starts_open_with_baseline_observation():
    trade = fresh_trade()
    assert trade.status is TradeStatus.OPEN and trade.is_active
    assert trade.observation_count == 1 and trade.max_observation_gap_seconds is None
    assert trade.latest_price == trade.tracking_price and trade.latest_price_at == trade.tracked_at


def test_trade_must_expire_after_it_is_tracked():
    trade = fresh_trade()
    with pytest.raises(ValueError):
        dataclasses.replace(trade, expires_at=trade.tracked_at)


def test_active_trade_cannot_carry_closing_fields():
    trade = fresh_trade()
    with pytest.raises(ValueError):
        dataclasses.replace(trade, closed_at=T0 + timedelta(seconds=5), close_reason="X")


def test_tp2_hit_requires_tp1_credit_and_r():
    trade = fresh_trade()
    common = dict(status=TradeStatus.TP2_HIT, closed_at=T0 + timedelta(seconds=5), close_reason="TP2_REACHED",
                  tp2_hit_at=T0 + timedelta(seconds=5), tp2_hit_price=106.0, realized_r=3.0)
    with pytest.raises(ValueError):
        dataclasses.replace(trade, **common)  # no tp1 credit
    ok = dataclasses.replace(trade, tp1_hit_at=T0 + timedelta(seconds=5), tp1_hit_price=106.0, **common)
    assert ok.outcome is TradeOutcome.WIN


def test_stop_loss_hit_requires_minus_one_r():
    trade = fresh_trade()
    common = dict(status=TradeStatus.STOP_LOSS_HIT, closed_at=T0 + timedelta(seconds=5), close_reason="STOP_LOSS_REACHED",
                  sl_hit_at=T0 + timedelta(seconds=5), sl_hit_price=98.0)
    with pytest.raises(ValueError):
        dataclasses.replace(trade, realized_r=-0.5, **common)
    assert dataclasses.replace(trade, realized_r=-1.0, **common).outcome is TradeOutcome.LOSS


def test_invalidated_and_expired_carry_no_r():
    trade = fresh_trade()
    common = dict(closed_at=T0 + timedelta(seconds=5), close_reason="X")
    for status in (TradeStatus.INVALIDATED, TradeStatus.EXPIRED):
        with pytest.raises(ValueError):
            dataclasses.replace(trade, status=status, realized_r=1.0, **common)
        assert dataclasses.replace(trade, status=status, **common).realized_r is None


def test_invalidated_trade_never_reached_tp1():
    trade = fresh_trade()
    with pytest.raises(ValueError):
        dataclasses.replace(trade, status=TradeStatus.INVALIDATED, closed_at=T0 + timedelta(seconds=5),
                            close_reason="X", tp1_hit_at=T0 + timedelta(seconds=1), tp1_hit_price=103.0)


def test_hit_time_and_price_must_be_set_together():
    with pytest.raises(ValueError):
        dataclasses.replace(fresh_trade(), status=TradeStatus.TP1_HIT, tp1_hit_at=T0 + timedelta(seconds=1))
