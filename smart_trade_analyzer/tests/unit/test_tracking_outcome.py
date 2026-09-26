"""Outcome-engine tests. The engine is pure, so every rule in
tracking/outcome.py's docstring is asserted directly on frozen trades.

Reference plans (tests/tracking_support.py):
  LONG : entry 100, SL 98,  TP1 103, TP2 106, RR2 3.0, invalidation 97
  SHORT: entry 100, SL 102, TP1 97,  TP2 94,  RR2 3.0, invalidation 103
Tracked at T0 with a 48-candle (12 h) lifetime on 15m.
"""
import dataclasses
import random
from datetime import timedelta

import pytest

from smart_trade_analyzer.contracts import Direction, Timeframe
from smart_trade_analyzer.data.models import TIMEFRAME_DURATION_SECONDS
from smart_trade_analyzer.tests.tracking_support import T0, fresh_trade, run_ticks, tick
from smart_trade_analyzer.tracking import (
    PriceObservation, TradeOutcome, TradeStatus, apply_expiry, apply_observation, baseline_void_reason,
    compute_expires_at,
)

OPEN, TP1, TP2, SL = TradeStatus.OPEN, TradeStatus.TP1_HIT, TradeStatus.TP2_HIT, TradeStatus.STOP_LOSS_HIT
L, S = Direction.LONG, Direction.SHORT


def rng(price, seconds, low=None, high=None):
    return PriceObservation(observed_at=T0 + timedelta(seconds=seconds), price=price, low=low, high=high)


# ---------------------------------------------------------------------------
# 3-4. LONG take-profit and stop detection.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticks,status,r", [
    ([(101, 60), (102.99, 120), (98.01, 180)], OPEN, None),        # nothing touched
    ([(103.0, 60)], TP1, None),                                    # exact touch of TP1 counts
    ([(103.5, 60), (100.0, 120)], TP1, None),                      # milestone persists when price falls back
    ([(106.0, 60)], TP2, 3.0),                                     # exact touch of TP2
    ([(103.0, 60), (106.0, 120)], TP2, 3.0),
    ([(110.0, 60)], TP2, 3.0),                                     # jumped straight through both targets
])
def test_long_take_profit_detection(ticks, status, r):
    trade = run_ticks(fresh_trade(L), *ticks)
    assert trade.status is status and trade.realized_r == r


@pytest.mark.parametrize("ticks", [
    [(98.0, 60)],                       # exact touch of the stop counts
    [(97.5, 60)],
    [(90.0, 60)],                       # gapped far through the stop
    [(103.0, 60), (97.9, 120)],         # stop AFTER TP1 is still a loss (no breakeven/partials in the MVP)
])
def test_long_stop_loss_detection(ticks):
    trade = run_ticks(fresh_trade(L), *ticks)
    assert trade.status is SL and trade.realized_r == -1.0 and trade.outcome is TradeOutcome.LOSS


# ---------------------------------------------------------------------------
# 5-6. SHORT mirrors.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticks,status,r", [
    ([(99, 60), (97.01, 120), (101.99, 180)], OPEN, None),
    ([(97.0, 60)], TP1, None),
    ([(96.5, 60), (100.0, 120)], TP1, None),
    ([(94.0, 60)], TP2, 3.0),
    ([(97.0, 60), (94.0, 120)], TP2, 3.0),
    ([(90.0, 60)], TP2, 3.0),
])
def test_short_take_profit_detection(ticks, status, r):
    trade = run_ticks(fresh_trade(S), *ticks)
    assert trade.status is status and trade.realized_r == r


@pytest.mark.parametrize("ticks", [
    [(102.0, 60)], [(102.5, 60)], [(110.0, 60)], [(97.0, 60), (102.1, 120)],
])
def test_short_stop_loss_detection(ticks):
    trade = run_ticks(fresh_trade(S), *ticks)
    assert trade.status is SL and trade.realized_r == -1.0 and trade.outcome is TradeOutcome.LOSS


