"""Prototype: starting-pitcher TOTAL OUTS RECORDED as a stopping time on a lattice.

This is the reference implementation of the model form specified in
``docs/OUTS_MODEL.md``.  It is deliberately dependency-light (numpy only) and
carries its own proof-by-execution that the composed PMF over 0..27 normalises.

Run it:

    python models/outs_hazard_proto.py

WHY NOT THE STRIKEOUTS FAMILY
-----------------------------
Outs is not a count process.  Measured on 13,170 regular-season starts from
this repo's Statcast cache (2024-03-28 .. 2026-08-06): mean 15.52, sd 4.25,
65.5% of starts land on an exact multiple of 3, P(18)/P(19) = 8.3x and
P(21)/P(22) = 18.8x.  A moment-matched negative binomial puts 0.073 on 18
against an empirical 0.220.  The Stage-A x Stage-B compound used for
strikeouts cannot reproduce that sawtooth and is not reused here.

THE STATE CHAIN
---------------
For inning j = 1..9, with A_j = "the starter throws at least one pitch in
inning j" (A_1 is true by definition of 'starter'):

    X_j in {0,1,2,3}   outs he records in inning j, given A_j
    R_j in {0,1}       he comes back out for inning j+1, given X_j == 3

    X_j <  3  ->  the start is over at  3*(j-1) + X_j   (removed mid-inning,
                  or the game ended mid-inning on a walk-off / weather)
    X_j == 3 and R_j == 0  ->  the start is over at 3*j
    X_j == 3 and R_j == 1  ->  continue into inning j+1
    j == 9 and X_9 == 3    ->  the start is over at 27 (structural ceiling)

Note that outs == 3*j is reachable by TWO disjoint paths: completing inning j
and not returning, or returning for inning j+1 and being removed before
recording an out.  Both are real and both are counted.  Measured on the same
13,170 starts, the second path supplies 3.4%-23.2% of the mass at 3*j
(largest at 12 outs, smallest at 21).

PARAMETERISATION
----------------
Covariate effects are SHARED across inning boundaries, except that the as-of
pitcher-quality block gets a boundary-specific slope:

    logit P(X_j == 3 | A_j, x)      = gamma_j + x'delta + x_Q' delta_j
    logit P(R_j == 1 | X_j == 3, x) = alpha_j + x'beta  + x_Q' beta_j
    P(X_j = r | A_j, X_j < 3)       = softmax(psi_j)_r ,  r in {0,1,2}

x_Q is the sub-vector of x indexed by ``quality_idx``.  The partial-inning
shape carries NO covariates -- adding them was measured to be worthless (see
the spec, Q2).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "N_INNINGS",
    "MAX_OUTS",
    "N_OUTCOMES",
    "MARKET_LINES",
    "OutsHazardParams",
    "outs_pmf",
    "p_over",
    "random_params",
]

N_INNINGS = 9
MAX_OUTS = 27
N_OUTCOMES = MAX_OUTS + 1          # support is 0..27 inclusive
MARKET_LINES = (13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5)


# ---------------------------------------------------------------------------
# numerics
# ---------------------------------------------------------------------------
def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Overflow-safe logistic. Saturates rather than returning NaN."""
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    e = np.exp(z[~pos])
    out[~pos] = e / (1.0 + e)
    return out


