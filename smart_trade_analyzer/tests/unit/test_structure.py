"""Unit tests for features/structure.py -- swing support/resistance.

Every fixture in this file was constructed and independently checked
against the real implementation before being committed here (not assumed
to behave a certain way by hand-arithmetic alone) -- same discipline as
test_indicators.py.
"""
import math

import pytest

from smart_trade_analyzer.features.structure import (
    MIN_CANDLES_SWING,
    PIVOT_LEFT,
    PIVOT_RIGHT,
    find_pivot_highs,
    find_pivot_lows,
    swing_levels,
)


def _spike_series(base: float, spikes: dict, length: int = 21):
    arr = [base] * length
    for i, v in spikes.items():
        arr[i] = v
    return arr


class TestFindPivots:
    def test_single_clear_pivot_high(self):
        values = [1, 2, 3, 10, 3, 2, 1]  # length 7 == PIVOT_LEFT+PIVOT_RIGHT+1
        assert find_pivot_highs(values) == [3]

    def test_single_clear_pivot_low(self):
        values = [10, 9, 8, 1, 8, 9, 10]
        assert find_pivot_lows(values) == [3]

    def test_boundary_one_candle_short_of_minimum_finds_nothing(self):
        values = [1, 2, 3, 10, 3, 2, 1]
        assert MIN_CANDLES_SWING == PIVOT_LEFT + PIVOT_RIGHT + 1 == 7
        assert find_pivot_highs(values[:-1]) == []  # 6 candles: structurally impossible

    def test_boundary_exact_minimum_finds_the_pivot(self):
        values = [1, 2, 3, 10, 3, 2, 1]  # exactly 7 candles
        assert find_pivot_highs(values) == [3]

    def test_multiple_pivots_found_in_chronological_order(self):
        highs = _spike_series(60, {3: 110, 10: 105, 17: 120})
        assert find_pivot_highs(highs) == [3, 10, 17]

    def test_tie_disqualifies_pivot(self):
        # two equal maxima in the same window -- neither is a strict pivot
        values = [1, 2, 3, 10, 3, 10, 3, 2, 1]
        assert find_pivot_highs(values, left=3, right=3) == []

    def test_monotonic_series_has_no_pivots_of_either_kind(self):
        # a legitimate "no structure here" case -- not a data gap
        mono = [100.0 + i for i in range(60)]
        assert find_pivot_highs(mono) == []
        assert find_pivot_lows(mono) == []

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            find_pivot_highs([1, 2, 3], left=0, right=1)
        with pytest.raises(ValueError):
            find_pivot_lows([1, 2, 3], left=1, right=0)

    def test_recent_candles_within_right_window_cannot_be_confirmed_pivots(self):
        # A spike sitting inside the last `right` candles has no future
        # context yet -- it must not be reported, no matter how extreme.
        values = [1, 2, 3, 2, 1, 1, 500]  # 500 is the very last candle
        assert find_pivot_highs(values) == []


class TestSwingLevels:
    def test_nearest_above_and_below_are_selected_correctly(self):
        highs = _spike_series(60, {3: 110, 10: 105, 17: 120})
        lows = _spike_series(60, {3: 40, 10: 45, 17: 30})
        support, resistance = swing_levels(highs, lows, current_close=60.0)
        assert support == 45  # nearest below 60 among {40, 45, 30}
        assert resistance == 105  # nearest above 60 among {110, 105, 120}

    def test_no_qualifying_resistance_above_current_price_is_none(self):
        highs = _spike_series(60, {3: 110, 10: 105, 17: 120})
        lows = _spike_series(60, {3: 40, 10: 45, 17: 30})
        support, resistance = swing_levels(highs, lows, current_close=200.0)
        assert resistance is None
        assert support == 45

    def test_no_qualifying_support_below_current_price_is_none(self):
        highs = _spike_series(60, {3: 110, 10: 105, 17: 120})
        lows = _spike_series(60, {3: 40, 10: 45, 17: 30})
        support, resistance = swing_levels(highs, lows, current_close=10.0)
        assert support is None
        assert resistance == 105

    def test_monotonic_uptrend_can_legitimately_have_no_levels(self):
        mono = [100.0 + i for i in range(60)]
        support, resistance = swing_levels(mono, mono, current_close=mono[-1])
        assert (support, resistance) == (None, None)

    def test_too_few_candles_returns_none_none(self):
        support, resistance = swing_levels([1, 2, 3], [1, 2, 3], current_close=2.0)
        assert (support, resistance) == (None, None)

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            swing_levels([1] * 10, [1] * 9, current_close=1.0)

    def test_non_finite_current_close_returns_none_none(self):
        highs = _spike_series(60, {3: 110, 10: 105, 17: 120})
        lows = _spike_series(60, {3: 40, 10: 45, 17: 30})
        assert swing_levels(highs, lows, current_close=float("nan")) == (None, None)
        assert swing_levels(highs, lows, current_close=float("inf")) == (None, None)

    def test_non_finite_values_in_series_yield_no_pivots(self):
        highs = _spike_series(60, {3: 110, 10: 105, 17: 120})
        highs[5] = float("nan")
        assert find_pivot_highs(highs) == []

    def test_lookback_excludes_pivots_outside_the_window(self):
        # Index 3 (old, outside a 15-candle lookback) is PRICE-CLOSER to
        # current_close than index 40 (recent, inside the lookback). If
        # lookback filtering had no effect, the closer old pivot (65) would
        # win as "nearest above". With filtering, only 105 remains eligible.
        highs = _spike_series(60, {3: 65, 40: 105}, length=50)
        lows = [60.0] * 50
        without_lookback = swing_levels(highs, lows, current_close=60.0, lookback=50)
        with_lookback = swing_levels(highs, lows, current_close=60.0, lookback=15)
        assert without_lookback[1] == 65  # closer pivot wins when both are eligible
        assert with_lookback[1] == 105  # the closer one is now outside the window
