"""Gate the early-hook mixture for Stage A (A-042).

Runs CLAUDE.md's three-way out-of-sample split on the batters-faced
distribution and prints a PASS/FAIL per gate. Nothing here promotes
anything: `USE_HOOK_MIXTURE` stays False until the shadow says otherwise.

The defect being fixed
----------------------
Batters faced is LEFT-skewed (empirical skew -1.58 on 13,170 starts) and
a negative binomial is right-skewed (+0.24), so the production model
prices a disaster start at 1-in-900 when it is 1-in-32. A disaster start
settles every OVER as a loss and can never settle an UNDER that way, so
the missing left tail inflates P(over) on every pitcher — the mechanism
behind A-041's +5.0pp OVER bias.

Why a mixture rather than a better-fitted NB
--------------------------------------------
The dispersion is already pinned at its lower bound (alpha = exp(-5)
exactly: the optimizer wanted LESS spread, not more), and no NB is
left-skewed at any alpha. Loosening the bound makes it more Poisson,
i.e. worse. The process is two-component and the model should say so.

Usage:
    python tools/gate_hook_mixture.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln, xlogy

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

STARTS = ROOT / "data" / "outs_starts.parquet"

# 1/alpha appears in the log-pmf, so alpha must stay strictly positive.
# The first run of this analysis let the optimizer drive it to 0 and the
# formula returned POSITIVE log-probabilities (mean +4.67) and a "tail
# error" of 331 — arithmetically impossible, and it read as a spectacular
# improvement. _check() below exists so that can never be reported again.
A_MIN, A_MAX = 1e-4, 20.0
SHRINK_K = 5.0
TAIL_THRESHOLDS = (8, 10, 12, 16)


def _check(lp, label: str):
    lp = np.asarray(lp)
    if not np.all(np.isfinite(lp)):
        raise AssertionError(f"{label}: non-finite log-pmf")
    if lp.max() > 1e-9:
        raise AssertionError(
            f"{label}: log-pmf is POSITIVE (max {lp.max():.4f}). A "
            f"probability cannot exceed 1 — the fit has degenerated."
        )
    return lp


def nb_log_pmf(k, mu, alpha):
    k = np.asarray(k, float)
    mu = np.maximum(np.asarray(mu, float), 1e-6)
    alpha = float(np.clip(alpha, A_MIN, A_MAX))
    r = 1.0 / alpha
    return (gammaln(k + r) - gammaln(k + 1) - gammaln(r)
            + r * np.log(r / (r + mu)) + xlogy(k, mu / (r + mu)))


def mix_log_pmf(k, mu, p):
    pi = float(np.clip(p["pi"], 1e-5, 0.5))
    ms = float(np.clip(p["mu_short"], 1.0, 20.0))
    mu_n = np.maximum((np.asarray(mu, float) - pi * ms) / (1 - pi), 1.0)
    return np.logaddexp(np.log(pi) + nb_log_pmf(k, ms, p["a_short"]),
                        np.log(1 - pi) + nb_log_pmf(k, mu_n, p["alpha"]))


def _unpack(v):
    return {"pi": 1 / (1 + np.exp(-v[0])), "mu_short": np.exp(v[1]),
            "a_short": np.exp(v[2]), "alpha": np.exp(v[3])}


def fit_base(y, mu):
    f = lambda p: -_check(nb_log_pmf(y, mu, np.exp(p[0])), "base").sum()  # noqa: E731
    r = minimize(f, [np.log(0.05)], method="L-BFGS-B",
                 bounds=[(np.log(A_MIN), np.log(A_MAX))])
    return {"alpha": float(np.exp(r.x[0]))}


def fit_mix(y, mu):
    """Multi-start, because a mixture likelihood is multimodal.

    Single-start L-BFGS-B collapsed two of the three splits onto the
    boundary (pi -> 1e-4, mu_short -> 20), reporting a dead heat where
    there was a real effect. The restarts are not decoration.
    """
    f = lambda v: -_check(mix_log_pmf(y, mu, _unpack(v)), "mix").sum()  # noqa: E731
    best = None
    for pi0 in (0.02, 0.05, 0.10):
        for ms0 in (5.0, 8.0, 12.0):
            v0 = [np.log(pi0 / (1 - pi0)), np.log(ms0),
                  np.log(0.05), np.log(0.02)]
            r = minimize(f, v0, method="Nelder-Mead",
                         options={"maxiter": 30000, "fatol": 1e-10,
                                  "xatol": 1e-10})
            if best is None or r.fun < best.fun:
                best = r
    return _unpack(best.x)


def load_starts() -> pd.DataFrame:
    """Per-start table with a LEAKAGE-SAFE as-of workload mean.

    `.shift(1).expanding().mean()` is strictly prior starts for that
    pitcher — the row's own outcome is never in its own feature. That is
    Gate 1, and it is enforced by construction rather than audited after.
    """
    d = pd.read_parquet(STARTS).sort_values(["pitcher", "game_date"]).copy()
    d["prior_bf_mean"] = d.groupby("pitcher")["bf"].transform(
        lambda s: s.shift(1).expanding().mean())
    d["n_prior"] = d.groupby("pitcher").cumcount()
    league = d["bf"].mean()
    d["mu"] = np.where(
        d["n_prior"] > 0,
        (d["prior_bf_mean"].fillna(league) * d["n_prior"] + league * SHRINK_K)
        / (d["n_prior"] + SHRINK_K),
        league)
    return d


def tail_error(y, mu, logpmf) -> float:
    errs = []
    for t in TAIL_THRESHOLDS:
        actual = float((y <= t).mean())
        k = np.arange(t + 1)
        pred = float(np.mean([np.exp(logpmf(k, m)).sum() for m in mu]))
        errs.append(abs(actual - pred))
    return float(np.mean(errs))


def main() -> int:
    if not STARTS.exists():
        print(f"No starts table at {STARTS} — run tools/build_outs_dataset.py")
        return 1
    d = load_starts()

    splits = [("2024", 2025, [2024]), ("2025", 2024, [2025]),
              ("2024+2025", 2026, [2024, 2025])]
    print(f"{'train':>10}{'test':>6}{'n':>7}{'base LL':>10}{'mix LL':>10}"
          f"{'delta':>9}{'tail base':>11}{'tail mix':>10}")
    ll_ok, tail_ok, params = True, True, []
    for name, test_year, train_years in splits:
        tr = d[d["game_year"].isin(train_years)]
        te = d[d["game_year"] == test_year]
        if tr.empty or te.empty:
            print(f"{name:>10}{test_year:>6}  (no rows — skipped)")
            continue
        base = fit_base(tr["bf"].values.astype(float), tr["mu"].values)
        mix = fit_mix(tr["bf"].values.astype(float), tr["mu"].values)
        y, mu = te["bf"].values.astype(float), te["mu"].values

        ll_b = _check(nb_log_pmf(y, mu, base["alpha"]), "test-base").mean()
        ll_m = _check(mix_log_pmf(y, mu, mix), "test-mix").mean()
        e_b = tail_error(y, mu, lambda k, m: nb_log_pmf(k, m, base["alpha"]))
        e_m = tail_error(y, mu, lambda k, m: mix_log_pmf(k, m, mix))

        ll_ok &= (ll_m > ll_b)
        tail_ok &= (e_m < e_b)
        params.append(mix)
        print(f"{name:>10}{test_year:>6}{len(te):>7}{ll_b:>10.4f}{ll_m:>10.4f}"
              f"{ll_m - ll_b:>+9.4f}{e_b:>11.4f}{e_m:>10.4f}")
        print(f"{'':>10}  pi={mix['pi']:.4f} mu_short={mix['mu_short']:.2f}")

    pis = [p["pi"] for p in params]
    mss = [p["mu_short"] for p in params]
    stable = (len(params) == 3
              and max(pis) - min(pis) < 0.01
              and max(mss) - min(mss) < 1.0)

    print("\n=== GATES ===")
    print(f"  Gate 1 leakage      : PASS (as-of mean is shift(1).expanding, "
          f"strictly prior starts)")
    print(f"  Gate 2 three-way    : {'PASS' if ll_ok else 'FAIL'} "
          f"(log-lik improves in every direction)")
    print(f"  Gate 3 effect size  : {'PASS' if stable else 'FAIL'} "
          f"(pi {min(pis):.4f}-{max(pis):.4f}, mu_short "
          f"{min(mss):.2f}-{max(mss):.2f} across disjoint fits)")
    print(f"  Gate 4 collinearity : N/A (no new covariate — this is a "
          f"distributional shape with 3 global parameters)")
    print(f"  Gate 5 calibration  : PARTIAL — left-tail error "
          f"{'improves' if tail_ok else 'DOES NOT improve'} on every split, "
          f"but Gate 5 asks for P(K >= line), which needs the full "
          f"Stage A->B->compound path. That is what the shadow measures.")
    print("\n  USE_HOOK_MIXTURE stays False until the 2-week shadow reports.")
    return 0 if (ll_ok and tail_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
