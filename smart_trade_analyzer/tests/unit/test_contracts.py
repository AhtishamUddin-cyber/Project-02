"""Unit tests for smart_trade_analyzer.contracts.

Covers: enum values, valid construction, invalid/ambiguous states,
immutability, JSON serialization round-trips, the WAIT/NO_TRADE distinction,
the Direction.NEUTRAL-cannot-be-a-final-decision guarantee, explicitness of
unavailable-data fields, and the score-vs-probability naming guard.
"""
import dataclasses
import json
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import (
    MarketType, Timeframe, Direction, Decision, DataQualityState,
    RegimeType, SetupType, EvidenceCategory, QualityGrade,
    CandleData, MarketData, DataQuality, FeatureSet, MarketRegime,
    SetupCandidate, EvidenceItem, ConfluenceResult, EntryPlan, RiskPlan,
    SignalDecision, SignalRecord, ShadowOutcome,
)

NOW = datetime(2026, 8, 21, 12, 0, 0)


# ---------------------------------------------------------------------------
# Fixtures / builders -- small helper functions, not pytest fixtures, so each
# test can tweak exactly the one field it cares about via **overrides.
# ---------------------------------------------------------------------------

def make_candle(**overrides):
    base = dict(open_time=NOW, open=100.0, high=105.0, low=95.0, close=102.0,
                volume=1000.0, is_closed=True)
    base.update(overrides)
    return CandleData(**base)


def make_market_data(**overrides):
    base = dict(
        symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
        timeframe=Timeframe.H1, as_of=NOW, candles=[make_candle()],
        live_price=100.5, price_source="bitget_ticker",
        price_quality=DataQualityState.VALID,
    )
    base.update(overrides)
    return MarketData(**base)


def make_data_quality(**overrides):
    base = dict(
        overall=DataQualityState.VALID, candle_count=200, candle_count_required=200,
        per_source={"orderbook": DataQualityState.VALID, "funding": DataQualityState.UNAVAILABLE},
        excluded_sources=["funding"], reasons=["funding: Bitget API timeout"],
    )
    base.update(overrides)
    return DataQuality(**base)


def make_feature_set(**overrides):
    base = dict(
        symbol="BTC", timeframe=Timeframe.H1, as_of=NOW, close=100.0,
        ema9=99.0, ema21=98.0, ema50=97.0, ema200=None,
        rsi14=55.0, stoch_rsi_k=60.0, stoch_rsi_d=58.0,
        macd_line=0.5, macd_signal=0.3, macd_hist=0.2,
        bb_upper=105.0, bb_mid=100.0, bb_lower=95.0,
        atr=2.5, atr_pct=2.5, volume_ratio=1.2,
        swing_support=94.0, swing_resistance=106.0,
        divergence=None, completeness=0.9,
    )
    base.update(overrides)
    return FeatureSet(**base)


def make_regime(**overrides):
    base = dict(regime=RegimeType.UPTREND, trend_strength=0.5,
                volatility_percentile=0.4, basis=["EMA9>EMA21>EMA50"])
    base.update(overrides)
    return MarketRegime(**base)


def make_setup_candidate(**overrides):
    base = dict(
        setup_type=SetupType.TREND_CONTINUATION, direction=Direction.LONG,
        prerequisites_met=True, confirmation_met=True, invalidation_price=95.0,
        evidence_refs=["ema_stack_bullish"], failed_conditions=[],
    )
    base.update(overrides)
    return SetupCandidate(**base)


def make_evidence_item(**overrides):
    base = dict(category=EvidenceCategory.MOMENTUM, direction=Direction.LONG,
                strength=0.6, detail="RSI 28.4 -- oversold, favors LONG")
    base.update(overrides)
    return EvidenceItem(**base)


def make_confluence_result(**overrides):
    base = dict(
        proposed_direction=Direction.LONG,
        category_scores={EvidenceCategory.TREND: 0.7, EvidenceCategory.MOMENTUM: 0.3},
        categories_available=[EvidenceCategory.TREND, EvidenceCategory.MOMENTUM],
        categories_excluded=[EvidenceCategory.FLOW],
        setup_quality_score=72.5,
        evidence=[make_evidence_item()],
        conflicts=[],
    )
    base.update(overrides)
    return ConfluenceResult(**base)


