"""End-to-end analytical pipeline orchestration -- Phase 6.

Coordinates the already-approved Phase 2-5 engines in the sequence the
approved architecture diagram specifies:

    MarketData -> FeatureEngine -> MarketRegime -> Setup detection
    -> Confluence -> Entry -> Risk -> Quality Gate

This module does no calculation of its own -- every number in a
PipelineResult was computed by the engine whose job that already is (each
already audited and approved in Phase 2-5). run_pipeline()'s only job is
sequencing: decide which stage runs next and what it's handed, never
recompute or second-guess what a stage already returned. "Do not move
calculations into the orchestrator merely for convenience" (Phase 6 brief,
Section 2) -- nothing here does arithmetic on price/indicator values.

Deliberately takes an ALREADY-FETCHED MarketData/DataQuality pair rather
than a MarketDataSource -- keeps this module a pure function of its
inputs (no I/O, no network calls), fully unit-testable with hand-built
fixtures exactly like tests/integration/test_phase5_pipeline.py already
does by hand. The data-fetching I/O boundary belongs to scanner/engine.py,
not here -- see that module's scan_symbol(), the only caller of this
function in production code.

The Quality Gate is always invoked exactly once per call, with whatever
stage outputs were actually reached (None for anything not reached). Per
quality_gate/gate.py's own module docstring, it is the sole authority for
Decision -- every terminal state in this orchestrator (data unavailable,
insufficient history, no setup, unconfirmed setup, or a fully-graded
setup) is expressed by what gets fed to evaluate(), never by a hand-rolled
Decision constructed here. G1 (data valid) and G2 (setup exists) already
cover the "data unavailable" / "nothing detected" cases correctly on
their own -- this module does not duplicate that logic; it only decides
what to (or not to) compute before handing it to the Gate, and short-
circuits computing further stages once an earlier one has nothing to hand
the next (mirroring scanner/engine.py's own established, approved
ordering from Phase 3 -- not a new invention).
"""
from datetime import datetime
from typing import List, Optional

from ..confluence import compute_confluence
from ..contracts import DataQuality, DataQualityState, MarketData, RegimeType
from ..data import utc_now
from ..entry import build_entry_plan, refresh_staleness
from ..features import closed_candles, compute_feature_set
from ..quality_gate import GateContext, evaluate
from ..regime import classify_regime
from ..risk import build_risk_plan
from ..setup import detect_setup
from .models import PipelineResult


def _resolve_current_price(market_data: MarketData, feature_set) -> Optional[float]:
    """Best available "current" price for the Gate's chase-distance check
    (G7) -- prefers the real live ticker price (MarketData.live_price)
    and falls back to the last CLOSED candle's close only when no live
    price is available, the same fallback the existing hand-built test
    fixtures already lean on (e.g. test_full_pipeline.py's build_md sets
    live_price=candles[-1].close). Returns None if neither exists yet
    (e.g. before a FeatureSet could even be computed) -- gate.py's G7
    already treats current_price=None permissively ("we don't know, so
    don't block on it"), so returning None here is an honest "unknown",
    never a fabricated number.
    """
    if market_data.live_price is not None:
        return market_data.live_price
    if feature_set is not None:
        return feature_set.close
    return None


