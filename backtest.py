"""Backtest harness — compound model vs naive baseline.

Runs both models on the same game set and compares:
- Brier score at each line
- Calibration (mean predicted vs actual rate)
- Sharpness (spread of predictions)

Uses as-of feature computation via pitcher season K% computed
from prior games only (same anti-leakage as the naive baseline).
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from data.backfill_statcast import load_cached
from models.stage_a_bf import StageA
from models.stage_b_rate import StageB
from models.compound import compound_k_distribution, prob_k_geq
from strikeout_predictor import StrikeoutPredictor

LINES = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]


def _build_test_set(start_date: date, end_date: date):
    """Build game-level test set with pitcher stats AND per-game lineup K rates.

    Returns (game_df, lineup_dict) where lineup_dict maps
    (game_pk, pitcher) -> list of 9 batter K% values.
    """
    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame(), {}

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    completed = df[df["events"].notna()]

    game_pitcher = completed.groupby(["game_pk", "pitcher"]).agg(
        actual_bf=("events", "count"),
    ).reset_index()
    game_pitcher = game_pitcher[game_pitcher["actual_bf"] >= 9]

    ks = completed[completed["events"].isin(["strikeout", "strikeout_double_play"])]
    k_counts = ks.groupby(["game_pk", "pitcher"]).size().reset_index(name="actual_k")
    game_pitcher = game_pitcher.merge(k_counts, on=["game_pk", "pitcher"], how="left")
    game_pitcher["actual_k"] = game_pitcher["actual_k"].fillna(0).astype(int)

    pitcher_stats = completed.groupby("pitcher").agg(
        season_bf=("events", "count"),
    ).reset_index()
    pitcher_k_counts = ks.groupby("pitcher").size().reset_index(name="season_k")
    pitcher_stats = pitcher_stats.merge(pitcher_k_counts, on="pitcher", how="left")
    pitcher_stats["season_k"] = pitcher_stats["season_k"].fillna(0)
    pitcher_stats["season_k_pct"] = pitcher_stats["season_k"] / pitcher_stats["season_bf"]
    pitcher_stats = pitcher_stats[pitcher_stats["season_bf"] >= 50]

    pitcher_bf_stats = game_pitcher.groupby("pitcher").agg(
        bf_mean=("actual_bf", "mean"),
        bf_std=("actual_bf", lambda x: x.std(ddof=1) if len(x) > 1 else 5.0),
        n_starts=("game_pk", "count"),
    ).reset_index()

    game_pitcher = game_pitcher.merge(
        pitcher_stats[["pitcher", "season_k_pct"]],
        on="pitcher", how="inner"
    )
    game_pitcher = game_pitcher.merge(
        pitcher_bf_stats[["pitcher", "bf_mean", "bf_std", "n_starts"]],
        on="pitcher", how="inner"
    )
    game_pitcher = game_pitcher[game_pitcher["n_starts"] >= 3]

    if "zone" in df.columns:
        zone_valid = df[df["zone"].notna()]
        pitcher_zone = zone_valid.groupby("pitcher").apply(
            lambda g: g["zone"].isin(range(1, 10)).sum() / len(g)
            if len(g) >= 50 else None,
            include_groups=False,
        ).reset_index(name="zone_pct")
        game_pitcher = game_pitcher.merge(pitcher_zone, on="pitcher", how="left")
        game_pitcher["zone_pct"] = game_pitcher["zone_pct"].fillna(0.45)
    else:
        game_pitcher["zone_pct"] = 0.45

    from features.t2_candidates import TEAM_TIMEZONES
    if "home_team" in df.columns and "game_date" in df.columns:
        game_teams = df.groupby("game_pk").agg(
            home_team=("home_team", "first"),
            game_date=("game_date", "first"),
        ).reset_index()
        gp_with_teams = game_pitcher.merge(game_teams, on="game_pk", how="left")
        gp_with_teams = gp_with_teams.sort_values(["pitcher", "game_date"])
        gp_with_teams["curr_tz"] = gp_with_teams["home_team"].map(TEAM_TIMEZONES)
        gp_with_teams["prev_tz"] = gp_with_teams.groupby("pitcher")["curr_tz"].shift(1)
        gp_with_teams["eastward_tz"] = (gp_with_teams["curr_tz"] - gp_with_teams["prev_tz"]).clip(lower=0).fillna(0)
        game_pitcher = game_pitcher.merge(
            gp_with_teams[["game_pk", "pitcher", "eastward_tz"]].drop_duplicates(),
            on=["game_pk", "pitcher"], how="left",
        )
        game_pitcher["eastward_tz"] = game_pitcher["eastward_tz"].fillna(0.0)
    else:
        game_pitcher["eastward_tz"] = 0.0

    batter_stats = completed.groupby("batter").agg(
        batter_bf=("events", "count"),
    ).reset_index()
    batter_k_counts = ks.groupby("batter").size().reset_index(name="batter_k")
    batter_stats = batter_stats.merge(batter_k_counts, on="batter", how="left")
    batter_stats["batter_k"] = batter_stats["batter_k"].fillna(0)
    batter_stats["batter_k_pct"] = batter_stats["batter_k"] / batter_stats["batter_bf"]
    batter_k_map = dict(zip(batter_stats["batter"], batter_stats["batter_k_pct"]))

    lineup_dict = {}
    starter_set = set(zip(game_pitcher["game_pk"], game_pitcher["pitcher"]))

    for (game_pk, pitcher_id), group in completed.groupby(["game_pk", "pitcher"]):
        if (game_pk, pitcher_id) not in starter_set:
            continue
        sorted_abs = group.sort_values("at_bat_number")
        seen = []
        for _, row in sorted_abs.iterrows():
            batter_id = row["batter"]
            if batter_id not in seen:
                seen.append(batter_id)
            if len(seen) >= 9:
                break

        lineup_kpcts = [batter_k_map.get(b, 0.225) for b in seen[:9]]
        while len(lineup_kpcts) < 9:
            lineup_kpcts.append(0.225)
        lineup_dict[(game_pk, pitcher_id)] = lineup_kpcts

    rookie_threshold = 100
    rookie_map = dict(zip(batter_stats["batter"], batter_stats["batter_bf"]))
    n_rookies_dict = {}
    for (game_pk, pitcher_id), group in completed.groupby(["game_pk", "pitcher"]):
        if (game_pk, pitcher_id) not in starter_set:
            continue
        sorted_abs = group.sort_values("at_bat_number")
        seen_batters = []
        for _, row in sorted_abs.iterrows():
            if row["batter"] not in seen_batters:
                seen_batters.append(row["batter"])
            if len(seen_batters) >= 9:
                break
        count = sum(1 for b in seen_batters if rookie_map.get(b, 0) < rookie_threshold)
        n_rookies_dict[(game_pk, pitcher_id)] = count

    game_pitcher["n_rookies"] = game_pitcher.apply(
        lambda r: n_rookies_dict.get((r["game_pk"], r["pitcher"]), 0), axis=1
    ).astype(float)

    return game_pitcher, lineup_dict


def _naive_baseline_prediction(season_k_pct: float, bf_mean: float,
                                bf_std: float) -> dict:
    """Simple binomial baseline: Binom(round(bf_mean), season_k_pct)."""
    from scipy.stats import binom

    bf_range = range(max(1, int(bf_mean - 2 * bf_std)),
                     int(bf_mean + 2 * bf_std) + 1)
    bf_probs = {}
    for bf in bf_range:
        z = (bf - bf_mean) / max(bf_std, 0.1)
        bf_probs[bf] = np.exp(-0.5 * z ** 2)
    total = sum(bf_probs.values())
    bf_probs = {k: v / total for k, v in bf_probs.items()}

    k_dist = np.zeros(41)
    for bf, bf_prob in bf_probs.items():
        for k in range(min(bf, 40) + 1):
            k_dist[k] += bf_prob * binom.pmf(k, bf, season_k_pct)

    per_line = {}
    for line in LINES:
        per_line[line] = prob_k_geq(k_dist, line)

    return per_line


def _compound_model_prediction(predictor: StrikeoutPredictor,
                                season_k_pct: float,
                                bf_mean: float,
                                lineup_k_pcts: list[float] | None = None,
                                zone_pct: float | None = None,
                                eastward_tz: float = 0.0,
                                n_rookies: float = 0.0) -> dict:
    """Two-stage compound model prediction."""
    features = {
        "a3_season_k_pct_shrunk": season_k_pct,
        "a3_season_k_pct_raw": season_k_pct,
        "c1_bf_mean": bf_mean,
        "a9_zone_pct": zone_pct,
        "f1_eastward_tz": eastward_tz,
        "b14_n_rookies": n_rookies,
        "c10_il_return": False,
        "c11_pitch_limit": None,
        "c12_bp_heavy": False,
    }

    result = predictor.predict(features, lineup_k_pcts=lineup_k_pcts, lines=LINES)
    return result["per_line"]


def run_backtest():
    """Run both models on the test set and compare."""
    print("Building test set...")
    test_df, lineup_dict = _build_test_set(date(2026, 6, 1), date(2026, 8, 3))
    print(f"  {len(test_df)} starter appearances")
    print(f"  {len(lineup_dict)} lineups extracted")

    print("Loading compound model...")
    predictor = StrikeoutPredictor()
    predictor.load_models()

    print("Running predictions...")
    naive_results = []
    model_results = []

    for i, (_, row) in enumerate(test_df.iterrows()):
        if i % 200 == 0:
            print(f"  {i}/{len(test_df)}...", flush=True)

        naive_preds = _naive_baseline_prediction(
            row["season_k_pct"], row["bf_mean"], row["bf_std"]
        )
        lineup_kpcts = lineup_dict.get(
            (row["game_pk"], row["pitcher"]), None
        )
        model_preds = _compound_model_prediction(
            predictor, row["season_k_pct"], row["bf_mean"],
            lineup_k_pcts=lineup_kpcts,
            zone_pct=row.get("zone_pct"),
            eastward_tz=row.get("eastward_tz", 0.0),
            n_rookies=row.get("n_rookies", 0.0),
        )

        for line in LINES:
            actual_over = 1 if row["actual_k"] >= np.ceil(line) else 0

            naive_results.append({
                "game_pk": row["game_pk"],
                "pitcher": row["pitcher"],
                "line": line,
                "p_over": naive_preds[line],
                "actual_over": actual_over,
                "actual_k": row["actual_k"],
            })

            model_results.append({
                "game_pk": row["game_pk"],
                "pitcher": row["pitcher"],
                "line": line,
                "p_over": model_preds[line],
                "actual_over": actual_over,
                "actual_k": row["actual_k"],
            })

    naive_df = pd.DataFrame(naive_results)
    model_df = pd.DataFrame(model_results)

    naive_df["brier"] = (naive_df["p_over"] - naive_df["actual_over"]) ** 2
    model_df["brier"] = (model_df["p_over"] - model_df["actual_over"]) ** 2

    print(f"\n{'=' * 70}")
    print(f"BACKTEST RESULTS: Compound Model vs Naive Baseline")
    print(f"{'=' * 70}")
    print(f"{'':>6}  {'--- Naive ---':>26}  {'--- Model ---':>26}  {'Improvement':>12}")
    print(f"{'Line':>6}  {'Brier':>7} {'Pred':>7} {'Bias':>7}  "
          f"{'Brier':>7} {'Pred':>7} {'Bias':>7}  {'Brier':>7} {'%':>4}")
    print("-" * 76)

    for line in LINES:
        n_df = naive_df[naive_df["line"] == line]
        m_df = model_df[model_df["line"] == line]

        n_brier = n_df["brier"].mean()
        n_pred = n_df["p_over"].mean()
        n_actual = n_df["actual_over"].mean()
        n_bias = n_pred - n_actual

        m_brier = m_df["brier"].mean()
        m_pred = m_df["p_over"].mean()
        m_actual = m_df["actual_over"].mean()
        m_bias = m_pred - m_actual

        improvement = n_brier - m_brier
        pct = improvement / n_brier * 100 if n_brier > 0 else 0

        print(f"{line:>6.1f}  {n_brier:>7.4f} {n_pred:>6.1%} {n_bias:>+6.1%}  "
              f"{m_brier:>7.4f} {m_pred:>6.1%} {m_bias:>+6.1%}  "
              f"{improvement:>+7.4f} {pct:>+3.0f}%")

    overall_naive = naive_df["brier"].mean()
    overall_model = model_df["brier"].mean()
    overall_imp = overall_naive - overall_model
    overall_pct = overall_imp / overall_naive * 100

    print("-" * 76)
    print(f"{'All':>6}  {overall_naive:>7.4f} {'':>7} {'':>7}  "
          f"{overall_model:>7.4f} {'':>7} {'':>7}  "
          f"{overall_imp:>+7.4f} {overall_pct:>+3.0f}%")

    print(f"\nReference: coin-flip Brier = 0.2500")

    print(f"\nSharpness (std of predictions):")
    for line in LINES:
        n_std = naive_df[naive_df["line"] == line]["p_over"].std()
        m_std = model_df[model_df["line"] == line]["p_over"].std()
        print(f"  {line}: naive={n_std:.4f}  model={m_std:.4f}  "
              f"{'sharper' if m_std > n_std else 'blunter'}")


if __name__ == "__main__":
    run_backtest()