def make_entry_plan(**overrides):
    base = dict(
        direction=Direction.LONG, entry_zone_low=100.0, entry_zone_high=101.0,
        confirmation_price=101.0, invalidation_price=95.0, max_chase_distance=2.5,
        structure_clearance_ok=True, generated_at=NOW, staleness_ttl_minutes=45.0,
        is_stale=False,
    )
    base.update(overrides)
    return EntryPlan(**base)


def make_risk_plan(**overrides):
    base = dict(
        stop_loss=95.0, take_profit_1=105.0, take_profit_2=110.0,
        risk_reward_1=1.7, risk_reward_2=2.8, meets_min_rr=True,
        target_clearance_ok=True, position_size=150.0, max_safe_leverage=5.0,
        warnings=[],
    )
    base.update(overrides)
    return RiskPlan(**base)


def make_signal_decision(**overrides):
    base = dict(
        decision=Decision.LONG, direction=Direction.LONG, quality_grade=QualityGrade.B,
        gates={"G1_DATA_VALID": True, "G9_QUALITY_THRESHOLD": True}, reasons=["SETUP_CONFIRMED"],
        warnings=[],
    )
    base.update(overrides)
    return SignalDecision(**base)


def make_signal_record(**overrides):
    base = dict(
        id="sig_001", symbol="BTC", timeframe=Timeframe.H1, market_type=MarketType.SPOT,
        timestamp=NOW, decision=Decision.LONG, direction=Direction.LONG,
        setup_type=SetupType.TREND_CONTINUATION, market_regime=RegimeType.UPTREND,
        setup_score=72.5, historical_probability=None, entry=101.0,
        entry_zone=(100.0, 101.0), stop_loss=95.0, take_profit_1=105.0,
        take_profit_2=110.0, risk_reward=1.7, confirmation_price=101.0,
        invalidation_price=95.0, quality_grade=QualityGrade.B,
        reasons=["SETUP_CONFIRMED"], warnings=[], data_quality=make_data_quality(),
        confluence=make_confluence_result(), entry_plan=make_entry_plan(),
        risk_plan=make_risk_plan(), feature_snapshot=make_feature_set(),
        regime_snapshot=make_regime(), status="PENDING",
    )
    base.update(overrides)
    return SignalRecord(**base)


def make_shadow_outcome(**overrides):
    base = dict(
        signal_id="sig_001", opened_at=NOW, closed_at=None, exit_price=None,
        status="OPEN", pnl_pct=None, mae_pct=None, mfe_pct=None,
        time_to_outcome_minutes=None,
    )
    base.update(overrides)
    return ShadowOutcome(**base)


ALL_BUILDERS = {
    "CandleData": make_candle,
    "MarketData": make_market_data,
    "DataQuality": make_data_quality,
    "FeatureSet": make_feature_set,
    "MarketRegime": make_regime,
    "SetupCandidate": make_setup_candidate,
    "EvidenceItem": make_evidence_item,
    "ConfluenceResult": make_confluence_result,
    "EntryPlan": make_entry_plan,
    "RiskPlan": make_risk_plan,
    "SignalDecision": make_signal_decision,
    "SignalRecord": make_signal_record,
    "ShadowOutcome": make_shadow_outcome,
}


# ---------------------------------------------------------------------------
# 1. Enum values
# ---------------------------------------------------------------------------

def test_market_type_values():
    assert {m.value for m in MarketType} == {"spot", "futures"}


def test_timeframe_values():
    assert {t.value for t in Timeframe} == {
        "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w",
    }


def test_direction_values():
    assert {d.value for d in Direction} == {"LONG", "SHORT", "NEUTRAL"}


def test_decision_values():
    assert {d.value for d in Decision} == {"LONG", "SHORT", "WAIT", "NO_TRADE"}


def test_decision_enum_has_no_neutral_member():
    assert "NEUTRAL" not in Decision.__members__
    with pytest.raises(ValueError):
        Decision("NEUTRAL")


def test_data_quality_state_values():
    assert {s.value for s in DataQualityState} == {"VALID", "DEGRADED", "UNAVAILABLE"}


def test_regime_type_values():
    assert {r.value for r in RegimeType} == {
        "STRONG_UPTREND", "UPTREND", "RANGE", "DOWNTREND", "STRONG_DOWNTREND",
        "HIGH_VOLATILITY", "LOW_VOLATILITY", "TRANSITION", "UNKNOWN",
    }


