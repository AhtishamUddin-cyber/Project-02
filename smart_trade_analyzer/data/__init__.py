"""Data source and data quality foundation.

This package is the boundary between external market-data sources and the
rest of the analyzer. It NEVER decides LONG / SHORT / WAIT / NO_TRADE -- it
only fetches, normalizes, validates, and reports on the quality and
availability of market data (see quality.py's module docstring). Everything
downstream (the future Feature Engine and beyond) depends on the interface
and models exported here, not on Bitget-specific details -- see source.py
and bitget.py.

No Streamlit import exists anywhere in this package.
"""
from .exceptions import (
    DataLayerError,
    DataSourceError,
    DataSourceTimeoutError,
    DataSourceUnavailableError,
    InvalidSymbolOrTimeframeError,
    DataNormalizationError,
    DataValidationError,
    DataUnavailableError,
)
from .models import (
    TickerPrice,
    NormalizationIssue,
    NormalizationResult,
    ValidationIssue,
    ValidationResult,
    FreshnessResult,
    TIMEFRAME_DURATION_SECONDS,
    timeframe_duration_seconds,
    utc_now,
)
from .source import MarketDataSource
from .bitget import BitgetMarketDataSource
from .normalizer import normalize_candles, normalize_ticker_price
from .validator import validate_sequence, check_freshness
from .quality import (
    evaluate_candle_quality,
    evaluate_data_quality,
    fetch_canonical_market_data,
    closed_candles_only,
    REQUIRED_CANDLES_FOR_FULL,
    MIN_CANDLES_FOR_DEGRADED,
    MIN_USABLE_CANDLES,
)

__all__ = [
    # exceptions
    "DataLayerError",
    "DataSourceError",
    "DataSourceTimeoutError",
    "DataSourceUnavailableError",
    "InvalidSymbolOrTimeframeError",
    "DataNormalizationError",
    "DataValidationError",
    "DataUnavailableError",
    # models
    "TickerPrice",
    "NormalizationIssue",
    "NormalizationResult",
    "ValidationIssue",
    "ValidationResult",
    "FreshnessResult",
    "TIMEFRAME_DURATION_SECONDS",
    "timeframe_duration_seconds",
    "utc_now",
    # interface + adapter
    "MarketDataSource",
    "BitgetMarketDataSource",
    # normalization / validation
    "normalize_candles",
    "normalize_ticker_price",
    "validate_sequence",
    "check_freshness",
    # quality + orchestration
    "evaluate_candle_quality",
    "evaluate_data_quality",
    "fetch_canonical_market_data",
    "closed_candles_only",
    "REQUIRED_CANDLES_FOR_FULL",
    "MIN_CANDLES_FOR_DEGRADED",
    "MIN_USABLE_CANDLES",
]
