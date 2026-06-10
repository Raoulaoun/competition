"""
THE MERGE GATE — synthetic cohort test.

No change to services/scoring merges unless this file passes green.

Asserts the three invariants that prove risk adjustment actually works:
  1. The disciplined low-leverage grinder ranks #1.
  2. The lucky gambler — despite the highest raw return (~42%) — is demoted
     to 3rd place or lower by the risk adjustment.
  3. The blowup account (94% loss, 800x leverage) is disqualified off the
     ladder and lands dead last under --include-dq.
"""

from __future__ import annotations

import pytest

from scoring.engine import score_cohort
from scoring.models import ScoringResult
from tests.fixtures.synthetic_cohort import build_cohort


@pytest.fixture(scope="module")
def cohort():
    return build_cohort()


@pytest.fixture(scope="module")
def result_default(cohort, config):
    """score_cohort with include_dq=False (default)."""
    return score_cohort(cohort, config, state="projected", include_dq=False)


@pytest.fixture(scope="module")
def result_include_dq(cohort, config):
    """score_cohort with include_dq=True."""
    return score_cohort(cohort, config, state="projected", include_dq=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rank_of(trader_id: str, result: ScoringResult) -> int:
    for tr in result.leaderboard:
        if tr.trader_id == trader_id:
            return tr.rank
    raise AssertionError(f"{trader_id!r} not found in leaderboard")


def return_pct_of(trader_id: str, result: ScoringResult) -> float:
    for tr in result.leaderboard:
        if tr.trader_id == trader_id:
            return tr.metrics.net_return_pct
    raise AssertionError(f"{trader_id!r} not found in leaderboard")


# ---------------------------------------------------------------------------
# Core invariants
# ---------------------------------------------------------------------------

class TestRankInvariants:
    def test_disciplined_grinder_ranks_first(self, result_default):
        assert result_default.leaderboard[0].trader_id == "disciplined_grinder", (
            f"Expected disciplined_grinder at rank 1, got "
            f"{result_default.leaderboard[0].trader_id}"
        )

    def test_lucky_gambler_has_highest_raw_return(self, result_default):
        """Sanity check: gambler really does have the highest raw return."""
        gambler_return = None
        for tr in result_default.leaderboard:
            if tr.trader_id == "lucky_gambler":
                gambler_return = tr.metrics.net_return_pct
                break
        assert gambler_return is not None, "lucky_gambler not found in leaderboard"
        for tr in result_default.leaderboard:
            if tr.trader_id != "lucky_gambler":
                assert gambler_return > tr.metrics.net_return_pct, (
                    f"lucky_gambler ({gambler_return:.1%}) should have highest return, "
                    f"but {tr.trader_id} has {tr.metrics.net_return_pct:.1%}"
                )

    def test_lucky_gambler_demoted_below_grinder(self, result_default):
        grinder_rank = rank_of("disciplined_grinder", result_default)
        gambler_rank = rank_of("lucky_gambler", result_default)
        assert gambler_rank > grinder_rank, (
            f"Gambler (rank {gambler_rank}) should rank BELOW grinder (rank {grinder_rank})"
        )

    def test_lucky_gambler_demoted_to_third_or_lower(self, result_default):
        gambler_rank = rank_of("lucky_gambler", result_default)
        assert gambler_rank >= 3, (
            f"Gambler should be demoted to rank 3 or lower; got rank {gambler_rank}. "
            f"Risk adjustment is not working correctly."
        )


class TestBlowupDQ:
    def test_blowup_not_on_default_leaderboard(self, result_default):
        ids = [tr.trader_id for tr in result_default.leaderboard]
        assert "blowup" not in ids, "blowup account should be DQ'd and excluded from leaderboard"

    def test_blowup_is_disqualified(self, result_default):
        dq_ids = [tr.trader_id for tr in result_default.dq_traders]
        assert "blowup" in dq_ids

    def test_blowup_has_dq_reason(self, result_default):
        for tr in result_default.dq_traders:
            if tr.trader_id == "blowup":
                assert tr.dq_reason != "", "blowup should have a non-empty dq_reason"
                return
        pytest.fail("blowup not found in dq_traders")

    def test_blowup_last_with_include_dq(self, result_include_dq):
        last = result_include_dq.leaderboard[-1]
        assert last.trader_id == "blowup", (
            f"Expected blowup last with include_dq=True, got {last.trader_id}"
        )


# ---------------------------------------------------------------------------
# Fixture sanity checks
# ---------------------------------------------------------------------------

class TestFixtureSanity:
    def test_blowup_end_balance_below_threshold(self, cohort, config):
        """blowup fixture ends below 10% of start → blowup_threshold_pct fires."""
        from scoring.metrics import compute_metrics
        _, deals = next(c for c in cohort if c[0] == "blowup")
        m = compute_metrics(deals)
        threshold = config.eligibility.blowup_threshold_pct * m["start_balance"]
        assert m["end_balance"] <= threshold, (
            f"blowup end_balance ({m['end_balance']}) should be ≤ "
            f"threshold ({threshold})"
        )

    def test_blowup_win_rate_low(self, cohort):
        from scoring.metrics import compute_metrics
        _, deals = next(c for c in cohort if c[0] == "blowup")
        m = compute_metrics(deals)
        assert m["win_rate"] == pytest.approx(2 / 16, abs=0.001)
        assert m["trade_count"] == 16

    def test_blowup_peak_leverage_extreme(self, cohort):
        from scoring.metrics import compute_metrics
        _, deals = next(c for c in cohort if c[0] == "blowup")
        m = compute_metrics(deals)
        # expect ~800x; allow wide tolerance for balance variation during trades
        assert m["peak_leverage"] > 500

    def test_grinder_drawdown_tiny(self, cohort):
        from scoring.metrics import compute_metrics
        _, deals = next(c for c in cohort if c[0] == "disciplined_grinder")
        m = compute_metrics(deals)
        assert m["max_drawdown"] > -0.05, "grinder drawdown should be < 5%"

    def test_grinder_leverage_low(self, cohort):
        from scoring.metrics import compute_metrics
        _, deals = next(c for c in cohort if c[0] == "disciplined_grinder")
        m = compute_metrics(deals)
        assert m["peak_leverage"] < 10, "grinder peak leverage should be < 10x"

    def test_gambler_leverage_high(self, cohort):
        from scoring.metrics import compute_metrics
        _, deals = next(c for c in cohort if c[0] == "lucky_gambler")
        m = compute_metrics(deals)
        assert m["peak_leverage"] > 30, "gambler peak leverage should be > 30x"

    def test_all_eligible_except_blowup(self, result_default):
        eligible_ids = {tr.trader_id for tr in result_default.leaderboard}
        expected_eligible = {
            "disciplined_grinder", "solid_professional", "lucky_gambler",
            "choppy_breakeven", "reckless_survivor",
        }
        assert expected_eligible == eligible_ids


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_cohort_same_result(self, config):
        cohort = build_cohort()
        r1 = score_cohort(cohort, config, state="projected")
        r2 = score_cohort(cohort, config, state="projected")
        ids1 = [tr.trader_id for tr in r1.leaderboard]
        ids2 = [tr.trader_id for tr in r2.leaderboard]
        assert ids1 == ids2
        for tr1, tr2 in zip(r1.leaderboard, r2.leaderboard):
            assert tr1.league_rating == pytest.approx(tr2.league_rating)
            assert tr1.composite == pytest.approx(tr2.composite)
