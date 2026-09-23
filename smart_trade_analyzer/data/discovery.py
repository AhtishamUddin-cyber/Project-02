"""Live instrument discovery -- the tradable-symbol-universe layer built
on top of MarketDataSource.list_instruments().

Deliberately separate from bitget.py's adapter method, matching this
project's established split between "faithfully report everything the
adapter found" (list_instruments() itself -- every instrument Bitget
lists for that market, tradable or not, same "never silently discard"
discipline as get_candles()/NormalizationResult) and "decide what's
usable for a specific purpose" (this module, for populating a symbol
selector or market scanner). No caching lives here -- see ui/ for the
Streamlit-level TTL cache (Section: Caching says instrument discovery
should not hit the endpoint on every UI rerun; that's a UI-layer
rerun concern, not a data-layer one). This module is a plain,
synchronous, fully-testable function with no Streamlit dependency.
"""
from typing import List

from .models import Instrument
from .source import MarketDataSource


def discover_tradable_instruments(source: MarketDataSource) -> List[Instrument]:
    """Every instrument `source` currently lists for its own market,
    filtered to exactly the ones Bitget's own metadata marks tradable
    right now (Instrument.tradable) -- never a hardcoded list, never an
    instrument this call didn't actually see reported as tradable.

    De-duplicated by symbol (keeping the first occurrence) and sorted
    alphabetically by symbol, purely for deterministic, predictable
    output -- this is presentation ordering, not a trading decision (see
    scanner/multi_scan.py for the separate, documented sort that DOES
    apply to scan results).

    "No Spot/Futures mixing" is structural, not something this function
    has to enforce: `source` is a single adapter instance already fixed
    to one market_type, so every Instrument returned here already shares
    that market_type -- callers that want both markets must call this
    twice, once per source, and must not merge the two lists into one
    undifferentiated list.

    Raises whatever source.list_instruments() raises (a DataSourceError
    subclass) on a genuine fetch failure -- never swallowed here; the
    caller decides how to present that (Section: Error Handling -- "show
    a clear user-facing error", not silently returning an empty list).
    """
    instruments = source.list_instruments()
    seen = set()
    tradable: List[Instrument] = []
    for instrument in instruments:
        if not instrument.tradable:
            continue
        if instrument.symbol in seen:
            continue
        seen.add(instrument.symbol)
        tradable.append(instrument)
    return sorted(tradable, key=lambda i: i.symbol)
