"""Integration tests for engine.score_cohort()."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scoring.engine import score_cohort
from scoring.models import Deal

T = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bal(b: float) -> Deal:
    return Deal(T, "balance", "balance", None, None, 0.0, 0.0, 0.0, 0.0, b)


def _out(pnl: float, bal: float) -> Deal:
    return Deal(T, "out", "buy", 0.5, 1.2, 0.0, 0.0, 0.0, pnl, bal)


def _in() -> Deal:
    return Deal(T, "in", "buy", 0.5, 1.2, 0.0, 0.0, 0.0, 0.0, None)


def make_trader(name: str, start: float, pnls: list[float]) -> tuple[str, list[Deal]]:
    deals = [_bal(start)]
    balance = start
    for pnl in pnls:
        balance += pnl
        deals.extend([_in(), _out(pnl, balance)])
    return name, deals


class TestBasicCohort:
    def test_leaderboard_ordered_desc(self, config):
        cohort = [
            make_trader("strong", 10_000, [300, 200, 100, 50, 50, 50, 50, 50]),
            make_trader("weak", 10_000, [-50, -50, -50, 50, 50, 50, 50, 50]),
        ]
        result = score_cohort(cohort, config)
        assert len(result.leaderboard) == 2
        assert result.leaderboard[0].league_rating >= result.leaderboard[1].league_rating

    def test_ranks_assigned(self, config):
        cohort = [
            make_trader("a", 10_000, [100, 100, 100, 100, 100]),
            make_trader("b", 10_000, [50, 50, 50, 50, 50]),
            make_trader("c", 10_000, [10, 10, 10, 10, 10]),
        ]
        result = score_cohort(cohort, config)
        ranks = [tr.rank for tr in result.leaderboard]
        assert ranks == [1, 2, 3]

    def test_all_traders_in_ratings(self, config):
        cohort = [
            make_trader("x", 10_000, [100] * 6),
            make_trader("y", 10_000, [50] * 6),
        ]
        result = score_cohort(cohort, config)
        assert "x" in result.ratings
        assert "y" in result.ratings

    def test_carry_forward_shape(self, config):
        cohort = [make_trader("t", 10_000, [100] * 6)]
        result = score_cohort(cohort, config)
        r = result.ratings["t"]
        assert "rating" in r
        assert "rd" in r
        assert "vol" in r


class TestDQBehavior:
    def test_dq_excluded_from_leaderboard_by_default(self, config):
        # ends at ~5% of start (500/10_000) → blowup gate fires
        blowup_pnls = [-9_500] + [0] * 9
        cohort = [
            make_trader("good", 10_000, [100] * 8),
            make_trader("blowup", 10_000, blowup_pnls),
        ]
        result = score_cohort(cohort, config)
        trader_ids = [tr.trader_id for tr in result.leaderboard]
        assert "good" in trader_ids
        assert "blowup" not in trader_ids

    def test_dq_in_dq_traders_list(self, config):
        blowup_pnls = [-9_500] + [10] * 9
        cohort = [
            make_trader("ok", 10_000, [100] * 6),
            make_trader("dq", 10_000, blowup_pnls),
        ]
        result = score_cohort(cohort, config)
        dq_ids = [tr.trader_id for tr in result.dq_traders]
        assert "dq" in dq_ids

    def test_include_dq_appends_to_leaderboard(self, config):
        blowup_pnls = [-9_500] + [10] * 9
        cohort = [
            make_trader("ok", 10_000, [100] * 8),
            make_trader("dq", 10_000, blowup_pnls),
        ]
        result = score_cohort(cohort, config, include_dq=True)
        all_ids = [tr.trader_id for tr in result.leaderboard]
        assert "ok" in all_ids
        assert "dq" in all_ids

    def test_include_dq_dq_last(self, config):
        blowup_pnls = [-9_500] + [10] * 9
        cohort = [
            make_trader("ok", 10_000, [100] * 8),
            make_trader("dq", 10_000, blowup_pnls),
        ]
        result = score_cohort(cohort, config, include_dq=True)
        assert result.leaderboard[-1].trader_id == "dq"


class TestPillarBreakdown:
    def test_pillar_scores_present(self, config):
        cohort = [make_trader("t", 10_000, [100] * 6)]
        result = score_cohort(cohort, config)
        tr = result.leaderboard[0]
        expected_pillars = {
            "risk_adjusted", "capital_preservation", "payoff_discipline",
            "return_alpha", "consistency", "behavioral_hygiene",
        }
        assert set(tr.pillar_scores.keys()) == expected_pillars

    def test_pillar_scores_in_range(self, config):
        cohort = [
            make_trader("a", 10_000, [100] * 6),
            make_trader("b", 10_000, [50] * 6),
        ]
        result = score_cohort(cohort, config)
        for tr in result.leaderboard:
            for score in tr.pillar_scores.values():
                assert 0.0 <= score <= 1.0 + 1e-9


class TestEdgeCases:
    def test_near_empty_deal_set_dq_not_crash(self, config):
        cohort = [
            make_trader("ok", 10_000, [100] * 6),
            make_trader("tiny", 10_000, [50, 50]),  # 2 trades < min_trade_count=5
        ]
        result = score_cohort(cohort, config)
        dq_ids = [tr.trader_id for tr in result.dq_traders]
        assert "tiny" in dq_ids

    def test_all_losing_account(self, config):
        """An all-losing account must complete without exception."""
        cohort = [
            make_trader("winner", 10_000, [100] * 8),
            make_trader("loser", 10_000, [-100, -100, -50, -50, -30, -20, -20, -20]),
        ]
        result = score_cohort(cohort, config)
        assert result is not None

    def test_minimal_two_trader_cohort(self, config):
        cohort = [
            make_trader("a", 10_000, [200] * 6),
            make_trader("b", 10_000, [50] * 6),
        ]
        result = score_cohort(cohort, config)
        assert len(result.leaderboard) == 2
        assert result.leaderboard[0].trader_id == "a"

    def test_priors_affect_ratings(self, config):
        from scoring.glicko2 import Rating
        cohort = [
            make_trader("veteran", 10_000, [200] * 6),
            make_trader("newbie", 10_000, [50] * 6),
        ]
        priors = {"veteran": Rating(rating=1700, rd=100, vol=0.06)}
        result = score_cohort(cohort, config, priors=priors)
        veteran_rating = result.ratings["veteran"]["rating"]
        # veteran starts at 1700, not 1500; should have higher rating than newbie
        newbie_rating = result.ratings["newbie"]["rating"]
        assert veteran_rating > newbie_rating


class TestDeterminism:
    def test_same_input_same_output(self, config):
        cohort = [
            make_trader("a", 10_000, [300, 200, -50, 100, 150, 100]),
            make_trader("b", 10_000, [100, -100, 200, -50, 300, 100]),
            make_trader("c", 10_000, [50, 50, 50, 50, 50, 50]),
        ]
        r1 = score_cohort(cohort, config)
        r2 = score_cohort(cohort, config)
        for i in range(len(r1.leaderboard)):
            assert r1.leaderboard[i].trader_id == r2.leaderboard[i].trader_id
            assert r1.leaderboard[i].league_rating == pytest.approx(
                r2.leaderboard[i].league_rating
            )
