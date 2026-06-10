# Phase 1 Plan — Data Pipeline (Ingestion Worker)

**Goal:** One demo account's deals landing in Postgres, repeatably and idempotently.  
**Done when:** pointing the worker at a single demo account produces correct rows in
`raw_deals`, and re-running it produces no duplicates.

---

## Assumptions and constraints to confirm before implementing

### MT5 API path — the highest-risk item in this phase

MT5 exposes two server-side APIs. The choice determines the host OS and what
the worker can actually pull. **Confirm your broker's access tier before writing
a line of integration code.**

| API | What it gives you | Host constraint | Credential type |
|---|---|---|---|
| **MT5 Manager API** (preferred) | Full privileged access: all accounts, all deals, real-time push | **Windows only** — the Manager API DLL is Windows-only; cannot run on Linux/macOS natively | Manager login + password + server address |
| **MT5 Web API** | REST/WebSocket over HTTPS; subset of Manager API data | Any OS (HTTP client) | Depends on broker config — may require Manager-level credentials or per-account access |
| **Per-account MetaTrader5 Python lib** (`MetaTrader5` pip package) | Single-account pull via a running MT5 terminal | **Windows only** — the Python lib shells out to a local MT5 terminal installation | Investor or full account login + password |

**Plan is written for the MT5 Python library path (`MetaTrader5` package) as the
default**, because it is the most commonly available and does not require a
Manager API license. If your broker provides the Manager API or Web API, the
adapter layer swaps out cleanly — the rest of the worker is identical.

**Key MT5 Python lib constraints to confirm:**
1. Must run on a **Windows host** (the lib launches `terminal64.exe` in headless mode).
2. The MT5 terminal installation must already exist on the host (the lib attaches to it).
3. Credentials are `login` (account number), `password`, and `server` (broker server name).
4. The lib can pull `copy_deals_range(from_date, to_date)` for the authenticated account.
5. Investor-password access gives read-only access to one account — sufficient for ingestion.

**If your broker only offers Web API:** flag before implementing and I'll write an
HTTP adapter instead. The Supabase migration, idempotency logic, and worker shell
are identical either way.

---

## Module layout

```
services/ingestion/
├── pyproject.toml               # ruff, black, pytest; package: ingestion
├── worker/
│   ├── __init__.py
│   ├── main.py                  # entry point: python -m worker
│   ├── config.py                # reads env vars → typed WorkerConfig (no defaults for secrets)
│   ├── mt5_adapter.py           # MT5 connection + deal pull → list[RawDeal]
│   ├── transform.py             # RawDeal → Deal (canonical shape from /docs/contracts/deal.md)
│   ├── db.py                    # upsert to raw_deals via psycopg / supabase-py
│   └── run_once.py              # pull + transform + upsert for one account; used by main + tests
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_transform.py        # unit: RawDeal → Deal shape, edge cases
    ├── test_db.py               # unit: idempotency logic (mock DB)
    └── test_run_once.py         # integration: mock MT5 → mock DB, end-to-end
```

---

## Supabase migration

File: `db/supabase/migrations/<timestamp>_create_raw_deals.sql`

