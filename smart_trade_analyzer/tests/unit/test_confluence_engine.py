"""Tests for confluence/confluence_engine.py -- category aggregation,
anti-double-counting, direction inheritance, and the Section 8 formula.
"""
import random
from datetime import datetime, timedelta

import pytest

from smart_trade_analyzer.confluence.confluence_engine import (
    CATEGORY_WEIGHTS, CONFLICT_THRESHOLD, GRADE_THRESHOLDS, compute_confluence,
)
from smart_trade_analyzer.contracts import (
    CandleData, Direction, EvidenceCategory, FeatureSet, MarketRegime, RegimeType, SetupCandidate, SetupType, Timeframe,
)

NOW = datetime(2026, 8, 27, 12, 0, 0)


def make_fs(**overrides):
    defaults = dict(
        symbol="X", timeframe=Timeframe.M1, as_of=NOW, close=100.0,
        ema9=None, ema21=None, ema50=None, ema200=None,
        rsi14=None, stoch_rsi_k=None, stoch_rsi_d=None,
        macd_line=None, macd_signal=None, macd_hist=None,
        bb_upper=None, bb_mid=None, bb_lower=None,
        atr=None, atr_pct=None, volume_ratio=None,
        swing_support=None, swing_resistance=None,
        divergence=None, completeness=1.0,
    )
    defaults.update(overrides)
    return FeatureSet(**defaults)


def make_regime(**overrides):
    defaults = dict(regime=RegimeType.UPTREND, trend_strength=0.5, volatility_percentile=0.5, basis=["t"])
    defaults.update(overrides)
    return MarketRegime(**defaults)


def make_setup(direction=Direction.LONG, setup_type=SetupType.TREND_CONTINUATION):
    return SetupCandidate(
        setup_type=setup_type, direction=direction, prerequisites_met=True, confirmation_met=True,
        invalidation_price=90.0, evidence_refs=["x"], failed_conditions=[],
    )


# ---------------------------------------------------------------------------
# Section 8 constants -- exact approved values, regression-locked
# ---------------------------------------------------------------------------

def test_category_weights_match_exact_approved_values():
    assert CATEGORY_WEIGHTS == {
        EvidenceCategory.TREND: 20, EvidenceCategory.MOMENTUM: 15, EvidenceCategory.STRUCTURE: 15,
        EvidenceCategory.HTF: 15, EvidenceCategory.FLOW: 10, EvidenceCategory.SENTIMENT: 10,
        EvidenceCategory.VOLUME: 10, EvidenceCategory.VOLATILITY: 5,
    }


def test_category_weights_sum_to_one_hundred():
    assert sum(CATEGORY_WEIGHTS.values()) == 100


def test_grade_thresholds_match_exact_approved_values():
    assert GRADE_THRESHOLDS == {"A": 75.0, "B": 60.0, "C": 40.0, "F": 0.0}


def test_all_eight_categories_have_a_weight():
    assert set(CATEGORY_WEIGHTS.keys()) == set(EvidenceCategory)


# ---------------------------------------------------------------------------
# proposed_direction: inherited, never independently computed
# ---------------------------------------------------------------------------

def test_proposed_direction_is_inherited_unchanged_from_setup_candidate():
    fs = make_fs(rsi14=90.0)  # strongly bullish evidence
    result = compute_confluence(make_setup(direction=Direction.SHORT), fs, make_regime(), [])
    assert result.proposed_direction == Direction.SHORT  # NOT flipped to LONG despite bullish evidence


def test_proposed_direction_matches_long_setup_too():
    fs = make_fs(rsi14=10.0)  # strongly bearish evidence
    result = compute_confluence(make_setup(direction=Direction.LONG), fs, make_regime(), [])
    assert result.proposed_direction == Direction.LONG  # NOT flipped to SHORT despite bearish evidence


def test_proposed_direction_is_never_computed_from_evidence_across_random_inputs():
    # Stress test: across many random FeatureSets, proposed_direction always
    # equals exactly the input SetupCandidate's direction -- never anything
    # derived from the (randomized, sometimes contradictory) evidence.
    for seed in range(50):
        rng = random.Random(seed)
        fs = make_fs(
            rsi14=rng.uniform(0, 100), ema9=rng.uniform(90, 110), ema21=100.0,
            macd_hist=rng.uniform(-2, 2), divergence=rng.choice(["BULLISH", "BEARISH", None]),
        )
        direction = rng.choice([Direction.LONG, Direction.SHORT])
        result = compute_confluence(make_setup(direction=direction), fs, make_regime(), [])
        assert result.proposed_direction == direction


