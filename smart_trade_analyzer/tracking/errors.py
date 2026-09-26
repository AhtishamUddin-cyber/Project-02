"""Exception taxonomy for trade tracking.

Every failure a caller can reasonably act on has its own type, so the UI can
show an honest, specific message instead of a generic one.
"""
from __future__ import annotations

from typing import Any, Optional


class TrackingError(Exception):
    """Base class for every trade-tracking failure."""


class NotTrackableError(TrackingError, ValueError):
    """The result cannot be tracked (not LONG/SHORT, or no valid trade plan)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PriceUnavailableError(TrackingError):
    """No live Bitget price could be obtained, so nothing was tracked.

    Tracking fails closed: without a live price there is no evidence that the
    plan is still live, and no honest baseline observation to start from.
    """


class DuplicateTradeError(TrackingError):
    """The trade (or an identical active plan) is already tracked."""

    def __init__(self, message: str, existing: Optional[Any] = None):
        super().__init__(message)
        self.existing = existing


class DuplicateSignalError(DuplicateTradeError):
    """This exact signal (same SignalRecord.id) was already tracked."""


class DuplicateActivePlanError(DuplicateTradeError):
    """A different signal with an identical frozen plan is still active."""


class RepositoryError(TrackingError):
    """A persistence-level failure."""


class SchemaVersionError(RepositoryError):
    """The database file has a schema this code does not know how to use."""
