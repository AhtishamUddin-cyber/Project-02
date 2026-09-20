"""Risk Engine orchestrator -- Section 11 of the approved specification.

Assembles the frozen RiskPlan contract from stop_loss.py, targets.py,
position_sizing.py, and leverage.py. This is the only module in `risk/`
that constructs a RiskPlan; the four sub-modules each do one job and stay
independently testable and importable on their own.

MIN_RR_HARD_GATE (1.2) is Section H item #8's proposed minimum TP1 R:R --
PROVISIONAL, not yet approved (flagged explicitly during this phase's
preflight: the frozen contracts/risk.py module docstring incorrectly
claims this was already approved at 1.5 under "decision #11" -- the
specification's actual, current Section H table lists it as item #8,
proposed at 1.2, not yet signed off; #11 in the current table is an
unrelated deployment-target decision. Treated as provisional here per the
current spec text, not the stale contract comment -- see HANDOFF.md).
`meets_min_rr` only gates TP1 (Section 11's own wording: "a hard gate...
on TP1"); `risk_reward_2` is still computed and returned for visibility,
never used as a gate condition itself.
"""
from typing import List, Optional

from ..contracts import Direction, RiskPlan
from .leverage import suggest_max_safe_leverage
from .position_sizing import compute_position_size
from .stop_loss import compute_stop_loss
from .targets import compute_targets

MIN_RR_HARD_GATE = 1.2  # PROVISIONAL -- Section H item #8, not yet approved


def build_risk_plan(
    direction: Direction,
    entry_reference: float,
    atr: float,
    swing_support: Optional[float],
    swing_resistance: Optional[float],
    account_balance: Optional[float] = None,
    risk_pct: Optional[float] = None,
) -> RiskPlan:
    """`entry_reference` is the single price risk math is measured from
    (risk_engine.py's callers -- see entry/entry_engine.py's
    reference_price -- decide what that is; this function is agnostic to
    where it came from). `account_balance`/`risk_pct` are optional
    account-level configuration with no home in any Phase 1-4 contract;
    when either is missing, `RiskPlan.position_size` is honestly left
    None rather than guessed at.
    """
    stop_loss = compute_stop_loss(direction, entry_reference, atr, swing_support, swing_resistance)
    targets = compute_targets(direction, entry_reference, atr, swing_support, swing_resistance)

    risk_distance = abs(entry_reference - stop_loss)
    risk_reward_1 = abs(targets.take_profit_1 - entry_reference) / risk_distance if risk_distance > 0 else 0.0
    risk_reward_2 = abs(targets.take_profit_2 - entry_reference) / risk_distance if risk_distance > 0 else 0.0
    meets_min_rr = risk_reward_1 >= MIN_RR_HARD_GATE

    position_size = None
    if account_balance is not None and risk_pct is not None:
        position_size = compute_position_size(account_balance, risk_pct, entry_reference, stop_loss)

    leverage = suggest_max_safe_leverage(entry_reference, stop_loss)

    warnings: List[str] = list(targets.warnings)
    if not meets_min_rr:
        warnings.append(
            f"TP1 R:R {risk_reward_1:.2f} is below the minimum hard gate of {MIN_RR_HARD_GATE} "
            f"(Section H item #8, provisional pending sign-off)"
        )
    warnings.append(
        f"Advisory comfort leverage {leverage.comfort_leverage:.1f}x "
        f"(hard safety ceiling {leverage.max_safe_leverage:.1f}x -- never exceed this)"
    )

    return RiskPlan(
        stop_loss=stop_loss,
        take_profit_1=targets.take_profit_1,
        take_profit_2=targets.take_profit_2,
        risk_reward_1=risk_reward_1,
        risk_reward_2=risk_reward_2,
        meets_min_rr=meets_min_rr,
        target_clearance_ok=targets.target_clearance_ok,
        position_size=position_size,
        max_safe_leverage=leverage.max_safe_leverage,
        warnings=warnings,
    )