def test_setup_type_values():
    assert {s.value for s in SetupType} == {
        "TREND_CONTINUATION", "PULLBACK", "BREAKOUT_RETEST", "REVERSAL",
        "RANGE_MEAN_REVERSION",
    }


def test_evidence_category_values():
    assert {c.value for c in EvidenceCategory} == {
        "TREND", "MOMENTUM", "STRUCTURE", "VOLATILITY", "VOLUME", "HTF",
        "FLOW", "SENTIMENT",
    }


def test_quality_grade_values():
    assert {g.value for g in QualityGrade} == {"A", "B", "C", "F"}


# ---------------------------------------------------------------------------
# 2. Valid construction -- every model builds successfully with sane data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,builder", list(ALL_BUILDERS.items()))
def test_valid_construction(name, builder):
    instance = builder()
    assert instance is not None
    assert dataclasses.is_dataclass(instance)


def test_signal_record_wait_variant_constructs():
    rec = make_signal_record(
        decision=Decision.WAIT, direction=None, setup_type=SetupType.PULLBACK,
        entry=None, entry_zone=None, stop_loss=None, take_profit_1=None,
        take_profit_2=None, risk_reward=None, confirmation_price=None,
        invalidation_price=None, entry_plan=None, risk_plan=None, status="PENDING",
    )
    assert rec.decision == Decision.WAIT
    assert rec.direction is None


def test_signal_record_no_trade_variant_constructs():
    rec = make_signal_record(
        decision=Decision.NO_TRADE, direction=None, setup_type=None,
        entry=None, entry_zone=None, stop_loss=None, take_profit_1=None,
        take_profit_2=None, risk_reward=None, confirmation_price=None,
        invalidation_price=None, entry_plan=None, risk_plan=None,
        quality_grade=QualityGrade.F, status="INVALIDATED",
    )
    assert rec.decision == Decision.NO_TRADE
    assert rec.direction is None
    assert rec.setup_type is None


# ---------------------------------------------------------------------------
# 3. Invalid / ambiguous states -- each enforceable rule raises ValueError
# ---------------------------------------------------------------------------

def test_candle_high_below_low_rejected():
    with pytest.raises(ValueError):
        make_candle(high=90.0, low=95.0)


def test_candle_open_outside_high_low_rejected():
    with pytest.raises(ValueError):
        make_candle(open=200.0)


def test_candle_close_outside_high_low_rejected():
    with pytest.raises(ValueError):
        make_candle(close=200.0)


def test_candle_negative_volume_rejected():
    with pytest.raises(ValueError):
        make_candle(volume=-1.0)


def test_market_data_negative_price_rejected():
    with pytest.raises(ValueError):
        make_market_data(live_price=-5.0)


def test_market_data_empty_symbol_rejected():
    with pytest.raises(ValueError):
        make_market_data(symbol="")


def test_data_quality_negative_candle_count_rejected():
    with pytest.raises(ValueError):
        make_data_quality(candle_count=-1)


def test_data_quality_excludes_non_unavailable_source_rejected():
    with pytest.raises(ValueError):
        make_data_quality(
            per_source={"orderbook": DataQualityState.VALID},
            excluded_sources=["orderbook"],
        )


def test_feature_set_completeness_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_feature_set(completeness=1.5)


def test_feature_set_rsi_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_feature_set(rsi14=150.0)


def test_feature_set_negative_atr_rejected():
    with pytest.raises(ValueError):
        make_feature_set(atr=-1.0)


def test_feature_set_bad_divergence_label_rejected():
    with pytest.raises(ValueError):
        make_feature_set(divergence="SIDEWAYS")


def test_feature_set_nonpositive_close_rejected():
    with pytest.raises(ValueError):
        make_feature_set(close=0.0)


def test_market_regime_trend_strength_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_regime(trend_strength=1.5)


def test_market_regime_volatility_percentile_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_regime(volatility_percentile=-0.1)


def test_setup_candidate_neutral_direction_rejected():
    with pytest.raises(ValueError):
        make_setup_candidate(direction=Direction.NEUTRAL)


def test_setup_candidate_confirmation_without_prerequisites_rejected():
    with pytest.raises(ValueError):
        make_setup_candidate(prerequisites_met=False, confirmation_met=True)


def test_setup_candidate_nonpositive_invalidation_price_rejected():
    with pytest.raises(ValueError):
        make_setup_candidate(invalidation_price=0.0)


