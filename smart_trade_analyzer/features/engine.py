"""The Feature Engine: MarketData (Phase 2) -> FeatureSet (Phase 1), and
nothing else. This is the one place indicators.py's, volume.py's,
structure.py's, and divergence.py's pure functions are assembled into the
canonical, contract-shaped output every other package consumes.

Phase 4 update: `swing_support`, `swing_resistance`, and `divergence` are
now genuinely computed (features/structure.py, features/divergence.py),
replacing the Phase 3 placeholders that were explicitly documented as
deferred, not accidental (see setup/engine.py's docstring for the setup
families this unblocks).

`completeness` DELIBERATELY remains computed over only the same 16 fields
it covered in Phase 3 (see readiness.py) -- this is a considered decision,
not an oversight carried over from before these fields existed. Every one
of those 16 fields is DATA-DETERMINISTIC: given enough candles, None can
only mean insufficient or non-finite data. `swing_support`,
`swing_resistance`, and `divergence` are not like that -- None is their
overwhelmingly common, fully-expected value even with abundant, perfectly
clean data (a monotonic trend can genuinely have no qualifying swing level
on one side; most candles simply have no active divergence pattern).
Folding them into `completeness` would make a data-quality metric
fluctuate with normal market structure instead of with data availability,
which is exactly the score-vs-signal conflation this whole rebuild exists
to eliminate. `readiness()` in readiness.py DOES cover all 19 fields now
(it answers a different, purely count-based question: "has enough history
accumulated to attempt this computation at all"), and that distinction is
what makes both functions correct rather than contradictory.

No-lookahead, enforced structurally, not just by convention: this module
filters MarketData.candles down to `is_closed == True` BEFORE calling any
indicator function, and every indicator function itself only ever looks
backward through the array it's given (see indicators.py's causality tests
in test_no_lookahead.py). The still-forming current candle -- even if
present in MarketData.candles -- never reaches an indicator calculation as
if it were completed historical evidence.

No network calls, no Streamlit import, no wall-clock reads: `as_of` is
taken directly from the MarketData passed in, never read from the system
clock. Deterministic: the same MarketData always produces the same
FeatureSet, which is what makes this engine usable identically for live
analysis and a later phase's backtesting.
"""
from typing import Dict, List, Optional

from ..contracts import CandleData, FeatureSet, MarketData
from . import divergence as div
from . import indicators as ind
from . import readiness
from . import structure as struct
from . import volume as vol


def closed_candles(market_data: MarketData) -> List[CandleData]:
    """The subset of MarketData.candles that are actually completed --
    the only candles this engine (or anything downstream of it) is allowed
    to treat as historical evidence."""
    return [c for c in market_data.candles if c.is_closed]


def compute_feature_set(market_data: MarketData) -> Optional[FeatureSet]:
    """Compute the canonical FeatureSet for `market_data`.

    Returns None if there is no closed candle at all -- FeatureSet.close is
    a required, positive field (Phase 1's frozen contract), and there is
    nothing honest to put there without at least one completed candle. This
    is a "cannot compute anything" signal, not a fabricated zero-feature
    FeatureSet -- callers (the future Opportunity Scanner) are expected to
    treat None here the same way they would treat UNAVAILABLE data quality.
    """
    closed = closed_candles(market_data)
    if not closed:
        return None

    closes = [c.close for c in closed]
    highs = [c.high for c in closed]
    lows = [c.low for c in closed]
    volumes = [c.volume for c in closed]

    ema9 = ind.ema(closes, 9)
    ema21 = ind.ema(closes, 21)
    ema50 = ind.ema(closes, 50)
    ema200 = ind.ema(closes, 200)

    rsi14 = ind.rsi(closes)

    macd_line, macd_signal, macd_hist = ind.macd(closes)

    stoch_k, stoch_d = ind.stoch_rsi(closes)

    bb_upper, bb_mid, bb_lower = ind.bollinger_bands(closes)

    atr_value = ind.atr(highs, lows, closes)
    last_close = closes[-1]
    atr_pct = (atr_value / last_close * 100.0) if (atr_value is not None and last_close > 0) else None

    vol_ratio = vol.volume_ratio(volumes)

    swing_support, swing_resistance = struct.swing_levels(highs, lows, current_close=last_close)
    divergence_value = div.detect_divergence(highs, lows, closes)

    # Deliberately excludes swing_support/swing_resistance/divergence -- see
    # the module docstring's completeness section for why folding them in
    # would corrupt this metric rather than improve it.
    field_values: Dict[str, object] = {
        "ema9": ema9, "ema21": ema21, "ema50": ema50, "ema200": ema200,
        "rsi14": rsi14,
        "macd_line": macd_line, "macd_signal": macd_signal, "macd_hist": macd_hist,
        "stoch_rsi_k": stoch_k, "stoch_rsi_d": stoch_d,
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower,
        "atr": atr_value, "atr_pct": atr_pct,
        "volume_ratio": vol_ratio,
    }
    completeness = readiness.compute_completeness(field_values)

    return FeatureSet(
        symbol=market_data.symbol,
        timeframe=market_data.timeframe,
        as_of=market_data.as_of,
        close=last_close,
        ema9=ema9, ema21=ema21, ema50=ema50, ema200=ema200,
        rsi14=rsi14,
        stoch_rsi_k=stoch_k, stoch_rsi_d=stoch_d,
        macd_line=macd_line, macd_signal=macd_signal, macd_hist=macd_hist,
        bb_upper=bb_upper, bb_mid=bb_mid, bb_lower=bb_lower,
        atr=atr_value, atr_pct=atr_pct,
        volume_ratio=vol_ratio,
        swing_support=swing_support, swing_resistance=swing_resistance,
        divergence=divergence_value,
        completeness=completeness,
    )
