---
name: trader-elo-rater
description: "Use this skill whenever the user has one or more MetaTrader 5 'ReportHistory' track-record exports (.xlsx) and wants to score, rank, or rate traders for the trading-competition league. Trigger when the user uploads an MT5 report and asks to 'rate this trader', 'score the track record', 'run the round', 'build the leaderboard', 'update ratings', 'who won the round', 'is this account eligible', or 'generate the rating report'. The skill recomputes every metric from the raw Deals section (never trusting the broker summary), normalizes within the cohort, produces a weighted risk-and-luck-adjusted composite, runs a Glicko-2 rating with virtual pairwise matches, and ALWAYS finishes by auto-generating an official PDF rating report per trader — the report is the core deliverable, not an optional step. A folder of files yields ranked rating reports; a single file yields a track-record report marked rating-pending."
---

# Trader ELO Rater

Turns MetaTrader 5 track-record exports into a luck-and-risk-adjusted competition
rating. Built on a two-layer design: a per-file **performance score** (Layer 1)
feeds a Glicko-2 **rating engine** (Layer 2).

This skill implements **Trading League Rating Standard v2** (`reference/Trading-League-Rating-Standard-v2.md`).
If anything here is ambiguous, that document is canonical.

## Why two layers

Raw P/L conflates skill, luck, and risk taken. A reckless trader who maxed
leverage and got lucky will out-earn a disciplined one on raw return. Rating on
P/L therefore rates luck and recklessness — fatal on demo accounts where no real
money enforces discipline. So:

- **Layer 1 — performance score.** Converts one track record into a clean,
  cohort-relative, risk-adjusted number. This is where the metric pillars live.
- **Layer 2 — rating engine.** Glicko-2. A rating only exists *relative to the
  other traders in the round*; this layer manufactures virtual matches from the
  Layer-1 scores and produces a self-correcting, confidence-aware rating.

A rating CANNOT be produced from a single file alone — one file yields the raw
metric vector; the rating needs a cohort.

## The displayed rating is conservative

The headline number is **League Rating = R − 2·RD** (Glicko rating minus two
deviations), never raw R. RD is wide for the unproven and shrinks only with
repeatable results, so a lucky one-round spike does not climb the ladder. New
entrants start low (R=1500, RD=350 → 800) and earn their way up.

## Two states: Projected vs Confirmed

One rating, two confidence floors set by data completeness:

- **Projected** (`--state projected`, default) — from a track-record export.
  Track-record data lacks floating drawdown, MAE, and verified execution, so RD
  is floored at **190** and the conservative display is permanently suppressed
  (~380 pts). Reaches at most the **Gold** tier.
- **Confirmed** (`--state confirmed`) — from on-platform live trading that
  captures tick equity, MAE, and true concurrent exposure. RD floor drops to
  **50**, so the display rises toward true R and the top tiers (Diamond, Apex)
  become reachable. These tiers are **structurally confirmed-only.**

Same R can display ~270 points higher once confirmed — the mathematical incentive
to trade on-platform. Transition: a trader's projected rating is carried as the
**prior** into their first confirmed round (good track record = head start), and
the RD floor drops from 190 to 50.

## Input contract

Every competitor submits the standard MT5 **ReportHistory** export (.xlsx): three
stacked sections (Positions, Orders, Deals) plus a Results block. The skill parses
the **Deals** section as ground truth and **recomputes everything** — the broker's
Results block is never trusted.

## The deliverable is the PDF report

This skill exists to produce **official PDF rating reports**. Analysis is not
"done" until the report is generated. `rate.py` is the single core command — it
ingests, recomputes every metric, rates the cohort, and **auto-generates one
official PDF per trader as its final step.** Do not stop at a CSV leaderboard and
do not treat the report as an optional extra; the PDF is the point.

## Decision guide

- **A folder of files (a round / "rate them / who won / build the leaderboard")**
  → `rate.py --round-dir ...`. Rates the cohort and writes one official rating PDF
  per trader automatically, plus the leaderboard, carry-forward `ratings.json`, and
  `round_detail.csv`.
- **A single file** → `rate.py --file ...`. Produces a **track-record report** PDF
  (full metrics + eligibility, marked *rating pending*), because a competitive
  rating is relative and needs a cohort. Say this plainly to the user.
- **Carrying a league across rounds** → pass last round's `ratings.json` via
  `--priors` so rating deviation shrinks and ratings compound; it is also the prior
  when a trader moves projected→confirmed.
