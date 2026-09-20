"""Data-layer-internal models.

These are NOT part of the frozen Phase 1 contracts (smart_trade_analyzer.contracts)
-- they are working types used only within this package, to carry information
between normalization, validation, and quality evaluation before a canonical
CandleData / MarketData / DataQuality (all defined in contracts/) is produced.

Also defines TIMEFRAME_DURATION_SECONDS, the single source of truth for "how
long is one candle of timeframe X" -- used by normalizer.py (to decide
whether the most recent candle is still forming), validator.py (to detect
gaps and evaluate freshness), and nowhere else. The legacy analyzer.py
defined its Bitget-granularity casing maps twice (once in
get_realtime_indicators, once in _fetch_candles_raw) -- this package defines
each shared mapping exactly once.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..contracts import CandleData, Timeframe, DataQualityState


def utc_now() -> datetime:
    """The single place "current time" is read anywhere in this package.

    Returns a NAIVE datetime whose value represents UTC (not local time,
    and not timezone-aware). This matches the legacy analyzer's convention
    of naive datetimes throughout (datetime.now(), naive strptime with no
    tzinfo) -- so this package stays consistent with it -- while still being
    correct: Bitget candle timestamps are UNIX epoch milliseconds (a
    timezone-agnostic instant), and this module converts them with
    datetime.utcfromtimestamp() (see normalizer.py), which is naive-but-UTC.
    Using datetime.now() (naive-but-LOCAL) instead would silently shift
    every freshness/is_closed comparison by the running machine's UTC
    offset -- a real and easy-to-miss bug class this function exists to
    prevent by being the one place that decision is made.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Shared constant: canonical duration of one candle, per Phase 1 Timeframe.
# ---------------------------------------------------------------------------

TIMEFRAME_DURATION_SECONDS: Dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 5 * 60,
    Timeframe.M15: 15 * 60,
    Timeframe.M30: 30 * 60,
    Timeframe.H1: 60 * 60,
    Timeframe.H2: 2 * 60 * 60,
    Timeframe.H4: 4 * 60 * 60,
    Timeframe.D1: 24 * 60 * 60,
    Timeframe.W1: 7 * 24 * 60 * 60,
}


def timeframe_duration_seconds(timeframe: Timeframe) -> int:
    """Return the duration of one candle for `timeframe`, in seconds.

    Raises KeyError (not a DataLayerError) if `timeframe` is not a member of
    the Timeframe enum at all -- that would mean someone is passing a raw
    string instead of a Timeframe member, which is a programming error this
    module deliberately does not try to paper over.
    """
    return TIMEFRAME_DURATION_SECONDS[timeframe]


# ---------------------------------------------------------------------------
# Ticker price -- the result of an adapter's get_ticker_price(), before it is
# folded into a MarketData.live_price / .price_source / .price_quality.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickerPrice:
    price: float
    source: str          # e.g. "bitget_ticker" -- mirrors MarketData.price_source
    fetched_at: datetime
    quality: DataQualityState

    def __post_init__(self):
        if self.price <= 0:
            raise ValueError(f"TickerPrice.price must be positive, got {self.price}")
        if not self.source:
            raise ValueError("TickerPrice.source must be a non-empty string")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizationIssue:
    """A single raw row that could NOT be turned into a CandleData at all."""
    index: int             # position in the raw response, 0-based
    raw: object             # the raw row itself (kept for debugging/logging)
    reason: str              # human-readable -- e.g. "low (105.0) exceeds high (100.0)"


@dataclass(frozen=True)
class NormalizationResult:
    """Output of normalizer.normalize_candles(): everything that COULD be
    parsed, plus a full accounting of everything that could NOT be, with why.
    Nothing is silently dropped without a corresponding issue entry."""
    candles: List[CandleData]
    issues: List[NormalizationIssue] = field(default_factory=list)

    @property
    def attempted_count(self) -> int:
        return len(self.candles) + len(self.issues)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Issue-type labels -- plain string constants (not a Phase-1-style shared
# enum, since these are internal diagnostic labels local to this package,
# not a value with system-wide significance the way contracts/enums.py's
# enums are).
ISSUE_DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
ISSUE_UNSORTED = "UNSORTED"
ISSUE_FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
ISSUE_GAP = "GAP"
ISSUE_UNEXPECTED_UNCLOSED_CANDLE = "UNEXPECTED_UNCLOSED_CANDLE"


@dataclass(frozen=True)
class ValidationIssue:
    issue_type: str
    index: Optional[int]     # position in the candle sequence this concerns, if applicable
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    """Pure analysis of an already-normalized candle sequence. Does NOT
    modify the sequence (no de-duplication, no re-sorting) -- see
    validator.py's module docstring for why."""
    issues: List[ValidationIssue] = field(default_factory=list)
    duplicate_count: int = 0
    gap_count: int = 0
    missing_candles_estimate: int = 0

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreshnessResult:
    state: DataQualityState
    latest_closed_candle_time: Optional[datetime]
    age_seconds: Optional[float]
    max_age_for_valid_seconds: float
    max_age_for_degraded_seconds: float
    detail: str
