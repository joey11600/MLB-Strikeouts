"""Re-gauntlet the promoted Stage B features on the honest harness.

The original gauntlet (Phase 6) promoted a9_zone_pct, f1_eastward_tz,
and b14_n_rookies using leaky full-window feature aggregates and
~800-game within-2026 splits. This re-test uses the Phase 9
cross-season machinery: strictly as-of features, per-season priors,
train-season-only fits, three splits.

Design — drop-one marginal value:
  Variants: FULL (all 3), BASE (none), and FULL minus each feature.
  For each split, every variant scores the identical test starts, so
  each feature's value is a PAIRED per-start Brier delta:

      delta_i = brier_i(drop-F) - brier_i(full)     (mean across lines)

  delta > 0 means removing F hurts, i.e. F carries real signal.
  Per-start deltas give a mean and a t-statistic per split — no
  arbitrary noise floor needed; the sample does the work.

Verdict per feature (repo bar: both temporal directions must help):
  KEEP    mean delta > 0 with t >= 2 in BOTH cross-season directions
  DEMOTE  otherwise (marginal features don't ride)

Writes data/regauntlet_results.json. Runtime ~30-45 min.

Usage: python tools/regauntlet.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import SEASONS, SPLITS, _build_test_set, LINES
from models.stage_a_bf import StageA, prepare_training_data as prepare_stage_a
from models.stage_b_rate import (
    StageB, prepare_training_data as prepare_stage_b, EXTRA_FEATURES,
)
from models.compound import compound_k_distribution, prob_k_geq

RESULTS_PATH = Path(__file__).parent.parent / "data" / "regauntlet_results.json"

VARIANTS = {
    "full": list(EXTRA_FEATURES),
    "base": [],
    "drop_zone_pct": [f for f in EXTRA_FEATURES if f != "zone_pct"],
    "drop_eastward_tz": [f for f in EXTRA_FEATURES if f != "eastward_tz"],
    "drop_n_rookies": [f for f in EXTRA_FEATURES if f != "n_rookies"],
}


def _predict_start_brier(stage_a: StageA, stage_b: StageB, row,
                         lineup_kpcts: list[float]) -> float:
    """Mean Brier across LINES for one start, one Stage B variant."""
    features = {
        "a3_season_k_pct_shrunk": row.asof_k_pct_shrunk,
        "c1_bf_mean": row.asof_bf_mean,
        "c10_il_return": False,
        "c11_pitch_limit": None,
        "c12_bp_heavy": False,
    }
    bf_dist = stage_a.predict_bf_distribution(features)
    per_batter = stage_b.predict_per_batter_k_prob(
        row.asof_k_pct_shrunk, lineup_kpcts, n_max=40,
        zone_pct=row.zone_pct, eastward_tz=row.eastward_tz,
        n_rookies=row.n_rookies,
    )
    k_dist = compound_k_distribution(bf_dist, per_batter)

    total = 0.0
    for line in LINES:
        p = prob_k_geq(k_dist, line)
        actual = 1.0 if row.actual_k >= np.ceil(line) else 0.0
        total += (p - actual) ** 2
    return total / len(LINES)


def run_split(split_name: str) -> dict:
    cfg = SPLITS[split_name]
    train_label = "+".join(str(y) for y in cfg["train"])
    print(f"\n{'#' * 72}")
    print(f"SPLIT {split_name}: train {train_label} -> test {cfg['test']}")
    print(f"{'#' * 72}")

    print("Preparing training data (as-of, per-season)...")
    train_a = pd.concat(
        [prepare_stage_a(*SEASONS[y]) for y in cfg["train"]], ignore_index=True
    )
    train_b = pd.concat(
        [prepare_stage_b(*SEASONS[y]) for y in cfg["train"]], ignore_index=True
    )
    print(f"  Stage A rows: {len(train_a)}, Stage B rows: {len(train_b)}")

    stage_a = StageA()
    stage_a.fit(train_a)

    print("Building test set...")
    test_df, lineup_dict = _build_test_set(cfg["test"])
    print(f"  {len(test_df)} test starts")

    variant_models = {}
    variant_coefs = {}
    for vname, extras in VARIANTS.items():
        sb = StageB(extra_features=extras)
        sb.fit(train_b)
        variant_models[vname] = sb
        variant_coefs[vname] = dict(zip(sb.feature_names, [round(float(c), 4) for c in sb.coefficients]))

    print("Scoring variants on identical test starts...")
    briers = {vname: np.zeros(len(test_df)) for vname in VARIANTS}
    rows = list(test_df.itertuples())
    for i, row in enumerate(rows):
        if i % 1000 == 0:
            print(f"  {i}/{len(rows)}...", flush=True)
        lineup = lineup_dict.get((row.game_pk, row.pitcher), [0.225] * 9)
        for vname, sb in variant_models.items():
            briers[vname][i] = _predict_start_brier(stage_a, sb, row, lineup)

    out = {
        "split": split_name,
        "n_starts": len(test_df),
        "variant_brier": {v: round(float(b.mean()), 5) for v, b in briers.items()},
        "full_coefficients": variant_coefs["full"],
        "features": {},
    }

    print(f"\n  {'variant':>18} {'Brier':>9}")
    for vname, b in briers.items():
        print(f"  {vname:>18} {b.mean():>9.5f}")

    for feat in EXTRA_FEATURES:
        drop_name = f"drop_{feat}"
        delta = briers[drop_name] - briers["full"]  # >0 => dropping hurts
        mean = float(delta.mean())
        se = float(delta.std(ddof=1) / np.sqrt(len(delta)))
        t = mean / se if se > 0 else 0.0
        out["features"][feat] = {
            "mean_delta": round(mean, 7),
            "se": round(se, 7),
            "t": round(t, 2),
            "helps": bool(mean > 0),
            "significant": bool(t >= 2.0),
        }
        print(f"  {feat:>18}: drop-delta {mean:+.6f} (t={t:+.2f}) "
              f"{'HELPS' if mean > 0 else 'no value'}")

    base_delta = briers["base"] - briers["full"]
    bmean = float(base_delta.mean())
    bse = float(base_delta.std(ddof=1) / np.sqrt(len(base_delta)))
    out["trio_vs_base"] = {
        "mean_delta": round(bmean, 7),
        "t": round(bmean / bse, 2) if bse > 0 else 0.0,
    }

    return out


def main():
    split_results = [run_split(name) for name in SPLITS]

    cross_dirs = [r for r in split_results if r["split"] in ("24to25", "25to24")]
    verdicts = {}
    for feat in EXTRA_FEATURES:
        keep = all(
            r["features"][feat]["helps"] and r["features"][feat]["significant"]
            for r in cross_dirs
        )
        verdicts[feat] = "KEEP" if keep else "DEMOTE"

    print(f"\n{'=' * 72}")
    print("RE-GAUNTLET VERDICTS (bar: helps with t>=2 in BOTH cross-directions)")
    print(f"{'=' * 72}")
    for feat, v in verdicts.items():
        per_split = "  ".join(
            f"{r['split']}: {r['features'][feat]['mean_delta']:+.6f} (t={r['features'][feat]['t']:+.1f})"
            for r in split_results
        )
        print(f"  {feat:>14} -> {v:6}  {per_split}")

    payload = {
        "harness": "cross-season as-of drop-one (Phase 9)",
        "bar": "drop-delta > 0 with t >= 2 in both cross-season directions",
        "verdicts": verdicts,
        "splits": split_results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"\nResults saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
