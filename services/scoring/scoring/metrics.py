"""
Compute a raw metric vector from a list of Deal objects.

Pure function — no file I/O, no config, no state. Ported from
trader-elo-rater/scripts/extract.py (computation block only; file parsing
lives in adapters/xlsx.py).

Recomputes everything from raw deals. The broker's summary rows are never
trusted and are never passed into this function.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from scoring.models import Deal

LOT = 100_000  # FX standard lot size in base units


def compute_metrics(deals: list[Deal]) -> dict[str, Any]:
    """
    Return a dict of raw (non-normalized) metrics for one trader's deal list.

    The caller (engine.py) wraps this dict in a MetricVector with trader_id
    and account added. Normalization into percentile ranks and the final
    composite happen at the cohort level in normalise.py.
    """
    # equity curve: all non-null balance values in deal order
    equity = np.array([d.balance for d in deals if d.balance is not None], dtype=float)

    # start balance: first "balance"-type deal; fallback to first non-null balance
    bal_type = [d for d in deals if d.type.strip() == "balance"]
    if bal_type and bal_type[0].balance is not None:
        start_balance = float(bal_type[0].balance)
    elif len(equity):
        start_balance = float(equity[0])
    else:
        start_balance = 0.0

    end_balance = float(equity[-1]) if len(equity) else start_balance

    # realized trades: "out" direction; net = profit + swap + commission + fee
    out_deals = [d for d in deals if d.direction.strip() == "out"]
    net = np.array(
        [d.profit + d.swap + d.commission + d.fee for d in out_deals], dtype=float
    )
    n = int(len(net))

    wins = net[net > 0]
    losses = net[net < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())  # negative
    net_pl = float(net.sum())
    net_return_pct = net_pl / start_balance if start_balance else 0.0

    # drawdown off the realized equity curve
    if len(equity):
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / np.where(peak == 0, np.nan, peak)
        max_dd = float(np.nanmin(dd))
        ulcer = float(np.sqrt(np.nanmean(np.square(np.minimum(dd, 0.0)))))
    else:
        max_dd = 0.0
        ulcer = 0.0

    # per-trade risk-adjusted (unannualized, per-trade convention)
    mean_t = float(net.mean()) if n else 0.0
    std_t = float(net.std(ddof=1)) if n > 1 else 0.0
    downside = net[net < 0]
    dstd = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sharpe = mean_t / std_t if std_t else 0.0
    sortino = mean_t / dstd if dstd else 0.0

    if gross_loss != 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 0.0
    win_rate = len(wins) / n if n else 0.0
    expectancy = mean_t
    largest_loss = float(net.min()) if n else 0.0
    largest_loss_pct = largest_loss / start_balance if start_balance else 0.0
    recovery_factor = net_pl / abs(max_dd * start_balance) if max_dd != 0 else 0.0
    return_over_maxdd = net_return_pct / abs(max_dd) if max_dd != 0 else 0.0

    # leverage proxy: notional / balance for non-balance deals with volume + price + balance
    sized = [
        (d.volume, d.price, d.balance)
        for d in deals
        if d.volume is not None
        and d.price is not None
        and d.balance is not None
        and d.type.strip() != "balance"
    ]
    if sized:
        notional = np.array([v * LOT * p for v, p, _ in sized], dtype=float)
        bal_at = np.array([float(b) for _, _, b in sized], dtype=float)
        bal_at = np.where(bal_at == 0, np.nan, bal_at)
        lev_vals = notional / bal_at
        lev = lev_vals[~np.isnan(lev_vals)]
    else:
        lev = np.array([])

    peak_leverage = float(lev.max()) if len(lev) else 0.0
    lev_consistency = float(lev.std(ddof=1)) if len(lev) > 1 else 0.0

    # equity-curve linearity: R² of balance vs trade index, signed
    if len(equity) > 2:
        x = np.arange(len(equity))
        r = float(np.corrcoef(x, equity)[0, 1])
        linearity = (r * r) * (1.0 if equity[-1] >= equity[0] else -1.0)
    else:
        linearity = 0.0

    # DQ gate fields (raw; gate logic in eligibility.py)
    equity_low = float(np.nanmin(equity)) if len(equity) else start_balance
    equity_low_pct = equity_low / start_balance if start_balance else 0.0
    negative_balance = end_balance < 0

    return {
        "net_pl": round(net_pl, 2),
        "net_return_pct": round(net_return_pct, 4),
        "start_balance": round(start_balance, 2),
        "end_balance": round(end_balance, 2),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "return_over_maxdd": round(return_over_maxdd, 4),
        "max_drawdown": round(max_dd, 4),
        "ulcer_index": round(ulcer, 4),
        "recovery_factor": round(recovery_factor, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != math.inf else 999.0,
        "payoff_ratio": round(payoff_ratio, 4),
        "expectancy": round(expectancy, 4),
        "win_rate": round(win_rate, 4),
        "largest_loss_pct": round(largest_loss_pct, 4),
        "trade_return_std": round(std_t, 4),
        "equity_linearity": round(linearity, 4),
        "trade_count": n,
        "peak_leverage": round(peak_leverage, 2),
        "leverage_consistency": round(lev_consistency, 2),
        "equity_low_pct": round(equity_low_pct, 4),
        "negative_balance": bool(negative_balance),
    }
