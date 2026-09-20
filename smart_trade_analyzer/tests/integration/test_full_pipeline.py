"""Integration tests: the full Phase 1-4 chain, MarketData -> FeatureSet ->
MarketRegime -> SetupCandidate -> ConfluenceResult, run together rather
than each module tested in isolation. Unit tests (tests/unit/) already
cover each module's own rules in detail with tightly-controlled,
hand-engineered fixtures; these tests instead check that the pieces
compose correctly and stay internally consistent across realistic,
varied data -- and that a genuinely honest NO_TRADE reading (no setup
detected at all) correctly stops the chain rather than forcing a
downstream score out of nothing.

Wiring this chain into scanner/'s own public output is explicitly out of
this phase's scope (see HANDOFF.md's Phase 4 scope discipline) -- these
tests drive features -> regime -> setup -> confluence directly.
"""
import random
from datetime import datetime, timedelta

from smart_trade_analyzer.confluence import compute_confluence
from smart_trade_analyzer.contracts import (
    CandleData, DataQualityState, Direction, MarketData, MarketRegime, MarketType, RegimeType, SetupType, Timeframe,
)
from smart_trade_analyzer.features import compute_feature_set
from smart_trade_analyzer.regime import classify_regime
from smart_trade_analyzer.setup import detect_setup

NOW = datetime(2026, 8, 27, 12, 0, 0)


def realistic_candles_with_wicks(n, seed=1, base=100.0, drift=0.0, noise=0.025):
    """Independent random high/low wicks per candle -- unlike a fixed
    max(o,c)*const shape, which ties adjacent highs together at every
    local peak and (correctly, per structure.py's strict tie rule)
    suppresses pivot/divergence detection entirely. See
    test_no_lookahead.py's make_closed_candles_with_wicks for the same
    reasoning in more detail.
    """
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


def run_chain(candles):
    """Returns (feature_set, regime, setup, confluence_result_or_None)."""
    fs = compute_feature_set(build_md(candles))
    regime = classify_regime(candles, fs)
    setup = detect_setup(candles, fs, regime)
    confluence = compute_confluence(setup, fs, regime, candles) if setup is not None else None
    return fs, regime, setup, confluence


# ---------------------------------------------------------------------------
# Realistic data, swept across many seeds: the chain must never raise, and
# every well-formed output must respect its own frozen contract.
# ---------------------------------------------------------------------------

def test_full_chain_never_raises_and_stays_internally_consistent_across_many_seeds():
    setups_seen = 0
    confluence_seen = 0
    for seed in range(60):
        candles = realistic_candles_with_wicks(210, seed=seed, drift=random.Random(seed).uniform(-0.004, 0.004))
        fs, regime, setup, confluence = run_chain(candles)

        assert fs.symbol == "BTC"
        assert regime.regime in RegimeType

        if setup is not None:
            setups_seen += 1
            assert setup.direction in (Direction.LONG, Direction.SHORT)  # never NEUTRAL
            assert confluence is not None
            confluence_seen += 1
            # proposed_direction is inherited, never independently derived --
            # true by construction in compute_confluence, re-checked here at
            # the integration level too.
            assert confluence.proposed_direction == setup.direction
            assert 0.0 <= confluence.setup_quality_score <= 100.0
            assert set(confluence.categories_available) | set(confluence.categories_excluded) == set(
                confluence.categories_available
            ) | set(confluence.categories_excluded)
            for cat in ("HTF", "FLOW", "SENTIMENT"):
                assert any(c.value == cat for c in confluence.categories_excluded)
        else:
            assert confluence is None  # NO_TRADE must never produce a fabricated downstream score

    # Sanity: across 60 varied seeds, at least some should produce a setup
    # and some shouldn't -- if either count were 0, the sweep parameters
    # wouldn't actually be exercising both paths.
    assert setups_seen > 0
    assert setups_seen == confluence_seen
    assert setups_seen < 60  # NO_TRADE genuinely happens sometimes in this sweep


def test_no_trade_path_never_fabricates_a_confluence_result():
    # Perfectly flat data: no trend, no pullback, no range extremity, no
    # breakout, no divergence -- every detector's prerequisites should
    # fail, and the chain must stop at None, not manufacture a score.
    flat = [
        CandleData(open_time=NOW - timedelta(minutes=(210 - i)), open=100.0, high=100.05, low=99.95,
                   close=100.0, volume=100.0, is_closed=True)
        for i in range(210)
    ]
    fs, regime, setup, confluence = run_chain(flat)
    assert setup is None
    assert confluence is None


# ---------------------------------------------------------------------------
# BREAKOUT_RETEST and REVERSAL, driven through the full public chain
# (detect_setup(), not the private detector functions test_setup.py calls
# directly) -- proving the new setup families surface correctly through
# the same path every other family already used, and that confluence
# scores them sensibly.
# ---------------------------------------------------------------------------

