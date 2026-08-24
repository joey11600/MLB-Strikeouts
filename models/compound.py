"""Compound distribution — Poisson-binomial DP.

Combines Stage A (BF distribution) and Stage B (per-batter p_i) into
the final P(K = k) distribution:

    P(K = k) = sum_n P(BF=n) * PoissonBinomial(k; p_1..p_n)

The Poisson-binomial is computed exactly via dynamic programming,
O(n^2) where n <= 40. This is microseconds, not a performance concern.

Do NOT:
- Fit a regression on total_Ks and put a normal on residuals.
- Use Poisson (wrong: underdispersed at fixed BF, overdispersed once BF varies).
- Use a plain binomial with one shared p (cannot represent lineup order,
  per-batter matchup, or TTO decay).
"""
import numpy as np

# A-051: cross-season argmin of the compound's own NLL over the sigma
# grid (tools/gate_rate_re.py): 0.15 in BOTH temporal directions
# (identical), 0.10 on the decision split. Shadow-only until its
# 2-week shadow (p_over_re) reports; production serves sigma = 0.
RATE_RE_SIGMA = 0.15


def poisson_binomial_dp(probs: np.ndarray) -> np.ndarray:
    """Exact Poisson-binomial PMF via DP.

    Parameters
    ----------
    probs : array of shape (n,)
        Per-trial success probabilities p_1..p_n (each in [0, 1]).

    Returns
    -------
    pmf : array of shape (n+1,)
        pmf[k] = P(exactly k successes out of n trials).
    """
    n = len(probs)
    dp = np.zeros(n + 1)
    dp[0] = 1.0
    for i, p in enumerate(probs):
        new_dp = np.zeros(n + 1)
        new_dp[0] = dp[0] * (1 - p)
        for k in range(1, i + 2):
            new_dp[k] = dp[k] * (1 - p) + dp[k - 1] * p
        dp = new_dp
    return dp


def compound_k_distribution_re(
    bf_dist: np.ndarray,
    per_batter_probs: np.ndarray,
    sigma: float,
    n_quad: int = 5,
) -> np.ndarray:
    """Compound with a per-start latent "stuff today" random effect.

    A-051: measured on the full 2026 backtest the served distribution is
    ~10% short of the realized K variance (6.03 vs 6.66) and the actual
    over-rate exceeds the model's at EVERY line (mean -1.2pp) — the
    Poisson-binomial's conditional dispersion is exactly binomial while
    real pitchers vary game to game around their season rate. This adds
    the missing between-start variance:

        p_i(u) = sigmoid(logit(p_i) + delta_i + sigma * u),  u ~ N(0,1)

    integrated by n_quad-point Gauss-Hermite quadrature. delta_i is a
    per-batter recentering solved numerically so E_u[p_i(u)] = p_i —
    THE CONDITIONAL MEAN IS PRESERVED, the same load-bearing rule as
    the hook mixture (A-042): sigma widens the distribution without
    moving the unbiased point estimate.

    sigma = 0 reproduces compound_k_distribution exactly. The DP runs
    once over batters with the quadrature nodes as columns, capturing
    the prefix PMF at each n — O(n^2 * n_quad) total.
    """
    if sigma <= 0:
        return compound_k_distribution(bf_dist, per_batter_probs)

    nodes, w = np.polynomial.hermite_e.hermegauss(n_quad)
    w = w / w.sum()

    p = np.clip(np.asarray(per_batter_probs, dtype=float), 1e-9, 1 - 1e-9)
    logits = np.log(p / (1 - p))

    # Recenter so the mixture mean matches p_i per batter (two Newton
    # steps on delta; the objective is monotone in delta so this
    # converges fast and safely).
    delta = np.zeros_like(logits)
    for _ in range(3):
        z = logits[:, None] + delta[:, None] + sigma * nodes[None, :]
        pu = 1.0 / (1.0 + np.exp(-z))
        mean = pu @ w
        grad = (pu * (1 - pu)) @ w
        delta -= (mean - p) / np.maximum(grad, 1e-12)

    z = logits[:, None] + delta[:, None] + sigma * nodes[None, :]
    P = 1.0 / (1.0 + np.exp(-z))                     # (n_max, J)

    max_bf = len(bf_dist)
    J = n_quad
    dp = np.zeros((max_bf, J))
    dp[0, :] = 1.0
    out = np.zeros((max_bf, J))
    out[0, :] = bf_dist[0]                            # BF=0 -> K=0

    for i in range(1, max_bf):
        pi = P[i - 1, :][None, :]                     # batter i, (1, J)
        # numpy evaluates the RHS before assignment, so the shifted
        # slice read is safe.
        dp[1:i + 1, :] = dp[1:i + 1, :] * (1 - pi) + dp[0:i, :] * pi
        dp[0, :] = dp[0, :] * (1 - pi[0, :])
        if bf_dist[i] >= 1e-12:
            out += bf_dist[i] * dp

    k_dist = out @ w
    s = k_dist.sum()
    return k_dist / s if s > 0 else k_dist


def compound_k_distribution(
    bf_dist: np.ndarray,
    per_batter_probs: np.ndarray,
) -> np.ndarray:
    """Combine Stage A and Stage B into P(K = k).

    Parameters
    ----------
    bf_dist : array of shape (41,)
        P(BF = n) for n = 0..40 from Stage A.
    per_batter_probs : array of shape (40,)
        p_i for i = 1..40 from Stage B.

    Returns
    -------
    k_dist : array of shape (41,)
        P(K = k) for k = 0..40.
    """
    max_bf = len(bf_dist)
    k_dist = np.zeros(max_bf)

    for n in range(max_bf):
        if bf_dist[n] < 1e-12:
            continue
        if n == 0:
            k_dist[0] += bf_dist[0]
            continue
        pb_pmf = poisson_binomial_dp(per_batter_probs[:n])
        k_dist[: n + 1] += bf_dist[n] * pb_pmf

    return k_dist


def prob_k_geq(k_dist: np.ndarray, line: float) -> float:
    """P(over) at a HALF-POINT line: for 5.5, P(K >= 6).

    Whole-number lines are refused (A-047). The old behavior returned
    ceil(6) -> P(K >= 6), which counts the push at K = 6 as an over WIN
    — its own docstring claimed P(K >= 7), so code and doc disagreed
    and both sides of a push-able line would have been mispriced in the
    over's favor. No caller has ever passed a whole line (DK posts
    X.5), so this is a latent-bug guard, not a behavior change: if an
    alternate book or a settings change ever surfaces an integer line,
    the pipeline must model {win, push, lose} explicitly, the way
    models/outs_hazard.py already refuses (its lines sit ON the
    lattice, where this class of error is fatal).
    """
    import math
    if float(line) == int(line):
        raise ValueError(
            f"prob_k_geq: whole-number line {line} needs explicit push "
            f"handling (P(K == {int(line)}) is a stake-returned PUSH, "
            f"not an over win). Refusing to fold it into either side."
        )
    threshold = math.ceil(line)
    if threshold >= len(k_dist):
        return 0.0
    return float(np.sum(k_dist[threshold:]))
