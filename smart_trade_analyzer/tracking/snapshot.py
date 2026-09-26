"""Turns a finalized OpportunityResult into a frozen TradeSnapshot.

This module only READS an OpportunityResult and COPIES its values. It never
computes an entry, stop, target, direction, setup or quality: everything in a
snapshot is exactly what the analyzer already decided. The one thing it does
add is a refusal -- a result that is not a complete, internally consistent
LONG/SHORT plan is not trackable, and says why.
"""
from __future__ import annotations

from typing import Optional

from ..contracts import Decision
from ..scanner.models import OpportunityResult
from .errors import NotTrackableError
from .models import TradeSnapshot, plan_geometry_problem


def trackability_problem(result: OpportunityResult) -> Optional[str]:
    """None if `result` can be tracked, else a human-readable reason."""
    if result.decision not in (Decision.LONG, Decision.SHORT):
        return (f"Only actionable LONG/SHORT results can be tracked "
                f"(this result is {result.decision.value}).")
    record = result.signal_record
    if record is None:
        return "This result has no signal record, so there is no trade plan to track."
    if result.entry_plan is None or result.risk_plan is None:
        return "This result has no entry/risk plan, so there is no trade plan to track."
    if record.direction.value != result.decision.value:
        return (f"Signal direction ({record.direction.value}) does not match the decision "
                f"({result.decision.value}).")
    missing = [name for name, value in (
        ("entry", record.entry), ("stop_loss", record.stop_loss),
        ("take_profit_1", record.take_profit_1), ("take_profit_2", record.take_profit_2),
        ("confirmation_price", record.confirmation_price),
    ) if value is None]
    if missing:
        return f"The trade plan is incomplete (missing: {', '.join(missing)})."
    return plan_geometry_problem(record.direction, record.entry, record.stop_loss,
                                 record.take_profit_1, record.take_profit_2)


def is_trackable(result: OpportunityResult) -> bool:
    return trackability_problem(result) is None


def build_trade_snapshot(result: OpportunityResult) -> TradeSnapshot:
    """Freeze `result`'s trade plan. Raises NotTrackableError with a reason if
    the result has no complete, consistent LONG/SHORT plan. `result` is never
    modified.
    """
    problem = trackability_problem(result)
    if problem:
        raise NotTrackableError(problem)

    record = result.signal_record
    market_data = result.market_data
    analysis_price = market_data.live_price if market_data is not None else None
    analysis_price_source = (market_data.price_source or "unavailable") if market_data is not None else "unavailable"
    if analysis_price is None:
        analysis_price_source = "unavailable"

    zone = record.entry_zone
    try:
        return TradeSnapshot(
            signal_id=record.id,
            symbol=result.symbol,
            pair=result.pair,
            market=result.market_type,
            timeframe=result.timeframe,
            direction=record.direction,
            setup=record.setup_type.value,
            quality_score=record.setup_score,
            grade=record.quality_grade.value,
            entry=record.entry,
            confirmation_price=record.confirmation_price,
            stop_loss=record.stop_loss,
            take_profit_1=record.take_profit_1,
            take_profit_2=record.take_profit_2,
            risk_reward_1=result.risk_plan.risk_reward_1,
            risk_reward_2=result.risk_plan.risk_reward_2,
            signal_generated_at=record.timestamp,
            entry_zone_low=zone[0] if zone is not None else None,
            entry_zone_high=zone[1] if zone is not None else None,
            invalidation_price=record.invalidation_price,
            analysis_price=analysis_price,
            analysis_price_source=analysis_price_source,
        )
    except ValueError as exc:
        raise NotTrackableError(f"The trade plan failed validation: {exc}") from exc