```sql
-- Raw deals written by services/ingestion.
-- Schema conforms to /docs/contracts/deal.md.
-- Downstream scoring recomputes everything from these rows;
-- broker summary figures are never stored here.

create table if not exists raw_deals (
    id             bigserial primary key,

    -- scoping (tenant isolation + round routing)
    tenant_id      uuid        not null,
    account_id     text        not null,
    round_id       text        not null,

    -- Deal contract fields (see /docs/contracts/deal.md)
    time           timestamptz not null,
    direction      text        not null,   -- "in" | "out" | "balance"
    type           text        not null,   -- "buy" | "sell" | "balance" | etc.
    volume         numeric,               -- null for balance events
    price          numeric,               -- null for balance events
    commission     numeric     not null default 0,
    fee            numeric     not null default 0,
    swap           numeric     not null default 0,
    profit         numeric     not null default 0,
    balance        numeric,               -- null on "in" deals

    -- provenance
    mt5_deal_id    bigint,                -- broker's own deal ticket; null if unavailable
    ingested_at    timestamptz not null default now()
);

-- Idempotency index: one row per (tenant, account, round, MT5 deal ticket).
-- Used by the upsert in db.py.
-- NOTE: mt5_deal_id may be null for balance/deposit events that have no ticket.
-- Those are de-duplicated by (account_id, round_id, time, direction) instead.
create unique index raw_deals_dedup_ticket
    on raw_deals (tenant_id, account_id, round_id, mt5_deal_id)
    where mt5_deal_id is not null;

create unique index raw_deals_dedup_no_ticket
    on raw_deals (tenant_id, account_id, round_id, time, direction, type)
    where mt5_deal_id is null;

-- RLS: every query must be scoped to the authenticated tenant.
alter table raw_deals enable row level security;

create policy raw_deals_tenant_isolation
    on raw_deals
    using (tenant_id = current_setting('app.tenant_id')::uuid);
```

**Why two unique indexes:**  
MT5 deal tickets (`mt5_deal_id`) are unique per account on the broker's side, making
them the cleanest idempotency key. However, balance/deposit events (direction="balance")
often do not carry a deal ticket — these fall back to the composite key
`(account_id, round_id, time, direction, type)`. The two partial indexes enforce
this cleanly without a NULL-equality hack.

---

## Credentials and secrets — hard rules

All MT5 credentials and the Supabase service-role key live in environment variables
only. They are:
- **Never** committed (`.env*` is in `.gitignore`)
- **Never** logged — the worker must not log any credential field, even at DEBUG level
- **Never** printed to stdout in any error path
- **Never** passed as CLI arguments (visible in `ps aux`)
- **Only** loaded server-side, never touched by any web/API package

Required env vars:

```
# MT5 connection (per-account investor or manager credentials)
MT5_LOGIN=<account number>
MT5_PASSWORD=<password>
MT5_SERVER=<broker server name>

# Supabase (service-role key — server-side only, never the anon key)
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service role key>

# Scoping for this run
WORKER_TENANT_ID=<uuid>
WORKER_ACCOUNT_ID=<string>
WORKER_ROUND_ID=<string>

# Optional: pull window (defaults to full history if omitted)
WORKER_FROM_DATE=2024-01-01     # ISO date
WORKER_TO_DATE=2024-12-31       # ISO date
```

`config.py` reads these at startup and raises `ValueError` with a safe message
(naming the missing var but **not** its value) if any required var is absent.
No default values for credential fields — fail loud rather than silently connect
to nothing.

---

## Worker pipeline (`run_once.py`)

```
connect_mt5(config) → pull_deals(account, from_date, to_date) → list[RawDeal]
    ↓
transform(raw_deals) → list[Deal]           # canonical shape from /docs/contracts/deal.md
    ↓
upsert(deals, tenant_id, account_id, round_id, conn)   # idempotent
    ↓
return UpsertStats(inserted, skipped, errors)
```

Each step is a separate function so unit tests can mock any layer independently.

### Step 1 — MT5 connection (`mt5_adapter.py`)

```python
def connect(config: WorkerConfig) -> None:
    """Initialize the MT5 terminal connection. Raises on failure."""
    ok = mt5.initialize(
        login=config.mt5_login,
        password=config.mt5_password,   # never logged
        server=config.mt5_server,
    )
    if not ok:
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
        # mt5.last_error() returns (code, message); message safe to log,
        # does not contain credentials

def pull_deals(from_dt: datetime, to_dt: datetime) -> list[RawDeal]:
    """Pull closed deals for the connected account within [from_dt, to_dt]."""
    deals = mt5.copy_deals_range(from_dt, to_dt, mt5.DEAL_TYPE_SELL | mt5.DEAL_TYPE_BUY)
    # also pull balance operations separately
    balance_deals = mt5.copy_deals_range(from_dt, to_dt, mt5.DEAL_TYPE_BALANCE)
    ...
```