@pytest.mark.parametrize("direction,stop,tp1,tp2", [(L, 98.0, 103.0, 106.0), (S, 102.0, 97.0, 94.0)])
def test_every_price_on_a_grid_matches_an_independent_oracle(direction, stop, tp1, tp2):
    """A single tick can only ever be OPEN, TP1_HIT, TP2_HIT or STOP_LOSS_HIT, and which one is
    decided purely by where it sits against the frozen levels."""
    for tenths in range(800, 1201):
        price = tenths / 10
        beyond = (lambda level: price <= level) if direction is L else (lambda level: price >= level)
        reached = (lambda level: price >= level) if direction is L else (lambda level: price <= level)
        if beyond(stop):
            expected = SL
        elif reached(tp2):
            expected = TP2
        elif reached(tp1):
            expected = TP1
        else:
            expected = OPEN
        assert run_ticks(fresh_trade(direction), (price, 60)).status is expected, price


# ---------------------------------------------------------------------------
# 7. Deterministic level precedence.
# ---------------------------------------------------------------------------

def test_stop_beats_target_inside_one_observation_long():
    trade = apply_observation(fresh_trade(L), rng(100.0, 60, low=97.5, high=106.5))
    assert trade.status is SL and trade.realized_r == -1.0
    assert trade.tp1_hit_at is None and trade.tp2_hit_at is None  # no target credited on the stop's observation


def test_stop_beats_target_inside_one_observation_short():
    trade = apply_observation(fresh_trade(S), rng(100.0, 60, low=93.5, high=102.5))
    assert trade.status is SL and trade.tp1_hit_at is None


def test_after_tp1_an_observation_spanning_tp2_and_stop_is_a_loss():
    trade = run_ticks(fresh_trade(L), (103.0, 60))
    trade = apply_observation(trade, rng(100.0, 120, low=97.0, high=107.0))
    assert trade.status is SL and trade.tp1_hit_at == T0 + timedelta(seconds=60)


@pytest.mark.parametrize("direction,price,low,high,evidence", [(L, 103.0, 100.5, 106.5, 106.5), (S, 97.0, 93.5, 99.5, 93.5)])
def test_reaching_tp2_credits_tp1_at_the_same_observation(direction, price, low, high, evidence):
    trade = apply_observation(fresh_trade(direction), rng(price, 60, low=low, high=high))
    assert trade.status is TP2
    assert trade.tp1_hit_at == trade.tp2_hit_at == T0 + timedelta(seconds=60)
    assert trade.tp1_hit_price == trade.tp2_hit_price == evidence


def test_tp2_keeps_the_earlier_tp1_timestamp_when_tp1_was_already_reached():
    trade = run_ticks(fresh_trade(L), (103.2, 60), (106.4, 300))
    assert trade.status is TP2
    assert trade.tp1_hit_at == T0 + timedelta(seconds=60) and trade.tp1_hit_price == 103.2
    assert trade.tp2_hit_at == T0 + timedelta(seconds=300)


def test_range_observation_reaching_only_tp1_is_a_milestone():
    trade = apply_observation(fresh_trade(L), rng(101.0, 60, low=99.0, high=104.0))
    assert trade.status is TP1 and trade.tp1_hit_price == 104.0


def test_precedence_is_deterministic():
    steps = [(101, 60), (103.4, 120), (104, 180), (99, 240), (97.5, 300), (110, 360)]
    assert run_ticks(fresh_trade(L), *steps) == run_ticks(fresh_trade(L), *steps)


def test_first_terminal_event_wins_and_later_observations_change_nothing():
    trade = run_ticks(fresh_trade(L), (97.0, 60))
    assert trade.status is SL
    assert apply_observation(trade, tick(110.0, 120)) is trade


# ---------------------------------------------------------------------------
# No lookahead: only observations strictly after tracked_at count.
# ---------------------------------------------------------------------------

def test_observation_at_the_tracking_instant_is_ignored():
    trade = fresh_trade(L)
    assert apply_observation(trade, tick(90.0, 0)) is trade


def test_observation_before_tracking_never_resolves_a_trade():
    trade = fresh_trade(L)
    assert apply_observation(trade, tick(90.0, -30)) is trade
    assert apply_observation(trade, tick(120.0, -30)) is trade


def test_duplicate_and_out_of_order_observations_are_ignored():
    trade = run_ticks(fresh_trade(L), (103.0, 60))
    assert apply_observation(trade, tick(110.0, 60)) is trade   # same instant as the latest
    assert apply_observation(trade, tick(90.0, 30)) is trade    # older than the latest


