"""
Core data types for the scoring service.

Deal is the canonical input shape — both the xlsx adapter and the postgres
adapter must produce list[Deal]. All scoring functions downstream are
adapter-agnostic.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Deal:
    time: datetime
    direction: str        # "in" | "out" | "balance"
    type: str             # raw MT5 type string, lowercased
    volume: Optional[float]   # lot size; None for balance events
    price: Optional[float]    # execution price; None for balance events
    commission: float     # 0.0 when absent
    fee: float            # 0.0 when absent
    swap: float           # 0.0 when absent
    profit: float         # realized P/L for "out" deals; 0.0 otherwise
    balance: Optional[float]  # running balance after event; None on "in" deals


@dataclass(frozen=True)
class MetricVector:
    """All metrics computed from raw deals for one trader."""

    trader_id: str
    account: Optional[str]
    # return & alpha
    net_pl: float
    net_return_pct: float
    start_balance: float
    end_balance: float
    # risk-adjusted return
    sharpe: float
    sortino: float
    return_over_maxdd: float
    # capital preservation
    max_drawdown: float       # stored negative; closer to 0 = better
    ulcer_index: float
    recovery_factor: float
    # payoff & tail discipline
    profit_factor: float
    payoff_ratio: float
    expectancy: float
    win_rate: float           # context only; never a standalone pillar weight
    largest_loss_pct: float   # stored negative; closer to 0 = better
    # consistency
    trade_return_std: float
    equity_linearity: float
    # behavioral hygiene
    trade_count: int
    peak_leverage: float
    leverage_consistency: float
    # DQ gate fields (raw values; gate logic lives in eligibility.py)
    equity_low_pct: float     # min(equity) / start_balance
    negative_balance: bool    # end_balance < 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class TraderResult:
    """Fully scored trader — metrics + composite + Glicko rating."""

    rank: int
    trader_id: str
    account: Optional[str]
    state: str
    tier: str
    league_rating: float
    R: float
    RD: float
    vol: float
    composite: float
    pillar_scores: dict[str, float]   # pillar_name -> score (feeds "Why this rank")
    metrics: MetricVector
    eligible: bool
    dq_reason: str


@dataclass
class ScoringResult:
    """Output of score_cohort()."""

    # Eligible traders sorted by league_rating desc (rank 1 = best).
    # When include_dq=True, DQ'd traders are appended at the end sorted by
    # league_rating within the DQ group.
    leaderboard: list[TraderResult]
    # All DQ'd traders regardless of include_dq (for transparency).
    # Has Glicko data only when include_dq=True (they participated in scoring).
    dq_traders: list[TraderResult]
    # Carry-forward state for next round: {trader_id: {"rating", "rd", "vol"}}
    ratings: dict[str, dict[str, float]]
