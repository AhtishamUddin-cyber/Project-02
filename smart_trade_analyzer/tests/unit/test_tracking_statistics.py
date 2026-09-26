"""Statistics tests: aggregation rules and terminology guarantees.
TP1_HIT counts as open (not a win); EXPIRED/INVALIDATED are excluded from
win rate and R; win rate and average R are None, not 0, with no resolved
trades (an undefined rate is not the same as a 0% rate)."""
from datetime import timedelta

import pytest

from smart_trade_analyzer.contracts import Direction
from smart_trade_analyzer.tests.tracking_support import T0, fresh_trade, run_ticks
from smart_trade_analyzer.tracking import TradeOutcome, compute_statistics


def test_empty_history_has_no_defined_rate_or_average():
    stats = compute_statistics([])
    assert stats.total_tracked == 0 and stats.resolved == 0
    assert stats.win_rate is None and stats.average_r is None and stats.total_realized_r == 0.0


def test_a_single_open_trade_is_open_not_a_loss():
    stats = compute_statistics([fresh_trade()])
    assert stats.total_tracked == 1 and stats.open_trades == 1
    assert stats.wins == 0 and stats.losses == 0 and stats.resolved == 0 and stats.win_rate is None


def test_tp1_hit_counts_as_open_and_is_separately_reported():
    trade = run_ticks(fresh_trade(), (103.5, 60))
    stats = compute_statistics([trade])
    assert stats.open_trades == 1 and stats.open_tp1_reached == 1 and stats.wins == 0
    assert stats.tp1_reached_any == 1


def test_win_rate_and_r_over_a_mixed_set():
    trades = [
        run_ticks(fresh_trade(trade_id="w1", signal_id="w1"), (106.0, 60)),                # WIN, +3R
        run_ticks(fresh_trade(trade_id="l1", signal_id="l1"), (97.0, 60)),                 # LOSS, -1R
        run_ticks(fresh_trade(trade_id="l2", signal_id="l2"), (103.0, 60), (97.5, 120)),   # LOSS after TP1, -1R
        fresh_trade(trade_id="o1", signal_id="o1"),                                        # OPEN
        fresh_trade(trade_id="v1", signal_id="v1", tracking_price=90.0),                   # INVALIDATED
    ]
    stats = compute_statistics(trades)
    assert stats.total_tracked == 5 and stats.wins == 1 and stats.losses == 2
    assert stats.invalidated == 1 and stats.open_trades == 1 and stats.resolved == 3
    assert stats.win_rate == pytest.approx(1 / 3)
    assert stats.total_realized_r == pytest.approx(3.0 - 1.0 - 1.0)
    assert stats.average_r == pytest.approx((3.0 - 1.0 - 1.0) / 3)


def test_a_loss_after_tp1_still_counts_as_a_loss_not_a_partial_win():
    trade = run_ticks(fresh_trade(), (103.0, 60), (97.5, 120))
    stats = compute_statistics([trade])
    assert stats.wins == 0 and stats.losses == 1 and stats.win_rate == 0.0 and stats.total_realized_r == -1.0


def test_expired_and_invalidated_are_excluded_from_win_rate_and_r():
    expired = run_ticks(fresh_trade(trade_id="e1", signal_id="e1"), (101.0, 60))
    from smart_trade_analyzer.tracking import apply_expiry
    expired = apply_expiry(expired, expired.expires_at + timedelta(seconds=1))
    invalidated = fresh_trade(trade_id="v1", signal_id="v1", tracking_price=90.0)
    stats = compute_statistics([expired, invalidated])
    assert stats.expired == 1 and stats.invalidated == 1
    assert stats.resolved == 0 and stats.win_rate is None and stats.total_realized_r == 0.0


def test_short_trades_aggregate_identically_to_long():
    trades = [run_ticks(fresh_trade(Direction.SHORT, trade_id="ws", signal_id="ws"), (94.0, 60)),
              run_ticks(fresh_trade(Direction.SHORT, trade_id="ls", signal_id="ls"), (102.0, 60))]
    stats = compute_statistics(trades)
    assert stats.wins == 1 and stats.losses == 1 and stats.win_rate == 0.5


def test_realized_r_uses_each_trades_own_frozen_multiple():
    trades = [run_ticks(fresh_trade(trade_id="a", signal_id="a", risk_reward_2=2.0), (106.0, 60)),
              run_ticks(fresh_trade(trade_id="b", signal_id="b", risk_reward_2=5.0), (106.0, 60))]
    stats = compute_statistics(trades)
    assert stats.total_realized_r == pytest.approx(7.0) and stats.average_r == pytest.approx(3.5)
