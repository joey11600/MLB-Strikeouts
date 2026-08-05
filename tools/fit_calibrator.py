"""Fit the isotonic calibrator on honest out-of-sample predictions.

Input: data/backtest_predictions.csv (written by backtest.py — eval
models trained strictly before the test window, features as-of).

Honesty protocol:
  1. Cross-fit check — split test games into two halves by game_pk
     parity, fit on each half, evaluate on the other. The reported
     calibrated Brier is never self-graded.
  2. Final calibrator — fit on ALL out-of-sample predictions, saved to
     models/calibrator.pkl for the live pipeline.

The calibrator maps any raw tail probability P(K >= x) to a calibrated
one; it is fit pooled across lines (the map is p -> p, line-agnostic).

Usage: python tools/fit_calibrator.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.calibration import IsotonicCalibrator, brier_score, CALIBRATOR_PATH

PREDICTIONS_PATH = Path(__file__).parent.parent / "data" / "backtest_predictions.csv"


def main():
    if not PREDICTIONS_PATH.exists():
        print(f"No predictions file at {PREDICTIONS_PATH}. Run backtest.py first.")
        sys.exit(1)

    df = pd.read_csv(PREDICTIONS_PATH)
    print(f"Loaded {len(df)} out-of-sample predictions "
          f"({df['game_pk'].nunique()} games)")

    raw = df["model_p_over"].values
    outcomes = df["actual_over"].values.astype(float)

    print(f"\nRaw model Brier: {brier_score(raw, outcomes):.4f}")

    # --- Cross-fit: never grade a calibrator on the data it saw ---
    half_a = df["game_pk"] % 2 == 0
    half_b = ~half_a

    calibrated_oos = np.zeros(len(df))
    for fit_mask, eval_mask in [(half_a, half_b), (half_b, half_a)]:
        cal = IsotonicCalibrator()
        cal.fit(raw[fit_mask.values], outcomes[fit_mask.values])
        calibrated_oos[eval_mask.values] = [
            cal.predict(p) for p in raw[eval_mask.values]
        ]

    cal_brier = brier_score(calibrated_oos, outcomes)
    print(f"Cross-fit calibrated Brier: {cal_brier:.4f}")

    print("\nPer-line bias (predicted minus actual over-rate):")
    print(f"{'Line':>6} {'Raw bias':>10} {'Cal bias':>10}")
    for line in sorted(df["line"].unique()):
        m = df["line"] == line
        raw_bias = raw[m.values].mean() - outcomes[m.values].mean()
        cal_bias = calibrated_oos[m.values].mean() - outcomes[m.values].mean()
        print(f"{line:>6.1f} {raw_bias:>+10.1%} {cal_bias:>+10.1%}")

    # --- Final calibrator on all OOS predictions ---
    final = IsotonicCalibrator()
    final.fit(raw, outcomes)
    final.save(CALIBRATOR_PATH)
    print(f"\nFinal calibrator (fit on all {len(df)} rows) saved to {CALIBRATOR_PATH}")

    demo_points = [0.05, 0.15, 0.30, 0.50, 0.70, 0.85]
    print("\nCalibration map (raw -> calibrated):")
    for p in demo_points:
        print(f"  {p:.2f} -> {final.predict(p):.3f}")


if __name__ == "__main__":
    main()
