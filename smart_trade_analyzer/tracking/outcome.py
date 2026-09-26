"""The deterministic outcome engine. PURE: no network, no clock, no I/O.

It evaluates a FROZEN tracked trade against observed prices, and nothing
else. It never calls the analyzer, never reads a recommendation, never
touches Entry/SL/TP. The same trade and the same observations always produce
the same result.

Rules (also rendered to users from tracking/definitions.py)
-----------------------------------------------------------
Baseline (at Track Trade time, one live Bitget price):
  The plan is VOID if that price is already beyond the stop, beyond the
  plan's invalidation level, or already at/through TP1. A void plan is
  saved as INVALIDATED (terminal, no R, not a win or loss): it was never a
  live trade. Otherwise the trade starts OPEN, entry assumed filled at the
  frozen entry (no fees, slippage or fill modeling).

Evaluation of one later observation, in this exact order:
  0. Ignored (state unchanged) if the trade is already closed; if the
     observation is not strictly after tracked_at; or if it is not strictly
     after the previous observation (duplicate / out-of-order).
  1. If it is later than expires_at it is NOT evidence: the trade EXPIRES.
  2. Touching counts (inclusive comparison). LONG: SL if low <= SL;
     TP1 if high >= TP1; TP2 if high >= TP2. SHORT mirrors (SL if
     high >= SL; TP if low <= TP).
  3. PRECEDENCE inside a single observation: SL beats TP. If an observation
     could imply both a stop and a target we do not pretend to know the
     order in which they happened, and take the conservative reading.
  4. TP2 reached => TP2_HIT (WIN, R = frozen risk_reward_2), and TP1 is
     credited as reached at the same observation if not already.
  5. TP1 reached (TP2 not) while OPEN => TP1_HIT: a MILESTONE. The trade
     stays monitored; it is not a win.
  6. SL reached, at any time, even after TP1 => STOP_LOSS_HIT (LOSS, R=-1).
     Partial exits and breakeven stops are not modeled in this MVP.

Expiry: a trade still active more than TTL after tracked_at becomes EXPIRED.
No R; the observation window is (tracked_at, expires_at].

Known limitation (documented, not hidden): observations are SAMPLES. A
ticker sample is a single price; anything the market did between two samples
is invisible. The engine records how many observations a trade has seen and
the largest gap between them (max_observation_gap_seconds) so that exposure
is visible per trade.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from ..contracts import Direction, Timeframe
from ..data.models import TIMEFRAME_DURATION_SECONDS
from .models import PriceObservation, TrackedTrade, TradeSnapshot, TradeStatus

# Deterministic close-reason codes (persisted, shown to users).
CLOSE_TP2 = "TP2_REACHED"
CLOSE_STOP_LOSS = "STOP_LOSS_REACHED"
CLOSE_EXPIRED = "EXPIRED_TTL"
VOID_PREFIX = "VOID_AT_TRACKING:"

VOID_STOP_LOSS = "STOP_LOSS"
VOID_INVALIDATION = "INVALIDATION"
VOID_TP1 = "TP1"

STOP_LOSS_R = -1.0


def compute_expires_at(tracked_at: datetime, timeframe: Timeframe, ttl_candles: int) -> datetime:
    """tracked_at + ttl_candles candles of `timeframe`."""
    if ttl_candles < 1:
        raise ValueError(f"ttl_candles must be >= 1, got {ttl_candles}")
    return tracked_at + timedelta(seconds=TIMEFRAME_DURATION_SECONDS[timeframe] * ttl_candles)


def baseline_void_reason(snapshot: TradeSnapshot, price: float) -> Optional[str]:
    """Why the plan is already void at `price`, or None if it is still live.
    Checked in a fixed order (stop, invalidation, TP1) so the reason is
    deterministic when several apply.
    """
    if snapshot.direction is Direction.LONG:
        if price <= snapshot.stop_loss:
            return VOID_STOP_LOSS
        if snapshot.invalidation_price is not None and price <= snapshot.invalidation_price:
            return VOID_INVALIDATION
        if price >= snapshot.take_profit_1:
            return VOID_TP1
    else:
        if price >= snapshot.stop_loss:
            return VOID_STOP_LOSS
        if snapshot.invalidation_price is not None and price >= snapshot.invalidation_price:
            return VOID_INVALIDATION
        if price <= snapshot.take_profit_1:
            return VOID_TP1
    return None


def open_trade(snapshot: TradeSnapshot, *, trade_id: str, tracking_price: float,
               tracked_at: datetime, expires_at: datetime) -> TrackedTrade:
    """The initial state of a newly tracked trade. `tracking_price` is the live
    Bitget price observed at `tracked_at`; it is observation #1.
    """
    common = dict(
        trade_id=trade_id, snapshot=snapshot, tracking_price=tracking_price,
        tracked_at=tracked_at, expires_at=expires_at,
        latest_price=tracking_price, latest_price_at=tracked_at, observation_count=1,
    )
    reason = baseline_void_reason(snapshot, tracking_price)
    if reason is not None:
        return TrackedTrade(**common, status=TradeStatus.INVALIDATED, closed_at=tracked_at,
                            close_reason=f"{VOID_PREFIX}{reason}")
    return TrackedTrade(**common)


def expire_trade(trade: TrackedTrade) -> TrackedTrade:
    """Close an active trade as EXPIRED at its own expires_at. The tail gap
    (last observation -> expiry) counts toward the largest observation gap.
    """
    if not trade.is_active:
        return trade
    reference = trade.latest_price_at or trade.tracked_at
    tail_gap = max((trade.expires_at - reference).total_seconds(), 0.0)
    max_gap = tail_gap if trade.max_observation_gap_seconds is None else max(trade.max_observation_gap_seconds, tail_gap)
    return replace(trade, status=TradeStatus.EXPIRED, closed_at=trade.expires_at,
                   close_reason=CLOSE_EXPIRED, max_observation_gap_seconds=max_gap)


def apply_expiry(trade: TrackedTrade, now: datetime) -> TrackedTrade:
    """Expire the trade if `now` is past its lifetime. Never uses a price."""
    if trade.is_active and now > trade.expires_at:
        return expire_trade(trade)
    return trade


def apply_observation(trade: TrackedTrade, observation: PriceObservation) -> TrackedTrade:
    """Evaluate one observed price against the frozen trade. See module docs."""
    if not trade.is_active:
        return trade
    if observation.observed_at <= trade.tracked_at:
        return trade
    if trade.latest_price_at is not None and observation.observed_at <= trade.latest_price_at:
        return trade
    if observation.observed_at > trade.expires_at:
        return expire_trade(trade)

    reference = trade.latest_price_at or trade.tracked_at
    gap = (observation.observed_at - reference).total_seconds()
    max_gap = gap if trade.max_observation_gap_seconds is None else max(trade.max_observation_gap_seconds, gap)
    seen = dict(
        latest_price=observation.price, latest_price_at=observation.observed_at,
        observation_count=trade.observation_count + 1, max_observation_gap_seconds=max_gap,
    )

    snap = trade.snapshot
    if snap.direction is Direction.LONG:
        stop_crossed = observation.low <= snap.stop_loss
        tp1_crossed = observation.high >= snap.take_profit_1
        tp2_crossed = observation.high >= snap.take_profit_2
        stop_evidence, target_evidence = observation.low, observation.high
    else:
        stop_crossed = observation.high >= snap.stop_loss
        tp1_crossed = observation.low <= snap.take_profit_1
        tp2_crossed = observation.low <= snap.take_profit_2
        stop_evidence, target_evidence = observation.high, observation.low

    if stop_crossed:  # precedence: SL beats TP within one observation
        return replace(trade, **seen, status=TradeStatus.STOP_LOSS_HIT,
                       sl_hit_at=observation.observed_at, sl_hit_price=stop_evidence,
                       closed_at=observation.observed_at, close_reason=CLOSE_STOP_LOSS,
                       realized_r=STOP_LOSS_R)

    if tp2_crossed:  # TP2 also credits TP1
        already = trade.tp1_hit_at is not None
        return replace(trade, **seen, status=TradeStatus.TP2_HIT,
                       tp1_hit_at=trade.tp1_hit_at if already else observation.observed_at,
                       tp1_hit_price=trade.tp1_hit_price if already else target_evidence,
                       tp2_hit_at=observation.observed_at, tp2_hit_price=target_evidence,
                       closed_at=observation.observed_at, close_reason=CLOSE_TP2,
                       realized_r=snap.risk_reward_2)

    if tp1_crossed and trade.status is TradeStatus.OPEN:  # milestone, still monitored
        return replace(trade, **seen, status=TradeStatus.TP1_HIT,
                       tp1_hit_at=observation.observed_at, tp1_hit_price=target_evidence)

    return replace(trade, **seen)
