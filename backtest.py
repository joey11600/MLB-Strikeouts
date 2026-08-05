"""Backtest harness — compound model vs naive baseline, honestly.

Split design (within-2026 time split; multi-season cache not yet
backfilled — see AUDIT):
  TRAIN: games on or before TRAIN_CUTOFF, as-of features
  TEST:  games after TRAIN_CUTOFF, as-of features

Anti-leakage guarantees:
  1. Stage A/B are fit ONLY on train-window games; no test game's
     outcome touches a coefficient.
  2. Every feature (pitcher K%, BF mean/std, zone%, batter K%,
     rookie counts) is computed strictly as-of via features.asof —
     only games BEFORE the predicted game contribute.
  3. The naive baseline gets the same as-of inputs, so the comparison
     is apples-to-apples.

Outputs data/backtest_predictions.csv (model + naive p_over per
game/line) for calibration fitting and the dashboard Model view.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from data.backfill_statcast import load_cached
from features.asof import asof_pitcher_game_table, asof_batter_game_table
from models.stage_a_bf import StageA, prepare_training_data as prepare_stage_a
from models.stage_b_rate import StageB, prepare_training_data as prepare_stage_b, ROOKIE_BF_THRESHOLD
from models.compound import prob_k_geq
from strikeout_predictor import StrikeoutPredictor

LINES = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]

TRAIN_START = date(2026, 6, 1)
TRAIN_CUTOFF = date(2026, 7, 8)
TEST_END = date(2026, 8, 3)

PREDICTIONS_PATH = Path(__file__).parent / "data" / "backtest_predictions.csv"
EVAL_STAGE_A_PATH = Path(__file__).parent / "models" / "stage_a_eval.pkl"
EVAL_STAGE_B_PATH = Path(__file__).parent / "models" / "stage_b_eval.pkl"


def _build_test_set(df: pd.DataFrame):
    """Build the honest test set: post-cutoff starts with as-of features.

    Returns (test_df, lineup_dict) where lineup_dict maps
    (game_pk, pitcher) -> list of 9 as-of batter K% values.
    """
    pt = asof_pitcher_game_table(df)
    bt = asof_batter_game_table(df)

    cutoff_ts = pd.Timestamp(TRAIN_CUTOFF)
    test = pt[
        (pt["actual_bf"] >= 9)
        & (pt["game_date"] > cutoff_ts)
        & (pt["prior_games"] >= 3)
        & (pt["prior_bf"] >= 50)
        & pt["asof_k_pct"].notna()
    ].copy()

    test["zone_pct"] = test["asof_zone_pct"].fillna(0.45)

    # As-of batter lookup: {(batter, game_pk): (shrunk_k_pct, prior_bf)}
    batter_map = {
        (row.batter, row.game_pk): (row.asof_k_pct_shrunk, row.prior_bf)
        for row in bt.itertuples()
    }

    completed = df[df["events"].notna()]
    starter_set = set(zip(test["game_pk"], test["pitcher"]))

    lineup_dict = {}
    n_rookies_dict = {}
    for (game_pk, pitcher_id), group in completed.groupby(["game_pk", "pitcher"]):
        if (game_pk, pitcher_id) not in starter_set:
            continue
        sorted_abs = group.sort_values("at_bat_number")
        seen = []
        for batter_id in sorted_abs["batter"]:
            if batter_id not in seen:
                seen.append(batter_id)
            if len(seen) >= 9:
                break

        kpcts = []
        rookies = 0
        for b in seen[:9]:
            k_pct, prior_bf = batter_map.get((b, game_pk), (None, 0))
            kpcts.append(k_pct if k_pct is not None and not np.isnan(k_pct) else 0.225)
            if (prior_bf or 0) < ROOKIE_BF_THRESHOLD:
                rookies += 1
        while len(kpcts) < 9:
            kpcts.append(0.225)

        lineup_dict[(game_pk, pitcher_id)] = kpcts
        n_rookies_dict[(game_pk, pitcher_id)] = float(rookies)

    test["n_rookies"] = test.apply(
        lambda r: n_rookies_dict.get((r["game_pk"], r["pitcher"]), 0.0), axis=1
    )

    return test, lineup_dict


def _naive_baseline_prediction(season_k_pct: float, bf_mean: float,
                                bf_std: float) -> dict:
    """Simple binomial baseline: Binom(round(bf_mean), season_k_pct)."""
    from scipy.stats import binom

    bf_std = max(bf_std if bf_std and not np.isnan(bf_std) else 5.0, 0.1)
    bf_range = range(max(1, int(bf_mean - 2 * bf_std)),
                     int(bf_mean + 2 * bf_std) + 1)
    bf_probs = {}
    for bf in bf_range:
        z = (bf - bf_mean) / bf_std
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


def fit_eval_models() -> tuple[StageA, StageB]:
    """Fit Stage A/B on the train window only, save eval pickles."""
    print(f"Fitting eval models on {TRAIN_START} .. {TRAIN_CUTOFF} (as-of features)...")

    train_a = prepare_stage_a(TRAIN_START, TRAIN_CUTOFF)
    print(f"  Stage A train rows: {len(train_a)}")
    stage_a = StageA()
    stage_a.fit(train_a)
    stage_a.save(EVAL_STAGE_A_PATH)

    train_b = prepare_stage_b(TRAIN_START, TRAIN_CUTOFF)
    print(f"  Stage B train rows: {len(train_b)}")
    stage_b = StageB()
    stage_b.fit(train_b)
    stage_b.save(EVAL_STAGE_B_PATH)

    return stage_a, stage_b


def run_backtest():
    """Fit on train window, predict the test window, compare, save."""
    print(f"Honest backtest: train <= {TRAIN_CUTOFF}, test {TRAIN_CUTOFF} < d <= {TEST_END}")

    stage_a, stage_b = fit_eval_models()

    print("Building test set (as-of features)...")
    df = load_cached(TRAIN_START, TEST_END)
    test_df, lineup_dict = _build_test_set(df)
    print(f"  {len(test_df)} starter appearances in test window")
    print(f"  {len(lineup_dict)} lineups extracted")

    predictor = StrikeoutPredictor()
    predictor.stage_a = stage_a
    predictor.stage_b = stage_b

    print("Running predictions...")
    rows = []

    for i, (_, row) in enumerate(test_df.iterrows()):
        if i % 200 == 0:
            print(f"  {i}/{len(test_df)}...", flush=True)

        naive_preds = _naive_baseline_prediction(
            row["asof_k_pct"], row["asof_bf_mean"], row["asof_bf_std"]
        )
        lineup_kpcts = lineup_dict.get((row["game_pk"], row["pitcher"]), None)
        model_preds = _compound_model_prediction(
            predictor, row["asof_k_pct_shrunk"], row["asof_bf_mean"],
            lineup_k_pcts=lineup_kpcts,
            zone_pct=row["zone_pct"],
            eastward_tz=row["eastward_tz"],
            n_rookies=row["n_rookies"],
        )

        for line in LINES:
            actual_over = 1 if row["actual_k"] >= np.ceil(line) else 0
            rows.append({
                "game_pk": row["game_pk"],
                "pitcher": row["pitcher"],
                "game_date": row["game_date"].date(),
                "line": line,
                "model_p_over": model_preds[line],
                "naive_p_over": naive_preds[line],
                "actual_over": actual_over,
                "actual_k": row["actual_k"],
                "asof_k_pct": row["asof_k_pct"],
                "asof_k_pct_shrunk": row["asof_k_pct_shrunk"],
                "asof_bf_mean": row["asof_bf_mean"],
            })

    pred_df = pd.DataFrame(rows)
    pred_df.to_csv(PREDICTIONS_PATH, index=False)
    print(f"\nSaved {len(pred_df)} predictions to {PREDICTIONS_PATH}")

    pred_df["model_brier"] = (pred_df["model_p_over"] - pred_df["actual_over"]) ** 2
    pred_df["naive_brier"] = (pred_df["naive_p_over"] - pred_df["actual_over"]) ** 2

    print(f"\n{'=' * 76}")
    print("HONEST BACKTEST: train <= "
          f"{TRAIN_CUTOFF} -> test through {TEST_END}, all features as-of")
    print(f"{'=' * 76}")
    print(f"{'':>6}  {'--- Naive ---':>26}  {'--- Model ---':>26}  {'Improvement':>12}")
    print(f"{'Line':>6}  {'Brier':>7} {'Pred':>7} {'Bias':>7}  "
          f"{'Brier':>7} {'Pred':>7} {'Bias':>7}  {'Brier':>7} {'%':>4}")
    print("-" * 76)

    for line in LINES:
        sub = pred_df[pred_df["line"] == line]
        n_brier = sub["naive_brier"].mean()
        n_bias = sub["naive_p_over"].mean() - sub["actual_over"].mean()
        m_brier = sub["model_brier"].mean()
        m_bias = sub["model_p_over"].mean() - sub["actual_over"].mean()
        improvement = n_brier - m_brier
        pct = improvement / n_brier * 100 if n_brier > 0 else 0

        print(f"{line:>6.1f}  {n_brier:>7.4f} {sub['naive_p_over'].mean():>6.1%} {n_bias:>+6.1%}  "
              f"{m_brier:>7.4f} {sub['model_p_over'].mean():>6.1%} {m_bias:>+6.1%}  "
              f"{improvement:>+7.4f} {pct:>+3.0f}%")

    overall_naive = pred_df["naive_brier"].mean()
    overall_model = pred_df["model_brier"].mean()
    overall_imp = overall_naive - overall_model
    overall_pct = overall_imp / overall_naive * 100

    print("-" * 76)
    print(f"{'All':>6}  {overall_naive:>7.4f} {'':>7} {'':>7}  "
          f"{overall_model:>7.4f} {'':>7} {'':>7}  "
          f"{overall_imp:>+7.4f} {overall_pct:>+3.0f}%")

    print(f"\nReference: coin-flip Brier = 0.2500")

    print(f"\nSharpness (std of predictions):")
    for line in LINES:
        sub = pred_df[pred_df["line"] == line]
        n_std = sub["naive_p_over"].std()
        m_std = sub["model_p_over"].std()
        print(f"  {line}: naive={n_std:.4f}  model={m_std:.4f}  "
              f"{'sharper' if m_std > n_std else 'blunter'}")

    return pred_df


if __name__ == "__main__":
    run_backtest()
