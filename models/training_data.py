"""Training data assembler.

Builds flat (game, pitcher, features, outcome) rows from cached Statcast
data for model fitting. Enforces as-of anti-leakage: each game's features
are computed from data BEFORE that game.

Two output tables:
  1. game_level: one row per (game_pk, pitcher). Labels: actual_bf, actual_k.
     Used for Stage A (BF model) and the compound distribution baseline.
  2. batter_level: one row per (game_pk, pitcher, batter_seq). Label: is_k.
     Used for Stage B (per-batter K rate model).
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached
from features.pitcher import build_pitcher_features
from features.workload import _pitcher_bf_distribution


TTO_RATIOS = {1: 1.053, 2: 0.930, 3: 0.884}


def _extract_starters(df: pd.DataFrame) -> pd.DataFrame:
    """Identify starting pitchers from pitch-level data.

    A starter is the pitcher who threw the first pitch of the game
    for their team. We filter to starters who faced >= 9 batters.
    """
    completed = df[df["events"].notna()].copy()

    game_pitcher_bf = completed.groupby(["game_pk", "pitcher"]).agg(
        bf=("events", "count"),
    ).reset_index()

    game_pitcher_pitches = df.groupby(["game_pk", "pitcher"]).agg(
        n_pitches=("pitch_type", "count"),
    ).reset_index()
    game_pitcher_bf = game_pitcher_bf.merge(game_pitcher_pitches,
                                             on=["game_pk", "pitcher"], how="left")

    starters = game_pitcher_bf[game_pitcher_bf["bf"] >= 9].copy()

    ks = completed[completed["events"].isin(["strikeout", "strikeout_double_play"])]
    k_counts = ks.groupby(["game_pk", "pitcher"]).size().reset_index(name="actual_k")
    starters = starters.merge(k_counts, on=["game_pk", "pitcher"], how="left")
    starters["actual_k"] = starters["actual_k"].fillna(0).astype(int)
    starters.rename(columns={"bf": "actual_bf"}, inplace=True)

    game_dates = df.groupby("game_pk")["game_date"].first().reset_index()
    starters = starters.merge(game_dates, on="game_pk", how="left")

    return starters


def _build_batter_sequence(df: pd.DataFrame, game_pk: int,
                            pitcher_id: int) -> pd.DataFrame:
    """Build the ordered batter sequence for one pitcher in one game.

    Returns a DataFrame with columns: bf_seq, batter, is_k, tto,
    batter_hand, pitcher_hand.
    """
    game_pitches = df[(df["game_pk"] == game_pk) & (df["pitcher"] == pitcher_id)]
    completed = game_pitches[game_pitches["events"].notna()].copy()
    completed = completed.sort_values("at_bat_number")

    records = []
    for seq, (_, row) in enumerate(completed.iterrows(), 1):
        if seq <= 9:
            tto = 1
        elif seq <= 18:
            tto = 2
        elif seq <= 27:
            tto = 3
        else:
            tto = 4

        records.append({
            "bf_seq": seq,
            "batter": row["batter"],
            "is_k": 1 if row["events"] in ("strikeout", "strikeout_double_play") else 0,
            "tto": tto,
            "batter_hand": row.get("stand", "R"),
            "pitcher_hand": row.get("p_throws", "R"),
        })

    return pd.DataFrame(records)


def build_game_level_data(start_date: date, end_date: date,
                           quick: bool = False) -> pd.DataFrame:
    """Build game-level training data with pitcher features.

    If quick=True, skip expensive feature computation and just return
    the outcome table (for fast iteration on model structure).
    """
    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    starters = _extract_starters(df)
    print(f"  {len(starters)} starter appearances extracted")

    if quick:
        return starters

    rows = []
    for i, (_, starter) in enumerate(starters.iterrows()):
        game_pk = starter["game_pk"]
        pitcher_id = starter["pitcher"]
        game_date_val = pd.Timestamp(starter["game_date"]).date()

        if i % 50 == 0:
            print(f"  Building features: {i}/{len(starters)}...", flush=True)

        try:
            pitcher_feats = build_pitcher_features(pitcher_id, game_pk, game_date_val)
        except Exception:
            pitcher_feats = {}

        flat_feats = {}
        for k, v in pitcher_feats.items():
            if isinstance(v, (dict, list)):
                continue
            flat_feats[k] = v

        row = {
            "game_pk": game_pk,
            "pitcher": pitcher_id,
            "game_date": game_date_val,
            "actual_bf": starter["actual_bf"],
            "actual_k": starter["actual_k"],
            "n_pitches": starter.get("n_pitches", 0),
            **flat_feats,
        }
        rows.append(row)

    return pd.DataFrame(rows)


def build_batter_level_data(start_date: date, end_date: date) -> pd.DataFrame:
    """Build batter-level training data for Stage B.

    One row per (game, pitcher, batter_in_sequence) with TTO assignment
    and the is_k label.
    """
    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    starters = _extract_starters(df)

    all_rows = []
    for i, (_, starter) in enumerate(starters.iterrows()):
        game_pk = starter["game_pk"]
        pitcher_id = starter["pitcher"]

        seq = _build_batter_sequence(df, game_pk, pitcher_id)
        if seq.empty:
            continue

        seq["game_pk"] = game_pk
        seq["pitcher"] = pitcher_id
        seq["game_date"] = starter["game_date"]
        all_rows.append(seq)

    if not all_rows:
        return pd.DataFrame()

    return pd.concat(all_rows, ignore_index=True)


def main():
    print("Building training data (quick mode)...")
    game_df = build_game_level_data(date(2026, 6, 1), date(2026, 8, 3), quick=True)
    print(f"\nGame-level: {len(game_df)} rows")
    print(f"  BF range: {game_df['actual_bf'].min()}-{game_df['actual_bf'].max()}")
    print(f"  K range: {game_df['actual_k'].min()}-{game_df['actual_k'].max()}")
    print(f"  Mean BF: {game_df['actual_bf'].mean():.1f}, Mean K: {game_df['actual_k'].mean():.1f}")

    print("\nBuilding batter-level data...")
    batter_df = build_batter_level_data(date(2026, 6, 1), date(2026, 8, 3))
    print(f"Batter-level: {len(batter_df)} rows")
    print(f"  K rate: {batter_df['is_k'].mean():.3f}")
    print(f"  TTO distribution:")
    for tto in sorted(batter_df["tto"].unique()):
        sub = batter_df[batter_df["tto"] == tto]
        print(f"    TTO {tto}: {len(sub)} BF, K% = {sub['is_k'].mean():.1%}")


if __name__ == "__main__":
    main()
