"""Data-quality evaluation and the top-level "fetch canonical MarketData"
orchestration -- the last two stages of the target flow:

    ... -> VALIDATION -> DATA QUALITY -> CANONICAL MarketData

This module NEVER decides LONG/SHORT/WAIT/NO_TRADE -- it only reports
whether market data is VALID, DEGRADED, or UNAVAILABLE, and why. That is the
architecture rule for the whole data/ package, and this is the module where
it matters most to keep sight of, since this is where everything converges.

Phase 2 review fix, Issue 2: evaluate_candle_quality/evaluate_data_quality
now accept the normalization issues produced while fetching candles (rows
that could not be parsed into a CandleData at all), and factor them into
both DataQuality.reasons and the VALID/DEGRADED/UNAVAILABLE classification.
Previously these issues were logged by the adapter and then genuinely lost
-- this module never saw them, so a batch of malformed rows was
indistinguishable from "the exchange simply returned fewer candles." This
module owns that judgment now, alongside every other quality threshold, and
does so without importing anything Bitget-specific: NormalizationIssue is a
plain, source-agnostic type already defined in models.py.
"""
from datetime import datetime
from typing import List, Optional, Tuple

from ..contracts import CandleData, DataQuality, DataQualityState, MarketData, MarketType, Timeframe
from .exceptions import DataSourceError
from .models import NormalizationIssue, TickerPrice, ValidationResult, FreshnessResult, utc_now
from .models import ISSUE_FUTURE_TIMESTAMP, ISSUE_UNSORTED
from .source import MarketDataSource
from .validator import validate_sequence, check_freshness

# ---------------------------------------------------------------------------
# Thresholds -- match the candle-count reasoning already approved in the
# Phase 0 technical specification, Section 3 (Data Quality Contract):
#   ">= required count for the longest-lookback feature in use (200 for EMA200)"
#   "DEGRADED if ... enough for a reduced feature subset (e.g. >=50)"
#   "UNAVAILABLE if ... < minimum needed for any feature (e.g. <20)"
# Reused verbatim here rather than re-derived, so Phase 2 stays consistent
# with what was already reviewed and approved.
# ---------------------------------------------------------------------------

REQUIRED_CANDLES_FOR_FULL = 200
MIN_CANDLES_FOR_DEGRADED = 50
MIN_USABLE_CANDLES = 20

MAX_ISSUE_EXAMPLES_IN_REASONS = 3
# How many individual normalization-issue reasons to quote verbatim in
# DataQuality.reasons before summarizing the rest as "and N more" -- keeps
# .reasons readable even when dozens of rows are malformed, while still
# satisfying "the exact number ... must be available."


def _summarize_normalization_issues(issues: List[NormalizationIssue]) -> str:
    if not issues:
        return ""
    examples = [i.reason for i in issues[:MAX_ISSUE_EXAMPLES_IN_REASONS]]
    text = "; ".join(examples)
    remaining = len(issues) - len(examples)
    if remaining > 0:
        text += f"; and {remaining} more"
    return text


