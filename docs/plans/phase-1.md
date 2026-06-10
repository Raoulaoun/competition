# Phase 1 Plan — Data Pipeline (Ingestion Worker)

**Goal:** One demo account's deals landing in Postgres, repeatably and idempotently.  
**Done when:** pointing the worker at a single demo account produces correct rows in
`raw_deals` via the REST sync path, and re-running produces no duplicates.

---

## Architecture decision: Web API, hybrid two-channel design

**No Windows host required.** The worker uses the MT5 **Web API**
(HTTPS + WebSocket), which runs on any OS. Different competitions may use
different brokers with different API endpoints — the architecture keeps this
route clear via a `BrokerAdapter` ABC. To onboard a new broker, implement the
ABC; nothing else changes.

### Two channels, one adapter interface

| Channel | Protocol | When used | What it does |
|---|---|---|---|
| **REST sync** | HTTPS (POST/GET) | Bootstrap, login, catch-up | Pulls historical closed deals for a date range |
| **Data Pump** | WebSocket (persistent TCP) | Live, real-time | Server pushes new deal events, position changes, margin changes without polling |

**Phase 1 implements the REST sync channel only.** The pump channel is
architected, stubbed, and ready for Phase 3 (live leaderboard). Both channels
live on the same adapter object so the switch-on is `worker start --mode sync`
vs. `worker start --mode pump` — no structural change later.

### Per-broker adapter contract

Different brokers host their own MT5 Web API instance (different base URLs,
possibly different auth schemes). The adapter is parameterized by broker config
loaded from env — no broker-specific URLs or auth logic are ever hardcoded.

```python
class BrokerAdapter(ABC):

    @abstractmethod
    async def fetch_deals(
        self,
        account_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[RawDeal]:
        """
        REST channel: pull closed deals for one account within [from_dt, to_dt].
        Called on bootstrap and catch-up runs.
        """

    @abstractmethod
    async def subscribe_pump(
        self,
        account_ids: list[str],
        on_event: Callable[[RawDeal], Awaitable[None]],
    ) -> None:
        """
        Pump channel: establish persistent WebSocket, call on_event for each
        incoming deal/position event. Runs until cancelled.
        Phase 1: raises NotImplementedError — implemented in Phase 3.
        """
```

Phase 1 ships `MT5WebAdapter(BrokerAdapter)` with `fetch_deals` implemented and
`subscribe_pump` raising `NotImplementedError`.

---

## Module layout

```
services/ingestion/
├── pyproject.toml                    # ruff, black, pytest; package: ingestion
├── worker/
│   ├── __init__.py
│   ├── main.py                       # entry: python -m worker [--mode sync|pump]
│   ├── config.py                     # BrokerConfig + WorkerConfig from env (no secret defaults)
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                   # BrokerAdapter ABC + RawDeal dataclass
│   │   └── mt5_web.py                # MT5WebAdapter: REST fetch_deals; pump stub
│   ├── transform.py                  # RawDeal → Deal (canonical shape, balance reconstruction)
│   ├── db.py                         # upsert_deals() → UpsertStats; idempotent
│   └── sync.py                       # orchestrate: fetch → transform → upsert for one account
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_transform.py             # unit: RawDeal → Deal, all edge cases
    ├── test_db.py                    # unit: idempotency (mock DB)
    └── test_sync.py                  # integration: mock adapter + mock DB, end-to-end
```

---

## `RawDeal` — the wire shape before transformation

Sits in `adapters/base.py`. Represents exactly what the MT5 Web API JSON returns,
before normalization. Fields that the Web API may not always provide are `Optional`.

```python
@dataclass
class RawDeal:
    deal_id:     int                 # MT5 deal ticket (broker-unique per account)
    order_id:    int | None          # MT5 order ticket that generated this deal
    position_id: int | None          # MT5 position ID (links in+out of one trade)
    time:        int                 # Unix timestamp (seconds) from broker
    time_msc:    int | None          # Unix timestamp (milliseconds), higher precision
    type:        int                 # MT5 DEAL_TYPE_* enum value
    entry:       int                 # 0=in, 1=out, 2=inout
    symbol:      str | None          # trading pair, e.g. "EURUSD"
    volume:      float               # lot size (0.0 for balance events)
    price:       float               # execution price (0.0 for balance events)
    commission:  float
    swap:        float
    profit:      float
    fee:         float
    comment:     str | None
```

