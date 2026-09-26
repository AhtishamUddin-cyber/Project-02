"""Domain models for trade tracking.

Streamlit-free, network-free and persistence-free: plain frozen dataclasses
plus the two enums that define the lifecycle vocabulary.

Vocabulary
----------
TradeStatus  -- the lifecycle state of a tracked trade (see outcome.py for
                the exact, deterministic transition rules).
TradeOutcome -- how a status is classified for statistics. It is a pure
                function of status (STATUS_TO_OUTCOME); it is persisted too,
                but only as a mirror that the database CHECKs for consistency.

Naming note: this package's stop-loss terminal status is STOP_LOSS_HIT. The
frozen Phase 1 contract (contracts/signal_record.py VALID_STATUSES) calls the
same thing SL_HIT. CONTRACT_STATUS below is the explicit, tested mapping, so
a future calibration phase that bridges to ShadowOutcome has one place to
look. Nothing in this package writes to, or subclasses, a frozen contract.

All datetimes are naive UTC, matching the rest of the project
(data/models.py utc_now()). Timezone-aware datetimes are rejected rather than
silently compared against naive ones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Tuple

from ..contracts import Direction, MarketType, Timeframe


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class TradeOutcome(str, Enum):
    OPEN = "OPEN"              # unresolved: status OPEN or TP1_HIT
    WIN = "WIN"                # TP2_HIT
    LOSS = "LOSS"              # STOP_LOSS_HIT
    EXPIRED = "EXPIRED"        # lifetime elapsed without a terminal price event
    INVALIDATED = "INVALIDATED"  # plan already void when tracking began


ACTIVE_STATUSES: Tuple[TradeStatus, ...] = (TradeStatus.OPEN, TradeStatus.TP1_HIT)
TERMINAL_STATUSES: Tuple[TradeStatus, ...] = (
    TradeStatus.TP2_HIT, TradeStatus.STOP_LOSS_HIT, TradeStatus.EXPIRED, TradeStatus.INVALIDATED,
)

STATUS_TO_OUTCOME: Dict[TradeStatus, TradeOutcome] = {
    TradeStatus.OPEN: TradeOutcome.OPEN,
    TradeStatus.TP1_HIT: TradeOutcome.OPEN,
    TradeStatus.TP2_HIT: TradeOutcome.WIN,
    TradeStatus.STOP_LOSS_HIT: TradeOutcome.LOSS,
    TradeStatus.EXPIRED: TradeOutcome.EXPIRED,
    TradeStatus.INVALIDATED: TradeOutcome.INVALIDATED,
}

# Mapping to the frozen contract vocabulary (contracts/signal_record.py
# VALID_STATUSES). Only STOP_LOSS_HIT differs by name.
CONTRACT_STATUS: Dict[TradeStatus, str] = {
    TradeStatus.OPEN: "OPEN",
    TradeStatus.TP1_HIT: "TP1_HIT",
    TradeStatus.TP2_HIT: "TP2_HIT",
    TradeStatus.STOP_LOSS_HIT: "SL_HIT",
    TradeStatus.EXPIRED: "EXPIRED",
    TradeStatus.INVALIDATED: "INVALIDATED",
}


def to_contract_status(status: TradeStatus) -> str:
    """The frozen contract's spelling of a tracking status."""
    return CONTRACT_STATUS[status]


# ---------------------------------------------------------------------------
# Validation helpers.
# ---------------------------------------------------------------------------

def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_price(name: str, value: Optional[float], *, optional: bool = False) -> None:
    if value is None:
        if optional:
            return
        raise ValueError(f"{name} is required")
    if not _is_number(value) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")


def _require_naive(name: str, value: Optional[datetime], *, optional: bool = False) -> None:
    if value is None:
        if optional:
            return
        raise ValueError(f"{name} is required")
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime, got {value!r}")
    if value.tzinfo is not None:
        raise ValueError(f"{name} must be a naive-UTC datetime (project convention), got tz-aware {value!r}")


def plan_geometry_problem(direction: Direction, entry: float, stop_loss: float,
                          tp1: float, tp2: float) -> Optional[str]:
    """Returns a human-readable problem if the frozen plan levels are not in
    the strictly increasing order a plan of this direction requires, else None.
    This is a consistency guard on already-final levels; it never changes them.
    """
    if direction is Direction.LONG:
        if not (stop_loss < entry < tp1 < tp2):
            return (f"LONG plan levels must satisfy stop_loss < entry < TP1 < TP2 "
                    f"(got SL={stop_loss}, entry={entry}, TP1={tp1}, TP2={tp2})")
        return None
    if direction is Direction.SHORT:
        if not (tp2 < tp1 < entry < stop_loss):
            return (f"SHORT plan levels must satisfy TP2 < TP1 < entry < stop_loss "
                    f"(got TP2={tp2}, TP1={tp1}, entry={entry}, SL={stop_loss})")
        return None
    return f"direction must be LONG or SHORT, got {direction!r}"


