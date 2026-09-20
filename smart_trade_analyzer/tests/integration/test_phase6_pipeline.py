"""Phase 6 integration tests: the full chain from MarketData through
pipeline.run_pipeline and signal_assembly.build_signal_record, and
scanner.scan_symbol end to end.

Fixture-building conventions (NOW, realistic_candles*, build_md) mirror
tests/integration/test_phase5_pipeline.py and test_full_pipeline.py
exactly, for the same reason those two files share them: independent
per-candle wicks are required for structure/divergence detection to
engage at all (see test_full_pipeline.py's realistic_candles_with_wicks
docstring). Every "happy path" fixture below was found the same way this
project's existing fixtures document they were (test_setup.py: "Seed 2
was searched for and confirmed during development") -- by running the
real detectors against candidate data and keeping what genuinely
produces the state under test, never by asserting what a fixture "should"
do without having run it.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.contracts import (
    CandleData, DataQuality, DataQualityState, Decision, Direction, MarketData, MarketType, RegimeType,
    RiskPlan, SetupType, Timeframe,
)
from smart_trade_analyzer.data import NormalizationResult
from smart_trade_analyzer.data.models import TickerPrice
from smart_trade_analyzer.pipeline import run_pipeline
from smart_trade_analyzer.quality_gate import GateContext, evaluate
from smart_trade_analyzer.scanner import OpportunityResult, scan_symbol
from smart_trade_analyzer.signal_assembly import build_signal_record

NOW = datetime(2026, 8, 27, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixture-building helpers -- same shapes as test_phase5_pipeline.py /
# test_full_pipeline.py, duplicated here rather than imported, matching this
# project's own established convention of each integration test file owning
# its fixture builders (see test_phase5_pipeline.py existing alongside
# test_full_pipeline.py with near-identical helpers already).
# ---------------------------------------------------------------------------

def realistic_candles(n, drift=0.0, noise=0.01, base=100.0, seed=1):
    """No-wick variant -- matches tests/unit/test_setup.py's own helper of
    the same name, used there to find the TREND_CONTINUATION seeds this
    file reuses below (seed=2 => confirmed LONG, seed=1 => confirmed
    SHORT, both at drift=+/-0.003 -- "confirmed during development" per
    that file's own comment)."""
    random.seed(seed)
    closes = [base]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    out = []
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = closes[i - 1] if i > 0 else c
        out.append(CandleData(open_time=t, open=o, high=max(o, c) * 1.001, low=min(o, c) * 0.999,
                               close=c, volume=100.0, is_closed=True))
    return out


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


def build_md(candles, as_of=NOW, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
             timeframe=Timeframe.M1, live_price="close"):
    return MarketData(
        symbol=symbol, pair=pair, market_type=market_type, timeframe=timeframe, as_of=as_of, candles=candles,
        live_price=(candles[-1].close if live_price == "close" else live_price) if candles else None,
        price_source="fake", price_quality=DataQualityState.VALID,
    )


def valid_dq(candles, overall=DataQualityState.VALID, reasons=None):
    return DataQuality(overall=overall, candle_count=len(candles), candle_count_required=200,
                        per_source={}, excluded_sources=[], reasons=reasons or [])


def flat_candles(n=210, level=100.0):
    return [
        CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=level, high=level + 0.05, low=level - 0.05,
                   close=level, volume=100.0, is_closed=True)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Setup-family-specific fixtures. Each is verified (empirically, while
# building this phase) to make detect_setup return that exact family,
# CONFIRMED, when run through the real classify_regime (never a
# hand-overridden MarketRegime -- run_pipeline has no way to accept one,
# by design: the Quality Gate must see the same regime the rest of the
# pipeline actually used).
# ---------------------------------------------------------------------------

def trend_continuation_long_candles():
    return realistic_candles(210, drift=0.003, noise=0.01, seed=2)


def trend_continuation_short_candles():
    return realistic_candles(210, drift=-0.003, noise=0.01, seed=1)


def pullback_long_candles():
    """A clean rally (60 candles, ~0.5/candle) into a 3-candle pullback
    toward EMA21, with a 2-candle uptick at the end so stoch_rsi %K
    crosses back above %D (PULLBACK's own confirmation condition -- see
    setup/engine.py::_pullback)."""
    random.seed(7)
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] + 0.5 + random.uniform(-0.15, 0.15))
    for _ in range(3):
        closes.append(closes[-1] - 1.5)
    closes.append(closes[-1] + 0.1)
    closes.append(closes[-1] + 0.1)
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = closes[i - 1] if i > 0 else c
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + 0.05, low=min(o, c) - 0.05,
                               close=c, volume=100.0, is_closed=True))
    return out


