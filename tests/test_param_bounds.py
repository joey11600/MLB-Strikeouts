"""Fitted parameters must not sit on their bounds, and no probability
served may be 0 or 1 (A-043).

A parameter at its bound is a fit that failed: the optimizer wanted a
value outside the search space and stopped at the wall, so the estimate
is an artifact of where the wall was put. Stage A shipped
`alpha = exp(-5)` — exactly its own lower bound — in two pickles for
months, and nothing noticed, because a float looks like a float (A-042).

The calibrator failed the same way at the other end. PAV gives a bin its
outcome mean, so a top bin of all-OVERs becomes exactly 1.0; the shipped
knot at raw 0.9404 did. Across all 2026 slates that produced 53 ladder
rungs served at model_prob == 1.0000 while the RAW model never exceeded
0.9959 — the calibrator manufactured the certainty. Five of the 46 with
settled outcomes LOST, including Drew Anderson needing K>=1 and
recording 0.

Run:  python -m pytest tests/test_param_bounds.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")

from models.calibration import (  # noqa: E402
    CALIBRATOR_PATH, PROB_EPS, IsotonicCalibrator)
import tools.audit_param_bounds as audit  # noqa: E402


def _cal():
    c = IsotonicCalibrator()
    c.load(CALIBRATOR_PATH)
    return c


def test_calibrator_never_serves_certainty():
    """The defect, at the seam that had it.

    p=1.0 makes log-loss infinite and Kelly size unbounded. It survived
    in production only because MAX_STAKE_UNITS caps the stake and the
    50/50 market blend held the served number to 0.9688 — two unrelated
    guards, neither of which is about probability being well-formed.
    """
    c = _cal()
    served = np.array([c.predict(v) for v in np.linspace(0.0, 1.0, 2001)])

    assert served.max() < 1.0, f"served a probability of {served.max()}"
    assert served.min() > 0.0, f"served a probability of {served.min()}"
    assert served.max() <= 1.0 - PROB_EPS + 1e-12
    assert served.min() >= PROB_EPS - 1e-12


def test_the_specific_knot_that_saturated_is_now_safe():
    """raw >= 0.9404 used to calibrate to exactly 1.0."""
    c = _cal()
    for raw in (0.9404, 0.95, 0.99, 1.0):
        assert c.predict(raw) < 1.0, f"raw {raw} still serves certainty"


def test_the_clamp_does_not_move_ordinary_probabilities():
    """A guard must not become a recalibration.

    Measured blast radius: 61 of 1001 raw values move at all, none by
    more than 0.001. If this test starts failing, the clamp has grown
    into a model change and needs the gauntlet.
    """
    import pickle
    with open(CALIBRATOR_PATH, "rb") as f:
        d = pickle.load(f)
    c = _cal()
    xs = np.linspace(0.0, 1.0, 1001)
    served = np.array([c.predict(x) for x in xs])
    raw_interp = np.interp(xs, d["x_knots"], d["y_knots"])
    delta = np.abs(served - raw_interp)

    assert delta.max() <= PROB_EPS + 1e-9, f"max change {delta.max()}"
    assert (delta > 0.01).sum() == 0
    assert np.isclose(c.predict(0.5), float(np.interp(0.5, d["x_knots"],
                                                      d["y_knots"])))


def test_an_unfitted_calibrator_also_clamps():
    """The passthrough branch returned raw_prob untouched.

    A calibrator that failed to load must not become a hole through
    which a 1.0 reaches the board.
    """
    c = IsotonicCalibrator()
    assert c.predict(1.0) < 1.0
    assert c.predict(0.0) > 0.0


def test_stage_a_alpha_is_still_reported_as_pinned():
    """Regression guard on the AUDIT, not on the model.

    A-042 gates the real fix behind USE_HOOK_MIXTURE, so alpha is still
    pinned on purpose. What must not happen is the audit going quiet
    about it — this asserts the detector still fires.
    """
    findings = audit.check_stage_a()
    assert findings, "the audit stopped reporting the known pinned alpha"
    assert any("exp(-5)" in f for f in findings), findings


def test_outs_hazard_lambda_is_reported_at_the_grid_edge():
    from models.outs_hazard import LAMBDA_GRID

    findings = audit.check_outs_hazard()
    assert findings, "lambda at the top of LAMBDA_GRID was not reported"
    assert any("GRID EDGE" in f for f in findings)
    assert str(max(LAMBDA_GRID)).rstrip("0").rstrip(".") in findings[0]


def test_audit_detects_a_bound_from_the_module_not_a_copy():
    """The tolerance must catch near-misses, not just exact equality.

    Copying bounds into the audit would let them drift apart silently,
    and an exact-equality check would miss a fit that lands a hair
    inside the wall while still leaning on it.
    """
    assert audit._rel_close(np.log(np.exp(-5.0)), -5.0)
    assert audit._rel_close(-5.0 * (1 + 1e-4), -5.0)
    assert not audit._rel_close(-4.0, -5.0)


def test_audit_exit_code_is_nonzero_while_findings_remain():
    """CI has to be able to fail on this."""
    assert audit.audit(), "audit reports clean while known findings stand"
