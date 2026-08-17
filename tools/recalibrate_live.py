"""Refit the calibrator on live 2026 rows — and decide whether to promote.

Written for A-041, where the operator's read was "the model itself is
wrong". It is, but not in the way the betting record suggests, and NOT
in a way a recalibration fixes. This tool exists so that conclusion is
re-derived from data every time rather than remembered.

What it does
------------
1. Within-2026 TIME split on `data/model_log.csv` (reconstructed rows
   excluded). CLAUDE.md prescribes a within-2026 time split for
   regime-scoped work in place of the two temporal directions, and a
   calibrator refit on live rows is exactly that.
2. Refits the isotonic map raw -> calibrated on the train window and
   scores it on the held-out window.
3. Sweeps MODEL_TRUST_WEIGHT (the market blend) over the same split.
4. PAIRED significance tests against production and against the market,
   because two Brier scores four decimals apart on ~120 rows are not a
   result.

Why it will usually refuse to promote
-------------------------------------
The finding it was written to guard is that the model is well calibrated
WHERE IT AGREES WITH THE MARKET and badly wrong where it does not:

    model vs market      n    model pred   actual     gap
    much UNDER          41        0.350     0.488   +0.138
    agrees              79        0.493     0.506   +0.014
    much OVER           26        0.638     0.308   -0.331

That is adverse selection, and a univariate p -> p map cannot fix it:
the same model probability is well calibrated in one column and inverted
in the other, so no single map is right for both. Worse, the edge filter
SELECTS the disagreement rows — the ones the table says are wrong.

The deeper gap it points at: `data/backtest_predictions.csv` carries no
odds. The model has only ever been scored against a NAIVE baseline, never
against the market. Beating naive on a synthetic line grid says nothing
about beating a book at the posted line, and live it does not.

Usage:
    python tools/recalibrate_live.py
    python tools/recalibrate_live.py --save-shadow   # writes, never promotes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from models.calibration import IsotonicCalibrator  # noqa: E402
from tracker import DATA_STATE_DIR  # noqa: E402

MODEL_LOG = DATA_STATE_DIR / "model_log.csv"
SHADOW_PATH = ROOT / "models" / "calibrator_live_shadow.pkl"

# Promotion gates. Both must pass, and they are deliberately strict:
# a calibrator is the last thing between a wrong probability and a
# staked bet, so "looks better" is not a standard.
MIN_TEST_ROWS = 300      # per side of the split
MIN_Z = 1.96             # paired, two-sided, against production


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def _sq(p, y):
    return (np.asarray(p, float) - np.asarray(y, float)) ** 2


def paired(candidate, baseline, y) -> tuple[float, float, float]:
    """Mean paired difference in squared error, its SE, and z.

    Negative mean = candidate beats baseline. Paired because both score
    the SAME rows, and the row-to-row variance of an outcome that is 0
    or 1 swamps the difference otherwise.
    """
    diff = _sq(candidate, y) - _sq(baseline, y)
    m = float(diff.mean())
    se = float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else 0.0
    return m, se, (m / se if se > 0 else 0.0)


def promotion_blockers(n_test: int, z_refit: float, z_market: float) -> list[str]:
    """Every reason NOT to promote. Empty list means the gates pass.

    Separate from main() so the refusal is testable: this is the last
    thing between a wrong probability and a staked bet, and a gate that
    is only exercised by running the whole script is a gate nobody
    checks. Fails CLOSED — an unknown is a blocker, not a pass.
    """
    reasons = []
    if n_test < MIN_TEST_ROWS:
        reasons.append(f"held-out sample {n_test} < {MIN_TEST_ROWS} rows")
    if abs(z_refit) < MIN_Z:
        reasons.append(f"refit not distinguishable from production "
                       f"(z={z_refit:+.2f}, need |z|>{MIN_Z})")
    elif z_refit > 0:
        reasons.append(f"refit is significantly WORSE than production "
                       f"(z={z_refit:+.2f})")
    if z_market > MIN_Z:
        reasons.append("the model is significantly worse than the market — "
                       "recalibration is the wrong lever for adverse selection")
    return reasons


def load_live() -> pd.DataFrame:
    df = pd.read_csv(MODEL_LOG)
    live = df[(df["reconstructed"] == 0) & (df["over_hit"].notna())].copy()
    return live.sort_values("date")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-shadow", action="store_true",
                    help="write the refit to a SHADOW path; never promotes")
    args = ap.parse_args()

    if not MODEL_LOG.exists():
        print(f"No model log at {MODEL_LOG}")
        return 1

    live = load_live()
    dates = sorted(live["date"].unique())
    if len(dates) < 4:
        print(f"Only {len(dates)} live date(s) — nothing to split on yet.")
        return 1

    cut = dates[len(dates) // 2]
    tr, te = live[live["date"] < cut], live[live["date"] >= cut]
    ytr = tr["over_hit"].values.astype(float)
    yte = te["over_hit"].values.astype(float)

    print(f"Live rows: {len(live)} over {len(dates)} date(s) "
          f"({dates[0]} .. {dates[-1]})")
    print(f"  train  {dates[0]} .. {cut} (exclusive)  n={len(tr)}")
    print(f"  test   {cut} .. {dates[-1]}             n={len(te)}")

    # --- refit on train only -------------------------------------------
    cal = IsotonicCalibrator()
    cal.fit(tr["p_over_raw"].values, ytr)
    refit = np.array([cal.predict(p) for p in te["p_over_raw"].values])
    fair_te = te["fair_over"].values
    refit_blend = 0.5 * refit + 0.5 * fair_te

    prod = te["blended_prob_over"].values
    contenders = {
        "production (blend w=0.5)": prod,
        "production calibrated   ": te["p_over_calibrated"].values,
        "REFIT calibrated        ": refit,
        "REFIT + blend w=0.5     ": refit_blend,
        "market only (w=0)       ": fair_te,
        "train base rate         ": np.full(len(te), ytr.mean()),
    }
    print("\n=== held-out Brier (lower is better) ===")
    for name, p in contenders.items():
        print(f"  {name}: {brier(p, yte):.4f}")

    print("\n=== paired vs production, held-out (negative = better) ===")
    for name, p in contenders.items():
        m, se, z = paired(p, prod, yte)
        tag = "indistinguishable" if abs(z) < MIN_Z else (
            "BETTER" if m < 0 else "worse")
        print(f"  {name}: {m:+.5f} +/- {se:.5f} (z={z:+.2f})  {tag}")

    # --- does the model beat the MARKET at all? ------------------------
    y_all = live["over_hit"].values.astype(float)
    m, se, z = paired(live["blended_prob_over"].values,
                      live["fair_over"].values, y_all)
    print(f"\n=== model vs MARKET, all {len(live)} live rows (paired) ===")
    print(f"  blended minus market: {m:+.5f} +/- {se:.5f} (z={z:+.2f})")
    if z > MIN_Z:
        print("  -> the model is SIGNIFICANTLY WORSE than the market.")
    elif z > 0:
        print("  -> the model trends worse than the market; not significant.")
    else:
        print("  -> the model is at least as good as the market here.")

    # --- trust-weight sweep -------------------------------------------
    print("\n=== market trust weight (fit on train, scored on test) ===")
    mp_tr, fo_tr = tr["p_over_calibrated"].values, tr["fair_over"].values
    mp_te, fo_te = te["p_over_calibrated"].values, fair_te
    rows = []
    for w in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        rows.append((w,
                     brier(w * mp_tr + (1 - w) * fo_tr, ytr),
                     brier(w * mp_te + (1 - w) * fo_te, yte)))
    print(f"  {'w':>5}{'train':>10}{'TEST':>10}")
    for w, b_tr, b_te in rows:
        print(f"  {w:>5.1f}{b_tr:>10.4f}{b_te:>10.4f}"
              f"{'   <- production' if abs(w - 0.5) < 1e-9 else ''}")
    best_w = min(rows, key=lambda r: r[1])[0]
    monotone = all(b >= a for (_, _, a), (_, _, b) in zip(rows, rows[1:]))
    print(f"  best w on train: {best_w:.1f}"
          + ("  (TEST Brier rises monotonically with model weight — every "
             "unit of model hurts)" if monotone else ""))

    # --- promotion decision -------------------------------------------
    _, _, z_refit = paired(refit_blend, prod, yte)
    print("\n=== PROMOTION GATE ===")
    reasons = promotion_blockers(len(te), z_refit, z)
    if reasons:
        print("  DO NOT PROMOTE:")
        for r in reasons:
            print(f"    - {r}")
    else:
        print("  gates pass — still requires the 2-week shadow per CLAUDE.md")

    if args.save_shadow:
        final = IsotonicCalibrator()
        final.fit(live["p_over_raw"].values, y_all)
        final.save(SHADOW_PATH)
        print(f"\nShadow calibrator written to {SHADOW_PATH}")
        print("  NOT promoted. models/calibrator.pkl is untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
