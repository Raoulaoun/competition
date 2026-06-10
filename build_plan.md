# Build Plan — Trading Competition League Platform
### Slice-by-slice sequence for building with Claude Code

This is the order to build in, the guardrails per stage, and example prompts you can hand to Claude Code. The golden rule throughout: **thin vertical slices** — get one trader's data flowing all the way to a live rank before you build breadth. Plan each slice before coding it, review the plan, then implement.

---

## Step 0 — De-risk before any code (not a Claude Code task)

The whole platform hinges on getting trade data out of MT4/MT5, which depends on your **broker provisioning API access**. Settle this first:

- Confirm your launch broker will give you **MT5 Manager API or Web API** access (server-side, privileged). Get the access type in writing.
- Understand the host constraint: the MT5 Manager API is **Windows-oriented**, so the ingestion worker likely needs a Windows host/VM separate from the rest of the stack. Confirm what your broker's API actually exposes.
- Fallback if no Manager API: per-account read-only investor passwords. Much more painful at scale — know this before you design around it.

**If you can't get a clean data tap, stop and resolve it. Everything downstream assumes it.**

## Decisions to lock before Phase 0
- Final stack confirmation (defaults below): Next.js + TS + Tailwind (web), Supabase (db/auth/realtime/RLS), Python (scoring + ingestion), Redis (cache), Vercel + a worker host.
- The scoring formula's final pillar weights (you have these in `trader-elo-rater` — freeze them as the v1 config).
- The league name (replaces the `MERIDIAN` placeholder).
- Who is the human reviewer for money/integrity/consent code (your technical partner).

---

## How to drive Claude Code through this
1. Put `CLAUDE.md` at the repo root and your three reference docs in `/docs`.
2. For each phase: ask Claude Code to **write a plan first** (`/docs/plans/<phase>.md`), review it, then implement.
3. One **branch per slice**; PR + review; CI green before merge.
4. `/clear` between unrelated slices to keep context clean.
5. Keep tests non-optional on anything touching scores, ranks, prizes, consent, or tenancy.

---

## Phase 0 — Foundation
**Goal:** a running monorepo skeleton with CI, no features yet.

Build: monorepo (`apps/web`, `apps/api`, `services/scoring`, `services/ingestion`, `db`), TypeScript + lint + format config, Supabase project + local dev, a CI pipeline (lint + test + build), and `/init` to seed/refine CLAUDE.md.

*Example prompt:* "Read CLAUDE.md and /docs/build_plan.md. Scaffold the monorepo described in the Architecture section with pnpm workspaces. Set up TypeScript strict, ESLint, Prettier, Vitest for the TS packages, and a Python package with pytest + ruff for services/scoring. Add a GitHub Actions CI that runs lint, test, and build. Write the plan to /docs/plans/phase-0.md first and stop for my review before implementing."

**Done when:** `pnpm dev`, `pnpm test`, and CI all run green on an empty skeleton.

## Phase 1 — Data pipeline (the linchpin slice)
**Goal:** one demo account's trades landing in Postgres, on a schedule.

Build: the ingestion worker connecting to the broker MT5 API (or a sandbox), pulling the raw **deals** for one account, and writing them to a `raw_deals` table. Credentials from env/secrets only.