def test_evidence_item_strength_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_evidence_item(strength=1.1)


def test_evidence_item_empty_detail_rejected():
    with pytest.raises(ValueError):
        make_evidence_item(detail="")


def test_evidence_item_neutral_direction_is_allowed():
    # NEUTRAL is legitimate at the evidence level -- a source can honestly
    # report "no lean." This is the deliberate contrast with the rejections
    # in confluence/entry/setup above.
    item = make_evidence_item(direction=Direction.NEUTRAL, strength=0.0)
    assert item.direction == Direction.NEUTRAL


def test_confluence_result_neutral_proposed_direction_rejected():
    with pytest.raises(ValueError):
        make_confluence_result(proposed_direction=Direction.NEUTRAL)


def test_confluence_result_score_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_confluence_result(setup_quality_score=150.0)


def test_confluence_result_category_score_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_confluence_result(category_scores={EvidenceCategory.TREND: 2.0})


def test_confluence_result_category_both_available_and_excluded_rejected():
    with pytest.raises(ValueError):
        make_confluence_result(
            categories_available=[EvidenceCategory.FLOW],
            categories_excluded=[EvidenceCategory.FLOW],
        )


def test_entry_plan_neutral_direction_rejected():
    with pytest.raises(ValueError):
        make_entry_plan(direction=Direction.NEUTRAL)


def test_entry_plan_inverted_zone_rejected():
    with pytest.raises(ValueError):
        make_entry_plan(entry_zone_low=110.0, entry_zone_high=100.0)


def test_entry_plan_nonpositive_price_rejected():
    with pytest.raises(ValueError):
        make_entry_plan(confirmation_price=0.0)


def test_entry_plan_zero_ttl_rejected():
    with pytest.raises(ValueError):
        make_entry_plan(staleness_ttl_minutes=0.0)


def test_risk_plan_nonpositive_sl_rejected():
    with pytest.raises(ValueError):
        make_risk_plan(stop_loss=0.0)


def test_risk_plan_negative_rr_rejected():
    with pytest.raises(ValueError):
        make_risk_plan(risk_reward_1=-0.5)


def test_risk_plan_nonpositive_position_size_rejected():
    with pytest.raises(ValueError):
        make_risk_plan(position_size=0.0)


def test_shadow_outcome_closed_before_opened_rejected():
    with pytest.raises(ValueError):
        make_shadow_outcome(
            closed_at=NOW - timedelta(hours=1), exit_price=100.0, status="SL_HIT",
        )


def test_shadow_outcome_negative_mae_rejected():
    with pytest.raises(ValueError):
        make_shadow_outcome(mae_pct=-0.5)


def test_shadow_outcome_bad_status_rejected():
    with pytest.raises(ValueError):
        make_shadow_outcome(status="NOT_A_REAL_STATUS")


def test_signal_record_bad_status_rejected():
    with pytest.raises(ValueError):
        make_signal_record(status="NOT_A_REAL_STATUS")


def test_signal_record_score_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_signal_record(setup_score=101.0)


def test_signal_record_probability_out_of_range_rejected():
    with pytest.raises(ValueError):
        make_signal_record(historical_probability=1.5)


def test_signal_record_inverted_entry_zone_rejected():
    with pytest.raises(ValueError):
        make_signal_record(entry_zone=(110.0, 100.0))


def test_signal_record_bad_schema_version_rejected():
    with pytest.raises(ValueError):
        make_signal_record(schema_version=0)


# ---------------------------------------------------------------------------
# 4. Immutability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,builder", list(ALL_BUILDERS.items()))
def test_immutable(name, builder):
    instance = builder()
    first_field = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, first_field, getattr(instance, first_field))


def test_signal_record_candles_list_not_swappable():
    rec = make_signal_record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.status = "OPEN"


# ---------------------------------------------------------------------------
# 5. Serialization round-trips
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,builder", list(ALL_BUILDERS.items()))
def test_to_dict_from_dict_roundtrip(name, builder):
    instance = builder()
    d = instance.to_dict()
    assert isinstance(d, dict)
    restored = type(instance).from_dict(d)
    assert restored == instance


@pytest.mark.parametrize("name,builder", list(ALL_BUILDERS.items()))
def test_to_json_from_json_roundtrip(name, builder):
    instance = builder()
    s = instance.to_json()
    assert isinstance(s, str)
    json.loads(s)  # must be valid JSON on its own, independent of our decoder
    restored = type(instance).from_json(s)
    assert restored == instance


