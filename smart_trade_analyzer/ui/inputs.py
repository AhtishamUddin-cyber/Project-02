"""Pure input-normalization helpers for the Opportunity UI -- turning
whatever the user typed into the (symbol, pair) pair scanner.scan_symbol
expects. No Streamlit import, no network access, fully unit-testable.
"""
from typing import Optional

# Ordered longest-suffix-first so e.g. "FDUSD" is tried before any shorter
# suffix that might also technically match. Deliberately a small, common
# set of quote currencies actually listed on Bitget -- this only affects
# the DISPLAY `symbol` (a cosmetic label; see contracts/market.py), never
# the `pair` string actually sent to the exchange, so getting an unusual
# quote currency "wrong" here has no functional effect on the analysis.
_KNOWN_QUOTE_SUFFIXES = ("FDUSD", "USDT", "USDC", "BUSD", "USD", "BTC", "ETH")


def normalize_pair(raw: str) -> str:
    """Whitespace-stripped, uppercased -- "harmless formatting" only
    (Section 4). Does not invent or guess at a different symbol; whatever
    the user typed (minus stray whitespace/case) is what gets sent.
    """
    return raw.strip().upper()


def derive_display_symbol(pair: str) -> str:
    """Best-effort base-asset label for display only, e.g. "BTCUSDT" ->
    "BTC". Falls back to the full pair string when no known quote
    currency suffix matches -- never guesses, never fabricates a symbol
    that wasn't in what the user typed.
    """
    for suffix in _KNOWN_QUOTE_SUFFIXES:
        if pair.endswith(suffix) and len(pair) > len(suffix):
            return pair[: -len(suffix)]
    return pair


def validate_pair_input(raw: Optional[str]) -> Optional[str]:
    """Returns an error message if `raw` is not a usable symbol input, or
    None if it looks usable (does NOT check it's a real, listed symbol --
    that is the backend's job, via a real DataQuality.UNAVAILABLE result,
    not a UI-side guess).
    """
    if raw is None or not raw.strip():
        return "Please enter a symbol."
    candidate = normalize_pair(raw)
    if not candidate.isalnum():
        return "Symbol should only contain letters and numbers (e.g. BTCUSDT)."
    if len(candidate) < 2:
        return "That symbol looks too short."
    return None