def evaluate_candle_quality(
    candles: List[CandleData],
    validation: ValidationResult,
    freshness: FreshnessResult,
    normalization_issues: Optional[List[NormalizationIssue]] = None,
) -> Tuple[DataQualityState, List[str]]:
    """Classify the candle source alone (not combined with price). Returns
    (state, reasons) -- reasons is always populated with the facts that led
    to the classification, even for VALID, so a caller never has to guess
    why a state was assigned.

    `normalization_issues` (review fix, Issue 2): raw rows that the
    normalizer could not turn into a CandleData at all, passed through
    unchanged from whatever MarketDataSource.get_candles() returned. A few
    malformed rows among many good ones is a recoverable DEGRADED condition;
    normalization issues meeting or exceeding the number of successfully
    parsed candles is treated as effectively UNAVAILABLE, regardless of the
    raw candle count -- matching "a few malformed rows can result in
    DEGRADED... overwhelmingly malformed data can result in UNAVAILABLE."
    """
    normalization_issues = normalization_issues or []
    reasons: List[str] = []
    n = len(candles)
    issue_count = len(normalization_issues)

    unsorted_count = sum(1 for i in validation.issues if i.issue_type == ISSUE_UNSORTED)
    future_count = sum(1 for i in validation.issues if i.issue_type == ISSUE_FUTURE_TIMESTAMP)

    if validation.duplicate_count:
        reasons.append(f"{validation.duplicate_count} duplicate timestamp(s) detected")
    if validation.gap_count:
        reasons.append(
            f"{validation.gap_count} gap(s) detected (~{validation.missing_candles_estimate} "
            f"candle(s) likely missing)"
        )
    if unsorted_count:
        reasons.append(f"{unsorted_count} out-of-order candle(s) detected")
    if future_count:
        reasons.append(f"{future_count} candle(s) with a future timestamp detected")
    if issue_count:
        reasons.append(
            f"{issue_count} of {n + issue_count} raw candle row(s) failed normalization "
            f"({_summarize_normalization_issues(normalization_issues)})"
        )
    reasons.append(freshness.detail)

    if n < MIN_USABLE_CANDLES:
        state = DataQualityState.UNAVAILABLE
        reasons.append(f"only {n} usable candle(s), below the minimum of {MIN_USABLE_CANDLES}")
    elif issue_count and issue_count >= n:
        # Overwhelming normalization failure -- at least as many rows failed
        # to parse as survived. Too corrupted to trust even though some
        # candles are technically present.
        state = DataQualityState.UNAVAILABLE
        reasons.append(
            f"normalization issues ({issue_count}) meet or exceed successfully parsed "
            f"candles ({n}) -- data too corrupted to trust"
        )
    elif unsorted_count or future_count:
        # Structural integrity problems -- even a large candle count is not
        # trustworthy if the sequence isn't honestly ordered/timestamped.
        state = DataQualityState.DEGRADED
        reasons.append("structural anomaly present -- downgraded regardless of candle count")
    elif issue_count:
        # A minority of rows failed -- recoverable degradation, not
        # disqualifying (requirement: "do NOT automatically mark the entire
        # dataset UNAVAILABLE because one row is malformed").
        state = DataQualityState.DEGRADED
    elif n < MIN_CANDLES_FOR_DEGRADED:
        state = DataQualityState.DEGRADED
        reasons.append(f"only {n} candle(s), below the {MIN_CANDLES_FOR_DEGRADED} needed for a full feature set")
    elif n < REQUIRED_CANDLES_FOR_FULL or validation.gap_count or validation.duplicate_count:
        state = DataQualityState.DEGRADED
    else:
        state = DataQualityState.VALID

    # Freshness can only ever pull the state down, never up -- never convert
    # an UNAVAILABLE freshness read into anything milder just to keep the
    # analyzer running (explicit rule from the approved specification).
    if freshness.state == DataQualityState.UNAVAILABLE:
        state = DataQualityState.UNAVAILABLE
    elif freshness.state == DataQualityState.DEGRADED and state == DataQualityState.VALID:
        state = DataQualityState.DEGRADED

    return state, reasons


def evaluate_data_quality(
    candles: List[CandleData],
    validation: ValidationResult,
    freshness: FreshnessResult,
    ticker: Optional[TickerPrice],
    normalization_issues: Optional[List[NormalizationIssue]] = None,
) -> DataQuality:
    """Combine candle quality and live-price quality into one DataQuality.

    Live price is treated as load-bearing, matching the approved
    specification's Section 3: candles or price UNAVAILABLE makes the
    overall result UNAVAILABLE, not just DEGRADED.
    """
    candle_state, candle_reasons = evaluate_candle_quality(
        candles, validation, freshness, normalization_issues,
    )
    price_state = ticker.quality if ticker is not None else DataQualityState.UNAVAILABLE

    reasons = list(candle_reasons)
    if ticker is None:
        reasons.append("live price unavailable")

    per_source = {"candles": candle_state, "live_price": price_state}
    excluded_sources = [name for name, state in per_source.items() if state == DataQualityState.UNAVAILABLE]

    if candle_state == DataQualityState.UNAVAILABLE or price_state == DataQualityState.UNAVAILABLE:
        overall = DataQualityState.UNAVAILABLE
    elif candle_state == DataQualityState.DEGRADED or price_state == DataQualityState.DEGRADED:
        overall = DataQualityState.DEGRADED
    else:
        overall = DataQualityState.VALID

    return DataQuality(
        overall=overall,
        candle_count=len(candles),
        candle_count_required=REQUIRED_CANDLES_FOR_FULL,
        per_source=per_source,
        excluded_sources=excluded_sources,
        reasons=reasons,
    )


