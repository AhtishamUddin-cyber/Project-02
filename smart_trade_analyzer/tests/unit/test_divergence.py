"""Unit tests for features/divergence.py.

The bearish/bullish price paths below were constructed iteratively and
checked directly against the real rsi_series()/find_pivot_* implementations
before being committed here -- the exact numbers are not hand-guessed to
"look about right"; they were verified to actually produce a genuine
higher-high/lower-RSI (and mirrored lower-low/higher-RSI) relationship.
"""
from smart_trade_analyzer.features.divergence import (
    MIN_CANDLES_DIVERGENCE,
    detect_divergence,
)
from smart_trade_analyzer.features.indicators import rsi_series
from smart_trade_analyzer.features.structure import MIN_CANDLES_SWING, find_pivot_highs, find_pivot_lows


def _bearish_fixture():
    """Price: higher high. RSI: lower high. -> BEARISH."""
    closes = [100.0]
    for _ in range(20):
        closes.append(closes[-1])  # flat warm-up so RSI starts neutral
    for _ in range(8):
        closes.append(closes[-1] + 2.0)  # sharp, uninterrupted rally -> pivot A
    for _ in range(5):
        closes.append(closes[-1] - 1.3)  # pullback confirms pivot A
    for _ in range(4):
        closes.append(closes[-1] - 0.2)
    pattern = [2.0, 1.8, -0.7, 2.0, -0.8, 1.9, -0.6, 2.0, -0.7, 2.1, -0.5, 2.2, -0.8, 2.3, -0.4, 2.4, -0.5, 2.2]
    for d in pattern:
        closes.append(closes[-1] + d)  # choppier rally to a HIGHER price than pivot A
    for _ in range(5):
        closes.append(closes[-1] - 1.0)  # confirms pivot B
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return highs, lows, closes


def _bullish_fixture():
    """Price: lower low. RSI: higher low. -> BULLISH. Exact mirror of the
    bearish fixture (all deltas negated)."""
    closes = [100.0]
    for _ in range(20):
        closes.append(closes[-1])
    for _ in range(8):
        closes.append(closes[-1] - 2.0)
    for _ in range(5):
        closes.append(closes[-1] + 1.3)
    for _ in range(4):
        closes.append(closes[-1] + 0.2)
    pattern = [-2.0, -1.8, 0.7, -2.0, 0.8, -1.9, 0.6, -2.0, 0.7, -2.1, 0.5, -2.2, 0.8, -2.3, 0.4, -2.4, 0.5, -2.2]
    for d in pattern:
        closes.append(closes[-1] + d)
    for _ in range(5):
        closes.append(closes[-1] + 1.0)
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return highs, lows, closes


class TestDivergenceFixturesAreGenuine:
    """Sanity-lock the fixtures themselves against the canonical RSI, so a
    future change to rsi_series() that silently breaks these assumptions
    fails loudly here rather than only inside detect_divergence()."""

    def test_bearish_fixture_has_higher_price_high_and_lower_rsi_high(self):
        highs, lows, closes = _bearish_fixture()
        rsi_vals = rsi_series(closes)
        idxs = find_pivot_highs(highs)
        assert len(idxs) >= 2
        a, b = idxs[-2], idxs[-1]
        assert highs[b] > highs[a]
        assert rsi_vals[a] is not None and rsi_vals[b] is not None
        assert rsi_vals[b] < rsi_vals[a]

    def test_bullish_fixture_has_lower_price_low_and_higher_rsi_low(self):
        highs, lows, closes = _bullish_fixture()
        rsi_vals = rsi_series(closes)
        idxs = find_pivot_lows(lows)
        assert len(idxs) >= 2
        a, b = idxs[-2], idxs[-1]
        assert lows[b] < lows[a]
        assert rsi_vals[a] is not None and rsi_vals[b] is not None
        assert rsi_vals[b] > rsi_vals[a]


class TestDetectDivergence:
    def test_bearish_divergence_detected(self):
        highs, lows, closes = _bearish_fixture()
        assert detect_divergence(highs, lows, closes) == "BEARISH"

    def test_bullish_divergence_detected(self):
        highs, lows, closes = _bullish_fixture()
        assert detect_divergence(highs, lows, closes) == "BULLISH"

    def test_monotonic_series_has_no_divergence(self):
        # legitimate "nothing is happening" case -- not a data gap
        closes = [100.0 + i for i in range(60)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        assert detect_divergence(highs, lows, closes) is None

    def test_too_few_candles_returns_none(self):
        closes = [100.0, 101.0, 102.0]
        assert detect_divergence(closes, closes, closes) is None

    def test_single_pivot_is_not_enough_for_a_comparison(self):
        # One clean spike placed AFTER RSI's warm-up (index 14) -> exactly
        # one confirmed pivot, with a defined RSI value, but nothing to
        # compare it against. Placing the spike post-warm-up isolates this
        # from the separate warm-up-skip case tested below.
        closes = [100.0] * 14 + [130.0] + [100.0] * 10
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        idxs = find_pivot_highs(highs)
        assert len(idxs) == 1
        assert rsi_series(closes)[idxs[0]] is not None  # confirms RSI IS defined here
        assert detect_divergence(highs, lows, closes) is None

    def test_pivot_before_rsi_warmup_is_skipped_not_treated_as_a_match(self):
        # Two pivots: the earlier at index 3 (RSI still None, warm-up needs
        # index >= 14), the later at index 14 (RSI defined). The earlier
        # one must not be silently paired with the later one as if it had
        # a comparable value -- filtering must leave only one USABLE pivot,
        # which is insufficient, not "compare index 3 against index 14".
        closes = [float(x) for x in [60] * 3 + [100] + [60] * 10 + [90] + [60] * 3]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        idxs = find_pivot_highs(highs)
        rsi_vals = rsi_series(closes)
        assert idxs == [3, 14]
        assert rsi_vals[3] is None and rsi_vals[14] is not None  # confirms the premise
        assert detect_divergence(highs, lows, closes) is None

    def test_mismatched_lengths_raises(self):
        import pytest

        with pytest.raises(ValueError):
            detect_divergence([1, 2, 3], [1, 2], [1, 2, 3])

    def test_min_candles_constant_is_at_least_two_confirmed_pivots_worth(self):
        assert MIN_CANDLES_DIVERGENCE >= MIN_CANDLES_SWING