def run_pipeline(
    market_data: MarketData,
    data_quality: DataQuality,
    evaluated_at: Optional[datetime] = None,
    current_price: Optional[float] = None,
    account_balance: Optional[float] = None,
    risk_pct: Optional[float] = None,
) -> PipelineResult:
    """Run every analytical stage for one already-fetched MarketData
    snapshot, in the approved order, and return everything computed along
    the way plus the Quality Gate's own SignalDecision. Never raises for
    ordinary analytical outcomes (no setup, no confirmation, data issues)
    -- matches fetch_canonical_market_data's and analyze_market's own
    "always a value" contract, extended through this phase's additional
    stages.

    evaluated_at: the real "now" the signal is being checked for
    freshness against (GateContext.evaluated_at / G10) -- distinct from
    market_data.as_of (when the data snapshot was pinned). Defaults to
    utc_now() when not supplied, i.e. "evaluate this as of the actual
    current moment", the honest default for a live scan. Callers that
    need deterministic, reproducible output (tests, replay) pass this
    explicitly -- exactly as tests/unit/test_quality_gate.py's own
    fixture builder already does (evaluated_at=NOW alongside as_of=NOW).
    Passing a value earlier than market_data.as_of is a caller error in
    the same sense passing a negative price would be; this function does
    not validate or correct that, it only threads it through to G10
    exactly as gate.py already expects.

    current_price: fed to the Gate's chase-distance check (G7). Defaults
    to _resolve_current_price()'s live-price-then-close fallback when not
    supplied.

    account_balance / risk_pct: optional pass-through to
    risk.build_risk_plan -- see risk/position_sizing.py's own docstring
    for why these stay optional (no Phase 1-4 contract carries account
    data, so RiskPlan.position_size stays None rather than fabricated
    when neither is supplied).
    """
    if evaluated_at is None:
        evaluated_at = utc_now()

    stage_reasons: List[str] = []

    feature_set = None
    regime = None
    setup = None
    confluence = None
    entry_plan = None
    risk_plan = None

    # Mirrors scanner/engine.py's approved Phase 3 short-circuit ordering
    # exactly (data unavailable -> insufficient history via no closed
    # candle -> insufficient history via regime UNKNOWN -> setup
    # detection onward) so behavior stays consistent between the two
    # scanner entry points. Skipping feature computation once data is
    # already known UNAVAILABLE avoids wasted work; it is not what makes
    # the final decision safe -- G1 below is what actually blocks a trade
    # on unavailable data, independently of this guard.
    if data_quality.overall != DataQualityState.UNAVAILABLE:
        feature_set = compute_feature_set(market_data)
        if feature_set is None:
            stage_reasons.append("no closed candle available -- cannot compute any feature")
        else:
            closed = closed_candles(market_data)
            regime = classify_regime(closed, feature_set)
            if regime.regime == RegimeType.UNKNOWN:
                stage_reasons.extend(regime.basis)
            else:
                setup = detect_setup(closed, feature_set, regime)
                if setup is None:
                    stage_reasons.append("no setup candidate detected")
                else:
                    confluence = compute_confluence(setup, feature_set, regime, closed)
                    entry_plan = build_entry_plan(setup, feature_set, closed)
                    if entry_plan is not None:
                        # Re-evaluate staleness against the actual moment
                        # of evaluation, not the moment of construction --
                        # build_entry_plan() itself always sets
                        # is_stale=False (see entry_engine.py: freshly
                        # generated at fs.as_of, before anyone has had a
                        # chance to check it against a later "now"). This
                        # is the integration this phase is explicitly
                        # asked to make (Section 6: "Integrate the
                        # existing Entry/Quality Gate freshness
                        # semantics") -- not a redesign of entry_engine.py
                        # itself, just calling the refresh function it
                        # already exposes for exactly this purpose.
                        entry_plan = refresh_staleness(entry_plan, evaluated_at)
                        risk_plan = build_risk_plan(
                            setup.direction, entry_plan.confirmation_price, feature_set.atr,
                            feature_set.swing_support, feature_set.swing_resistance,
                            account_balance=account_balance, risk_pct=risk_pct,
                        )

    if current_price is None:
        current_price = _resolve_current_price(market_data, feature_set)

    ctx = GateContext(
        data_quality=data_quality, as_of=market_data.as_of, timeframe=market_data.timeframe,
        setup=setup, confluence=confluence, entry_plan=entry_plan, risk_plan=risk_plan,
        evaluated_at=evaluated_at, current_price=current_price,
    )
    signal_decision = evaluate(ctx)

    return PipelineResult(
        market_data=market_data, data_quality=data_quality, evaluated_at=evaluated_at,
        current_price=current_price, feature_set=feature_set, regime=regime, setup=setup,
        confluence=confluence, entry_plan=entry_plan, risk_plan=risk_plan,
        signal_decision=signal_decision, stage_reasons=stage_reasons,
    )
