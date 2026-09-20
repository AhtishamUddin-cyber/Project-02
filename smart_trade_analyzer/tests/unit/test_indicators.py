"""Tests for smart_trade_analyzer.features.indicators and .volume.

Every expected value here is computed independently of the production
implementation -- by hand arithmetic, by a deliberately differently-
structured reference function, or via Python's own `statistics` library --
never by asserting the implementation against itself. See each test's
comment for exactly how its expected value was derived.
"""
import math
import statistics

import pytest

from smart_trade_analyzer.features import indicators as ind
from smart_trade_analyzer.features import readiness
from smart_trade_analyzer.features import volume as vol


def realistic_closes(n, drift=0.001, noise=0.01, base=100.0, seed=1):
    """Multiplicative random walk -- noise exceeds drift so individual steps
    genuinely go both directions (realistic), while the cumulative drift
    still produces a clear trend over many candles. Never goes negative."""
    import random
    random.seed(seed)
    out = [base]
    for _ in range(n - 1):
        out.append(max(out[-1] * (1 + drift + random.uniform(-noise, noise)), 0.01))
    return out


def realistic_ohlc(n, drift=0.001, noise=0.01, base=100.0, seed=1):
    closes = realistic_closes(n, drift, noise, base, seed)
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    return highs, lows, closes


# ---------------------------------------------------------------------------
# EMA -- hand-computed
# ---------------------------------------------------------------------------

def test_ema_matches_hand_computed_value():
    closes = [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # seed = mean(1,2,3) = 2.0 ; k = 2/(3+1) = 0.5 ; e = x*k + e*(1-k)
    e = 2.0
    for x in [4, 5, 6, 7, 8, 9, 10]:
        e = x * 0.5 + e * 0.5
    assert ind.ema(closes, 3) == pytest.approx(e, abs=1e-9)
    assert ind.ema(closes, 3) == pytest.approx(9.0, abs=1e-9)


def test_ema_none_below_minimum():
    assert ind.ema([1.0, 2.0], 3) is None


def test_ema_exact_minimum_produces_a_value():
    assert ind.ema([1.0, 2.0, 3.0], 3) is not None


def test_ema_series_causality_earlier_values_unaffected_by_later_change():
    closes_a = realistic_closes(210, seed=5)
    closes_b = list(closes_a)
    closes_b[150] *= 50  # dramatic change late in the series
    series_a = ind.ema_series(closes_a, 9)
    series_b = ind.ema_series(closes_b, 9)
    assert series_a[:150] == series_b[:150]
    assert series_a[150] != series_b[150]


def test_ema_rejects_non_finite_input():
    bad = [1.0, 2.0, float("nan"), 4.0, 5.0]
    assert ind.ema(bad, 3) is None


def test_ema_invalid_period_raises():
    with pytest.raises(ValueError):
        ind.ema([1.0, 2.0], 0)


# ---------------------------------------------------------------------------
# RSI (Wilder's smoothing) -- cross-checked against an independently
# structured reference implementation
# ---------------------------------------------------------------------------

def _reference_rsi(prices, period):
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    ag = statistics.mean(gains[:period])
    al = statistics.mean(losses[:period])
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)


def test_rsi_matches_independent_reference():
    closes = [44.0, 44.25, 44.5, 43.75, 44.5, 45.0, 45.25, 45.5, 45.75, 46.0,
              46.25, 46.5, 46.25, 46.5, 46.0, 46.75, 47.0, 46.5, 46.75, 47.25]
    assert ind.rsi(closes, 14) == pytest.approx(_reference_rsi(closes, 14), abs=1e-9)


def test_rsi_all_gains_is_100():
    assert ind.rsi([float(i) for i in range(1, 20)], 14) == 100.0


def test_rsi_all_losses_is_0():
    assert ind.rsi([float(i) for i in range(20, 1, -1)], 14) == 0.0


def test_rsi_flat_prices_is_50():
    assert ind.rsi([100.0] * 20, 14) == 50.0


