"""Pure, dependency-free formatting helpers for the Opportunity UI.

Nothing here reads a backend contract or makes a trading judgment -- these
functions only turn already-computed values into display-ready strings.
Every function is total (handles None honestly) and side-effect-free, so
it is fully unit-testable without Streamlit itself.
"""
from datetime import datetime
from typing import Optional

NOT_AVAILABLE = "Not available"


def format_price(value: Optional[float]) -> Optional[str]:
    """Scale decimal precision to the value's own magnitude so small-value
    assets (e.g. a coin trading at 0.00001234) don't get rounded down to
    "0.00", and large-value assets (e.g. 105432.5) don't show meaningless
    extra decimals. Returns None (never a fabricated number) when value is
    None -- callers render NOT_AVAILABLE for that themselves, keeping this
    function a pure formatter rather than a display-policy decision.
    """
    if value is None:
        return None
    magnitude = abs(value)
    if magnitude >= 100:
        decimals = 2
    elif magnitude >= 1:
        decimals = 4
    elif magnitude >= 0.01:
        decimals = 6
    else:
        decimals = 8
    return f"{value:,.{decimals}f}"


def format_price_range(low: Optional[float], high: Optional[float]) -> Optional[str]:
    if low is None or high is None:
        return None
    return f"{format_price(low)} – {format_price(high)}"


def format_quality_score(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    return f"{score:.0f}"


def format_risk_reward(rr: Optional[float]) -> Optional[str]:
    if rr is None:
        return None
    return f"1 : {rr:.2f}"


def format_timestamp(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_label(value: Optional[object]) -> Optional[str]:
    """For any enum-like value with a `.value` attribute (Decision,
    Direction, SetupType, RegimeType, QualityGrade, DataQualityState,
    Timeframe, MarketType), or a plain string -- returns the underlying
    display string, or None if value itself is None. Never invents a
    label for a value that wasn't actually provided.
    """
    if value is None:
        return None
    return getattr(value, "value", value)


def title_case_label(value: Optional[str]) -> Optional[str]:
    """"TREND_CONTINUATION" -> "Trend Continuation". Purely cosmetic."""
    if value is None:
        return None
    return value.replace("_", " ").title()
