"""
Eligibility gate — DQ logic.

Ported from trader-elo-rater/scripts/rate.py (apply_gate). Refactored to
operate on MetricVector objects and return typed results rather than mutating
a DataFrame in place. blowup_threshold_pct is now read from EligibilityConfig
instead of being hardcoded.

DQ reasons are applied in order; all that apply are concatenated:
  1. under_min_trades   — trade_count < min_trade_count
  2. negative_balance   — end_balance < 0
  3. stop_out           — equity_low_pct <= stop_out_equity_pct (when > 0)
  4. blowout            — end_balance <= blowup_threshold_pct * start_balance
                          (fires only if no prior reason already set)
"""

from __future__ import annotations

from dataclasses import dataclass

from scoring.config import EligibilityConfig
from scoring.models import MetricVector


@dataclass
class GateResult:
    eligible: bool
    dq_reason: str   # empty string when eligible; semicolon-separated reasons when not


def apply_gate(mv: MetricVector, config: EligibilityConfig) -> GateResult:
    reasons = ""

    if mv.trade_count < config.min_trade_count:
        reasons += "under_min_trades;"

    if mv.negative_balance:
        reasons += "negative_balance;"

    if config.stop_out_equity_pct > 0 and mv.equity_low_pct <= config.stop_out_equity_pct:
        reasons += f"stop_out(<{config.stop_out_equity_pct:.0%});"

    # blowup gate fires last, only if no other reason already set
    blowup = mv.end_balance <= config.blowup_threshold_pct * mv.start_balance
    if blowup and reasons == "":
        reasons += "blowout;"

    return GateResult(eligible=(reasons == ""), dq_reason=reasons)
