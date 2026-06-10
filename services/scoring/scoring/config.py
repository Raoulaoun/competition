"""
Load and validate the scoring configuration from weights.json.

Usage:
    config = load_config("reference/weights.json")
    config = load_config("reference/weights.json", division="scalping")

Division layering: if the JSON contains a "divisions" key and the requested
division is present within it, those keys are shallow-merged onto the base
config before parsing. This lets per-division weight overrides be added to
weights.json without changing any scoring call sites.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MetricDef:
    w: float
    direction: int  # 1 = higher is better, -1 = lower is better


@dataclass(frozen=True)
class PillarConfig:
    weight: float
    metrics: dict[str, MetricDef]


@dataclass(frozen=True)
class EngineConfig:
    base_rating: float
    start_rd: float
    start_vol: float
    tau: float
    rd_floor_projected: float
    rd_floor_confirmed: float


@dataclass(frozen=True)
class EligibilityConfig:
    min_trade_count: int
    stop_out_equity_pct: float
    blowup_threshold_pct: float


@dataclass(frozen=True)
class TierDef:
    min: float
    name: str


@dataclass(frozen=True)
class ScoringConfig:
    normalization: str
    engine: EngineConfig
    pillars: dict[str, PillarConfig]
    eligibility: EligibilityConfig
    tiers: list[TierDef]


def load_config(path: str | Path, division: str | None = None) -> ScoringConfig:
    with open(path) as f:
        raw = json.load(f)

    if division and "divisions" in raw and division in raw["divisions"]:
        raw = _merge(raw, raw["divisions"][division])

    eng = raw["engine"]
    engine = EngineConfig(
        base_rating=float(eng["base_rating"]),
        start_rd=float(eng["start_rd"]),
        start_vol=float(eng["start_vol"]),
        tau=float(eng["tau"]),
        rd_floor_projected=float(eng["rd_floor_projected"]),
        rd_floor_confirmed=float(eng["rd_floor_confirmed"]),
    )

    pillars = {}
    for name, pdef in raw["pillars"].items():
        metrics = {
            k: MetricDef(w=float(v["w"]), direction=int(v["direction"]))
            for k, v in pdef["metrics"].items()
            if not k.startswith("_")
        }
        pillars[name] = PillarConfig(weight=float(pdef["weight"]), metrics=metrics)

    gate = raw["eligibility"]
    eligibility = EligibilityConfig(
        min_trade_count=int(gate["min_trade_count"]),
        stop_out_equity_pct=float(gate["stop_out_equity_pct"]),
        blowup_threshold_pct=float(gate["blowup_threshold_pct"]),
    )

    tiers = [
        TierDef(min=float(t["min"]), name=t["name"])
        for t in raw["tiers"]
        if not str(t.get("min", "")).startswith("_")
    ]

    return ScoringConfig(
        normalization=raw.get("normalization", "rank"),
        engine=engine,
        pillars=pillars,
        eligibility=eligibility,
        tiers=tiers,
    )


def _merge(base: dict, overrides: dict) -> dict:
    """Shallow-merge overrides onto a copy of base (top-level keys only)."""
    result = copy.deepcopy(base)
    for k, v in overrides.items():
        result[k] = v
    return result
