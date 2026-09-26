"""The documented MVP rules, as user-facing text.

These strings are the single source for the "How outcomes are decided"
panel in the UI and are checked by tests against the engine's real behavior
(tests/unit/test_tracking_definitions.py), so the documentation cannot drift
from the rules silently.
"""
from __future__ import annotations

from typing import Dict, List

from .constants import TRADE_TTL_CANDLES
from .models import TradeStatus

STATUS_LABELS: Dict[TradeStatus, str] = {
    TradeStatus.OPEN: "Open",
    TradeStatus.TP1_HIT: "TP1 reached (still open)",
    TradeStatus.TP2_HIT: "TP2 hit",
    TradeStatus.STOP_LOSS_HIT: "Stop loss hit",
    TradeStatus.EXPIRED: "Expired",
    TradeStatus.INVALIDATED: "Invalidated",
}

STATUS_DEFINITIONS: Dict[TradeStatus, str] = {
    TradeStatus.OPEN: "Tracked and monitored. Entry is assumed filled at the frozen entry price. Unresolved.",
    TradeStatus.TP1_HIT: (
        "An observed price reached TP1. This is a milestone, not a result: the trade stays monitored "
        "until TP2, the stop, or expiry. Unresolved."
    ),
    TradeStatus.TP2_HIT: "An observed price reached TP2 (TP1 is credited as reached too). Final: WIN.",
    TradeStatus.STOP_LOSS_HIT: (
        "An observed price reached the stop loss, at any time, even after TP1 (partial exits and "
        "breakeven stops are not modeled). Final: LOSS, -1R."
    ),
    TradeStatus.EXPIRED: (
        f"Still unresolved {TRADE_TTL_CANDLES} candles of the trade's timeframe after tracking began. "
        "Final, but neither a win nor a loss, and it carries no R."
    ),
    TradeStatus.INVALIDATED: (
        "The plan was already void when tracking began: the live price at that moment was already beyond "
        "the stop, beyond the plan's invalidation level, or at/through TP1. Final, neither a win nor a "
        "loss; it was never a live trade."
    ),
}

OUTCOME_RULES: List[str] = [
    "Only actual Bitget ticker prices observed AFTER tracking began can change a trade. Nothing is ever "
    "inferred, back-filled or guessed, and the analyzer is never re-run to decide a result.",
    "The entry, stop and targets are frozen at the moment you press Track Trade and never change.",
    "Touching a level counts. LONG: stop at or below the stop loss, targets at or above TP1/TP2. "
    "SHORT: the mirror image.",
    "If one observation could imply both a stop and a target, the stop wins (conservative). If TP2 is "
    "reached, TP1 is credited as reached as well.",
    "WIN = TP2 hit, worth the plan's own R:R to TP2. LOSS = stop loss hit, worth -1R. TP1 alone is not a win.",
    "Entry is assumed filled at the frozen entry. No fees, slippage, partial fills or position "
    "management are modeled.",
    f"A trade expires {TRADE_TTL_CANDLES} candles of its own timeframe after tracking. This lifetime is a "
    "provisional, unvalidated setting.",
    "Tracking needs a live Bitget price. If none is available, nothing is saved.",
]

LIMITATIONS: List[str] = [
    "Prices are SAMPLES taken when a refresh runs (roughly, when this page is used). Anything the market "
    "did between two samples is invisible: a stop touched and recovered between refreshes cannot be seen, "
    "and a later target hit could then be recorded even though the stop came first.",
    "Each trade shows how many observations it has and its largest gap between observations, so this "
    "exposure is visible per trade. Fewer observations and larger gaps mean less reliable results.",
    "A price's timestamp is when this app received it, not the exchange's own trade time.",
]

STATISTICS_DISCLAIMER = (
    "These figures summarize the historical results of the signals you tracked, under the rules above. "
    "They are not a forecast: they say nothing about the chance that any future trade will win."
)
