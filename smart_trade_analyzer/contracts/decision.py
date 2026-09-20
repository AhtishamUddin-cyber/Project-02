"""SignalDecision -- the Quality Gate's raw, gate-by-gate output.

See Section 12 of the approved specification. This is an intermediate value:
a later quality_gate/gate.py phase produces one of these, and a later
signal/signal_builder.py phase folds it into the final SignalRecord
(signal_record.py). The two are kept as distinct contracts on purpose --
see the "design decisions encountered" note in the accompanying summary for
the reasoning (in short: SignalDecision is the gate engine's raw,
independently-testable output type; SignalRecord is the fuller, persisted
record, and re-derives its own decision/direction fields directly rather
than embedding a nested SignalDecision, to satisfy the literal top-level
field list the approved spec requires on SignalRecord without duplication).

This module contains the most important safety check in the whole package:
Decision has no NEUTRAL member (see enums.py), and the validation below
additionally guarantees that whenever a Direction IS recorded alongside a
Decision, it can only ever be exactly the matching LONG/SHORT (for a
LONG/SHORT decision) or exactly None (for WAIT/NO_TRADE) -- Direction.NEUTRAL
is therefore rejected for every possible Decision value, by exhaustive case
matching rather than a special-cased denylist check.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from .enums import Decision, Direction, QualityGrade
from ._serde import JSONSerializable


@dataclass(frozen=True)
class SignalDecision(JSONSerializable):
    decision: Decision
    direction: Optional[Direction]
    quality_grade: QualityGrade
    gates: Dict[str, bool]   # every gate ID (Section 12, e.g. "G1_DATA_VALID") -> pass/fail
    reasons: List[str]
    warnings: List[str]

    def __post_init__(self):
        if not isinstance(self.decision, Decision):
            raise ValueError(
                f"SignalDecision.decision must be a Decision enum member, got {self.decision!r}"
            )
        if self.decision in (Decision.LONG, Decision.SHORT):
            expected = Direction.LONG if self.decision == Decision.LONG else Direction.SHORT
            if self.direction != expected:
                raise ValueError(
                    f"SignalDecision.decision={self.decision} requires "
                    f"direction={expected}, got {self.direction!r}"
                )
        else:  # WAIT or NO_TRADE
            if self.direction is not None:
                raise ValueError(
                    f"SignalDecision.decision={self.decision} must have "
                    f"direction=None (no lean is asserted), got {self.direction!r}"
                )
        if not self.gates:
            raise ValueError(
                "SignalDecision.gates cannot be empty -- a decision must record "
                "the outcome of at least one gate check"
            )
