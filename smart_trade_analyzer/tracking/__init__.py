"""Trade tracking: freeze an analyzed opportunity, follow it with actual
Bitget prices, persist its history, summarize the results.

Boundaries (see HANDOFF.md, "Trade Tracking"):
  * Consumes a finalized scanner.OpportunityResult; never produces analysis.
  * Never re-runs scan_symbol() and never recomputes Entry/SL/TP.
  * Prices come only from the existing MarketDataSource ticker.
  * No Streamlit import anywhere in this package.
  * No order execution, no auto-trading, no news/sentiment.
"""
from .constants import TRADE_TTL_CANDLES
from .errors import (
    DuplicateActivePlanError, DuplicateSignalError, DuplicateTradeError, NotTrackableError,
    PriceUnavailableError, RepositoryError, SchemaVersionError, TrackingError,
)
from .models import (
    ACTIVE_STATUSES, TERMINAL_STATUSES, PriceObservation, TrackedTrade, TradeOutcome, TradeSnapshot,
    TradeStatus, to_contract_status,
)
from .outcome import apply_expiry, apply_observation, baseline_void_reason, compute_expires_at, open_trade
from .pricing import CurrentPrice, PriceFreshness, classify_freshness, fetch_current_price, price_age_seconds
from .repository import SqliteTradeRepository, TradeRepository, default_db_path
from .service import RefreshReport, StatusChange, TrackResult, TrackingService, build_default_service
from .snapshot import build_trade_snapshot, is_trackable, trackability_problem
from .statistics import TrackingStatistics, compute_statistics

__all__ = [
    "ACTIVE_STATUSES", "CurrentPrice", "DuplicateActivePlanError", "DuplicateSignalError", "DuplicateTradeError",
    "NotTrackableError", "PriceFreshness", "PriceObservation", "PriceUnavailableError", "RefreshReport",
    "RepositoryError", "SchemaVersionError", "SqliteTradeRepository", "StatusChange", "TERMINAL_STATUSES",
    "TRADE_TTL_CANDLES", "TrackResult", "TrackedTrade", "TrackingError", "TrackingService",
    "TrackingStatistics", "TradeOutcome", "TradeRepository", "TradeSnapshot", "TradeStatus",
    "apply_expiry", "apply_observation", "baseline_void_reason", "build_default_service",
    "build_trade_snapshot", "classify_freshness", "compute_expires_at", "compute_statistics",
    "default_db_path", "fetch_current_price", "is_trackable", "open_trade", "price_age_seconds",
    "to_contract_status", "trackability_problem",
]
