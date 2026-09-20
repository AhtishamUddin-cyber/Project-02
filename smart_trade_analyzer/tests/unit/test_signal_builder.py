"""Unit tests for signal_assembly/builder.py: build_signal_record and
compile_reasons_and_warnings, exercised against hand-built PipelineResult
objects (constructed directly, not derived from realistic candle data --
PipelineResult is a plain, phase-local dataclass free to construct by hand
for exactly this purpose, the same reason tests/unit/test_quality_gate.py
hand-builds GateContext/RiskPlan/EntryPlan rather than deriving every
scenario from raw candles). tests/integration/test_phase6_pipeline.py
already proves the realistic, end-to-end behavior; these tests instead
pin down build_signal_record's own field-by-field mapping and its
None-safety rules precisely.
"""
from datetime import datetime

from smart_trade_analyzer.contracts import (
    ConfluenceResult, DataQuality, DataQualityState, Decision, Direction, EntryPlan, EvidenceCategory, FeatureSet,
    MarketData, MarketRegime, MarketType, QualityGrade, RegimeType, RiskPlan, SetupCandidate, SetupType,
    SignalDecision, Timeframe,
)
from smart_trade_analyzer.pipeline import PipelineResult
from smart_trade_analyzer.signal_assembly import build_signal_record
from smart_trade_analyzer.signal_assembly.builder import _make_signal_id

NOW = datetime(2026, 8, 27, 12, 0, 0)


def _market_data():
    return MarketData(symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
                       as_of=NOW, candles=[], live_price=100.0, price_source="fake",
                       price_quality=DataQualityState.VALID)


def _dq():
    return DataQuality(overall=DataQualityState.VALID, candle_count=210, candle_count_required=200,
                        per_source={}, excluded_sources=[], reasons=[])


def _feature_set():
    return FeatureSet(
        symbol="BTC", timeframe=Timeframe.M1, as_of=NOW, close=100.0, rsi14=60.0,
        ema9=99.0, ema21=98.0, ema50=95.0, ema200=85.0,
        stoch_rsi_k=55.0, stoch_rsi_d=50.0, macd_line=0.5, macd_signal=0.3, macd_hist=0.2,
        bb_upper=105.0, bb_mid=100.0, bb_lower=95.0, atr=2.0, atr_pct=2.0, volume_ratio=1.1,
        swing_support=90.0, swing_resistance=110.0, divergence=None, completeness=1.0,
    )


def _regime():
    return MarketRegime(regime=RegimeType.STRONG_UPTREND, trend_strength=0.8, volatility_percentile=0.5,
                         basis=["unit test"])


def _setup(direction=Direction.LONG, confirmation_met=True):
    return SetupCandidate(
        setup_type=SetupType.TREND_CONTINUATION, direction=direction, prerequisites_met=True,
        confirmation_met=confirmation_met, invalidation_price=95.0,
        evidence_refs=["regime=STRONG_UPTREND", "rsi14=60.00 in [45.0, 80.0]"],
        failed_conditions=[] if confirmation_met else ["macd_hist does not confirm"],
    )


def _confluence(direction=Direction.LONG, score=70.0):
    return ConfluenceResult(
        proposed_direction=direction,
        category_scores={EvidenceCategory.TREND: 0.6, EvidenceCategory.MOMENTUM: 0.4, EvidenceCategory.STRUCTURE: -0.5},
        categories_available=[EvidenceCategory.TREND, EvidenceCategory.MOMENTUM, EvidenceCategory.STRUCTURE],
        categories_excluded=[EvidenceCategory.HTF, EvidenceCategory.FLOW, EvidenceCategory.SENTIMENT],
        setup_quality_score=score, evidence=[], conflicts=[EvidenceCategory.STRUCTURE],
    )


def _entry_plan(direction=Direction.LONG):
    return EntryPlan(
        direction=direction, entry_zone_low=99.0, entry_zone_high=101.0, confirmation_price=100.0,
        invalidation_price=95.0, max_chase_distance=2.0, structure_clearance_ok=True,
        generated_at=NOW, staleness_ttl_minutes=150.0, is_stale=False,
    )


def _risk_plan():
    return RiskPlan(
        stop_loss=97.0, take_profit_1=103.0, take_profit_2=105.0, risk_reward_1=2.0, risk_reward_2=3.0,
        meets_min_rr=True, target_clearance_ok=True, position_size=None, max_safe_leverage=10.0,
        warnings=["Advisory comfort leverage 10.0x (hard safety ceiling 20.0x -- never exceed this)"],
    )


