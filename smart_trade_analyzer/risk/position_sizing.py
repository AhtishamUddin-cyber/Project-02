"""Position sizing -- Section 11 of the approved specification.

The specification says to preserve `position_size()`'s existing,
audit-validated logic as-is (Section 19's table: "preserve `position_size()`
logic as-is (audit: preserve, no findings against it)"). The legacy
`analyzer.py` this refers to is not present in this environment (this
session, like Phase 4's swing-structure work, only has the approved
contracts/spec, not the original source tree) -- so, same as
`features/structure.py`'s module docstring already documents for the same
reason, this is a fresh implementation of the standard, described behavior
(fixed-fractional risk sizing: how much of the account to risk, divided by
how far away the stop is), not a literal port of code that could not
actually be inspected here.

CONVENTION, audited and fixed (Phase 5 audit-fix pass -- see HANDOFF.md):
`risk_pct` is a PERCENTAGE NUMBER, not a fraction -- pass 1.0 to mean
"risk 1% of the account", not 0.01. This matches the specification's own
naming (`risk_pct`, not `risk_fraction` or `risk_ratio`) and is the
convention this function's ORIGINAL, audited implementation used. The
first Phase 5 delivery of this file computed `account_balance * risk_pct`
directly (treating risk_pct as an already-divided fraction) -- a caller
passing the natural, everyday value `risk_pct=1.0` to mean "1 percent"
would then have had 100% of the account balance sized as risk on a single
trade. Fixed to `account_balance * (risk_pct / 100.0)`. See
test_risk.py's `TestPositionSizing` for the explicit regression proving
`risk_pct=1.0` means 1%, not 100%.

Deliberately does NOT read `setup_quality_score`, `QualityGrade`, or any
confidence/probability value -- "Position sizing should remain driven by
configured risk and SL distance" (Section 11). `account_balance` and
`risk_pct` are account-level configuration with no home in any Phase 1-4
contract (SetupCandidate/FeatureSet/ConfluenceResult carry no account
data) -- this function accepts them as plain parameters; risk_engine.py's
orchestrator treats both as optional and leaves `RiskPlan.position_size`
as None when they are not supplied, rather than fabricating a number from
data that was never actually available.
"""
from typing import Optional

# risk_pct above this is treated as a nonsensical input (you cannot risk
# more than 100% of the account on a single trade's stop-loss) rather than
# silently sizing an enormous position -- provisional, documented, same
# discipline as every other boundary constant in this codebase.
MAX_SANE_RISK_PCT = 100.0


def compute_position_size(
    account_balance: float,
    risk_pct: float,
    entry_reference: float,
    stop_loss: float,
) -> Optional[float]:
    """Fixed-fractional position sizing: risk exactly `risk_pct` PERCENT
    (e.g. 1.0 = 1%, 2.5 = 2.5% -- NOT a 0-1 fraction) of `account_balance`
    on this trade, sized so a stop-out loses that amount -- not more, not
    less. Returns position size in units of the underlying asset (e.g.
    BTC, not USD notional). Returns None if the inputs make sizing
    meaningless: zero/negative balance, non-positive or implausibly large
    (>100) risk_pct, or a zero stop distance -- an entry equal to its own
    stop is not a valid risk shape to size at all.
    """
    if account_balance <= 0 or risk_pct <= 0 or risk_pct > MAX_SANE_RISK_PCT:
        return None
    stop_distance = abs(entry_reference - stop_loss)
    if stop_distance <= 0:
        return None
    risk_amount = account_balance * (risk_pct / 100.0)
    return risk_amount / stop_distance