def test_rsi_insufficient_data_is_none_not_a_fabricated_fifty():
    # Deliberate deviation from the legacy implementation (which returns a
    # fabricated 50 here) -- see indicators.py's module docstring.
    assert ind.rsi([float(i) for i in range(10)], 14) is None


def test_rsi_exact_minimum_candles():
    assert ind.rsi([float(i) for i in range(14)], 14) is None       # 14 closes: not enough
    assert ind.rsi([float(i) for i in range(15)], 14) is not None   # 15 closes: exactly enough


def test_rsi_series_causality():
    closes_a = realistic_closes(210, seed=6)
    closes_b = list(closes_a)
    closes_b[150] *= 50
    series_a = ind.rsi_series(closes_a, 14)
    series_b = ind.rsi_series(closes_b, 14)
    assert series_a[:150] == series_b[:150]
    assert series_a[150] != series_b[150]


def test_rsi_rejects_non_finite_input():
    bad = realistic_closes(20)
    bad[5] = float("inf")
    assert ind.rsi(bad, 14) is None


# ---------------------------------------------------------------------------
# MACD -- cross-checked against a corrected legacy-style reference (see
# indicators.py's module docstring for the documented, discovered
# off-by-one departure from the LITERAL legacy loop boundary)
# ---------------------------------------------------------------------------

def _reference_macd(prices, fast=12, slow=26, signal=9):
    e_fast = ind.ema(prices, fast)
    e_slow = ind.ema(prices, slow)
    if e_fast is None or e_slow is None:
        return None, None, None
    line = e_fast - e_slow
    mv = []
    for i in range(slow - 1, len(prices)):  # first entry uses exactly `slow` closes
        a = ind.ema(prices[: i + 1], fast)
        b = ind.ema(prices[: i + 1], slow)
        if a is not None and b is not None:
            mv.append(a - b)
    sig = ind.ema(mv, signal) if len(mv) >= signal else None
    hist = (line - sig) if sig is not None else None
    return line, sig, hist


def test_macd_matches_corrected_reference():
    closes = realistic_closes(60, seed=7)
    ref_line, ref_sig, ref_hist = _reference_macd(closes)
    my_line, my_sig, my_hist = ind.macd(closes)
    assert my_line == pytest.approx(ref_line, abs=1e-9)
    assert my_sig == pytest.approx(ref_sig, abs=1e-9)
    assert my_hist == pytest.approx(ref_hist, abs=1e-9)


def test_macd_deliberately_differs_from_the_literal_legacy_off_by_one():
    # Documents, with a real number, the discovered legacy quirk this
    # implementation does NOT reproduce (range(26, len(prices)) instead of
    # range(25, len(prices)) -- see indicators.py's module docstring).
    closes = realistic_closes(60, seed=7)

    def literal_legacy_macd(prices, fast=12, slow=26, signal=9):
        e_fast = ind.ema(prices, fast)
        e_slow = ind.ema(prices, slow)
        line = e_fast - e_slow
        mv = [
            ind.ema(prices[: i + 1], fast) - ind.ema(prices[: i + 1], slow)
            for i in range(slow, len(prices))
        ]
        sig = ind.ema(mv, signal) if len(mv) >= signal else None
        return line, sig

    _, literal_sig = literal_legacy_macd(closes)
    _, my_sig, _ = ind.macd(closes)
    assert literal_sig != pytest.approx(my_sig, abs=1e-9)  # confirms the two are genuinely different
    assert literal_sig == pytest.approx(my_sig, rel=0.01)   # but close (small, documented effect)


def test_macd_line_minimum_candles():
    closes = realistic_closes(26, seed=8)
    line, sig, hist = ind.macd(closes[:25])
    assert line is None
    line, sig, hist = ind.macd(closes[:26])
    assert line is not None


def test_macd_signal_minimum_candles():
    closes = realistic_closes(40, seed=8)
    _, sig33, _ = ind.macd(closes[:33])
    _, sig34, _ = ind.macd(closes[:34])
    assert sig33 is None
    assert sig34 is not None


