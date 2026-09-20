"""Turns a real scanner.OpportunityResult into UI-ready display data.

This module makes NO trading decision and performs NO analytical
calculation -- it only reads fields that the Phase 6 backend already
computed (scanner.scan_symbol -> pipeline.run_pipeline ->
signal_assembly.build_signal_record) and formats them for display. Every
field on OpportunityDisplayModel traces to a specific field the backend
already produced; nothing is invented, defaulted-to-zero, or recomputed
here (Section 3 / Section 7 of this phase's brief). `decision` is always
`result.decision` verbatim -- there is no branch anywhere below that
inspects a score, a setup, or a gate and picks a Decision; the UI layer
is a consumer, exactly as instructed.

Pure and synchronous: build_display_model() takes an already-fetched
OpportunityResult and returns an immutable OpportunityDisplayModel. No
Streamlit import anywhere in this module -- this is what makes it
directly unit-testable with plain pytest (see tests/ui/test_display_model.py).
"""
from dataclasses import dataclass, field
from typing import List, Optional

from ..scanner import OpportunityResult
from .formatting import (
    format_label, format_price, format_price_range, format_quality_score, format_risk_reward,
    format_timestamp, title_case_label,
)


@dataclass(frozen=True)
class OpportunityDisplayModel:
    # Core
    symbol: str
    pair: str
    market_type: str
    timeframe: str
    decision: str                      # "LONG" | "SHORT" | "WAIT" | "NO_TRADE" -- verbatim from the backend
    decision_headline: str             # a display-friendly headline for the same value (e.g. "NO TRADE")
    setup_family: Optional[str]        # None when no setup was ever detected
    direction: Optional[str]           # the DECISION's own direction (LONG/SHORT) -- None for WAIT/NO_TRADE, always
    pending_direction: Optional[str]   # a detected setup's own direction, shown as CONTEXT for WAIT only -- never the Decision
    has_quality: bool
    quality_score: Optional[str]
    quality_grade: Optional[str]

    # Trade plan
    has_trade_plan: bool
    entry: Optional[str]
    entry_zone: Optional[str]
    confirmation_price: Optional[str]
    invalidation_price: Optional[str]
    stop_loss: Optional[str]
    take_profit_1: Optional[str]
    take_profit_2: Optional[str]
    risk_reward: Optional[str]

    # Context
    market_regime: Optional[str]
    signal_timestamp: Optional[str]
    is_stale: Optional[bool]           # None when no EntryPlan exists to carry a freshness verdict at all
    data_quality_state: str
    data_quality_reasons: List[str] = field(default_factory=list)

    # Explanation
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


_DECISION_HEADLINES = {
    "LONG": "LONG",
    "SHORT": "SHORT",
    "WAIT": "WAIT",
    "NO_TRADE": "NO TRADE",
}


def build_display_model(result: OpportunityResult) -> OpportunityDisplayModel:
    setup = result.setup
    confluence = result.confluence
    entry_plan = result.entry_plan
    risk_plan = result.risk_plan
    signal_record = result.signal_record
    data_quality = result.data_quality

    decision_value = format_label(result.decision)

    # Quality is shown only when a real setup was actually scored --
    # quality_gate/gate.py defaults quality_grade to F when confluence is
    # None (nothing to grade), which would otherwise read as a real,
    # meaningful "F" grade for a case where there was nothing to fail.
    has_quality = confluence is not None
    quality_score = format_quality_score(confluence.setup_quality_score) if confluence is not None else None
    quality_grade = (
        format_label(result.signal_decision.quality_grade)
        if has_quality and result.signal_decision is not None
        else None
    )

    has_trade_plan = signal_record is not None and entry_plan is not None and risk_plan is not None
    if signal_record is not None:
        entry = format_price(signal_record.entry)
        entry_zone = (
            format_price_range(signal_record.entry_zone[0], signal_record.entry_zone[1])
            if signal_record.entry_zone is not None else None
        )
        confirmation_price = format_price(signal_record.confirmation_price)
        invalidation_price = format_price(signal_record.invalidation_price)
        stop_loss = format_price(signal_record.stop_loss)
        take_profit_1 = format_price(signal_record.take_profit_1)
        take_profit_2 = format_price(signal_record.take_profit_2)
        risk_reward = format_risk_reward(signal_record.risk_reward)
    else:
        entry = entry_zone = confirmation_price = invalidation_price = None
        stop_loss = take_profit_1 = take_profit_2 = risk_reward = None

    # direction: the DECISION's own direction only -- never inferred from
    # the setup when the Gate itself returned None (WAIT/NO_TRADE always
    # carry direction=None on the frozen contract; see contracts/decision.py).
    direction = format_label(result.signal_decision.direction) if result.signal_decision is not None else None
    # pending_direction: informational only, for a WAIT caused by an
    # unconfirmed-but-detected setup -- distinct field, distinct label in
    # the UI, never rendered as if it were the Decision's direction.
    pending_direction = format_label(setup.direction) if setup is not None else None

    signal_timestamp = (
        format_timestamp(signal_record.timestamp) if signal_record is not None
        else (format_timestamp(result.market_data.as_of) if result.market_data is not None else None)
    )
    is_stale = entry_plan.is_stale if entry_plan is not None else None

    return OpportunityDisplayModel(
        symbol=result.symbol,
        pair=result.pair,
        market_type=format_label(result.market_type),
        timeframe=format_label(result.timeframe),
        decision=decision_value,
        decision_headline=_DECISION_HEADLINES.get(decision_value, decision_value),
        setup_family=title_case_label(format_label(setup.setup_type)) if setup is not None else None,
        direction=direction,
        pending_direction=pending_direction,
        has_quality=has_quality,
        quality_score=quality_score,
        quality_grade=quality_grade,
        has_trade_plan=has_trade_plan,
        entry=entry,
        entry_zone=entry_zone,
        confirmation_price=confirmation_price,
        invalidation_price=invalidation_price,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward=risk_reward,
        market_regime=title_case_label(format_label(result.regime.regime)) if result.regime is not None else None,
        signal_timestamp=signal_timestamp,
        is_stale=is_stale,
        data_quality_state=format_label(data_quality.overall) if data_quality is not None else "UNKNOWN",
        data_quality_reasons=list(data_quality.reasons) if data_quality is not None else [],
        reasons=list(result.reasons),
        warnings=list(result.warnings),
    )
