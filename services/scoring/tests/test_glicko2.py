"""Unit tests for the Glicko-2 rating engine."""

from __future__ import annotations

import pytest

from scoring.glicko2 import (
    DEFAULT_RD,
    DEFAULT_RATING,
    Rating,
    assign_tier,
    league_rating,
    rate,
    run_round,
)
from scoring.config import TierDef

TIERS = [
    TierDef(min=1900, name="Apex"),
    TierDef(min=1750, name="Diamond"),
    TierDef(min=1600, name="Gold"),
    TierDef(min=1450, name="Silver"),
    TierDef(min=1300, name="Bronze"),
    TierDef(min=-99999, name="Provisional"),
]


class TestNewEntrant:
    def test_default_rating(self):
        r = Rating()
        assert r.rating == pytest.approx(DEFAULT_RATING)
        assert r.rd == pytest.approx(DEFAULT_RD)

    def test_league_rating_new_entrant(self):
        # R=1500, RD=350 → league_rating = 1500 - 2*350 = 800
        r = Rating()
        assert league_rating(r) == pytest.approx(800.0)

    def test_new_entrant_tier_provisional(self):
        r = Rating()
        tier = assign_tier(league_rating(r), TIERS)
        assert tier == "Provisional"


class TestRDFloor:
    def test_projected_rd_floor(self):
        r = Rating(rating=1600, rd=200, vol=0.06)
        results = [(Rating(rating=1500, rd=200, vol=0.06), 1.0)] * 5
        updated = rate(r, results, rd_floor=190.0)
        assert updated.rd >= 190.0

    def test_confirmed_rd_floor(self):
        r = Rating(rating=1600, rd=200, vol=0.06)
        results = [(Rating(rating=1500, rd=200, vol=0.06), 1.0)] * 5
        updated = rate(r, results, rd_floor=50.0)
        assert updated.rd >= 50.0

    def test_no_floor_rd_can_be_low(self):
        # many wins with no floor → RD can shrink below 190
        r = Rating(rating=1600, rd=200, vol=0.06)
        results = [(Rating(rating=1500, rd=200, vol=0.06), 1.0)] * 10
        updated = rate(r, results, rd_floor=0.0)
        assert updated.rd < 190.0


class TestWinningRound:
    def test_winner_rating_rises(self):
        r = Rating()
        opponents = [Rating(rating=1500, rd=200, vol=0.06)] * 4
        results = [(opp, 1.0) for opp in opponents]
        updated = rate(r, results, rd_floor=190.0)
        assert updated.rating > r.rating

    def test_loser_rating_falls(self):
        r = Rating()
        opponents = [Rating(rating=1500, rd=200, vol=0.06)] * 4
        results = [(opp, 0.0) for opp in opponents]
        updated = rate(r, results, rd_floor=190.0)
        assert updated.rating < r.rating

    def test_rd_shrinks_after_active_round(self):
        r = Rating()
        opponents = [Rating(rating=1500, rd=200, vol=0.06)] * 4
        results = [(opp, 1.0) for opp in opponents]
        updated = rate(r, results, rd_floor=190.0)
        assert updated.rd < r.rd


class TestNoGames:
    def test_no_games_rd_grows(self):
        r = Rating(rating=1500, rd=200, vol=0.06)
        updated = rate(r, [], rd_floor=0.0)
        assert updated.rd >= r.rd

    def test_no_games_rating_unchanged(self):
        r = Rating(rating=1500, rd=200, vol=0.06)
        updated = rate(r, [], rd_floor=0.0)
        assert updated.rating == pytest.approx(r.rating)


class TestRunRound:
    def test_higher_composite_wins(self):
        scores = {"alice": 0.8, "bob": 0.3}
        result = run_round(scores, rd_floor=190.0)
        assert result["alice"].rating > result["bob"].rating

    def test_priors_loaded(self):
        priors = {"alice": Rating(rating=1600, rd=200, vol=0.06)}
        scores = {"alice": 0.8, "bob": 0.5}
        result = run_round(scores, priors=priors, rd_floor=190.0)
        # alice starts at 1600, not default 1500; her result should differ
        default_result = run_round(scores, rd_floor=190.0)
        assert result["alice"].rating != pytest.approx(default_result["alice"].rating)

    def test_all_traders_in_result(self):
        scores = {"a": 0.9, "b": 0.6, "c": 0.3}
        result = run_round(scores)
        assert set(result.keys()) == {"a", "b", "c"}

    def test_tie_scores(self):
        scores = {"a": 0.5, "b": 0.5}
        result = run_round(scores)
        # Both start at same rating; with identical scores they should end equal
        assert result["a"].rating == pytest.approx(result["b"].rating, abs=0.01)


class TestLeagueRating:
    def test_formula(self):
        r = Rating(rating=1700, rd=100, vol=0.06)
        assert league_rating(r) == pytest.approx(1500.0)

    def test_high_rd_suppresses_display(self):
        r = Rating(rating=1700, rd=350, vol=0.06)
        # 1700 - 2*350 = 1000 → displayed much lower than raw R
        assert league_rating(r) == pytest.approx(1000.0)


class TestAssignTier:
    def test_apex_tier(self):
        assert assign_tier(1950, TIERS) == "Apex"

    def test_gold_tier(self):
        assert assign_tier(1620, TIERS) == "Gold"

    def test_provisional_tier(self):
        assert assign_tier(800, TIERS) == "Provisional"

    def test_boundary_exact(self):
        assert assign_tier(1600, TIERS) == "Gold"
        assert assign_tier(1599, TIERS) == "Silver"


class TestDeterminism:
    def test_same_input_same_output(self):
        scores = {"a": 0.9, "b": 0.6, "c": 0.3, "d": 0.1}
        r1 = run_round(scores, rd_floor=190.0)
        r2 = run_round(scores, rd_floor=190.0)
        for tid in scores:
            assert r1[tid].rating == pytest.approx(r2[tid].rating)
            assert r1[tid].rd == pytest.approx(r2[tid].rd)