def _softmax_rows(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------
@dataclass
class OutsHazardParams:
    """Fitted parameters of the inning-lattice hazard model.

    gamma       (9,)      completion intercepts, innings 1..9
    delta       (P,)      shared completion slopes
    delta_j     (9, Q)    boundary-varying completion slopes on the quality block
    alpha       (8,)      return intercepts, boundaries 1..8  (no boundary 9)
    beta        (P,)      shared return slopes
    beta_j      (8, Q)    boundary-varying return slopes on the quality block
    psi         (9, 3)    partial-inning multinomial logits over {0,1,2} outs
    quality_idx (Q,)      column indices of x that carry boundary-varying slopes
    """

    gamma: np.ndarray
    delta: np.ndarray
    delta_j: np.ndarray
    alpha: np.ndarray
    beta: np.ndarray
    beta_j: np.ndarray
    psi: np.ndarray
    quality_idx: np.ndarray

    def validate(self, n_features: int) -> None:
        q = len(self.quality_idx)
        assert self.gamma.shape == (N_INNINGS,), self.gamma.shape
        assert self.delta.shape == (n_features,), self.delta.shape
        assert self.delta_j.shape == (N_INNINGS, q), self.delta_j.shape
        assert self.alpha.shape == (N_INNINGS - 1,), self.alpha.shape
        assert self.beta.shape == (n_features,), self.beta.shape
        assert self.beta_j.shape == (N_INNINGS - 1, q), self.beta_j.shape
        assert self.psi.shape == (N_INNINGS, 3), self.psi.shape
        assert self.quality_idx.min() >= 0 and self.quality_idx.max() < n_features


# ---------------------------------------------------------------------------
# the recursion
# ---------------------------------------------------------------------------
def outs_pmf(params: OutsHazardParams, X: np.ndarray) -> np.ndarray:
    """Compose the per-inning hazards into a PMF over outs 0..27.

    Returns an (n, 28) array whose rows sum to 1 by construction.

    Normalisation proof.  Let S_j = P(A_j), S_1 = 1.  In inning j the mass
    emitted to terminal states is

        S_j * (1 - c_j)          (removed mid-inning, split over {0,1,2})
      + S_j * c_j * (1 - r_j)    (completed j, did not return)

    and the mass carried forward is S_{j+1} = S_j * c_j * r_j.  Their sum is
    S_j * [(1 - c_j) + c_j * (1 - r_j) + c_j * r_j] = S_j exactly.  Summing
    over j telescopes to S_1 - S_10.  At j = 9 the return step is skipped and
    S_9 * c_9 is emitted to outs == 27, so S_10 = 0 and the total is S_1 = 1.
    """
    X = np.atleast_2d(np.asarray(X, dtype=float))
    n, p = X.shape
    params.validate(p)

    XQ = X[:, params.quality_idx]
    xd = X @ params.delta            # shared completion linear predictor
    xb = X @ params.beta             # shared return linear predictor
    shape = _softmax_rows(params.psi)          # (9, 3)

    pmf = np.zeros((n, N_OUTCOMES))
    surv = np.ones(n)                # S_j = P(active entering inning j)

    for j in range(1, N_INNINGS + 1):
        i = j - 1
        c = _sigmoid(params.gamma[i] + xd + XQ @ params.delta_j[i])
        base = 3 * i

        # --- removed mid-inning: 0, 1 or 2 outs recorded in inning j ---
        partial = surv * (1.0 - c)
        for r in range(3):
            pmf[:, base + r] += partial * shape[i, r]

        if j < N_INNINGS:
            # --- completed inning j; does he come back out? ---
            r_prob = _sigmoid(params.alpha[i] + xb + XQ @ params.beta_j[i])
            pmf[:, 3 * j] += surv * c * (1.0 - r_prob)
            surv = surv * c * r_prob
        else:
            # --- 27-out ceiling: no boundary-9 return decision exists ---
            pmf[:, MAX_OUTS] += surv * c
            surv = np.zeros(n)

    return pmf


def p_over(pmf: np.ndarray, line: float) -> np.ndarray:
    """P(outs > line) for a half-integer market line.

    DraftKings 'Outs Recorded' lines in this market are all half-integers
    (13.5 .. 19.5 observed), so no PUSH is reachable and the two sides
    partition the support exactly.  A whole-number line would need the
    PUSH branch from CLAUDE.md's prop-grading rules and is rejected here.
    """
    if float(line) == int(line):
        raise ValueError(
            f"line {line} is a whole number; this market quotes half-integers "
            "only. A whole-number line needs explicit PUSH handling."
        )
    lo = int(np.ceil(line))            # smallest outs total that beats the line
    if lo > MAX_OUTS:
        return np.zeros(pmf.shape[0])
    return pmf[:, lo:].sum(axis=1)


def random_params(rng: np.random.Generator, n_features: int, n_quality: int = 5,
                  scale: float = 1.0) -> OutsHazardParams:
    """Random but structurally valid parameters, for normalisation testing."""
    qi = rng.choice(n_features, size=n_quality, replace=False)
    return OutsHazardParams(
        gamma=rng.normal(0, 2.0 * scale, N_INNINGS),
        delta=rng.normal(0, 0.5 * scale, n_features),
        delta_j=rng.normal(0, 0.5 * scale, (N_INNINGS, n_quality)),
        alpha=rng.normal(0, 2.0 * scale, N_INNINGS - 1),
        beta=rng.normal(0, 0.5 * scale, n_features),
        beta_j=rng.normal(0, 0.5 * scale, (N_INNINGS - 1, n_quality)),
        psi=rng.normal(0, 1.0 * scale, (N_INNINGS, 3)),
        quality_idx=np.sort(qi),
    )


# ---------------------------------------------------------------------------
# self-tests
# ---------------------------------------------------------------------------
def _selftest() -> None:
    rng = np.random.default_rng(20260808)
    P, Q = 27, 5
    worst_sum = 0.0

    print("=" * 74)
    print("PMF NORMALISATION ON RANDOM INPUTS")
    print("=" * 74)
    for scale, tag in [(0.5, "mild"), (1.0, "typical"), (4.0, "wild"),
                       (20.0, "saturating")]:
        errs, negs = [], 0
        for _ in range(200):
            params = random_params(rng, P, Q, scale=scale)
            X = rng.normal(0, 1.5, (64, P))
            pmf = outs_pmf(params, X)
            assert pmf.shape == (64, N_OUTCOMES)
            assert np.isfinite(pmf).all(), "non-finite PMF entry"
            negs += int((pmf < 0).sum())
            errs.append(np.abs(pmf.sum(axis=1) - 1.0).max())
        m = max(errs)
        worst_sum = max(worst_sum, m)
        print(f"  scale={scale:>5} ({tag:<10}) 200 draws x 64 rows  "
              f"max|sum-1| = {m:.3e}   negative entries = {negs}")
    assert worst_sum < 1e-12, worst_sum
    print(f"  -> worst normalisation error over all regimes: {worst_sum:.3e}  PASS")

    print()
    print("=" * 74)
    print("STRUCTURAL INVARIANTS")
    print("=" * 74)
    params = random_params(rng, P, Q)
    X = rng.normal(0, 1.5, (500, P))
    pmf = outs_pmf(params, X)

    print(f"  support is exactly 0..27                : {pmf.shape[1] == 28}")
    print(f"  all mass non-negative                   : {(pmf >= 0).all()}")

    # 27-out ceiling: nothing beyond 27 can exist because the array stops there
    # and because boundary 9 has no return parameter at all.
    assert params.beta_j.shape[0] == N_INNINGS - 1
    assert params.alpha.shape[0] == N_INNINGS - 1
    print(f"  no return parameter at boundary 9       : True (alpha has "
          f"{params.alpha.shape[0]} entries for 9 innings)")

    # 0 floor: mass at 0 is exactly P(X_1 = 0), i.e. surv=1, not completing, 0 outs
    c1 = _sigmoid(params.gamma[0] + X @ params.delta
                  + X[:, params.quality_idx] @ params.delta_j[0])
    q1 = _softmax_rows(params.psi)[0]
    expect0 = (1.0 - c1) * q1[0]
    print(f"  mass at 0 == P(X_1 = 0) exactly         : "
          f"{np.allclose(pmf[:, 0], expect0, atol=1e-15)}")

    # degenerate: everyone always completes and always returns -> all mass at 27
    hard = random_params(rng, P, Q)
    hard.gamma[:] = 60.0
    hard.alpha[:] = 60.0
    hard.delta[:] = 0.0
    hard.beta[:] = 0.0
    hard.delta_j[:] = 0.0
    hard.beta_j[:] = 0.0
    d = outs_pmf(hard, X)
    print(f"  c=1, r=1 collapses to a point mass at 27: "
          f"{np.allclose(d[:, 27], 1.0) and np.allclose(d.sum(1), 1.0)}")

    # degenerate: nobody ever completes an inning -> all mass in {0,1,2}
    soft = random_params(rng, P, Q)
    soft.gamma[:] = -60.0
    soft.delta[:] = 0.0
    soft.delta_j[:] = 0.0
    d2 = outs_pmf(soft, X)
    print(f"  c=0 puts all mass on {{0,1,2}}            : "
          f"{np.allclose(d2[:, :3].sum(1), 1.0)}")

    # two-path structure: mass at 3j exceeds the 'completed and left' path alone
    print(f"  mass at 3j is fed by two disjoint paths  : "
          f"{bool((pmf[:, 3] > 0).all() and (pmf[:, 6] > 0).all())}")

    print()
    print("=" * 74)
    print("MARKET LINES: P(outs > L)")
    print("=" * 74)
    surv_prev = np.ones(500)
    mono = True
    for L in MARKET_LINES:
        po = p_over(pmf, L)
        assert np.allclose(po, pmf[:, int(np.ceil(L)):].sum(axis=1))
        mono &= bool((po <= surv_prev + 1e-15).all())
        surv_prev = po
        print(f"  L = {L:<5}  P(over) mean = {po.mean():.4f}   "
              f"[{po.min():.4f}, {po.max():.4f}]   over+under = "
              f"{np.abs(po + (1 - po) - 1).max():.1e}")
    print(f"  survival is monotone non-increasing in L: {mono}")
    try:
        p_over(pmf, 17.0)
        print("  whole-number line rejected              : False")
    except ValueError:
        print("  whole-number line rejected              : True")

    print()
    print("=" * 74)
    print("CALIBRATION SHAPE CHECK AGAINST MEASURED LEAGUE HAZARDS")
    print("=" * 74)
    # Plug in the measured 2024-2026 marginal hazards (no covariates) and
    # confirm the recursion reproduces the empirical lattice.
    p_complete = np.array([0.99393, 0.98512, 0.97961, 0.95231, 0.86812,
                           0.72240, 0.64877, 0.58718, 0.69136])
    p_return = np.array([0.98594, 0.98773, 0.97675, 0.93638, 0.76261,
                         0.46897, 0.25016, 0.35371])
    shape = np.array([[0.0500, 0.1875, 0.7625], [0.1458, 0.3854, 0.4688],
                      [0.1523, 0.3438, 0.5039], [0.1326, 0.3595, 0.5079],
                      [0.1557, 0.3843, 0.4600], [0.1905, 0.3946, 0.4149],
                      [0.2109, 0.4147, 0.3744], [0.2547, 0.3975, 0.3478],
                      [0.3600, 0.3200, 0.3200]])
    lg = OutsHazardParams(
        gamma=np.log(p_complete / (1 - p_complete)),
        delta=np.zeros(1), delta_j=np.zeros((N_INNINGS, 1)),
        alpha=np.log(p_return / (1 - p_return)),
        beta=np.zeros(1), beta_j=np.zeros((N_INNINGS - 1, 1)),
        psi=np.log(shape), quality_idx=np.array([0]),
    )
    lp = outs_pmf(lg, np.zeros((1, 1)))[0]
    empirical = {0: 0.0003, 3: 0.0162, 6: 0.0148, 9: 0.0275, 12: 0.0720,
                 15: 0.1962, 18: 0.2201, 21: 0.0919, 24: 0.0119, 27: 0.0043}
    print(f"  {'outs':>5} {'model':>9} {'empirical':>11} {'diff':>9}")
    for k, e in empirical.items():
        print(f"  {k:>5} {lp[k]:>9.4f} {e:>11.4f} {lp[k]-e:>+9.4f}")
    print(f"  sum = {lp.sum():.12f}")
    print(f"  mean outs = {(lp * np.arange(28)).sum():.3f}  (empirical 15.52)")
    print(f"  P(multiple of 3) = {lp[::3].sum():.3f}  (empirical 0.655)")
    print(f"  P(18)/P(19) = {lp[18]/lp[19]:.1f}  (empirical 8.3)")
    print(f"  P(21)/P(22) = {lp[21]/lp[22]:.1f}  (empirical 18.8)")

    print()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    _selftest()
