"""Streamlit-free display logic for the Trade Tracking UI.

Everything here turns already-decided domain objects into strings. Nothing
here decides a status, a level, a price or an outcome -- those belong to
smart_trade_analyzer.tracking. app.py only renders what this returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from ..tracking.definitions import STATUS_DEFINITIONS, STATUS_LABELS
from ..tracking.errors import (
    DuplicateActivePlanError, DuplicateSignalError, NotTrackableError, PriceUnavailableError, TrackingError,
)
from ..tracking.models import TrackedTrade, TradeStatus
from ..tracking.outcome import CLOSE_EXPIRED, CLOSE_STOP_LOSS, CLOSE_TP2, VOID_PREFIX
from ..tracking.pricing import PriceFreshness, classify_freshness, price_age_seconds
from ..tracking.service import RefreshReport, TrackResult
from ..tracking.statistics import TrackingStatistics
from .formatting import NOT_AVAILABLE, format_price, format_risk_reward, format_timestamp

FRESHNESS_LABELS: Dict[PriceFreshness, str] = {
    PriceFreshness.FRESH: "🟢 fresh",
    PriceFreshness.AGING: "🟡 aging",
    PriceFreshness.STALE: "🔴 stale",
    PriceFreshness.UNAVAILABLE: "⚪ no price",
}

_VOID_REASON_TEXT = {
    "STOP_LOSS": "the price was already beyond the stop loss",
    "INVALIDATION": "the price was already beyond the plan's invalidation level",
    "TP1": "the price was already at or through TP1",
}


def format_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return NOT_AVAILABLE
    seconds = max(float(seconds), 0.0)
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def describe_price_age(fetched_at: Optional[datetime], now: datetime) -> str:
    """e.g. '12 s ago · 🟢 fresh'. Display only."""
    freshness = classify_freshness(fetched_at, now)
    if fetched_at is None:
        return FRESHNESS_LABELS[freshness]
    return f"{format_age(price_age_seconds(fetched_at, now))} ago · {FRESHNESS_LABELS[freshness]}"


def format_r(value: Optional[float]) -> str:
    return NOT_AVAILABLE if value is None else f"{value:+.2f} R"


def status_label(status: TradeStatus) -> str:
    return STATUS_LABELS[status]


def close_reason_text(close_reason: Optional[str]) -> str:
    if close_reason is None:
        return "—"
    if close_reason == CLOSE_TP2:
        return "TP2 reached"
    if close_reason == CLOSE_STOP_LOSS:
        return "Stop loss reached"
    if close_reason == CLOSE_EXPIRED:
        return "Lifetime elapsed without a terminal price event"
    if close_reason.startswith(VOID_PREFIX):
        why = _VOID_REASON_TEXT.get(close_reason[len(VOID_PREFIX):], "the plan was already void")
        return f"Plan already void when tracking began: {why}"
    return close_reason


def describe_coverage(trade: TrackedTrade) -> str:
    gap = "no gap recorded yet" if trade.max_observation_gap_seconds is None \
        else f"largest gap {format_age(trade.max_observation_gap_seconds)}"
    return f"{trade.observation_count} observation(s) · {gap}"


# ---------------------------------------------------------------------------
# Statistics.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatMetric:
    label: str
    value: str
    help: str


def build_statistics_metrics(stats: TrackingStatistics) -> List[StatMetric]:
    win_rate = "—" if stats.win_rate is None else f"{stats.win_rate * 100:.1f}% ({stats.wins} of {stats.resolved})"
    average_r = "—" if stats.average_r is None else f"{stats.average_r:+.2f} R"
    return [
        StatMetric("Total tracked", str(stats.total_tracked), "Every trade you have tracked."),
        StatMetric("Open (incl. TP1 reached)", str(stats.open_trades),
                   f"Still monitored. {stats.open_tp1_reached} of them already reached TP1."),
        StatMetric("Wins (TP2 hit)", str(stats.wins), "Trades that reached TP2."),
        StatMetric("Losses (stop hit)", str(stats.losses), "Trades that hit the stop loss, including after TP1."),
        StatMetric("Expired / invalidated", f"{stats.expired} / {stats.invalidated}",
                   "Neither a win nor a loss; excluded from win rate and R."),
        StatMetric("Win rate (resolved trades)", win_rate,
                   "Wins divided by resolved trades (wins + losses) among the trades you tracked."),
        StatMetric("Total realized R", f"{stats.total_realized_r:+.2f} R",
                   "Sum of R over resolved trades: +R to TP2 for a win, -1R for a loss."),
        StatMetric("Average R (resolved trades)", average_r, "Total realized R divided by resolved trades."),
    ]


# ---------------------------------------------------------------------------
# Trade table + detail.
# ---------------------------------------------------------------------------

def short_id(trade_id: str) -> str:
    return trade_id[:8]


def _px(value: Optional[float]) -> str:
    return format_price(value) or NOT_AVAILABLE


def build_table_rows(trades: Sequence[TrackedTrade], now: datetime) -> List[Dict[str, str]]:
    rows = []
    for t in trades:
        s = t.snapshot
        if t.is_active:
            last = f"{_px(t.latest_price)} ({describe_price_age(t.latest_price_at, now)})"
        else:
            last = _px(t.latest_price)
        rows.append({
            "Trade": short_id(t.trade_id), "Pair": s.pair, "Market": s.market.value.title(),
            "TF": s.timeframe.value, "Side": s.direction.value, "Setup": s.setup,
            "Status": status_label(t.status), "Entry": _px(s.entry), "SL": _px(s.stop_loss),
            "TP1": _px(s.take_profit_1), "TP2": _px(s.take_profit_2), "Last price": last,
            "R": format_r(t.realized_r), "Tracked (UTC)": format_timestamp(t.tracked_at) or NOT_AVAILABLE,
            "Closed (UTC)": format_timestamp(t.closed_at) or "—", "Observations": describe_coverage(t),
        })
    return rows


def build_trade_detail(trade: TrackedTrade, now: datetime) -> List[Tuple[str, str]]:
    """(label, value) pairs for one trade's detail panel."""
    s = trade.snapshot
    zone = "—" if s.entry_zone_low is None else f"{_px(s.entry_zone_low)} – {_px(s.entry_zone_high)}"
    lines: List[Tuple[str, str]] = [
        ("Trade ID", trade.trade_id),
        ("Signal ID", s.signal_id),
        ("Instrument", f"{s.pair} · {s.market.value.title()} · {s.timeframe.value}"),
        ("Direction / setup", f"{s.direction.value} · {s.setup}"),
        ("Quality (analytical score, not a probability)", f"{s.quality_score:.0f} ({s.grade})"),
        ("Entry (frozen)", _px(s.entry)),
        ("Entry zone (frozen)", zone),
        ("Confirmation price (frozen)", _px(s.confirmation_price)),
        ("Invalidation (frozen)", _px(s.invalidation_price)),
        ("Stop loss (frozen)", _px(s.stop_loss)),
        ("TP1 (frozen)", f"{_px(s.take_profit_1)}  ({format_risk_reward(s.risk_reward_1)})"),
        ("TP2 (frozen)", f"{_px(s.take_profit_2)}  ({format_risk_reward(s.risk_reward_2)})"),
        ("Analysis price", f"{_px(s.analysis_price)}  (source: {s.analysis_price_source})"),
        ("Price when tracking began", _px(trade.tracking_price)),
        ("Signal generated (UTC)", format_timestamp(s.signal_generated_at) or NOT_AVAILABLE),
        ("Tracked (UTC)", format_timestamp(trade.tracked_at) or NOT_AVAILABLE),
        ("Expires (UTC)", format_timestamp(trade.expires_at) or NOT_AVAILABLE),
        ("Status", f"{status_label(trade.status)} — {STATUS_DEFINITIONS[trade.status]}"),
    ]
    if trade.tp1_hit_at is not None:
        lines.append(("TP1 reached", f"{format_timestamp(trade.tp1_hit_at)} at {_px(trade.tp1_hit_price)}"))
    if trade.tp2_hit_at is not None:
        lines.append(("TP2 reached", f"{format_timestamp(trade.tp2_hit_at)} at {_px(trade.tp2_hit_price)}"))
    if trade.sl_hit_at is not None:
        lines.append(("Stop loss reached", f"{format_timestamp(trade.sl_hit_at)} at {_px(trade.sl_hit_price)}"))
    if trade.closed_at is not None:
        lines.append(("Closed (UTC)", f"{format_timestamp(trade.closed_at)} — {close_reason_text(trade.close_reason)}"))
        lines.append(("Realized R", format_r(trade.realized_r)))
    latest = f"{_px(trade.latest_price)}  ({format_timestamp(trade.latest_price_at) or NOT_AVAILABLE}"
    latest += f", {describe_price_age(trade.latest_price_at, now)})" if trade.is_active else ")"
    lines.append(("Latest observed price", latest))
    lines.append(("Observation coverage", describe_coverage(trade)))
    return lines


