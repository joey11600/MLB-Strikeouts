"""The pitch-limit cap in Stage A (AUDIT A-024).

This code path has NEVER executed in production -- data/manual_pitch_limits.csv
has only a header row -- which is exactly why it was wrong and nobody noticed.
The divisor was 4.0, chosen by eye.

Measured on 3,283 real 2026 starts by replaying pitches in order and counting
batters actually faced at the Nth pitch:

    limit 60 -> 15.83 BF (3.791)    limit 75 -> 19.68 BF (3.812)
    limit 90 -> 23.16 BF (3.885)    limit 100 -> 25.04 BF (3.993)

4.0 is right for a ~100-pitch outing, which is not a limit at all. Over the
60-90 range where real limits land it understated batters faced by 0.7-0.9,
and since P(over) moves ~2.45 points per batter, it silently suppressed OVER
by roughly 2 points on exactly the starts an operator flagged as shortened.

These tests exist because the failure mode is invisible: the path only fires
the first time someone enters a limit, and if it is wrong the board just
quietly prices those pitchers low.

Run:  python -m pytest tests/test_stage_a_pitch_limit.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from models.stage_a_bf import (  # noqa: E402
    PITCHES_PER_BF_UNDER_LIMIT,
    MODEL_PATH,
    StageA,
)

pytestmark = pytest.mark.skipif(
    not MODEL_PATH.exists(), reason="needs models/stage_a_fitted.pkl"
)


def _mean_bf(dist) -> float:
    return float(sum(n * p for n, p in enumerate(dist)))


def _features(prior_bf: float, limit: float | None) -> dict:
    return {
        "c1_bf_mean": prior_bf,
        "a3_season_k_pct_shrunk": 0.23,
        "c10_il_return": False,
        "c11_pitch_limit": limit,
        "c12_bp_heavy": False,
    }


def test_divisor_matches_the_measurement_not_a_round_number():
    """4.0 was eyeballed. Anything outside the measured 60-90 band is a
    regression, and 4.0 specifically is the old wrong value."""
    assert 3.7 <= PITCHES_PER_BF_UNDER_LIMIT <= 3.9, (
        f"divisor {PITCHES_PER_BF_UNDER_LIMIT} is outside the measured "
        "3.79-3.89 range for real pitch limits"
    )
    assert PITCHES_PER_BF_UNDER_LIMIT != 4.0


def test_the_cap_binds_and_lands_where_the_data_says():
    """A 75-pitch limit on a pitcher who would otherwise go deep must cap
    him near the 19.7 batters the data shows, not the 18.8 that 4.0 gave."""
    m = StageA()
    m.load()
    capped = _mean_bf(m.predict_bf_distribution(_features(25.0, 75)))

    assert capped == pytest.approx(75 / PITCHES_PER_BF_UNDER_LIMIT, abs=0.35)
    # The whole point of the change: strictly more batters than 4.0 allowed.
    assert capped > 75 / 4.0


def test_no_limit_means_no_cap():
    """The cap must not touch a pitcher with no announced limit."""
    m = StageA()
    m.load()
    free = _mean_bf(m.predict_bf_distribution(_features(25.0, None)))
    capped = _mean_bf(m.predict_bf_distribution(_features(25.0, 75)))
    assert free > capped


def test_cap_never_raises_a_short_pitcher():
    """min() semantics: a limit must only ever shorten, never lengthen.

    A reliever-ish 12-BF profile with a generous 95-pitch limit must not be
    inflated to 25 batters by the cap.
    """
    m = StageA()
    m.load()
    free = _mean_bf(m.predict_bf_distribution(_features(12.0, None)))
    with_limit = _mean_bf(m.predict_bf_distribution(_features(12.0, 95)))
    assert with_limit <= free + 1e-9
