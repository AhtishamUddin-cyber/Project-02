"""Bitget market-data adapter -- the concrete MarketDataSource implementation.

Every endpoint, parameter shape, response field, and quirk used below was
verified directly against the existing project's own working Bitget
integration (get_realtime_indicators, _fetch_candles_raw, get_single_ticker_price,
FUTURES_PRODUCT_TYPES) before being written here -- nothing about Bitget's
API shape is invented. Specifically verified:

  - Base URL and paths (spot candles, mix/futures candles, spot tickers,
    mix ticker) -- identical strings to the existing project.
  - Spot vs. futures use different granularity casing for the same
    timeframe (spot "1h"/"4h"/"1day", futures "1H"/"4H"/"1D") -- the exact
    existing project comment explaining why is preserved below.
  - Response shape: {"data": [[timestamp_ms, open, high, low, close,
    volume, ...], ...]}, newest-first (see normalizer.py's reversal step).
  - No authentication header is used anywhere for these endpoints in the
    existing project -- these are public market-data endpoints.
  - FUTURES_PRODUCT_TYPES and the "try each until one has data" fallback
    pattern, exact order preserved: usdt-futures, susdt-futures,
    usdc-futures, coin-futures.
  - Ticker price field fallback: `lastPr` first, then `last`.

What is NEW here, not present in the legacy code (confirmed by inspection --
grep for "retry"/"backoff" across analyzer.py returns nothing): retry logic
for transient failures. The legacy code makes exactly one attempt per
request and silently returns None/{}/[] on any exception. This adapter
instead retries a bounded number of times on timeouts and 5xx/429 responses,
and raises a specific, typed exception (never a generic Exception, never a
silent empty result) once retries are exhausted or a non-retryable failure
(4xx other than 429) occurs.

Phase 2 review fix, Issue 2: get_candles() returns a NormalizationResult
(candles + any normalization issues), not a bare candle list -- see
source.py's docstring for the full rationale. This adapter's job is now
strictly "fetch, and faithfully report what was parseable and what wasn't";
it no longer decides that a heavily-malformed response is unusable (that
judgment moved to quality.py, alongside every other data-quality
threshold). It still raises when the fetch itself produced nothing to work
with at all (empty response, network failure) -- that remains a source-level
failure, distinct from normalization quality.
"""
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from ..contracts import MarketType, Timeframe
from .exceptions import (
    DataNormalizationError,
    DataSourceError,
    DataSourceTimeoutError,
    DataSourceUnavailableError,
    InvalidSymbolOrTimeframeError,
)
from .models import NormalizationResult, TickerPrice, utc_now
from .normalizer import normalize_candles, normalize_ticker_price

BASE_URL = "https://api.bitget.com"
SPOT_CANDLES_URL = f"{BASE_URL}/api/v2/spot/market/candles"
MIX_CANDLES_URL = f"{BASE_URL}/api/v2/mix/market/candles"
SPOT_TICKER_URL = f"{BASE_URL}/api/v2/spot/market/tickers"
MIX_TICKER_URL = f"{BASE_URL}/api/v2/mix/market/ticker"

# Verified exact order from the existing project (FUTURES_PRODUCT_TYPES,
# analyzer.py line 43) -- usdt-futures first because it is the standard,
# most liquid contract type and should win ties.
FUTURES_PRODUCT_TYPES = ["usdt-futures", "susdt-futures", "usdc-futures", "coin-futures"]