# ---------------------------------------------------------------------------
# User-facing messages for tracking actions.
# ---------------------------------------------------------------------------

def describe_track_success(result: TrackResult) -> Tuple[str, str]:
    """(level, message) after a successful Track Trade click."""
    t = result.trade
    where = f"{t.snapshot.direction.value} {t.snapshot.pair} ({t.snapshot.timeframe.value})"
    if result.void_at_tracking:
        return ("warning",
                f"Tracked {where} as INVALIDATED (trade {short_id(t.trade_id)}): {close_reason_text(t.close_reason).lower()} "
                f"({_px(t.tracking_price)}). It was never a live trade, and counts as neither a win nor a loss.")
    return ("success",
            f"Tracking {where} — trade {short_id(t.trade_id)}. The plan is frozen; the live Bitget price when "
            f"tracking began was {_px(t.tracking_price)}. See the Trade Tracking tab.")


def describe_track_error(exc: TrackingError) -> Tuple[str, str]:
    """(level, message) for a failed Track Trade click."""
    if isinstance(exc, DuplicateSignalError):
        return ("info", "This exact signal is already being tracked — nothing was added.")
    if isinstance(exc, DuplicateActivePlanError):
        return ("info", "An identical trade plan is already being tracked — nothing was added.")
    if isinstance(exc, PriceUnavailableError):
        return ("error", f"{exc} Tracking needs a live Bitget price; please try again in a moment.")
    if isinstance(exc, NotTrackableError):
        return ("error", f"This result cannot be tracked: {exc.reason}")
    return ("error", "The trade could not be saved. Nothing was tracked.")


def describe_refresh_report(report: RefreshReport) -> List[Tuple[str, str]]:
    messages: List[Tuple[str, str]] = []
    if report.instruments_refreshed or report.trades_observed:
        messages.append(("success",
                         f"Refreshed {report.instruments_refreshed} instrument price(s); "
                         f"{report.trades_observed} open trade(s) evaluated."))
    for change in report.changes:
        messages.append(("info", f"{change.pair}: {status_label(change.from_status)} → {status_label(change.to_status)}"))
    if report.failed:
        names = ", ".join(pair for _, pair in report.failed)
        messages.append(("warning",
                         f"Could not get a live price for: {names}. Their previous prices were kept and are "
                         f"aging; no trade was changed because of the failure."))
    if report.not_refreshed:
        names = ", ".join(pair for _, pair in report.not_refreshed)
        messages.append(("warning",
                         f"Stopped early after repeated failures; not refreshed: {names}. Bitget may be "
                         f"unreachable — try again later."))
    if not messages:
        messages.append(("info", "No open trades to refresh."))
    return messages