def test_signal_record_full_nesting_roundtrip_preserves_every_layer():
    rec = make_signal_record()
    d = rec.to_dict()
    # spot check nested structures survived as plain JSON-safe primitives
    assert d["decision"] == "LONG"
    assert d["confluence"]["proposed_direction"] == "LONG"
    assert d["confluence"]["category_scores"] == {"TREND": 0.7, "MOMENTUM": 0.3}
    assert d["entry_zone"] == [100.0, 101.0]
    assert d["feature_snapshot"]["rsi14"] == 55.0
    assert d["data_quality"]["excluded_sources"] == ["funding"]

    restored = SignalRecord.from_dict(d)
    assert restored == rec
    assert isinstance(restored.confluence, ConfluenceResult)
    assert isinstance(restored.feature_snapshot, FeatureSet)
    assert isinstance(restored.data_quality, DataQuality)
    assert restored.confluence.category_scores[EvidenceCategory.TREND] == 0.7


# ---------------------------------------------------------------------------
# 6. WAIT vs. NO_TRADE distinction
# ---------------------------------------------------------------------------

def test_wait_and_no_trade_are_distinct_decision_values():
    assert Decision.WAIT != Decision.NO_TRADE
    assert Decision.WAIT.value == "WAIT"
    assert Decision.NO_TRADE.value == "NO_TRADE"


def test_wait_and_no_trade_both_valid_and_distinct_from_long_short():
    for d in (Decision.WAIT, Decision.NO_TRADE):
        assert d not in (Decision.LONG, Decision.SHORT)


def test_signal_decision_wait_requires_no_direction():
    sd = make_signal_decision(decision=Decision.WAIT, direction=None)
    assert sd.decision == Decision.WAIT
    assert sd.direction is None


def test_signal_decision_no_trade_requires_no_direction():
    sd = make_signal_decision(decision=Decision.NO_TRADE, direction=None)
    assert sd.decision == Decision.NO_TRADE
    assert sd.direction is None


# ---------------------------------------------------------------------------
# 7. Direction.NEUTRAL cannot become a final decision
# ---------------------------------------------------------------------------

def test_decision_type_itself_has_no_neutral_option():
    # The strongest guarantee: the type doesn't even have the value to assign.
    assert not hasattr(Decision, "NEUTRAL")


def test_signal_decision_rejects_neutral_direction_with_long_decision():
    with pytest.raises(ValueError):
        make_signal_decision(decision=Decision.LONG, direction=Direction.NEUTRAL)


def test_signal_decision_rejects_neutral_direction_with_short_decision():
    with pytest.raises(ValueError):
        make_signal_decision(decision=Decision.SHORT, direction=Direction.NEUTRAL)


def test_signal_decision_rejects_neutral_direction_with_wait_decision():
    with pytest.raises(ValueError):
        make_signal_decision(decision=Decision.WAIT, direction=Direction.NEUTRAL)


def test_signal_decision_rejects_neutral_direction_with_no_trade_decision():
    with pytest.raises(ValueError):
        make_signal_decision(decision=Decision.NO_TRADE, direction=Direction.NEUTRAL)


def test_signal_record_rejects_neutral_direction_with_long_decision():
    with pytest.raises(ValueError):
        make_signal_record(decision=Decision.LONG, direction=Direction.NEUTRAL)


def test_signal_record_rejects_neutral_direction_with_wait_decision():
    with pytest.raises(ValueError):
        make_signal_record(decision=Decision.WAIT, direction=Direction.NEUTRAL)


def test_signal_record_rejects_mismatched_long_short_direction():
    # decision=LONG but direction=SHORT is just as invalid as NEUTRAL would be
    with pytest.raises(ValueError):
        make_signal_record(decision=Decision.LONG, direction=Direction.SHORT)


# ---------------------------------------------------------------------------
# 8. Optional unavailable-data fields remain explicit (never silently filled)
# ---------------------------------------------------------------------------

