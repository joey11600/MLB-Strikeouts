"""Distributional naive baseline for the Strikeouts Model.

The "dumb model" that every future model must beat.

For each start:
  1. p = pitcher's season K% (as-of, excluding this game)
  2. BF distribution = pitcher's own BF histogram (as-of)
  3. For each possible BF, compute Binomial(BF, p) distribution
  4. Weight by BF probability to get compound P(K = k)
  5. Compute P(K >= line) for each half-integer line (4.5, 5.5, ...)

Score with Brier score on the binary outcome K >= line.

This baseline captures pitcher talent and workload but ignores
matchups, TTO decay, park, weather, umpire, and all game-level
context.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached


def build_game_level(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-pitcher-per-game stats."""
    completed = df[df["events"].notna()].copy()
    ks = completed[completed["events"].isin(["strikeout", "strikeout_double_play"])]

    stats = completed.groupby(["game_pk", "pitcher", "game_date"]).agg(
        bf=("events", "count"),
    ).reset_index()

    k_counts = ks.groupby(["game_pk", "pitcher"]).size().reset_index(name="strikeouts")
    stats = stats.merge(k_counts, on=["game_pk", "pitcher"], how="left")
    stats["strikeouts"] = stats["strikeouts"].fillna(0).astype(int)

    stats = stats[stats["bf"] >= 9]
    stats = stats.sort_values(["pitcher", "game_date", "game_pk"])
    return stats


def compute_as_of_stats(pitcher_games: pd.DataFrame, game_pk: int):
    """Compute season K% and BF distribution BEFORE a given game."""
    prior = pitcher_games[pitcher_games["game_pk"] != game_pk]
    if prior.empty or len(prior) < 2:
        return None, None

    k_pct = prior["strikeouts"].sum() / prior["bf"].sum()

    bf_values = prior["bf"].values
    bf_min, bf_max = bf_values.min(), bf_values.max()
    bf_range = np.arange(max(1, bf_min - 3), bf_max + 4)
    bf_hist = np.zeros(len(bf_range))
    for bf in bf_values:
        idx = bf - bf_range[0]
        if 0 <= idx < len(bf_hist):
            bf_hist[idx] += 1
    bf_hist /= bf_hist.sum()

    return k_pct, dict(zip(bf_range.tolist(), bf_hist.tolist()))


def compound_binomial(k_pct: float, bf_dist: dict, max_k: int = 30) -> np.ndarray:
    """Compute compound K distribution: sum over BF of Binom(BF, p)."""
    k_dist = np.zeros(max_k + 1)
    for bf, bf_prob in bf_dist.items():
        bf = int(bf)
        for k in range(min(bf, max_k) + 1):
            k_dist[k] += bf_prob * binom.pmf(k, bf, k_pct)
    return k_dist


def prob_k_geq(k_dist: np.ndarray, line: float) -> float:
    """P(K >= ceil(line))."""
    threshold = int(np.ceil(line))
    if threshold >= len(k_dist):
        return 0.0
    return float(k_dist[threshold:].sum())


def evaluate_baseline(game_stats: pd.DataFrame, lines: list[float] = None) -> dict:
    """Run the naive baseline on all games and score."""
    if lines is None:
        lines = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]

    pitcher_groups = game_stats.groupby("pitcher")

    predictions = []
    for pitcher_id, pitcher_games in pitcher_groups:
        if len(pitcher_games) < 3:
            continue

        for _, game in pitcher_games.iterrows():
            k_pct, bf_dist = compute_as_of_stats(pitcher_games, game["game_pk"])
            if k_pct is None:
                continue

            k_dist = compound_binomial(k_pct, bf_dist)

            for line in lines:
                p_over = prob_k_geq(k_dist, line)
                actual_over = 1 if game["strikeouts"] >= np.ceil(line) else 0

                predictions.append({
                    "game_pk": game["game_pk"],
                    "pitcher": pitcher_id,
                    "actual_k": game["strikeouts"],
                    "actual_bf": game["bf"],
                    "as_of_k_pct": k_pct,
                    "line": line,
                    "p_over": p_over,
                    "actual_over": actual_over,
                    "brier": (p_over - actual_over) ** 2,
                })

    pred_df = pd.DataFrame(predictions)

    results = {"n_predictions": len(pred_df), "per_line": {}}
    for line in lines:
        line_df = pred_df[pred_df["line"] == line]
        if line_df.empty:
            continue
        brier = line_df["brier"].mean()
        actual_rate = line_df["actual_over"].mean()
        mean_pred = line_df["p_over"].mean()

        cal_bins = pd.cut(line_df["p_over"], bins=10)
        cal_groups = line_df.groupby(cal_bins, observed=True).agg(
            pred_mean=("p_over", "mean"),
            actual_mean=("actual_over", "mean"),
            count=("brier", "count"),
        )
        cal_error = 0.0
        for _, row in cal_groups.iterrows():
            if row["count"] >= 5:
                cal_error += row["count"] * (row["pred_mean"] - row["actual_mean"]) ** 2
        ece = np.sqrt(cal_error / len(line_df)) if len(line_df) > 0 else 0

        results["per_line"][line] = {
            "brier": brier,
            "actual_rate": actual_rate,
            "mean_pred": mean_pred,
            "bias": mean_pred - actual_rate,
            "ece": ece,
            "n": len(line_df),
        }

    all_brier = pred_df["brier"].mean()
    results["overall_brier"] = all_brier

    return results, pred_df


def main():
    print("Loading cached Statcast data...")
    df = load_cached(date(2026, 6, 1), date(2026, 8, 3))

    if df.empty:
        print("No data.")
        return

    print(f"Loaded {len(df):,} pitches")
    game_stats = build_game_level(df)
    print(f"{len(game_stats)} pitcher-game starts (>=9 BF)")

    lines = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]

    print("\nRunning naive baseline evaluation...")
    results, pred_df = evaluate_baseline(game_stats, lines)

    print(f"\n{'=' * 65}")
    print(f"NAIVE BASELINE: Binomial(season K%, historical BF dist)")
    print(f"{'=' * 65}")
    print(f"Total predictions: {results['n_predictions']}")
    print(f"Overall Brier score: {results['overall_brier']:.4f}\n")

    print(f"{'Line':>6}  {'Brier':>7}  {'Pred':>7}  {'Actual':>7}  {'Bias':>7}  {'ECE':>7}  {'N':>6}")
    print("-" * 55)
    for line in lines:
        if line not in results["per_line"]:
            continue
        r = results["per_line"][line]
        print(f"{line:>6.1f}  {r['brier']:>7.4f}  {r['mean_pred']:>6.1%}  {r['actual_rate']:>6.1%}  "
              f"{r['bias']:>+6.1%}  {r['ece']:>7.4f}  {r['n']:>6}")

    print(f"\nReference: coin-flip Brier = 0.2500")
    print(f"Baseline beats coin-flip by {0.25 - results['overall_brier']:.4f}")

    mid_line = 5.5
    if mid_line in results["per_line"]:
        r = results["per_line"][mid_line]
        print(f"\nAt the most common line ({mid_line}):")
        print(f"  Brier = {r['brier']:.4f}")
        print(f"  Model predicts {r['mean_pred']:.1%} over, actual is {r['actual_rate']:.1%}")
        print(f"  Bias = {r['bias']:+.1%}")

    out_path = Path("data/naive_baseline_predictions.csv")
    pred_df.to_csv(out_path, index=False)
    print(f"\nPredictions saved to {out_path}")


if __name__ == "__main__":
    main()
