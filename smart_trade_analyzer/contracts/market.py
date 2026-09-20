"""Raw market data contracts: individual candles and a full market snapshot.

No API calls and no calculations live here (design rules 1-2) -- this module
only defines shapes that data_sources/ (a later phase) must populate.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .enums import DataQualityState, MarketType, Timeframe
from ._serde import JSONSerializable


@dataclass(frozen=True)
class CandleData(JSONSerializable):
    """A single OHLCV candle."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool  # False for the still-forming current candle -- excluded
                      # from indicator calculation wherever FeatureSet is built

    def __post_init__(self):
        if self.volume < 0:
            raise ValueError(f"CandleData.volume cannot be negative, got {self.volume}")
        if self.low > self.high:
            raise ValueError(
                f"CandleData.low ({self.low}) cannot exceed CandleData.high ({self.high})"
            )
        if not (self.low <= self.open <= self.high):
            raise ValueError(
                f"CandleData.open ({self.open}) must be within "
                f"[low, high] = [{self.low}, {self.high}]"
            )
        if not (self.low <= self.close <= self.high):
            raise ValueError(
                f"CandleData.close ({self.close}) must be within "
                f"[low, high] = [{self.low}, {self.high}]"
            )


@dataclass(frozen=True)
class MarketData(JSONSerializable):
    """A market data snapshot for one symbol/timeframe as of one instant.

    `as_of` is real "now" in LIVE mode and the historical instant in BACKTEST
    mode -- this field is what makes running the identical pipeline for live
    and backtest possible (approved decision #7); every other contract in
    this package is ultimately derived from data reachable through one of
    these snapshots.
    """

    symbol: str
    pair: str
    market_type: MarketType
    timeframe: Timeframe
    as_of: datetime
    candles: List[CandleData]
    live_price: Optional[float]
    price_source: str  # e.g. "bitget_ticker" | "coingecko" | "last_closed_candle"
                        # -- always explicit, never silently ambiguous (K-1)
    price_quality: DataQualityState
    price_change_24h_pct: Optional[float] = None
    # Informational only (approved decision #3). Deliberately NOT part of
    # FeatureSet and never read as evidence by confluence/ in a later phase --
    # kept here only so a signal card can still show it for human context.

    def __post_init__(self):
        if self.live_price is not None and self.live_price <= 0:
            raise ValueError(
                f"MarketData.live_price must be positive if known, got {self.live_price}"
            )
        if not self.symbol:
            raise ValueError("MarketData.symbol must be a non-empty string")
        if not self.pair:
            raise ValueError("MarketData.pair must be a non-empty string")
        if not self.price_source:
            raise ValueError("MarketData.price_source must be a non-empty string")