def test_feature_set_allows_and_preserves_none_indicators():
    fs = make_feature_set(
        ema200=None, macd_line=None, macd_signal=None, macd_hist=None,
        swing_support=None, swing_resistance=None, divergence=None,
    )
    assert fs.ema200 is None
    assert fs.macd_line is None
    assert fs.divergence is None

    restored = FeatureSet.from_dict(fs.to_dict())
    assert restored.ema200 is None
    assert restored.macd_line is None
    assert restored.divergence is None
    # explicit None must survive round-trip -- not become 0.0 or "N/A" or similar
    assert restored == fs


def test_signal_record_allows_none_historical_probability_and_it_survives_roundtrip():
    rec = make_signal_record(historical_probability=None)
    assert rec.historical_probability is None
    restored = SignalRecord.from_dict(rec.to_dict())
    assert restored.historical_probability is None


def test_market_data_missing_live_price_stays_none_not_zero():
    md = make_market_data(live_price=None, price_quality=DataQualityState.UNAVAILABLE)
    assert md.live_price is None
    restored = MarketData.from_dict(md.to_dict())
    assert restored.live_price is None


def test_shadow_outcome_open_position_has_all_outcome_fields_none():
    so = make_shadow_outcome()
    assert so.closed_at is None
    assert so.exit_price is None
    assert so.pnl_pct is None
    assert so.mae_pct is None
    assert so.mfe_pct is None
    restored = ShadowOutcome.from_dict(so.to_dict())
    assert restored.pnl_pct is None
    assert restored.mae_pct is None


# ---------------------------------------------------------------------------
# 9. No score field is named or typed as probability
# ---------------------------------------------------------------------------

ALL_MODEL_CLASSES = [
    CandleData, MarketData, DataQuality, FeatureSet, MarketRegime,
    SetupCandidate, EvidenceItem, ConfluenceResult, EntryPlan, RiskPlan,
    SignalDecision, SignalRecord, ShadowOutcome,
]


def test_only_one_field_across_all_models_is_named_probability():
    probability_fields = []
    for cls in ALL_MODEL_CLASSES:
        for f in dataclasses.fields(cls):
            if "probability" in f.name.lower():
                probability_fields.append(f"{cls.__name__}.{f.name}")
    assert probability_fields == ["SignalRecord.historical_probability"], (
        f"Expected exactly one probability-named field in the whole package, "
        f"found: {probability_fields}"
    )


def test_score_fields_are_not_named_probability():
    score_field_names = {"setup_score", "setup_quality_score"}
    found = []
    for cls in ALL_MODEL_CLASSES:
        for f in dataclasses.fields(cls):
            if f.name in score_field_names:
                found.append((cls.__name__, f.name))
                assert "probability" not in f.name.lower()
    # sanity: we actually found the score fields we expected to check
    names_found = {name for _, name in found}
    assert "setup_score" in names_found        # on SignalRecord
    assert "setup_quality_score" in names_found  # on ConfluenceResult


def test_historical_probability_field_type_allows_none():
    hints = dataclasses.fields(SignalRecord)
    prob_field = next(f for f in hints if f.name == "historical_probability")
    # Optional[float] -- must be able to hold None (see construction test above);
    # this test asserts the field exists with that exact name so a future rename
    # back toward something ambiguous (e.g. "confidence") would be caught.
    assert prob_field.name == "historical_probability"
    rec = make_signal_record(historical_probability=None)
    assert rec.historical_probability is None
    rec2 = make_signal_record(historical_probability=0.42)
    assert rec2.historical_probability == 0.42


def test_setup_quality_score_is_bounded_0_to_100_not_0_to_1():
    # A probability lives on [0,1]; this score deliberately lives on [0,100]
    # so the two can never be confused by range alone either.
    cr = make_confluence_result(setup_quality_score=100.0)
    assert cr.setup_quality_score == 100.0
    # a value that IS a valid probability (within [0,1]) is also a valid low
    # score here -- the two scales overlap at the bottom, which is exactly
    # why the field NAME (previous test) is what has to carry the distinction
    cr_low = make_confluence_result(setup_quality_score=0.5)
    assert cr_low.setup_quality_score == 0.5
    # but this field is genuinely 0-100, not 0-1 -- values only a 0-100 scale
    # would produce must be accepted, and out-of-range values on EITHER
    # interpretation must be rejected
    cr_high = make_confluence_result(setup_quality_score=87.0)
    assert cr_high.setup_quality_score == 87.0
    with pytest.raises(ValueError):
        make_confluence_result(setup_quality_score=100.01)
    with pytest.raises(ValueError):
        make_confluence_result(setup_quality_score=-0.01)
