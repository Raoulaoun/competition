# CLAUDE.md — Trading Competition League Platform

> Drop this at the repo root. Claude Code reads it at the start of every session. Keep it tight (~150 lines); link out to `/docs` for detail. Refine it as the project teaches you what Claude gets wrong.

## What we're building
A broker-agnostic trading-competition **league**. Traders compete on **demo accounts** across format divisions and city/country teams, accumulating ranking points across a season toward a physical championship. This codebase is the **competition layer**: it ingests trade data, scores it, ranks it, and presents it in a customizable web "cockpit" (plus a lighter mobile companion). Brokers and sponsors plug in.

## Non-negotiable product principles
- **Trading happens on the broker's MT4/MT5 — never here.** We only ever READ trade data. This platform NEVER places, modifies, or executes orders.
- **Demo accounts only.** No live-money execution integration, ever.
- **Broker-agnostic + multi-tenant from day one.** Brokers are tenants. Data is isolated per tenant. No cross-tenant leakage, ever.
- **Risk-adjusted scoring, recomputed from raw data.** Rankings use the risk-adjusted composite (see Scoring). Always recompute from raw deals; NEVER trust a broker's summary figures.
- **Integrity and consent are product features, not afterthoughts.**

## Architecture
- `apps/web` — Next.js (App Router) + TypeScript + Tailwind. The cockpit (customizable widget grid) and the mobile companion (curated, non-customizable). PWA.
- `apps/api` — API layer (Next.js route handlers / edge functions). Reads from DB, serves leaderboards/widgets, enforces tenant + consent rules.
- `services/scoring` — Python. The scoring engine, ported from the `trader-elo-rater` skill (six weighted metric pillars + Glicko-2). Pure, tested, deterministic.
- `services/ingestion` — Python worker. Pulls trade/account data from MT4/MT5 (Manager/Web API) into the DB. Likely runs on a dedicated host (MT5 Manager API is Windows-oriented). Holds broker credentials server-side only.
- `db` — Supabase (Postgres + Auth + Realtime + Row Level Security). Migrations via Supabase CLI.
- `cache` — Redis. Precomputed leaderboards/scores served from cache (handles the finale read spike).

Data flow: `ingestion → db (raw deals) → scoring → db (scores/ranks) → cache → api → web`.

## Stack & conventions
- TypeScript strict mode. ESLint + Prettier. No `any` without a comment justifying it.
- Python: type hints, `ruff` + `black`, `pytest`.
- Tests: Vitest (TS), pytest (Python). The scoring service requires tests for every change.
- Commits: conventional commits, one logical change per commit.
- Branches: one branch per vertical slice (see Workflow). PR + review before merge to `main`.
- Colors in UI: green/red are RESERVED for profit/loss only — never as brand or accent.

## Commands
- `pnpm dev` — run web + api locally
- `pnpm test` / `pnpm lint` / `pnpm build`
- `supabase start` / `supabase db push` — local DB + migrations
- `cd services/scoring && pytest` — scoring tests
- `cd services/ingestion && python -m worker` — run ingestion worker

## Hard rules (do not violate)
- NEVER commit secrets, API keys, or broker/MT5 credentials. They live in env / secrets manager, server-side only. Never in the client bundle, never in logs.
- NEVER expose MT5 credentials or the Manager API to the browser. Ingestion is server-side only.
- ALL scoring, ranking, and prize-eligibility logic MUST have unit tests. No untested changes to `services/scoring`.
- Recompute every metric from raw deals. Do not trust broker summary rows.
- Consent records are APPEND-ONLY audit data. Never mutate or delete them.
- Participant PII (contact, performance) is shared with a broker ONLY after a recorded explicit consent exists. Enforce this in code at the query boundary, not just the UI.
- Every DB query is scoped by tenant via RLS. Treat a cross-tenant read as a critical bug.

## Workflow rules
- PLAN before coding. For each slice, write a short plan to `/docs/plans/<slice>.md` and get it reviewed before implementing.
- Work in THIN VERTICAL SLICES — one feature end-to-end before adding breadth. Get one trader's data flowing all the way to a live rank before building more widgets.
- Run tests + lint before every commit. CI must pass before merge.
- `/clear` context between unrelated slices.
- Stop and ask a human for: scoring-formula changes, anything touching prize eligibility, consent/PII handling, tenant isolation, or money values.

## Domain glossary
- **Tenant** — a broker. Owns rounds, sponsorship, branding. Data isolated per tenant.
- **Division** — a format category (Scalping / Day / Swing / Investing). Each has its own scoring config.
- **Round / Season** — a single competition / the league arc of rounds → championship.
- **Qualification cutoff** — the rank line above which traders advance to the finale.
- **Composite (risk-adjusted) score** — the ranking number: return penalized by drawdown, weighted by consistency, across six pillars. Glicko-2 rating runs on virtual pairwise matches.
- **Derby** — a city/country team head-to-head.
- **IB** — Introducing Broker agreement; the recurring revenue relationship (out of scope for code, in scope for consent/data terms).
- **Ingestion worker** — the server-side service pulling data from the broker's MT5.

## References
- `/docs/build_plan.md` — phased slice-by-slice build sequence (read this first).
- `/docs/operating_model.md` — business/revenue model and why decisions are what they are.
- `/docs/widgets.md` — widget list, purposes, fixed vs. closable.
- `trader-elo-rater` skill — the source logic for `services/scoring`.
