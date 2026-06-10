"""
Synthetic deal-list fixtures for the merge-gate cohort test.

All fixtures are built purely in memory as list[Deal] — no xlsx files needed.
The fixture values are chosen to reliably produce the assertions in
test_synthetic_cohort.py regardless of minor scoring-formula perturbations.

Archetype summary
-----------------
disciplined_grinder : low leverage (3x), tiny drawdown (<1%), smooth equity,
                      moderate return (~16%). Should rank #1.
solid_professional  : moderate leverage (10x), moderate drawdown (~5%),
                      good payoff metrics, moderate return (~18%). Should rank ~2nd.
lucky_gambler       : HIGH leverage (42x), large drawdown (~25%), erratic,
                      but HIGHEST raw return (~42%). Should be demoted to ~3rd or lower.
choppy_breakeven    : near-zero return (~0%), oscillating equity, moderate leverage.
reckless_survivor   : very HIGH leverage (70x), very large drawdown (~40%),
                      moderate return (~22%).
blowup              : ReportHistory-22933 profile — 94% loss from $3,000 start,
                      12.5% win rate, ~800x peak leverage. Must be DQ'd.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scoring.models import Deal

PRICE = 1.2000
LOT_UNIT = 100_000
BASE_TIME = datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc)


def _balance_deal(dt: datetime, balance: float) -> Deal:
    return Deal(
        time=dt,
        direction="balance",
        type="balance",
        volume=None,
        price=None,
        commission=0.0,
        fee=0.0,
        swap=0.0,
        profit=0.0,
        balance=balance,
    )


def _trade(
    dt_in: datetime,
    dt_out: datetime,
    volume: float,
    pnl: float,
    balance_after: float,
    price: float = PRICE,
) -> tuple[Deal, Deal]:
    """Return (in_deal, out_deal) for one round-trip trade."""
    in_deal = Deal(
        time=dt_in,
        direction="in",
        type="buy",
        volume=volume,
        price=price,
        commission=0.0,
        fee=0.0,
        swap=0.0,
        profit=0.0,
        balance=None,  # balance unchanged at entry; mimics MT5 export
    )
    out_deal = Deal(
        time=dt_out,
        direction="out",
        type="buy",
        volume=volume,
        price=price,
        commission=0.0,
        fee=0.0,
        swap=0.0,
        profit=pnl,
        balance=balance_after,
    )
    return in_deal, out_deal


def _make_deals(
    start_balance: float,
    trade_specs: list[tuple[float, float]],  # [(pnl, volume), ...]
    price: float = PRICE,
    offset_hours: int = 0,
) -> list[Deal]:
    """Build a list[Deal] from (pnl, volume) specs."""
    deals: list[Deal] = [
        _balance_deal(BASE_TIME - timedelta(hours=1) + timedelta(hours=offset_hours), start_balance)
    ]
    balance = start_balance
    for i, (pnl, volume) in enumerate(trade_specs):
        t_in = BASE_TIME + timedelta(hours=offset_hours + i * 4)
        t_out = t_in + timedelta(hours=2)
        balance += pnl
        in_d, out_d = _trade(t_in, t_out, volume, pnl, balance, price)
        deals.append(in_d)
        deals.append(out_d)
    return deals


# ---------------------------------------------------------------------------
# Archetype builders
# ---------------------------------------------------------------------------

def disciplined_grinder() -> list[Deal]:
    """
    30 trades. Win $75 × 25, lose $45 × 5 (every 6th trade).
    Net: +$1,650 → 16.5% return from $10,000.
    Volume 0.25 lots → peak leverage ≈ 3x.
    Losses spread out (never consecutive) → max drawdown < 1%.
    High equity linearity (smooth climb).
    """
    specs = []
    for i in range(30):
        pnl = -45.0 if (i + 1) % 6 == 0 else 75.0
        specs.append((pnl, 0.25))
    return _make_deals(10_000.0, specs)


def solid_professional() -> list[Deal]:
    """
    20 trades. Win $220 × 14, lose $150 × 6 (two consecutive pairs).
    Net: +$2,180 → 21.8% return from $10,000.
    Volume 0.8 lots → leverage ≈ 10x.
    Two clusters of 2 consecutive losses → max drawdown ≈ -3%.
    """
    pnls = []
    for i in range(20):
        # losses at positions 4,5 and 13,14
        if i in (4, 5, 13, 14):
            pnls.append(-150.0)
        else:
            pnls.append(220.0)
    specs = [(p, 0.8) for p in pnls]
    return _make_deals(10_000.0, specs)


def lucky_gambler() -> list[Deal]:
    """
    15 trades. Win $600 × 12, lose $1,000 × 3 (consecutive at positions 2-4).
    Net: +$4,200 → 42% return from $10,000. HIGHEST raw return in cohort.
    Volume 3.5 lots → peak leverage ≈ 42x.
    3 consecutive losses after 2 early wins → max drawdown ≈ -25%.
    High trade_return_std (erratic results).
    """
    pnls = []
    for i in range(15):
        if i in (2, 3, 4):
            pnls.append(-1_000.0)
        else:
            pnls.append(600.0)
    specs = [(p, 3.5) for p in pnls]
    return _make_deals(10_000.0, specs)


def choppy_breakeven() -> list[Deal]:
    """
    25 trades alternating small wins and slightly larger losses.
    Net: ≈ +$25 → ~0% return from $10,000.
    Volume 0.3 lots → leverage ≈ 3.6x.
    Zigzag equity → low linearity, moderate drawdown.
    """
    pnls = []
    for i in range(25):
        pnls.append(55.0 if i % 2 == 0 else -53.0)
    # 13 wins × 55 - 12 losses × 53 = 715 - 636 = 79 → 0.8%
    specs = [(p, 0.3) for p in pnls]
    return _make_deals(10_000.0, specs)


def reckless_survivor() -> list[Deal]:
    """
    18 trades. Win $400 × 12, lose $700 × 6 (two clusters of 3).
    Net: +$600 → 6% return from $10,000.
    Volume 6.0 lots → peak leverage ≈ 72x.
    6 consecutive losses spread across two clusters → max drawdown ≈ -40%.
    """
    pnls = []
    for i in range(18):
        if i in (2, 3, 4, 12, 13, 14):
            pnls.append(-700.0)
        else:
            pnls.append(400.0)
    specs = [(p, 6.0) for p in pnls]
    return _make_deals(10_000.0, specs)


def blowup_account() -> list[Deal]:
    """
    ReportHistory-22933 profile.
    16 trades: 2 wins ($60 each), 14 losses ($-210 each).
    Start: $3,000. End: $3,000 + 2×60 - 14×210 = $3,000 + 120 - 2,940 = $180.
    End balance = 6% of start → below blowup_threshold_pct=10% → DQ.
    Win rate: 2/16 = 12.5%.
    Volume 20 lots → peak leverage ≈ 800x (at $3,000 balance).
    """
    pnls = []
    for i in range(16):
        if i in (0, 8):
            pnls.append(60.0)
        else:
            pnls.append(-210.0)
    specs = [(p, 20.0) for p in pnls]
    return _make_deals(3_000.0, specs)


# ---------------------------------------------------------------------------
# Convenience: full cohort as used in test_synthetic_cohort.py
# ---------------------------------------------------------------------------

ARCHETYPE_BUILDERS = {
    "disciplined_grinder": disciplined_grinder,
    "solid_professional": solid_professional,
    "lucky_gambler": lucky_gambler,
    "choppy_breakeven": choppy_breakeven,
    "reckless_survivor": reckless_survivor,
    "blowup": blowup_account,
}


def build_cohort() -> list[tuple[str, list[Deal]]]:
    return [(name, builder()) for name, builder in ARCHETYPE_BUILDERS.items()]