The `type` and `entry` integer enums are resolved to strings in `transform.py`,
not here.

---

## Supabase migration

File: `db/supabase/migrations/<timestamp>_create_raw_deals.sql`

```sql
-- Raw deals written by services/ingestion.
-- Schema conforms to /docs/contracts/deal.md (scoring columns),
-- plus raw provenance columns for integrity checks and future use.
-- Scoring recomputes everything from these rows — broker summary figures
-- are never stored here.

create table if not exists raw_deals (
    id              bigserial    primary key,

    -- tenant isolation + round routing (always scoped, never omitted)
    tenant_id       uuid         not null,
    account_id      text         not null,
    round_id        text         not null,

    -- Deal contract fields (see /docs/contracts/deal.md)
    time            timestamptz  not null,
    direction       text         not null,   -- "in" | "out" | "balance"
    type            text         not null,   -- "buy" | "sell" | "balance" | ...
    volume          numeric,                 -- null for balance events
    price           numeric,                 -- null for balance events
    commission      numeric      not null default 0,
    fee             numeric      not null default 0,
    swap            numeric      not null default 0,
    profit          numeric      not null default 0,
    balance         numeric,                 -- null on "in" deals; reconstructed

    -- Raw provenance fields (not part of scoring contract; preserved for integrity)
    mt5_deal_id     bigint,                  -- broker deal ticket
    mt5_order_id    bigint,                  -- broker order ticket
    mt5_position_id bigint,                  -- links "in" + "out" of same trade
    symbol          text,                    -- trading pair, e.g. "EURUSD"

    ingested_at     timestamptz  not null default now()
);

-- Idempotency: one row per (tenant, account, round, MT5 deal ticket).
-- Deals with a ticket use the ticket as the key.
-- Balance/deposit events that carry no ticket fall back to a composite key.
create unique index raw_deals_dedup_ticket
    on raw_deals (tenant_id, account_id, round_id, mt5_deal_id)
    where mt5_deal_id is not null;

create unique index raw_deals_dedup_no_ticket
    on raw_deals (tenant_id, account_id, round_id, time, direction, type)
    where mt5_deal_id is null;

-- Query performance: scoring reads all deals for one (account, round)
create index raw_deals_account_round
    on raw_deals (tenant_id, account_id, round_id, time asc);

-- RLS: all queries must be scoped to the authenticated tenant.
alter table raw_deals enable row level security;

create policy raw_deals_tenant_isolation on raw_deals
    using (tenant_id = current_setting('app.tenant_id')::uuid);
```

**Why preserve `symbol`, `mt5_position_id`, `mt5_order_id`:**
Not needed by scoring today, but `mt5_position_id` is the cleanest way to link
an "in" and "out" deal for the same trade (Phase 6 integrity), and `symbol` is
needed if/when per-instrument analysis is added. Storing them now costs nothing;
retroactively adding them requires re-ingestion.

---

## Credentials and secrets — hard rules

All broker credentials and the Supabase service-role key live in environment
variables only. They are:
- **Never** committed (`.env*` is in `.gitignore`)
- **Never** logged — no credential field appears in any log line, even at DEBUG
- **Never** printed to stdout in any error path or traceback
- **Never** passed as CLI arguments (they appear in `ps aux`)
- **Only** read server-side in `config.py`; never referenced in `apps/web` or `apps/api`

### Env var structure

One worker process per tenant (broker). Each runs with its own env:

```
# Broker Web API (per-tenant — loaded from secrets manager or .env on the host)
MT5_WEB_API_BASE_URL=https://<broker-mt5-host>/api
MT5_WEB_API_LOGIN=<manager login number>
MT5_WEB_API_PASSWORD=<manager password>
MT5_WEB_API_SERVER=<MT5 server name>        # used for auth, not as a hostname

# Supabase (service-role key — server-side only, never the anon key)
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service role key>

# Scoping for this run
WORKER_TENANT_ID=<uuid>                     # identifies the broker/tenant
WORKER_ACCOUNT_ID=<string>                  # demo account number to pull
WORKER_ROUND_ID=<string>                    # which round these deals belong to

# Sync window (REST path)
WORKER_FROM_DATE=2024-01-01                 # ISO date; required for Phase 1
WORKER_TO_DATE=2024-12-31                   # ISO date; required for Phase 1
```

