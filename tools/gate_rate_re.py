"""Gate the per-start rate random effect (A-051): fit sigma, validate.

The defect (measured, 2026-08-24 audit): the served K distribution is
~10% short of realized variance on the full 2026 backtest and the
actual over-rate exceeds the model's at EVERY line (mean -1.2pp).
Mechanism: the Poisson-binomial is exactly binomial-dispersed at fixed
BF, but a real pitcher's true rate varies game to game around his
season estimate. compound_k_distribution_re adds that between-start
variance with a mean-preserving latent effect; this tool answers what
sigma the data wants and whether it wants it in BOTH temporal
directions (repo rule).

Design: for each cross-season split, fit Stage A/B on the train
seasons, build the honest as-of test set, then score a sigma grid on
the identical test starts:

  - NLL: -log P(K = actual_k) under the compound  (the distribution's
    own likelihood — the quantity a WIDTH parameter must be judged on;
    Brier at 6 lines barely sees the tails)
  - per-line Brier and bias for the audit's tail-bias readout

Verdict: KEEP if argmin-sigma > 0 with an NLL improvement in both
cross-season directions and the fitted sigma is consistent (within one
grid step). A KEEP earns a shadow column, never a production flip.

Writes data/gate_rate_re.json. Runtime ~10-20 min.

Usage: python tools/gate_rate_re.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import SEASONS, SPLITS, _build_test_set, LINES
from models.stage_a_bf import StageA, prepare_training_data as prepare_stage_a
from models.stage_b_rate import StageB, prepare_training_data as prepare_stage_b
from models.compound import compound_k_distribution_re, prob_k_geq

RESULTS_PATH = Path(__file__).parent.parent / "data" / "gate_rate_re.json"
SIGMA_GRID = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]


def run_split(split_name: str) -> dict:
    cfg = SPLITS[split_name]
    print(f"\n{'#' * 72}\nSPLIT {split_name}: train "
          f"{'+'.join(map(str, cfg['train']))} -> test {cfg['test']}\n{'#' * 72}")

    train_a = pd.concat([prepare_stage_a(*SEASONS[y]) for y in cfg["train"]],
                        ignore_index=True)
    train_b = pd.concat([prepare_stage_b(*SEASONS[y]) for y in cfg["train"]],
                        ignore_index=True)
    stage_a = StageA()
    stage_a.fit(train_a)
    stage_b = StageB(extra_features=[])
    stage_b.fit(train_b)

    test_df, lineup_dict = _build_test_set(cfg["test"])
    print(f"  {len(test_df)} test starts")

    nll = {s: 0.0 for s in SIGMA_GRID}
    brier = {s: np.zeros(len(LINES)) for s in SIGMA_GRID}
    bias_pred = {s: np.zeros(len(LINES)) for s in SIGMA_GRID}
    bias_act = np.zeros(len(LINES))
    n = 0

    rows = list(test_df.itertuples())
    for i, row in enumerate(rows):
        if i % 1000 == 0:
            print(f"  {i}/{len(rows)}...", flush=True)
        lineup = lineup_dict.get((row.game_pk, row.pitcher), [0.225] * 9)
        features = {
            "a3_season_k_pct_shrunk": row.asof_k_pct_shrunk,
            "c1_bf_mean": row.asof_bf_mean,
            "c10_il_return": False,
            "c11_pitch_limit": None,
        }
        bf_dist = stage_a.predict_bf_distribution(features)
        pb = stage_b.predict_per_batter_k_prob(
            row.asof_k_pct_shrunk, lineup, n_max=40)
        ak = int(min(max(row.actual_k, 0), 40))
        n += 1
        for j, line in enumerate(LINES):
            bias_act[j] += 1.0 if row.actual_k >= np.ceil(line) else 0.0
        for s in SIGMA_GRID:
            kd = compound_k_distribution_re(bf_dist, pb, s)
            nll[s] += -np.log(max(float(kd[ak]), 1e-12))
            for j, line in enumerate(LINES):
                p = prob_k_geq(kd, line)
                actual = 1.0 if row.actual_k >= np.ceil(line) else 0.0
                brier[s][j] += (p - actual) ** 2
                bias_pred[s][j] += p

    out = {"split": split_name, "n_starts": n, "grid": {}}
    print(f"\n  {'sigma':>6} {'NLL/start':>10} {'Brier':>8} {'mean bias (pp)':>14}")
    for s in SIGMA_GRID:
        avg_nll = nll[s] / n
        avg_brier = float(brier[s].mean() / n)
        bias = float(((bias_pred[s] - bias_act) / n).mean() * 100)
        out["grid"][str(s)] = {"nll": round(avg_nll, 5),
                               "brier": round(avg_brier, 5),
                               "mean_bias_pp": round(bias, 3)}
        print(f"  {s:>6.2f} {avg_nll:>10.5f} {avg_brier:>8.5f} {bias:>+14.3f}")

    best = min(SIGMA_GRID, key=lambda s: nll[s] / n)
    out["sigma_star"] = best
    out["nll_gain_vs_zero"] = round(nll[0.0] / n - nll[best] / n, 5)
    print(f"  sigma* = {best}  (NLL gain vs 0: {out['nll_gain_vs_zero']:+.5f}/start)")
    return out


def main():
    results = [run_split(name) for name in SPLITS]
    cross = [r for r in results if r["split"] in ("24to25", "25to24")]
    keep = all(r["sigma_star"] > 0 and r["nll_gain_vs_zero"] > 0 for r in cross)
    sigmas = [r["sigma_star"] for r in cross]
    consistent = abs(sigmas[0] - sigmas[1]) <= 0.051
    verdict = "KEEP" if (keep and consistent) else "REJECT"

    print(f"\n{'=' * 72}\nVERDICT: {verdict}  "
          f"(sigma* {sigmas} — needs >0 with NLL gain both directions, "
          f"within one grid step)\n{'=' * 72}")
    payload = {"harness": "cross-season as-of NLL grid (A-051)",
               "verdict": verdict, "splits": results}
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"Results saved to {RESULTS_PATH}")
    print("A KEEP earns a shadow column (A-046 pattern), never a "
          "production flip.")


if __name__ == "__main__":
    main()
