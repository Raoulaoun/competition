# Deal — Canonical Contract

**Single source of truth for the normalized deal shape consumed by `services/scoring`.**

The `raw_deals` Postgres table (written by `services/ingestion` in Phase 1) **conforms
to this shape**. The xlsx adapter in `services/scoring/scoring/adapters/xlsx.py` also
produces this shape. Both paths emit the same Python dataclass; `engine.py` is
adapter-agnostic.

---

## Python dataclass (`scoring/models.py`)

```python
@dataclass(frozen=True)
class Deal:
    time:       datetime      # timestamp of the deal event (UTC)
    direction:  str           # "in" | "out" | "balance"
    type:       str           # raw MT5 type string (e.g. "buy", "sell", "balance")
    volume:     float | None  # lot size; None for balance/deposit events
    price:      float | None  # execution price; None for balance events
    commission: float         # 0.0 when absent
    fee:        float         # 0.0 when absent
    swap:       float         # 0.0 when absent
    profit:     float         # realized P/L for "out" deals; 0.0 otherwise
    balance:    float | None  # running account balance after the event;
                              # None on "in" deals (balance unchanged at entry)
```

---

## Field semantics

| Field | Source in MT5 export | Notes |
|---|---|---|
| `time` | `Time` column | Parse to UTC datetime |
| `direction` | `Direction` column, lowercased and stripped | `"in"` = position opened, `"out"` = position closed, `"balance"` = deposit/withdrawal |
| `type` | `Type` column, lowercased and stripped | `"buy"`, `"sell"`, `"balance"`, etc. |
| `volume` | `Volume` column | Standard lots; `None` for balance events |
| `price` | `Price` column | `None` for balance events |
| `commission` | `Commission` column | Default 0.0; included in net P/L |
| `fee` | `Fee` column | Default 0.0; included in net P/L |
| `swap` | `Swap` column | Default 0.0; included in net P/L |
| `profit` | `Profit` column | Raw MT5 realized profit for `"out"` deals |
| `balance` | `Balance` column | Running balance *after* this event; `None` on `"in"` deals |

---

## Net P/L per trade

The scoring service computes net per closed trade as:

```
net = profit + swap + commission + fee
```

This is computed from raw deal fields only — **never from the broker's summary rows**.

---

## Postgres `raw_deals` column mapping (Phase 1)

| Deal field | Column name | Type | Nullable |
|---|---|---|---|
| `time` | `time` | `timestamptz` | NOT NULL |
| `direction` | `direction` | `text` | NOT NULL |
| `type` | `type` | `text` | NOT NULL |
| `volume` | `volume` | `numeric` | NULL |
| `price` | `price` | `numeric` | NULL |
| `commission` | `commission` | `numeric` | NOT NULL DEFAULT 0 |
| `fee` | `fee` | `numeric` | NOT NULL DEFAULT 0 |
| `swap` | `swap` | `numeric` | NOT NULL DEFAULT 0 |
| `profit` | `profit` | `numeric` | NOT NULL DEFAULT 0 |
| `balance` | `balance` | `numeric` | NULL |

Additional columns Phase 1 adds for routing/scoping (not part of the scoring contract):
`id`, `account_id`, `round_id`, `tenant_id`, `ingested_at`.

---

## Invariants

- A deal list for one account always starts with at least one `direction="balance"` deal
  carrying the initial deposit in `balance`. The scoring service uses this as `start_balance`.
- `"in"` deals have `balance=None` (balance is unchanged at position entry in MT5).
- `"out"` deals always have `balance` set (post-close running balance).
- `volume` and `price` are set on all non-balance deals; used for the leverage proxy.
- The scoring service **recomputes everything from raw deals**. It never reads the
  broker's `Results` summary block or any aggregated field.
