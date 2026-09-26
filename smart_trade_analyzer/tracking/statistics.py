"""Aggregate statistics over ACTUAL tracked trades. Pure functions.

TERMINOLOGY (enforced by tests on the labels the UI shows): these numbers
describe the historical outcomes of signals the user chose to track, under
the MVP rules in definitions.py. They are not accuracy, not a probability of
winning, not an expected future win rate and not a strategy guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .models import TrackedTrade, TradeOutcome, TradeStatus


@dataclass(frozen=True)
class TrackingStatistics:
    total_tracked: int
    open_trades: int            # active: OPEN + TP1_HIT (still being monitored)
    open_tp1_reached: int       # subset of open_trades already at TP1_HIT
    wins: int                   # TP2_HIT
    losses: int                 # STOP_LOSS_HIT (including after TP1)
    expired: int
    invalidated: int
    resolved: int               # wins + losses: the only trades win rate and R are computed over
    win_rate: Optional[float]   # wins / resolved, a fraction in [0, 1]; None when resolved == 0
    total_realized_r: float     # sum of realized R over resolved trades
    average_r: Optional[float]  # total_realized_r / resolved; None when resolved == 0
    tp1_reached_any: int        # informational: trades that ever reached TP1, whatever their final status


def compute_statistics(trades: Iterable[TrackedTrade]) -> TrackingStatistics:
    trades = list(trades)
    wins = losses = expired = invalidated = open_trades = open_tp1 = tp1_any = 0
    total_r = 0.0
    for trade in trades:
        outcome = trade.outcome
        if trade.tp1_hit_at is not None:
            tp1_any += 1
        if outcome is TradeOutcome.WIN:
            wins += 1
            total_r += trade.realized_r
        elif outcome is TradeOutcome.LOSS:
            losses += 1
            total_r += trade.realized_r
        elif outcome is TradeOutcome.EXPIRED:
            expired += 1
        elif outcome is TradeOutcome.INVALIDATED:
            invalidated += 1
        else:
            open_trades += 1
            if trade.status is TradeStatus.TP1_HIT:
                open_tp1 += 1
    resolved = wins + losses
    return TrackingStatistics(
        total_tracked=len(trades), open_trades=open_trades, open_tp1_reached=open_tp1,
        wins=wins, losses=losses, expired=expired, invalidated=invalidated, resolved=resolved,
        win_rate=(wins / resolved) if resolved else None,
        total_realized_r=total_r,
        average_r=(total_r / resolved) if resolved else None,
        tp1_reached_any=tp1_any,
    )