# ---------------------------------------------------------------------------
# The frozen analysis, captured at Track Trade time.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeSnapshot:
    """The exact analyzed plan, frozen at tracking time.

    Every price here is copied verbatim from the finalized OpportunityResult
    (never recomputed, never rounded). `pair` is the exchange symbol used for
    ticker calls (e.g. BTCUSDT); `symbol` is the base display symbol (BTC),
    mirroring OpportunityResult exactly.

    R semantics are inherited from the risk engine: R is measured from
    `entry` (== confirmation price) to `stop_loss`; risk_reward_1/2 are the
    engine's own TP1/TP2 multiples, copied as-is.
    """
    signal_id: str
    symbol: str
    pair: str
    market: MarketType
    timeframe: Timeframe
    direction: Direction
    setup: str
    quality_score: float
    grade: str
    entry: float
    confirmation_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_1: float
    risk_reward_2: float
    signal_generated_at: datetime
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    invalidation_price: Optional[float] = None
    # Bitget ticker price captured during the analysis fetch, or None if the
    # ticker was unavailable then. Never substituted with anything else.
    analysis_price: Optional[float] = None
    analysis_price_source: str = "unavailable"

    def __post_init__(self) -> None:
        for name in ("signal_id", "symbol", "pair", "setup", "grade", "analysis_price_source"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.market, MarketType):
            raise ValueError(f"market must be a MarketType, got {self.market!r}")
        if not isinstance(self.timeframe, Timeframe):
            raise ValueError(f"timeframe must be a Timeframe, got {self.timeframe!r}")
        if self.direction not in (Direction.LONG, Direction.SHORT):
            raise ValueError(f"direction must be LONG or SHORT, got {self.direction!r}")
        if not _is_number(self.quality_score) or not math.isfinite(self.quality_score):
            raise ValueError(f"quality_score must be a finite number, got {self.quality_score!r}")
        for name in ("entry", "confirmation_price", "stop_loss", "take_profit_1", "take_profit_2",
                     "risk_reward_1", "risk_reward_2"):
            _require_price(name, getattr(self, name))
        for name in ("entry_zone_low", "entry_zone_high", "invalidation_price", "analysis_price"):
            _require_price(name, getattr(self, name), optional=True)
        if (self.entry_zone_low is None) != (self.entry_zone_high is None):
            raise ValueError("entry_zone_low and entry_zone_high must be given together")
        if self.entry_zone_low is not None and self.entry_zone_low > self.entry_zone_high:
            raise ValueError("entry_zone_low must not exceed entry_zone_high")
        problem = plan_geometry_problem(self.direction, self.entry, self.stop_loss,
                                        self.take_profit_1, self.take_profit_2)
        if problem:
            raise ValueError(problem)
        _require_naive("signal_generated_at", self.signal_generated_at)

    @property
    def plan_fingerprint(self) -> Tuple:
        """Identity of the frozen plan, independent of when it was analyzed.
        Two snapshots with the same fingerprint describe the same trade idea.
        """
        return (self.pair, self.market.value, self.timeframe.value, self.direction.value, self.setup,
                self.entry, self.stop_loss, self.take_profit_1, self.take_profit_2)


# ---------------------------------------------------------------------------
# One observed Bitget price.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceObservation:
    """One observed market price.

    `price` is the last observed price. `low`/`high` bound the range the
    market is known to have traded through since the previous observation; for
    a ticker sample they equal `price` (a point sample -- it says nothing about
    what happened between two samples). The outcome engine reads low/high so a
    future range-bearing source (e.g. candle extremes) can plug in with no
    change to the rules; the MVP only ever feeds it ticker samples.
    """
    observed_at: datetime
    price: float
    low: Optional[float] = None
    high: Optional[float] = None
    source: str = "bitget_ticker"

    def __post_init__(self) -> None:
        _require_naive("observed_at", self.observed_at)
        _require_price("price", self.price)
        if self.low is None:
            object.__setattr__(self, "low", self.price)
        if self.high is None:
            object.__setattr__(self, "high", self.price)
        _require_price("low", self.low)
        _require_price("high", self.high)
        if not (self.low <= self.price <= self.high):
            raise ValueError(f"require low <= price <= high, got low={self.low}, price={self.price}, high={self.high}")

    @classmethod
    def tick(cls, price: float, observed_at: datetime, source: str = "bitget_ticker") -> "PriceObservation":
        return cls(observed_at=observed_at, price=price, source=source)


