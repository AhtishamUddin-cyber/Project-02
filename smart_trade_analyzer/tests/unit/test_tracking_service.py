"""TrackingService tests: the orchestration layer (track / refresh / stats)
against a scriptable FakeTickerSource and a real SqliteTradeRepository. No
Streamlit, no network. Uses REAL analyzer output (tracking_support) for the
track() flow, since that is what a real OpportunityResult looks like.
"""
from datetime import timedelta

import pytest

from smart_trade_analyzer.contracts import MarketType
from smart_trade_analyzer.tests.tracking_support import (
    T0, FakeTickerSource, fresh_trade, make_service, real_long_opportunity, real_no_trade_opportunity,
    real_short_opportunity,
)
from smart_trade_analyzer.tracking import (
    DuplicateActivePlanError, DuplicateSignalError, NotTrackableError, PriceUnavailableError, TradeStatus,
)


# -- track() ------------------------------------------------------------------

def test_track_a_real_long_result_saves_an_open_trade(tmp_path):
    opportunity = real_long_opportunity()
    source = FakeTickerSource({opportunity.pair: opportunity.signal_record.entry})
    service = make_service(tmp_path, source)

    result = service.track(opportunity)

    assert result.trade.status is TradeStatus.OPEN and not result.void_at_tracking
    assert result.trade.snapshot.signal_id == opportunity.signal_record.id
    assert result.trade.tracking_price == opportunity.signal_record.entry
    stored = service.find_tracked(opportunity.signal_record.id)
    assert stored == result.trade


def test_track_a_real_short_result_saves_an_open_trade(tmp_path):
    opportunity = real_short_opportunity()
    source = FakeTickerSource({opportunity.pair: opportunity.signal_record.entry})
    service = make_service(tmp_path, source)
    result = service.track(opportunity)
    assert result.trade.status is TradeStatus.OPEN


def test_track_uses_only_the_pairs_ticker_call(tmp_path):
    opportunity = real_long_opportunity()
    source = FakeTickerSource({opportunity.pair: 100.0})
    service = make_service(tmp_path, source)
    service.track(opportunity)
    assert source.calls == [opportunity.pair]


def test_track_a_price_already_beyond_the_stop_saves_an_invalidated_trade(tmp_path):
    opportunity = real_long_opportunity()
    below_stop = opportunity.signal_record.stop_loss - 1.0
    source = FakeTickerSource({opportunity.pair: below_stop})
    service = make_service(tmp_path, source)

    result = service.track(opportunity)

    assert result.void_at_tracking and result.trade.status is TradeStatus.INVALIDATED
    assert result.trade.tracking_price == below_stop


def test_track_without_a_live_price_fails_closed_and_saves_nothing(tmp_path):
    opportunity = real_long_opportunity()
    source = FakeTickerSource({})  # no price configured -> ticker returns None
    service = make_service(tmp_path, source)

    with pytest.raises(PriceUnavailableError):
        service.track(opportunity)

    assert service.list_trades() == []
    assert service.find_tracked(opportunity.signal_record.id) is None


def test_track_when_the_ticker_raises_also_fails_closed(tmp_path):
    opportunity = real_long_opportunity()
    source = FakeTickerSource({opportunity.pair: 100.0})
    source.raise_for.add(opportunity.pair)
    service = make_service(tmp_path, source)
    with pytest.raises(PriceUnavailableError):
        service.track(opportunity)
    assert service.list_trades() == []


def test_track_a_non_actionable_result_is_refused_before_any_price_fetch(tmp_path):
    opportunity = real_no_trade_opportunity()
    source = FakeTickerSource({})
    service = make_service(tmp_path, source)
    with pytest.raises(NotTrackableError):
        service.track(opportunity)
    assert source.calls == []  # refused on the plan itself; never even asked for a price
    assert service.list_trades() == []


def test_tracking_the_same_signal_twice_is_refused_and_nothing_new_is_saved(tmp_path):
    opportunity = real_long_opportunity()
    source = FakeTickerSource({opportunity.pair: 100.0})
    service = make_service(tmp_path, source)
    first = service.track(opportunity)
    with pytest.raises(DuplicateSignalError) as info:
        service.track(opportunity)
    assert info.value.existing == first.trade
    assert len(service.list_trades()) == 1


def test_tracking_an_identical_active_plan_from_a_second_scan_is_refused(tmp_path):
    """Re-scanning the same setup produces a NEW SignalRecord.id (new
    timestamp) but the SAME frozen plan; tracking it twice while the first
    is still active must be refused."""
    import dataclasses
    opportunity = real_long_opportunity()
    source = FakeTickerSource({opportunity.pair: opportunity.signal_record.entry})
    service = make_service(tmp_path, source)
    service.track(opportunity)

    rescanned = dataclasses.replace(
        opportunity, signal_record=dataclasses.replace(
            opportunity.signal_record, id=opportunity.signal_record.id + "-rescan",
            timestamp=opportunity.signal_record.timestamp + timedelta(minutes=15)))
    with pytest.raises(DuplicateActivePlanError):
        service.track(rescanned)
    assert len(service.list_trades()) == 1


def test_track_never_mutates_the_opportunity_result(tmp_path):
    import copy
    opportunity = real_long_opportunity()
    before = copy.deepcopy(opportunity)
    service = make_service(tmp_path, FakeTickerSource({opportunity.pair: 100.0}))
    service.track(opportunity)
    assert opportunity == before


