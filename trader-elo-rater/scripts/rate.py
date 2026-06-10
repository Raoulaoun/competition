"""
Core entry point for the trading-ELO league. Conforms to Trading League Rating
Standard v2. ANALYZE -> RATE -> REPORT in one command: PDF reports are generated
automatically as the primary deliverable unless --no-report is passed.

Pipeline (one round):
  1. Extract a raw metric vector from every MT5 file.
  2. Eligibility gate: min trade count + stop-out DQ (negative balance / equity
     breaching the broker stop-out level).
  3. Percentile-RANK each metric within the eligible cohort (distribution-free).
  4. Weighted pillar means -> composite score per trader.
  5. Rank by composite -> virtual pairwise matches -> Glicko-2 (state RD floor).
  6. League Rating = R - 2*RD; assign tier.
  7. Auto-generate one official PDF per trader.

A single file (or --file) yields a TRACK-RECORD report (metrics + eligibility,
rating-pending) since a rating is relative and needs a cohort.

Usage:
  python rate.py --round-dir <folder>  [--state projected|confirmed]
                 [--league "Name"] [--round "Label"] [--priors prev.json]
                 [--out-dir ./out] [--include-dq] [--no-report]
  python rate.py --file <one.xlsx>     [--league "Name"] [--out-dir ./out]
"""

import os
import sys
import json
import glob
import argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract as ex
import glicko2 as gl
import report as rpt


def load_weights(path):
    with open(path) as f:
        return json.load(f)


def rank_norm(series, direction):
    s = pd.to_numeric(series, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)
    oriented = s * direction
    if oriented.notna().sum() <= 1:
        return pd.Series(np.full(len(s), 0.5), index=s.index)
    pr = oriented.rank(method="average", pct=True)
    return pr.fillna(pr.min() if pr.notna().any() else 0.5)


def composite_scores(df, weights):
    comp = pd.Series(np.zeros(len(df)), index=df.index); contributions = {}
    for pillar, pdef in weights["pillars"].items():
        pw = pdef["weight"]; msum = sum(m["w"] for m in pdef["metrics"].values())
        pillar_score = pd.Series(np.zeros(len(df)), index=df.index)
        for metric, mdef in pdef["metrics"].items():
            if metric not in df.columns: continue
            pillar_score = pillar_score + rank_norm(df[metric], mdef["direction"]) * (mdef["w"]/msum)
        contributions[pillar] = pillar_score; comp = comp + pillar_score * pw
    return comp, contributions


def assign_tier(lr, tiers):
    for t in sorted(tiers, key=lambda x: -x["min"]):
        if lr >= t["min"]:
            return t["name"]
    return tiers[-1]["name"]


def apply_gate(df, gate):
    so = gate.get("stop_out_equity_pct", 0.0)
    df["dq_reason"] = ""
    df.loc[df["trade_count"] < gate["min_trade_count"], "dq_reason"] += "under_min_trades;"
    df.loc[df["negative_balance"], "dq_reason"] += "negative_balance;"
    if so > 0 and "equity_low_pct" in df.columns:
        df.loc[df["equity_low_pct"] <= so, "dq_reason"] += f"stop_out(<{so:.0%});"
    df.loc[df["blowup_flag"] & (df["dq_reason"] == ""), "dq_reason"] += "blowout;"
    df["eligible"] = df["dq_reason"] == ""
    return df