def _mean_reverting_closes(seed, n=210, kappa=0.05, noise=0.6, base=100.0):
    random.seed(seed)
    closes = [base]
    for _ in range(n - 1):
        nxt = closes[-1] + kappa * (base - closes[-1]) + random.uniform(-noise, noise)
        closes.append(max(nxt, 0.01))
    return closes


def _candles_from_closes(closes, wick=0.05):
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = closes[i - 1] if i > 0 else c
        out.append(CandleData(open_time=t, open=o, high=max(o, c) + wick, low=min(o, c) - wick,
                               close=c, volume=100.0, is_closed=True))
    return out


def range_mean_reversion_long_candles():
    """A mean-reverting oscillator (kappa=0.05 pull toward 100, +/-0.6
    noise) naturally classifies as RANGE and, at seed=121, lands its last
    close within 15% of the lower Bollinger band with stoch_rsi %K deeply
    oversold -- RANGE_MEAN_REVERSION LONG's exact prerequisites (see
    setup/engine.py::_range_mean_reversion)."""
    return _candles_from_closes(_mean_reverting_closes(121))


def range_mean_reversion_short_candles():
    return _candles_from_closes(_mean_reverting_closes(257))


def breakout_retest_long_candles():
    """The Phase 4 BREAKOUT_RETEST fixture (test_full_pipeline.py's
    _breakout_retest_long_candles), with its leading flat segment
    extended from 20 to 40 candles (60 total instead of 40). The original
    40-candle version is exactly what Phase 4's own tests use to drive
    detect_setup() DIRECTLY (bypassing regime) -- correct for testing
    _breakout_retest in isolation, since it has no EMA50/regime
    dependency of its own. run_pipeline, however, faithfully reproduces
    scanner/engine.py's approved short-circuit at regime.regime ==
    UNKNOWN (see pipeline/orchestrator.py) -- and UNKNOWN is exactly what
    a 40-candle series gives (EMA50 needs 50 closed candles). The extra
    flat padding here exists ONLY to clear that pre-existing, approved
    minimum-history gate; it does not touch the breakout/retest price
    action itself, which is untouched from the original."""
    out = []
    n = 60
    base = 60.0
    for i in range(40):
        out.append(CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=base, high=base + 0.3,
                               low=base - 0.3, close=base, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 40)), open=base, high=65.3, low=59.8,
                           close=65.0, volume=100.0, is_closed=True))
    for i in range(41, 46):
        out.append(CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=base, high=base + 0.3,
                               low=base - 0.3, close=base, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 46)), open=base, high=68.5, low=59.9,
                           close=68.0, volume=250.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 47)), open=68.0, high=70.0, low=67.5,
                           close=69.5, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 48)), open=69.5, high=70.5, low=68.5,
                           close=69.0, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 49)), open=69.0, high=69.0, low=65.2,
                           close=65.5, volume=100.0, is_closed=True))
    for i in range(50, n):
        out.append(CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=66.0, high=67.0, low=65.5,
                               close=66.5, volume=100.0, is_closed=True))
    return out


