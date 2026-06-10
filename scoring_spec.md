# Scoring Service Spec — `services/scoring`
### Phase 2 acceptance criteria & port reference

**Source of truth:** the validated `trader-elo-rater` skill scripts (`rate.py`, `extract.py`, `report.py`, `weights.json`) will be uploaded into the repo. **Port those** — do not reconstruct formulas from description. This document is the **acceptance criteria**: the port is correct when its behavior matches both the uploaded scripts and the locked values below. Where this spec and the scripts disagree on a formula, the validated scripts win — except the one normalization correction called out below, which must be confirmed against the code.

---

## Two-layer design (recap)
- **Layer 1 — performance score.** One track record → a cohort-relative, risk-adjusted composite. The metric pillars live here.
- **Layer 2 — rating engine.** Glicko-2. Manufactures virtual pairwise matches from the Layer-1 scores and produces a confidence-aware rating. A rating only exists relative to a cohort — a single file yields a metric vector, not a rating.

---

## ⚠️ The one correction to verify against the code
The skill's prose (SKILL.md) describes **z-scoring** the pillars. The validated decision moved to **rank-based percentile normalization** instead — more robust to blowout outliers and small cohorts. The uploaded scripts should already implement rank-percentile. **Confirm the port uses rank-percentile normalization, not z-scores** — do not "fix" the code to match the prose.

---

## Layer 1 — pillars & locked v1 weights
Weights are frozen as config (verify exact sub-metrics against `weights.json`):

| # | Pillar | Weight | Metrics (verify against weights.json) |
|---|---|---|---|
| 1 | Risk-adjusted return | **28%** | Sortino, Sharpe, return-over-max-drawdown |
| 2 | Capital preservation | **28%** | Max drawdown, ulcer index, recovery factor |
| 3 | Payoff & tail discipline | **22%** | Profit factor, payoff ratio, expectancy, largest-loss % |
| 4 | Return / alpha | **10%** | Net return / alpha |
| 5 | Consistency | **7%** | Trade/day coverage, equity-curve smoothness — *verify exact metrics* |
| 6 | Behavioral hygiene | **5%** | Overtrading / leverage discipline signals — *verify exact metrics* |

(Sum = 100%.) Each metric is **rank-percentile normalized within the round's cohort**, **sign-oriented so higher is always better**, then weighted and summed → the **composite score**. Cohort-relative normalization is deliberate: if everyone profited because the market ripped, that's beta, not skill, and it nets out.

## Layer 2 — Glicko-2 rating
- **Virtual pairwise matches** manufactured from Layer-1 composites: every trader vs. every other; **higher composite wins**.
- **Volatility constant τ = 0.4** — blowups are treated as real signal, not smoothed away.
- **Two-state model:**
  - **Projected** (default; from track-record data): RD floored near **190**, conservative display suppressed, tier ceiling capped near **Gold**. First rounds with no priors run projected.
  - **Confirmed** (on-platform data with floating drawdown / MAE): RD floor drops to **50**, unlocking the top tiers (Diamond/Apex) — by construction, not by rule.
- **Conservative display** = `R − 2·RD` (suppressed in projected state).
- **Carry-forward:** priors load from the previous round's `ratings.json` (R / RD / volatility); RD shrinks as confidence builds across rounds.
- *Verify exact tier thresholds against the scripts — they are not restated here.*

## Disqualification gate
DQ on **negative ending balance** OR **equity breaching a configurable `stop_out_equity_pct`** (mirrors real broker stop-out logic — not an arbitrary threshold). DQ'd accounts drop off the ladder by default; `--include-dq` lands them dead last.

---

## Input contract — and the key port refactor
The skill parses the **Deals** section of an MT5 ReportHistory `.xlsx` as ground truth and **recomputes everything** — the broker's Results/summary block is never trusted.

**Port refactor (the main "reworks vs. ports directly" answer):**
- **Ports directly:** all metric computation (the six pillars) and the entire Glicko-2 engine. Keep them as pure, deterministic functions over a normalized list of deals.
- **Reworks:** the *input adapter*. The live platform path must read **`raw_deals` from Postgres** (the schema Phase 1 writes), not parse `.xlsx`. Refactor so the metric functions take a normalized deal list, fed by either adapter.
- **Keep the xlsx parser** as a second input path — it's still useful for offline track-record scoring and eligibility checks (single-file → metric vector). Its layout: Name/Account at column index 3 of the header block; a `Deals` marker in column 0 followed immediately by a 14-column header row; a `Results` marker terminating the deals block.
- **Critical for Phase 3:** the normalized deal shape the scoring functions consume must match the Phase 1 `raw_deals` schema exactly, so ingest → score is a clean join, not a reconciliation.

## Output contract
Same data, whether emitted as files (offline) or written to the DB (platform):
- Ranked ladder (`leaderboard.csv` equivalent).
- Carry-forward state (`ratings.json`: R / RD / vol) for the next round.
- Per-trader detail (`round_detail.csv` equivalent): every metric + each pillar's contribution — this feeds the "Why this rank" widget, so preserve the pillar-level breakdown.

---

## Acceptance test — the synthetic cohort (must pass)
Build the fixture: **five archetypes + one real blowup file.**
- Archetypes: disciplined grinder, solid professional, lucky high-leverage gambler, choppy breakeven trader, reckless survivor.
- Blowup file: the real `ReportHistory-22933` profile — ~**94% loss** from a **$3,000** start, **12.5% win rate** over **16 trades**, **~805× peak leverage**.

**Assertions that must hold (projected state):**
1. The **disciplined low-leverage grinder ranks #1**.
2. The **lucky gambler — despite the highest raw return (~49%) — is demoted to ~3rd** by the risk adjustment.
3. The **blowup account is disqualified off the ladder** (and lands dead last under `--include-dq`).

This is the test that proves the risk adjustment actually works — it is **not optional**, and no change to the scoring service merges without it green.

**Add these edge cases too:**
- A trader with very few trades (near-empty deal set).
- An all-losing account (no winning trades).
- A minimal cohort (2–3 traders) — confirm rank-percentile normalization stays sane.
- Determinism: same input → identical output across runs.

---

## Guardrails (from CLAUDE.md, restated for this service)
- Recompute every metric from raw deals; never trust broker summaries.
- The service is pure and deterministic — no hidden state, no network calls in the scoring path.
- Tests required on every change to scoring, ranking, or DQ logic.
- Weights and constants (τ, RD floors, `stop_out_equity_pct`) live in config, not as inline magic numbers.
