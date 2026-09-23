"""Pure symbol search/filter logic for the searchable symbol selector.
No Streamlit import, no network access -- operates entirely on an
already-fetched List[Instrument] (see data/discovery.py for how that
list is obtained).
"""
from typing import List, Optional

from ..data import Instrument

DEFAULT_SYMBOL = "BTCUSDT"


def search_instruments(instruments: List[Instrument], query: str, limit: Optional[int] = None) -> List[Instrument]:
    """Case-insensitive substring match against both the full symbol
    (e.g. "BTCUSDT") and the base coin (e.g. "BTC") -- "BTC" matches
    "BTCUSDT", "eth" matches "ETHUSDT", etc. An empty/whitespace-only
    query returns the input list unchanged (still respecting `limit`).
    A query that matches nothing returns an empty list -- never falls
    back to a default or a guessed match; the caller shows that
    honestly (Section: Symbol Selector UI).
    """
    normalized = query.strip().upper()
    if not normalized:
        matches = list(instruments)
    else:
        matches = [i for i in instruments if normalized in i.symbol or normalized in i.base_coin]
    return matches[:limit] if limit is not None else matches


def default_symbol_index(instruments: List[Instrument], default_symbol: str = DEFAULT_SYMBOL) -> int:
    """Index of `default_symbol` within `instruments` if present, else 0
    (the first available instrument) -- never an out-of-range index, and
    never assumes `default_symbol` exists (Section: Symbol Selector UI --
    "Keep BTCUSDT as a reasonable default IF AVAILABLE"). Returns 0 for
    an empty list too; callers must handle the empty-list case themselves
    (nothing to index into).
    """
    for position, instrument in enumerate(instruments):
        if instrument.symbol == default_symbol:
            return position
    return 0
