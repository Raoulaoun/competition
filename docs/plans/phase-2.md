# Phase 2 Plan — Scoring Service

**Goal:** Port the validated `trader-elo-rater` scripts into `services/scoring` as a
pure, deterministic, well-tested Python module. Input: normalized deal list for a
cohort. Output: per-trader metric vectors, six-pillar composites, Glicko-2 ratings,
and a ranked leaderboard — written to Postgres (platform path) or files (offline path).

**Done when:** all tests pass including the synthetic-cohort merge gate, the
verify-flagged normalization discrepancy is confirmed resolved, and `pytest services/scoring` runs green.

---

## Pre-read: discrepancy to verify before touching a line of code

`metrics.md` (the reference doc) still says **z-score clipped to ±3**. `SKILL.md`
also says z-scoring. `rate.py` implements **rank-percentile** (`rank_norm()`). `weights.json`
confirms `"normalization": "rank"`. Per the spec, **rank-percentile wins** — the prose
docs are stale. The port must use `rank_norm()` exactly as validated. Do not "fix"
the code to match the docs.

---

## What ports directly vs. what reworks

| Component | Verdict | Rationale |
|---|---|---|
| `glicko2.py` — entire file | **Port directly** | Pure math, no I/O, no state, fully self-contained |
| `extract.py` — all metric computation (lines 63–177) | **Port directly** | Pure functions over deal arrays; formulae are validated |
| `rate.py` — `rank_norm()` | **Port directly** | Already rank-percentile; the correct implementation |
| `rate.py` — `composite_scores()` | **Port directly** | Pure; reads weights from config dict |
| `rate.py` — `assign_tier()` | **Port directly** | Pure; reads tier thresholds from config |
| `rate.py` — `apply_gate()` | **Port directly with minor cleanup** | Extract the `stop_out_equity_pct` check into an explicit param; remove the mutable `df["dq_reason"]` side-effect pattern in favour of returning a typed result |
| `extract.py` — `load_deals()` (xlsx parser) | **Rework as adapter** | Keep the xlsx path; move to `adapters/xlsx.py`; no formula changes |
| `rate.py` — `run()`, argparse CLI, PDF invocation | **Rework as service API** | Replace with a callable `score_cohort()` function; CLI wrapper stays separate; PDF generation is a presentation concern excluded from the service |
| `report.py` — PDF generation | **Not ported** | Presentation layer; not part of the scoring service |
| `adapters/postgres.py` | **New code** | Reads `raw_deals` from Postgres; must emit the same normalized deal shape as the xlsx adapter |
| `models.py` | **New code** | Typed `Deal` dataclass that defines the canonical deal shape both adapters must produce |

---

## Module layout

```
services/scoring/
├── pyproject.toml               # ruff, black, pytest; package name: scoring
├── reference/
│   └── weights.json             # copied from trader-elo-rater/reference/weights.json
│                                #   this is the single source of truth for all weights/constants
├── scoring/
│   ├── __init__.py              # exports: score_cohort, load_config
│   ├── config.py                # loads weights.json → typed ScoringConfig dataclass
│   ├── models.py                # Deal dataclass (canonical deal shape), MetricVector, ScoringResult
│   ├── metrics.py               # ported from extract.py: compute_metrics(deals) → MetricVector
│   ├── normalise.py             # ported from rate.py: rank_norm(), composite_scores()
│   ├── eligibility.py           # ported from rate.py: apply_gate() → typed DQResult
│   ├── glicko2.py               # ported directly from scripts/glicko2.py (unchanged)
│   ├── engine.py                # orchestrator: score_cohort(cohort, config, priors, state)
│   └── adapters/
│       ├── __init__.py
│       ├── xlsx.py              # ported from extract.py: load_deals() → list[Deal]
│       └── postgres.py          # new: load_deals_from_postgres(account_id, conn) → list[Deal]
└── tests/
    ├── __init__.py
    ├── fixtures/
    │   ├── weights.json         # copy of reference weights for hermetic tests
    │   └── synthetic_cohort.py  # archetype builders (see test section below)
    ├── test_metrics.py          # unit: every metric formula, including edge cases
    ├── test_normalise.py        # unit: rank_norm, composite_scores
    ├── test_eligibility.py      # unit: DQ gate logic
    ├── test_glicko2.py          # unit: Glicko-2 correctness
    ├── test_engine.py           # integration: full pipeline end-to-end
    └── test_synthetic_cohort.py # THE MERGE GATE (see below)
```