# ---------------------------------------------------------------------------
# Anti-double-counting (Section 18's explicit regression case)
# ---------------------------------------------------------------------------

def test_rsi_and_stoch_rsi_agreeing_are_netted_not_summed():
    fs = make_fs(rsi14=75.0, stoch_rsi_k=90.0)  # signed: 0.5 and 0.8
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    score = result.category_scores[EvidenceCategory.MOMENTUM]
    assert score == pytest.approx((0.5 + 0.8) / 2)  # AVERAGE
    assert score != pytest.approx(0.5 + 0.8)  # explicitly NOT the sum
    assert -1.0 <= score <= 1.0


def test_ema_and_macd_agreeing_in_trend_are_netted_not_summed():
    fs = make_fs(ema9=102.0, ema21=100.0, close=100.0, macd_hist=1.0)
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    score = result.category_scores[EvidenceCategory.TREND]
    assert -1.0 <= score <= 1.0
    # both signals individually reach strength 1.0 (2% EMA gap, 1% MACD/close) -- netted average must stay at 1.0, not exceed it
    assert score == pytest.approx(1.0)


def test_disagreeing_signals_within_a_category_partially_cancel():
    fs = make_fs(rsi14=90.0, stoch_rsi_k=10.0)  # signed: +0.8 and -0.8 -- should net near zero
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    assert result.category_scores[EvidenceCategory.MOMENTUM] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# categories_available / categories_excluded
# ---------------------------------------------------------------------------

def test_htf_flow_sentiment_are_always_excluded():
    fs = make_fs(rsi14=60.0, ema9=101.0, ema21=100.0, close=100.0, macd_hist=0.1,
                  divergence="BULLISH", swing_support=99.5, atr=1.0,
                  bb_upper=110.0, bb_lower=90.0, volume_ratio=2.0)
    closed = [CandleData(open_time=NOW - timedelta(minutes=2), open=99, high=99.2, low=98.8, close=99,
                          volume=100, is_closed=True),
              CandleData(open_time=NOW - timedelta(minutes=1), open=99, high=100.2, low=98.8, close=100,
                          volume=100, is_closed=True)]
    result = compute_confluence(make_setup(), fs, make_regime(regime=RegimeType.RANGE), closed)
    assert EvidenceCategory.HTF in result.categories_excluded
    assert EvidenceCategory.FLOW in result.categories_excluded
    assert EvidenceCategory.SENTIMENT in result.categories_excluded
    for cat in (EvidenceCategory.HTF, EvidenceCategory.FLOW, EvidenceCategory.SENTIMENT):
        assert cat not in result.categories_available


def test_empty_feature_set_excludes_every_category():
    result = compute_confluence(make_setup(), make_fs(), make_regime(), [])
    assert set(result.categories_excluded) == set(EvidenceCategory)
    assert result.categories_available == []


def test_available_and_excluded_partition_all_eight_categories_with_no_overlap():
    fs = make_fs(rsi14=60.0)
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    assert set(result.categories_available) | set(result.categories_excluded) == set(EvidenceCategory)
    assert set(result.categories_available) & set(result.categories_excluded) == set()


def test_category_scores_never_contains_an_excluded_category():
    fs = make_fs(rsi14=60.0)  # only MOMENTUM available
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    assert set(result.category_scores.keys()) == set(result.categories_available)
    for excluded in result.categories_excluded:
        assert excluded not in result.category_scores


# ---------------------------------------------------------------------------
# setup_quality_score: exact formula, missing categories pull toward 50,
# never renormalized
# ---------------------------------------------------------------------------

def test_score_formula_hand_computed_single_category():
    fs = make_fs(rsi14=75.0, stoch_rsi_k=90.0)  # MOMENTUM (weight 15) netted to 0.65
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    expected = 50.0 + (15 * 0.65) / 2.0
    assert result.setup_quality_score == pytest.approx(expected)


def test_score_formula_hand_computed_two_categories():
    fs = make_fs(rsi14=100.0, ema9=110.0, ema21=100.0)  # MOMENTUM=1.0 (weight15), TREND=1.0 (weight20)
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    expected = 50.0 + (15 * 1.0 + 20 * 1.0) / 2.0
    assert result.setup_quality_score == pytest.approx(expected)


def test_no_available_categories_scores_exactly_fifty():
    result = compute_confluence(make_setup(), make_fs(), make_regime(), [])
    assert result.setup_quality_score == pytest.approx(50.0)