# ---------------------------------------------------------------------------
# A tracked trade: frozen snapshot + lifecycle state.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackedTrade:
    """A frozen TradeSnapshot plus the lifecycle state accumulated from
    observed prices. Instances are immutable; the outcome engine returns a new
    instance for every state change.
    """
    trade_id: str
    snapshot: TradeSnapshot
    tracking_price: float          # live Bitget price observed when the user clicked Track Trade
    tracked_at: datetime
    expires_at: datetime
    status: TradeStatus = TradeStatus.OPEN
    tp1_hit_at: Optional[datetime] = None
    tp1_hit_price: Optional[float] = None
    tp2_hit_at: Optional[datetime] = None
    tp2_hit_price: Optional[float] = None
    sl_hit_at: Optional[datetime] = None
    sl_hit_price: Optional[float] = None
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None
    realized_r: Optional[float] = None
    latest_price: Optional[float] = None
    latest_price_at: Optional[datetime] = None
    observation_count: int = 1     # includes the baseline observation taken at tracking
    max_observation_gap_seconds: Optional[float] = None

    # -- derived -----------------------------------------------------------

    @property
    def outcome(self) -> TradeOutcome:
        return STATUS_TO_OUTCOME[self.status]

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    # -- invariants --------------------------------------------------------

    def __post_init__(self) -> None:
        if not isinstance(self.trade_id, str) or not self.trade_id:
            raise ValueError("trade_id must be a non-empty string")
        if not isinstance(self.snapshot, TradeSnapshot):
            raise ValueError("snapshot must be a TradeSnapshot")
        if not isinstance(self.status, TradeStatus):
            raise ValueError(f"status must be a TradeStatus, got {self.status!r}")
        _require_price("tracking_price", self.tracking_price)
        _require_naive("tracked_at", self.tracked_at)
        _require_naive("expires_at", self.expires_at)
        if self.expires_at <= self.tracked_at:
            raise ValueError("expires_at must be after tracked_at")
        for name in ("tp1_hit_at", "tp2_hit_at", "sl_hit_at", "closed_at", "latest_price_at"):
            _require_naive(name, getattr(self, name), optional=True)
        for name in ("tp1_hit_price", "tp2_hit_price", "sl_hit_price", "latest_price"):
            _require_price(name, getattr(self, name), optional=True)
        for at_name, px_name in (("tp1_hit_at", "tp1_hit_price"), ("tp2_hit_at", "tp2_hit_price"),
                                 ("sl_hit_at", "sl_hit_price")):
            if (getattr(self, at_name) is None) != (getattr(self, px_name) is None):
                raise ValueError(f"{at_name} and {px_name} must be set together")
        if (self.latest_price is None) != (self.latest_price_at is None):
            raise ValueError("latest_price and latest_price_at must be set together")
        if not isinstance(self.observation_count, int) or isinstance(self.observation_count, bool) \
                or self.observation_count < 1:
            raise ValueError("observation_count must be an integer >= 1")
        if self.max_observation_gap_seconds is not None and (
                not _is_number(self.max_observation_gap_seconds)
                or not math.isfinite(self.max_observation_gap_seconds)
                or self.max_observation_gap_seconds < 0):
            raise ValueError("max_observation_gap_seconds must be a finite number >= 0")
        self._check_status_consistency()

    def _check_status_consistency(self) -> None:
        s = self.status
        if s in ACTIVE_STATUSES:
            if (self.closed_at is not None or self.close_reason is not None or self.realized_r is not None
                    or self.tp2_hit_at is not None or self.sl_hit_at is not None):
                raise ValueError(f"an active {s.value} trade cannot carry closing fields")
            if (s is TradeStatus.TP1_HIT) != (self.tp1_hit_at is not None):
                raise ValueError("TP1_HIT requires tp1_hit_at; OPEN forbids it")
            return
        if self.closed_at is None or not self.close_reason:
            raise ValueError(f"a {s.value} trade requires closed_at and close_reason")
        if s is TradeStatus.TP2_HIT:
            if self.tp1_hit_at is None or self.tp2_hit_at is None or self.sl_hit_at is not None:
                raise ValueError("TP2_HIT requires tp1_hit_at and tp2_hit_at (TP2 credits TP1) and no sl_hit_at")
            if self.realized_r is None:
                raise ValueError("TP2_HIT requires realized_r")
        elif s is TradeStatus.STOP_LOSS_HIT:
            if self.sl_hit_at is None or self.tp2_hit_at is not None:
                raise ValueError("STOP_LOSS_HIT requires sl_hit_at and forbids tp2_hit_at")
            if self.realized_r != -1.0:
                raise ValueError("STOP_LOSS_HIT requires realized_r == -1.0")
        else:  # EXPIRED / INVALIDATED
            if self.realized_r is not None or self.tp2_hit_at is not None or self.sl_hit_at is not None:
                raise ValueError(f"{s.value} carries no R and no TP2/SL hit")
            if s is TradeStatus.INVALIDATED and self.tp1_hit_at is not None:
                raise ValueError("INVALIDATED trades never reached TP1")
