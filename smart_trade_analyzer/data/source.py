"""The market-data source interface.

The rest of the analyzer depends on this interface, never on bitget.py
directly -- so a future second exchange adapter (or a recorded-fixture
adapter for backtesting) can be substituted without any caller changing.

Design note on market_type: a source implementation is constructed for ONE
market type (spot or futures) -- see BitgetMarketDataSource in bitget.py.
Spot and futures are different data domains on Bitget (different endpoints,
different symbol behavior), so scoping it at construction time keeps the
per-call signature exactly `get_candles(symbol, timeframe, limit)` as
specified, rather than repeating market_type on every call.

Interface change (Phase 2 review fix, Issue 2): get_candles() now returns a
NormalizationResult instead of a bare List[CandleData]. Rationale: the
original design normalized inside the adapter and returned only the
successfully-parsed candles -- any row that failed to normalize was logged
by the adapter and then genuinely lost; by the time quality.py evaluated
DataQuality, it had no way to know some rows had been malformed at all, let
alone how many or why. That is exactly the "silently discard a corrupted
record" failure mode this whole project exists to prevent, just moved one
layer up. NormalizationResult (defined in models.py, already used
internally by normalizer.py) is reused here rather than inventing a new
type -- it is already the right, source-agnostic shape (candles + the
issues encountered producing them), and does not require the quality layer
to import anything Bitget-specific. The call signature -- what a caller
passes in -- is unchanged; only the return type is enriched. All call sites
and tests were updated accordingly.

No Streamlit import exists anywhere in this module, and none should ever be
added to it.
"""
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from ..contracts import Timeframe
from .models import NormalizationResult, TickerPrice


@runtime_checkable
class MarketDataSource(Protocol):
    """Structural interface for anything that can supply candles (and,
    optionally, a live ticker price) for one market (spot or futures).

    Implementations MUST NOT return fabricated data on failure -- they must
    raise one of the exceptions in exceptions.py instead. See bitget.py for
    the concrete Bitget implementation and exactly which failures map to
    which exception.
    """

    def get_candles(
        self, symbol: str, timeframe: Timeframe, limit: int, as_of: Optional[datetime] = None,
    ) -> NormalizationResult:
        """Return up to `limit` candles for `symbol` at `timeframe`, oldest
        first, already normalized into CandleData (including a correctly
        computed `is_closed` on each one) -- plus, explicitly, every raw row
        that could NOT be normalized and why (`.issues`). See this module's
        docstring for why the return type is NormalizationResult rather than
        a bare candle list: normalization problems must remain visible to
        the data-quality layer, not disappear once fetched.

        A raw row failing to normalize is NOT, by itself, a reason to raise
        -- `.candles` may legitimately be empty (or short) with `.issues`
        explaining why; that is data for the quality layer to grade, not an
        exceptional condition. Raising is reserved for the source failing to
        produce a response to work with AT ALL (network failure, timeout,
        empty/malformed top-level response) -- see exceptions.py.

        `as_of` lets a caller pin "now" for is_closed / determinism in tests;
        implementations must default to the real current time when omitted
        and must never call the wall clock anywhere except through this
        parameter's default.

        Raises a DataSourceError subclass when no response was obtainable at
        all. Never returns fabricated candles and never silently returns
        fewer candles than were actually available without that being
        either a legitimate result of the exchange's own history limit, or
        explained in `.issues`.
        """
        ...

    def get_ticker_price(self, symbol: str) -> Optional[TickerPrice]:
        """Return the current live price for `symbol`, or None if genuinely
        unavailable after the adapter's own retry policy is exhausted.

        Unlike get_candles, this returns None rather than raising, because a
        missing live price is expected to be a common, recoverable
        condition (callers typically have a fallback, e.g. the last closed
        candle's close) -- see quality.py for how this is combined into a
        DataQuality/MarketData.
        """
        ...