def test_missing_categories_are_not_renormalized_score_compresses_toward_fifty():
    # SAME per-category scores (fully bullish), but fewer available
    # categories -- the achievable score must shrink toward 50, never
    # "make up" for the missing weight.
    fs_one_cat = make_fs(rsi14=100.0)
    fs_two_cat = make_fs(rsi14=100.0, ema9=110.0, ema21=100.0)
    one = compute_confluence(make_setup(), fs_one_cat, make_regime(), [])
    two = compute_confluence(make_setup(), fs_two_cat, make_regime(), [])
    assert 50.0 < one.setup_quality_score < two.setup_quality_score <= 100.0


def test_score_is_always_clipped_within_zero_and_one_hundred_across_random_inputs():
    for seed in range(200):
        rng = random.Random(seed)
        fs = make_fs(
            rsi14=rng.uniform(0, 100), stoch_rsi_k=rng.uniform(0, 100),
            ema9=rng.uniform(50, 150), ema21=100.0, close=rng.uniform(50, 150),
            macd_hist=rng.uniform(-5, 5), divergence=rng.choice(["BULLISH", "BEARISH", None]),
            swing_support=rng.uniform(50, 99), swing_resistance=rng.uniform(101, 150), atr=rng.uniform(0.1, 5),
            bb_upper=rng.uniform(101, 150), bb_lower=rng.uniform(50, 99), volume_ratio=rng.uniform(0.5, 5),
        )
        regime = make_regime(regime=rng.choice(list(RegimeType)))
        direction = rng.choice([Direction.LONG, Direction.SHORT])
        result = compute_confluence(make_setup(direction=direction), fs, regime, [])
        assert 0.0 <= result.setup_quality_score <= 100.0
        for score in result.category_scores.values():
            assert -1.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# conflicts
# ---------------------------------------------------------------------------

def test_strongly_opposing_category_is_flagged_as_a_conflict():
    fs = make_fs(rsi14=95.0)  # signed +0.9, strongly bullish
    result = compute_confluence(make_setup(direction=Direction.SHORT), fs, make_regime(), [])
    assert EvidenceCategory.MOMENTUM in result.conflicts


def test_agreeing_category_is_never_flagged_as_a_conflict():
    fs = make_fs(rsi14=95.0)
    result = compute_confluence(make_setup(direction=Direction.LONG), fs, make_regime(), [])
    assert EvidenceCategory.MOMENTUM not in result.conflicts


def test_mild_disagreement_below_threshold_is_not_a_conflict():
    # signed = (55-50)/50 = 0.1, well below CONFLICT_THRESHOLD -- mild
    # disagreement is not the same as a strong conflict.
    fs = make_fs(rsi14=55.0)
    result = compute_confluence(make_setup(direction=Direction.SHORT), fs, make_regime(), [])
    assert 0.1 < CONFLICT_THRESHOLD
    assert EvidenceCategory.MOMENTUM not in result.conflicts


def test_excluded_categories_can_never_be_a_conflict():
    result = compute_confluence(make_setup(), make_fs(), make_regime(), [])
    assert result.conflicts == []


# ---------------------------------------------------------------------------
# Determinism / purity
# ---------------------------------------------------------------------------

def test_compute_confluence_is_deterministic():
    fs = make_fs(rsi14=60.0, ema9=101.0, ema21=100.0)
    setup = make_setup()
    regime = make_regime()
    r1 = compute_confluence(setup, fs, regime, [])
    r2 = compute_confluence(setup, fs, regime, [])
    assert r1 == r2


# ---------------------------------------------------------------------------
# evidence list contains exactly the items from available categories
# ---------------------------------------------------------------------------

def test_evidence_list_only_contains_items_from_available_categories():
    fs = make_fs(rsi14=60.0)  # only MOMENTUM
    result = compute_confluence(make_setup(), fs, make_regime(), [])
    assert len(result.evidence) > 0
    assert all(item.category == EvidenceCategory.MOMENTUM for item in result.evidence)


# ---------------------------------------------------------------------------
# Audit fix: category_scores must be relative to proposed_direction, not an
# absolute LONG/SHORT axis. Before the fix, an EvidenceItem's raw
# Direction.LONG/SHORT was signed directly (LONG=+strength, SHORT=-strength)
# with no reference to proposed_direction at all -- a well-supported SHORT
# candidate was scored as if its supporting (bearish) evidence were
# adverse, purely because the evidence itself pointed SHORT. The four
# mirrored cases below are the exact regression the audit asked for.
# ---------------------------------------------------------------------------

