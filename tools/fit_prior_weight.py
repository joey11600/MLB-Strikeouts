"""Fit the prior-season recency weight W.

W says how much a batter faced LAST season is worth against one faced
THIS season, for the purpose of estimating a pitcher's strikeout rate:

    eff_bf = cur_bf + W * prior_bf
    eff_ks = cur_ks + W * prior_ks

then the existing league shrinkage on top, unchanged. W = 0 reproduces
production exactly (current season only); W = 1 would say a 2025 batter
is worth as much as a 2026 one.

Fitted by binomial log-loss on the starts the feature actually recovers
— those where the pitcher is under the 50-BF gate but has real prior
history. Pooling over the whole slate would drown the signal: 88% of
starts are unaffected by W and would just average it away.

**Fitting uses 2024 -> 2025 ONLY.** 2025 -> 2026 is the holdout and must
not be touched here; `--eval` exists to score a fixed W on a year pair
once W is already frozen. See docs/PRIOR_SEASON_SCOPE.md §5.

Usage:
    python tools/fit_prior_weight.py --fit 2025      # prior = 2024
    python tools/fit_prior_weight.py --eval 2026 --weight 0.42
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.backfill_statcast import load_cached
from tools.build_prior_season import load_prior_season

LEAGUE_K_RATE = 0.225
SHRINKAGE_BF = 70          # PITCHER_K_PSEUDO_BF
GATE_BF = 50               # the current refusal threshold
MIN_PRIOR_BF = 200
MIN_PRIOR_STARTS = 10

K_EVENTS = ["strikeout", "strikeout_double_play"]


def season_starts_asof(year: int, end: date | None = None) -> pd.DataFrame:
    """Per-start rows with strictly as-of season-to-date totals.

    The cumulative totals EXCLUDE the start being judged, so nothing about
    a game informs its own features (Gate 1).
    """
    df = load_cached(date(year, 3, 1), end or date(year, 11, 30))
    if df.empty:
        raise SystemExit(f"No Statcast rows cached for {year}.")

    first = df[df["inning"] == 1]
    idx = first.groupby(["game_pk", "inning_topbot"])["at_bat_number"].idxmin()
    starter_pairs = set(
        map(tuple, first.loc[idx, ["game_pk", "pitcher"]].to_numpy()))

    c = df[df["events"].notna()]
    g = (c.assign(k=c["events"].isin(K_EVENTS))
           .groupby(["pitcher", "game_pk"])
           .agg(bf=("k", "size"), ks=("k", "sum"),
                gdate=("game_date", "first"))
           .reset_index()
           .sort_values(["pitcher", "gdate"]))
    g["started"] = [(gp, p) in starter_pairs
                    for gp, p in zip(g["game_pk"], g["pitcher"])]

    grp = g.groupby("pitcher")
    g["cur_bf"] = grp["bf"].cumsum() - g["bf"]
    g["cur_ks"] = grp["ks"].cumsum() - g["ks"]
    # Outings so far, excluding this one. Needed to turn cur_bf into a
    # per-outing workload estimate — a season TOTAL compared against a
    # single start is not a workload comparison at all.
    g["cur_outings"] = grp.cumcount()
    return g[g["started"]].copy()


def recovered_starts(year: int, end: date | None = None) -> pd.DataFrame:
    """Starts the 50-BF gate refuses but prior season could answer."""
    starts = season_starts_asof(year, end)
    prior = load_prior_season(year - 1)
    if prior.empty:
        raise SystemExit(
            f"No prior-season sidecar for {year - 1}. Run "
            f"tools/build_prior_season.py {year - 1} first.")

    cols = ["pitcher", "prior_bf", "prior_ks", "prior_k_pct",
            "prior_starts", "prior_bf_mean", "prior_bf_p25"]
    m = starts.merge(prior[cols], on="pitcher", how="left")
    return m[(m["cur_bf"] < GATE_BF)
             & (m["prior_bf"] >= MIN_PRIOR_BF)
             & (m["prior_starts"] >= MIN_PRIOR_STARTS)].copy()


def blended_rate(cur_bf, cur_ks, prior_bf, prior_ks, w: float):
    """Production's shrinkage, applied to a prior-weighted sample."""
    eff_bf = cur_bf + w * prior_bf
    eff_ks = cur_ks + w * prior_ks
    raw = np.where(eff_bf > 0, eff_ks / np.maximum(eff_bf, 1e-9),
                   LEAGUE_K_RATE)
    return (eff_bf * raw + SHRINKAGE_BF * LEAGUE_K_RATE) / (
        eff_bf + SHRINKAGE_BF)


def logloss(rows: pd.DataFrame, w: float) -> float:
    """Binomial log-loss per batter faced."""
    p = blended_rate(rows["cur_bf"].to_numpy(), rows["cur_ks"].to_numpy(),
                     rows["prior_bf"].to_numpy(), rows["prior_ks"].to_numpy(),
                     w)
    p = np.clip(p, 1e-6, 1 - 1e-6)
    ks = rows["ks"].to_numpy()
    bf = rows["bf"].to_numpy()
    return -(ks * np.log(p) + (bf - ks) * np.log(1 - p)).sum() / bf.sum()


def main():
    ap = argparse.ArgumentParser(description="Fit prior-season weight W")
    ap.add_argument("--fit", type=int, metavar="YEAR",
                    help="Fit W on YEAR using YEAR-1 as prior")
    ap.add_argument("--eval", type=int, metavar="YEAR",
                    help="Score a fixed --weight on YEAR (holdout)")
    ap.add_argument("--weight", type=float, help="W for --eval")
    ap.add_argument("--end", type=str, default=None,
                    help="Cap the season at this date (YYYY-MM-DD)")
    args = ap.parse_args()

    if args.eval and args.weight is None:
        raise SystemExit("--eval needs --weight")
    year = args.fit or args.eval
    if not year:
        raise SystemExit("Pass --fit YEAR or --eval YEAR")

    end = date.fromisoformat(args.end) if args.end else None
    rows = recovered_starts(year, end)
    print(f"{year}: {len(rows)} recovered starts "
          f"({rows['bf'].sum():,} batters faced), prior = {year - 1}")

    base = logloss(rows, 0.0)
    if args.eval:
        got = logloss(rows, args.weight)
        print(f"\n  W = 0.00 (production today): log-loss {base:.5f}")
        print(f"  W = {args.weight:.2f} (fitted):        log-loss {got:.5f}")
        print(f"  improvement: {(base - got) / base * 100:+.2f}%")
        return

    grid = np.arange(0.0, 2.01, 0.01)
    losses = np.array([logloss(rows, w) for w in grid])
    best_w = float(grid[losses.argmin()])
    best = float(losses.min())

    print(f"\n  W = 0.00 (production today): log-loss {base:.5f}")
    print(f"  W = {best_w:.2f} (fitted):        log-loss {best:.5f}")
    print(f"  improvement: {(base - best) / base * 100:+.2f}%\n")
    print("  loss curve:")
    for w in [0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
        mark = "  <-- fitted" if abs(w - best_w) < 0.005 else ""
        print(f"    W={w:<5.2f} {logloss(rows, w):.5f}{mark}")


if __name__ == "__main__":
    main()
