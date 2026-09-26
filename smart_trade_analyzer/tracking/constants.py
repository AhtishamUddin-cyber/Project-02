"""Centralized constants for trade tracking.

Every value below that is a judgment call rather than a fact about Bitget
is labeled PROVISIONAL / UNVALIDATED, following the project convention:
none of these numbers has been calibrated against data, they exist so the
behavior is explicit, documented, and easy to change in exactly one place.
"""

# ---------------------------------------------------------------------------
# Trade lifetime.
# ---------------------------------------------------------------------------

# PROVISIONAL / UNVALIDATED. A tracked trade that has not reached a terminal
# status (TP2_HIT / STOP_LOSS_HIT / INVALIDATED) EXPIRES this many candles of
# its own timeframe after tracked_at (e.g. 48 x 15m = 12 hours; 48 x 1h = 2
# days). Expiry is purely time-based: it is not a win, not a loss, and carries
# no R. Prices observed after expiry never resolve a trade.
TRADE_TTL_CANDLES = 48

# ---------------------------------------------------------------------------
# Price freshness -- DISPLAY ONLY. PROVISIONAL / UNVALIDATED.
# These thresholds only decide the label shown next to a price's age. They
# never influence any trade outcome.
# ---------------------------------------------------------------------------

PRICE_FRESH_MAX_AGE_SECONDS = 60.0
PRICE_AGING_MAX_AGE_SECONDS = 300.0

# ---------------------------------------------------------------------------
# Refresh strategy (sequential, one ticker request per distinct instrument).
# ---------------------------------------------------------------------------

# Pause between two consecutive instrument requests in one refresh, to stay
# polite to the public API. PROVISIONAL.
REFRESH_DELAY_BETWEEN_INSTRUMENTS_SECONDS = 0.15

# After this many consecutive instrument failures the refresh stops early and
# reports the remaining instruments as "not refreshed" instead of hanging for
# minutes on an unreachable exchange. No trade is modified by a failure.
REFRESH_MAX_CONSECUTIVE_FAILURES = 3

# Retry/timeout budget for the ticker source used by tracking flows. This only
# CONFIGURES the existing BitgetMarketDataSource -- it is not a second Bitget
# implementation. Kept tighter than the analysis default so an unreachable
# exchange fails a Track Trade click or a refresh quickly.
TRACKING_TICKER_TIMEOUT_SECONDS = 6.0
TRACKING_TICKER_MAX_ATTEMPTS = 2

# ---------------------------------------------------------------------------
# Persistence location.
# ---------------------------------------------------------------------------

DB_ENV_VAR = "SMART_TRADE_ANALYZER_DB"
DEFAULT_DB_FILENAME = "trade_history.sqlite3"

# Schema version stored in SQLite's PRAGMA user_version.
SCHEMA_VERSION = 1