def run(args):
    weights = load_weights(args.weights)
    eng = weights["engine"]; gate = weights["eligibility"]; tiers = weights["tiers"]
    os.makedirs(args.out_dir, exist_ok=True)
    report_dir = os.path.join(args.out_dir, "reports")

    # gather files
    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.round_dir, "*.xlsx")))
    if not files:
        raise SystemExit("No .xlsx files found")

    rows = []
    for f in files:
        try:
            rows.append(ex.extract(f))
        except Exception as e:
            print(f"  ! skipped {os.path.basename(f)}: {e}", file=sys.stderr)
    df = pd.DataFrame(rows)
    df = apply_gate(df, gate)

    # SINGLE-FILE / no-cohort -> track-record report, no rating
    if len(files) == 1:
        detail_path = os.path.join(args.out_dir, "track_record.csv")
        df.to_csv(detail_path, index=False)
        print(f"\nTrack-record analysis [no cohort] for {df.iloc[0]['trader']} "
              f"({'eligible' if df.iloc[0]['eligible'] else 'DQ: '+df.iloc[0]['dq_reason']})")
        if not args.no_report:
            made = rpt.generate_all(df, args.league, args.round_name, report_dir)
            print("Report:\n  " + "\n  ".join(made))
        print("\nNote: a competitive rating is assigned only when this account is scored within a round cohort.")
        return df

    # COHORT -> full rating
    rd_floor = eng["rd_floor_confirmed"] if args.state == "confirmed" else eng["rd_floor_projected"]
    scoring_set = df if args.include_dq else df[df["eligible"]].copy()
    if len(scoring_set) == 0:
        print("No eligible traders; showing all for transparency.", file=sys.stderr)
        scoring_set = df.copy()

    comp, contribs = composite_scores(scoring_set, weights)
    scoring_set = scoring_set.copy(); scoring_set["composite"] = comp.values
    for pillar, s in contribs.items():
        scoring_set[f"pillar_{pillar}"] = s.values

    priors = {}
    if args.priors and os.path.exists(args.priors):
        with open(args.priors) as fp:
            for tid, r in json.load(fp).items():
                priors[tid] = gl.Rating(r["rating"], r["rd"], r["vol"])

    scores = {row["trader"]: row["composite"] for _, row in scoring_set.iterrows()}
    updated = gl.run_round(scores, priors=priors, tau=eng["tau"], rd_floor=rd_floor)
    scoring_set["R"] = scoring_set["trader"].map(lambda t: round(updated[t].rating, 1))
    scoring_set["RD"] = scoring_set["trader"].map(lambda t: round(updated[t].rd, 1))
    scoring_set["volatility"] = scoring_set["trader"].map(lambda t: round(updated[t].vol, 4))
    scoring_set["league_rating"] = scoring_set["trader"].map(lambda t: round(gl.league_rating(updated[t]), 1))
    scoring_set["tier"] = scoring_set["league_rating"].map(lambda lr: assign_tier(lr, tiers))
    scoring_set["state"] = args.state
    scoring_set = scoring_set.sort_values("league_rating", ascending=False).reset_index(drop=True)
    scoring_set["rank"] = scoring_set.index + 1

    cols = ["rank","trader","account","state","tier","league_rating","R","RD","composite",
            "net_return_pct","sortino","max_drawdown","profit_factor","peak_leverage",
            "trade_count","eligible","dq_reason"]
    cols = [c for c in cols if c in scoring_set.columns]
    scoring_set[cols].to_csv(os.path.join(args.out_dir, "leaderboard.csv"), index=False)
    with open(os.path.join(args.out_dir, "ratings.json"), "w") as fp:
        json.dump({t: updated[t].to_dict() for t in scores}, fp, indent=2)
    scoring_set.to_csv(os.path.join(args.out_dir, "round_detail.csv"), index=False)

    print(f"\nRound rated [{args.state}] | RD floor {rd_floor} | {len(scoring_set)} traders "
          f"({df['eligible'].sum()} eligible, {(~df['eligible']).sum()} DQ)\n")
    show = ["rank","trader","tier","league_rating","R","RD","composite","net_return_pct",
            "max_drawdown","peak_leverage","eligible"]
    show = [c for c in show if c in scoring_set.columns]
    with pd.option_context("display.width", 220, "display.max_columns", None):
        print(scoring_set[show].to_string(index=False))

    if not args.no_report:
        made = rpt.generate_all(scoring_set, args.league, args.round_name, report_dir, trader=args.report_trader)
        print(f"\nOfficial reports ({len(made)}):\n  " + "\n  ".join(made))
    return scoring_set


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--round-dir")
    g.add_argument("--file")
    ap.add_argument("--state", choices=["projected", "confirmed"], default="projected")
    ap.add_argument("--league", default="Trading League")
    ap.add_argument("--round", dest="round_name", default="Competition Round")
    ap.add_argument("--priors", default=None)
    ap.add_argument("--weights", default=os.path.join(here, "..", "reference", "weights.json"))
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--include-dq", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--report-trader", default=None, help="only report this trader")
    run(ap.parse_args())
