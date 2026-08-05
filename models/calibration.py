"""Isotonic calibration on P(K >= line).

Uses Pool-Adjacent-Violators (PAV) for monotonic isotonic regression.
Calibrates the raw compound distribution output to produce well-calibrated
probabilities for betting decisions.

Calibration, not accuracy, is the product. Gate 5 enforces this:
does adding a feature improve the Brier score and calibration curve?
Point-estimate RMSE improvements mean nothing when 72% of the variance
is irreducible.
"""
import numpy as np


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
        """Return calibrated probability via linear interpolation."""
        if self._x_knots is None:
            return raw_prob

        return float(np.interp(raw_prob, self._x_knots, self._y_knots))
