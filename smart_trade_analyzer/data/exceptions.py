"""Exception hierarchy for the data source / data quality layer.

Design intent (see module docstrings in bitget.py, normalizer.py, and
validator.py for exactly where each of these is raised vs. where an anomaly
is instead captured as data -- e.g. as a DataQuality of UNAVAILABLE -- rather
than thrown):

    DataLayerError                      (catch-all for "anything from data/")
    +-- DataSourceError                 (adapter/network-layer failures)
    |   +-- DataSourceTimeoutError      (retryable)
    |   +-- DataSourceUnavailableError  (retries exhausted / malformed
    |   |                                transport-level response)
    |   +-- InvalidSymbolOrTimeframeError (non-retryable -- the request
    |                                      itself was rejected)
    +-- DataNormalizationError          (raw payload too malformed to
    |                                    normalize AT ALL -- not the same as
    |                                    "some rows were bad," which is
    |                                    captured as NormalizationIssue data,
    |                                    not raised)
    +-- DataValidationError             (caller-error guard for validator.py
    |                                    -- e.g. being asked to validate
    |                                    candles for a Timeframe with no
    |                                    known duration. Per-candle/
    |                                    per-sequence anomalies are reported
    |                                    as ValidationIssue data, not raised)
    +-- DataUnavailableError            (raised by the top-level
                                         fetch_canonical_market_data()
                                         orchestration when, after every
                                         retry and fallback, there is no
                                         usable data at all)

This hierarchy is intentionally small. Ordinary, expected data problems
(a few bad candles, duplicates, staleness, gaps, missing optional sources)
are NOT modeled as exceptions anywhere in this package -- they are modeled
as DataQuality / ValidationIssue / NormalizationIssue values, per the
approved specification's core rule: missing or unavailable data must never
silently become directional evidence, but it also must not crash the
pipeline. Exceptions here are reserved for situations where no meaningful
value can be produced at all.
"""


class DataLayerError(Exception):
    """Base class for every exception raised by smart_trade_analyzer.data."""


class DataSourceError(DataLayerError):
    """Raised by a MarketDataSource implementation when it cannot fulfil a
    request. Always carries enough context (in its message) to explain what
    was being requested and why it failed."""


class DataSourceTimeoutError(DataSourceError):
    """The request timed out. Retryable."""


class DataSourceUnavailableError(DataSourceError):
    """The source could not be reached, returned a transport-level error, or
    returned a response so malformed that no data could be extracted from
    it, even after retries. Retryable up to the adapter's own retry policy;
    once raised, retries have already been exhausted."""


class InvalidSymbolOrTimeframeError(DataSourceError):
    """The request itself was rejected as invalid (e.g. a symbol the
    exchange does not recognize). Not retryable -- retrying the same
    request will not change the outcome."""


class DataNormalizationError(DataLayerError):
    """Raised only when a raw payload is structurally unusable in its
    entirety (e.g. not a list of rows at all). A raw payload that IS a list
    of rows, some of which are individually malformed, does NOT raise this
    -- see normalizer.NormalizationResult.issues instead."""


class DataValidationError(DataLayerError):
    """Raised only for programmer-error-shaped inputs to the validator
    (e.g. an unrecognized Timeframe with no known duration). Anomalies
    found WITHIN a legitimately-shaped candle sequence (duplicates, gaps,
    staleness, ordering) are reported via ValidationResult, not raised."""


class DataUnavailableError(DataLayerError):
    """Raised by the top-level orchestration (quality.fetch_canonical_market_data)
    when, after retries and fallbacks, there is no usable market data at all
    for the request. Callers that want a non-raising, always-a-value
    interface should catch this and use it as their UNAVAILABLE signal --
    it is deliberately a DataLayerError so it can be caught alongside every
    other exception this package raises."""
