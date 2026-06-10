"""
MT5 ReportHistory xlsx adapter.

Ported from trader-elo-rater/scripts/extract.py (load_deals only — metric
computation lives in metrics.py). Parses the Deals section of an MT5
ReportHistory export and returns list[Deal].

MT5 ReportHistory layout:
- Header block: Name/Account at column index 3
- "Deals" marker in column 0, immediately followed by a 14-column header row
- Deals rows until a "Results" marker in column 0 (or end of sheet)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scoring.models import Deal


def load_deals(path: str | Path) -> tuple[list[Deal], str | None, str | None]:
    """
    Parse an MT5 ReportHistory xlsx file.

    Returns (deals, name, account).
    Raises ValueError if the Deals section is not found.
    """
    raw = pd.read_excel(path, sheet_name=0, header=None)
    col0 = raw.iloc[:, 0].astype(str)

    deals_rows = col0[col0.str.strip() == "Deals"].index
    if len(deals_rows) == 0:
        raise ValueError(f"{path}: no 'Deals' section — not an MT5 ReportHistory export?")
    deals_row = deals_rows[0]
    header_row = deals_row + 1

    # deals run until "Results" marker or end of sheet
    end = len(raw)
    for r in range(header_row + 1, len(raw)):
        if str(raw.iloc[r, 0]).strip() == "Results":
            end = r
            break

    cols = [str(c).strip() for c in raw.iloc[header_row].tolist()]
    df = raw.iloc[header_row + 1 : end].copy()
    df.columns = cols
    df = df[df["Time"].notna()]

    # name and account from header block (column index 3)
    name: str | None = None
    acct: str | None = None
    for r in range(0, deals_row):
        label = str(raw.iloc[r, 0]).strip().rstrip(":")
        if label == "Name":
            name = str(raw.iloc[r, 3])
        elif label == "Account":
            acct = str(raw.iloc[r, 3]).split()[0]

    for c in ["Volume", "Price", "Commission", "Fee", "Swap", "Profit", "Balance"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    deals: list[Deal] = []
    for _, row in df.iterrows():
        t = row.get("Time")
        if isinstance(t, pd.Timestamp):
            t = t.to_pydatetime()
        elif not isinstance(t, datetime):
            try:
                t = pd.to_datetime(t).to_pydatetime()
            except Exception:
                continue

        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)

        def _f(col: str, default: float = 0.0) -> float:
            v = row.get(col)
            return float(v) if pd.notna(v) else default

        def _fn(col: str) -> float | None:
            v = row.get(col)
            return float(v) if pd.notna(v) else None

        deals.append(
            Deal(
                time=t,
                direction=str(row.get("Direction", "")).strip().lower(),
                type=str(row.get("Type", "")).strip().lower(),
                volume=_fn("Volume"),
                price=_fn("Price"),
                commission=_f("Commission"),
                fee=_f("Fee"),
                swap=_f("Swap"),
                profit=_f("Profit"),
                balance=_fn("Balance"),
            )
        )

    return deals, name, acct