- `extract.py` is an internal/debug helper (one file → raw metric vector as JSON).
  Normal requests go through `rate.py`, which calls it under the hood.

## How to run

Round (cohort) — analyze + rate + report in one command:
```bash
python scripts/rate.py --round-dir /path/to/round_folder \
    --state projected|confirmed \
    --league "League Name" --round "Round label" \
    [--priors ratings_prev.json] [--out-dir ./round_output] \
    [--include-dq] [--report-trader "Name"] [--no-report]
```

Single account (track-record report):
```bash
python scripts/rate.py --file /path/to/ReportHistory-XXXX.xlsx \
    --league "League Name" --round "Submission label" [--out-dir ./out]
```

Outputs land in `--out-dir`: PDF reports in `reports/`, plus `leaderboard.csv`
(ranked ladder), `ratings.json` (carry-forward state), `round_detail.csv` (every
metric + pillar contribution). Pass `--league` and `--round` so the report header
and footer are branded correctly; `--no-report` suppresses PDFs (rarely wanted).

Each rating PDF carries: branded header, headline League Rating with tier + state
badges, eligibility/DQ banner, a plain-language explanation of the rating, the
six-pillar cohort-percentile breakdown, and the full recomputed metric table.

## The metric pillars (Layer 1)

Each metric is **percentile-ranked within the cohort** (distribution-free, robust
to blowout outliers and small early cohorts — this replaced z-scoring in v2),
oriented so higher is better, then weighted and summed. Cohort-relative ranking
makes the score regime-relative: market-wide beta nets out.

Locked pillar weights (identical in both states; confirmed mode just feeds richer
metrics into the same pillars):

1. **Risk-adjusted return** — 0.28 (Sortino, Sharpe, return-over-max-drawdown).
2. **Capital preservation** — 0.28 (max drawdown, ulcer, recovery; + MAE & floating
   DD when confirmed). Strongest blow-up predictor.
3. **Payoff & tail discipline** — 0.22 (profit factor, payoff ratio, expectancy,
   largest loss %). The anti-gaming heart.
4. **Return / alpha** — 0.10. Deliberately starved; the most gameable, most
   exciting axis.
5. **Consistency** — 0.07 (equity linearity, trade dispersion).
6. **Behavioral hygiene** — 0.05 (peak leverage, sizing; + concurrent exposure &
   min margin level when confirmed).

Principle: **the exciting pillar gets the least weight, because excitement and
gameability are the same axis.** Win rate is never a standalone pillar.

## Eligibility gate (aligned to reality)

Pass/fail, separate from scoring:

- Minimum trade count (default 5).
- **Stop-out DQ aligned to the broker, not an arbitrary number:** negative ending
  balance, or equity low breaching `stop_out_equity_pct` (set this in weights.json
  to your broker's actual stop-out level; 0 = only a true negative-balance blowout
  disqualifies). DQ'd traders are scored and shown but excluded from the ladder and
  Glicko update unless `--include-dq`.

## The rating engine (Layer 2)

Glicko-2, for **confidence** tracking: R (rating), RD (uncertainty, with state
floor), σ (volatility, τ=0.4 so blowups are treated as real signal, not smoothed).

A "rating period" = one round. Each trader's composite ranks the cohort; A beats B
if A's composite > B's. Binary outcomes only (margin is encoded in how many you
outrank). Batch-fed to Glicko-2 → R, RD, σ → League Rating = R − 2·RD → tier.

## Tier ladder (on League Rating; names are branding placeholders)

Apex 1900+ (confirmed-only) · Diamond 1750–1899 · Gold 1600–1749 (projected ceiling)
· Silver 1450–1599 · Bronze 1300–1449 · Provisional <1300.

## The weighting vector is the main design lever

`reference/weights.json` controls the accuracy-vs-spectacle tradeoff. Preservation-
and risk-heavy weights produce an *accurate* rating that genuinely identifies skill
but a tamer-looking board; return-heavy weights make it *exciting* but noisier and
more gameable. Consider a conservative "true rating" plus a flashier public score
off the same metric vector. When the user wants to retune, edit this file only —
no code changes needed.

## Known measurement gap

MT5 closed-trade balance does not fully capture *floating* drawdown while losing
positions were open. For a system whose point is catching reckless risk before it
pays off, equity-level statements (with floating P/L) give a truer drawdown and
leverage picture. Flag this to the user if their reports show large gaps between
open and close times on losing trades.