def test_ignored_observations_do_not_touch_coverage_counters():
    trade = run_ticks(fresh_trade(L), (101.0, 60))
    same = apply_observation(trade, tick(101.0, 60))
    assert same.observation_count == trade.observation_count == 2


def test_applying_an_observation_never_modifies_its_input():
    trade = fresh_trade(L)
    snapshot_before = dataclasses.replace(trade)
    apply_observation(trade, tick(97.0, 60))
    assert trade == snapshot_before and trade.status is OPEN


# ---------------------------------------------------------------------------
# Baseline: a plan already void when tracking begins.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction,price,reason", [
    (L, 98.0, "STOP_LOSS"), (L, 90.0, "STOP_LOSS"), (L, 103.0, "TP1"), (L, 110.0, "TP1"),
    (S, 102.0, "STOP_LOSS"), (S, 110.0, "STOP_LOSS"), (S, 97.0, "TP1"), (S, 90.0, "TP1"),
])
def test_void_plans_are_saved_as_invalidated(direction, price, reason):
    trade = fresh_trade(direction, tracking_price=price)
    assert trade.status is TradeStatus.INVALIDATED and trade.outcome is TradeOutcome.INVALIDATED
    assert trade.close_reason == f"VOID_AT_TRACKING:{reason}"
    assert trade.closed_at == trade.tracked_at and trade.realized_r is None
    assert trade.observation_count == 1 and trade.latest_price == price


@pytest.mark.parametrize("direction,price", [(L, 100.0), (L, 98.01), (L, 102.99), (L, 99.0),
                                             (S, 100.0), (S, 101.99), (S, 97.01), (S, 101.0)])
def test_live_plans_start_open(direction, price):
    assert fresh_trade(direction, tracking_price=price).status is OPEN


def test_invalidation_level_voids_a_plan_that_has_it_nearer_than_the_stop():
    # level-anchored setups can place invalidation between entry and stop (verified on real BREAKOUT_RETEST/REVERSAL plans)
    long_trade = fresh_trade(L, tracking_price=98.9, invalidation_price=99.0)
    assert long_trade.status is TradeStatus.INVALIDATED and long_trade.close_reason == "VOID_AT_TRACKING:INVALIDATION"
    short_trade = fresh_trade(S, tracking_price=101.1, invalidation_price=101.0)
    assert short_trade.close_reason == "VOID_AT_TRACKING:INVALIDATION"


def test_void_checks_run_in_a_fixed_order_stop_then_invalidation_then_tp1():
    assert baseline_void_reason(fresh_trade(L).snapshot, 90.0) == "STOP_LOSS"
    assert baseline_void_reason(fresh_trade(L, invalidation_price=99.0).snapshot, 98.5) == "INVALIDATION"


def test_missing_invalidation_level_is_simply_not_checked():
    assert fresh_trade(L, tracking_price=98.5, invalidation_price=None).status is OPEN


def test_invalidated_trades_are_final():
    trade = fresh_trade(L, tracking_price=90.0)
    assert apply_observation(trade, tick(110.0, 60)) is trade
    assert apply_expiry(trade, T0 + timedelta(days=30)) is trade


def test_invalidation_is_not_a_mid_trade_exit():
    """Approved rule: crossing the invalidation level while a trade is live must NOT move a losing trade
    out of the loss count -- only the stop does."""
    trade = fresh_trade(L, invalidation_price=99.0)
    trade = run_ticks(trade, (98.5, 60))          # beyond invalidation, still above the stop
    assert trade.status is OPEN
    trade = run_ticks(trade, (97.9, 120))
    assert trade.status is SL and trade.outcome is TradeOutcome.LOSS


# ---------------------------------------------------------------------------
# Expiry (48 candles, provisional).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_expiry_is_48_candles_of_the_trades_own_timeframe(timeframe):
    assert compute_expires_at(T0, timeframe, 48) == T0 + timedelta(seconds=48 * TIMEFRAME_DURATION_SECONDS[timeframe])


def test_default_lifetime_on_15m_is_twelve_hours():
    assert fresh_trade(L).expires_at == T0 + timedelta(hours=12)