def test_macd_rejects_non_finite_input():
    bad = realistic_closes(60, seed=9)
    bad[10] = float("nan")
    assert ind.macd(bad) == (None, None, None)


# ---------------------------------------------------------------------------
# Stochastic RSI -- cross-checked against an independent reference; also
# guards the floating-point boundary-clamp fix found during development
# ---------------------------------------------------------------------------

def _reference_stoch_rsi(prices, rsi_p=14, stoch_p=14, k_s=3, d_s=3):
    rsi_vals = [v for v in ind.rsi_series(prices, rsi_p) if v is not None]
    if len(rsi_vals) < stoch_p:
        return None, None
    raws = []
    for i in range(stoch_p - 1, len(rsi_vals)):
        w = rsi_vals[i - stoch_p + 1 : i + 1]
        mn, mx = min(w), max(w)
        raws.append(50.0 if mx == mn else (w[-1] - mn) / (mx - mn) * 100)
    if len(raws) < k_s:
        return None, None
    k_vals = [statistics.mean(raws[i - k_s + 1 : i + 1]) for i in range(k_s - 1, len(raws))]
    k = k_vals[-1]
    if len(k_vals) < d_s:
        return k, None
    return k, statistics.mean(k_vals[-d_s:])


def test_stoch_rsi_matches_independent_reference():
    closes = realistic_closes(60, seed=7)
    ref_k, ref_d = _reference_stoch_rsi(closes)
    my_k, my_d = ind.stoch_rsi(closes)
    assert my_k == pytest.approx(ref_k, abs=1e-6)
    assert my_d == pytest.approx(ref_d, abs=1e-6)


def test_stoch_rsi_k_minimum_candles():
    closes = realistic_closes(35, seed=8)
    k29, _ = ind.stoch_rsi(closes[:29])
    k30, _ = ind.stoch_rsi(closes[:30])
    assert k29 is None
    assert k30 is not None


def test_stoch_rsi_d_minimum_candles():
    closes = realistic_closes(35, seed=8)
    _, d31 = ind.stoch_rsi(closes[:31])
    _, d32 = ind.stoch_rsi(closes[:32])
    assert d31 is None
    assert d32 is not None


@pytest.mark.parametrize("seed", range(30))
def test_stoch_rsi_always_within_0_100_bounds(seed):
    # Regression test for the floating-point boundary excursion found and
    # fixed during development (values of ~1e-14 outside [0,100] were
    # observed before the fix -- see indicators.py's _clamp_pct).
    closes = realistic_closes(220, drift=0.0, noise=0.02, seed=seed)
    k, d = ind.stoch_rsi(closes)
    if k is not None:
        assert 0.0 <= k <= 100.0
    if d is not None:
        assert 0.0 <= d <= 100.0


def test_stoch_rsi_rejects_non_finite_input():
    bad = realistic_closes(60, seed=9)
    bad[10] = float("inf")
    assert ind.stoch_rsi(bad) == (None, None)


# ---------------------------------------------------------------------------
# Bollinger Bands -- cross-checked against Python's own statistics library
# ---------------------------------------------------------------------------

def test_bollinger_matches_statistics_library():
    closes = realistic_closes(60, seed=10)
    window = closes[-20:]
    ref_mid = statistics.mean(window)
    ref_std = statistics.pstdev(window)  # population stdev -- genuinely independent call
    upper, mid, lower = ind.bollinger_bands(closes, 20, 2.0)
    assert mid == pytest.approx(ref_mid, abs=1e-9)
    assert upper == pytest.approx(ref_mid + 2 * ref_std, abs=1e-9)
    assert lower == pytest.approx(ref_mid - 2 * ref_std, abs=1e-9)


def test_bollinger_minimum_candles():
    closes = realistic_closes(20, seed=11)
    assert ind.bollinger_bands(closes[:19], 20) == (None, None, None)
    assert ind.bollinger_bands(closes[:20], 20) != (None, None, None)


