# Phase 0 Plan — Foundation

**Goal:** A running monorepo skeleton with CI, no features.  
**Done when:** `pnpm dev`, `pnpm test`, and CI all run green on an empty skeleton.

---

## Pre-conditions (must hold before any code is committed)

- [ ] `.gitignore` at repo root covers `.env*`, secrets, `node_modules`, Python venvs, Supabase local state, build artifacts, and IDE noise.
- [ ] No credentials, API keys, or MT5/MT4 connection strings anywhere in the repo (confirmed: currently clean).

---

## Deliverables

### 1. Root-level scaffold

```
/
├── .gitignore              # covers .env*, secrets, node_modules, .venv, .supabase, dist, .next, etc.
├── .github/
│   └── workflows/
│       └── ci.yml          # lint + test + build on push/PR
├── pnpm-workspace.yaml     # workspaces: apps/*, services/*
├── package.json            # root: scripts (dev, test, lint, build), devDependencies (TypeScript, ESLint, Prettier, Vitest)
├── tsconfig.base.json      # strict TS config inherited by all TS packages
├── .eslintrc.base.js       # shared ESLint config (typescript-eslint, prettier plugin)
├── .prettierrc             # formatting rules
├── CLAUDE.md               # (already exists)
├── build_plan.md           # (already exists)
└── docs/
    └── plans/
        └── phase-0.md      # this file
```

### 2. `apps/web` — Next.js skeleton

```
apps/web/
├── package.json            # name: @competition/web; deps: next, react, tailwindcss
├── tsconfig.json           # extends ../../tsconfig.base.json
├── next.config.ts
├── tailwind.config.ts
├── postcss.config.js
├── app/
│   ├── layout.tsx          # root layout (no content yet)
│   └── page.tsx            # placeholder "Competition platform — coming soon"
└── vitest.config.ts        # Vitest for unit tests
```

### 3. `apps/api` — Next.js API skeleton

```
apps/api/
├── package.json            # name: @competition/api; deps: next
├── tsconfig.json
├── next.config.ts
└── app/
    └── api/
        └── health/
            └── route.ts    # GET /api/health → { status: "ok" }
```

A `/health` endpoint exists from day one so CI can verify the app starts.

### 4. `services/scoring` — Python package skeleton

```
services/scoring/
├── pyproject.toml          # tool.ruff, tool.black, pytest config; package name: scoring
├── scoring/
│   ├── __init__.py
│   └── engine.py           # placeholder: score(deals: list) -> dict (raises NotImplementedError)
└── tests/
    ├── __init__.py
    └── test_engine.py      # one passing smoke test: engine module imports cleanly
```

### 5. `services/ingestion` — Python package skeleton

```
services/ingestion/
├── pyproject.toml          # same tooling as scoring
├── worker/
│   ├── __init__.py
│   └── main.py             # placeholder: def run(): raise NotImplementedError
└── tests/
    ├── __init__.py
    └── test_main.py        # one passing smoke test: module imports cleanly
```

### 6. `db` — Supabase project skeleton

```
db/
├── supabase/
│   ├── config.toml         # local Supabase config (ports, project ref placeholder)
│   └── migrations/         # empty; first migration comes in Phase 1
└── README.md               # how to run: supabase start / supabase db push
```

No schema yet — that belongs to Phase 1 when we have concrete tables to define.

### 7. `.gitignore` (critical — ships first)

Covers at minimum:
- `.env`, `.env.*`, `.env.local`, `.env.*.local`
- `*.pem`, `*.key`, `*.p12`, `credentials*.json`
- `node_modules/`
- `.next/`, `dist/`, `build/`, `out/`
- `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`
- `.supabase/` (local Supabase state, contains auth tokens)
- `.turbo/`
- IDE: `.idea/`, `.vscode/` (except shared launch configs, if any)

### 8. GitHub Actions CI

File: `.github/workflows/ci.yml`  
Triggers: `push` and `pull_request` on all branches.

Jobs (run in order, fail-fast):

| Job | Steps |
|-----|-------|
| `lint-ts` | `pnpm install` → `pnpm lint` |
| `test-ts` | `pnpm install` → `pnpm test` (Vitest, both apps) |
| `build-ts` | `pnpm install` → `pnpm build` |
| `lint-py` | `pip install ruff black` → `ruff check services/` → `black --check services/` |
| `test-py` | `pip install pytest` + package deps → `pytest services/` |

Python jobs use `python: 3.12`. TS jobs use `node: 20`, `pnpm: 9`.

No Supabase in CI for Phase 0 — DB tests come in Phase 1.

---

## Key decisions baked in

- **pnpm workspaces** (not Turborepo yet) — keep it simple; add Turborepo caching later if build times justify it.
- **`apps/api` as a separate Next.js app** (not co-located in `apps/web`) — keeps the API deployable independently and easier to move to an edge runtime or separate host later.
- **No Docker in Phase 0** — ingestion worker's Windows/MT5 constraint makes Docker a later concern; Phase 0 just confirms the Python packages install and tests pass.
- **No Redis in Phase 0** — cache only matters once we have data to serve (Phase 3).
- **Supabase local CLI for `db/`** — schema-as-migrations from day one, never hand-edited DB.
- **Vitest over Jest** — faster, native ESM, integrates cleanly with Vite-based Next.js tooling.
- **`tsconfig.base.json` at root, extended per package** — single source of truth for `strict: true`, `noUncheckedIndexedAccess`, etc.

---

## Explicit non-goals for Phase 0

- No database schema (Phase 1)
- No auth (Phase 5)
- No scoring logic (Phase 2)
- No real UI (Phase 4)
- No Docker / containerization
- No Redis
- No real API routes beyond `/health`

---

## Open questions for your review

1. **League name** — `MERIDIAN` is the placeholder in CLAUDE.md. Should I use it as-is for now, or do you have a final name to use from the start?
2. **`apps/api` deploy target** — Vercel edge function, standard Vercel serverless, or a separate host? Affects `next.config.ts` settings, but not a blocker for Phase 0.
3. **Supabase project ref** — for `db/supabase/config.toml` we need a project reference ID (from your Supabase dashboard) or we use `local-only` as a placeholder. Fine to leave as placeholder for Phase 0.
4. **Python version** — 3.12 assumed. Confirm if you need 3.11 for any broker SDK compatibility.

---

**Awaiting your review before implementing.**