---

## Canonical deal shape (`models.py`)

This is the contract both adapters must produce. It must match the Phase 1
`raw_deals` Postgres schema exactly — Phase 1 should be designed against this, not
the other way around.

```python
@dataclass
class Deal:
    time: datetime
    direction: str        # "in" | "out" | "balance"
    type: str             # raw MT5 type string
    volume: float | None
    price: float | None
    commission: float     # 0.0 if absent
    fee: float            # 0.0 if absent
    swap: float           # 0.0 if absent
    profit: float         # 0.0 if absent
    balance: float | None
```

The xlsx adapter produces this from `load_deals()`. The postgres adapter selects
these columns from `raw_deals` for a given `account_id` (and tenant, round — always
scoped). Downstream metric code only ever sees `list[Deal]` — never a raw DataFrame
or a database cursor.

---

## Config (`config.py` + `reference/weights.json`)

All weights and constants live in `weights.json`, never as inline magic numbers.
The config module loads it once and exposes a typed `ScoringConfig`. No other module
hard-codes a weight, threshold, or constant.

**Locked v1 values to verify against `weights.json` during port:**

| Constant | Value |
|---|---|
| Pillar: risk_adjusted | 0.28 |
| Pillar: capital_preservation | 0.28 |
| Pillar: payoff_discipline | 0.22 |
| Pillar: return_alpha | 0.10 |
| Pillar: consistency | 0.07 |
| Pillar: behavioral_hygiene | 0.05 |
| Glicko-2 base_rating | 1500 |
| Glicko-2 start_rd | 350 |
| Glicko-2 start_vol | 0.06 |
| Glicko-2 τ (tau) | 0.4 |
| RD floor projected | 190 |
| RD floor confirmed | 50 |
| Conservative display | R − 2·RD |
| min_trade_count | 5 |
| stop_out_equity_pct | 0.0 |
| Normalization | rank-percentile |

Sub-metric weights (within each pillar) — verify all against `weights.json` during port:

- `risk_adjusted`: sortino 0.5, sharpe 0.2, return_over_maxdd 0.3
- `capital_preservation`: max_drawdown 0.5, ulcer_index 0.3, recovery_factor 0.2
- `payoff_discipline`: profit_factor 0.30, payoff_ratio 0.25, expectancy 0.25, largest_loss_pct 0.20
- `return_alpha`: net_return_pct 1.0
- `consistency`: equity_linearity 0.6, trade_return_std 0.4
- `behavioral_hygiene`: peak_leverage 0.6, leverage_consistency 0.4

---

## `metrics.py` — what is being ported

Port the computation block from `extract.py` (lines 63–177) as a pure function:

```python
def compute_metrics(deals: list[Deal]) -> MetricVector:
    ...
```

No file I/O. No `pd.read_excel`. Takes `list[Deal]`, returns a typed `MetricVector`.
The function replicates these computations exactly:

- Balance reconstruction: equity curve from `deal.balance` values; start balance from
  first `balance`-type deal; end balance from last balance value.
- Realized trades: `direction == "out"` deals; net = profit + swap + commission + fee.
- `net_return_pct`, `net_pl`, `start_balance`, `end_balance`
- Drawdown off realized equity curve: `max_drawdown`, `ulcer_index`
- `sharpe`, `sortino` (per-trade, unannualized — per-trade convention from script)
- `profit_factor`, `payoff_ratio`, `expectancy`, `win_rate`, `largest_loss_pct`
- `recovery_factor`, `return_over_maxdd`
- Leverage proxy: `peak_leverage`, `leverage_consistency` (volume × 100,000 × price / balance)
- Equity linearity: R² of balance vs. trade index, signed negative if declining
- DQ gate fields: `equity_low_pct`, `negative_balance`, `blowup_flag`

**Sign orientations to preserve** (both stored and scoring directions matter):
- `max_drawdown` stored negative; `direction: 1` in weights (closer to 0 = better) ✓
- `largest_loss_pct` stored negative; `direction: 1` ✓
- `ulcer_index` positive; `direction: -1` (lower is better) ✓
- `peak_leverage`, `leverage_consistency` positive; `direction: -1` ✓
- `trade_return_std` positive; `direction: -1` ✓