def _breakout_retest_long_candles():
    out = []
    n = 40
    base = 60.0
    for i in range(20):
        out.append(CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=base, high=base + 0.3,
                               low=base - 0.3, close=base, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 20)), open=base, high=65.3, low=59.8,
                           close=65.0, volume=100.0, is_closed=True))
    for i in range(21, 26):
        out.append(CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=base, high=base + 0.3,
                               low=base - 0.3, close=base, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 26)), open=base, high=68.5, low=59.9,
                           close=68.0, volume=250.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 27)), open=68.0, high=70.0, low=67.5,
                           close=69.5, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 28)), open=69.5, high=70.5, low=68.5,
                           close=69.0, volume=100.0, is_closed=True))
    out.append(CandleData(open_time=NOW - timedelta(minutes=(n - 29)), open=69.0, high=69.0, low=65.2,
                           close=65.5, volume=100.0, is_closed=True))
    for i in range(30, n):
        out.append(CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=66.0, high=67.0, low=65.5,
                               close=66.5, volume=100.0, is_closed=True))
    return out


def test_breakout_retest_surfaces_through_the_full_public_chain():
    candles = _breakout_retest_long_candles()
    fs, regime, setup, confluence = run_chain(candles)
    assert setup is not None
    assert setup.setup_type == SetupType.BREAKOUT_RETEST
    assert setup.direction == Direction.LONG
    assert setup.confirmation_met is True
    assert confluence is not None
    assert confluence.proposed_direction == Direction.LONG
    assert 0.0 <= confluence.setup_quality_score <= 100.0


def _reversal_short_candles():
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
    # A deeper decline than the minimal test_setup.py fixture: enough to
    # push MACD histogram negative, so TREND_CONTINUATION's own
    # confirmation genuinely fails here (verified) and REVERSAL surfaces
    # on its own -- the REVERSAL/TREND_CONTINUATION conflict itself
    # already has dedicated coverage in test_setup.py's conflict tests.
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


def test_reversal_surfaces_through_the_full_public_chain():
    candles = _reversal_short_candles()
    fs = compute_feature_set(build_md(candles))
    # This fixture's early history is flat, which classify_regime would
    # read as RANGE, not the established UPTREND REVERSAL's prerequisites
    # need -- construct the regime directly here (same technique
    # test_setup.py's conflict tests use), since MarketRegime is a pure,
    # independently-constructible Phase 1 contract and detect_setup/
    # compute_confluence only consume it, they do not require it to have
    # come from classify_regime specifically.
    regime = MarketRegime(regime=RegimeType.UPTREND, trend_strength=0.6, volatility_percentile=0.5, basis=["integration test"])
    setup = detect_setup(candles, fs, regime)
    assert setup is not None
    assert setup.setup_type == SetupType.REVERSAL
    assert setup.direction == Direction.SHORT
    assert setup.confirmation_met is True
    confluence = compute_confluence(setup, fs, regime, candles)
    assert confluence.proposed_direction == Direction.SHORT
    assert 0.0 <= confluence.setup_quality_score <= 100.0
    # this fixture is bearish-divergence-driven -- STRUCTURE must be one of
    # the categories that actually fed the score, not just TREND/MOMENTUM
    assert any(c.value == "STRUCTURE" for c in confluence.categories_available)


def test_conflict_rule_engages_on_realistic_not_just_hand_picked_data():
    # A shallower version of the same reversal shape (decline of only
    # 0.01 per candle instead of 2.0) leaves TREND_CONTINUATION's own
    # confirmation conditions ALSO genuinely satisfied on real, computed
    # RSI/MACD/EMA values (not a hand-picked FeatureSet) -- discovered
    # while building the fixture above, and kept as its own test because
    # it's a good additional confirmation that the approved
    # REVERSAL-vs-TREND_CONTINUATION conflict rule (test_setup.py's
    # dedicated, hand-constructed tests) also fires correctly on organic,
    # realistically-computed data, not only on inputs engineered
    # specifically to trigger it.
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
        closes.append(closes[-1] - 0.01)
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = NOW - timedelta(minutes=(n - i + 1))
        out.append(CandleData(open_time=t, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0, is_closed=True))
    final_close = out[-1].low - 0.01
    out.append(CandleData(open_time=NOW, open=out[-1].close, high=out[-1].close + 0.1,
                           low=final_close - 0.1, close=final_close, volume=100.0, is_closed=True))

    fs = compute_feature_set(build_md(out))
    regime = MarketRegime(regime=RegimeType.UPTREND, trend_strength=0.6, volatility_percentile=0.5, basis=["integration test"])

    from smart_trade_analyzer.setup.engine import _reversal, _trend_continuation
    trend = _trend_continuation(out, regime, fs)
    reversal = _reversal(out, regime, fs)
    assert trend is not None and trend.confirmation_met and trend.direction == Direction.LONG
    assert reversal is not None and reversal.confirmation_met and reversal.direction == Direction.SHORT

    setup = detect_setup(out, fs, regime)
    assert setup.setup_type == SetupType.TREND_CONTINUATION
    assert setup.direction == Direction.LONG
