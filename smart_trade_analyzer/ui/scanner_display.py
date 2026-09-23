"""Compact per-symbol row for the Scanner results table.

Deliberately does NOT reimplement any formatting or field-mapping logic:
build_scan_result_row() is a thin wrapper around
ui.display_model.build_display_model(), narrowed to the columns a table
row needs. The full OpportunityDisplayModel (same object this wraps) is
what a "click to inspect" detail view renders -- see app.py -- so a
selected row's detail is never recomputed, only re-displayed.
"""
from dataclasses import dataclass
from typing import List, Optional

from ..scanner import OpportunityResult
from .display_model import OpportunityDisplayModel, build_display_model


@dataclass(frozen=True)
class ScanResultRow:
    symbol: str
    pair: str
    decision: str
    direction: Optional[str]
    setup_family: Optional[str]
    quality_score: Optional[str]
    quality_grade: Optional[str]
    entry: Optional[str]
    stop_loss: Optional[str]
    take_profit_1: Optional[str]
    risk_reward: Optional[str]
    market_regime: Optional[str]


def build_scan_result_row(result: OpportunityResult) -> ScanResultRow:
    model = build_display_model(result)
    return ScanResultRow(
        symbol=model.symbol, pair=model.pair, decision=model.decision, direction=model.direction,
        setup_family=model.setup_family, quality_score=model.quality_score, quality_grade=model.quality_grade,
        entry=model.entry, stop_loss=model.stop_loss, take_profit_1=model.take_profit_1,
        risk_reward=model.risk_reward, market_regime=model.market_regime,
    )


def build_scan_result_rows(results: List[OpportunityResult]) -> List[ScanResultRow]:
    return [build_scan_result_row(r) for r in results]
