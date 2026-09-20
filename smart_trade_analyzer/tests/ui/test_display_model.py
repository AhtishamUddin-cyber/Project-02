"""Unit tests for ui/display_model.py, against hand-built OpportunityResult
objects (same fixture-construction convention as
tests/unit/test_signal_builder.py's hand-built PipelineResult objects --
pure dataclasses are free to construct directly for exactly this kind of
field-mapping test). No Streamlit import anywhere in this file: these
tests exercise the pure display-model layer, which is exactly what makes
them fast, deterministic, and independent of any running app.
"""
from datetime import datetime

from smart_trade_analyzer.contracts import (
    ConfluenceResult, DataQuality, DataQualityState, Decision, Direction, EntryPlan, EvidenceCategory, FeatureSet,
    MarketData, MarketRegime, MarketType, QualityGrade, RegimeType, RiskPlan, SetupCandidate, SetupType,
    SignalDecision, SignalRecord, Timeframe,
)
from smart_trade_analyzer.scanner import OpportunityResult
from smart_trade_analyzer.ui import build_display_model

NOW = datetime(2026, 8, 27, 12, 0, 0)


def _market_data():
    return MarketData(symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M15,
                       as_of=NOW, candles=[], live_price=105000.0, price_source="fake",
                       price_quality=DataQualityState.VALID)


def _dq(overall=DataQualityState.VALID, reasons=None):
    return DataQuality(overall=overall, candle_count=210, candle_count_required=200,
                        per_source={}, excluded_sources=[], reasons=reasons or [])


def _feature_set():
    return FeatureSet(
        symbol="BTC", timeframe=Timeframe.M15, as_of=NOW, close=105000.0, rsi14=60.0,
        ema9=104800.0, ema21=104200.0, ema50=102000.0, ema200=98000.0,
        stoch_rsi_k=55.0, stoch_rsi_d=50.0, macd_line=50.0, macd_signal=30.0, macd_hist=20.0,
        bb_upper=106000.0, bb_mid=104500.0, bb_lower=103000.0, atr=800.0, atr_pct=0.8, volume_ratio=1.1,
        swing_support=101000.0, swing_resistance=108000.0, divergence=None, completeness=1.0,
    )


def _regime():
    return MarketRegime(regime=RegimeType.STRONG_UPTREND, trend_strength=0.8, volatility_percentile=0.5,
                         basis=["unit test"])


def _setup(direction=Direction.LONG, confirmation_met=True, failed_conditions=None):
    return SetupCandidate(
        setup_type=SetupType.TREND_CONTINUATION, direction=direction, prerequisites_met=True,
        confirmation_met=confirmation_met, invalidation_price=101800.0,
        evidence_refs=["regime=STRONG_UPTREND", "rsi14=60.00 in [45.0, 80.0]"],
        failed_conditions=failed_conditions or [],
    )


def _confluence(direction=Direction.LONG, score=68.0):
    return ConfluenceResult(
        proposed_direction=direction,
        category_scores={EvidenceCategory.TREND: 0.6, EvidenceCategory.MOMENTUM: 0.4},
        categories_available=[EvidenceCategory.TREND, EvidenceCategory.MOMENTUM],
        categories_excluded=[EvidenceCategory.HTF, EvidenceCategory.FLOW, EvidenceCategory.SENTIMENT,
                              EvidenceCategory.STRUCTURE, EvidenceCategory.VOLATILITY, EvidenceCategory.VOLUME],
        setup_quality_score=score, evidence=[], conflicts=[],
    )


def _entry_plan(direction=Direction.LONG, is_stale=False):
    return EntryPlan(
        direction=direction, entry_zone_low=104800.0, entry_zone_high=105200.0, confirmation_price=105000.0,
        invalidation_price=101800.0, max_chase_distance=800.0, structure_clearance_ok=True,
        generated_at=NOW, staleness_ttl_minutes=37.5, is_stale=is_stale,
    )


