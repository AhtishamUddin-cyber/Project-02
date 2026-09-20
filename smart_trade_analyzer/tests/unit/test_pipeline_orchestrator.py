"""Unit tests for pipeline/orchestrator.py's own specific mechanics --
current_price resolution, evaluated_at defaulting, and account_balance/
risk_pct pass-through -- that aren't already pinned down by the realistic,
end-to-end scenarios in tests/integration/test_phase6_pipeline.py.
"""
from datetime import datetime, timedelta

from smart_trade_analyzer.contracts import CandleData, DataQuality, DataQualityState, MarketData, MarketType, Timeframe
from smart_trade_analyzer.pipeline import run_pipeline
from smart_trade_analyzer.pipeline.orchestrator import _resolve_current_price

NOW = datetime(2026, 8, 27, 12, 0, 0)


def _candles(n=60, base=100.0):
    return [
        CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=base, high=base + 0.3, low=base - 0.3,
                   close=base, volume=100.0, is_closed=True)
        for i in range(n)
    ]


def _md(candles, live_price):
    return MarketData(symbol="BTC", pair="BTCUSDT", market_type=MarketType.SPOT, timeframe=Timeframe.M1,
                       as_of=NOW, candles=candles, live_price=live_price, price_source="fake",
                       price_quality=DataQualityState.VALID)


def _dq(candles):
    return DataQuality(overall=DataQualityState.VALID, candle_count=len(candles), candle_count_required=200,
                        per_source={}, excluded_sources=[], reasons=[])


# ---------------------------------------------------------------------------
# _resolve_current_price: live_price preferred, then feature_set.close,
# then honestly None (never fabricated) -- see orchestrator.py's own
# docstring for why this ordering.
# ---------------------------------------------------------------------------

def test_resolve_current_price_prefers_live_price():
    candles = _candles()
    md = _md(candles, live_price=123.45)
    from smart_trade_analyzer.features import compute_feature_set
    fs = compute_feature_set(md)
    assert _resolve_current_price(md, fs) == 123.45


def test_resolve_current_price_falls_back_to_feature_set_close_without_live_price():
    candles = _candles(base=77.0)
    md = _md(candles, live_price=None)
    from smart_trade_analyzer.features import compute_feature_set
    fs = compute_feature_set(md)
    assert _resolve_current_price(md, fs) == fs.close == 77.0


def test_resolve_current_price_is_none_when_neither_is_available():
    md = _md([], live_price=None)
    assert _resolve_current_price(md, None) is None


def test_run_pipeline_current_price_override_takes_priority_over_live_price():
    candles = _candles()
    md = _md(candles, live_price=999.0)
    result = run_pipeline(md, _dq(candles), evaluated_at=NOW, current_price=42.0)
    assert result.current_price == 42.0  # explicit override wins over MarketData.live_price


# ---------------------------------------------------------------------------
# evaluated_at defaulting: when not supplied, run_pipeline reads the real
# current wall clock (utc_now()) rather than reusing market_data.as_of --
# these are two genuinely different instants in live use (Section 6).
# ---------------------------------------------------------------------------

def test_run_pipeline_defaults_evaluated_at_to_a_real_current_timestamp():
    candles = _candles()
    md = _md(candles, live_price=100.0)
    before = datetime.now()
    result = run_pipeline(md, _dq(candles))  # evaluated_at omitted
    after = datetime.now()
    # evaluated_at must be a real "now" read at call time -- bounded by
    # wall-clock timestamps taken immediately before/after the call, with
    # a little slack for naive-vs-aware/UTC-vs-local differences across
    # environments (this only needs to prove it's a live read, not that
    # it's precisely UTC-aligned to this assertion's own clock read).
    assert isinstance(result.evaluated_at, datetime)
    assert abs((result.evaluated_at - before).total_seconds()) < 3600 * 24  # sanity: not a stale/fixed placeholder from a different era
    assert result.signal_decision is not None  # never raises just because evaluated_at was omitted


def test_run_pipeline_respects_an_explicit_evaluated_at():
    candles = _candles()
    md = _md(candles, live_price=100.0)
    pinned = datetime(2020, 1, 1, 0, 0, 0)
    result = run_pipeline(md, _dq(candles), evaluated_at=pinned)
    assert result.evaluated_at == pinned


# ---------------------------------------------------------------------------
# account_balance / risk_pct pass-through to risk.build_risk_plan.
# ---------------------------------------------------------------------------

def test_run_pipeline_passes_account_balance_and_risk_pct_through_to_risk_plan():
    import random
    random.seed(2)
    closes = [100.0]
    for _ in range(209):
        closes.append(max(closes[-1] * (1 + 0.003 + random.uniform(-0.01, 0.01)), 0.01))
    n = len(closes)
    candles = [
        CandleData(open_time=NOW - timedelta(minutes=(n - i)), open=(closes[i - 1] if i > 0 else c),
                   high=max((closes[i - 1] if i > 0 else c), c) * 1.001,
                   low=min((closes[i - 1] if i > 0 else c), c) * 0.999,
                   close=c, volume=100.0, is_closed=True)
        for i, c in enumerate(closes)
    ]
    md = _md(candles, live_price=candles[-1].close)
    without = run_pipeline(md, _dq(candles), evaluated_at=NOW)
    with_account = run_pipeline(md, _dq(candles), evaluated_at=NOW, account_balance=10_000.0, risk_pct=0.01)
    assert without.risk_plan is not None and with_account.risk_plan is not None
    assert without.risk_plan.position_size is None  # no account data -- never fabricated
    assert with_account.risk_plan.position_size is not None  # now genuinely computable