def fetch_canonical_market_data(
    source: MarketDataSource,
    symbol: str,
    pair: str,
    market_type: MarketType,
    timeframe: Timeframe,
    limit: int,
    as_of: Optional[datetime] = None,
) -> Tuple[MarketData, DataQuality]:
    """The full target flow, composed: fetch -> validate -> evaluate quality
    -> canonical MarketData. Always returns a value -- never raises for
    ordinary data problems (empty response, stale feed, malformed rows
    inside the adapter). A DataSourceError from the adapter is caught here
    and converted into an UNAVAILABLE DataQuality + an empty-candle
    MarketData, per "do not fabricate fallback market data, but also do not
    let an exception replace a value where a value was promised."

    This function makes no trading decision of any kind -- it produces
    exactly two things: a MarketData snapshot and the DataQuality that
    describes how much to trust it.
    """
    if as_of is None:
        as_of = utc_now()

    try:
        fetch_result = source.get_candles(symbol=pair, timeframe=timeframe, limit=limit, as_of=as_of)
    except DataSourceError as exc:
        dq = DataQuality(
            overall=DataQualityState.UNAVAILABLE,
            candle_count=0,
            candle_count_required=REQUIRED_CANDLES_FOR_FULL,
            per_source={"candles": DataQualityState.UNAVAILABLE, "live_price": DataQualityState.UNAVAILABLE},
            excluded_sources=["candles", "live_price"],
            reasons=[f"candle fetch failed: {exc}"],
        )
        md = MarketData(
            symbol=symbol, pair=pair, market_type=market_type, timeframe=timeframe,
            as_of=as_of, candles=[], live_price=None,
            price_source="unavailable", price_quality=DataQualityState.UNAVAILABLE,
        )
        return md, dq

    # Review fix, Issue 2: fetch_result is a NormalizationResult -- its
    # .issues (rows that failed to normalize) are threaded through to
    # evaluate_data_quality below, rather than being discarded here. This is
    # the fix: normalization problems now reach DataQuality.reasons instead
    # of disappearing once the adapter logged them.
    candles = fetch_result.candles
    normalization_issues = fetch_result.issues

    validation = validate_sequence(candles, timeframe, as_of=as_of)
    freshness = check_freshness(candles, timeframe, as_of=as_of)

    try:
        ticker = source.get_ticker_price(symbol=pair)
    except Exception:
        # get_ticker_price's documented contract is "returns None on
        # failure, never raises" -- this except is defense in depth in case
        # an adapter implementation doesn't honor that, not the expected path.
        ticker = None

    dq = evaluate_data_quality(candles, validation, freshness, ticker, normalization_issues=normalization_issues)

    md = MarketData(
        symbol=symbol, pair=pair, market_type=market_type, timeframe=timeframe,
        as_of=as_of, candles=candles,
        live_price=ticker.price if ticker else None,
        price_source=ticker.source if ticker else "unavailable",
        price_quality=ticker.quality if ticker else DataQualityState.UNAVAILABLE,
    )
    return md, dq


def closed_candles_only(market_data: MarketData) -> List[CandleData]:
    """Convenience filter for future consumers (e.g. the Feature Engine)
    that only want completed candles. Does NOT modify Phase 1's frozen
    MarketData -- this is a plain external function, not a method on it, so
    that contracts/ never needs a data/-layer-motivated change."""
    return [c for c in market_data.candles if c.is_closed]