def reversal_short_candles():
    """Identical to test_full_pipeline.py's _reversal_short_candles().
    That file hand-overrides MarketRegime when driving detect_setup()
    directly, on the stated assumption that this fixture's flat opening
    reads as RANGE under classify_regime. Empirically (verified while
    building this phase) that assumption no longer holds for the fixture
    in its final, tuned form: classify_regime actually reads this exact
    series as STRONG_UPTREND on its own, which already satisfies
    REVERSAL SHORT's prerequisite (an opposing established uptrend) --
    so no override is needed here, and none is available to run_pipeline
    regardless (see breakout_retest_long_candles's docstring above for
    why run_pipeline never accepts a hand-picked regime)."""
    closes = [100.0]
    for _ in range(20):
        closes.append(closes[-1])
    for _ in range(8):
        closes.append(closes[-1] + 2.0)
    for _ in range(5):
        closes.append(closes[-1] - 1.3)
    for _ in range(4):
        closes.append(closes[-1] - 0.2)
    for d in [2.0, 1.8, -0.7, 2.0, -0.8, 1.9, -0.6, 2.0, -0.7, 2.1, -0.5, 2.2, -0.8, 2.3, -0.4, 2.4, -0.5, 2.2]:
        closes.append(closes[-1] + d)
    for _ in range(3):
        closes.append(closes[-1] - 2.0)
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i + 1))
        out.append(CandleData(open_time=t, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0, is_closed=True))
    final_close = out[-1].low - 2.0
    out.append(CandleData(open_time=NOW, open=out[-1].close, high=out[-1].close + 0.1,
                           low=final_close - 0.1, close=final_close, volume=100.0, is_closed=True))
    return out


# A wide realistic-with-wicks sweep (drift in [-0.006, 0.006]) reliably
# produces at least one full LONG and one full SHORT decision (every gate
# passing) somewhere in its first couple hundred seeds -- these two were
# found that way and are pinned here so the "valid LONG" / "valid SHORT"
# tests below don't re-search on every run.
FULL_LONG_SEED = 1194
FULL_SHORT_SEED = 194


def wide_sweep_candles(seed):
    # noise defaults to realistic_candles_with_wicks' own default (0.025)
    # -- deliberately NOT overridden, since FULL_LONG_SEED/FULL_SHORT_SEED
    # were found (searched empirically) against that exact default; a
    # different noise value changes the resulting price path entirely,
    # even for the same seed.
    return realistic_candles_with_wicks(210, seed=seed, drift=random.Random(seed).uniform(-0.006, 0.006))


# ---------------------------------------------------------------------------
# 1-5. Pipeline happy paths: one per setup family. "Happy path" here means
# the family reaches detect_setup CONFIRMED and flows all the way through
# to a real SignalDecision + SignalRecord -- not necessarily all the way to
# LONG/SHORT (items 6-7 below cover that specifically; G9's quality
# threshold is a genuinely separate, honest bar these fixtures don't all
# clear, exactly as it shouldn't automatically for hand-built data).
# ---------------------------------------------------------------------------

def _assert_happy_path(candles, expected_setup_type, expected_direction):
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.setup is not None, "fixture must produce a real setup, or this test is vacuous"
    assert result.setup.setup_type == expected_setup_type
    assert result.setup.direction == expected_direction
    assert result.setup.confirmation_met is True
    assert result.confluence is not None
    assert result.confluence.proposed_direction == expected_direction
    assert result.signal_decision is not None
    assert result.signal_decision.decision in (Decision.LONG, Decision.SHORT, Decision.WAIT)
    record = build_signal_record(result)
    assert record is not None
    assert record.setup_type == expected_setup_type
    assert record.decision == result.signal_decision.decision
    return result, record


def test_pipeline_happy_path_trend_continuation():
    _assert_happy_path(trend_continuation_long_candles(), SetupType.TREND_CONTINUATION, Direction.LONG)


def test_pipeline_happy_path_pullback():
    _assert_happy_path(pullback_long_candles(), SetupType.PULLBACK, Direction.LONG)


def test_pipeline_happy_path_range_mean_reversion():
    _assert_happy_path(range_mean_reversion_long_candles(), SetupType.RANGE_MEAN_REVERSION, Direction.LONG)


def test_pipeline_happy_path_breakout_retest():
    _assert_happy_path(breakout_retest_long_candles(), SetupType.BREAKOUT_RETEST, Direction.LONG)


def test_pipeline_happy_path_reversal():
    _assert_happy_path(reversal_short_candles(), SetupType.REVERSAL, Direction.SHORT)


# ---------------------------------------------------------------------------
# 6-9. Decision states: valid LONG, valid SHORT, WAIT, NO_TRADE.
# ---------------------------------------------------------------------------