**MT5 deal fields available via the Python lib:**
`ticket`, `order`, `time`, `time_msc`, `type`, `entry` (0=in, 1=out, 2=inout),
`magic`, `position_id`, `reason`, `volume`, `price`, `commission`, `swap`,
`profit`, `fee`, `symbol`, `comment`, `external_id`.

Mapping to canonical Deal:
- `time` → `Deal.time` (convert from Unix epoch, localize to UTC)
- `entry` (0=in, 1=out) → `Deal.direction`; balance deals → `"balance"`
- `type` (0=buy, 1=sell, 2=balance, …) → `Deal.type` (string label)
- `volume` → `Deal.volume` (0.0 on balance deals → store as None)
- `price` → `Deal.price` (0.0 on balance deals → store as None)
- `commission`, `swap`, `profit`, `fee` → direct
- Running `balance` — **not available directly from deal objects**; must be
  reconstructed by cumulative sum (see note below)
- `ticket` → `mt5_deal_id`

**⚠️ Balance reconstruction note:**  
The `MetaTrader5` Python lib's `copy_deals_range()` returns individual deal
structs that do NOT include a running balance field (unlike the xlsx export's
`Balance` column). The running balance must be reconstructed:
1. Pull all deals for the account sorted by `time` ascending.
2. Start from the first balance operation's `profit` field (that's the initial deposit amount).
3. Accumulate: `balance[i] = balance[i-1] + profit[i] + commission[i] + swap[i] + fee[i]`.
4. Store the computed balance on each "out" and "balance" deal; store NULL on "in" deals.

This is equivalent to what the xlsx export's Balance column contains. The scoring
service's `compute_metrics()` depends on this being correct — verify it matches the
xlsx path for the same account before declaring Phase 1 done.

**Alternative if balance reconstruction is unreliable:** call
`account_info().balance` at the end of the pull as a sanity check — it should equal
the last computed balance. If it doesn't, log a warning with the discrepancy (not
the credential) and abort the upsert.

### Step 2 — Transform (`transform.py`)

Pure function. No I/O. Converts `list[RawDeal]` (MT5 structs) to `list[Deal]`
(canonical contract from `/docs/contracts/deal.md`). Handles:
- Epoch-to-UTC datetime conversion
- `entry` int → direction string
- `type` int → type string (using the MT5 type enum mapping)
- volume/price 0.0 → None for balance events
- balance NULL on "in" deals
- All numeric fields default to 0.0 when absent/NaN

### Step 3 — Upsert (`db.py`)

```python
def upsert_deals(
    deals: list[Deal],
    tenant_id: str,
    account_id: str,
    round_id: str,
    mt5_deal_ids: list[int | None],   # parallel to deals list
    conn,
) -> UpsertStats:
```

Uses `INSERT ... ON CONFLICT DO NOTHING`:
```sql
INSERT INTO raw_deals
    (tenant_id, account_id, round_id, time, direction, type,
     volume, price, commission, fee, swap, profit, balance,
     mt5_deal_id)
VALUES %s
ON CONFLICT DO NOTHING;
```

The unique indexes handle conflict detection. `ON CONFLICT DO NOTHING` means
re-running the worker on the same date range is fully idempotent — already-present
deals are silently skipped. Returns counts of inserted vs. skipped for the operator
log (no credential data in these counts).

**Connection:** uses `psycopg` (psycopg3) with the Supabase connection string built
from `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. The service-role key bypasses
RLS — this is correct for the ingestion worker (server-side only, never exposed to
a client). The `app.tenant_id` session variable is set immediately after connecting
so RLS policies on reads also apply if the worker ever queries back.

### Step 4 — Entry point (`main.py`)

```python
# python -m worker
config = load_config()          # reads env; raises on missing creds
connect_mt5(config)
try:
    stats = run_once(config)
    log.info("done: inserted=%d skipped=%d", stats.inserted, stats.skipped)
