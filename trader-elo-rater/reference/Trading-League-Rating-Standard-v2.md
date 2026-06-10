# Trading League Rating Standard — v2

The canonical definition of how a trader's rating is computed, displayed, and
carried across the league. One document, one standard. If the platform and the
skill ever disagree with each other, this file wins.

---

## 1. The one-number principle

A trader has exactly one headline number: the **League Rating**. It is a
*conservative* Glicko-2 rating:

```
League Rating = R − 2·RD
```

where **R** is the Glicko-2 rating and **RD** is its deviation (uncertainty).
Everything else in this document — pillars, metrics, weights, normalization —
exists only to produce the inputs that move R and RD. Nobody is ever ranked on a
raw pillar score or a single round's composite. Those are diagnostics.

**Why R − 2·RD and not R.** RD is wide for the unproven and narrows only as a
trader demonstrates repeatable results. Subtracting two deviations means a lucky
one-round spike (high R, but still-high RD) does *not* climb the ladder. To rise,
you must be good *and* consistent enough that the system becomes confident in you.
The leaderboard self-polices against luck. (Same idea as Xbox TrueSkill's μ − 3σ.)

A brand-new entrant starts at R=1500, RD=350 → League Rating 800. Low by design:
you earn your way up as confidence builds, you are not handed a mid-pack number.

---

## 2. The two states: Projected vs Confirmed

This is the heart of the standard, and it is **not two scoring systems** — it is
one rating with two confidence floors, driven entirely by data completeness.

| | **Projected** | **Confirmed** |
|---|---|---|
| Data source | MT5 track-record export | On-platform live trading |
| Captures floating drawdown / MAE? | No | Yes |
| Captures true concurrent exposure? | No (per-deal proxy only) | Yes |
| Execution verified? | No | Yes |
| **RD floor** | **190** | **50** |
| Reachable tiers | up to Gold | all, incl. confirmed-only top tiers |

Track-record data is fundamentally incomplete — it cannot show how deep underwater
a position went before it closed, or total open exposure at any instant. So the
system can never be fully confident in a projected rating: **RD is floored at 190
and can never drop below it.** Because the League Rating is R − 2·RD, that floor
imposes a permanent ~380-point suppression on the displayed number.

On-platform trading fills the gap (tick-level equity, MAE, concurrent exposure,
verified fills), so **RD may shrink to 50**, and the displayed rating rises toward
the true R.

**The mechanical incentive.** A trader at R=2000:
- Projected: 2000 − 2·190 = **1620**
- Confirmed: 2000 − 2·55 ≈ **1890**

Same underlying skill estimate; **+270 to the visible rating**, earned solely by
trading on the platform. Entering the competition is not a marketing ask — it is
the only way to realize your rating. And because the top tiers sit above the
projected suppression ceiling (§7), **they are structurally confirmed-only.** No
one games their way to elite on a PDF.

### State transition (projected → confirmed)
The projected rating is a Bayesian **prior**; platform performance is the
evidence; the confirmed rating is the posterior. On a trader's first platform
round, carry forward their projected (R, RD) as the starting point and drop the
RD floor from 190 to 50. A strong track record buys a head start, not a free pass.

---

## 3. The metric set

Every metric is recomputed from raw data — the broker's own summary block is never
trusted. Metrics feed pillars (§4). The **Projected** column marks what is
computable from a track-record export; **Confirmed-only** metrics require platform
data and enrich the same pillars they belong to.

### Risk-adjusted return
| Metric | Definition | Avail. |
|---|---|---|
| Sortino | mean trade return / downside deviation | Projected |
| Sharpe | mean trade return / total deviation | Projected |
| Return-over-maxDD | net return % / \|max drawdown\| | Projected |

### Capital preservation
| Metric | Definition | Avail. |
|---|---|---|
| Max drawdown | worst peak-to-trough on the **equity** curve | Projected (closed-trade approx) → upgraded when Confirmed |
| Ulcer index | sqrt(mean(negative drawdown²)) — depth × duration | Projected |
| Recovery factor | net P/L / \|max drawdown $\| | Projected |
| **Max Adverse Excursion** | worst *unrealized* point reached per position, aggregated | **Confirmed-only** |
| **True floating drawdown** | deepest account-level unrealized drawdown | **Confirmed-only** |

### Payoff & tail discipline
| Metric | Definition | Avail. |
|---|---|---|
| Profit factor | gross profit / \|gross loss\| | Projected |
| Payoff ratio | avg win / \|avg loss\| | Projected |
| Expectancy | mean net per trade | Projected |
| Largest loss % | worst single trade / starting balance | Projected |
| Win rate | wins / trades — **never weighted alone**, context only | Projected |

### Return / alpha
| Metric | Definition | Avail. |
|---|---|---|
| Net return % | cohort-relative net return for the round | Projected |

### Consistency
| Metric | Definition | Avail. |
|---|---|---|
| Equity linearity | signed R² of equity curve vs straight line | Projected |
| Trade-return dispersion | std of per-trade net | Projected |

### Behavioral hygiene
| Metric | Definition | Avail. |
|---|---|---|
| Peak leverage | max single-position notional / equity | Projected (per-deal proxy) |
| Leverage consistency | std of the leverage proxy | Projected |
| **Peak concurrent exposure** | max *summed* open notional / equity | **Confirmed-only** |
| **Min margin level** | closest approach to broker stop-out | **Confirmed-only** |

---

## 4. Pillar weights (locked)

The composite for a round is the weighted sum of the six pillar scores. These
weights are the standard; changing them changes the league, so they are versioned
with this document.

| Pillar | Weight | Rationale |
|---|---|---|
| Risk-adjusted return | **0.28** | Core skill signal; hard to fake with leverage |
| Capital preservation | **0.28** | Strongest blow-up predictor; survival wins demo leagues |
| Payoff & tail discipline | **0.22** | The anti-gaming heart (pennies-in-front-of-steamroller) |
| Return / alpha | **0.10** | Deliberately starved — the most gameable, most exciting axis |
| Consistency | **0.07** | Rewards repeatability over one-hit spikes |
| Behavioral hygiene | **0.05** | Flags reckless sizing even when it paid off |

Guiding principle: **the exciting pillar gets the least weight, because
excitement and gameability are the same axis.**

The pillars and weights are identical in both states. Confirmed mode does not
re-weight; it simply routes richer metrics (MAE, floating DD, concurrent exposure)
into the Preservation and Hygiene pillars they already belong to.

---

## 5. Normalization standard (rank-based)

Within each round's cohort, every metric is converted to a **percentile rank in
[0, 1]**, oriented so higher is always better (lower-is-better metrics are
inverted). Pillar score = weighted mean of its metrics' percentile ranks.

**Why rank, not z-score.** A trading league guarantees the two things z-scores
handle worst: extreme outliers (a −102% blowout) and small cohorts (six entrants
in an early round). One outlier warps a z-score mean and deviation for everyone;
percentile ranking is distribution-free, bounded, and stable at any cohort size.

---

## 6. From composite to rating (Glicko-2)

1. Compute each eligible trader's round composite (§4, §5).
2. Rank the cohort by composite.
3. Manufacture **virtual matches**: for every ordered pair, A *beats* B if A's
   composite > B's (tie if equal). Outcomes are **binary** {1, 0.5, 0} — never
   margin-weighted, which would break Glicko's probability model. Margin is
   already encoded in *how many* opponents you outrank.
4. Batch-feed each trader's results for the round into Glicko-2; update R, RD, σ.
5. Apply the state RD floor (§2): max(RD, 190) projected, max(RD, 50) confirmed.
6. League Rating = R − 2·RD.

**Rating speed.** Glicko governs update size through RD (the unproven move fast,
the established move slowly — exactly right) and the volatility constant **τ**.
Set **τ = 0.4** so the system treats a blowout as real signal, not noise to smooth
away. A reckless trader drops hard and stays down.

**A round / rating period** = one competition stage (a qualifier window or a
finale session). Inactivity lets RD grow back per standard Glicko, so stale
ratings correctly become less certain over time.

---

## 7. Rating scale & tier ladder

Tiers are defined on the **League Rating** (R − 2·RD), so they inherit the
confidence discipline automatically. Names are branding placeholders.

| League Rating | Tier (placeholder) | Notes |
|---|---|---|
| 1900 + | Apex | Structurally **confirmed-only** |
| 1750 – 1899 | Diamond | Effectively confirmed-only |
| 1600 – 1749 | Gold | Top reachable while Projected |
| 1450 – 1599 | Silver | |
| 1300 – 1449 | Bronze | |
| < 1300 | Provisional | New / unproven / recently active |

A projected rating is capped near Gold by the RD-190 floor (even a strong R≈2050
displays ≈1670). Diamond and Apex therefore *cannot* be reached without confirmed,
on-platform results — the ladder enforces "earn it on the platform" by construction,
not by rule.

---

## 8. Eligibility & disqualification gate

Pass/fail, evaluated before scoring. Separate from the rating math.

- **Minimum trades** to be ranked: 5 (stops one lucky bet from ranking).
- **Stop-out disqualification — aligned to reality, not an arbitrary number:**
  - Negative ending balance (proven blowout), **or**
  - Equity breaching the broker/competition **stop-out level** — a configurable
    parameter (`stop_out_equity_pct`) set to your broker's actual stop-out %.
    Confirmed mode evaluates this live from the margin-level series; Projected
    mode infers it from the closed-trade equity low and final balance.

DQ'd traders are still scored and shown for transparency but are excluded from the
ranked ladder and the Glicko update unless explicitly included.

---

## 9. Parameters (the only tunable constants)

| Parameter | Value | Where |
|---|---|---|
| Glicko base rating | 1500 | engine |
| Starting RD | 350 | engine |
| Starting volatility σ | 0.06 | engine |
| Volatility constant τ | 0.40 | engine |
| RD floor — Projected | 190 | state |
| RD floor — Confirmed | 50 | state |
| Display formula | R − 2·RD | display |
| Min trades to rank | 5 | gate |
| `stop_out_equity_pct` | broker-specific (set this) | gate |
| Pillar weights | 0.28 / 0.28 / 0.22 / 0.10 / 0.07 / 0.05 | §4 |

---

## 10. Roadmap: per-sector ratings

The global League Rating ships first. The schema reserves an `instrument_sector`
dimension (FX majors, metals, indices, crypto, …) so that the *same* pipeline can
later maintain parallel ratings per sector — a trader can be Apex in FX and
Provisional in indices. No engine change is required; the cohort is simply
partitioned by sector before §6 runs. Ship global, layer sectors later.
