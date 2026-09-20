"""The canonical, single-source-of-truth technical indicators.

Per the approved Phase 0 specification, Section 4: "Live analysis, divergence
detection, and backtest MUST use the same implementations." This module is
that single implementation for every indicator required this phase. No
other module in this codebase may define its own RSI/EMA/MACD/StochRSI/
Bollinger/ATR/volume-ratio -- everything downstream imports from here.

Every function is pure: given the same input list, it always returns the
same output, with no network calls, no wall-clock reads, and no hidden
state. This is what makes the Feature Engine safe to use identically for
live analysis and (in a later phase) backtesting.

Conventions used, and where each one came from
------------------------------------------------
Every formula below was verified directly against the existing project's
own indicator closures inside get_realtime_indicators (analyzer.py, lines
~1909-2030) before being written here -- not invented. Three deliberate
departures from the legacy behavior are called out explicitly, each with
its own reasoning, since "do not silently choose arbitrary variants when
the approved specification already defines one" cuts both ways: where the
legacy code IS the de facto existing convention, it is followed; where it
conflicts with THIS phase's explicit numerical rules or the already-approved
Phase 0 architectural fix, the deviation is documented, not hidden.

  EMA    -- SMA-seeded, standard smoothing constant k=2/(period+1).
            Matches the legacy implementation exactly. Minimum data: `period`
            closes.

  RSI    -- Wilder's smoothing (NOT a plain moving average of gains/losses --
            the legacy code's own "BUG FIX" comment explains this distinction
            was a real, previously-shipped bug). avg_gain/avg_loss seeded by
            a simple average of the first `period` deltas, then carried
            forward by Wilder's recursion avg = (avg*(period-1)+x)/period.
            Matches the legacy implementation's math exactly.
            Minimum data: `period + 1` closes (period deltas needs period+1 prices).
            DEVIATION FROM LEGACY: the legacy function returns a fabricated
            "50" (or a whole list of 50s) when there isn't enough data,
            rather than signaling "unknown." That is precisely the
            zero-as-missing / silent-fabrication pattern this phase's
            numerical rules explicitly forbid ("do not convert missing
            values into zero... do not pretend it is valid evidence").
            This implementation returns None instead. The genuine
            avg_loss==0 edge case (a real, fully-computed value -- prices
            only went up, or were perfectly flat) is NOT a missing-data
            case and is preserved: RSI=100 if there were gains, 50 if
            completely flat (matches the legacy convention for that
            specific, data-complete scenario).

  MACD   -- EMA(fast=12) - EMA(slow=26) for the line; signal = EMA(macd
            line series, 9); histogram = line - signal. Fast/slow/signal
            periods (12/26/9) match the legacy implementation.
            DEVIATION FROM LEGACY (efficiency): the legacy signal-line
            computation recomputes ema(prices[:i+1], 12) and
            ema(prices[:i+1], 26) from scratch at every index to build the
            macd-line history -- O(n^2) work for something an incremental
            EMA does in O(n). The approved Phase 0 specification (Section 4)
            explicitly calls for an "incremental/streaming-safe" EMA
            specifically to fix this. This implementation builds the
            macd-line series in one incremental pass and signal-EMAs that
            series directly.
            DEVIATION FROM LEGACY (correctness, discovered while verifying
            this implementation against a legacy-faithful reference): the
            legacy loop is `for i in range(26, len(prices))`, which means
            its FIRST macd-line value uses 27 closes (prices[:27]) -- it
            silently skips the value that would come from exactly 26
            closes, even though ema(prices, 26) itself considers 26 closes
            sufficient. There is no comment in the legacy code explaining
            this boundary; it reads as an unintentional off-by-one, not a
            deliberate design choice, and its effect is small but real
            (verified: ~0.03% difference on a representative dataset).
            This implementation includes the complete, correct macd-line
            series (starting from the first value ema() itself considers
            valid) as input to the signal EMA, rather than reproducing an
            undocumented, uncommented boundary quirk.
            Minimum data (verified empirically, not just derived by hand --
            see test_indicators.py): 26 closes for the line; 34 closes for
            a first signal/histogram value.

  Stochastic RSI -- rolling min-max normalization of the canonical Wilder
            RSI series (period=14 by default, matching the legacy code's
            core windowing logic and its own comment about why it reuses
            one continuous RSI series rather than re-seeding Wilder's
            smoothing on a tiny window each time). Raw value =
            (rsi[-1]-min(window))/(max(window)-min(window)) * 100.
            EXPLICIT CHOICE, DOCUMENTED (neither the legacy code nor the
            approved specification pins this down): the legacy
            implementation exposes only ONE raw value, but Phase 1's
            FeatureSet contract expects a genuine two-line %K/%D pair
            (`stoch_rsi_k`, `stoch_rsi_d` are separate fields). This
            implementation adds the standard, widely-used StochRSI
            smoothing convention on top of the legacy's core rolling-window
            math: %K = 3-period SMA of the raw stochastic value, %D =
            3-period SMA of %K (the common "14,14,3,3" StochRSI
            configuration used by most charting platforms). A flat RSI
            window (max==min) yields 50, matching the legacy convention for
            that specific degenerate-but-data-complete case.
            Minimum data: empirically determined and verified by test.

  Bollinger Bands -- 20-period SMA midline, +/-2 population standard
            deviations (dividing by `period`, not `period-1` -- matches the
            legacy implementation's exact formula). Minimum data: 20 closes.

  ATR    -- Wilder's smoothing over true range (max of high-low,
            |high-prevClose|, |low-prevClose|), seeded by a simple average
            of the first `period` true ranges, then carried forward by
            Wilder's recursion -- matches the legacy implementation exactly
            (including its own "BUG FIX" note about why Wilder's smoothing,
            not a plain moving average, is used). Minimum data: `period + 1`
            closes.

  Volume ratio -- see features/volume.py for this indicator; it is kept in
            its own module (matching the suggested package structure)
            rather than folded in here, since it is volume-derived rather
            than price-derived like the six indicators above.
"""
import math
from typing import List, Optional, Sequence, Tuple

