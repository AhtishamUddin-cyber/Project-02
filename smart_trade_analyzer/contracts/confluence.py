"""Confluence contracts -- see Sections 7-8 of the approved specification.

EvidenceItem.direction MAY legitimately be Direction.NEUTRAL: a source can
genuinely, honestly report "no lean right now" (e.g. Fear & Greed sitting
exactly at the midpoint). That is real information and is different from a
source being UNAVAILABLE, which per approved decision #9 produces no
EvidenceItem at all -- see DataQuality's excluded_sources.

ConfluenceResult.proposed_direction may NOT be NEUTRAL: it is inherited
unchanged from the SetupCandidate that triggered scoring, and a candidate
always proposes a side.

setup_quality_score is explicitly a 0-100 score, not a probability (approved
decision #4) -- see decision.py / signal_record.py for where the one
legitimate probability field in this whole package lives, and how it is kept
distinct in both name and type.
"""
from dataclasses import dataclass
from typing import Dict, List

from .enums import Direction, EvidenceCategory
from ._serde import JSONSerializable


@dataclass(frozen=True)
class EvidenceItem(JSONSerializable):
    category: EvidenceCategory
    direction: Direction  # LONG, SHORT, or NEUTRAL -- all three are legitimate here
    strength: float        # 0.0-1.0, how strongly this single piece of evidence leans
    detail: str             # human-readable -- feeds SignalRecord.reasons/.warnings

    def __post_init__(self):
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(
                f"EvidenceItem.strength must be within [0.0, 1.0], got {self.strength}"
            )
        if not self.detail:
            raise ValueError("EvidenceItem.detail must be a non-empty, human-readable string")


@dataclass(frozen=True)
class ConfluenceResult(JSONSerializable):
    proposed_direction: Direction
    category_scores: Dict[EvidenceCategory, float]      # -1.0 .. +1.0 each, ALREADY
                                                           # netted within-category
    categories_available: List[EvidenceCategory]
    categories_excluded: List[EvidenceCategory]
    setup_quality_score: float                            # 0.0-100.0 -- NOT a probability
    evidence: List[EvidenceItem]
    conflicts: List[EvidenceCategory]                      # categories strongly against
                                                             # proposed_direction

    def __post_init__(self):
        if self.proposed_direction == Direction.NEUTRAL:
            raise ValueError(
                "ConfluenceResult.proposed_direction cannot be NEUTRAL -- it is "
                "inherited from a SetupCandidate, which always proposes a side"
            )
        if not (0.0 <= self.setup_quality_score <= 100.0):
            raise ValueError(
                f"ConfluenceResult.setup_quality_score must be within [0.0, 100.0], "
                f"got {self.setup_quality_score}"
            )
        for cat, score in self.category_scores.items():
            if not (-1.0 <= score <= 1.0):
                raise ValueError(
                    f"category_scores[{cat}] must be within [-1.0, 1.0], got {score}"
                )
        overlap = set(self.categories_available) & set(self.categories_excluded)
        if overlap:
            raise ValueError(
                f"Categories cannot be both available and excluded: {sorted(c.value for c in overlap)}"
            )
