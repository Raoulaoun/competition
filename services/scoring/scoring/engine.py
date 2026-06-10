"""
Scoring engine — the single callable entry point for the platform.

score_cohort() is pure: no file I/O, no DB writes, no network calls.
Callers (a scheduled job in Phase 3, the xlsx CLI wrapper, or tests) are
responsible for loading deals via an adapter and persisting the result.
"""

from __future__ import annotations

from typing import Literal

from scoring.config import ScoringConfig
from scoring.eligibility import apply_gate
from scoring.glicko2 import Rating, assign_tier, league_rating, run_round
from scoring.metrics import compute_metrics
from scoring.models import Deal, MetricVector, ScoringResult, TraderResult
from scoring.normalise import composite_scores


def score_cohort(
    cohort: list[tuple[str, list[Deal]]],
    config: ScoringConfig,
    priors: dict[str, Rating] | None = None,
    state: Literal["projected", "confirmed"] = "projected",
    include_dq: bool = False,
    accounts: dict[str, str | None] | None = None,
) -> ScoringResult:
    """
    Score a cohort of traders for one round.

    cohort   : [(trader_id, deals), ...]
    config   : loaded ScoringConfig
    priors   : {trader_id: Rating} from the previous round (optional)
    state    : "projected" (xlsx track record) or "confirmed" (on-platform)
    include_dq : include DQ'd traders in composites + Glicko update
    accounts : optional {trader_id: account_number} for the output

    Returns ScoringResult with leaderboard, dq_traders, and carry-forward ratings.
    """
    accounts = accounts or {}
    rd_floor = (
        config.engine.rd_floor_confirmed
        if state == "confirmed"
        else config.engine.rd_floor_projected
    )

    # --- step 1: compute raw metrics ---
    all_vectors: list[MetricVector] = []
    for trader_id, deals in cohort:
        raw = compute_metrics(deals)
        mv = MetricVector(
            trader_id=trader_id,
            account=accounts.get(trader_id),
            **raw,
        )
        all_vectors.append(mv)

    # --- step 2: eligibility gate ---
    gate_results = {mv.trader_id: apply_gate(mv, config.eligibility) for mv in all_vectors}

    eligible_vectors = [mv for mv in all_vectors if gate_results[mv.trader_id].eligible]
    dq_vectors = [mv for mv in all_vectors if not gate_results[mv.trader_id].eligible]

    scoring_vectors = all_vectors if include_dq else eligible_vectors
    if not scoring_vectors:
        scoring_vectors = all_vectors  # full transparency fallback

    # --- step 3: composite scores (rank-percentile, cohort-relative) ---
    composites, contributions = composite_scores(scoring_vectors, config)

    # --- step 4: Glicko-2 ---
    scores_for_glicko = {mv.trader_id: composites[mv.trader_id] for mv in scoring_vectors}
    updated_ratings = run_round(scores_for_glicko, priors=priors, tau=config.engine.tau, rd_floor=rd_floor)

    # --- step 5: assemble TraderResult for scored traders ---
    scored_results: list[TraderResult] = []
    for mv in scoring_vectors:
        r = updated_ratings[mv.trader_id]
        lr = league_rating(r)
        tier = assign_tier(lr, config.tiers)
        gate = gate_results[mv.trader_id]
        scored_results.append(
            TraderResult(
                rank=0,  # assigned after sort
                trader_id=mv.trader_id,
                account=mv.account,
                state=state,
                tier=tier,
                league_rating=round(lr, 1),
                R=round(r.rating, 1),
                RD=round(r.rd, 1),
                vol=round(r.vol, 6),
                composite=round(composites[mv.trader_id], 6),
                pillar_scores={k: round(v, 6) for k, v in contributions[mv.trader_id].items()},
                metrics=mv,
                eligible=gate.eligible,
                dq_reason=gate.dq_reason,
            )
        )

    # --- step 6: sort and assign ranks ---
    if include_dq:
        # sort all together: eligible first (by league_rating), DQ last
        eligible_scored = sorted(
            [r for r in scored_results if r.eligible],
            key=lambda x: x.league_rating,
            reverse=True,
        )
        dq_scored = sorted(
            [r for r in scored_results if not r.eligible],
            key=lambda x: x.league_rating,
            reverse=True,
        )
        leaderboard = eligible_scored + dq_scored
    else:
        leaderboard = sorted(
            [r for r in scored_results if r.eligible],
            key=lambda x: x.league_rating,
            reverse=True,
        )

    for i, tr in enumerate(leaderboard):
        tr.rank = i + 1

    # --- DQ entries for non-include_dq path (no Glicko data) ---
    if include_dq:
        dq_in_leaderboard = [r for r in leaderboard if not r.eligible]
        dq_traders = dq_in_leaderboard
    else:
        dq_traders = []
        for mv in dq_vectors:
            gate = gate_results[mv.trader_id]
            dq_traders.append(
                TraderResult(
                    rank=0,
                    trader_id=mv.trader_id,
                    account=mv.account,
                    state=state,
                    tier="Provisional",
                    league_rating=0.0,
                    R=config.engine.base_rating,
                    RD=config.engine.start_rd,
                    vol=config.engine.start_vol,
                    composite=0.0,
                    pillar_scores={},
                    metrics=mv,
                    eligible=False,
                    dq_reason=gate.dq_reason,
                )
            )

    # carry-forward ratings (eligible traders only when include_dq=False)
    ratings = {tid: r.to_dict() for tid, r in updated_ratings.items()}

    return ScoringResult(
        leaderboard=leaderboard,
        dq_traders=dq_traders,
        ratings=ratings,
    )