`config.py` raises `ValueError("<VAR_NAME> is required")` if any required var is
absent — naming the missing variable but never its value. No defaults for any
credential field.

---

## REST sync pipeline (`sync.py`)

```
BrokerAdapter.fetch_deals(account, from_dt, to_dt) → list[RawDeal]
    ↓
transform(raw_deals) → list[Deal] + list[mt5_deal_id]   # canonical shape + ticket list
    ↓
upsert_deals(deals, mt5_deal_ids, tenant, account, round, conn)
    ↓
UpsertStats(inserted, skipped, errors)
```

### Step 1 — REST fetch (`adapters/mt5_web.py`)

The MT5 Web API authentication and deal-history endpoint vary by broker
configuration. The adapter is parameterized entirely by `BrokerConfig` (from env)
— no URL patterns or auth logic are hardcoded.

**Typical flow (document what your broker's API actually provides before implementing):**

1. `POST {base_url}/auth` with manager login/password → session token (JWT or cookie)
2. `POST {base_url}/history/deals` with `{login, from, to}` → JSON array of deal objects
3. Parse JSON → `list[RawDeal]`
4. On session expiry, re-authenticate transparently

The adapter uses `httpx` (async HTTP client) for the REST calls. Session tokens are
held in memory only — never written to disk or logged.

```python
class MT5WebAdapter(BrokerAdapter):
    async def fetch_deals(self, account_id, from_dt, to_dt) -> list[RawDeal]:
        token = await self._auth()          # token never logged
        resp = await self._client.post(
            f"{self._base_url}/history/deals",
            json={"login": int(account_id), "from": ..., "to": ...},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return [_parse_raw_deal(d) for d in resp.json()]

    async def subscribe_pump(self, account_ids, on_event):
        raise NotImplementedError("pump channel implemented in Phase 3")
```

**⚠️ Confirm with your broker before implementing:**
- The exact REST endpoint paths and auth scheme
- The JSON field names in the deal response (they vary by broker's API version)
- Whether the API returns deals in time-sorted order (the worker must sort ascending before balance reconstruction regardless)

### Step 2 — Transform + balance reconstruction (`transform.py`)

Pure function, no I/O. Converts `list[RawDeal]` → `list[Deal]`.

The MT5 Web API deal response does not include a running account balance field —
this is true of the REST endpoint and the pump. The running balance must be
reconstructed from the deal stream:

1. Sort all deals ascending by `time` (then `time_msc` as tiebreaker if available).
2. Identify the initial deposit: the first deal where `entry`=2 (balance operation)
   and `type`=DEAL_TYPE_BALANCE. Its `profit` field is the deposit amount.
3. Accumulate forward:
   ```
   balance[0] = deposit.profit
   balance[i] = balance[i-1] + deal[i].profit + deal[i].commission
                              + deal[i].swap + deal[i].fee
   ```
4. Assign the reconstructed balance to "out" and "balance" deals. "in" deals get
   `balance=None` (balance unchanged at position entry — matches the xlsx export
   convention the scoring service was tested against).

**Sanity check (log-only, does not abort):** after reconstruction, compare
`balance[-1]` against the account equity fetched from the broker's account-info
REST endpoint. A mismatch > 0.01 is logged as a warning (amount of mismatch only,
never balance or credential values). This detects broker data gaps without blocking
ingestion.

**Type mapping — `RawDeal.type` int → `Deal.type` string:**

| MT5 int | String |
|---|---|
| 0 | `"buy"` |
| 1 | `"sell"` |
| 2 | `"balance"` |
| 3 | `"credit"` |
| 4 | `"charge"` |
| 5 | `"correction"` |
| 6 | `"bonus"` |
| (others) | `"unknown_<int>"` |

**Direction mapping — `RawDeal.entry` int → `Deal.direction` string:**

| MT5 int | String |
|---|---|
| 0 | `"in"` |
| 1 | `"out"` |
| 2 | `"balance"` (balance/deposit type) |

Zero volume/price (balance events) → `None`.

### Step 3 — Upsert (`db.py`)

```sql
INSERT INTO raw_deals
    (tenant_id, account_id, round_id, time, direction, type,
     volume, price, commission, fee, swap, profit, balance,
     mt5_deal_id, mt5_order_id, mt5_position_id, symbol)
VALUES %s
ON CONFLICT DO NOTHING;
```

`ON CONFLICT DO NOTHING` against the two partial unique indexes makes re-runs
fully idempotent. Returns `UpsertStats(inserted, skipped)` for the operator log.

Uses `psycopg` (psycopg3) async. The Supabase connection string is assembled from
`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. The service-role key bypasses RLS —
correct for a server-side worker. Immediately after connecting:
```sql
SET app.tenant_id = '<tenant_id>';
```
so any RLS-gated reads within the same session are correctly scoped.

### Step 4 — Entry point (`main.py`)

```python
# python -m worker --mode sync
config = load_config()          # raises on missing vars; never logs secret values
adapter = MT5WebAdapter(config.broker)
async with db_connection(config.supabase) as conn:
    stats = await run_sync(adapter, config, conn)
log.info("sync complete", extra={"inserted": stats.inserted, "skipped": stats.skipped})
```

Structured JSON logging throughout. Exit 0 on success, non-zero on any unhandled
error. The `--mode pump` path exists as a CLI flag (prints "not implemented yet")
so Phase 3 has a clear hook point.

---

## Tests

**No test ever touches real credentials, a real broker API, or a real database.**

### `test_transform.py` — pure unit tests

- Balance/deposit deal → `direction="balance"`, `volume=None`, `price=None`
- "in" deal → `balance=None`
- "out" deal → `balance` set to reconstructed value
- Running balance reconstruction: cumulative sum matches expected sequence
- Unix epoch → UTC datetime
- Zero volume/price → `None`
- MT5 type int → correct string label
- Unknown type int → `"unknown_<int>"` (does not crash)
- All numeric fields default to 0.0 when absent
- Determinism: same input → identical output

### `test_db.py` — mock DB (no network)

- Fresh insert: `inserted=1, skipped=0`
- Re-insert same deal (by ticket): `inserted=0, skipped=1`
- Re-insert balance event (no ticket, composite key): `inserted=0, skipped=1`
- Mixed batch (some new, some seen): counts correct
- DB error propagates; does not swallow

### `test_sync.py` — mock adapter + mock DB

- Mock adapter returns 10 deals (1 deposit + 9 trades)
- First sync: 10 inserted
- Second sync (same date range): 0 inserted, 10 skipped
- Balance reconstruction in transform verified against expected cumulative sum
- Adapter auth error → logged safely, process exits non-zero (no credentials in log)

---

## Verification procedure (manual, once implemented)

1. Set all env vars in a `.env` file on the server (not committed).
2. Run `python -m worker --mode sync`.
3. Confirm in Supabase:
   ```sql
   select count(*), min(time), max(time)
   from raw_deals
   where tenant_id = '<tenant>' and account_id = '<account>';
   ```
4. Re-run with the same `WORKER_FROM_DATE` / `WORKER_TO_DATE`.
5. Confirm row count is unchanged — idempotency verified.
6. **Cross-check with xlsx path:** export the same account's ReportHistory from MT5
   terminal; run `scoring/adapters/xlsx.py` on it; compare the Deal list
   field-by-field with what landed in `raw_deals`. They should be identical — this
   confirms balance reconstruction is correct and the scoring service will see the
   same data regardless of which adapter feeds it.

---

## Open questions for your review

1. **MT5 Web API endpoint paths and auth scheme** — these vary by broker. Before
   implementing `mt5_web.py`, I need the base URL, the auth endpoint, the
   deal-history endpoint path, and the auth method (bearer token, basic auth,
   cookie, etc.) for the first broker. The adapter structure is finalized; only
   the HTTP layer needs these specifics.

2. **Pump channel protocol** — you described it as a "permanent persistent socket
   connection." For the Web API path: is this a WebSocket upgrade on the same
   HTTPS host, or a separate raw TCP socket to a different port? This matters for
   Phase 3 design but not Phase 1.

3. **`round_id` assignment** — recommendation: operator sets `WORKER_ROUND_ID`
   manually for Phase 1. Phase 7 (operator console) automates this from the active
   round config. Confirm this is acceptable for now.

4. **Date range** — recommendation: require explicit `WORKER_FROM_DATE` /
   `WORKER_TO_DATE` for Phase 1 (auditable). A "pull full history" mode can be
   added once the first run is verified.

5. **Supabase project ref** — needed to apply the migration via `supabase db push`.
   Confirm the project ref and region when ready.

---

**Awaiting your review before implementing.**