def test_decision_state_valid_long():
    candles = wide_sweep_candles(FULL_LONG_SEED)
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.signal_decision.decision == Decision.LONG
    assert result.signal_decision.direction == Direction.LONG
    assert all(result.signal_decision.gates.values())
    record = build_signal_record(result)
    assert record is not None
    assert record.decision == Decision.LONG and record.direction == Direction.LONG
    assert record.entry is not None and record.stop_loss is not None
    assert record.take_profit_1 is not None and record.risk_reward is not None
    assert record.risk_reward >= 1.2 - 1e-9  # MIN_RR_HARD_GATE, provisional (risk/risk_engine.py)


def test_decision_state_valid_short():
    candles = wide_sweep_candles(FULL_SHORT_SEED)
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.signal_decision.decision == Decision.SHORT
    assert result.signal_decision.direction == Direction.SHORT
    assert all(result.signal_decision.gates.values())
    record = build_signal_record(result)
    assert record is not None
    assert record.decision == Decision.SHORT and record.direction == Direction.SHORT
    assert record.stop_loss > record.entry  # protective stop sits ABOVE entry for a SHORT
    assert record.take_profit_1 < record.entry


def test_decision_state_wait_unconfirmed_setup_still_produces_a_record():
    # PULLBACK's own fixture above is confirmed; a shorter version of the
    # same ramp (no pullback/turn tail at all) leaves it merely FORMING --
    # prerequisites met, confirmation not -- which is its own distinct WAIT
    # path (G3), still honestly recorded rather than discarded.
    random.seed(7)
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] + 0.5 + random.uniform(-0.15, 0.15))
    for _ in range(2):
        closes.append(closes[-1] - 1.5)  # only 2 down candles, no recovery tail -- stoch not yet turning
    n = len(closes)
    candles = []
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i))
        o = closes[i - 1] if i > 0 else c
        candles.append(CandleData(open_time=t, open=o, high=max(o, c) + 0.05, low=min(o, c) - 0.05,
                                   close=c, volume=100.0, is_closed=True))
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.setup is not None
    assert result.setup.prerequisites_met is True
    assert result.setup.confirmation_met is False
    assert result.signal_decision.decision == Decision.WAIT
    assert result.signal_decision.gates["G3_SETUP_CONFIRMED"] is False
    record = build_signal_record(result)
    assert record is not None  # confluence IS still computable for a forming (unconfirmed) candidate
    assert record.decision == Decision.WAIT
    assert record.direction is None


def test_decision_state_no_trade_no_setup_detected():
    candles = flat_candles()
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.setup is None
    assert result.confluence is None and result.entry_plan is None and result.risk_plan is None
    assert result.signal_decision.decision == Decision.NO_TRADE
    assert result.signal_decision.direction is None
    assert build_signal_record(result) is None  # honestly nothing to embed -- never fabricated


# ---------------------------------------------------------------------------
# Quality Gate authority: the final decision is always exactly what
# quality_gate.evaluate() returned -- nothing upstream or downstream can
# change it. Swept across many seeds (not just the two pinned happy-path
# ones) so this is checked against a genuinely varied sample, the same
# discipline test_phase5_pipeline.py's own sweep test already uses.
# ---------------------------------------------------------------------------

def test_quality_gate_is_the_sole_authority_for_decision():
    decisions_seen = set()
    for seed in range(80):
        candles = wide_sweep_candles(seed)
        md = build_md(candles)
        result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
        decisions_seen.add(result.signal_decision.decision)

        # Independently re-run the Gate on the exact same stage outputs --
        # if assembly (or anything else) had silently overridden the
        # decision, this would catch it (recomputing evaluate() from the
        # SAME inputs must reproduce the SAME decision).
        ctx = GateContext(
            data_quality=result.data_quality, as_of=result.market_data.as_of, timeframe=result.market_data.timeframe,
            setup=result.setup, confluence=result.confluence, entry_plan=result.entry_plan,
            risk_plan=result.risk_plan, evaluated_at=result.evaluated_at, current_price=result.current_price,
        )
        recomputed = evaluate(ctx)
        assert recomputed.decision == result.signal_decision.decision
        assert recomputed.direction == result.signal_decision.direction
        assert recomputed.gates == result.signal_decision.gates

        record = build_signal_record(result)
        if record is not None:
            assert record.decision == result.signal_decision.decision
            assert record.direction == result.signal_decision.direction

    assert len(decisions_seen) > 1  # sweep must genuinely exercise more than one outcome


