"""
Postgres adapter — reads raw_deals from the Phase 1 DB schema.

STUB: not fully implementable until Phase 1 defines and migrates the
raw_deals table. The function signature and return type are final (they
match the Deal contract in /docs/contracts/deal.md); only the query body
is a placeholder.

The raw_deals table columns that map to Deal fields:
    time, direction, type, volume, price,
    commission, fee, swap, profit, balance
Scoping columns (not part of the Deal contract):
    account_id, round_id, tenant_id

Tenant isolation is enforced by Postgres RLS — the connection must be
authenticated as the correct tenant role before this query runs.
"""

from __future__ import annotations

from scoring.models import Deal


async def load_deals_from_postgres(
    account_id: str,
    round_id: str,
    conn,  # asyncpg or psycopg3 connection; type finalized in Phase 1
) -> list[Deal]:
    """
    Load raw deals for one account in one round from Postgres.

    Always scoped by account_id + round_id. Tenant isolation is enforced
    by Postgres RLS on the authenticated connection — never pass a raw
    tenant filter here; rely on RLS.

    Returns list[Deal] in ascending time order, matching the shape that
    metrics.compute_metrics() expects.
    """
    raise NotImplementedError(
        "postgres adapter is pending Phase 1 raw_deals migration. "
        "See /docs/contracts/deal.md for the expected column mapping."
    )