RSI_PERIOD_DEFAULT = 14
STOCH_RSI_PERIOD_DEFAULT = 14
STOCH_RSI_K_SMOOTHING = 3
STOCH_RSI_D_SMOOTHING = 3
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLLINGER_PERIOD_DEFAULT = 20
BOLLINGER_STD_MULTIPLIER = 2.0
ATR_PERIOD_DEFAULT = 14


def _all_finite(values: Sequence[float]) -> bool:
    return all(math.isfinite(v) for v in values)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------

def ema(closes: Sequence[float], period: int) -> Optional[float]:
    """Single EMA value for `period` over `closes`. SMA-seeded. None if
    fewer than `period` closes are available."""
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if len(closes) < period or not _all_finite(closes):
        return None
    k = 2.0 / (period + 1)
    e = sum(closes[:period]) / period
    for x in closes[period:]:
        e = x * k + e * (1 - k)
    return e


def ema_series(closes: Sequence[float], period: int) -> List[Optional[float]]:
    """EMA value aligned to every index of `closes` (incremental, O(n)).
    Indices before the first `period` closes are None -- warm-up, not zero.
    """
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if n < period or not _all_finite(closes):
        return out
    k = 2.0 / (period + 1)
    e = sum(closes[:period]) / period
    out[period - 1] = e
    for i in range(period, n):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out


# ---------------------------------------------------------------------------
# RSI (Wilder's smoothing)
# ---------------------------------------------------------------------------

def _rsi_value_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_series(closes: Sequence[float], period: int = RSI_PERIOD_DEFAULT) -> List[Optional[float]]:
    """Wilder-smoothed RSI aligned to every index of `closes`. None for
    every index before the first genuinely computable value (index
    `period`, since `period` deltas require `period + 1` closes) -- this
    implementation never fabricates a placeholder value the way the legacy
    rsi(return_series=True) does for a too-short input (see module
    docstring)."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if n < period + 1 or not _all_finite(closes):
        return out
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_value_from_averages(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_value_from_averages(avg_gain, avg_loss)
    return out


def rsi(closes: Sequence[float], period: int = RSI_PERIOD_DEFAULT) -> Optional[float]:
    """Latest Wilder RSI value, or None if not enough data (see rsi_series)."""
    series = rsi_series(closes, period)
    return series[-1] if series else None


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    closes: Sequence[float],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(macd_line, macd_signal, macd_hist) -- each None independently if not
    enough data for that specific component (line needs `slow` closes;
    signal/hist additionally need `signal` valid macd-line values)."""
    if not _all_finite(closes):
        return None, None, None
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    macd_line_series: List[Optional[float]] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_series, slow_series)
    ]
    macd_line = macd_line_series[-1] if macd_line_series else None

    valid_line_values = [v for v in macd_line_series if v is not None]
    if len(valid_line_values) < signal:
        return macd_line, None, None

    signal_series = ema_series(valid_line_values, signal)
    macd_signal = signal_series[-1]
    macd_hist = (macd_line - macd_signal) if (macd_line is not None and macd_signal is not None) else None
    return macd_line, macd_signal, macd_hist