def test_downstream_assembly_cannot_invent_a_direction_for_wait_or_no_trade():
    for seed in range(60):
        candles = wide_sweep_candles(seed)
        md = build_md(candles)
        result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
        record = build_signal_record(result)
        if result.signal_decision.decision in (Decision.WAIT, Decision.NO_TRADE):
            assert result.signal_decision.direction is None
            if record is not None:
                assert record.direction is None
        else:
            assert result.signal_decision.direction == result.setup.direction
            if record is not None:
                assert record.direction == result.setup.direction


# ---------------------------------------------------------------------------
# Direction symmetry.
# ---------------------------------------------------------------------------

def test_direction_symmetry_trend_continuation_long_vs_short():
    long_result, long_record = _assert_happy_path(
        trend_continuation_long_candles(), SetupType.TREND_CONTINUATION, Direction.LONG,
    )
    short_result, short_record = _assert_happy_path(
        trend_continuation_short_candles(), SetupType.TREND_CONTINUATION, Direction.SHORT,
    )
    # Same decision-relevant structure on both sides -- same gates
    # evaluated, same shape of outcome, only direction differs.
    assert set(long_result.signal_decision.gates.keys()) == set(short_result.signal_decision.gates.keys())
    assert long_record.market_regime == long_result.regime.regime
    assert short_record.market_regime == short_result.regime.regime
    if long_record.stop_loss is not None and long_record.entry is not None:
        assert long_record.stop_loss < long_record.entry  # LONG: protective stop below entry
    if short_record.stop_loss is not None and short_record.entry is not None:
        assert short_record.stop_loss > short_record.entry  # SHORT: protective stop above entry


def test_direction_symmetry_range_mean_reversion_long_vs_short():
    long_result, long_record = _assert_happy_path(
        range_mean_reversion_long_candles(), SetupType.RANGE_MEAN_REVERSION, Direction.LONG,
    )
    short_result, short_record = _assert_happy_path(
        range_mean_reversion_short_candles(), SetupType.RANGE_MEAN_REVERSION, Direction.SHORT,
    )
    assert long_result.regime.regime == short_result.regime.regime == RegimeType.RANGE


# ---------------------------------------------------------------------------
# Data quality.
# ---------------------------------------------------------------------------

def test_data_quality_unavailable_never_reaches_feature_computation():
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles, overall=DataQualityState.UNAVAILABLE, reasons=["price unavailable"]),
                           evaluated_at=NOW)
    assert result.feature_set is None
    assert result.regime is None and result.setup is None
    assert result.signal_decision.decision == Decision.NO_TRADE
    assert result.signal_decision.gates == {"G1_DATA_VALID": False}
    assert build_signal_record(result) is None


def test_data_quality_insufficient_history_short_circuits_before_setup_detection():
    # 30 closed candles: enough to be "usable" but short of the 50 EMA50
    # needs -- classify_regime honestly returns UNKNOWN rather than
    # guessing (regime/engine.py's own documented priority order).
    candles = realistic_candles(30, drift=0.001, noise=0.01, seed=3)
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles, overall=DataQualityState.DEGRADED, reasons=["thin history"]),
                           evaluated_at=NOW)
    assert result.feature_set is not None  # a FeatureSet IS computable (some indicators just come back None)
    assert result.regime is not None and result.regime.regime == RegimeType.UNKNOWN
    assert result.setup is None
    assert result.signal_decision.decision == Decision.NO_TRADE
    assert any("insufficient" in r.lower() or "ema50" in r.lower() for r in result.stage_reasons)
    assert build_signal_record(result) is None


def test_data_quality_degraded_reasons_surface_as_warnings_when_a_record_is_built():
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    dq = valid_dq(candles, overall=DataQualityState.DEGRADED, reasons=["one source excluded"])
    result = run_pipeline(md, dq, evaluated_at=NOW)
    assert result.setup is not None  # this fixture still produces a confirmed setup even under DEGRADED
    record = build_signal_record(result)
    assert record is not None
    assert "one source excluded" in record.warnings


