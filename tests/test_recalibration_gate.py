"""A calibrator refit must not promote itself on thin evidence (A-041).

The operator asked for a recalibration after a 4W-8L stretch. Twelve
bets is noise, and the live sample behind the refit is ~130 rows per
side of the split — enough to produce a Brier score that looks better
and nowhere near enough to act on. A calibrator is the last thing
between a wrong probability and a staked bet, so the refusal has to be
a tested property, not a paragraph in a docstring.

The gate also encodes the finding that motivated it: the model is well
calibrated where it AGREES with the market and inverted where it does
not (adverse selection). No univariate p -> p map fixes that, so a
model significantly worse than the market blocks promotion outright,
however good the refit's own numbers look.

Run:  python -m pytest tests/test_recalibration_gate.py -q
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

import tools.recalibrate_live as rc  # noqa: E402

PASSING_Z = rc.MIN_Z + 1.0   # significant, and in the right direction
BIG_N = rc.MIN_TEST_ROWS + 50


def test_thin_sample_blocks_promotion():
    """The defect this exists to prevent: shipping on ~130 rows."""
    reasons = rc.promotion_blockers(n_test=137, z_refit=-PASSING_Z,
                                    z_market=0.0)

    assert reasons, "a 137-row held-out sample was allowed to promote"
    assert any("137" in r for r in reasons)


def test_an_indistinguishable_refit_blocks_promotion():
    """Better-looking is not better. z=-0.89 was the real measurement."""
    reasons = rc.promotion_blockers(n_test=BIG_N, z_refit=-0.89, z_market=0.0)

    assert any("not distinguishable" in r for r in reasons), reasons


def test_a_refit_that_is_significantly_worse_blocks_promotion():
    """Significance alone is not the test — direction matters.

    |z| > MIN_Z is satisfied just as well by a refit that is reliably
    WORSE, and a gate keyed only on magnitude would wave it through.
    """
    reasons = rc.promotion_blockers(n_test=BIG_N, z_refit=+PASSING_Z,
                                    z_market=0.0)

    assert any("WORSE" in r for r in reasons), reasons


def test_losing_to_the_market_blocks_promotion_on_its_own():
    """Adverse selection is not a calibration problem.

    Even with a large sample and a refit that beats production, a model
    that loses to the market must not ship a recalibration as the fix —
    the same probability is calibrated when it agrees with the book and
    inverted when it does not, which no p -> p map can express.
    """
    reasons = rc.promotion_blockers(n_test=BIG_N, z_refit=-PASSING_Z,
                                    z_market=PASSING_Z)

    assert any("worse than the market" in r for r in reasons), reasons


def test_gates_can_actually_pass():
    """A gate that can never open is not a gate, it is a wall.

    Large sample, refit significantly better, model not losing to the
    market -> no blockers. (CLAUDE.md still requires the 2-week shadow
    on top of this; that is a process step, not a computable one.)
    """
    assert rc.promotion_blockers(n_test=BIG_N, z_refit=-PASSING_Z,
                                 z_market=-1.0) == []


def test_paired_difference_signs_and_scale():
    """Negative mean = candidate beats baseline, and it must be PAIRED.

    Unpaired comparison of two Brier scores on 0/1 outcomes is swamped
    by row-to-row variance; the whole point is that both score the same
    rows.
    """
    y = np.array([1.0, 0.0, 1.0, 0.0] * 25)
    good = np.where(y == 1, 0.9, 0.1)
    bad = np.where(y == 1, 0.6, 0.4)

    m, se, z = rc.paired(good, bad, y)
    assert m < 0, "the better predictor did not score negative"
    assert se > 0 and z < 0

    m_rev, _, z_rev = rc.paired(bad, good, y)
    assert m_rev > 0 and z_rev > 0
    assert np.isclose(m, -m_rev), "paired difference is not antisymmetric"


def test_brier_is_the_plain_definition():
    y = np.array([1.0, 0.0])
    assert np.isclose(rc.brier([1.0, 0.0], y), 0.0)
    assert np.isclose(rc.brier([0.0, 1.0], y), 1.0)
    assert np.isclose(rc.brier([0.5, 0.5], y), 0.25)