finally:
    mt5.shutdown()
```

Structured JSON logging (no credential fields, no raw error strings that might
contain them). Exit code 0 on success, non-zero on any failure.

---

## Scheduling

Phase 1 runs the worker **manually** (one-shot, invoked by the operator to verify
deals land). Scheduling (cron / Windows Task Scheduler / a loop) is a Phase 3
concern once the full pipeline is proven end-to-end.

---

## Tests

### `test_transform.py` — pure unit tests, no MT5, no DB

- Deposit deal (type=balance): `volume=None`, `price=None`, `direction="balance"` ✓
- "in" deal: `balance=None` ✓
- "out" deal: `balance` set (reconstructed) ✓
- Epoch timestamp → UTC datetime ✓
- Zero volume/price fields → None ✓
- All numeric fields default to 0.0 ✓
- Determinism: same raw deals → identical Deal list ✓

### `test_db.py` — mock DB, no MT5

- First insert: `inserted=1, skipped=0`
- Re-insert same deal: `inserted=0, skipped=1` (idempotency)
- Insert mixed batch (some new, some seen): correct counts
- Balance deal (no ticket) uses composite key, not ticket ✓
- DB error is raised (not swallowed) ✓

### `test_run_once.py` — mock MT5 + mock DB, end-to-end

- Mock returns a small fixture cohort (5–10 deals, one deposit + trades)
- First run: all deals inserted
- Second run (same date range): all skipped, no duplicates
- Balance reconstruction: running balance matches cumulative sum ✓
- Worker exits cleanly (MT5 shutdown called in finally block) ✓

**No test ever touches real credentials or a real MT5 connection.**

---

## Verification procedure (manual, once implemented)

1. Set all env vars in a `.env` file (not committed — covered by `.gitignore`).
2. Run `python -m worker` on the Windows host.
3. Confirm in Supabase dashboard (or `psql`):
   ```sql
   select count(*), min(time), max(time)
   from raw_deals
   where account_id = '<your account>';
   ```
4. Run `python -m worker` a second time with the same date range.
5. Confirm row count is unchanged (idempotency verified).
6. Open the account's MT5 terminal, compare deal count and first/last timestamps.
7. Optional: run the xlsx adapter on the same account's ReportHistory export and
   compare the Deal list field-by-field — they should be identical, confirming the
   balance reconstruction is correct.

---

## Open questions for your review

1. **MT5 API access confirmed?** The plan assumes the `MetaTrader5` Python lib
   (Windows host, investor-password access to one account). If your broker provides
   the Manager API or a Web API endpoint instead, I need to know before implementing
   — the adapter layer changes, everything else stays the same.

2. **Windows host available?** The Python lib requires a Windows machine with MT5
   terminal installed. Do you have a Windows VM / dedicated host ready, or do we
   need to plan for the Web API as a Linux-compatible fallback?

3. **`round_id` assignment** — when the worker runs, how does it know which round
   the deals belong to? Options:
   - A) `WORKER_ROUND_ID` env var set manually by the operator per run (simplest for Phase 1)
   - B) The worker queries a `rounds` table to find the active round for this tenant
      (requires a `rounds` table, which isn't built yet)
   - Recommendation: **Option A for Phase 1** — operator sets it manually. Phase 7
     (operator console) automates this.

4. **Date range for the initial pull** — should the worker default to pulling all
   available history for the account (from MT5's earliest available date), or require
   explicit `WORKER_FROM_DATE` / `WORKER_TO_DATE`? Recommendation: **require explicit
   dates for Phase 1** to keep the first run predictable and auditable. A "full history"
   mode can be added later.

5. **Supabase project ref** — `db/supabase/config.toml` currently has a placeholder
   project ref. Phase 1 needs the real Supabase project ref and region to apply the
   migration via `supabase db push`. Confirm when ready.

---

**Awaiting your review before implementing.**
