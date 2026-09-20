"""The Opportunity Scanner: the single high-level entry point a future UI
calls, per the Phase 3 brief ("the UI must eventually be able to call ONE
high-level analysis function rather than knowing how RSI, MACD, regime, or
setup detection work internally").

    MarketData -> FeatureSet -> MarketRegime -> SetupCandidate(s) -> OpportunityScanResult

No entry/SL/TP/risk math -- deliberately out of scope this phase (see
scanner/models.py). No network calls of its own -- all fetching goes
through the injected MarketDataSource (Phase 2), exactly as Phase 2's own
architecture already requires.
"""
from datetime import datetime
from typing import Optional

from ..contracts import DataQualityState, Direction, MarketType, RegimeType, Timeframe
from ..data import MarketDataSource, fetch_canonical_market_data, REQUIRED_CANDLES_FOR_FULL
from ..features import closed_candles, compute_feature_set
from ..pipeline import run_pipeline
from ..regime import classify_regime
from ..setup import detect_setup
from ..signal_assembly import build_signal_record, compile_reasons_and_warnings
from .models import OpportunityResult, OpportunityScanResult, ScanStatus


def analyze_market(
    source: MarketDataSource,
    symbol: str,
    pair: str,
    market_type: MarketType,
    timeframe: Timeframe,
    limit: int = REQUIRED_CANDLES_FOR_FULL,
    as_of: Optional[datetime] = None,
) -> OpportunityScanResult:
    """Run the full pipeline for one symbol/timeframe and return an honest
    answer: is there a confirmed trade opportunity here, or not, and why.
    Never raises for ordinary data problems -- every branch below returns a
    value (matching fetch_canonical_market_data's own "always a value"
    contract, extended through this phase's additional stages).
    """
    market_data, data_quality = fetch_canonical_market_data(
        source, symbol, pair, market_type, timeframe, limit, as_of=as_of,
    )

    if data_quality.overall == DataQualityState.UNAVAILABLE:
        return OpportunityScanResult(
            status=ScanStatus.NO_DATA, symbol=symbol, pair=pair, market_type=market_type,
            timeframe=timeframe, reasons=list(data_quality.reasons),
            market_data=market_data, data_quality=data_quality,
        )

    feature_set = compute_feature_set(market_data)
    if feature_set is None:
        return OpportunityScanResult(
            status=ScanStatus.INSUFFICIENT_HISTORY, symbol=symbol, pair=pair, market_type=market_type,
            timeframe=timeframe, reasons=["no closed candle available -- cannot compute any feature"],
            market_data=market_data, data_quality=data_quality,
        )

    closed = closed_candles(market_data)
    regime = classify_regime(closed, feature_set)

    if regime.regime == RegimeType.UNKNOWN:
        return OpportunityScanResult(
            status=ScanStatus.INSUFFICIENT_HISTORY, symbol=symbol, pair=pair, market_type=market_type,
            timeframe=timeframe, reasons=list(regime.basis),
            market_data=market_data, data_quality=data_quality, feature_set=feature_set, regime=regime,
        )

    setup = detect_setup(closed, feature_set, regime)

    if setup is None or not setup.confirmation_met:
        reasons = ["no setup candidate detected"] if setup is None else (
            [f"{setup.setup_type.value} forming ({setup.direction.value}) but not yet confirmed"]
            + list(setup.failed_conditions)
        )
        return OpportunityScanResult(
            status=ScanStatus.NO_SETUP, symbol=symbol, pair=pair, market_type=market_type,
            timeframe=timeframe, reasons=reasons,
            market_data=market_data, data_quality=data_quality, feature_set=feature_set,
            regime=regime, setup=setup,
        )

    status = ScanStatus.SETUP_LONG if setup.direction == Direction.LONG else ScanStatus.SETUP_SHORT
    return OpportunityScanResult(
        status=status, symbol=symbol, pair=pair, market_type=market_type, timeframe=timeframe,
        reasons=list(setup.evidence_refs),
        market_data=market_data, data_quality=data_quality, feature_set=feature_set,
        regime=regime, setup=setup,
    )


def scan_symbol(
    source: MarketDataSource,
    symbol: str,
    pair: str,
    market_type: MarketType,
    timeframe: Timeframe,
    limit: int = REQUIRED_CANDLES_FOR_FULL,
    as_of: Optional[datetime] = None,
    evaluated_at: Optional[datetime] = None,
    current_price: Optional[float] = None,
    account_balance: Optional[float] = None,
    risk_pct: Optional[float] = None,
) -> OpportunityResult:
    """Phase 6's public entry point: the full analytical chain, symbol in,
    opportunity result out --

        symbol -> fetch_canonical_market_data (Phase 2)
                -> pipeline.run_pipeline (FeatureEngine -> MarketRegime ->
                   Setup -> Confluence -> Entry -> Risk -> Quality Gate)
                -> signal_assembly.build_signal_record
                -> OpportunityResult

    This function does no analysis of its own: fetching is the one I/O
    boundary here (unchanged in approach from analyze_market above), and
    everything past that is composing the two Phase 6 packages that
    actually do the work, never re-deriving what they already computed.
    `decision` on the returned OpportunityResult is always
    pipeline_result.signal_decision.decision, copied verbatim -- the
    Quality Gate is invoked exactly once, inside run_pipeline, and
    nothing here or downstream of it can override that (Phase 6 Section
    1's central rule).

    analyze_market() above is completely unmodified and remains available
    for any existing caller; this is an ADDITIONAL entry point, not a
    replacement -- see HANDOFF.md for why both coexist.
    """
    market_data, data_quality = fetch_canonical_market_data(
        source, symbol, pair, market_type, timeframe, limit, as_of=as_of,
    )

    pipeline_result = run_pipeline(
        market_data, data_quality, evaluated_at=evaluated_at, current_price=current_price,
        account_balance=account_balance, risk_pct=risk_pct,
    )

    signal_record = build_signal_record(pipeline_result)
    reasons, warnings = compile_reasons_and_warnings(pipeline_result)

    return OpportunityResult(
        decision=pipeline_result.signal_decision.decision,
        symbol=symbol, pair=pair, market_type=market_type, timeframe=timeframe,
        reasons=reasons, warnings=warnings,
        market_data=market_data, data_quality=data_quality,
        feature_set=pipeline_result.feature_set, regime=pipeline_result.regime,
        setup=pipeline_result.setup, confluence=pipeline_result.confluence,
        entry_plan=pipeline_result.entry_plan, risk_plan=pipeline_result.risk_plan,
        signal_decision=pipeline_result.signal_decision, signal_record=signal_record,
    )
