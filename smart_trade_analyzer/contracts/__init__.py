"""Typed, immutable data contracts for the Smart Trade Analyzer rewrite.

This package contains ONLY data shapes -- no API calls, no trading
calculations, no persistence, no Streamlit code (design rules 1-4 of the
approved foundation-layer specification). Every model here is:

  - an immutable (frozen) dataclass, validated at construction time for every
    invariant that's checkable without external data (ranges, enum
    consistency, and consistency between related fields on the same object),
  - safely serializable to/from JSON via .to_dict() / .from_dict() /
    .to_json() / .from_json(),
  - built from plain-stdlib types only (dataclasses, enum, datetime, typing)
    -- no third-party dependency is required to use this package.

Import everything from here, e.g.:

    from smart_trade_analyzer.contracts import SignalRecord, Decision, Direction

rather than reaching into individual submodules.
"""
from .enums import (
    MarketType,
    Timeframe,
    Direction,
    Decision,
    DataQualityState,
    RegimeType,
    SetupType,
    EvidenceCategory,
    QualityGrade,
)
from .market import CandleData, MarketData
from .quality import DataQuality
from .features import FeatureSet
from .regime import MarketRegime
from .setup import SetupCandidate
from .confluence import EvidenceItem, ConfluenceResult
from .entry import EntryPlan
from .risk import RiskPlan
from .decision import SignalDecision
from .signal_record import SignalRecord
from .shadow import ShadowOutcome

__all__ = [
    # enums
    "MarketType",
    "Timeframe",
    "Direction",
    "Decision",
    "DataQualityState",
    "RegimeType",
    "SetupType",
    "EvidenceCategory",
    "QualityGrade",
    # models
    "CandleData",
    "MarketData",
    "DataQuality",
    "FeatureSet",
    "MarketRegime",
    "SetupCandidate",
    "EvidenceItem",
    "ConfluenceResult",
    "EntryPlan",
    "RiskPlan",
    "SignalDecision",
    "SignalRecord",
    "ShadowOutcome",
]
