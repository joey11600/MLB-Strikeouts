"""Refit production Stage A/B on all cached seasons.

Run ONLY after backtest.py's cross-season validation passes — this
overwrites the production pickles the live pipeline loads.

Training data: per-season as-of preparation (priors reset each season,
matching what the live pipeline serves), concatenated across
2024 + 2025 + 2026-to-date.

Usage: python tools/retrain_production.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import SEASONS
from models.stage_a_bf import StageA, prepare_training_data as prepare_stage_a
from models.stage_b_rate import StageB, prepare_training_data as prepare_stage_b


def main():
    years = sorted(SEASONS)
    label = "+".join(str(y) for y in years)
    print(f"Refitting PRODUCTION models on {label} (as-of, per-season priors)")

    frames_a = []
    for y in years:
        f = prepare_stage_a(*SEASONS[y])
        print(f"  Stage A {y}: {len(f)} rows")
        frames_a.append(f)
    train_a = pd.concat(frames_a, ignore_index=True)
    print(f"  Stage A total: {len(train_a)} rows")
    stage_a = StageA()
    stage_a.fit(train_a)
    stage_a.save()

    frames_b = []
    for y in years:
        f = prepare_stage_b(*SEASONS[y])
        print(f"  Stage B {y}: {len(f)} rows")
        frames_b.append(f)
    train_b = pd.concat(frames_b, ignore_index=True)
    print(f"  Stage B total: {len(train_b)} rows")
    stage_b = StageB()
    stage_b.fit(train_b)
    stage_b.save()

    print("\nProduction pickles updated (stage_a_fitted.pkl, stage_b_fitted.pkl).")
    print("Next: python tools/fit_calibrator.py  (refit on the new OOS predictions)")


if __name__ == "__main__":
    main()