def test_ttl_must_be_positive():
    with pytest.raises(ValueError):
        compute_expires_at(T0, Timeframe.M15, 0)


def test_trade_expires_only_strictly_after_its_lifetime():
    trade = fresh_trade(L)
    assert apply_expiry(trade, trade.expires_at) is trade
    expired = apply_expiry(trade, trade.expires_at + timedelta(seconds=1))
    assert expired.status is TradeStatus.EXPIRED and expired.outcome is TradeOutcome.EXPIRED
    assert expired.closed_at == trade.expires_at and expired.close_reason == "EXPIRED_TTL"
    assert expired.realized_r is None


def test_expiry_after_tp1_keeps_the_milestone_but_has_no_result():
    trade = run_ticks(fresh_trade(L), (103.5, 60))
    expired = apply_expiry(trade, trade.expires_at + timedelta(seconds=1))
    assert expired.status is TradeStatus.EXPIRED and expired.tp1_hit_at is not None and expired.realized_r is None


def test_an_observation_after_expiry_is_never_evidence():
    trade = fresh_trade(L)
    late = PriceObservation.tick(110.0, trade.expires_at + timedelta(seconds=1))
    result = apply_observation(trade, late)
    assert result.status is TradeStatus.EXPIRED and result.tp2_hit_at is None and result.realized_r is None
    assert result.observation_count == 1


def test_an_observation_exactly_at_expiry_still_counts():
    trade = fresh_trade(L)
    at_expiry = PriceObservation.tick(106.0, trade.expires_at)
    assert apply_observation(trade, at_expiry).status is TP2


def test_expiry_counts_the_unobserved_tail_toward_the_largest_gap():
    trade = run_ticks(fresh_trade(L), (101.0, 60))
    expired = apply_expiry(trade, trade.expires_at + timedelta(seconds=1))
    assert expired.max_observation_gap_seconds == pytest.approx(12 * 3600 - 60)


# ---------------------------------------------------------------------------
# Coverage bookkeeping (the sampled-ticker limitation, made visible).
# ---------------------------------------------------------------------------

def test_observation_count_and_largest_gap_are_recorded():
    trade = run_ticks(fresh_trade(L), (101.0, 60), (101.5, 120), (101.2, 600))
    assert trade.observation_count == 4
    assert trade.max_observation_gap_seconds == 480.0
    assert trade.latest_price == 101.2 and trade.latest_price_at == T0 + timedelta(seconds=600)


def test_the_first_gap_is_measured_from_the_tracking_baseline():
    assert run_ticks(fresh_trade(L), (101.0, 90)).max_observation_gap_seconds == 90.0


def test_closing_observation_is_counted_too():
    trade = run_ticks(fresh_trade(L), (101.0, 60), (97.0, 300))
    assert trade.status is SL and trade.observation_count == 3 and trade.max_observation_gap_seconds == 240.0


# ---------------------------------------------------------------------------
# R accounting comes from the frozen plan, verbatim.
# ---------------------------------------------------------------------------

def test_win_r_is_the_frozen_tp2_multiple_verbatim():
    trade = run_ticks(fresh_trade(L, risk_reward_2=2.75), (106.0, 60))
    assert trade.realized_r == 2.75


def test_loss_r_is_always_minus_one():
    assert run_ticks(fresh_trade(S, risk_reward_2=4.2), (105.0, 60)).realized_r == -1.0


# ---------------------------------------------------------------------------
# 16. No fabricated outcomes.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction", [L, S])
def test_random_prices_that_touch_no_level_never_produce_a_result(direction):
    generator = random.Random(7)
    for _ in range(50):
        trade = fresh_trade(direction)
        for step in range(1, 40):
            trade = apply_observation(trade, tick(generator.uniform(98.01, 101.99) if direction is S else generator.uniform(98.01, 102.99)
                                                  if direction is L else 100.0, step * 30))
        assert trade.status is OPEN and trade.realized_r is None and trade.outcome is TradeOutcome.OPEN


def test_no_observation_means_no_result():
    trade = fresh_trade(L)
    assert trade.status is OPEN and trade.realized_r is None and trade.tp1_hit_at is None
    assert apply_expiry(trade, T0 + timedelta(hours=1)) is trade