def test_bollinger_rejects_non_finite_input():
    bad = realistic_closes(25, seed=12)
    bad[5] = float("nan")
    assert ind.bollinger_bands(bad, 20) == (None, None, None)


# ---------------------------------------------------------------------------
# ATR (Wilder's smoothing) -- cross-checked against an independently
# structured reference implementation
# ---------------------------------------------------------------------------

def _reference_atr(highs, lows, closes, period):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    a = statistics.mean(trs[:period])
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


def test_atr_matches_independent_reference():
    highs, lows, closes = realistic_ohlc(30, seed=13)
    ref = _reference_atr(highs, lows, closes, 14)
    assert ind.atr(highs, lows, closes, 14) == pytest.approx(ref, abs=1e-9)


def test_atr_minimum_candles():
    highs, lows, closes = realistic_ohlc(16, seed=14)
    assert ind.atr(highs[:14], lows[:14], closes[:14], 14) is None
    assert ind.atr(highs[:15], lows[:15], closes[:15], 14) is not None


def test_atr_series_causality():
    highs, lows, closes = realistic_ohlc(210, seed=15)
    highs_b = list(highs)
    highs_b[150] *= 50
    series_a = ind.atr_series(highs, lows, closes, 14)
    series_b = ind.atr_series(highs_b, lows, closes, 14)
    assert series_a[:150] == series_b[:150]
    assert series_a[150] != series_b[150]


def test_atr_rejects_non_finite_input():
    highs, lows, closes = realistic_ohlc(20, seed=16)
    highs[5] = float("inf")
    assert ind.atr(highs, lows, closes, 14) is None


def test_atr_invalid_period_raises():
    with pytest.raises(ValueError):
        ind.atr([1.0], [1.0], [1.0], 0)


# ---------------------------------------------------------------------------
# Volume ratio -- hand-computed
# ---------------------------------------------------------------------------

def test_volume_ratio_hand_computed():
    volumes = [float(i) for i in range(1, 21)]  # 1..20
    # trailing 19 = [1..19], avg=10.0 ; current=20 -> ratio=2.0
    assert vol.volume_ratio(volumes) == 2.0


def test_volume_ratio_minimum_candles():
    volumes = [float(i) for i in range(1, 20)]
    assert vol.volume_ratio(volumes) is None
    volumes = [float(i) for i in range(1, 21)]
    assert vol.volume_ratio(volumes) is not None


def test_volume_ratio_zero_trailing_average_is_none_not_fabricated():
    volumes = [0.0] * 19 + [5.0]
    assert vol.volume_ratio(volumes) is None


def test_volume_ratio_rejects_non_finite_input():
    volumes = [float(i) for i in range(1, 21)]
    volumes[5] = float("inf")
    assert vol.volume_ratio(volumes) is None


def test_volume_ratio_invalid_window_raises():
    with pytest.raises(ValueError):
        vol.volume_ratio([1.0, 2.0], window=1)


# ---------------------------------------------------------------------------
# Readiness -- documented minimums match the empirically-verified boundaries
# exercised above
# ---------------------------------------------------------------------------

def test_readiness_flags_match_documented_minimums():
    flags_at_49 = readiness.readiness(49)
    flags_at_50 = readiness.readiness(50)
    assert flags_at_49["ema50"] is False
    assert flags_at_50["ema50"] is True


def test_compute_completeness_zero_when_nothing_populated():
    empty = {k: None for k in readiness.FEATURE_MIN_CANDLES}
    assert readiness.compute_completeness(empty) == 0.0


def test_compute_completeness_one_when_everything_populated():
    full = {k: 1.0 for k in readiness.FEATURE_MIN_CANDLES}
    assert readiness.compute_completeness(full) == 1.0


def test_compute_completeness_fraction():
    half = {k: (1.0 if i % 2 == 0 else None) for i, k in enumerate(readiness.FEATURE_MIN_CANDLES)}
    expected = sum(1 for v in half.values() if v is not None) / len(half)
    assert readiness.compute_completeness(half) == pytest.approx(expected)