# Verified exact casing from the existing project's get_realtime_indicators /
# _fetch_candles_raw. Bitget's Spot candles endpoint and Futures/Mix candles
# endpoint use DIFFERENT casing for the same granularity: Spot wants
# lowercase ("1h", "4h", "1day"), Futures/Mix wants uppercase ("1H", "4H",
# "1D"). Using the wrong case returns an empty response, not an error.
# Defined ONCE here (the legacy code defined this same mapping twice, in two
# different functions -- see the Phase 0/1 audit finding on that
# duplication). Limited to exactly the 9 Timeframe values Phase 1 defines
# (the legacy maps also had 3m/6h/12h, which are not in the canonical
# Timeframe enum and are therefore out of scope here).
TIMEFRAME_GRANULARITY_SPOT: Dict[Timeframe, str] = {
    Timeframe.M1: "1min", Timeframe.M5: "5min", Timeframe.M15: "15min", Timeframe.M30: "30min",
    Timeframe.H1: "1h", Timeframe.H2: "2h", Timeframe.H4: "4h",
    Timeframe.D1: "1day", Timeframe.W1: "1week",
}
TIMEFRAME_GRANULARITY_FUTURES: Dict[Timeframe, str] = {
    Timeframe.M1: "1m", Timeframe.M5: "5m", Timeframe.M15: "15m", Timeframe.M30: "30m",
    Timeframe.H1: "1H", Timeframe.H2: "2H", Timeframe.H4: "4H",
    Timeframe.D1: "1D", Timeframe.W1: "1W",
}

DEFAULT_TIMEOUT_SECONDS = 10.0
# Matches the most common timeout already used in the existing project's
# candle-fetching calls (10s in get_realtime_indicators; 15s in the backtest
# variant, 8s for the cheaper single-ticker call -- 10s is a reasonable
# single default for this adapter's candle calls).

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
# NEW in this phase (see module docstring) -- not present in the legacy
# code. Backoff is linear (attempt_number * base_backoff), which is simple,
# bounded, and sufficient for a 3-attempt policy; no library dependency
# needed for something this small.