def test_data_quality_stale_market_data_is_distinguished_from_fresh():
    # Same snapshot, evaluated at two different times: freshness (G10 /
    # entry staleness) must differ even though nothing about the market
    # data itself changed -- "fresh" vs "stale" is a property of WHEN
    # you ask, not just what was fetched (Section 6).
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    dq = valid_dq(candles)
    fresh = run_pipeline(md, dq, evaluated_at=NOW)
    stale = run_pipeline(md, dq, evaluated_at=NOW + timedelta(hours=5))
    assert fresh.entry_plan is not None and fresh.entry_plan.is_stale is False
    assert stale.entry_plan is not None and stale.entry_plan.is_stale is True
    assert stale.signal_decision.decision == Decision.WAIT
    assert stale.signal_decision.gates["G7_ENTRY_VALID"] is False
    assert "entry stale" in stale.signal_decision.reasons


# ---------------------------------------------------------------------------
# Risk: invalid R:R and invalid target/SL.
#
# Neither condition occurred naturally across a 500-seed sweep of varied
# realistic data while building this phase (risk/targets.py's provisional
# TP1=1.5xATR against risk/risk_engine.py's provisional 1.2 minimum leaves
# real headroom under ordinary structure). Rather than hand-engineer
# contrived candle data purely to force it (which risks testing an
# artifact of the fixture instead of the integration), these two tests
# verify the WIRING directly: a RiskPlan carrying meets_min_rr=False /
# target_clearance_ok=False -- built the same way tests/unit/test_risk.py
# itself already constructs edge-case RiskPlans -- correctly drives
# quality_gate.evaluate() to NO_TRADE via G8 when combined with an
# otherwise-real setup/confluence/entry_plan (from an already-verified
# fixture above). This is exactly the integration Phase 6 owns; the
# arithmetic that decides WHETHER a given price structure yields a bad
# R:R is Phase 5's own, already covered by its own approved test suite.
# ---------------------------------------------------------------------------

def test_risk_invalid_rr_reaches_no_trade_through_the_gate():
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.entry_plan is not None and result.risk_plan is not None  # baseline: this fixture normally has valid risk

    bad_risk_plan = RiskPlan(
        stop_loss=result.risk_plan.stop_loss, take_profit_1=result.risk_plan.take_profit_1,
        take_profit_2=result.risk_plan.take_profit_2,
        risk_reward_1=0.4, risk_reward_2=result.risk_plan.risk_reward_2,  # below MIN_RR_HARD_GATE (1.2)
        meets_min_rr=False, target_clearance_ok=True,
        position_size=None, max_safe_leverage=result.risk_plan.max_safe_leverage, warnings=[],
    )
    ctx = GateContext(
        data_quality=result.data_quality, as_of=result.market_data.as_of, timeframe=result.market_data.timeframe,
        setup=result.setup, confluence=result.confluence, entry_plan=result.entry_plan, risk_plan=bad_risk_plan,
        evaluated_at=NOW, current_price=result.current_price,
    )
    decision = evaluate(ctx)
    assert decision.decision == Decision.NO_TRADE
    assert decision.gates["G8_RISK_VALID"] is False


def test_risk_invalid_target_clearance_reaches_no_trade_through_the_gate():
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert result.entry_plan is not None and result.risk_plan is not None

    bad_risk_plan = RiskPlan(
        stop_loss=result.risk_plan.stop_loss, take_profit_1=result.risk_plan.take_profit_1,
        take_profit_2=result.risk_plan.take_profit_2,
        risk_reward_1=result.risk_plan.risk_reward_1, risk_reward_2=result.risk_plan.risk_reward_2,
        meets_min_rr=True, target_clearance_ok=False,  # opposing structure blocks the target
        position_size=None, max_safe_leverage=result.risk_plan.max_safe_leverage,
        warnings=["TP1 blocked by opposing structure"],
    )
    ctx = GateContext(
        data_quality=result.data_quality, as_of=result.market_data.as_of, timeframe=result.market_data.timeframe,
        setup=result.setup, confluence=result.confluence, entry_plan=result.entry_plan, risk_plan=bad_risk_plan,
        evaluated_at=NOW, current_price=result.current_price,
    )
    decision = evaluate(ctx)
    assert decision.decision == Decision.NO_TRADE
    assert decision.gates["G8_RISK_VALID"] is False


