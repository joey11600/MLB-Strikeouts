"""End-to-end strikeout predictor.

Connects all components:
  Stage A (BF model) -> BF distribution P(BF = n)
  Stage B (K rate model) -> per-batter p_i with TTO decay
  Compound distribution -> P(K = k) via Poisson-binomial DP
  Calibration -> calibrated P(K >= line)

Usage:
    from strikeout_predictor import StrikeoutPredictor
    pred = StrikeoutPredictor()
    pred.load_models()
    result = pred.predict(pitcher_features, lineup_k_pcts)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from models.stage_a_bf import StageA
from models.stage_b_rate import StageB
from models.compound import compound_k_distribution, prob_k_geq
from models.calibration import IsotonicCalibrator, brier_score


class StrikeoutPredictor:
    """Full prediction pipeline: features -> P(K >= line)."""

    def __init__(self):
        self.stage_a = StageA()
        self.stage_b = StageB()
        self.calibrator = IsotonicCalibrator()

    def load_models(self):
        """Load fitted models from disk."""
        self.stage_a.load()
        self.stage_b.load()

    def predict(
        self,
        pitcher_features: dict,
        lineup_k_pcts: list[float] | None = None,
        lines: list[float] | None = None,
    ) -> dict:
        """Run the full prediction pipeline.

        Parameters
        ----------
        pitcher_features : dict
            Output of build_pitcher_features() + build_workload_features().
            Must contain at minimum: a3_season_k_pct_shrunk, c1_bf_mean.
        lineup_k_pcts : list of 9 floats
            Per-batter K% in batting order. Defaults to league avg.
        lines : list of float
            Lines to evaluate P(K >= line) for. Defaults to standard set.

        Returns
        -------
        dict with:
            bf_dist: P(BF = n) for n = 0..40
            k_dist: P(K = k) for k = 0..40
            expected_k: E[K]
            expected_bf: E[BF]
            per_line: {line: p_over} for each requested line
        """
        if lines is None:
            lines = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]
        if lineup_k_pcts is None:
            lineup_k_pcts = [0.225] * 9

        pitcher_k = pitcher_features.get("a3_season_k_pct_shrunk")
        if pitcher_k is None:
            pitcher_k = pitcher_features.get("a3_season_k_pct_raw", 0.225)
        if pitcher_k is None:
            pitcher_k = 0.225

        zone_pct = pitcher_features.get("a9_zone_pct")
        eastward_tz = pitcher_features.get("f1_eastward_tz", 0.0)
        n_rookies = pitcher_features.get("b14_n_rookies", 0.0)

        bf_dist = self.stage_a.predict_bf_distribution(pitcher_features)

        per_batter_probs = self.stage_b.predict_per_batter_k_prob(
            pitcher_k, lineup_k_pcts, n_max=40, zone_pct=zone_pct,
            eastward_tz=eastward_tz, n_rookies=n_rookies,
        )

        k_dist = compound_k_distribution(bf_dist, per_batter_probs)

        expected_k = float(np.sum(np.arange(len(k_dist)) * k_dist))
        expected_bf = float(np.sum(np.arange(len(bf_dist)) * bf_dist))

        per_line = {}
        for line in lines:
            per_line[line] = prob_k_geq(k_dist, line)

        return {
            "bf_dist": bf_dist,
            "k_dist": k_dist,
            "expected_k": expected_k,
            "expected_bf": expected_bf,
            "per_line": per_line,
            "pitcher_k_pct": pitcher_k,
            "per_batter_probs": per_batter_probs,
        }
