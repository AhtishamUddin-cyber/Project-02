from datetime import datetime

from smart_trade_analyzer.contracts import (
    ConfluenceResult, DataQuality, DataQualityState, Decision, Direction, EntryPlan, EvidenceCategory, MarketRegime,
    MarketType, QualityGrade, RegimeType, RiskPlan, SetupCandidate, SetupType, SignalDecision, SignalRecord,
    Timeframe,
)
from smart_trade_analyzer.scanner import OpportunityResult
from smart_trade_analyzer.ui.scanner_display import build_scan_result_row, build_scan_result_rows

NOW = datetime(2026, 8, 27, 12, 0, 0)


def _long_result(symbol="BTC", pair="BTCUSDT"):
    confluence = ConfluenceResult(
        proposed_direction=Direction.LONG,
        category_scores={EvidenceCategory.TREND: 0.6}, categories_available=[EvidenceCategory.TREND],
        categories_excluded=[], setup_quality_score=72.0, evidence=[], conflicts=[],
    )
    entry_plan = EntryPlan(direction=Direction.LONG, entry_zone_low=99.0, entry_zone_high=101.0,
                            confirmation_price=100.0, invalidation_price=95.0, max_chase_distance=2.0,
                            structure_clearance_ok=True, generated_at=NOW, staleness_ttl_minutes=37.5,
                            is_stale=False)
    risk_plan = RiskPlan(stop_loss=97.0, take_profit_1=103.0, take_profit_2=105.0, risk_reward_1=2.0,
                          risk_reward_2=3.0, meets_min_rr=True, target_clearance_ok=True, position_size=None,
                          max_safe_leverage=10.0, warnings=[])
    signal_record = SignalRecord(
        id="abc", symbol=symbol, timeframe=Timeframe.M15, market_type=MarketType.SPOT, timestamp=NOW,
        decision=Decision.LONG, direction=Direction.LONG, setup_type=SetupType.TREND_CONTINUATION,
        market_regime=RegimeType.STRONG_UPTREND, setup_score=72.0, historical_probability=None,
        entry=100.0, entry_zone=(99.0, 101.0), stop_loss=97.0, take_profit_1=103.0, take_profit_2=105.0,
        risk_reward=2.0, confirmation_price=100.0, invalidation_price=95.0, quality_grade=QualityGrade.B,
        reasons=["setup confirmed"], warnings=[], data_quality=DataQuality(
            overall=DataQualityState.VALID, candle_count=210, candle_count_required=200, per_source={},
            excluded_sources=[], reasons=[],
        ), confluence=confluence, entry_plan=entry_plan, risk_plan=risk_plan,
        feature_snapshot=None, regime_snapshot=MarketRegime(regime=RegimeType.STRONG_UPTREND, trend_strength=0.8,
                                                              volatility_percentile=0.5, basis=[]),
        status="PENDING",
    )
    return OpportunityResult(
        decision=Decision.LONG, symbol=symbol, pair=pair, market_type=MarketType.SPOT, timeframe=Timeframe.M15,
        reasons=["setup confirmed"], warnings=[],
        setup=SetupCandidate(setup_type=SetupType.TREND_CONTINUATION, direction=Direction.LONG,
                              prerequisites_met=True, confirmation_met=True, invalidation_price=95.0,
                              evidence_refs=[], failed_conditions=[]),
        confluence=confluence, entry_plan=entry_plan, risk_plan=risk_plan,
        regime=MarketRegime(regime=RegimeType.STRONG_UPTREND, trend_strength=0.8, volatility_percentile=0.5, basis=[]),
        signal_decision=SignalDecision(decision=Decision.LONG, direction=Direction.LONG,
                                        gates={"G1": True}, quality_grade=QualityGrade.B, reasons=[], warnings=[]),
        signal_record=signal_record,
    )


def test_scan_result_row_narrows_display_model_fields_correctly():
    row = build_scan_result_row(_long_result())
    assert row.symbol == "BTC"
    assert row.pair == "BTCUSDT"
    assert row.decision == "LONG"
    assert row.direction == "LONG"
    assert row.setup_family == "Trend Continuation"
    assert row.quality_score == "72"
    assert row.quality_grade == "B"
    assert row.entry == "100.00"
    assert row.stop_loss == "97.0000"
    assert row.take_profit_1 == "103.00"
    assert row.risk_reward == "1 : 2.00"
    assert row.market_regime == "Strong Uptrend"


def test_build_scan_result_rows_maps_a_list_in_order():
    results = [_long_result(symbol="BTC", pair="BTCUSDT"), _long_result(symbol="ETH", pair="ETHUSDT")]
    rows = build_scan_result_rows(results)
    assert [r.pair for r in rows] == ["BTCUSDT", "ETHUSDT"]


def test_build_scan_result_rows_handles_empty_list():
    assert build_scan_result_rows([]) == []
