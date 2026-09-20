"""Phase 5 integration tests: the full chain from MarketData through a
final SignalDecision (Setup -> Confluence -> Entry -> Risk -> Quality
Gate), and a capstone no-lookahead proof spanning entry+risk+the gate
together. Unit tests (tests/unit/test_entry.py, test_risk.py,
test_quality_gate.py) already cover each stage's own rules in isolation
with tightly-controlled fixtures; these tests check the stages compose
correctly across realistic, varied data.
"""
import random
from datetime import datetime, timedelta

from smart_trade_analyzer.confluence import compute_confluence
from smart_trade_analyzer.contracts import (
    CandleData, DataQuality, DataQualityState, Decision, Direction, MarketData, MarketType, Timeframe,
)
from smart_trade_analyzer.entry import build_entry_plan
from smart_trade_analyzer.features import compute_feature_set
from smart_trade_analyzer.quality_gate import GateContext, evaluate
from smart_trade_analyzer.regime import classify_regime
from smart_trade_analyzer.risk import build_risk_plan
from smart_trade_analyzer.setup import detect_setup

NOW = datetime(2026, 8, 27, 12, 0, 0)


def realistic_candles_with_wicks(n, seed=1, base=100.0, drift=0.0, noise=0.025):
    random.seed(seed)
    closes = [base]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    out = []
    price = base
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = price
        body_high, body_low = max(o, c), min(o, c)
        wick_up = body_high * random.uniform(0.001, 0.01)
        wick_down = body_low * random.uniform(0.001, 0.01)
        out.append(CandleData(open_time=t, open=o, high=body_high + wick_up, low=body_low - wick_down,
                               close=c, volume=random.uniform(80, 120), is_closed=True))
        price = c
    return out


def build_md(candles):
    return MarketData(symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
                       as_of=NOW, candles=candles, live_price=candles[-1].close if candles else None,
                       price_source="fake", price_quality=DataQualityState.VALID)


def run_full_chain(candles, evaluated_at=NOW):
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    setup = detect_setup(candles, fs, regime)
    confluence = compute_confluence(setup, fs, regime, candles) if setup is not None else None
    entry_plan = build_entry_plan(setup, fs, candles) if setup is not None else None
    risk_plan = None
    if entry_plan is not None:
        risk_plan = build_risk_plan(
            setup.direction, entry_plan.confirmation_price, fs.atr, fs.swing_support, fs.swing_resistance,
        )
    dq = DataQuality(overall=DataQualityState.VALID, candle_count=len(candles), candle_count_required=200,
                      per_source={}, excluded_sources=[], reasons=[])
    ctx = GateContext(
        data_quality=dq, as_of=fs.as_of, timeframe=fs.timeframe, setup=setup, confluence=confluence,
        entry_plan=entry_plan, risk_plan=risk_plan, evaluated_at=evaluated_at, current_price=fs.close,
    )
    return evaluate(ctx), fs, setup, confluence, entry_plan, risk_plan


def test_full_chain_never_raises_and_stays_internally_consistent_across_many_seeds():
    decisions_seen = set()
    for seed in range(60):
        candles = realistic_candles_with_wicks(210, seed=seed, drift=random.Random(seed).uniform(-0.004, 0.004))
        result, fs, setup, confluence, entry_plan, risk_plan = run_full_chain(candles)
        decisions_seen.add(result.decision)

        assert len(result.gates) > 0
        assert result.quality_grade is not None
        if result.decision in (Decision.LONG, Decision.SHORT):
            assert result.direction == setup.direction
            assert all(result.gates.values())
            assert confluence.proposed_direction == setup.direction
        else:
            assert result.direction is None

    # across 60 varied seeds, the sweep should genuinely exercise more
    # than one outcome -- otherwise this test isn't really testing the gate logic
    assert len(decisions_seen) > 1


def test_no_trade_when_no_setup_is_detected():
    flat = [
        CandleData(open_time=NOW - timedelta(minutes=(210 - i)), open=100.0, high=100.05, low=99.95,
                   close=100.0, volume=100.0, is_closed=True)
        for i in range(210)
    ]
    result, fs, setup, confluence, entry_plan, risk_plan = run_full_chain(flat)
    assert setup is None
    assert confluence is None and entry_plan is None and risk_plan is None
    assert result.decision == Decision.NO_TRADE
    assert result.direction is None


def test_full_chain_no_lookahead_wild_forming_candle():
    # Verified fixture (seed chosen empirically) that reaches a setup and
    # produces a genuine, non-trivial gate evaluation -- not vacuous.
    candles = realistic_candles_with_wicks(210, seed=3, drift=0.003, noise=0.02)
    baseline, fs_a, setup_a, *_ = run_full_chain(candles)
    assert setup_a is not None, "fixture must produce a real setup, or this test is vacuous"

    wild_forming = CandleData(
        open_time=NOW, open=candles[-1].close, high=candles[-1].close * 200,
        low=candles[-1].close * 0.01, close=candles[-1].close * 150, volume=999999.0, is_closed=False,
    )
    with_forming, fs_b, setup_b, *_ = run_full_chain(candles + [wild_forming])

    assert fs_a == fs_b
    assert setup_a == setup_b
    assert baseline.decision == with_forming.decision
    assert baseline.direction == with_forming.direction
    assert baseline.gates == with_forming.gates


def test_decision_direction_always_matches_the_confirmed_setup_never_invented():
    for seed in range(30):
        candles = realistic_candles_with_wicks(210, seed=seed + 500, drift=random.Random(seed).uniform(-0.005, 0.005))
        result, fs, setup, confluence, entry_plan, risk_plan = run_full_chain(candles)
        if result.decision in (Decision.LONG, Decision.SHORT):
            assert setup is not None and setup.confirmation_met
            assert result.direction == setup.direction