def _signal_decision(decision=Decision.LONG, direction=Direction.LONG, grade=QualityGrade.B):
    return SignalDecision(
        decision=decision, direction=direction,
        gates={"G1_DATA_VALID": True, "G9_QUALITY_THRESHOLD": decision in (Decision.LONG, Decision.SHORT)},
        quality_grade=grade, reasons=["setup confirmed", "direction aligned"], warnings=[],
    )


def _full_pipeline_result(**overrides):
    kwargs = dict(
        market_data=_market_data(), data_quality=_dq(), evaluated_at=NOW, current_price=100.0,
        feature_set=_feature_set(), regime=_regime(), setup=_setup(), confluence=_confluence(),
        entry_plan=_entry_plan(), risk_plan=_risk_plan(), signal_decision=_signal_decision(),
    )
    kwargs.update(overrides)
    return PipelineResult(**kwargs)


# ---------------------------------------------------------------------------
# None-safety: exactly the fields SignalRecord itself requires as non-
# Optional (confluence, feature_snapshot, regime_snapshot -- see
# contracts/signal_record.py) must each independently block record
# construction when missing.
# ---------------------------------------------------------------------------

def test_no_record_when_setup_missing():
    result = _full_pipeline_result(setup=None, confluence=None, entry_plan=None, risk_plan=None)
    assert build_signal_record(result) is None


def test_no_record_when_confluence_missing_even_with_a_setup():
    result = _full_pipeline_result(confluence=None)
    assert build_signal_record(result) is None


def test_no_record_when_feature_set_missing():
    result = _full_pipeline_result(feature_set=None)
    assert build_signal_record(result) is None


def test_no_record_when_regime_missing():
    result = _full_pipeline_result(regime=None)
    assert build_signal_record(result) is None


def test_no_record_when_signal_decision_missing():
    result = _full_pipeline_result(signal_decision=None)
    assert build_signal_record(result) is None


def test_record_still_built_without_entry_or_risk_plan():
    # A confirmed setup with a real ConfluenceResult but no buildable
    # EntryPlan (e.g. build_entry_plan returned None) is still an honest,
    # embeddable record -- entry/risk fields on SignalRecord are Optional
    # for exactly this reason.
    result = _full_pipeline_result(entry_plan=None, risk_plan=None,
                                    signal_decision=_signal_decision(decision=Decision.WAIT, direction=None))
    record = build_signal_record(result)
    assert record is not None
    assert record.entry is None and record.entry_zone is None
    assert record.stop_loss is None and record.take_profit_1 is None and record.take_profit_2 is None
    assert record.risk_reward is None
    # invalidation_price falls back to the setup's own value when there is no EntryPlan to read it from
    assert record.invalidation_price == 95.0


# ---------------------------------------------------------------------------
# Field-by-field mapping, with everything present.
# ---------------------------------------------------------------------------

def test_full_record_field_mapping():
    result = _full_pipeline_result()
    record = build_signal_record(result)
    assert record is not None

    assert record.symbol == "BTC"
    assert record.timeframe == Timeframe.M1
    assert record.market_type == MarketType.SPOT
    assert record.timestamp == NOW
    assert record.decision == Decision.LONG
    assert record.direction == Direction.LONG
    assert record.setup_type == SetupType.TREND_CONTINUATION
    assert record.market_regime == RegimeType.STRONG_UPTREND
    assert record.setup_score == 70.0
    assert record.historical_probability is None  # never fabricated -- no calibration phase yet

    # entry and confirmation_price are the SAME canonical reference price
    assert record.entry == 100.0
    assert record.confirmation_price == 100.0
    assert record.entry == record.confirmation_price
    assert record.entry_zone == (99.0, 101.0)
    assert record.invalidation_price == 95.0
    assert record.stop_loss == 97.0
    assert record.take_profit_1 == 103.0
    assert record.take_profit_2 == 105.0
    assert record.risk_reward == 2.0  # risk_reward_1, not risk_reward_2 -- see builder.py's own comment on why

    assert record.quality_grade == QualityGrade.B
    assert record.data_quality is result.data_quality
    assert record.confluence is result.confluence
    assert record.entry_plan is result.entry_plan
    assert record.risk_plan is result.risk_plan
    assert record.feature_snapshot is result.feature_set
    assert record.regime_snapshot is result.regime
    assert record.status == "PENDING"
    assert record.schema_version == 1


