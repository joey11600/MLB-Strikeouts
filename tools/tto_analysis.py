"""Compute K% by Times Through Order (TTO) from Statcast.

No published version of this statistic exists that controls for batter
quality. We compute it directly from pitch-level data:

TTO 1 = batters 1-9  (first time through the order)
TTO 2 = batters 10-18 (second time through)
TTO 3 = batters 19-27 (third time through)
TTO 4+ = batters 28+  (fourth+ time through, rare)

For each TTO bucket we report:
  - Raw K%
  - BF count
  - Adjusted K% (controlling for pitcher talent via season K%)
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached


def assign_tto(df: pd.DataFrame) -> pd.DataFrame:
    """Assign TTO bucket to each plate appearance.

    Uses at_bat_number within each game to determine which time through
    the order each batter represents for the pitcher currently facing them.
    """
    abs_completed = df[df["events"].notna()].copy()

    game_pitcher_groups = abs_completed.groupby(["game_pk", "pitcher"])

    records = []
    for (game_pk, pitcher_id), group in game_pitcher_groups:
        sorted_abs = group.sort_values("at_bat_number")
        bf_seq = 0
        for _, row in sorted_abs.iterrows():
            bf_seq += 1
            if bf_seq <= 9:
                tto = 1
            elif bf_seq <= 18:
                tto = 2
            elif bf_seq <= 27:
                tto = 3
            else:
                tto = 4
            records.append({
                "game_pk": game_pk,
                "pitcher": pitcher_id,
                "batter": row["batter"],
                "at_bat_number": row["at_bat_number"],
                "bf_seq": bf_seq,
                "tto": tto,
                "event": row["events"],
                "is_k": 1 if row["events"] in ("strikeout", "strikeout_double_play") else 0,
            })

    return pd.DataFrame(records)


def compute_tto_stats(tto_df: pd.DataFrame) -> pd.DataFrame:
    """Compute raw K% by TTO bucket."""
    stats = tto_df.groupby("tto").agg(
        bf=("is_k", "count"),
        strikeouts=("is_k", "sum"),
    ).reset_index()
    stats["k_pct"] = stats["strikeouts"] / stats["bf"]
    return stats


def compute_adjusted_tto(tto_df: pd.DataFrame) -> pd.DataFrame:
    """Compute TTO K% adjusted for pitcher talent.

    Method: for each pitcher, compute their overall K% across all TTOs.
    Then compute TTO-specific K% as a ratio to their overall.
    Average these ratios across pitchers (weighted by BF) to get
    the talent-controlled TTO effect.
    """
    pitcher_overall = tto_df.groupby("pitcher").agg(
        total_bf=("is_k", "count"),
        total_k=("is_k", "sum"),
    ).reset_index()
    pitcher_overall["overall_k_pct"] = pitcher_overall["total_k"] / pitcher_overall["total_bf"]
    pitcher_overall = pitcher_overall[pitcher_overall["total_bf"] >= 50]

    tto_df = tto_df.merge(
        pitcher_overall[["pitcher", "overall_k_pct"]],
        on="pitcher", how="inner"
    )

    pitcher_tto = tto_df.groupby(["pitcher", "tto"]).agg(
        bf=("is_k", "count"),
        k=("is_k", "sum"),
        overall_k_pct=("overall_k_pct", "first"),
    ).reset_index()
    pitcher_tto["tto_k_pct"] = pitcher_tto["k"] / pitcher_tto["bf"]
    pitcher_tto = pitcher_tto[pitcher_tto["bf"] >= 5]

    pitcher_tto["ratio"] = pitcher_tto["tto_k_pct"] / pitcher_tto["overall_k_pct"]

    adjusted = pitcher_tto.groupby("tto", group_keys=False).apply(
        lambda g: pd.Series({
            "n_pitchers": len(g),
            "total_bf": g["bf"].sum(),
            "weighted_ratio": np.average(g["ratio"], weights=g["bf"]),
            "ratio_std": g["ratio"].std(),
        }), include_groups=False,
    ).reset_index()

    league_k = tto_df["is_k"].mean()
    adjusted["adjusted_k_pct"] = adjusted["weighted_ratio"] * league_k

    return adjusted


def compute_position_in_order_stats(tto_df: pd.DataFrame) -> pd.DataFrame:
    """Compute K% by position in the batting order (1-9)."""
    tto_df["lineup_slot"] = ((tto_df["bf_seq"] - 1) % 9) + 1

    stats = tto_df.groupby("lineup_slot").agg(
        bf=("is_k", "count"),
        strikeouts=("is_k", "sum"),
    ).reset_index()
    stats["k_pct"] = stats["strikeouts"] / stats["bf"]
    return stats


def main():
    print("Loading cached Statcast data...")
    df = load_cached(date(2026, 6, 1), date(2026, 8, 3))

    if df.empty:
        print("No data.")
        return

    print(f"Loaded {len(df):,} pitches")

    print("Assigning TTO buckets...")
    tto_df = assign_tto(df)
    print(f"  {len(tto_df):,} plate appearances assigned")

    print("\n" + "=" * 60)
    print("K% BY TIMES THROUGH ORDER (RAW)")
    print("=" * 60)
    raw = compute_tto_stats(tto_df)
    for _, row in raw.iterrows():
        tto_label = f"TTO {int(row['tto'])}" if row['tto'] < 4 else "TTO 4+"
        print(f"  {tto_label:8s}  K% = {row['k_pct']:.1%}  ({int(row['strikeouts'])}/{int(row['bf'])} BF)")

    print("\n" + "=" * 60)
    print("K% BY TTO (ADJUSTED FOR PITCHER TALENT)")
    print("=" * 60)
    adjusted = compute_adjusted_tto(tto_df)
    league_k = tto_df["is_k"].mean()
    print(f"  League K% = {league_k:.1%}\n")
    for _, row in adjusted.iterrows():
        tto_label = f"TTO {int(row['tto'])}" if row['tto'] < 4 else "TTO 4+"
        ratio_str = f"{row['weighted_ratio']:.3f}"
        print(f"  {tto_label:8s}  Adj K% = {row['adjusted_k_pct']:.1%}  "
              f"(ratio = {ratio_str}, n={int(row['n_pitchers'])} pitchers, "
              f"{int(row['total_bf'])} BF)")

    tto1 = adjusted[adjusted["tto"] == 1]["adjusted_k_pct"].values[0]
    tto3 = adjusted[adjusted["tto"] == 3]["adjusted_k_pct"].values[0]
    decay = tto1 - tto3
    decay_pct = decay / tto1 * 100

    print(f"\n  TTO 1->3 decay: {tto1:.1%} -> {tto3:.1%} = -{decay:.1%} ({decay_pct:.0f}% decline)")

    print("\n" + "=" * 60)
    print("K% BY LINEUP SLOT (1-9)")
    print("=" * 60)
    slot_stats = compute_position_in_order_stats(tto_df)
    for _, row in slot_stats.iterrows():
        bar = "#" * int(row["k_pct"] * 200)
        print(f"  Slot {int(row['lineup_slot']):1d}  K% = {row['k_pct']:.1%}  {bar}  ({int(row['bf'])} BF)")


if __name__ == "__main__":
    main()
