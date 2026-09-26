"""The user-facing rule text in tracking/definitions.py is checked against
the REAL engine behavior, so documentation cannot silently drift from what
the code actually does."""
from smart_trade_analyzer.tests.tracking_support import fresh_trade, run_ticks
from smart_trade_analyzer.tracking import TradeStatus
from smart_trade_analyzer.tracking.definitions import (
    LIMITATIONS, OUTCOME_RULES, STATISTICS_DISCLAIMER, STATUS_DEFINITIONS, STATUS_LABELS,
)


def test_every_status_has_a_label_and_a_definition():
    assert set(STATUS_LABELS) == set(TradeStatus) == set(STATUS_DEFINITIONS)


def test_tp1_definition_says_it_is_not_a_result():
    text = STATUS_DEFINITIONS[TradeStatus.TP1_HIT].lower()
    assert "milestone" in text and "unresolved" in text


def test_tp2_definition_says_win():
    assert "win" in STATUS_DEFINITIONS[TradeStatus.TP2_HIT].lower()


def test_stop_loss_definition_says_loss_and_mentions_no_partial_exits():
    text = STATUS_DEFINITIONS[TradeStatus.STOP_LOSS_HIT].lower()
    assert "loss" in text and "partial" in text


def test_expired_and_invalidated_definitions_say_neither_win_nor_loss():
    for status in (TradeStatus.EXPIRED, TradeStatus.INVALIDATED):
        text = STATUS_DEFINITIONS[status].lower()
        assert "neither" in text and "win" in text and "loss" in text


def test_documented_ttl_matches_the_actual_default_used_by_the_engine():
    from smart_trade_analyzer.tracking import TRADE_TTL_CANDLES
    assert any(str(TRADE_TTL_CANDLES) in rule for rule in OUTCOME_RULES)
    trade = fresh_trade()
    from datetime import timedelta
    assert trade.expires_at - trade.tracked_at == timedelta(hours=TRADE_TTL_CANDLES * 15 / 60)


def test_stop_beats_target_rule_is_documented_and_actually_true():
    assert any("stop" in rule.lower() and "wins" in rule.lower() for rule in OUTCOME_RULES)
    from smart_trade_analyzer.tests.tracking_support import tick
    from smart_trade_analyzer.tracking import apply_observation
    from datetime import datetime, timedelta
    from smart_trade_analyzer.tracking.models import PriceObservation
    trade = fresh_trade()
    spanning = PriceObservation(observed_at=trade.tracked_at + timedelta(seconds=60), price=100.0, low=97.0, high=107.0)
    assert apply_observation(trade, spanning).status is TradeStatus.STOP_LOSS_HIT


def test_tp2_credits_tp1_rule_is_documented_and_actually_true():
    assert any("tp2" in rule.lower() and "tp1" in rule.lower() and "credited" in rule.lower() for rule in OUTCOME_RULES)
    trade = run_ticks(fresh_trade(), (106.0, 60))
    assert trade.tp1_hit_at is not None and trade.status is TradeStatus.TP2_HIT


def test_only_prices_after_tracking_began_rule_is_documented_and_true():
    assert any("after tracking began" in rule.lower() for rule in OUTCOME_RULES)
    from smart_trade_analyzer.tests.tracking_support import tick
    trade = fresh_trade()
    assert run_ticks(trade, (90.0, 0)).status is TradeStatus.OPEN  # at the tracking instant: ignored


def test_entry_assumed_filled_rule_is_documented():
    assert any("entry" in rule.lower() and "assumed filled" in rule.lower() for rule in OUTCOME_RULES)


def test_fails_closed_without_a_live_price_rule_is_documented():
    assert any("live bitget price" in rule.lower() for rule in OUTCOME_RULES)


def test_sampled_ticker_limitation_is_documented():
    text = " ".join(LIMITATIONS).lower()
    assert "sample" in text and "between two samples" in text


def test_observation_coverage_limitation_is_documented():
    text = " ".join(LIMITATIONS).lower()
    assert "observations" in text and "gap" in text


def test_statistics_disclaimer_does_not_claim_a_forecast():
    text = STATISTICS_DISCLAIMER.lower()
    assert "not a forecast" in text
    for banned in ("guarantee", "accuracy", "probability of winning", "expected future win rate"):
        assert banned not in text
