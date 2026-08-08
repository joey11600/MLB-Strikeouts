"""Build the per-start OUTS RECORDED dataset from the Statcast pitch cache.

Target market: DraftKings "Outs Recorded O/U" for the starting pitcher
(subcategory 17413, half-integer lines 13.5-19.5).

This is a DIFFERENT model family from the strikeouts model. Outs is a stopping
time on a lattice, not a count process: 65% of starts terminate on an exact
multiple of three because the removal decision is made at inning boundaries.
This module produces the foundation table; it fits nothing.

OUTS RECONSTRUCTION
-------------------
The `events` column in this cache never carries caught_stealing or pickoff, so
counting outs from events is biased low. Instead we difference the
`outs_when_up` state variable, which is the true out state at the start of each
plate appearance:

    within (game_pk, inning, inning_topbot), sorted by at_bat_number:
        outs_on_play[i] = max(0, outs_when_up[i+1] - outs_when_up[i])
        final PA of the half-inning:  outs_on_play = 3 - outs_when_up
        ... overridden to 0 when that PA is a walk-off (game ends, no third out)

STARTER IDENTIFICATION
----------------------
The starter of a half-inning-side is the pitcher of the FIRST plate appearance
of that side's half of inning 1. Every regular-season game therefore yields
exactly two starter rows.

USAGE
-----
    python tools/build_outs_dataset.py            # build + validate + cache
    python tools/build_outs_dataset.py --force    # ignore existing cache

    from tools.build_outs_dataset import load_outs_starts
    df = load_outs_starts()
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / "data" / "statcast_cache"
OUT_PATH = REPO / "data" / "outs_starts.parquet"

# Columns pulled from the pitch-level cache. Verified present in all 545
# non-empty cache files as of 2026-08-06.
PITCH_COLS = [
    "game_pk",
    "game_date",
    "game_year",
    "game_type",
    "inning",
    "inning_topbot",
    "at_bat_number",
    "pitch_number",
    "outs_when_up",
    "pitcher",
    "events",
    "home_team",
    "away_team",
    "bat_score",
    "post_bat_score",
    "post_fld_score",
    "n_thruorder_pitcher",
    "pitcher_days_since_prev_game",
    "des",
]

# Outs charged to the plate appearance's own terminal event. Calibrated
# empirically: for interior PAs with no pitching change the outs_when_up
# difference is unambiguous, and the mode of that difference per event value
# gives this table (measured over 497,112 PAs, 2024-2026).
EVENT_OWN_OUTS_2 = {
    "grounded_into_double_play", "double_play", "strikeout_double_play",
    "sac_fly_double_play", "sac_bunt_double_play",
}
EVENT_OWN_OUTS_0 = {
    "single", "double", "triple", "home_run", "walk", "intent_walk",
    "hit_by_pitch", "field_error", "catcher_interf", "fielders_choice",
    "truncated_pa", "",
}
# Events whose own description already names a retired baserunner, so an
# "out at 2nd" phrase in `des` cannot explain an EXTRA out beyond `own`.
EVENT_NAMES_RUNNER_OUT = EVENT_OWN_OUTS_2 | {
    "force_out", "fielders_choice_out", "triple_play",
}
# A runner retired on the play itself (as opposed to a pickoff or caught
# stealing between plate appearances).
DES_OUT_ON_PLAY = (
    r"out at |thrown out|doubled off|caught stealing|picked off|"
    r"double play|out stretching|out advancing|is out"
)

HIT_EVENTS = {"single", "double", "triple", "home_run"}
WALK_EVENTS = {"walk", "intent_walk"}
K_EVENTS = {"strikeout", "strikeout_double_play"}
REACHED_EVENTS = HIT_EVENTS | WALK_EVENTS | {
    "hit_by_pitch",
    "field_error",
    "catcher_interf",
    "fielders_choice",
}


# --------------------------------------------------------------------------
# stage 1: pitch rows -> plate-appearance rows
# --------------------------------------------------------------------------
def _cache_files() -> list[Path]:
    return sorted(CACHE_DIR.glob("*/*.parquet"))


def _readable(path: Path) -> bool:
    """26 cache files (off-days, All-Star break) carry a ZERO-COLUMN schema and
    raise pyarrow.ArrowInvalid on a columns= read. Guard before touching them."""
    try:
        return len(pq.ParquetFile(path).schema_arrow.names) > 0
    except Exception:
        return False


def _file_to_pa(path: Path) -> pd.DataFrame:
    """Reduce one day of pitch rows to one row per plate appearance."""
    df = pq.read_table(path, columns=PITCH_COLS).to_pandas()
    if df.empty:
        return df

    df = df[df["game_type"] == "R"]
    if df.empty:
        return df

    df = df.sort_values(["game_pk", "at_bat_number", "pitch_number"], kind="mergesort")

    n_pitches = (
        df.groupby(["game_pk", "at_bat_number"], sort=False)
        .size()
        .rename("pa_pitches")
    )

    # The last pitch of a PA carries the terminal `events` and the post-play
    # score. outs_when_up is constant within a PA, so any row would do for it.
    pa = df.drop_duplicates(subset=["game_pk", "at_bat_number"], keep="last").copy()
    pa = pa.merge(n_pitches, on=["game_pk", "at_bat_number"], how="left")
    return pa


def build_pa_table(verbose: bool = True) -> pd.DataFrame:
    files = [f for f in _cache_files() if _readable(f)]
    if verbose:
        print(f"[pa] reading {len(files)} non-empty cache files")
    frames = []
    for i, f in enumerate(files):
        frames.append(_file_to_pa(f))
        if verbose and (i + 1) % 100 == 0:
            print(f"[pa]   {i + 1}/{len(files)}")
    pa = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    pa["game_date"] = pd.to_datetime(pa["game_date"])
    for c in ("game_pk", "inning", "at_bat_number", "outs_when_up", "pitcher",
              "bat_score", "post_bat_score", "post_fld_score", "game_year"):
        pa[c] = pd.to_numeric(pa[c], errors="coerce").astype("Int64")
    if verbose:
        print(f"[pa] {len(pa):,} plate appearances, "
              f"{pa['game_pk'].nunique():,} regular-season games")
    return pa


# --------------------------------------------------------------------------
# stage 2: outs on play
# --------------------------------------------------------------------------
def add_outs_on_play(pa: pd.DataFrame) -> pd.DataFrame:
    """Difference outs_when_up within each half-inning. See module docstring."""
    pa = pa.sort_values(["game_pk", "at_bat_number"], kind="mergesort").reset_index(drop=True)

    half = ["game_pk", "inning", "inning_topbot"]
    grp = pa.groupby(half, sort=False)

    nxt = grp["outs_when_up"].shift(-1)
    is_last_pa_of_half = nxt.isna()

    owu = pa["outs_when_up"].astype("float64")
    outs = np.where(
        is_last_pa_of_half,
        3.0 - owu,
        np.maximum(0.0, nxt.astype("float64") - owu),
    )

    # Walk-off: the final PA of the game, in a bottom half, on which the batting
    # team scored and finished ahead. The half-inning ends without a third out.
    last_ab = pa.groupby("game_pk", sort=False)["at_bat_number"].transform("max")
    walkoff = (
        (pa["at_bat_number"] == last_ab)
        & (pa["inning_topbot"] == "Bot")
        & (pa["post_bat_score"] > pa["bat_score"])
        & (pa["post_bat_score"] > pa["post_fld_score"])
    ).fillna(False).to_numpy()

    outs = np.where(walkoff, 0.0, outs)
    pa["outs_on_play"] = np.clip(outs, 0, 3).astype("int16")
    pa["is_walkoff_pa"] = walkoff

    pa = _reattribute_pitching_change_outs(pa)
    return pa


def _reattribute_pitching_change_outs(pa: pd.DataFrame) -> pd.DataFrame:
    """Fix out attribution across a mid-inning pitching change.

    Differencing outs_when_up charges every out that occurs between PA i and
    PA i+1 to PA i. When the pitcher changes in that gap, an out recorded on
    the bases (a pickoff or caught stealing by the RELIEVER, before he faced
    his first batter) is wrongly charged to the departing starter.

    Measured on 13,170 starts: 54 starts (0.41%) carry an excess out at their
    exit PA. Resolving all 54 against the MLB boxscore, 36 belonged to the
    reliever and 18 were genuinely the starter's (a runner retired on the play
    itself). `des` separates the two: the rule below reproduced all 54/54.
    """
    ev = pa["events"].fillna("").astype(str)
    own = np.where(
        ev == "triple_play", 3,
        np.where(ev.isin(EVENT_OWN_OUTS_2), 2,
                 np.where(ev.isin(EVENT_OWN_OUTS_0), 0, 1)),
    ).astype("int16")

    same_half = (
        (pa["game_pk"].shift(-1) == pa["game_pk"])
        & (pa["inning"].shift(-1) == pa["inning"])
        & (pa["inning_topbot"].shift(-1) == pa["inning_topbot"])
    )
    changed = same_half & (pa["pitcher"].shift(-1) != pa["pitcher"])
    excess = (pa["outs_on_play"] - own).clip(lower=0)

    des_out = pa["des"].fillna("").astype(str).str.contains(DES_OUT_ON_PLAY, case=False, regex=True)
    # The departing pitcher keeps the extra out only when `des` shows a runner
    # was retired on the play, and the event itself is not what that phrase
    # describes.
    keeps = des_out & ~ev.isin(EVENT_NAMES_RUNNER_OUT)

    move = (changed & (excess > 0) & ~keeps).fillna(False).to_numpy()
    n_move = int(move.sum())
    if n_move:
        amt = excess.to_numpy()
        o = pa["outs_on_play"].to_numpy().copy()
        idx = np.flatnonzero(move)
        o[idx] -= amt[idx]
        o[idx + 1] += amt[idx]          # credit the incoming pitcher
        pa["outs_on_play"] = o.astype("int16")
    pa.attrs["reattributed_pas"] = n_move
    return pa


def validate_half_innings(pa: pd.DataFrame) -> dict:
    s = pa.groupby(["game_pk", "inning", "inning_topbot"], sort=False)["outs_on_play"].sum()
    n = len(s)
    exact = int((s == 3).sum())
    return {
        "half_innings": n,
        "sum_to_3": exact,
        "pct_sum_to_3": 100.0 * exact / n,
        "sum_lt_3": int((s < 3).sum()),
        "sum_gt_3": int((s > 3).sum()),
    }


# --------------------------------------------------------------------------
# stage 3: PA rows -> per-start rows
# --------------------------------------------------------------------------
def build_starts(pa: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """One row per (game, starting pitcher). Exactly 2 x n_games rows expected."""
    # -- starter = pitcher of the first PA of each half of inning 1
    first1 = (
        pa[pa["inning"] == 1]
        .sort_values(["game_pk", "inning_topbot", "at_bat_number"], kind="mergesort")
        .drop_duplicates(subset=["game_pk", "inning_topbot"], keep="first")
        [["game_pk", "inning_topbot", "pitcher"]]
        .rename(columns={"pitcher": "starter"})
    )

    m = pa.merge(first1, on=["game_pk", "inning_topbot"], how="inner")
    m = m[m["pitcher"] == m["starter"]]

    ev = m["events"].fillna("")
    m["is_hit"] = ev.isin(HIT_EVENTS)
    m["is_walk"] = ev.isin(WALK_EVENTS)
    m["is_k"] = ev.isin(K_EVENTS)
    m["is_reached"] = ev.isin(REACHED_EVENTS)
    m["runs_on_play"] = (m["post_bat_score"] - m["bat_score"]).astype("float64")

    g = m.groupby(["game_pk", "inning_topbot", "starter"], sort=False)
    starts = g.agg(
        game_date=("game_date", "first"),
        game_year=("game_year", "first"),
        home_team=("home_team", "first"),
        away_team=("away_team", "first"),
        outs=("outs_on_play", "sum"),
        bf=("at_bat_number", "size"),
        pitches=("pa_pitches", "sum"),
        runs_allowed=("runs_on_play", "sum"),
        hits=("is_hit", "sum"),
        walks=("is_walk", "sum"),
        strikeouts=("is_k", "sum"),
        reached_base=("is_reached", "sum"),
        max_inning=("inning", "max"),
        tto_max=("n_thruorder_pitcher", "max"),
        days_since_prev_game=("pitcher_days_since_prev_game", "first"),
    ).reset_index().rename(columns={"starter": "pitcher"})

    # inning_topbot == 'Top' means the AWAY team is batting -> HOME team pitching
    starts["is_home"] = (starts["inning_topbot"] == "Top")
    starts["pitcher_team"] = np.where(starts["is_home"], starts["home_team"], starts["away_team"])
    starts["opponent_team"] = np.where(starts["is_home"], starts["away_team"], starts["home_team"])

    # game-level context: needed to tell "manager did not send him back out"
    # apart from "there was no next inning to send him out for".
    gmax = pa.groupby("game_pk", sort=False)["inning"].max().rename("game_max_inning")
    starts = starts.merge(gmax, on="game_pk", how="left")

    starts["outs"] = starts["outs"].astype("int16")
    for c in ("bf", "pitches", "hits", "walks", "strikeouts", "reached_base",
              "max_inning", "game_max_inning"):
        starts[c] = pd.to_numeric(starts[c], errors="coerce").astype("Int32")
    starts["tto_max"] = pd.to_numeric(starts["tto_max"], errors="coerce").astype("Int32")
    starts["days_since_prev_game"] = pd.to_numeric(
        starts["days_since_prev_game"], errors="coerce").astype("Int32")
    starts["runs_allowed"] = starts["runs_allowed"].astype("float32")

    cols = [
        "game_pk", "game_date", "game_year", "pitcher", "pitcher_team",
        "opponent_team", "is_home", "outs", "bf", "pitches", "runs_allowed",
        "hits", "walks", "strikeouts", "reached_base", "max_inning", "tto_max",
        "days_since_prev_game", "game_max_inning", "inning_topbot",
    ]
    starts = starts[cols].sort_values(["game_date", "game_pk", "is_home"]).reset_index(drop=True)
    if verbose:
        print(f"[starts] {len(starts):,} starts")
    return starts


# --------------------------------------------------------------------------
# atomic write (CLAUDE.md: tempfile + fsync + os.replace, never bypass)
# --------------------------------------------------------------------------
def atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp.parquet")
    os.close(fd)
    try:
        df.to_parquet(tmp, index=False)
        # Windows: fsync requires a writable handle ("rb" gives EBADF).
        with open(tmp, "r+b") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def build(verbose: bool = True) -> pd.DataFrame:
    pa = build_pa_table(verbose=verbose)
    pa = add_outs_on_play(pa)
    v = validate_half_innings(pa)
    if verbose:
        print(f"[validate] {v['sum_to_3']:,}/{v['half_innings']:,} half-innings "
              f"sum to exactly 3 ({v['pct_sum_to_3']:.2f}%); "
              f"<3: {v['sum_lt_3']:,}  >3: {v['sum_gt_3']:,}")

    starts = build_starts(pa, verbose=verbose)

    n_games = pa["game_pk"].nunique()
    if verbose:
        print(f"[validate] games={n_games:,}  starts={len(starts):,}  "
              f"expected={2 * n_games:,}  "
              f"{'OK' if len(starts) == 2 * n_games else 'MISMATCH'}")
        bad = int(((starts["outs"] < 0) | (starts["outs"] > 27)).sum())
        print(f"[validate] starts with outs outside 0..27: {bad}")
    return starts


def load_outs_starts(force: bool = False, verbose: bool = False) -> pd.DataFrame:
    if OUT_PATH.exists() and not force:
        return pd.read_parquet(OUT_PATH)
    df = build(verbose=verbose)
    atomic_write_parquet(df, OUT_PATH)
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild even if cached")
    a = ap.parse_args()
    if OUT_PATH.exists() and not a.force:
        print(f"{OUT_PATH} exists; use --force to rebuild")
        return 0
    df = build(verbose=True)
    atomic_write_parquet(df, OUT_PATH)
    print(f"wrote {OUT_PATH}  rows={len(df):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
