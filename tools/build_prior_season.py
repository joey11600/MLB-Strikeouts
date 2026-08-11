"""Build the prior-season pitcher sidecar.

One row per pitcher summarising a COMPLETED season: strikeout rate, and
the distribution of his starter outings. `tools/daily_pipeline.py` reads
this instead of loading a second season of Statcast on every run — the
worker prices six times a day and an extra season is ~750K pitch rows
per run.

Why a pitcher's outing DISTRIBUTION and not just the mean: the mean
overstates batters faced by 5+ on 8.6% of the starts this feature
recovers, and at ~2.45 points of P(over) per batter faced that is a
14-point phantom OVER edge — A-007 magnitude, landing exactly where the
edge filter looks hardest. The p25 halves that tail while staying
near-unbiased. See docs/PRIOR_SEASON_SCOPE.md §3.

A start is "threw the game's first pitch for his side". That is knowable
before first pitch, so it is Gate-1 clean. Defining it as "faced 15+
batters" would be post-hoc — it silently drops the starter yanked after
eight, which is precisely the outing that makes prior-season workload
dangerous.

Usage:
    python tools/build_prior_season.py 2025
    python tools/build_prior_season.py 2025 --force   # rebuild
"""
import argparse
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.backfill_statcast import load_cached
from tracker import DATA_STATE_DIR

PRIOR_DIR = DATA_STATE_DIR / "prior_season"

K_EVENTS = ["strikeout", "strikeout_double_play"]

# A season is only summarisable once it is over. Building mid-season
# would bake a partial year into a "prior season" table that the pipeline
# then trusts as complete, and nothing downstream could tell.
SEASON_OVER_MONTH = 11  # November


def _season_is_complete(year: int) -> bool:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return today >= date(year, SEASON_OVER_MONTH, 1)


def build_prior_season(year: int) -> pd.DataFrame:
    """Summarise one completed season, one row per pitcher."""
    df = load_cached(date(year, 3, 1), date(year, 11, 30))
    if df.empty:
        raise SystemExit(
            f"No Statcast rows cached for {year}. Run "
            f"data/backfill_statcast.py for that season first."
        )

    # Starter = on the mound for the first at-bat of each half of the 1st.
    first = df[df["inning"] == 1]
    idx = first.groupby(["game_pk", "inning_topbot"])["at_bat_number"].idxmin()
    starter_pairs = set(
        map(tuple, first.loc[idx, ["game_pk", "pitcher"]].to_numpy())
    )

    completed = df[df["events"].notna()]
    per_game = (
        completed.assign(k=completed["events"].isin(K_EVENTS))
        .groupby(["pitcher", "game_pk"])
        .agg(bf=("k", "size"), ks=("k", "sum"))
        .reset_index()
    )
    per_game["started"] = [
        (gp, p) in starter_pairs
        for gp, p in zip(per_game["game_pk"], per_game["pitcher"])
    ]

    starts = per_game[per_game["started"]]
    totals = per_game.groupby("pitcher").agg(
        prior_bf=("bf", "sum"), prior_ks=("ks", "sum"),
        prior_games=("bf", "size"),
    )
    out = totals.join(
        pd.DataFrame({
            "prior_starts": starts.groupby("pitcher").size(),
            "prior_bf_mean": starts.groupby("pitcher")["bf"].mean(),
            "prior_bf_median": starts.groupby("pitcher")["bf"].median(),
            "prior_bf_p25": starts.groupby("pitcher")["bf"].quantile(0.25),
            "prior_bf_p10": starts.groupby("pitcher")["bf"].quantile(0.10),
        })
    )
    out["prior_starts"] = out["prior_starts"].fillna(0).astype(int)
    out["prior_k_pct"] = out["prior_ks"] / out["prior_bf"]
    out["season"] = year
    return out.reset_index()


def _write_parquet_atomic(path: Path, df: pd.DataFrame) -> None:
    """Atomic parquet write: tempfile + fsync + os.replace (repo rule)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        df.to_parquet(tmp_path, index=False)
        # "rb+" not "rb": Windows refuses fsync on a read-only handle
        # (OSError 9), so a read-only reopen would skip the durability
        # step the repo rule exists to guarantee.
        with open(tmp_path, "rb+") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_prior_season(year: int) -> pd.DataFrame:
    """Load the sidecar for `year`, or an empty frame if not built.

    Empty is a valid answer — it means every pitcher falls back to
    current-season-only behaviour, which is what production did before
    this feature existed.
    """
    path = PRIOR_DIR / f"{year}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def main():
    ap = argparse.ArgumentParser(description="Build prior-season sidecar")
    ap.add_argument("year", type=int, help="Season to summarise (completed)")
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if the file already exists")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="Build a season that is not over yet (testing only)")
    args = ap.parse_args()

    if not _season_is_complete(args.year) and not args.allow_incomplete:
        raise SystemExit(
            f"{args.year} is not over. A partial season written here would "
            f"be trusted downstream as a complete one, and nothing would "
            f"say otherwise. Pass --allow-incomplete only for testing."
        )

    path = PRIOR_DIR / f"{args.year}.parquet"
    if path.exists() and not args.force:
        print(f"{path} exists. Pass --force to rebuild.")
        return

    print(f"Building prior-season sidecar for {args.year}...")
    out = build_prior_season(args.year)
    _write_parquet_atomic(path, out)

    startable = out[out["prior_starts"] >= 10]
    print(f"  {len(out)} pitchers, {len(startable)} with 10+ starts")
    print(f"  written: {path}")


if __name__ == "__main__":
    main()