**One implementation detail to carry over explicitly:**
`blowup_flag = negative_balance OR (end_balance ≤ 0.10 × start_balance)`.
This is a separate field from the DQ `stop_out_equity_pct` check. In `apply_gate()`,
`blowup_flag` only fires as a DQ reason if no other reason has already been set —
preserve this fallback-last logic.

---

## `engine.py` — the service API

The orchestrator replaces `rate.py`'s `run()`. Signature:

```python
def score_cohort(
    cohort: list[tuple[str, list[Deal]]],   # [(trader_id, deals), ...]
    config: ScoringConfig,
    priors: dict[str, Rating] | None = None,
    state: Literal["projected", "confirmed"] = "projected",
    include_dq: bool = False,
) -> ScoringResult:
    ...
```

Pipeline (mirrors `rate.py` steps 1–6, minus CLI/PDF):
1. `compute_metrics()` for each trader → `MetricVector`
2. `apply_gate()` → mark eligible / DQ with reason
3. `composite_scores()` over eligible set (rank-percentile per metric, weighted pillar sum)
4. `run_round()` (Glicko-2) over eligible composites with state RD floor
5. `league_rating()` = R − 2·RD; `assign_tier()`
6. Sort by league_rating descending → ranks

Returns `ScoringResult` containing:
- Ranked leaderboard (rank, trader_id, tier, league_rating, R, RD, composite, key metrics)
- Per-trader pillar breakdowns (feeds the "Why this rank" widget — must preserve all six)
- Updated ratings dict (carry-forward state for next round)
- DQ list with reasons

`score_cohort()` is pure: no file I/O, no DB writes, no network. Callers
(a scheduled job in Phase 3, or CLI wrapper) handle persistence.

---

## Adapters

### `adapters/xlsx.py`
Port `load_deals()` from `extract.py` exactly. Returns `list[Deal]`. This path is
kept for offline track-record scoring (single-file eligibility checks, pre-platform
submissions).

Layout notes preserved from the source:
- Name/Account at column index 3 of the header block
- `Deals` marker in column 0; immediately followed by a 14-column header row
- `Results` marker terminates the deals block

### `adapters/postgres.py`
New. Reads from the Phase 1 `raw_deals` table. Returns `list[Deal]` with the same
shape. Signature:

```python
def load_deals_from_postgres(
    account_id: str,
    round_id: str,
    conn,           # asyncpg or psycopg connection — to be finalized with Phase 1 schema
) -> list[Deal]:
    ...
```

Always scoped by `account_id` + `round_id` (and implicitly tenant via RLS).
**This adapter cannot be fully implemented until Phase 1 defines the `raw_deals`
schema.** Phase 2 will stub it out and mark it as pending Phase 1. The stub must
still satisfy the same return type so `engine.py` is agnostic to which adapter is used.

---

## Tests

### Synthetic cohort — the merge gate (`test_synthetic_cohort.py`)

Five in-memory archetypes built as `list[Deal]` fixtures — no xlsx files needed.

| Archetype | Key parameters |
|---|---|
| **Disciplined grinder** | 30 trades, ~18% net return, max_drawdown ~−4%, peak_leverage ~3×, smooth equity curve (high linearity), consistent sizing |
| **Solid professional** | 20 trades, ~28% net return, max_drawdown ~−10%, peak_leverage ~8×, good profit_factor |
| **Lucky gambler** | 15 trades, ~49% net return (highest raw), max_drawdown ~−35%, peak_leverage ~40×, erratic sizing |
| **Choppy breakeven** | 25 trades, ~1% net return, equity oscillates, moderate drawdown |
| **Reckless survivor** | 18 trades, ~22% net return, max_drawdown ~−45%, peak_leverage ~80×, high leverage_consistency std |
| **Blowup (ReportHistory-22933 profile)** | 16 trades, ~94% loss from $3,000 start (ends ~$180), 12.5% win rate (2 wins / 14 losses), ~805× peak leverage |

**Assertions (projected state, stop_out_equity_pct=0.0):**