# ---------------------------------------------------------------------------
# Entry: stale entry and excessive chase distance.
# ---------------------------------------------------------------------------

def test_entry_stale_reaches_wait_not_no_trade():
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW + timedelta(hours=5))
    assert result.entry_plan.is_stale is True
    assert result.signal_decision.decision == Decision.WAIT  # entry issues are WAIT-class, not NO_TRADE
    assert result.signal_decision.gates["G7_ENTRY_VALID"] is False


def test_entry_excessive_chase_distance_reaches_wait():
    candles = trend_continuation_long_candles()
    md = build_md(candles)
    baseline = run_pipeline(md, valid_dq(candles), evaluated_at=NOW)
    assert baseline.entry_plan is not None
    far_price = baseline.entry_plan.confirmation_price * 5.0  # nowhere near the entry zone
    result = run_pipeline(md, valid_dq(candles), evaluated_at=NOW, current_price=far_price)
    assert result.signal_decision.decision == Decision.WAIT
    assert result.signal_decision.gates["G7_ENTRY_VALID"] is False
    assert "chase distance exceeded" in result.signal_decision.reasons


# ---------------------------------------------------------------------------
# No-lookahead: an end-to-end proof (not just unit-level) that appending or
# mutating an unclosed/forming candle never changes anything the pipeline
# already decided from closed history alone.
# ---------------------------------------------------------------------------

def test_pipeline_no_lookahead_wild_forming_candle():
    candles = trend_continuation_long_candles()
    md_a = build_md(candles)
    baseline = run_pipeline(md_a, valid_dq(candles), evaluated_at=NOW, current_price=candles[-1].close)
    assert baseline.setup is not None, "fixture must produce a real setup, or this test is vacuous"

    wild_forming = CandleData(
        open_time=NOW, open=candles[-1].close, high=candles[-1].close * 200,
        low=candles[-1].close * 0.01, close=candles[-1].close * 150, volume=999999.0, is_closed=False,
    )
    candles_with_forming = candles + [wild_forming]
    md_b = build_md(candles_with_forming)
    # current_price is pinned to the SAME value in both calls: G7's chase-
    # distance check legitimately depends on the live ticker price, which
    # is a genuinely separate, real-time concern from whether ANALYSIS
    # read ahead into unclosed candle data -- letting it float here (e.g.
    # via this fixture's own live_price=candles[-1].close convenience,
    # which would pick up the wild forming candle's price) would test a
    # different thing than no-lookahead and produce a false failure.
    with_forming = run_pipeline(md_b, valid_dq(candles_with_forming), evaluated_at=NOW,
                                 current_price=candles[-1].close)

    assert baseline.feature_set == with_forming.feature_set
    assert baseline.regime == with_forming.regime
    assert baseline.setup == with_forming.setup
    assert baseline.confluence == with_forming.confluence
    assert baseline.entry_plan == with_forming.entry_plan
    assert baseline.risk_plan == with_forming.risk_plan
    assert baseline.signal_decision.decision == with_forming.signal_decision.decision
    assert baseline.signal_decision.direction == with_forming.signal_decision.direction
    assert baseline.signal_decision.gates == with_forming.signal_decision.gates

    record_a = build_signal_record(baseline)
    record_b = build_signal_record(with_forming)
    assert (record_a is None) == (record_b is None)
    if record_a is not None:
        assert record_a.id == record_b.id
        assert record_a.setup_score == record_b.setup_score
        assert record_a.entry == record_b.entry
        assert record_a.stop_loss == record_b.stop_loss


def test_pipeline_no_lookahead_across_all_five_family_fixtures():
    # Same proof as above, generalized across every happy-path fixture --
    # no single family's detector is accidentally reading ahead.
    fixtures = [
        trend_continuation_long_candles(), pullback_long_candles(), range_mean_reversion_long_candles(),
        breakout_retest_long_candles(), reversal_short_candles(),
    ]
    for candles in fixtures:
        md_a = build_md(candles)
        baseline = run_pipeline(md_a, valid_dq(candles), evaluated_at=NOW, current_price=candles[-1].close)
        wild_forming = CandleData(
            open_time=NOW, open=candles[-1].close, high=candles[-1].close * 50,
            low=max(candles[-1].close * 0.02, 0.01), close=candles[-1].close * 30, volume=888888.0, is_closed=False,
        )
        candles_with_forming = candles + [wild_forming]
        md_b = build_md(candles_with_forming)
        with_forming = run_pipeline(md_b, valid_dq(candles_with_forming), evaluated_at=NOW,
                                     current_price=candles[-1].close)
        assert baseline.setup == with_forming.setup
        assert baseline.entry_plan == with_forming.entry_plan
        assert baseline.risk_plan == with_forming.risk_plan
        assert baseline.signal_decision.decision == with_forming.signal_decision.decision
        assert baseline.signal_decision.gates == with_forming.signal_decision.gates


