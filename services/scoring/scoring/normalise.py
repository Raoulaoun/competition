"""
Rank-percentile normalization and composite score computation.

Ported directly from trader-elo-rater/scripts/rate.py (rank_norm and
composite_scores). Uses rank-percentile normalization — NOT z-scoring.
The metrics.md reference doc mentions z-scoring; that is stale. The
validated scripts use rank_norm() and weights.json confirms "normalization":
"rank". Do not revert this to z-scores.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scoring.config import ScoringConfig
from scoring.models import MetricVector


def rank_norm(series: pd.Series, direction: int) -> pd.Series:
    """
    Rank-percentile normalize a metric series.

    direction=1  : higher raw value → higher percentile (better)
    direction=-1 : lower raw value → higher percentile (better)

    Edge cases preserved from the validated script:
    - inf / -inf coerced to NaN before ranking
    - Single-value (or all-NaN) series returns 0.5
    - NaN values filled to the minimum non-NaN percentile rank
    """
    s = pd.to_numeric(series, errors="coerce").astype(float)
    s = s.replace([np.inf, -np.inf], np.nan)
    oriented = s * direction
    if oriented.notna().sum() <= 1:
        return pd.Series(np.full(len(s), 0.5), index=s.index)
    pr = oriented.rank(method="average", pct=True)
    return pr.fillna(pr.min() if pr.notna().any() else 0.5)


def composite_scores(
    vectors: list[MetricVector],
    config: ScoringConfig,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """
    Compute composite scores for a cohort of MetricVectors.

    Returns:
        composites    : {trader_id: composite_score}
        contributions : {trader_id: {pillar_name: pillar_score}}
    """
    if not vectors:
        return {}, {}

    df = pd.DataFrame([v.to_dict() for v in vectors])
    df = df.set_index("trader_id")

    comp = pd.Series(np.zeros(len(df)), index=df.index)
    pillar_cols: dict[str, pd.Series] = {}

    for pillar_name, pdef in config.pillars.items():
        pw = pdef.weight
        msum = sum(m.w for m in pdef.metrics.values())
        pillar_score = pd.Series(np.zeros(len(df)), index=df.index)

        for metric_name, mdef in pdef.metrics.items():
            if metric_name not in df.columns:
                continue
            normed = rank_norm(df[metric_name], mdef.direction)
            pillar_score = pillar_score + normed * (mdef.w / msum)

        pillar_cols[pillar_name] = pillar_score
        comp = comp + pillar_score * pw

    composites = comp.to_dict()
    contributions: dict[str, dict[str, float]] = {
        tid: {pillar: float(pillar_cols[pillar][tid]) for pillar in pillar_cols}
        for tid in df.index
    }
    return composites, contributions
