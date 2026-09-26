"""Current-price access for tracking and the Opportunity UI.

This module contains NO Bitget code: no URL, no HTTP, no parsing. A live
price is whatever the existing MarketDataSource.get_ticker_price() returns
(BitgetMarketDataSource in production), wrapped so that:

  * an exception or a None from the source both become "price unavailable"
    (None) -- never a guessed number;
  * the fetch time that source stamped on the price is preserved, so the UI
    can show how old it is. NOTE: that timestamp is when THIS APPLICATION
    received the price (data/normalizer.py stamps utc_now()), not the
    exchange's own trade time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from ..data.models import TickerPrice
from ..data.source import MarketDataSource
from .constants import PRICE_AGING_MAX_AGE_SECONDS, PRICE_FRESH_MAX_AGE_SECONDS

logger = logging.getLogger("trade_tracking")


@dataclass(frozen=True)
class CurrentPrice:
    price: float
    fetched_at: datetime
    source: str

    @classmethod
    def from_ticker(cls, ticker: TickerPrice) -> "CurrentPrice":
        return cls(price=ticker.price, fetched_at=ticker.fetched_at, source=ticker.source)


class PriceFreshness(str, Enum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


def fetch_current_price(source: MarketDataSource, pair: str) -> Optional[CurrentPrice]:
    """The latest ticker price from `source`, or None if it cannot be had."""
    try:
        ticker = source.get_ticker_price(pair)
    except Exception:
        logger.exception("ticker fetch raised for pair=%s", pair)
        return None
    if ticker is None:
        return None
    return CurrentPrice.from_ticker(ticker)


def price_age_seconds(fetched_at: Optional[datetime], now: datetime) -> Optional[float]:
    if fetched_at is None:
        return None
    return max((now - fetched_at).total_seconds(), 0.0)


def classify_freshness(fetched_at: Optional[datetime], now: datetime) -> PriceFreshness:
    """DISPLAY-ONLY label for how old a price is. Never affects any outcome."""
    age = price_age_seconds(fetched_at, now)
    if age is None:
        return PriceFreshness.UNAVAILABLE
    if age <= PRICE_FRESH_MAX_AGE_SECONDS:
        return PriceFreshness.FRESH
    if age <= PRICE_AGING_MAX_AGE_SECONDS:
        return PriceFreshness.AGING
    return PriceFreshness.STALE
