"""Risk plan contract -- see Section 11 of the approved specification.

Nothing in this module computes anything (design rule 2) -- but its shape
structurally supports approved decision #6 (TP distance must never be
determined by setup score): there is no field here that accepts a score as
input anywhere. `meets_min_rr` is a plain bool that a later risk_engine phase
must derive from price levels only, against the approved minimum TP1 R:R
hard gate of 1.5 (decision #11) -- that threshold is policy for the future
risk engine to apply, not a value stored on this shape.
"""
from dataclasses import dataclass
from typing import List, Optional

from ._serde import JSONSerializable


@dataclass(frozen=True)
class RiskPlan(JSONSerializable):
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_1: float
    risk_reward_2: float
    meets_min_rr: bool
    target_clearance_ok: bool
    position_size: Optional[float]
    max_safe_leverage: Optional[float]
    warnings: List[str]

    def __post_init__(self):
        for name, value in (
            ("stop_loss", self.stop_loss),
            ("take_profit_1", self.take_profit_1),
            ("take_profit_2", self.take_profit_2),
        ):
            if value <= 0:
                raise ValueError(f"RiskPlan.{name} must be positive, got {value}")
        if self.risk_reward_1 < 0 or self.risk_reward_2 < 0:
            raise ValueError(
                f"RiskPlan risk_reward values cannot be negative, got "
                f"risk_reward_1={self.risk_reward_1}, risk_reward_2={self.risk_reward_2}"
            )
        if self.position_size is not None and self.position_size <= 0:
            raise ValueError(
                f"RiskPlan.position_size must be positive if known, got {self.position_size}"
            )
        if self.max_safe_leverage is not None and self.max_safe_leverage <= 0:
            raise ValueError(
                f"RiskPlan.max_safe_leverage must be positive if known, "
                f"got {self.max_safe_leverage}"
            )
