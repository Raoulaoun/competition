"""Unit tests for normalise.rank_norm() and composite_scores()."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scoring.normalise import rank_norm


class TestRankNorm:
    def test_ascending_direction_1(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        r = rank_norm(s, direction=1)
        # values should be monotone ascending
        assert list(r) == sorted(r)
        assert r.min() > 0.0
        assert r.max() <= 1.0

    def test_descending_direction_minus1(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        r = rank_norm(s, direction=-1)
        # direction=-1: largest raw → lowest rank; result is monotone descending
        assert list(r) == sorted(r, reverse=True)

    def test_single_value_returns_half(self):
        s = pd.Series([42.0])
        r = rank_norm(s, direction=1)
        assert r.iloc[0] == pytest.approx(0.5)

    def test_all_same_value(self):
        # all tied → pandas rank(method="average") gives the same percentile to all
        s = pd.Series([7.0, 7.0, 7.0])
        r = rank_norm(s, direction=1)
        assert r.nunique() == 1  # all equal
        assert 0.0 < r.iloc[0] <= 1.0

    def test_nan_filled_to_min(self):
        s = pd.Series([1.0, 2.0, 3.0, np.nan])
        r = rank_norm(s, direction=1)
        # NaN should be filled to the minimum non-NaN percentile
        assert not r.isna().any()
        assert r.iloc[3] == r.min()

    def test_inf_coerced_to_nan(self):
        s = pd.Series([1.0, np.inf, 3.0])
        r = rank_norm(s, direction=1)
        assert not r.isna().any()
        # inf treated as nan → filled to min rank
        assert r.iloc[1] == r.min()

    def test_neg_inf_coerced_to_nan(self):
        s = pd.Series([1.0, -np.inf, 3.0])
        r = rank_norm(s, direction=1)
        assert not r.isna().any()

    def test_two_traders(self):
        """Minimal cohort of 2: should return two distinct values, not both 0.5."""
        s = pd.Series([10.0, 20.0])
        r = rank_norm(s, direction=1)
        assert r.iloc[0] != r.iloc[1]
        assert r.iloc[1] > r.iloc[0]

    def test_preserves_index(self):
        s = pd.Series([3.0, 1.0, 2.0], index=["c", "a", "b"])
        r = rank_norm(s, direction=1)
        assert list(r.index) == ["c", "a", "b"]