def _risk_plan():
    return RiskPlan(
        stop_loss=101800.0, take_profit_1=109800.0, take_profit_2=112000.0, risk_reward_1=1.5, risk_reward_2=2.2,
        meets_min_rr=True, target_clearance_ok=True, position_size=None, max_safe_leverage=10.0,
        warnings=["Advisory comfort leverage 10.0x (hard safety ceiling 20.0x -- never exceed this)"],
    )


def _signal_decision(decision=Decision.LONG, direction=Direction.LONG, grade=QualityGrade.B, reasons=None, gates=None):
    return SignalDecision(
        decision=decision, direction=direction,
        gates=gates or {"G1_DATA_VALID": True}, quality_grade=grade,
        reasons=reasons or ["setup confirmed", "direction aligned"], warnings=[],
    )


def _signal_record(decision=Decision.LONG, direction=Direction.LONG):
    return SignalRecord(
        id="abc123", symbol="BTC", timeframe=Timeframe.M15, market_type=MarketType.SPOT, timestamp=NOW,
        decision=decision, direction=direction, setup_type=SetupType.TREND_CONTINUATION,
        market_regime=RegimeType.STRONG_UPTREND, setup_score=68.0, historical_probability=None,
        entry=105000.0, entry_zone=(104800.0, 105200.0), stop_loss=101800.0, take_profit_1=109800.0,
        take_profit_2=112000.0, risk_reward=1.5, confirmation_price=105000.0, invalidation_price=101800.0,
        quality_grade=QualityGrade.B, reasons=["setup confirmed", "regime=STRONG_UPTREND"],
        warnings=["Advisory comfort leverage 10.0x (hard safety ceiling 20.0x -- never exceed this)"],
        data_quality=_dq(), confluence=_confluence(), entry_plan=_entry_plan(), risk_plan=_risk_plan(),
        feature_snapshot=_feature_set(), regime_snapshot=_regime(), status="PENDING",
    )


def _long_result():
    return OpportunityResult(
        decision=Decision.LONG, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M15,
        reasons=["setup confirmed", "regime=STRONG_UPTREND"],
        warnings=["Advisory comfort leverage 10.0x (hard safety ceiling 20.0x -- never exceed this)"],
        market_data=_market_data(), data_quality=_dq(), feature_set=_feature_set(), regime=_regime(),
        setup=_setup(), confluence=_confluence(), entry_plan=_entry_plan(), risk_plan=_risk_plan(),
        signal_decision=_signal_decision(), signal_record=_signal_record(),
    )


# ---------------------------------------------------------------------------
# 3-4. LONG / SHORT render correctly.
# ---------------------------------------------------------------------------

def test_long_decision_renders_with_full_trade_plan():
    model = build_display_model(_long_result())
    assert model.decision == "LONG"
    assert model.decision_headline == "LONG"
    assert model.direction == "LONG"
    assert model.setup_family == "Trend Continuation"
    assert model.has_quality is True
    assert model.quality_score == "68"
    assert model.quality_grade == "B"
    assert model.has_trade_plan is True
    assert model.entry == "105,000.00"
    assert model.entry_zone == "104,800.00 – 105,200.00"
    assert model.stop_loss == "101,800.00"
    assert model.take_profit_1 == "109,800.00"
    assert model.take_profit_2 == "112,000.00"
    assert model.risk_reward == "1 : 1.50"
    assert model.market_regime == "Strong Uptrend"
    assert model.is_stale is False
    assert "setup confirmed" in model.reasons
    assert any("comfort leverage" in w for w in model.warnings)


def test_short_decision_renders_with_full_trade_plan():
    result = OpportunityResult(
        decision=Decision.SHORT, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M15,
        reasons=["setup confirmed"], warnings=[],
        market_data=_market_data(), data_quality=_dq(),
        feature_set=_feature_set(), regime=MarketRegime(regime=RegimeType.STRONG_DOWNTREND, trend_strength=0.8,
                                                          volatility_percentile=0.5, basis=["unit test"]),
        setup=_setup(direction=Direction.SHORT), confluence=_confluence(direction=Direction.SHORT),
        entry_plan=_entry_plan(direction=Direction.SHORT), risk_plan=_risk_plan(),
        signal_decision=_signal_decision(decision=Decision.SHORT, direction=Direction.SHORT),
        signal_record=_signal_record(decision=Decision.SHORT, direction=Direction.SHORT),
    )
    model = build_display_model(result)
    assert model.decision == "SHORT"
    assert model.direction == "SHORT"
    assert model.market_regime == "Strong Downtrend"
    assert model.has_trade_plan is True


