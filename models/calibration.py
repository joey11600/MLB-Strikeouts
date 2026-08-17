"""Isotonic calibration on P(K >= line).

Uses Pool-Adjacent-Violators (PAV) for monotonic isotonic regression.
Calibrates the raw compound distribution output to produce well-calibrated
probabilities for betting decisions.

Calibration, not accuracy, is the product. Gate 5 enforces this:
does adding a feature improve the Brier score and calibration curve?
Point-estimate RMSE improvements mean nothing when 72% of the variance
is irreducible.
"""
import pickle
from pathlib import Path

import numpy as np

CALIBRATOR_PATH = Path(__file__).parent / "calibrator.pkl"

# No probability this model serves may be 0 or 1 (A-043).
#
# PAV assigns each bin its outcome mean, so a top bin whose starts all
# went OVER becomes exactly 1.0 -- and the top knot of the shipped
# calibrator IS 1.0, at raw x=0.9404. Interpolation then drags the whole
# final segment toward certainty: measured across all 2026 slates, 53
# ladder rungs were served at model_prob == 1.0000 while the RAW model
# never exceeded 0.9959. The calibrator manufactured the certainty.
#
# Of the 46 of those with a settled outcome, **5 LOST** -- an 10.9%
# failure rate on events priced as impossible to lose:
#
#     2026-08-04  Davis Martin    needed K>=2, got 1
#     2026-08-05  Drew Anderson   needed K>=1, got 0
#     2026-08-05  Drew Anderson   needed K>=2, got 0
#     2026-08-05  Casey Mize      needed K>=2, got 1
#     2026-08-09  Davis Martin    needed K>=2, got 1
#
# Every one is a low milestone killed by a short outing -- the same
# early-hook tail Stage A cannot produce (A-042). The workload model
# hides disaster starts, so the calibrator never sees them fail, so it
# calls them certain.
#
# p=1.0 is not merely optimistic: it makes log-loss infinite and Kelly
# size unbounded (survived here only because MAX_STAKE_UNITS caps the
# stake and the 50/50 market blend held the served number to 0.9688).
#
# This is a GUARD, not a recalibration. It stops the model asserting the
# impossible; it does NOT fix the top bin actually being miscalibrated,
# which is A-041 and still open.
PROB_EPS = 1e-3


def pav_isotonic(values: list[float], weights: list[float], increasing: bool = True) -> list[float]:
    """Pool-Adjacent-Violators isotonic regression.

    Parameters
    ----------
    values : list of float
        Raw values to make monotonic.
    weights : list of float
        Weight for each value.
    increasing : bool
        If True, enforce non-decreasing; if False, non-increasing.

    Returns
    -------
    list of float
        Isotonically calibrated values.
    """
    n = len(values)
    if n == 0:
        return []

    pools = [[values[i], weights[i], [i]] for i in range(n)]

    def violates(v1, v2):
        if increasing:
            return v1 > v2
        return v1 < v2

    i = 0
    while i < len(pools) - 1:
        v1, w1, idx1 = pools[i]
        v2, w2, idx2 = pools[i + 1]
        if violates(v1, v2):
            new_v = (v1 * w1 + v2 * w2) / (w1 + w2)
            pools[i] = [new_v, w1 + w2, idx1 + idx2]
            del pools[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1

    result = [0.0] * n
    for v, w, indices in pools:
        for idx in indices:
            result[idx] = v
    return result


def brier_score(predicted: np.ndarray, actual: np.ndarray) -> float:
    """Brier score: mean squared error of probabilistic predictions."""
    return float(np.mean((predicted - actual) ** 2))


class IsotonicCalibrator:
    """Calibrates P(K >= line) via isotonic regression."""

    def __init__(self):
        self._x_knots: np.ndarray | None = None
        self._y_knots: np.ndarray | None = None

    def fit(self, raw_probs: np.ndarray, outcomes: np.ndarray):
        """Fit on held-out data. outcomes[i] = 1 if K >= line, else 0.

        Groups predictions into sorted bins and fits isotonic regression
        to map raw probabilities to calibrated ones.
        """
        order = np.argsort(raw_probs)
        sorted_probs = raw_probs[order]
        sorted_outcomes = outcomes[order]

        n = len(sorted_probs)
        if n < 10:
            self._x_knots = np.array([0.0, 1.0])
            self._y_knots = np.array([0.0, 1.0])
            return

        bin_size = max(1, n // 50)
        x_vals = []
        y_vals = []
        weights = []

        for start in range(0, n, bin_size):
            end = min(start + bin_size, n)
            x_vals.append(float(np.mean(sorted_probs[start:end])))
            y_vals.append(float(np.mean(sorted_outcomes[start:end])))
            weights.append(float(end - start))

        calibrated = pav_isotonic(y_vals, weights, increasing=True)

        self._x_knots = np.array(x_vals)
        self._y_knots = np.array(calibrated)

    def predict(self, raw_prob: float) -> float:
        """Return calibrated probability via linear interpolation.

        Clamped away from {0, 1}: see PROB_EPS. Applied on the way OUT
        rather than at fit time so an already-shipped calibrator.pkl is
        made safe without a refit — the saturated knot is still in the
        artifact, it just can no longer reach the board.
        """
        if self._x_knots is None:
            return float(np.clip(raw_prob, PROB_EPS, 1.0 - PROB_EPS))

        p = float(np.interp(raw_prob, self._x_knots, self._y_knots))
        return float(np.clip(p, PROB_EPS, 1.0 - PROB_EPS))

    @property
    def is_fitted(self) -> bool:
        return self._x_knots is not None

    def save(self, path: Path | None = None):
        path = path or CALIBRATOR_PATH
        with open(path, "wb") as f:
            pickle.dump({
                "x_knots": self._x_knots,
                "y_knots": self._y_knots,
            }, f)

    def load(self, path: Path | None = None):
        path = path or CALIBRATOR_PATH
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._x_knots = data["x_knots"]
        self._y_knots = data["y_knots"]
