"""
Extract a raw metric vector from a single MetaTrader 5 'ReportHistory' export.

Parses the DEALS section (ground truth) and recomputes every metric from raw
deals -- the broker's own 'Results' block is never trusted. Output is a flat
dict of raw (non-normalized) metrics. Normalization into z-scores and the final
composite happen at the cohort level in rate.py, because a rating only exists
relative to the other traders in the same round.

FX standard lot = 100,000 base units. Notional and leverage proxies are computed
on a consistent formula so they are comparable WITHIN a cohort (relative risk),
not as exact USD margin figures.
"""

import sys
import json
import math
import numpy as np
import pandas as pd

LOT = 100_000


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


def load_deals(path):
    raw = pd.read_excel(path, sheet_name=0, header=None)
    col0 = raw.iloc[:, 0].astype(str)

    deals_row = col0[col0.str.strip() == "Deals"].index
    if len(deals_row) == 0:
        raise ValueError(f"{path}: no 'Deals' section found -- not an MT5 ReportHistory export?")
    deals_row = deals_row[0]
    header_row = deals_row + 1

    # data runs until the next section header (e.g. 'Results') or end of sheet
    end = len(raw)
    for r in range(header_row + 1, len(raw)):
        v = str(raw.iloc[r, 0]).strip()
        if v == "Results":
            end = r
            break

    cols = [str(c).strip() for c in raw.iloc[header_row].tolist()]
    df = raw.iloc[header_row + 1:end].copy()
    df.columns = cols
    df = df[df["Time"].notna()]

    # account holder / id from the header block
    name = None
    acct = None
    for r in range(0, deals_row):
        label = str(raw.iloc[r, 0]).strip().rstrip(":")
        if label == "Name":
            name = str(raw.iloc[r, 3])
        if label == "Account":
            acct = str(raw.iloc[r, 3]).split()[0]
    return df, name, acct


def extract(path):
    df, name, acct = load_deals(path)

    for c in ["Volume", "Price", "Commission", "Fee", "Swap", "Profit", "Balance"]:
        if c in df.columns:
            df[c] = _to_num(df[c])

    bal_rows = df[df["Type"].astype(str).str.strip() == "balance"]
    start_balance = float(bal_rows["Balance"].iloc[0]) if len(bal_rows) else float(df["Balance"].dropna().iloc[0])

    # equity curve from realized balance after each event
    equity = df["Balance"].dropna().astype(float).values
    end_balance = float(equity[-1])

    # realized trades = 'out' deals; net = profit + swap + commission + fee
    out = df[df["Direction"].astype(str).str.strip() == "out"].copy()
    for c in ["Profit", "Swap", "Commission", "Fee"]:
        if c not in out.columns:
            out[c] = 0.0
    out[["Profit", "Swap", "Commission", "Fee"]] = out[["Profit", "Swap", "Commission", "Fee"]].fillna(0.0)
    net = (out["Profit"] + out["Swap"] + out["Commission"] + out["Fee"]).values.astype(float)

    n = len(net)
    wins = net[net > 0]
    losses = net[net < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())  # negative
    net_pl = float(net.sum())
    net_return_pct = net_pl / start_balance if start_balance else 0.0

    # drawdown off the realized equity curve
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak == 0, np.nan, peak)
    max_dd = float(np.nanmin(dd)) if len(dd) else 0.0
    ulcer = float(np.sqrt(np.nanmean(np.square(np.minimum(dd, 0.0))))) if len(dd) else 0.0

    # per-trade risk-adjusted (unannualized, per-trade convention)
    mean_t = float(net.mean()) if n else 0.0
    std_t = float(net.std(ddof=1)) if n > 1 else 0.0
    downside = net[net < 0]
    dstd = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sharpe = mean_t / std_t if std_t else 0.0
    sortino = mean_t / dstd if dstd else 0.0

    profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else (math.inf if gross_profit > 0 else 0.0)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 0.0
    win_rate = len(wins) / n if n else 0.0
    expectancy = mean_t
    largest_loss = float(net.min()) if n else 0.0
    largest_loss_pct = largest_loss / start_balance if start_balance else 0.0
    recovery_factor = net_pl / abs(max_dd * start_balance) if max_dd != 0 else 0.0
    return_over_maxdd = net_return_pct / abs(max_dd) if max_dd != 0 else 0.0

    # leverage / sizing proxy: notional / balance at the time of each deal
    sized = df[df["Volume"].notna() & df["Price"].notna() & (df["Type"].astype(str).str.strip() != "balance")].copy()
    notional = (sized["Volume"] * LOT * sized["Price"]).astype(float)
    bal_at = sized["Balance"].astype(float).replace(0, np.nan)
    lev = (notional / bal_at).dropna()
    peak_leverage = float(lev.max()) if len(lev) else 0.0
    lev_consistency = float(lev.std(ddof=1)) if len(lev) > 1 else 0.0  # lower = steadier sizing

    # equity-curve linearity: R^2 of balance vs trade index (smooth climber scores high)
    if len(equity) > 2:
        x = np.arange(len(equity))
        r = np.corrcoef(x, equity)[0, 1]
        linearity = float(r * r) * (1 if equity[-1] >= equity[0] else -1)
    else:
        linearity = 0.0

    # equity low point as a fraction of starting balance (for stop-out gate)
    equity_low = float(np.nanmin(equity)) if len(equity) else start_balance
    equity_low_pct = equity_low / start_balance if start_balance else 0.0

    # stop-out signals (gate aligned to reality, evaluated against config in rate.py)
    negative_balance = end_balance < 0
    blowup = negative_balance or (end_balance <= 0.10 * start_balance)

    return {
        "trader": name or acct or path,
        "account": acct,
        "source_file": path,
        # return & alpha
        "net_pl": round(net_pl, 2),
        "net_return_pct": round(net_return_pct, 4),
        "start_balance": round(start_balance, 2),
        "end_balance": round(end_balance, 2),
        # risk-adjusted
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "return_over_maxdd": round(return_over_maxdd, 4),
        # drawdown & preservation
        "max_drawdown": round(max_dd, 4),
        "ulcer_index": round(ulcer, 4),
        "recovery_factor": round(recovery_factor, 4),
        # payoff & tail discipline
        "profit_factor": round(profit_factor, 4) if profit_factor != math.inf else 999.0,
        "payoff_ratio": round(payoff_ratio, 4),
        "expectancy": round(expectancy, 4),
        "win_rate": round(win_rate, 4),
        "largest_loss_pct": round(largest_loss_pct, 4),
        # consistency
        "trade_return_std": round(std_t, 4),
        "equity_linearity": round(linearity, 4),
        # behavioral hygiene
        "trade_count": n,
        "peak_leverage": round(peak_leverage, 2),
        "leverage_consistency": round(lev_consistency, 2),
        # gate
        "equity_low_pct": round(equity_low_pct, 4),
        "negative_balance": bool(negative_balance),
        "blowup_flag": bool(blowup),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python extract.py <mt5_report.xlsx>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(extract(sys.argv[1]), indent=2))