# ---------------------------------------------------------------------------
# 5. WAIT renders correctly (a detected-but-unconfirmed setup).
# ---------------------------------------------------------------------------

def test_wait_decision_renders_without_a_trade_plan_but_with_pending_direction():
    setup = _setup(confirmation_met=False, failed_conditions=["macd_hist does not confirm long momentum"])
    result = OpportunityResult(
        decision=Decision.WAIT, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M15,
        reasons=["setup forming but not yet confirmed"],
        warnings=["macd_hist does not confirm long momentum"],
        market_data=_market_data(), data_quality=_dq(), feature_set=_feature_set(), regime=_regime(),
        setup=setup, confluence=_confluence(), entry_plan=None, risk_plan=None,
        signal_decision=_signal_decision(decision=Decision.WAIT, direction=None,
                                          reasons=["setup forming but not yet confirmed"]),
        signal_record=None,
    )
    model = build_display_model(result)
    assert model.decision == "WAIT"
    assert model.decision_headline == "WAIT"
    assert model.direction is None                # the Decision itself has no direction
    assert model.pending_direction == "LONG"       # but the pending setup's own lean is shown as context
    assert model.has_trade_plan is False
    assert model.entry is None and model.stop_loss is None
    assert "setup forming but not yet confirmed" in model.reasons
    assert "macd_hist does not confirm long momentum" in model.warnings
    assert model.has_quality is True  # a real setup WAS detected and scored, just not confirmed/gated through


# ---------------------------------------------------------------------------
# 6. NO_TRADE renders correctly (nothing detected at all).
# ---------------------------------------------------------------------------

def test_no_trade_with_no_setup_detected_shows_no_quality_and_no_plan():
    result = OpportunityResult(
        decision=Decision.NO_TRADE, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
        timeframe=Timeframe.M15, reasons=["no setup candidate detected"], warnings=[],
        market_data=_market_data(), data_quality=_dq(), feature_set=_feature_set(), regime=_regime(),
        setup=None, confluence=None, entry_plan=None, risk_plan=None,
        signal_decision=_signal_decision(decision=Decision.NO_TRADE, direction=None, grade=QualityGrade.F,
                                          reasons=["no setup candidate detected"], gates={"G2_SETUP_EXISTS": False}),
        signal_record=None,
    )
    model = build_display_model(result)
    assert model.decision == "NO_TRADE"
    assert model.decision_headline == "NO TRADE"
    assert model.direction is None
    assert model.pending_direction is None       # no setup at all -- nothing to lean on
    assert model.setup_family is None
    assert model.has_quality is False            # NOT "F" -- there was nothing to grade
    assert model.quality_score is None
    assert model.quality_grade is None
    assert model.has_trade_plan is False
    assert "no setup candidate detected" in model.reasons


def test_no_trade_data_unavailable_reports_data_quality_honestly():
    result = OpportunityResult(
        decision=Decision.NO_TRADE, symbol="XYZ", pair="XYZUSDT", market_type=MarketType.SPOT,
        timeframe=Timeframe.M15, reasons=["candle fetch failed: no candle data returned"], warnings=[],
        market_data=_market_data(), data_quality=_dq(overall=DataQualityState.UNAVAILABLE,
                                                       reasons=["candle fetch failed: no candle data returned"]),
        feature_set=None, regime=None, setup=None, confluence=None, entry_plan=None, risk_plan=None,
        signal_decision=_signal_decision(decision=Decision.NO_TRADE, direction=None, grade=QualityGrade.F,
                                          reasons=["candle fetch failed: no candle data returned"],
                                          gates={"G1_DATA_VALID": False}),
        signal_record=None,
    )
    model = build_display_model(result)
    assert model.decision == "NO_TRADE"
    assert model.data_quality_state == "UNAVAILABLE"
    assert "candle fetch failed: no candle data returned" in model.data_quality_reasons
    assert model.market_regime is None
    assert model.has_quality is False