def test_reasons_include_gate_trail_and_setup_evidence_and_category_support():
    result = _full_pipeline_result()
    record = build_signal_record(result)
    assert "setup confirmed" in record.reasons          # from signal_decision.reasons
    assert "direction aligned" in record.reasons        # from signal_decision.reasons
    assert "regime=STRONG_UPTREND" in record.reasons     # from setup.evidence_refs
    assert "TREND supports proposed direction (LONG)" in record.reasons       # category_scores[TREND]=0.6 > 0
    assert "MOMENTUM supports proposed direction (LONG)" in record.reasons    # category_scores[MOMENTUM]=0.4 > 0
    assert "risk/reward acceptable (2.00:1 on TP1)" in record.reasons         # risk_plan.meets_min_rr=True


def test_warnings_include_conflicts_and_risk_warnings():
    result = _full_pipeline_result()
    record = build_signal_record(result)
    assert "STRUCTURE conflicts with proposed direction (LONG)" in record.warnings  # in confluence.conflicts
    assert any("comfort leverage" in w for w in record.warnings)                     # from risk_plan.warnings
    # a category that conflicts must never ALSO appear as a supporting reason
    assert not any("STRUCTURE supports" in r for r in record.reasons)


def test_degraded_data_quality_reasons_become_warnings():
    dq = DataQuality(overall=DataQualityState.DEGRADED, candle_count=60, candle_count_required=200,
                      per_source={}, excluded_sources=["one_source"], reasons=["one source excluded from consensus"])
    result = _full_pipeline_result(data_quality=dq)
    record = build_signal_record(result)
    assert "one source excluded from consensus" in record.warnings


def test_reasons_and_warnings_have_no_exact_duplicates():
    # setup.evidence_refs and signal_decision.reasons could in principle
    # overlap; compile_reasons_and_warnings collapses exact repeats.
    decision = _signal_decision()
    setup = SetupCandidate(
        setup_type=SetupType.TREND_CONTINUATION, direction=Direction.LONG, prerequisites_met=True,
        confirmation_met=True, invalidation_price=95.0, evidence_refs=["setup confirmed"], failed_conditions=[],
    )
    result = _full_pipeline_result(setup=setup, signal_decision=decision)
    record = build_signal_record(result)
    assert record.reasons.count("setup confirmed") == 1


# ---------------------------------------------------------------------------
# Signal id: deterministic (same identity tuple -> same id), and sensitive
# to each part of the identity tuple (different setup/direction/as_of ->
# different id) -- see builder.py's own docstring for why it is a hash of
# identity, not a random UUID.
# ---------------------------------------------------------------------------

def test_signal_id_is_deterministic():
    result = _full_pipeline_result()
    id_a = _make_signal_id(result, SetupType.TREND_CONTINUATION, Direction.LONG)
    id_b = _make_signal_id(result, SetupType.TREND_CONTINUATION, Direction.LONG)
    assert id_a == id_b
    assert len(id_a) == 24


def test_signal_id_changes_with_direction():
    result = _full_pipeline_result()
    id_long = _make_signal_id(result, SetupType.TREND_CONTINUATION, Direction.LONG)
    id_short = _make_signal_id(result, SetupType.TREND_CONTINUATION, Direction.SHORT)
    assert id_long != id_short


def test_signal_id_changes_with_setup_type():
    result = _full_pipeline_result()
    id_trend = _make_signal_id(result, SetupType.TREND_CONTINUATION, Direction.LONG)
    id_pullback = _make_signal_id(result, SetupType.PULLBACK, Direction.LONG)
    assert id_trend != id_pullback


def test_signal_id_changes_with_as_of():
    result_a = _full_pipeline_result()
    result_b = _full_pipeline_result(market_data=MarketData(
        symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
        as_of=datetime(2026, 8, 27, 13, 0, 0), candles=[], live_price=100.0, price_source="fake",
        price_quality=DataQualityState.VALID,
    ))
    id_a = _make_signal_id(result_a, SetupType.TREND_CONTINUATION, Direction.LONG)
    id_b = _make_signal_id(result_b, SetupType.TREND_CONTINUATION, Direction.LONG)
    assert id_a != id_b


def test_two_records_from_the_same_snapshot_are_fully_equal():
    result_a = _full_pipeline_result()
    result_b = _full_pipeline_result()
    record_a = build_signal_record(result_a)
    record_b = build_signal_record(result_b)
    assert record_a == record_b