class BitgetMarketDataSource:
    """Concrete MarketDataSource for one Bitget market (spot or futures).

    Implements smart_trade_analyzer.data.source.MarketDataSource. See that
    module for the interface contract this class must honor.
    """

    def __init__(
        self,
        market_type: MarketType,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        sleep_fn=time.sleep,
        log=lambda message: None,
    ):
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self.market_type = market_type
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep_fn
        self._log = log

    # -- MarketDataSource interface -----------------------------------

    def get_candles(
        self, symbol: str, timeframe: Timeframe, limit: int, as_of: Optional[datetime] = None,
    ) -> NormalizationResult:
        if as_of is None:
            as_of = utc_now()
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")

        if self.market_type == MarketType.FUTURES:
            granularity = TIMEFRAME_GRANULARITY_FUTURES.get(timeframe)
        else:
            granularity = TIMEFRAME_GRANULARITY_SPOT.get(timeframe)
        if granularity is None:
            raise InvalidSymbolOrTimeframeError(
                f"timeframe {timeframe!r} has no known Bitget granularity mapping "
                f"for market_type={self.market_type.value}"
            )

        # Fetch-level failure (no response to work with at all) still
        # raises -- this is distinct from normalization quality, see below.
        raw_rows = self._fetch_raw_candles(symbol, granularity, limit)
        if not raw_rows:
            raise DataSourceUnavailableError(
                f"no candle data returned for symbol={symbol!r} timeframe={timeframe.value} "
                f"market_type={self.market_type.value}"
            )

        # Review fix, Issue 2: once we HAVE raw rows to work with, whatever
        # normalize_candles() produces -- even if every single row turns out
        # malformed -- is returned as data (a NormalizationResult), not
        # raised as an exception. Grading "how bad is this" (a few malformed
        # rows -> recoverable; overwhelmingly malformed -> effectively
        # unusable) is the data-quality layer's job (quality.py), which
        # already owns every other quality threshold -- it should own this
        # one too, rather than the adapter pre-empting that judgment and
        # discarding the detail (exact count and reasons) an exception
        # message would lose. This adapter's only remaining responsibility
        # is to report faithfully what it fetched and what it could parse.
        result = normalize_candles(raw_rows, timeframe, as_of=as_of, reverse_input=True)
        if result.issues:
            self._log(
                f"{len(result.issues)} of {result.attempted_count} candle row(s) failed "
                f"normalization for {symbol} {timeframe.value}"
            )
        return result

    def get_ticker_price(self, symbol: str) -> Optional[TickerPrice]:
        try:
            raw = self._fetch_raw_ticker(symbol)
            if raw is None:
                return None
            return normalize_ticker_price(raw, source="bitget_ticker")
        except (DataSourceError, DataNormalizationError):
            return None

    # -- internal: raw fetch (retry + product-type fallback live here) -

    def _fetch_raw_candles(self, symbol: str, granularity: str, limit: int) -> List[Any]:
        if self.market_type == MarketType.FUTURES:
            last_error: Optional[DataSourceError] = None
            for product_type in FUTURES_PRODUCT_TYPES:
                try:
                    resp = self._get_with_retry(
                        MIX_CANDLES_URL,
                        {"symbol": symbol, "granularity": granularity, "limit": str(limit),
                         "productType": product_type},
                    )
                    data = _extract_data_list(resp, MIX_CANDLES_URL)
                    if data:
                        return data
                    last_error = None
                except DataSourceError as exc:
                    last_error = exc
                    continue
            if last_error is not None:
                raise last_error
            return []
        else:
            resp = self._get_with_retry(
                SPOT_CANDLES_URL, {"symbol": symbol, "granularity": granularity, "limit": str(limit)},
            )
            return _extract_data_list(resp, SPOT_CANDLES_URL)

    def _fetch_raw_ticker(self, symbol: str) -> Optional[dict]:
        if self.market_type == MarketType.FUTURES:
            for product_type in FUTURES_PRODUCT_TYPES:
                try:
                    resp = self._get_with_retry(
                        MIX_TICKER_URL, {"symbol": symbol, "productType": product_type},
                    )
                    data = _extract_data_list(resp, MIX_TICKER_URL)
                    if data:
                        return data[0]
                except DataSourceError:
                    continue
            return None
        else:
            resp = self._get_with_retry(SPOT_TICKER_URL, {"symbol": symbol})
            data = _extract_data_list(resp, SPOT_TICKER_URL)
            return data[0] if data else None

    def _get_with_retry(self, url: str, params: dict) -> requests.Response:
        """GET `url` with `params`, retrying on timeouts, connection errors,
        429, and 5xx up to self.max_attempts times (linear backoff). Raises
        immediately (no retry) on 4xx other than 429 -- retrying an invalid
        request cannot succeed. Never returns a fabricated response."""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout_seconds)
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    self._sleep(self.retry_backoff_seconds * attempt)
                    continue
                raise DataSourceTimeoutError(
                    f"timed out after {self.max_attempts} attempt(s) calling {url} "
                    f"with params={params}: {exc}"
                ) from exc
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    self._sleep(self.retry_backoff_seconds * attempt)
                    continue
                raise DataSourceUnavailableError(
                    f"request failed after {self.max_attempts} attempt(s) calling {url} "
                    f"with params={params}: {exc}"
                ) from exc

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_exc = None
                if attempt < self.max_attempts:
                    self._sleep(self.retry_backoff_seconds * attempt)
                    continue
                raise DataSourceUnavailableError(
                    f"HTTP {resp.status_code} from {url} after {self.max_attempts} attempt(s)"
                )
            if 400 <= resp.status_code < 500:
                raise InvalidSymbolOrTimeframeError(
                    f"HTTP {resp.status_code} from {url} with params={params} -- request "
                    f"rejected, not retrying"
                )
            return resp

        raise DataSourceUnavailableError(
            f"failed to fetch {url} after {self.max_attempts} attempt(s): {last_exc}"
        )


def _extract_data_list(resp: requests.Response, url: str) -> List[Any]:
    """Parse a Bitget-shaped {"data": [...]} response body. Raises
    DataSourceUnavailableError for anything that isn't that shape -- never
    returns a fabricated list."""
    try:
        payload = resp.json()
    except ValueError as exc:
        raise DataSourceUnavailableError(f"malformed (non-JSON) response from {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DataSourceUnavailableError(
            f"unexpected response shape from {url}: expected a JSON object, got {type(payload).__name__}"
        )
    data = payload.get("data")
    if data is None:
        raise DataSourceUnavailableError(f"response from {url} has no 'data' field")
    if not isinstance(data, list):
        raise DataSourceUnavailableError(
            f"response 'data' field from {url} is not a list (got {type(data).__name__})"
        )
    return data