# ---------------------------------------------------------------------------
# 7-8. reasons / warnings render.
# ---------------------------------------------------------------------------

def test_reasons_and_warnings_pass_through_verbatim_and_are_never_invented():
    result = _long_result()
    model = build_display_model(result)
    assert model.reasons == result.reasons
    assert model.warnings == result.warnings


def test_empty_reasons_and_warnings_do_not_crash():
    result = OpportunityResult(
        decision=Decision.NO_TRADE, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
        timeframe=Timeframe.M15, reasons=[], warnings=[],
        data_quality=_dq(overall=DataQualityState.UNAVAILABLE),
        signal_decision=_signal_decision(decision=Decision.NO_TRADE, direction=None, grade=QualityGrade.F,
                                          reasons=[], gates={"G1_DATA_VALID": False}),
    )
    model = build_display_model(result)
    assert model.reasons == [] and model.warnings == []


# ---------------------------------------------------------------------------
# 9. Entry/SL/TP render when available.
# ---------------------------------------------------------------------------

def test_trade_plan_fields_all_populated_for_a_long():
    model = build_display_model(_long_result())
    for field_value in (model.entry, model.entry_zone, model.confirmation_price, model.invalidation_price,
                        model.stop_loss, model.take_profit_1, model.take_profit_2, model.risk_reward):
        assert field_value is not None


# ---------------------------------------------------------------------------
# 10. Missing optional fields do not crash the UI -- the minimal possible
# OpportunityResult (only the required fields) must still build a model.
# ---------------------------------------------------------------------------

def test_minimal_result_with_only_required_fields_does_not_crash():
    result = OpportunityResult(
        decision=Decision.NO_TRADE, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
        timeframe=Timeframe.M15,
    )
    model = build_display_model(result)
    assert model.decision == "NO_TRADE"
    assert model.data_quality_state == "UNKNOWN"  # data_quality itself was never supplied
    assert model.has_trade_plan is False
    assert model.has_quality is False
    assert model.signal_timestamp is None
    assert model.is_stale is None


def test_result_with_market_data_but_nothing_else_still_shows_a_timestamp():
    result = OpportunityResult(
        decision=Decision.NO_TRADE, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
        timeframe=Timeframe.M15, market_data=_market_data(),
    )
    model = build_display_model(result)
    assert model.signal_timestamp == "2026-08-27 12:00:00 UTC"


# ---------------------------------------------------------------------------
# 12. The UI layer does not calculate or override Decision -- it is always
# exactly result.decision, regardless of what setup/confluence/quality say.
# ---------------------------------------------------------------------------

def test_display_model_decision_is_always_exactly_the_backend_decision():
    for decision, direction in [
        (Decision.LONG, Direction.LONG), (Decision.SHORT, Direction.SHORT),
        (Decision.WAIT, None), (Decision.NO_TRADE, None),
    ]:
        # Deliberately give WAIT/NO_TRADE a HIGH-scoring confluence (a
        # scenario a UI-side "quality >= 70 -> LONG" rule would get wrong)
        # to prove build_display_model never looks at the score to decide
        # anything about `decision` itself.
        needs_record = decision in (Decision.LONG, Decision.SHORT)
        result = OpportunityResult(
            decision=decision, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M15,
            data_quality=_dq(), setup=_setup(confirmation_met=needs_record),
            confluence=_confluence(score=95.0),
            signal_decision=_signal_decision(decision=decision, direction=direction,
                                              grade=QualityGrade.A, gates={"G9_QUALITY_THRESHOLD": True}),
            signal_record=_signal_record(decision=decision, direction=direction) if needs_record else None,
        )
        model = build_display_model(result)
        assert model.decision == decision.value
        assert model.direction == (direction.value if direction is not None else None)
