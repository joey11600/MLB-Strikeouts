"""Backtest harness — compound model vs naive baseline, honestly.

Cross-season splits (the repo's sanctioned three-way design):

    train 2024        -> test 2025
    train 2025        -> test 2024
    train 2024+2025   -> test 2026            (the decision split)

Anti-leakage guarantees:
  1. Stage A/B are fit ONLY on train-season games; no test game's
     outcome touches a coefficient.
  2. Every feature (pitcher K%, BF mean/std, zone%, batter K%, rookie
     counts) is computed strictly as-of via features.asof — only games
     BEFORE the predicted game contribute.
  3. Seasons are loaded separately, so priors RESET at season
     boundaries — matching what the live pipeline serves (it loads
     season-start..today).
  4. The naive baseline gets the same as-of inputs.

The decision split's predictions are saved to
data/backtest_predictions.csv (calibration fitting + dashboard Model
view); split metadata goes to data/backtest_meta.json.

Usage:
    python backtest.py             # all three splits
    python backtest.py 2425to26    # one split
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from data.backfill_statcast import load_cached
from features.asof import asof_pitcher_game_table, asof_batter_game_table
from models.stage_a_bf import StageA, prepare_training_data as prepare_stage_a
from models.stage_b_rate import (
    StageB, prepare_training_data as prepare_stage_b, ROOKIE_BF_THRESHOLD,
    PRODUCTION_EXTRA_FEATURES,
)
from models.compound import prob_k_geq
from strikeout_predictor import StrikeoutPredictor

LINES = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]

SEASONS = {
    2024: (date(2024, 3, 28), date(2024, 10, 31)),
    2025: (date(2025, 3, 27), date(2025, 10, 31)),
    2026: (date(2026, 3, 26), date(2026, 8, 4)),
}

SPLITS = {
    "24to25": {"train": [2024], "test": 2025},
    "25to24": {"train": [2025], "test": 2024},
    "2425to26": {"train": [2024, 2025], "test": 2026},
}
DECISION_SPLIT = "2425to26"

PREDICTIONS_PATH = Path(__file__).parent / "data" / "backtest_predictions.csv"
META_PATH = Path(__file__).parent / "data" / "backtest_meta.json"
EVAL_STAGE_A_PATH = Path(__file__).parent / "models" / "stage_a_eval.pkl"
EVAL_STAGE_B_PATH = Path(__file__).parent / "models" / "stage_b_eval.pkl"


def _build_test_set(test_year: int):
    """Build the honest test set for one season: as-of features only.

    Returns (test_df, lineup_dict) where lineup_dict maps
    (game_pk, pitcher) -> list of 9 as-of shrunk batter K% values.
    """
    lo, hi = SEASONS[test_year]
    df = load_cached(lo, hi)

    pt = asof_pitcher_game_table(df)
    bt = asof_batter_game_table(df)

    test = pt[
        (pt["actual_bf"] >= 9)
        & (pt["prior_games"] >= 3)
        & (pt["prior_bf"] >= 50)
        & pt["asof_k_pct"].notna()
    ].copy()

    test["zone_pct"] = test["asof_zone_pct"].fillna(0.45)

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
    """Two-stage compound model prediction (raw, uncalibrated)."""
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
    return result["per_line_raw"]


def fit_eval_models(train_years: list[int]) -> tuple[StageA, StageB]:
    """Fit Stage A/B on the train seasons only (per-season as-of prep)."""
    label = "+".join(str(y) for y in train_years)
    print(f"Fitting eval models on {label} (as-of features, per-season priors)...")

    frames_a = [prepare_stage_a(*SEASONS[y]) for y in train_years]
    train_a = pd.concat(frames_a, ignore_index=True)
    print(f"  Stage A train rows: {len(train_a)}")
    stage_a = StageA()
    stage_a.fit(train_a)
    stage_a.save(EVAL_STAGE_A_PATH)

    frames_b = [prepare_stage_b(*SEASONS[y]) for y in train_years]
    train_b = pd.concat(frames_b, ignore_index=True)
    print(f"  Stage B train rows: {len(train_b)} "
          f"(extras: {PRODUCTION_EXTRA_FEATURES or 'core only'})")
    stage_b = StageB(extra_features=PRODUCTION_EXTRA_FEATURES)
    stage_b.fit(train_b)
    stage_b.save(EVAL_STAGE_B_PATH)

    return stage_a, stage_b


def run_split(split_name: str) -> dict:
    """Run one train->test split. Returns summary; saves predictions CSV
    for the decision split."""
    cfg = SPLITS[split_name]
    train_label = "+".join(str(y) for y in cfg["train"])
    print(f"\n{'#' * 76}")
    print(f"SPLIT {split_name}: train {train_label} -> test {cfg['test']}")
    print(f"{'#' * 76}")

    stage_a, stage_b = fit_eval_models(cfg["train"])

    print(f"Building {cfg['test']} test set (as-of features)...")
    test_df, lineup_dict = _build_test_set(cfg["test"])
    print(f"  {len(test_df)} starter appearances")

    predictor = StrikeoutPredictor()
    predictor.stage_a = stage_a
    predictor.stage_b = stage_b

    print("Running predictions...")
    rows = []
    for i, (_, row) in enumerate(test_df.iterrows()):
        if i % 500 == 0:
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
    pred_df["model_brier"] = (pred_df["model_p_over"] - pred_df["actual_over"]) ** 2
    pred_df["naive_brier"] = (pred_df["naive_p_over"] - pred_df["actual_over"]) ** 2

    if split_name == DECISION_SPLIT:
        pred_df.drop(columns=["model_brier", "naive_brier"]).to_csv(
            PREDICTIONS_PATH, index=False
        )
        print(f"\nSaved {len(pred_df)} predictions to {PREDICTIONS_PATH}")

    print(f"\n{'=' * 76}")
    print(f"SPLIT {split_name}: train {train_label} -> test {cfg['test']}, all as-of")
    print(f"{'=' * 76}")
    print(f"{'':>6}  {'--- Naive ---':>26}  {'--- Model ---':>26}  {'Improvement':>12}")
    print(f"{'Line':>6}  {'Brier':>7} {'Pred':>7} {'Bias':>7}  "
          f"{'Brier':>7} {'Pred':>7} {'Bias':>7}  {'Brier':>7} {'%':>4}")
    print("-" * 76)

    per_line = []
    for line in LINES:
        sub = pred_df[pred_df["line"] == line]
        n_brier = sub["naive_brier"].mean()
        n_bias = sub["naive_p_over"].mean() - sub["actual_over"].mean()
        m_brier = sub["model_brier"].mean()
        m_bias = sub["model_p_over"].mean() - sub["actual_over"].mean()
        improvement = n_brier - m_brier
        pct = improvement / n_brier * 100 if n_brier > 0 else 0
        per_line.append({"line": line, "naive": n_brier, "model": m_brier, "pct": pct})

        print(f"{line:>6.1f}  {n_brier:>7.4f} {sub['naive_p_over'].mean():>6.1%} {n_bias:>+6.1%}  "
              f"{m_brier:>7.4f} {sub['model_p_over'].mean():>6.1%} {m_bias:>+6.1%}  "
              f"{improvement:>+7.4f} {pct:>+3.0f}%")

    overall_naive = pred_df["naive_brier"].mean()
    overall_model = pred_df["model_brier"].mean()
    overall_pct = (overall_naive - overall_model) / overall_naive * 100

    print("-" * 76)
    print(f"{'All':>6}  {overall_naive:>7.4f} {'':>7} {'':>7}  "
          f"{overall_model:>7.4f} {'':>7} {'':>7}  "
          f"{overall_naive - overall_model:>+7.4f} {overall_pct:>+3.0f}%")

    return {
        "split": split_name,
        "train": train_label,
        "test": str(cfg["test"]),
        "n_starts": int(test_df.shape[0]),
        "naive_brier": round(float(overall_naive), 4),
        "model_brier": round(float(overall_model), 4),
        "improvement_pct": round(float(overall_pct), 2),
        "per_line": [
            {"line": r["line"], "naive_brier": round(float(r["naive"]), 4),
             "model_brier": round(float(r["model"]), 4),
             "improvement_pct": round(float(r["pct"]), 1)}
            for r in per_line
        ],
    }


def run_backtest(only_split: str | None = None):
    summaries = []
    names = [only_split] if only_split else list(SPLITS.keys())
    for name in names:
        summaries.append(run_split(name))

    print(f"\n{'=' * 76}")
    print("CROSS-SEASON SUMMARY")
    print(f"{'=' * 76}")
    print(f"{'Split':>10} {'Train':>10} {'Test':>6} {'Starts':>7} "
          f"{'Naive':>8} {'Model':>8} {'Imp%':>6}")
    for s in summaries:
        print(f"{s['split']:>10} {s['train']:>10} {s['test']:>6} {s['n_starts']:>7} "
              f"{s['naive_brier']:>8.4f} {s['model_brier']:>8.4f} "
              f"{s['improvement_pct']:>+5.1f}%")

    if not only_split or only_split == DECISION_SPLIT:
        decision = next((s for s in summaries if s["split"] == DECISION_SPLIT), None)
        meta = {
            "splits": summaries,
            "decision_split": DECISION_SPLIT,
            "train_desc": "2024+2025 seasons",
            "test_window": "2026-03-26 to 2026-08-04",
            "train_cutoff_label": "2024+2025",
        }
        if decision:
            meta["n_starts"] = decision["n_starts"]
        with open(META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        print(f"\nSplit metadata saved to {META_PATH}")

    both_directions = all(
        s["improvement_pct"] > 0 for s in summaries
        if s["split"] in ("24to25", "25to24")
    )
    decision_ok = any(
        s["split"] == DECISION_SPLIT and s["improvement_pct"] > 0 for s in summaries
    )
    if len(summaries) == 3:
        print(f"\nVERDICT: cross-season directions positive: {both_directions}; "
              f"decision split positive: {decision_ok}")

    return summaries


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg is not None and arg not in SPLITS:
        print(f"Unknown split '{arg}'. Options: {', '.join(SPLITS)}")
        sys.exit(1)
    run_backtest(arg)