# -- refresh_active() ----------------------------------------------------------

def test_refresh_advances_open_trades_with_the_latest_price(tmp_path):
    source = FakeTickerSource({}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source)
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1"))
    source.prices["BTCUSDT"] = 103.5  # reaches TP1

    report = service.refresh_active()

    assert report.instruments_refreshed == 1 and report.trades_observed == 1
    trade = service._repo.get("t1")
    assert trade.status is TradeStatus.TP1_HIT
    assert len(report.changes) == 1 and report.changes[0].to_status is TradeStatus.TP1_HIT


def test_refresh_makes_exactly_one_ticker_call_per_distinct_pair_market(tmp_path):
    """Several trades sharing a pair/market must not each trigger their own
    ticker request."""
    source = FakeTickerSource({}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source)
    for i in range(4):  # 4 distinct plans on the same pair/market (small offsets, clear of SL=98/TP1=103)
        entry = 100.0 + i * 0.1
        service._repo.add(fresh_trade(trade_id=f"t{i}", signal_id=f"s{i}", entry=entry, confirmation_price=entry))
    source.prices["BTCUSDT"] = 101.0

    report = service.refresh_active()

    assert source.calls == ["BTCUSDT"]  # one call, not four
    assert report.trades_observed == 4


def test_refresh_calls_each_distinct_pair_separately(tmp_path):
    source = FakeTickerSource({}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source)
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1"))
    service._repo.add(fresh_trade(trade_id="t2", signal_id="s2", symbol="ETH", pair="ETHUSDT"))
    source.prices.update({"BTCUSDT": 101.0, "ETHUSDT": 99.0})

    report = service.refresh_active()

    assert sorted(source.calls) == ["BTCUSDT", "ETHUSDT"] and report.instruments_refreshed == 2


def test_a_failed_ticker_fetch_keeps_the_previous_price_and_changes_no_trade(tmp_path):
    source = FakeTickerSource({}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source)
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1"))
    # no price configured for BTCUSDT -> ticker unavailable

    report = service.refresh_active()

    assert report.instruments_refreshed == 0 and "BTCUSDT" in [p for _, p in report.failed]
    trade = service._repo.get("t1")
    assert trade.status is TradeStatus.OPEN and trade.observation_count == 1  # baseline only, untouched


def test_refresh_stops_early_after_consecutive_failures_and_reports_it(tmp_path):
    source = FakeTickerSource({}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source, max_consecutive_failures=2)
    for i, pair in enumerate(("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")):
        service._repo.add(fresh_trade(trade_id=f"t{i}", signal_id=f"s{i}", symbol=pair[:3], pair=pair))
    # every ticker unavailable

    report = service.refresh_active()

    assert len(report.failed) == 2 and len(report.not_refreshed) == 2 and report.aborted_early


def test_refresh_expires_trades_past_their_lifetime_without_needing_a_price(tmp_path):
    source = FakeTickerSource({}, now=T0 + timedelta(hours=13))  # LONG default TTL is 12h on 15m
    service = make_service(tmp_path, source)
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1"))

    report = service.refresh_active()

    assert source.calls == []  # no price needed to expire
    assert report.expired == 1
    assert service._repo.get("t1").status is TradeStatus.EXPIRED


def test_refresh_with_no_active_trades_makes_no_ticker_calls(tmp_path):
    source = FakeTickerSource({}, now=T0)
    service = make_service(tmp_path, source)
    report = service.refresh_active()
    assert source.calls == [] and report.instruments_refreshed == 0 and report.trades_observed == 0


def test_refresh_never_touches_a_trade_that_is_already_closed(tmp_path):
    source = FakeTickerSource({"BTCUSDT": 106.0}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source)
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1"))
    service.refresh_active()  # closes it as TP2_HIT
    closed = service._repo.get("t1")
    assert closed.status is TradeStatus.TP2_HIT

    source.now = T0 + timedelta(minutes=5)
    source.prices["BTCUSDT"] = 50.0  # would be a stop hit if (wrongly) re-evaluated
    report2 = service.refresh_active()

    assert service._repo.get("t1") == closed  # byte-for-byte unchanged
    assert report2.trades_observed == 0


def test_refresh_pauses_between_distinct_instruments(tmp_path):
    calls = []
    source = FakeTickerSource({"BTCUSDT": 101.0, "ETHUSDT": 99.0}, now=T0 + timedelta(minutes=1))
    service = make_service(tmp_path, source, sleep_fn=lambda s: calls.append(s), delay_between_instruments_seconds=0.3)
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1"))
    service._repo.add(fresh_trade(trade_id="t2", signal_id="s2", symbol="ETH", pair="ETHUSDT"))
    service.refresh_active()
    assert calls == [0.3]  # one pause between the two instruments, none before the first


# -- statistics() ---------------------------------------------------------------

def test_statistics_reflect_only_actually_tracked_trades(tmp_path):
    service = make_service(tmp_path, FakeTickerSource({}))
    service._repo.add(fresh_trade(trade_id="t1", signal_id="s1", tracking_price=90.0))  # void at tracking
    stats = service.statistics()
    assert stats.total_tracked == 1 and stats.invalidated == 1 and stats.wins == 0 and stats.win_rate is None