*Guardrail:* server-side only; no creds in client or logs; idempotent writes (re-running doesn't duplicate).

**Done when:** you can point the worker at a demo account and see its real deals in the DB, repeatably.

## Phase 2 — Scoring service (port your skill)
**Goal:** raw deals → a risk-adjusted composite score + Glicko-2 rating, deterministic and tested.

Build: port the `trader-elo-rater` logic into `services/scoring` as a pure function: input raw deals for a cohort, output per-trader pillar metrics, the weighted composite, and the Glicko-2 rating from virtual pairwise matches. Freeze the v1 pillar weights as config.

*Guardrail:* tests required — including the synthetic-cohort check you already validated (reckless high-return trader must rank BELOW a steady one). Recompute from raw deals; never trust summaries.

*Example prompt:* "Port the scoring logic from the trader-elo-rater skill into services/scoring as a tested, deterministic module. Reproduce the six weighted pillars and the Glicko-2 rating. Include the synthetic-cohort test that confirms a high-return/high-drawdown trader is demoted below a steady one. Plan first."

**Done when:** feeding a cohort produces stable, correct, test-covered rankings.

## Phase 3 — First end-to-end vertical: live leaderboard
**Goal:** data → score → rank → a leaderboard widget showing real numbers. This is the slice that proves the whole spine.

Build: a scheduled job that scores the ingested cohort and writes ranks; the leaderboard API (tenant-scoped, served from Redis cache); and the single Leaderboard widget from the mockup wired to real data, with the qualification-cutoff line.

*Guardrail:* leaderboard reads come from cache, not recomputed per request (finale spike). Tenant-scoped queries.

**Done when:** ingest → score → see a live, correct, cached leaderboard for one round.

## Phase 4 — The cockpit shell + remaining widgets
**Goal:** the customizable terminal from the mockup, on real data.

Build: the cockpit shell (fixed brand bar + fixed command strip + draggable/closable widget grid with saved-per-user layouts and a sensible default preset), then the widgets — rank/score command strip, "Why this rank" (score breakdown), open positions, equity/analytics, journal, team derby, watchlist, stream, sponsor brief. Use the mockup as the visual reference.

*Guardrail:* fixed widgets (brand bar, rank/score strip) cannot be closed — enforce in code, it's sponsor inventory. Green/red for P&L only.

**Done when:** a user sees their real cockpit, rearranges it, and the layout persists.

## Phase 5 — Auth, registry, consent
**Goal:** real users, the participant registry (your asset), and clean consent capture.

Build: auth (Supabase), the onboarding/entry flow, the participant registry, and the **explicit, recorded consent** for sharing performance + contact with the round's broker. Consent records are append-only.

*Guardrail:* PII is shared with a broker ONLY when a consent record exists; enforce at the query boundary. This is a mandatory human-review slice.

**Done when:** a participant signs up, consents, and the consent is an immutable audit record gating any broker data access.

## Phase 6 — Integrity layer
**Goal:** the platform can't be trivially gamed.

Build: identity binding (one verified person per competing account), device/IP fingerprinting, and automated detection of multi-accounting and mirrored/opposite-position collusion pairs. Surface flags to the operator console.

*Guardrail:* mandatory human review. Team formats make collusion easier — test against that case explicitly.

**Done when:** seeded multi-account and collusion attempts get flagged automatically.

## Phase 7 — Operator console
**Goal:** you can actually run a round without spreadsheets.

Build: tenant/round setup, scoring-config per division, the integrity-flag queue, and the (consent-gated) lead handoff to the broker.

**Done when:** you can configure and run a full round end-to-end from the console.

## Phase 8 — Mobile companion
**Goal:** the "check my standing on the go" PWA.

Build: a curated, non-customizable stacked view of the high-glance widgets (rank/score, leaderboard, score breakdown, open positions, notifications, sponsor bar). No drag-grid, no chart/journal-heavy panels.

**Done when:** it installs to a phone home screen and shows live standing.

## Phase 9 — Multi-tenancy / white-label
**Goal:** the same platform re-skins per broker — the thing that makes it a company.

Build: per-tenant theming/branding config, tenant onboarding, and verify RLS isolation under multiple live tenants.

*Guardrail:* prove there is zero cross-tenant data visibility. Mandatory human review.

**Done when:** two tenants run side by side, fully isolated and individually branded.

---

## Guardrails summary (the gates that don't move)
- Tests required on scoring, ranking, prize eligibility, consent, and tenant isolation.
- Secrets/credentials server-side only; never committed, never in the client, never in logs.
- We read from MT5; we never execute trades. Demo only.
- Consent is append-only and gates all PII handoff, enforced in code.
- Every query tenant-scoped; cross-tenant leak = critical bug.
- Don't let Claude Code merge money/integrity/consent code faster than your reviewer can read it.

## Where a human reviewer is mandatory
Phases 2, 5, 6, and 9 (scoring, consent/PII, integrity, tenant isolation). These are where a silent bug means the wrong person wins a prize, a privacy/regulatory breach, a gamed competition, or a data leak between brokers. Claude Code writes the code; your technical partner owns the review.

## Risk register (the genuinely hard parts)
- **MT5 access** — external dependency on the broker; Windows host constraint. Highest risk; de-risked in Step 0.
- **Real-time leaderboard at scale** — MT5 doesn't stream; you poll. Design for polling latency + cache; don't promise tick-accuracy you can't deliver.
- **Demo execution realism** — demo scalping fills are unrealistic; weight qualification toward styles that translate honestly to live.
- **Scoring legibility** — the "Why this rank" explanation is a trust feature, not a nice-to-have. Ship it with the leaderboard, not later.
- **Compliance** — consent/PDPL handling and the IB/sponsorship line are real exposure; get a local legal opinion in parallel with the build.
