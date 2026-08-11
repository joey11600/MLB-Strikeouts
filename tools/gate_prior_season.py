"""Gate 5 for the prior-season history window: calibration, not accuracy.

Runs the real compound model (Stage A -> Stage B -> Poisson-binomial) over
the starts this feature recovers, once with the prior season and once
without, and compares Brier score and calibration on P(K >= line).

Measured ON THE RECOVERED STARTS ONLY. Pooling over a full slate would
drown the result: 88% of starts are untouched by this feature and would
average the difference to nothing, which reads as "no harm" when it
actually means "not measured".

AUDIT A-002: historical DraftKings lines are not sourced, so P(K >= line)
is evaluated at the standard ladder (3.5-7.5) rather than at the line a
book actually hung. That measures whether the distribution is right, which
is what Gate 5 asks; it does not measure edge against a real market.

Usage:
    python tools/gate_prior_season.py 2025     # fitting year
    python tools/gate_prior_season.py 2026 --end 2026-08-10   # holdout
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from strikeout_predictor import StrikeoutPredictor
from tools.fit_prior_weight import (
    recovered_starts, blended_rate, LEAGUE_K_RATE,
)
from tools import daily_pipeline as dp

LINES = [3.5, 4.5, 5.5, 6.5, 7.5]


def workload(row, use_prior: bool) -> float:
    """Reproduce the pipeline's bf_mean choice for a thin current season."""
    cur_outings = int(row["cur_outings"])
    cur_mean = row["cur_bf"] / cur_outings if cur_outings else None
    if not use_prior:
        # Production today: current season only. With no outings at all
        # there is nothing to say, and the pitcher is refused outright.
        return cur_mean
    p25 = float(row["prior_bf_p25"])
    if cur_outings == 0:
        return p25
    return dp.PRIOR_WORKLOAD_BLEND * cur_mean + (
        1.0 - dp.PRIOR_WORKLOAD_BLEND) * p25


def run(year: int, end: date | None):
    rows = recovered_starts(year, end)
    pred = StrikeoutPredictor()
    pred.load_models()

    out = []
    for _, r in rows.iterrows():
        actual_k = int(r["ks"])
        for use_prior in (False, True):
            bf_mean = workload(r, use_prior)
            if bf_mean is None or not np.isfinite(bf_mean):
                # Production has no workload at all here (season debut),
                # so there is no baseline prediction to pair against. The
                # feature is purely additive on these starts — reported
                # separately, because an error here has no production
                # number to be caught against.
                continue
            k_pct = blended_rate(
                np.array([r["cur_bf"]]), np.array([r["cur_ks"]]),
                np.array([r["prior_bf"]]), np.array([r["prior_ks"]]),
                dp.PRIOR_SEASON_WEIGHT if use_prior else 0.0,
            )[0]
            res = pred.predict(
                {"a3_season_k_pct_shrunk": float(k_pct),
                 "c1_bf_mean": float(bf_mean)},
                lineup_k_pcts=[LEAGUE_K_RATE] * 9,
                lines=LINES,
            )
            for line in LINES:
                out.append({
                    "use_prior": use_prior,
                    "line": line,
                    "p": res["per_line"][line],
                    "hit": int(actual_k > line),
                    "start": int(r["game_pk"]),
                    "debut": int(r["cur_outings"]) == 0,
                })
    return pd.DataFrame(out)


def summarise(df: pd.DataFrame, label: str):
    print(f"\n=== {label} ===")
    hdr = f"  {'':6} {'Brier':>8} {'log-loss':>9} {'mean p':>8} {'actual':>8}"
    print(hdr)
    for use_prior in (False, True):
        d = df[df["use_prior"] == use_prior]
        p = np.clip(d["p"].to_numpy(), 1e-6, 1 - 1e-6)
        y = d["hit"].to_numpy()
        brier = float(np.mean((p - y) ** 2))
        ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
        tag = "prior" if use_prior else "base"
        print(f"  {tag:6} {brier:8.5f} {ll:9.5f} {p.mean():8.3f} "
              f"{y.mean():8.3f}")

    b = df[~df["use_prior"]]
    a = df[df["use_prior"]]
    bb = float(np.mean((b["p"] - b["hit"]) ** 2))
    ab = float(np.mean((a["p"] - a["hit"]) ** 2))
    print(f"\n  Brier change: {(bb - ab) / bb * 100:+.2f}% "
          f"({'better' if ab < bb else 'WORSE'})")

    print("\n  calibration (predicted vs actual, prior on):")
    a = a.copy()
    a["bucket"] = pd.cut(a["p"], [0, .2, .4, .6, .8, 1.0])
    for bucket, grp in a.groupby("bucket", observed=True):
        print(f"    {str(bucket):12} n={len(grp):5}  "
              f"predicted {grp['p'].mean():.3f}  actual {grp['hit'].mean():.3f}"
              f"  gap {grp['hit'].mean() - grp['p'].mean():+.3f}")


def summarise_debuts(df: pd.DataFrame):
    """Season debuts: no production baseline exists, so calibration is the
    only check available. Reported apart so it is never mistaken for a
    paired improvement."""
    d = df[df["debut"] & df["use_prior"]]
    if not len(d):
        print("\n  (no season debuts in this sample)")
        return
    p = np.clip(d["p"].to_numpy(), 1e-6, 1 - 1e-6)
    y = d["hit"].to_numpy()
    print(f"\n  SEASON DEBUTS — {d['start'].nunique()} starts, "
          f"production refuses all of them (no baseline to beat)")
    print(f"    Brier {float(np.mean((p - y) ** 2)):.5f}   "
          f"mean predicted {p.mean():.3f}   actual {y.mean():.3f}   "
          f"gap {y.mean() - p.mean():+.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--end", type=str, default=None)
    args = ap.parse_args()
    end = date.fromisoformat(args.end) if args.end else None
    df = run(args.year, end)
    paired = df[~df["debut"]]
    n = paired["start"].nunique()
    summarise(paired, f"{args.year} (prior {args.year - 1}) — {n} paired "
                      f"starts x {len(LINES)} lines")
    summarise_debuts(df)


if __name__ == "__main__":
    main()
