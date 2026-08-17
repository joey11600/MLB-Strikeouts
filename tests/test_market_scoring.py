"""Scoring against the closing line must not flatter the model (A-041).

`backtest_predictions.csv` has no odds, so "+3.2% Brier vs naive" was
never a claim about beating a book. `tools/score_vs_market.py` answers
the real question on the only window with prices (2026-08-05 onward).

Three things can silently turn that answer from "worse" into "fine",
and each has a test here:

  1. A wrong tail sum. P(K > 5.5) is P(K >= 6); off by one and every
     probability is quietly shifted a whole strikeout.
  2. A wrong de-vig. The first implementation normalised the alt
     ladder's own implied PMF, which books post truncated -- it
     produced a median overround of 0.946, a book with NEGATIVE vig,
     and flipped the ladder verdict from "model worse" (z=+2.36) to
     "indistinguishable" (z=+0.03).
  3. Unclustered standard errors. Alt-line rows are one start measured
     at several thresholds; treating them as independent shrinks the SE
     and manufactures significance.

Run:  python -m pytest tests/test_market_scoring.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")

import tools.score_vs_market as sm  # noqa: E402


def test_p_over_is_the_right_tail():
    """Over 5.5 is K >= 6 — not K >= 5, and not K > 6."""
    k_dist = [0.0] * 10
    k_dist[5] = 0.4      # exactly 5 K
    k_dist[6] = 0.6      # exactly 6 K

    assert np.isclose(sm.p_over_from_kdist(k_dist, 5.5), 0.6)
    assert np.isclose(sm.p_over_from_kdist(k_dist, 4.5), 1.0)
    assert np.isclose(sm.p_over_from_kdist(k_dist, 6.5), 0.0)


def test_whole_number_milestone_is_the_at_least_sum():
    """A K>=6 alt and an over-5.5 line are the same event."""
    k_dist = [0.0] * 10
    k_dist[6] = 0.3
    k_dist[7] = 0.2

    assert np.isclose(sm.p_over_from_kdist(k_dist, 6), 0.5)
    assert np.isclose(sm.p_over_from_kdist(k_dist, 5.5),
                      sm.p_over_from_kdist(k_dist, 6))


def test_p_over_handles_lines_past_the_support():
    assert sm.p_over_from_kdist([0.5, 0.5], 40.5) == 0.0
    assert sm.p_over_from_kdist([], 4.5) is None


def _ladder(start="d:1", odds=(-300, -150, 120)):
    return pd.DataFrame({
        "start_key": [start] * len(odds),
        "line": [2.5, 3.5, 4.5][:len(odds)],
        "odds": list(odds),
    })


def test_ladder_devig_uses_the_measured_hold_not_its_own_sum():
    """The bug that flipped the verdict.

    Fair probabilities must come out BELOW the raw implied ones — that
    is what removing vig means. The old normaliser divided by a
    truncated PMF sum (< 1) and pushed them UP.
    """
    from models.edge import american_to_implied
    df = _ladder()
    out = sm.devig_ladder(df, holds={"d:1": 1.06})

    assert len(out) == 3
    for _, r in out.iterrows():
        raw_imp = american_to_implied(r["odds"])
        assert r["fair"] < raw_imp, (
            f"de-vig raised the probability ({r['fair']:.4f} >= "
            f"{raw_imp:.4f}) — vig was added, not removed"
        )
        assert np.isclose(r["fair"], raw_imp / 1.06)


def test_ladder_drops_starts_with_no_two_sided_quote():
    """No measured margin means no assumption — drop it, never guess."""
    out = sm.devig_ladder(_ladder(), holds={})
    assert out.empty, "a start with no two-sided quote was de-vigged anyway"


def test_ladder_survival_curve_is_forced_monotone():
    """A crossed quote would imply a negative P(K = n)."""
    df = _ladder(odds=(-300, 200, -400))   # deliberately non-monotone
    out = sm.devig_ladder(df, holds={"d:1": 1.05}).sort_values("line")
    fair = out["fair"].values
    assert all(a >= b for a, b in zip(fair, fair[1:])), (
        f"survival curve not non-increasing: {fair}"
    )


def test_clustering_widens_the_standard_error():
    """The statistical guard.

    Same start repeated many times carries no more information than one
    start. The clustered SE must reflect that; the naive one does not.
    """
    rng = np.random.default_rng(0)
    n_starts, per = 20, 10
    clusters, a, b, y = [], [], [], []
    for s in range(n_starts):
        shift = rng.normal(0, 0.3)          # a whole start runs hot or cold
        for _ in range(per):
            clusters.append(f"s{s}")
            out = float(rng.random() < 0.5)
            y.append(out)
            a.append(np.clip(0.5 + shift, 0.01, 0.99))
            b.append(0.5)

    _, se_naive, _ = sm.paired(a, b, y, clusters=None)
    _, se_clustered, _ = sm.paired(a, b, y, clusters=clusters)

    assert se_clustered > se_naive, (
        f"clustered SE {se_clustered:.5f} not wider than naive "
        f"{se_naive:.5f} — correlated rows are being counted as "
        f"independent evidence"
    )


def test_paired_is_antisymmetric_and_signed():
    rng = np.random.default_rng(7)
    y = (rng.random(200) < 0.5).astype(float)
    # Varying skill, so the paired differences are not a constant.
    good = np.clip(np.where(y == 1, 0.75, 0.25) + rng.normal(0, .05, 200), .01, .99)
    bad = np.clip(0.5 + rng.normal(0, .05, 200), .01, .99)

    m, se, z = sm.paired(good, bad, y)
    m_rev, _, z_rev = sm.paired(bad, good, y)

    assert m < 0 < m_rev, "the better predictor did not score negative"
    assert z < 0 < z_rev and se > 0
    assert np.isclose(m, -m_rev)


def test_a_zero_variance_difference_fails_closed():
    """No spread in the paired differences => z = 0, not a divide by zero.

    Degenerate rather than impossible: two predictors that differ by the
    same amount on every row produce a constant difference. Reporting
    z=0 (indistinguishable) is the conservative direction — the gates in
    tools/recalibrate_live.py read |z| and would otherwise promote on an
    infinity produced by a rounding artefact.
    """
    y = np.array([1.0, 0.0] * 20)
    good = np.where(y == 1, 0.9, 0.1)   # identical squared error every row
    bad = np.full(len(y), 0.5)

    m, se, z = sm.paired(good, bad, y)
    assert m < 0, "the better predictor should still score negative"
    assert se == 0.0 and z == 0.0