# ---------------------------------------------------------------------------
# Stochastic RSI
# ---------------------------------------------------------------------------

def stoch_rsi(
    closes: Sequence[float],
    rsi_period: int = RSI_PERIOD_DEFAULT,
    stoch_period: int = STOCH_RSI_PERIOD_DEFAULT,
    k_smoothing: int = STOCH_RSI_K_SMOOTHING,
    d_smoothing: int = STOCH_RSI_D_SMOOTHING,
) -> Tuple[Optional[float], Optional[float]]:
    """(%K, %D) -- see module docstring for the %K/%D smoothing convention
    (a documented choice, not present in the legacy code, added to honor
    Phase 1's two-line FeatureSet contract)."""
    if not _all_finite(closes):
        return None, None
    r_series = rsi_series(closes, rsi_period)
    valid_rsi = [v for v in r_series if v is not None]
    if len(valid_rsi) < stoch_period:
        return None, None

    raw_values: List[float] = []
    for i in range(stoch_period - 1, len(valid_rsi)):
        window = valid_rsi[i - stoch_period + 1 : i + 1]
        mn, mx = min(window), max(window)
        raw_values.append(50.0 if mx == mn else (window[-1] - mn) / (mx - mn) * 100.0)

    if len(raw_values) < k_smoothing:
        return None, None
    k_series = _sma_series(raw_values, k_smoothing)
    valid_k = [v for v in k_series if v is not None]
    k = valid_k[-1] if valid_k else None

    if len(valid_k) < d_smoothing:
        return _clamp_pct(k), None
    d_series = _sma_series(valid_k, d_smoothing)
    d = d_series[-1]
    return _clamp_pct(k), _clamp_pct(d)


def _clamp_pct(value: Optional[float]) -> Optional[float]:
    """Clamp to [0, 100]. Stochastic RSI's %K/%D are mathematically
    guaranteed to fall in this range (they are a doubly-smoothed min-max
    normalization) -- any excursion outside it can only be floating-point
    representation error accumulated through the RSI -> raw-stoch -> %K ->
    %D chain (verified empirically -- see test_indicators.py), never a
    genuine out-of-range signal. This is a narrow, specific clamp on a
    value known by construction to be bounded, not a general-purpose
    "clamp everything" pattern."""
    if value is None:
        return None
    return max(0.0, min(100.0, value))


def _sma_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Simple moving average, recomputing each window's sum fresh rather
    than an incremental running-sum -- deliberately not the more common
    O(1)-per-step optimization. Verified empirically (see
    test_indicators.py) that the incremental running-sum variant can
    accumulate floating-point drift over many iterations and occasionally
    push a bounded value a hair outside its mathematically guaranteed
    range. `period` is always small here (3), so the O(period) recompute
    per step costs nothing meaningful in practice; correctness matters far
    more than an unmeasurable performance difference."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if n < period:
        return out
    for i in range(period - 1, n):
        out[i] = sum(values[i - period + 1 : i + 1]) / period
    return out


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    closes: Sequence[float],
    period: int = BOLLINGER_PERIOD_DEFAULT,
    std_multiplier: float = BOLLINGER_STD_MULTIPLIER,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(upper, mid, lower). Population standard deviation (divides by
    `period`, not `period - 1`) -- matches the legacy implementation
    exactly. None for all three if fewer than `period` closes."""
    if len(closes) < period or not _all_finite(closes):
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return mid + std_multiplier * std, mid, mid - std_multiplier * std


# ---------------------------------------------------------------------------
# ATR (Wilder's smoothing)
# ---------------------------------------------------------------------------

def true_range_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> List[float]:
    """True range for each index from 1 onward (needs a previous close).
    Length is len(closes) - 1."""
    n = len(closes)
    return [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, n)
    ]


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = ATR_PERIOD_DEFAULT,
) -> Optional[float]:
    """Latest Wilder-smoothed ATR, or None if fewer than `period + 1` candles."""
    series = atr_series(highs, lows, closes, period)
    return series[-1] if series else None


def atr_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = ATR_PERIOD_DEFAULT,
) -> List[Optional[float]]:
    """Wilder-smoothed ATR aligned to every index of `closes`. None before
    the first genuinely computable value."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if n < period + 1 or not (_all_finite(highs) and _all_finite(lows) and _all_finite(closes)):
        return out
    trs = true_range_series(highs, lows, closes)  # trs[i] corresponds to closes[i+1]
    if len(trs) < period:
        return out
    a = sum(trs[:period]) / period
    out[period] = a  # trs[0..period-1] -> closes[1..period], so first ATR aligns to closes[period]
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i + 1] = a
    return out
