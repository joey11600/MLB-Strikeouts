"""The early-hook mixture must add a left tail WITHOUT moving the mean (A-042).

Batters faced is left-skewed (empirical -1.58 over 13,170 starts); a
negative binomial is right-skewed (+0.24). The production model
therefore prices a disaster start at 1-in-900 when it is 1-in-32, and
because a disaster settles every OVER as a loss and can never settle an
UNDER that way, the missing tail inflates P(over) on every pitcher.
That is the mechanism behind A-041's +5.0pp OVER lean.

The mixture is only an improvement if it fixes the tail and leaves
everything that already worked alone. The live mean BF error is +0.00
over 264 starts — genuinely unbiased — so a change that drags the mean
down would trade a tail bias for a mean bias and look like progress on
the metric being watched.

Run:  python -m pytest tests/test_hook_mixture.py -q
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

from models import stage_a_bf as sa  # noqa: E402

N = np.arange(41)


def _nb(mu, alpha):
    d = np.array([sa._negbin_pmf(n, mu, alpha) for n in N], float)
    return d / d.sum()


def test_flag_is_off_so_production_is_unchanged():
    """The gauntlet requires a shadow before promotion, not after."""
    assert sa.USE_HOOK_MIXTURE is False, (
        "USE_HOOK_MIXTURE shipped ON — CLAUDE.md requires a 2-week shadow "
        "before a model change reaches production"
    )


def test_mixture_is_a_distribution():
    d = sa.hook_mixture_pmf(22.0, 0.0067)
    assert len(d) == 41
    assert np.all(d >= 0)
    assert np.isclose(d.sum(), 1.0, atol=1e-6), d.sum()


def test_mean_matches_the_model_it_replaces():
    """The load-bearing property.

    Re-centring the normal component keeps the conditional mean where
    the regression put it. The reference is the CURRENT model's mean on
    the same mu, not mu itself: both distributions live on 0..40, and
    that truncation already pulls the baseline to 27.69 at mu=28.
    Comparing against mu would charge the mixture for a bias the
    production model has had all along.
    """
    for mu in (16.0, 19.0, 22.0, 25.0, 28.0):
        base = float((N * _nb(mu, 0.0067)).sum())
        got = float((N * sa.hook_mixture_pmf(mu, 0.0067)).sum())
        assert abs(got - base) < 0.10, (
            f"mu={mu}: mixture mean {got:.3f} moved away from the current "
            f"model's {base:.3f} — the change is shifting the mean, not "
            f"just the tail"
        )


def test_mean_would_drift_without_recentring():
    """Guards the reason the re-centring exists, not just its presence.

    A naive mixture that leaves the normal arm at `mu` is measurably
    biased low; this pins the size of the error the fix avoids.
    """
    mu, alpha = 22.0, 0.0067
    naive = sa.HOOK_PI * _nb(sa.HOOK_MU_SHORT, sa.HOOK_ALPHA_SHORT) \
        + (1 - sa.HOOK_PI) * _nb(mu, alpha)
    naive_mean = float((N * (naive / naive.sum())).sum())
    base_mean = float((N * _nb(mu, alpha)).sum())
    fixed_mean = float((N * sa.hook_mixture_pmf(mu, alpha)).sum())

    assert naive_mean < base_mean - 0.2, (
        "the naive mixture was expected to sit low; if it does not, the "
        "re-centring test above is not proving anything"
    )
    assert abs(fixed_mean - base_mean) < 0.10


def test_left_tail_is_fatter_than_the_plain_negative_binomial():
    """The whole point: disaster starts must stop being 1-in-900."""
    mu, alpha = 22.0, 0.0067
    mix = sa.hook_mixture_pmf(mu, alpha)
    nb = _nb(mu, alpha)

    for t in (6, 8, 10, 12):
        assert mix[:t + 1].sum() > nb[:t + 1].sum(), (
            f"P(BF <= {t}) did not increase — the left tail is still thin"
        )
    # And by a meaningful multiple where the defect was worst.
    assert mix[:9].sum() > 5 * nb[:9].sum()


def test_upper_shoulder_is_not_inflated():
    """The NB was too COMMON at BF<=18 (25.5% vs 14.5% actual).

    Fixing the deep tail must not come from piling more mass into the
    region that was already over-weighted.
    """
    mu, alpha = 22.0, 0.0067
    mix = sa.hook_mixture_pmf(mu, alpha)
    nb = _nb(mu, alpha)
    assert mix[15:19].sum() < nb[15:19].sum()


def test_low_mu_cannot_produce_a_negative_normal_component():
    """A pitch limit can drive mu below pi*mu_short.

    `c11_pitch_limit` caps mu at serve time — an opener on a 40-pitch
    limit lands near 10 BF. Without the floor the normal arm's mean goes
    negative and the pmf becomes nan, silently, on exactly the starts
    where the leash matters most.
    """
    for mu in (1.0, 2.0, 5.0, 8.0):
        d = sa.hook_mixture_pmf(mu, 0.0067)
        assert np.all(np.isfinite(d)), f"mu={mu} produced non-finite pmf"
        assert np.isclose(d.sum(), 1.0, atol=1e-6)


def test_mixture_is_left_skewed_like_reality():
    """Empirical skew is -1.58; a plain NB is +0.24 (wrong direction)."""
    from scipy.stats import skew
    mu, alpha = 22.0, 0.0067
    rng = np.random.default_rng(0)
    mix = sa.hook_mixture_pmf(mu, alpha)
    nb = _nb(mu, alpha)

    s_mix = float(skew(rng.choice(41, 200_000, p=mix / mix.sum())))
    s_nb = float(skew(rng.choice(41, 200_000, p=nb / nb.sum())))

    assert s_mix < 0 < s_nb, f"mixture skew {s_mix:+.2f}, nb {s_nb:+.2f}"


def test_predict_path_honours_the_flag(monkeypatch):
    """Flipping the flag must actually change the served distribution."""
    model = sa.StageA()
    model.load()
    feats = {"c1_bf_mean": 22.0, "a3_season_k_pct_shrunk": 0.23,
             "c10_il_return": False, "c11_pitch_limit": None}

    monkeypatch.setattr(sa, "USE_HOOK_MIXTURE", False)
    off = model.predict_bf_distribution(feats)
    monkeypatch.setattr(sa, "USE_HOOK_MIXTURE", True)
    on = model.predict_bf_distribution(feats)

    assert not np.allclose(off, on), "the flag did not reach the predict path"
    assert on[:9].sum() > off[:9].sum(), "flag ON did not fatten the left tail"
    assert np.isclose(on.sum(), 1.0) and np.isclose(off.sum(), 1.0)