class TestDirectionRelativeOrientation:
    def test_long_candidate_long_supporting_evidence_is_positive(self):
        fs = make_fs(rsi14=90.0)  # RSI evidence direction=LONG, signed strength +0.8
        result = compute_confluence(make_setup(direction=Direction.LONG), fs, make_regime(), [])
        assert result.category_scores[EvidenceCategory.MOMENTUM] == pytest.approx(0.8)

    def test_long_candidate_short_supporting_evidence_is_negative(self):
        fs = make_fs(rsi14=10.0)  # RSI evidence direction=SHORT, opposes a LONG candidate
        result = compute_confluence(make_setup(direction=Direction.LONG), fs, make_regime(), [])
        assert result.category_scores[EvidenceCategory.MOMENTUM] == pytest.approx(-0.8)

    def test_short_candidate_short_supporting_evidence_is_positive(self):
        # The exact mirror of the first case: SHORT evidence supporting a
        # SHORT candidate must land at the SAME +0.8 a supporting LONG read
        # gets for a LONG candidate -- not -0.8.
        fs = make_fs(rsi14=10.0)  # RSI evidence direction=SHORT, supports a SHORT candidate
        result = compute_confluence(make_setup(direction=Direction.SHORT), fs, make_regime(), [])
        assert result.category_scores[EvidenceCategory.MOMENTUM] == pytest.approx(0.8)

    def test_short_candidate_long_supporting_evidence_is_negative(self):
        # The exact mirror of the second case.
        fs = make_fs(rsi14=90.0)  # RSI evidence direction=LONG, opposes a SHORT candidate
        result = compute_confluence(make_setup(direction=Direction.SHORT), fs, make_regime(), [])
        assert result.category_scores[EvidenceCategory.MOMENTUM] == pytest.approx(-0.8)

    def test_mirrored_evidence_direction_alone_never_changes_the_sign(self):
        # Property version of the four cases above, across many strengths:
        # what matters is agreement with proposed_direction, never the
        # absolute LONG/SHORT label by itself.
        for rsi in (55, 65, 75, 85, 95):
            long_supports_long = compute_confluence(
                make_setup(direction=Direction.LONG), make_fs(rsi14=rsi), make_regime(), []
            ).category_scores[EvidenceCategory.MOMENTUM]
            short_supports_short = compute_confluence(
                make_setup(direction=Direction.SHORT), make_fs(rsi14=100 - rsi), make_regime(), []
            ).category_scores[EvidenceCategory.MOMENTUM]
            assert long_supports_long == pytest.approx(short_supports_short)
            assert long_supports_long > 0

    def test_setup_quality_score_does_not_penalize_a_well_supported_short(self):
        # The concrete symptom the audit described: a correctly-supported
        # SHORT must not score lower than an equally-supported LONG merely
        # because the evidence (and the candidate) point SHORT.
        long_result = compute_confluence(make_setup(direction=Direction.LONG), make_fs(rsi14=90.0), make_regime(), [])
        short_result = compute_confluence(make_setup(direction=Direction.SHORT), make_fs(rsi14=10.0), make_regime(), [])
        assert long_result.setup_quality_score == pytest.approx(short_result.setup_quality_score)
        assert long_result.setup_quality_score > 50.0
        assert short_result.setup_quality_score > 50.0  # the actual bug: this used to be < 50

    def test_setup_quality_score_mirrors_for_conflicting_evidence_too(self):
        long_conflict = compute_confluence(make_setup(direction=Direction.LONG), make_fs(rsi14=10.0), make_regime(), [])
        short_conflict = compute_confluence(make_setup(direction=Direction.SHORT), make_fs(rsi14=90.0), make_regime(), [])
        assert long_conflict.setup_quality_score == pytest.approx(short_conflict.setup_quality_score)
        assert long_conflict.setup_quality_score < 50.0
        assert short_conflict.setup_quality_score < 50.0
        assert EvidenceCategory.MOMENTUM in long_conflict.conflicts
        assert EvidenceCategory.MOMENTUM in short_conflict.conflicts

    def test_multi_category_mirrored_score_symmetric(self):
        # Broader than a single MOMENTUM item: TREND + MOMENTUM together,
        # fully mirrored between a supported LONG and a supported SHORT.
        fs_long = make_fs(rsi14=90.0, ema9=102.0, ema21=100.0)
        fs_short = make_fs(rsi14=10.0, ema9=98.0, ema21=100.0)
        long_result = compute_confluence(make_setup(direction=Direction.LONG), fs_long, make_regime(), [])
        short_result = compute_confluence(make_setup(direction=Direction.SHORT), fs_short, make_regime(), [])
        assert long_result.setup_quality_score == pytest.approx(short_result.setup_quality_score)
        assert long_result.category_scores[EvidenceCategory.TREND] == pytest.approx(
            short_result.category_scores[EvidenceCategory.TREND]
        )
