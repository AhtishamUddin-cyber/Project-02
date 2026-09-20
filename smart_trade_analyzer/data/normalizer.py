"""Raw-response normalization: Bitget's raw candle/ticker payloads -> the
canonical Phase 1 contracts (CandleData) or this package's own TickerPrice.

Pure functions only -- no network calls, no retries, no exceptions escaping
for "some rows were bad" (that is reported via NormalizationResult.issues,
per the approved rule: never silently discard a corrupted record without
recording why). DataNormalizationError is reserved for payloads so malformed
there is nothing to iterate over at all (see exceptions.py).

Precondition on row order: `normalize_candles` expects raw rows to arrive in
the same newest-first order Bitget's candles endpoints actually return (this
was verified directly from the existing project's own `candles.reverse()`
calls in get_realtime_indicators and _fetch_candles_raw -- not assumed).
`reverse_input=True` (the default) reproduces that exact `.reverse()` step.
Pass `reverse_input=False` if the caller already has ascending-order rows
(e.g. in a test). This module does NOT re-sort by parsed timestamp -- if the
result is not actually in ascending order after this step (a corrupted or
unexpected response), that is deliberately left for validator.py to detect
and report, not silently fixed here.

Non-finite numeric values (NaN, +Infinity, -Infinity): Python's float()
parses strings like "nan"/"inf"/"-inf"/"Infinity" successfully -- it does
NOT raise. Left unchecked, this would let a NaN/Infinity silently become a
CandleData field. Phase 1's CandleData.__post_init__ happens to catch SOME
of these by accident (NaN/Infinity comparisons are always False in Python,
so e.g. open=NaN fails the "low<=open<=high" check) but not all of them --
verified empirically that high=+inf, low=-inf, and volume=NaN/+inf all pass
Phase 1's existing checks uncaught, since "anything <= +inf" and
"anything >= -inf" are trivially true, and "NaN < 0" is False. This module
therefore explicitly checks every numeric field with math.isfinite() BEFORE
attempting to construct a CandleData, rather than relying on that
incidental, incomplete side effect. Fixed here (the normalization boundary),
not in the frozen Phase 1 contracts.

Overflow: an absurdly large timestamp can make datetime.fromtimestamp()
raise OverflowError or OSError (verified empirically -- not just ValueError)
depending on how far out of range it is. Both are now caught alongside
ValueError/TypeError/KeyError/IndexError so an overflowing timestamp becomes
a NormalizationIssue like any other malformed field, rather than escaping
normalize_candles() as an uncaught exception.
"""
import math
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence

from ..contracts import CandleData, DataQualityState, Timeframe
from .exceptions import DataNormalizationError
from .models import NormalizationIssue, NormalizationResult, TickerPrice, timeframe_duration_seconds, utc_now

_NUMERIC_ROW_FIELDS = ("open", "high", "low", "close", "volume")


def _parse_row(row: Sequence[Any], timeframe: Timeframe, as_of) -> CandleData:
    """Parse one raw Bitget candle row: [timestamp_ms, open, high, low,
    close, volume, ...]. Raises ValueError/TypeError/KeyError/OverflowError/
    OSError on any problem -- callers catch and convert to a NormalizationIssue."""
    if row is None:
        raise ValueError("row is None")
    if len(row) < 6:
        raise ValueError(
            f"row has only {len(row)} field(s), expected at least 6 "
            f"[timestamp, open, high, low, close, volume]"
        )
    ts_ms = int(row[0])
    open_ = float(row[1])
    high = float(row[2])
    low = float(row[3])
    close = float(row[4])
    volume = float(row[5])

    for field_name, value in zip(_NUMERIC_ROW_FIELDS, (open_, high, low, close, volume)):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} is not a finite number: {value!r}")

    open_time = _ms_epoch_to_utc_naive(ts_ms)
    duration = timeframe_duration_seconds(timeframe)
    is_closed = (open_time + timedelta(seconds=duration)) <= as_of

    # CandleData.__post_init__ (Phase 1, frozen) already enforces OHLC
    # sanity (low<=open<=high, low<=close<=high, volume>=0) -- we deliberately
    # do not re-check those bounds here ourselves (no duplicated validation
    # logic); a violation raises ValueError from CandleData's own
    # constructor, which propagates up and is caught by normalize_candles.
    # The finiteness check above runs first and separately, since it is NOT
    # reliably covered by those OHLC comparisons alone (see module docstring).
    return CandleData(
        open_time=open_time, open=open_, high=high, low=low,
        close=close, volume=volume, is_closed=is_closed,
    )


def _ms_epoch_to_utc_naive(ts_ms: int):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def normalize_candles(
    raw_rows: Sequence[Any],
    timeframe: Timeframe,
    as_of=None,
    reverse_input: bool = True,
) -> NormalizationResult:
    """Convert raw Bitget candle rows into a NormalizationResult.

    `as_of` defaults to utc_now() if not given -- pass it explicitly in
    tests for determinism (see models.utc_now's docstring for why this
    package always threads "now" through a parameter rather than calling
    the clock deep inside logic).
    """
    if raw_rows is None:
        raise DataNormalizationError("raw_rows is None -- expected a list of candle rows")
    if not isinstance(raw_rows, (list, tuple)):
        raise DataNormalizationError(
            f"raw_rows must be a list/tuple of rows, got {type(raw_rows).__name__}"
        )

    if as_of is None:
        as_of = utc_now()

    rows = list(reversed(raw_rows)) if reverse_input else list(raw_rows)

    candles: List[CandleData] = []
    issues: List[NormalizationIssue] = []
    for i, row in enumerate(rows):
        try:
            candles.append(_parse_row(row, timeframe, as_of))
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, OSError) as exc:
            issues.append(NormalizationIssue(index=i, raw=row, reason=str(exc)))

    return NormalizationResult(candles=candles, issues=issues)


def normalize_ticker_price(raw_data: Any, source: str, as_of=None) -> TickerPrice:
    """Convert one raw Bitget ticker entry (a dict with a 'lastPr' or 'last'
    field, matching the existing project's own get_single_ticker_price)
    into a TickerPrice.

    Raises DataNormalizationError if no usable price can be extracted --
    the adapter (bitget.py) catches this and translates it into the
    documented get_ticker_price()->None contract; this function itself
    always either succeeds or clearly reports why it could not.
    """
    if as_of is None:
        as_of = utc_now()
    if not isinstance(raw_data, dict):
        raise DataNormalizationError(
            f"ticker entry must be a dict, got {type(raw_data).__name__}"
        )
    raw_price = raw_data.get("lastPr")
    if raw_price in (None, "", 0, "0"):
        raw_price = raw_data.get("last")
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        raise DataNormalizationError(
            f"could not parse a usable price from ticker entry (lastPr={raw_data.get('lastPr')!r}, "
            f"last={raw_data.get('last')!r})"
        )
    if not math.isfinite(price):
        raise DataNormalizationError(f"ticker price is not a finite number: {price!r}")
    if price <= 0:
        raise DataNormalizationError(f"ticker price must be positive, got {price}")

    return TickerPrice(price=price, source=source, fetched_at=as_of, quality=DataQualityState.VALID)
