"""Unit tests for eligibility.apply_gate()."""

from __future__ import annotations

import pytest

from scoring.config import EligibilityConfig
from scoring.eligibility import apply_gate
from scoring.models import MetricVector

DEFAULT_GATE = EligibilityConfig(
    min_trade_count=5,
    stop_out_equity_pct=0.0,
    blowup_threshold_pct=0.10,
)


def _mv(**overrides) -> MetricVector:
    """Build a minimal eligible MetricVector with overrides."""
    defaults = dict(
        trader_id="t1", account=None,
        net_pl=1000.0, net_return_pct=0.10,
        start_balance=10_000.0, end_balance=11_000.0,
        sharpe=1.0, sortino=1.5, return_over_maxdd=2.0,
        max_drawdown=-0.05, ulcer_index=0.02, recovery_factor=2.0,
        profit_factor=1.5, payoff_ratio=1.2, expectancy=50.0,
        win_rate=0.6, largest_loss_pct=-0.02,
        trade_return_std=30.0, equity_linearity=0.9,
        trade_count=10, peak_leverage=5.0, leverage_consistency=1.0,
        equity_low_pct=0.95, negative_balance=False,
    )
    defaults.update(overrides)
    return MetricVector(**defaults)


class TestEligibleTrader:
    def test_clean_trader_is_eligible(self):
        result = apply_gate(_mv(), DEFAULT_GATE)
        assert result.eligible is True
        assert result.dq_reason == ""


class TestUnderMinTrades:
    def test_below_min_trades_dq(self):
        result = apply_gate(_mv(trade_count=3), DEFAULT_GATE)
        assert result.eligible is False
        assert "under_min_trades" in result.dq_reason

    def test_exactly_min_trades_eligible(self):
        result = apply_gate(_mv(trade_count=5), DEFAULT_GATE)
        assert result.eligible is True


class TestNegativeBalance:
    def test_negative_balance_dq(self):
        result = apply_gate(_mv(negative_balance=True, end_balance=-100.0), DEFAULT_GATE)
        assert result.eligible is False
        assert "negative_balance" in result.dq_reason


class TestStopOut:
    def test_stop_out_fires_when_configured(self):
        gate = EligibilityConfig(min_trade_count=5, stop_out_equity_pct=0.20, blowup_threshold_pct=0.10)
        # equity_low_pct=0.15 <= 0.20 → DQ
        result = apply_gate(_mv(equity_low_pct=0.15), gate)
        assert result.eligible is False
        assert "stop_out" in result.dq_reason

    def test_stop_out_zero_does_not_fire(self):
        # stop_out_equity_pct=0.0 → this check is disabled
        result = apply_gate(_mv(equity_low_pct=0.05), DEFAULT_GATE)
        # blowup gate may fire; stop_out should not be in the reason
        assert "stop_out" not in result.dq_reason

    def test_stop_out_exactly_at_threshold(self):
        gate = EligibilityConfig(min_trade_count=5, stop_out_equity_pct=0.20, blowup_threshold_pct=0.10)
        result = apply_gate(_mv(equity_low_pct=0.20), gate)
        assert result.eligible is False
        assert "stop_out" in result.dq_reason


class TestBlowup:
    def test_blowout_below_threshold(self):
        # end_balance=500 ≤ 0.10 × 10_000 = 1000 → blowout
        result = apply_gate(_mv(end_balance=500.0, start_balance=10_000.0), DEFAULT_GATE)
        assert result.eligible is False
        assert "blowout" in result.dq_reason

    def test_blowout_fires_last_only(self):
        # trade_count=2 also triggers under_min_trades; blowout should NOT double-fire
        result = apply_gate(
            _mv(trade_count=2, end_balance=500.0, start_balance=10_000.0), DEFAULT_GATE
        )
        assert "under_min_trades" in result.dq_reason
        assert "blowout" not in result.dq_reason  # no double-fire; prior reason set first

    def test_exactly_at_blowup_threshold_is_dq(self):
        # end_balance = 0.10 × 10_000 = 1_000 exactly → DQ
        result = apply_gate(_mv(end_balance=1_000.0, start_balance=10_000.0), DEFAULT_GATE)
        assert result.eligible is False

    def test_above_threshold_not_blowup(self):
        # end_balance=1_001 > 1_000 → not blowout
        result = apply_gate(_mv(end_balance=1_001.0, start_balance=10_000.0), DEFAULT_GATE)
        assert result.eligible is True


class TestMultipleReasons:
    def test_all_reasons_concatenated(self):
        gate = EligibilityConfig(min_trade_count=5, stop_out_equity_pct=0.20, blowup_threshold_pct=0.10)
        mv = _mv(
            trade_count=2,
            negative_balance=True,
            end_balance=-100.0,
            equity_low_pct=0.10,
        )
        result = apply_gate(mv, gate)
        assert "under_min_trades" in result.dq_reason
        assert "negative_balance" in result.dq_reason
        assert result.eligible is False