# ---------------------------------------------------------------------------
# Determinism: the same input snapshot produces equivalent output, except
# for explicitly time-dependent metadata (there is none left un-pinned
# here, since evaluated_at is always passed explicitly in these tests --
# so output should be EXACTLY equal, not just "equivalent").
# ---------------------------------------------------------------------------

def test_determinism_same_snapshot_same_output():
    for candles in (trend_continuation_long_candles(), pullback_long_candles(), reversal_short_candles()):
        md = build_md(candles)
        dq = valid_dq(candles)
        result_a = run_pipeline(md, dq, evaluated_at=NOW)
        result_b = run_pipeline(md, dq, evaluated_at=NOW)
        assert result_a.feature_set == result_b.feature_set
        assert result_a.regime == result_b.regime
        assert result_a.setup == result_b.setup
        assert result_a.confluence == result_b.confluence
        assert result_a.entry_plan == result_b.entry_plan
        assert result_a.risk_plan == result_b.risk_plan
        assert result_a.signal_decision == result_b.signal_decision

        record_a = build_signal_record(result_a)
        record_b = build_signal_record(result_b)
        assert (record_a is None) == (record_b is None)
        if record_a is not None:
            assert record_a == record_b  # full equality, including id -- see signal_assembly/builder.py


# ---------------------------------------------------------------------------
# scan_symbol: the same guarantees, through the actual public, I/O-bearing
# entry point (a fake MarketDataSource in place of a real network call --
# the same dependency-injection seam analyze_market's own tests already
# use).
# ---------------------------------------------------------------------------

class _FakeSource:
    """A minimal, real implementation of data.MarketDataSource (not a
    mock/patch -- this codebase never mocks its own interfaces, and
    analyze_market's own tests already use exactly this dependency-
    injection seam with a hand-written fake). One instance serves one
    market, matching BitgetMarketDataSource's own construction contract."""

    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, symbol, timeframe, limit, as_of=None):
        return NormalizationResult(candles=list(self._candles[-limit:]), issues=[])

    def get_ticker_price(self, symbol):
        if not self._candles:
            return None
        return TickerPrice(price=self._candles[-1].close, source="fake_ticker", fetched_at=NOW,
                            quality=DataQualityState.VALID)


def test_scan_symbol_end_to_end_matches_run_pipeline_directly():
    candles = trend_continuation_long_candles()
    source = _FakeSource(candles)
    opp = scan_symbol(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, limit=len(candles),
                       as_of=NOW, evaluated_at=NOW)
    assert isinstance(opp, OpportunityResult)
    assert opp.symbol == "BTC" and opp.pair == "BTCUSDT"
    assert opp.setup is not None and opp.setup.setup_type == SetupType.TREND_CONTINUATION
    assert opp.decision == opp.signal_decision.decision
    if opp.decision in (Decision.LONG, Decision.SHORT):
        assert opp.signal_record is not None


def test_scan_symbol_no_data_from_a_source_that_returns_nothing():
    source = _FakeSource([])
    opp = scan_symbol(source, "BTC", "BTCUSDT", MarketType.SPOT, Timeframe.M1, as_of=NOW, evaluated_at=NOW)
    assert opp.decision == Decision.NO_TRADE
    assert opp.signal_record is None
    assert opp.data_quality is not None and opp.data_quality.overall == DataQualityState.UNAVAILABLE


def test_opportunity_result_rejects_long_short_without_a_signal_record():
    with pytest.raises(ValueError):
        OpportunityResult(decision=Decision.LONG, symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT,
                           timeframe=Timeframe.M1, signal_record=None)

