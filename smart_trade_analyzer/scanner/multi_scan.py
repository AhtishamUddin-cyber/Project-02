"""Multi-coin market scanner -- orchestration only.

    Live symbol universe (data.discover_tradable_instruments)
        |
    symbol filtering (caller's responsibility -- see scan_market's own
        docstring: this module does not call discover_tradable_instruments
        itself, so a caller can filter/limit the universe however it
        needs to before handing it to scan_market)
        v
    scan_market() loops over instruments, calling the EXISTING
    scanner.engine.scan_symbol() once per symbol -- the same, single,
    authoritative analysis path Section: NON-NEGOTIABLE ARCHITECTURE RULE
    requires. Nothing in this file computes an indicator, a setup, a
    score, a Decision, or a LONG/SHORT direction -- it only calls
    scan_symbol() and collects what comes back.
        v
    sort_scan_results() / filter_actionable_only() /
    filter_by_min_quality_score() -- three small, separate, PRESENTATION-
    ONLY functions. Each takes an already-scanned List[OpportunityResult]
    (every Decision in it was already final, from quality_gate.evaluate(),
    by the time any of these run) and returns a reordered/narrowed list.
    None of them ever inspects a score to CHANGE a Decision -- only to
    decide whether/where an already-decided result appears in a list.
"""
import time
from datetime import datetime
from typing import Callable, List, Optional

from ..contracts import Decision, MarketType, Timeframe
from ..data import MarketDataSource, REQUIRED_CANDLES_FOR_FULL, Instrument
from .engine import scan_symbol
from .models import MarketScanResult, ScanFailure, OpportunityResult

DEFAULT_DELAY_BETWEEN_SYMBOLS_SECONDS = 0.15
# NEW in this phase. bitget.py's own retry/backoff already reacts to a
# 429/5xx once it happens (see BitgetMarketDataSource._get_with_retry);
# this is the scanner's own, separate, PROACTIVE spacing between symbols
# so a scan of many instruments does not fire a burst of near-simultaneous
# requests in the first place. Deliberately small and sequential rather
# than concurrent -- Section: Performance/API Safety explicitly accepts
# "a safe sequential or bounded approach", and true concurrency would be
# a real, not-strictly-necessary redesign of how this project talks to
# Bitget (Section: "Do not redesign the existing Bitget adapter unless
# absolutely necessary").


def scan_market(
    source: MarketDataSource,
    instruments: List[Instrument],
    market_type: MarketType,
    timeframe: Timeframe,
    limit: int = REQUIRED_CANDLES_FOR_FULL,
    as_of: Optional[datetime] = None,
    evaluated_at: Optional[datetime] = None,
    max_symbols: Optional[int] = None,
    delay_between_symbols_seconds: float = DEFAULT_DELAY_BETWEEN_SYMBOLS_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda message: None,
) -> MarketScanResult:
    """Scan every instrument in `instruments` (already discovered and
    filtered by the caller -- see module docstring) through the existing,
    single-symbol scan_symbol() path, sequentially, with a small fixed
    delay between requests. Never decides LONG/SHORT itself; every
    OpportunityResult.decision in the returned MarketScanResult is
    exactly what quality_gate.evaluate() (inside scan_symbol(), inside
    pipeline.run_pipeline()) produced for that symbol.

    A symbol whose scan raises an unexpected exception is recorded as a
    ScanFailure (symbol, pair, a concise error string) and the scan
    continues with the next symbol -- one bad symbol never aborts the
    whole scan, and a failure is never turned into a fabricated WAIT/
    NO_TRADE result (Section: Error Handling).

    Raises ValueError immediately, before scanning anything, if any
    instrument's own market_type does not match the `market_type`
    parameter -- mixing Spot and Futures instruments in one scan is a
    caller error this function refuses to paper over (Section: "Do not
    silently mix Spot and Futures instruments").

    max_symbols optionally caps how many instruments are actually
    scanned (still in the order `instruments` was given) -- useful for a
    UI that wants to bound how long a scan takes without changing which
    symbols are in the discovered universe. `requested_count` on the
    returned MarketScanResult is always len(instruments); `scanned_count`
    is how many were actually attempted after this cap.
    """
    for instrument in instruments:
        if instrument.market_type != market_type:
            raise ValueError(
                f"scan_market called with market_type={market_type.value} but instrument "
                f"{instrument.symbol!r} has market_type={instrument.market_type.value} -- "
                f"refusing to mix Spot and Futures instruments in one scan"
            )

    scoped = instruments if max_symbols is None else instruments[:max_symbols]

    results: List[OpportunityResult] = []
    failures: List[ScanFailure] = []
    for position, instrument in enumerate(scoped):
        if position > 0 and delay_between_symbols_seconds > 0:
            sleep_fn(delay_between_symbols_seconds)
        try:
            result = scan_symbol(
                source, symbol=instrument.base_coin, pair=instrument.symbol,
                market_type=market_type, timeframe=timeframe, limit=limit,
                as_of=as_of, evaluated_at=evaluated_at,
            )
            results.append(result)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one bad symbol must never abort the scan
            log(f"scan failed for {instrument.symbol}: {exc}")
            failures.append(ScanFailure(symbol=instrument.base_coin, pair=instrument.symbol, error=str(exc)))

    return MarketScanResult(
        market_type=market_type, timeframe=timeframe, results=results, failures=failures,
        requested_count=len(instruments), scanned_count=len(scoped),
    )


def sort_scan_results(results: List[OpportunityResult]) -> List[OpportunityResult]:
    """Presentation order only -- actionable (LONG/SHORT) results first,
    then WAIT/NO_TRADE; within each group, quality score descending
    (a result with no confluence/quality score at all, i.e. no setup was
    ever detected, sorts last within its group). Applied AFTER every
    Decision in `results` is already final -- this function cannot and
    does not change a single one. The first item in the returned list is
    not "the best trade" and carries no guarantee of superiority
    (Section: Sorting).
    """
    def sort_key(result: OpportunityResult):
        is_actionable = result.decision in (Decision.LONG, Decision.SHORT)
        score = result.confluence.setup_quality_score if result.confluence is not None else -1.0
        return (0 if is_actionable else 1, -score)

    return sorted(results, key=sort_key)


def filter_actionable_only(results: List[OpportunityResult]) -> List[OpportunityResult]:
    """Keep only LONG/SHORT results -- the scanner's recommended default
    view (Section: Scanner Result Rules). A pure display filter: WAIT/
    NO_TRADE results are simply left out of the returned list, never
    reinterpreted as anything else.
    """
    return [r for r in results if r.decision in (Decision.LONG, Decision.SHORT)]


def filter_by_min_quality_score(
    results: List[OpportunityResult], min_quality_score: Optional[float],
) -> List[OpportunityResult]:
    """Optional display filter narrowing already-decided results by
    quality score. `min_quality_score=None` (the default) preserves
    current project behavior exactly -- every scanned result is
    returned, unfiltered, matching Section: Multi-Coin Scanner's own
    instruction ("default should preserve current project behavior; do
    not invent a new trading threshold without documenting it"). This is
    NOT the Quality Gate's own G9 threshold (already applied, inside
    quality_gate.evaluate(), before this function ever runs) -- it is a
    strictly-narrower, optional, UI-driven display cut on top of results
    that already passed (or didn't) the real gate. A result with no
    confluence/quality score at all is excluded once any minimum is set,
    since there is no score to compare.
    """
    if min_quality_score is None:
        return list(results)
    return [
        r for r in results
        if r.confluence is not None and r.confluence.setup_quality_score >= min_quality_score
    ]