```python
def test_disciplined_grinder_ranks_first():
    result = score_cohort(cohort, config)
    assert result.leaderboard[0].trader_id == "disciplined_grinder"

def test_lucky_gambler_demoted_despite_highest_return():
    result = score_cohort(cohort, config)
    grinder_rank = rank_of("disciplined_grinder", result)
    gambler_rank = rank_of("lucky_gambler", result)
    assert gambler_rank > grinder_rank          # gambler ranks lower (worse)
    assert gambler_rank >= 3                    # specifically demoted to ~3rd or below

def test_blowup_disqualified():
    result = score_cohort(cohort, config)
    blowup = find_trader("blowup", result)
    assert not blowup.eligible
    assert blowup.dq_reason != ""
    # Not on the ladder by default
    assert "blowup" not in [t.trader_id for t in result.leaderboard]

def test_blowup_last_with_include_dq():
    result = score_cohort(cohort, config, include_dq=True)
    assert result.leaderboard[-1].trader_id == "blowup"
```

### Edge case tests (`test_metrics.py`, `test_engine.py`)

```python
def test_near_empty_deal_set():
    # 2 trades — below min_trade_count(5); should DQ, not crash
    ...

def test_all_losing_account():
    # No winning trades: profit_factor=0, payoff_ratio=0, win_rate=0
    # Must complete without division-by-zero
    ...

def test_minimal_cohort_rank_percentile():
    # 2-trader cohort: rank_norm must return [1.0, 0.0] (or [0.75, 0.25] with
    # average method) — confirm it doesn't degenerate to 0.5/0.5 or NaN
    ...

def test_determinism():
    r1 = score_cohort(cohort, config)
    r2 = score_cohort(cohort, config)
    assert r1.leaderboard == r2.leaderboard
    assert r1.ratings == r2.ratings
```

### Glicko-2 unit tests (`test_glicko2.py`)

- New entrant (no priors): starts R=1500, RD=350 → league_rating=800
- After one dominant round: R rises, RD shrinks, league_rating climbs
- RD floor projected (190): `rate()` can never return RD < 190 in projected state
- RD floor confirmed (50): floor drops as expected
- τ=0.4 preserved: rapid volatility changes not over-smoothed
- No-games case: RD grows toward DEFAULT_RD but not past it

### Normalization unit tests (`test_normalise.py`)

- `rank_norm()` with `direction=1` on `[1,2,3,4,5]` → monotone ascending percentiles
- `rank_norm()` with `direction=-1` → reversed
- Single-value series → returns 0.5 (not NaN, not error)
- Series with NaN → NaN filled to `pr.min()` (preserve script behavior)
- `inf` and `-inf` values → coerced to NaN (preserve `replace([np.inf, -np.inf], np.nan)`)

### Eligibility unit tests (`test_eligibility.py`)

- `trade_count=3` (< 5) → DQ `under_min_trades`
- `negative_balance=True` → DQ `negative_balance`
- `equity_low_pct=0.15` with `stop_out_equity_pct=0.20` → DQ `stop_out`
- `blowup_flag=True` with no other reason → DQ `blowout`
- Multiple reasons → all concatenated in `dq_reason`
- Eligible trader → `dq_reason=""`, `eligible=True`

---

## Output contract (preserved from skill)

Same whether offline (files) or platform (DB writes in Phase 3):

| Output | Contents |
|---|---|
| Leaderboard | rank, trader_id, account, state, tier, league_rating, R, RD, composite, key metrics, eligible, dq_reason |
| Ratings carry-forward | `{trader_id: {rating, rd, vol}}` for next round's priors |
| Round detail / pillar breakdown | All metrics + each pillar's contribution per trader — feeds "Why this rank" widget |

The `ScoringResult` dataclass must carry all three. Phase 3 will write them to
Postgres and Redis; Phase 2 only produces the in-memory result (and optionally
writes CSV/JSON for the offline xlsx path).

---

## Decisions locked before implementation

1. **`raw_deals` schema** — `Deal` dataclass is the canonical contract. Phase 1's
   `raw_deals` table conforms to it. Documented in `/docs/contracts/deal.md`.

2. **`blowup_threshold_pct`** — moved to `weights.json` alongside `stop_out_equity_pct`.
   Default 0.10 (10%). `compute_metrics` returns raw `end_balance`/`start_balance`;
   `apply_gate` applies the threshold from `EligibilityConfig`. The hardcoded
   `blowup_flag` field is removed from `MetricVector`.

3. **`report.py`** — stays in `trader-elo-rater` as the standalone offline PDF tool.
   Excluded from `services/scoring`.

4. **Division configs** — one global config for now. `load_config(path, division=None)`
   supports an optional `"divisions"` key in `weights.json` for future per-division
   overrides without touching scoring call sites.
