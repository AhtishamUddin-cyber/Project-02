"""Shared fixtures for the trade-tracking tests. NOT a test module.

* real_long_opportunity()/real_short_opportunity(): genuine OpportunityResults
  produced by the real scan_market()/scan_symbol()/pipeline (same seeded
  generators tests/unit/test_multi_scan.py already relies on) -- never
  hand-built stand-ins for the analyzer's own output.
* make_snapshot()/fresh_trade(): small explicit plans for engine tests, where
  exact price levels are the point.
* FakeTickerSource: a scriptable structural stand-in for MarketDataSource that
  implements only get_ticker_price() (the only thing tracking may call).
"""
from __future__ import annotations

import functools
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from smart_trade_analyzer.contracts import DataQualityState, Direction, MarketType, Timeframe
from smart_trade_analyzer.data.models import TickerPrice
from smart_trade_analyzer.scanner import scan_market, scan_symbol
from smart_trade_analyzer.tests.unit import test_multi_scan as _scan_fixtures
from smart_trade_analyzer.tracking import (
    PriceObservation, SqliteTradeRepository, TrackedTrade, TradeSnapshot, TrackingService, apply_observation,
    compute_expires_at, open_trade,
)

T0 = datetime(2026, 9, 1, 12, 0, 0)
LONG_LEVELS = dict(entry=100.0, stop_loss=98.0, take_profit_1=103.0, take_profit_2=106.0,
                   risk_reward_1=1.5, risk_reward_2=3.0, invalidation_price=97.0,
                   entry_zone_low=99.5, entry_zone_high=100.0)
SHORT_LEVELS = dict(entry=100.0, stop_loss=102.0, take_profit_1=97.0, take_profit_2=94.0,
                    risk_reward_1=1.5, risk_reward_2=3.0, invalidation_price=103.0,
                    entry_zone_low=100.0, entry_zone_high=100.5)


def make_snapshot(direction: Direction = Direction.LONG, **overrides) -> TradeSnapshot:
    levels = dict(LONG_LEVELS if direction is Direction.LONG else SHORT_LEVELS)
    values = dict(
        signal_id=f"sig-{direction.value.lower()}", symbol="BTC", pair="BTCUSDT", market=MarketType.SPOT,
        timeframe=Timeframe.M15, direction=direction, setup="TREND_CONTINUATION", quality_score=72.0,
        grade="B", confirmation_price=levels["entry"], signal_generated_at=T0 - timedelta(minutes=1),
        analysis_price=100.1, analysis_price_source="bitget_ticker", **levels,
    )
    values.update(overrides)
    if "confirmation_price" not in overrides and "entry" in overrides:
        values["confirmation_price"] = overrides["entry"]
    return TradeSnapshot(**values)


def fresh_trade(direction: Direction = Direction.LONG, tracking_price: float = 100.0, tracked_at: datetime = T0,
                ttl_candles: int = 48, trade_id: str = "trade-1", **snapshot_overrides) -> TrackedTrade:
    snapshot = make_snapshot(direction, **snapshot_overrides)
    return open_trade(snapshot, trade_id=trade_id, tracking_price=tracking_price, tracked_at=tracked_at,
                      expires_at=compute_expires_at(tracked_at, snapshot.timeframe, ttl_candles))


def tick(price: float, seconds_after_t0: float) -> PriceObservation:
    return PriceObservation.tick(price, T0 + timedelta(seconds=seconds_after_t0))


def run_ticks(trade: TrackedTrade, *steps) -> TrackedTrade:
    """Apply (price, seconds_after_t0) observations in order."""
    for price, seconds in steps:
        trade = apply_observation(trade, tick(price, seconds))
    return trade


# ---------------------------------------------------------------------------
# Real analyzer output.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _real(direction_value: str):
    if direction_value == "LONG":
        pair, base, candles = "BTCUSDT", "BTC", _scan_fixtures.long_candles()
    else:
        pair, base, candles = "ETHUSDT", "ETH", _scan_fixtures.short_candles()
    source = _scan_fixtures.FakeSource({pair: candles})
    scan = scan_market(source, [_scan_fixtures.instrument(pair, base)], MarketType.SPOT, Timeframe.M1,
                       as_of=_scan_fixtures.NOW, evaluated_at=_scan_fixtures.NOW, delay_between_symbols_seconds=0.0)
    return scan.results[0]


def real_long_opportunity():
    result = _real("LONG")
    assert result.decision.value == "LONG", "fixture drifted: seeded LONG scenario is no longer LONG"
    return result


def real_short_opportunity():
    result = _real("SHORT")
    assert result.decision.value == "SHORT", "fixture drifted: seeded SHORT scenario is no longer SHORT"
    return result


def real_no_trade_opportunity():
    """A genuine non-actionable result: the real pipeline on a symbol with no data."""
    source = _scan_fixtures.FakeSource({})
    result = scan_symbol(source, "NODATA", "NODATAUSDT", MarketType.SPOT, Timeframe.M1,
                         as_of=_scan_fixtures.NOW, evaluated_at=_scan_fixtures.NOW)
    assert result.decision.value in ("NO_TRADE", "WAIT")
    return result


# ---------------------------------------------------------------------------
# Scriptable ticker source + service wiring.
# ---------------------------------------------------------------------------

class FakeTickerSource:
    """Implements ONLY get_ticker_price(): tracking must never need more.
    `prices[pair]` is the next price (None/missing == unavailable);
    `raise_for` makes specific pairs raise; every call is recorded."""

    def __init__(self, prices: Optional[Dict[str, Optional[float]]] = None, now: datetime = T0):
        self.prices: Dict[str, Optional[float]] = dict(prices or {})
        self.raise_for = set()
        self.now = now
        self.calls: List[str] = []

    def get_ticker_price(self, symbol: str):
        self.calls.append(symbol)
        if symbol in self.raise_for:
            raise RuntimeError(f"simulated ticker failure for {symbol}")
        price = self.prices.get(symbol)
        if price is None:
            return None
        return TickerPrice(price=price, source="fake_ticker", fetched_at=self.now, quality=DataQualityState.VALID)


def make_service(tmp_path, source: FakeTickerSource, **kwargs) -> TrackingService:
    repository = SqliteTradeRepository(tmp_path / "trade_history.sqlite3")
    kwargs.setdefault("sleep_fn", lambda seconds: None)
    return TrackingService(repository, lambda market: source, now_fn=lambda: source.now, **kwargs)
