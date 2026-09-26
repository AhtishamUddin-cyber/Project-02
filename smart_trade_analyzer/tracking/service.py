"""Orchestration for trade tracking: track a result, refresh open trades,
report statistics. All decisions live in the pure modules it composes
(snapshot.py, outcome.py, statistics.py); this file only sequences them and
talks to the repository and the price source.

Track Trade
-----------
  1. Freeze the finalized OpportunityResult (snapshot.py). Nothing is
     recomputed; the result is never modified.
  2. Refuse duplicates: the same signal_id, or an identical plan that is
     still active. (The repository re-checks atomically at insert time.)
  3. Fetch ONE live Bitget price through the existing MarketDataSource. If
     none is available, FAIL CLOSED: nothing is saved.
  4. Start the trade (outcome.open_trade): OPEN, or INVALIDATED if the plan
     was already void at that price.

Refresh
-------
  Sequential, one ticker request per distinct (market, pair) no matter how
  many trades share it; a short pause between instruments; an early stop
  after several consecutive failures. A failed fetch changes NOTHING: the
  previous price stays, it simply ages (freshness is computed at display
  time). Trades already past their lifetime are expired without any price.
  Refresh runs only when asked to (a button), never implicitly.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..contracts import MarketType
from ..data.bitget import BitgetMarketDataSource
from ..data.models import utc_now
from ..data.source import MarketDataSource
from ..scanner.models import OpportunityResult
from .constants import (
    REFRESH_DELAY_BETWEEN_INSTRUMENTS_SECONDS, REFRESH_MAX_CONSECUTIVE_FAILURES, TRACKING_TICKER_MAX_ATTEMPTS,
    TRACKING_TICKER_TIMEOUT_SECONDS, TRADE_TTL_CANDLES,
)
from .errors import DuplicateActivePlanError, DuplicateSignalError, PriceUnavailableError
from .models import PriceObservation, TrackedTrade, TradeStatus
from .outcome import apply_expiry, apply_observation, compute_expires_at, open_trade
from .pricing import CurrentPrice, fetch_current_price
from .repository import SqliteTradeRepository, TradeRepository, default_db_path
from .snapshot import build_trade_snapshot
from .statistics import TrackingStatistics, compute_statistics

SourceFactory = Callable[[MarketType], MarketDataSource]


@dataclass(frozen=True)
class TrackResult:
    trade: TrackedTrade

    @property
    def void_at_tracking(self) -> bool:
        """True when the plan was already void at the tracking-time price."""
        return self.trade.status is TradeStatus.INVALIDATED


@dataclass(frozen=True)
class StatusChange:
    trade_id: str
    pair: str
    from_status: TradeStatus
    to_status: TradeStatus


@dataclass(frozen=True)
class RefreshReport:
    checked_at: datetime
    instruments_refreshed: int
    failed: Tuple[Tuple[str, str], ...]        # (market, pair) whose price could not be fetched
    not_refreshed: Tuple[Tuple[str, str], ...]  # skipped after too many consecutive failures
    trades_observed: int                        # trades that were given a new price observation
    expired: int                                # trades closed as EXPIRED during this refresh
    changes: Tuple[StatusChange, ...]
    aborted_early: bool


class TrackingService:
    def __init__(
        self,
        repository: TradeRepository,
        source_factory: SourceFactory,
        *,
        now_fn: Callable[[], datetime] = utc_now,
        sleep_fn: Callable[[float], None] = time.sleep,
        ttl_candles: int = TRADE_TTL_CANDLES,
        delay_between_instruments_seconds: float = REFRESH_DELAY_BETWEEN_INSTRUMENTS_SECONDS,
        max_consecutive_failures: int = REFRESH_MAX_CONSECUTIVE_FAILURES,
    ):
        if ttl_candles < 1:
            raise ValueError("ttl_candles must be >= 1")
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        self._repo = repository
        self._source_factory = source_factory
        self._now = now_fn
        self._sleep = sleep_fn
        self._ttl_candles = ttl_candles
        self._delay = delay_between_instruments_seconds
        self._max_failures = max_consecutive_failures
        self._sources: Dict[MarketType, MarketDataSource] = {}

    # -- price ---------------------------------------------------------------

    def _source(self, market: MarketType) -> MarketDataSource:
        if market not in self._sources:
            self._sources[market] = self._source_factory(market)
        return self._sources[market]

    def fetch_current_price(self, market: MarketType, pair: str) -> Optional[CurrentPrice]:
        """The latest Bitget ticker price for `pair` (existing source), or None."""
        return fetch_current_price(self._source(market), pair)

    # -- reads ---------------------------------------------------------------

    def find_tracked(self, signal_id: str) -> Optional[TrackedTrade]:
        return self._repo.find_by_signal_id(signal_id)

    def list_trades(self, statuses: Optional[Sequence[TradeStatus]] = None) -> List[TrackedTrade]:
        return self._repo.list_trades(statuses)

    def statistics(self) -> TrackingStatistics:
        return compute_statistics(self._repo.list_trades())

    # -- track ---------------------------------------------------------------

    def track(self, opportunity: OpportunityResult) -> TrackResult:
        """Track a finalized OpportunityResult. Raises NotTrackableError,
        DuplicateSignalError, DuplicateActivePlanError or PriceUnavailableError;
        in every failure case nothing is saved."""
        snapshot = build_trade_snapshot(opportunity)

        existing = self._repo.find_by_signal_id(snapshot.signal_id)
        if existing is not None:
            raise DuplicateSignalError("this signal is already tracked", existing)
        same_plan = self._repo.find_active_by_plan(snapshot)
        if same_plan is not None:
            raise DuplicateActivePlanError("an identical plan is already being tracked", same_plan)

        price = self.fetch_current_price(snapshot.market, snapshot.pair)
        if price is None:
            raise PriceUnavailableError(
                f"No live Bitget price could be obtained for {snapshot.pair}, so the trade was not tracked.")

        tracked_at = price.fetched_at
        trade = open_trade(
            snapshot, trade_id=uuid.uuid4().hex, tracking_price=price.price, tracked_at=tracked_at,
            expires_at=compute_expires_at(tracked_at, snapshot.timeframe, self._ttl_candles),
        )
        self._repo.add(trade)
        return TrackResult(trade)

    # -- refresh -------------------------------------------------------------

    def refresh_active(self) -> RefreshReport:
        now = self._now()
        changes: List[StatusChange] = []
        expired = 0
        groups: Dict[Tuple[MarketType, str], List[TrackedTrade]] = {}

        for trade in self._repo.list_active():
            if now > trade.expires_at:  # lifetime over: no price is needed, none is used
                after = self._repo.mutate(trade.trade_id, lambda t: apply_expiry(t, now))
                if after is not None and after.status is not trade.status:
                    expired += 1
                    changes.append(StatusChange(trade.trade_id, trade.snapshot.pair, trade.status, after.status))
                continue
            groups.setdefault((trade.snapshot.market, trade.snapshot.pair), []).append(trade)

        refreshed = 0
        observed = 0
        failed: List[Tuple[str, str]] = []
        skipped: List[Tuple[str, str]] = []
        consecutive_failures = 0
        fetched_any = False

        for (market, pair), trades in groups.items():
            if consecutive_failures >= self._max_failures:
                skipped.append((market.value, pair))
                continue
            if fetched_any and self._delay > 0:
                self._sleep(self._delay)
            fetched_any = True

            price = self.fetch_current_price(market, pair)
            if price is None:
                failed.append((market.value, pair))
                consecutive_failures += 1
                continue
            consecutive_failures = 0
            refreshed += 1
            observation = PriceObservation.tick(price.price, price.fetched_at, price.source)

            for trade in trades:
                after = self._repo.mutate(trade.trade_id, lambda t: apply_observation(t, observation))
                if after is None:
                    continue
                observed += 1
                if after.status is not trade.status:
                    changes.append(StatusChange(trade.trade_id, trade.snapshot.pair, trade.status, after.status))
                    if after.status is TradeStatus.EXPIRED:
                        expired += 1

        return RefreshReport(
            checked_at=now, instruments_refreshed=refreshed, failed=tuple(failed),
            not_refreshed=tuple(skipped), trades_observed=observed, expired=expired,
            changes=tuple(changes), aborted_early=bool(skipped),
        )


def build_default_service(db_path=None) -> TrackingService:
    """The production wiring: SQLite file + the existing BitgetMarketDataSource
    (only configured with a tighter timeout/retry budget -- no second Bitget
    implementation)."""
    repository = SqliteTradeRepository(db_path if db_path is not None else default_db_path())

    def factory(market_type: MarketType) -> MarketDataSource:
        return BitgetMarketDataSource(
            market_type, timeout_seconds=TRACKING_TICKER_TIMEOUT_SECONDS,
            max_attempts=TRACKING_TICKER_MAX_ATTEMPTS,
        )

    return TrackingService(repository, factory)
