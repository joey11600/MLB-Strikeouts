"""A-051: the per-start rate random effect must widen without moving.

The measured defect: ~10% variance shortfall at scale with the actual
over-rate above the model's at every backtest line. The fix adds
between-start rate variance; these tests pin its two load-bearing
properties (exact sigma=0 passthrough, mean preservation) and the
behavior it exists for (variance and tail growth in sigma).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.compound import (
    RATE_RE_SIGMA, compound_k_distribution, compound_k_distribution_re)


def _bf_dist():
    # a plausible leash distribution centered ~22
    n = np.arange(41)
    d = np.exp(-0.5 * ((n - 22) / 5.0) ** 2)
    return d / d.sum()


PB = np.full(40, 0.23)


def test_sigma_zero_is_exact_passthrough():
    base = compound_k_distribution(_bf_dist(), PB)
    re0 = compound_k_distribution_re(_bf_dist(), PB, 0.0)
    np.testing.assert_allclose(re0, base)


def test_mean_preserved_across_sigma():
    k = np.arange(41)
    base_mean = float(k @ compound_k_distribution(_bf_dist(), PB))
    for s in (0.1, 0.15, 0.3):
        d = compound_k_distribution_re(_bf_dist(), PB, s)
        assert float(k @ d) == pytest.approx(base_mean, abs=2e-3)


def test_variance_and_tails_grow_with_sigma():
    k = np.arange(41)
    prev_var, prev_tail = -1.0, -1.0
    for s in (0.0, 0.1, 0.2, 0.3):
        d = compound_k_distribution_re(_bf_dist(), PB, s)
        m = float(k @ d)
        var = float(((k - m) ** 2) @ d)
        tail = float(d[9:].sum())
        assert var > prev_var
        assert tail > prev_tail
        prev_var, prev_tail = var, tail


def test_pmf_is_valid():
    d = compound_k_distribution_re(_bf_dist(), PB, RATE_RE_SIGMA)
    assert d.min() >= 0
    assert d.sum() == pytest.approx(1.0)


def test_shipped_sigma_matches_gate_result():
    """Pin the constant to the cross-season argmin so a silent retune
    can't drift it away from the recorded evidence."""
    assert RATE_RE_SIGMA == 0.15
