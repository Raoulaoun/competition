"""Unit tests for metrics.compute_metrics()."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from scoring.metrics import compute_metrics
from scoring.models import Deal

T = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bal(balance: float) -> Deal:
    return Deal(T, "balance", "balance", None, None, 0.0, 0.0, 0.0, 0.0, balance)


def _out(pnl: float, balance: float, volume: float = 1.0, price: float = 1.0) -> Deal:
    return Deal(T, "out", "buy", volume, price, 0.0, 0.0, 0.0, pnl, balance)


def _in(volume: float = 1.0, price: float = 1.0) -> Deal:
    return Deal(T, "in", "buy", volume, price, 0.0, 0.0, 0.0, 0.0, None)


def simple_cohort(start: float, pnls: list[float]) -> list[Deal]:
    deals = [_bal(start)]
    balance = start
    for pnl in pnls:
        balance += pnl
        deals.append(_in())
        deals.append(_out(pnl, balance))
    return deals


class TestNetReturn:
    def test_positive_return(self):
        m = compute_metrics(simple_cohort(10_000, [500, 300, -100]))
        assert m["net_pl"] == pytest.approx(700.0, abs=0.01)
        assert m["net_return_pct"] == pytest.approx(0.07, abs=0.0001)

    def test_negative_return(self):
        m = compute_metrics(simple_cohort(10_000, [-200, -300]))
        assert m["net_pl"] == pytest.approx(-500.0, abs=0.01)
        assert m["net_return_pct"] == pytest.approx(-0.05, abs=0.0001)

    def test_start_end_balance(self):
        m = compute_metrics(simple_cohort(5_000, [100, -50]))
        assert m["start_balance"] == pytest.approx(5_000.0)
        assert m["end_balance"] == pytest.approx(5_050.0)


class TestDrawdown:
    def test_flat_equity_zero_drawdown(self):
        # all wins, equity monotone → drawdown should be 0
        m = compute_metrics(simple_cohort(10_000, [100, 200, 300]))
        assert m["max_drawdown"] == pytest.approx(0.0, abs=0.0001)

    def test_drawdown_after_peak(self):
        # equity: 10000, 11000, 9000 → dd from peak = (9000-11000)/11000 ≈ -0.1818
        m = compute_metrics(simple_cohort(10_000, [1_000, -2_000]))
        assert m["max_drawdown"] < 0
        assert m["max_drawdown"] == pytest.approx(-0.1818, abs=0.001)

    def test_max_drawdown_negative(self):
        # stored negative; closer to 0 = better
        m = compute_metrics(simple_cohort(10_000, [500, -2_000, 1_000]))
        assert m["max_drawdown"] < 0

    def test_ulcer_index_positive(self):
        m = compute_metrics(simple_cohort(10_000, [500, -1_000]))
        assert m["ulcer_index"] >= 0


class TestRiskAdjusted:
    def test_sharpe_zero_std(self):
        # single trade → std=0 → sharpe=0
        m = compute_metrics(simple_cohort(10_000, [500]))
        assert m["sharpe"] == pytest.approx(0.0)

    def test_sortino_no_losses(self):
        # no losing trades → dstd=0 → sortino=0
        m = compute_metrics(simple_cohort(10_000, [100, 200, 300]))
        assert m["sortino"] == pytest.approx(0.0)

    def test_return_over_maxdd_zero_when_no_dd(self):
        m = compute_metrics(simple_cohort(10_000, [100, 200]))
        assert m["return_over_maxdd"] == pytest.approx(0.0)


class TestPayoff:
    def test_profit_factor_no_losses(self):
        # all wins → profit_factor = 999.0 (inf capped)
        m = compute_metrics(simple_cohort(10_000, [100, 200, 300]))
        assert m["profit_factor"] == pytest.approx(999.0)

    def test_profit_factor_no_wins(self):
        m = compute_metrics(simple_cohort(10_000, [-100, -200]))
        assert m["profit_factor"] == pytest.approx(0.0)

    def test_profit_factor_mixed(self):
        # gross_profit=300, gross_loss=100 → pf=3.0
        m = compute_metrics(simple_cohort(10_000, [300, -100]))
        assert m["profit_factor"] == pytest.approx(3.0, abs=0.001)

    def test_payoff_ratio_no_losses(self):
        m = compute_metrics(simple_cohort(10_000, [100, 200]))
        assert m["payoff_ratio"] == pytest.approx(0.0)

    def test_largest_loss_pct_negative(self):
        m = compute_metrics(simple_cohort(10_000, [100, -500, 100]))
        assert m["largest_loss_pct"] < 0
        assert m["largest_loss_pct"] == pytest.approx(-0.05, abs=0.0001)

    def test_win_rate(self):
        m = compute_metrics(simple_cohort(10_000, [100, -100, 100]))
        assert m["win_rate"] == pytest.approx(2 / 3, abs=0.001)


class TestLeverage:
    def test_peak_leverage_computed(self):
        # volume=1, price=1.2, balance=10000 → notional=120000 → lev=12
        deals = [_bal(10_000)]
        deals.append(_in(1.0, 1.2))
        deals.append(_out(100.0, 10_100.0, 1.0, 1.2))
        m = compute_metrics(deals)
        assert m["peak_leverage"] == pytest.approx(12.0, abs=0.5)

    def test_zero_leverage_no_volume_deals(self):
        # only balance deal
        m = compute_metrics([_bal(10_000)])
        assert m["peak_leverage"] == pytest.approx(0.0)


class TestConsistency:
    def test_linearity_monotone_positive(self):
        # perfect ascending equity → linearity near 1.0
        m = compute_metrics(simple_cohort(10_000, [100, 100, 100, 100, 100]))
        assert m["equity_linearity"] > 0.99

    def test_linearity_negative_when_losing(self):
        m = compute_metrics(simple_cohort(10_000, [-100, -100, -100]))
        assert m["equity_linearity"] < 0


class TestGateFields:
    def test_negative_balance_flag(self):
        m = compute_metrics(simple_cohort(1_000, [-1_500]))
        assert m["negative_balance"] is True

    def test_positive_balance_no_flag(self):
        m = compute_metrics(simple_cohort(10_000, [500]))
        assert m["negative_balance"] is False

    def test_equity_low_pct(self):
        # min equity = 8000 / 10000 = 0.8
        m = compute_metrics(simple_cohort(10_000, [-2_000, 1_000]))
        assert m["equity_low_pct"] == pytest.approx(0.8, abs=0.01)

    def test_trade_count(self):
        m = compute_metrics(simple_cohort(10_000, [100, 200, 300]))
        assert m["trade_count"] == 3


class TestEdgeCases:
    def test_empty_deals(self):
        m = compute_metrics([_bal(10_000)])
        assert m["trade_count"] == 0
        assert m["net_pl"] == pytest.approx(0.0)

    def test_all_losing(self):
        """No winning trades must not raise any exception."""
        m = compute_metrics(simple_cohort(10_000, [-100, -200, -300]))
        assert m["win_rate"] == pytest.approx(0.0)
        assert m["profit_factor"] == pytest.approx(0.0)
        assert m["payoff_ratio"] == pytest.approx(0.0)
        assert not math.isnan(m["sortino"])

    def test_single_trade(self):
        """Single trade: std=0, downside std=0 → no division by zero."""
        m = compute_metrics(simple_cohort(10_000, [500]))
        assert m["trade_count"] == 1
        assert m["sharpe"] == pytest.approx(0.0)
        assert m["sortino"] == pytest.approx(0.0)

    def test_determinism(self):
        deals = simple_cohort(10_000, [100, -50, 200, -75, 150])
        m1 = compute_metrics(deals)
        m2 = compute_metrics(deals)
        assert m1 == m2

    def test_net_includes_swap_commission_fee(self):
        # pnl=100, commission=-5, fee=-2, swap=1 → net = 94
        deal = Deal(T, "out", "buy", 1.0, 1.0, -5.0, -2.0, 1.0, 100.0, 10_094.0)
        deals = [_bal(10_000.0), _in(), deal]
        m = compute_metrics(deals)
        assert m["net_pl"] == pytest.approx(94.0, abs=0.01)
