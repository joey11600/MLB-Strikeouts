"""Stage A — Batters-faced distribution model.

Predicts P(BF = n) for n in 0..40 for a given starting pitcher in a
given game context. This is the "leash model."

Approach: negative binomial regression on BF count, fitted via MLE.
Features: pitcher's prior BF mean, season K%, IL return flag,
announced pitch limit, bullpen fatigue. The negative binomial captures
the overdispersion in BF counts that a Poisson can't.

Output: a probability vector of length 41 summing to 1.0.
"""
import pickle
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln, digamma

sys.path.insert(0, str(Path(__file__).parent.parent))

MODEL_PATH = Path(__file__).parent / "stage_a_fitted.pkl"

LEAGUE_BF_MEAN = 21.1
LEAGUE_BF_STD = 5.06

# Pitches per batter faced, used ONLY to convert an announced pitch limit
# into a cap on batters faced.
#
# This was 4.0, picked by eye. Measured on 3,283 real 2026 starts by
# replaying pitches in order and counting how many batters a starter had
# actually faced at the moment he threw his Nth pitch -- which is the
# question a pitch limit actually asks, rather than the whole-start
# average (3.87, and the wrong statistic here because efficiency drifts
# as the game goes on):
#
#     limit  n starts  mean BF  implied divisor
#       60     3140     15.83       3.791
#       75     2821     19.68       3.812
#       90     1611     23.16       3.885
#      100      379     25.04       3.993
#
# 4.0 is right for a ~100-pitch outing, which is not a limit. Across the
# 60-90 range where limits actually land it understated batters faced by
# 0.7-0.9, which at ~2.45 pp of P(over) per batter is roughly 2 points of
# probability -- always in the direction of suppressing OVER.
#
# A single constant covers 60-90 to within 0.09 BF, which is far inside
# the 2.71 BF noise floor, so a limit-dependent curve would be false
# precision.
PITCHES_PER_BF_UNDER_LIMIT = 3.8

# --- Early-hook mixture (A-042). FLAG OFF pending the 2-week shadow. ---
#
# The negative binomial is the wrong SHAPE for batters faced. Measured on
# 13,170 starts (2024-2026, data/outs_starts.parquet): empirical skew
# **-1.58** -- a long LEFT tail -- against the fitted NB's **+0.24**.
# What that costs, on the same starts:
#
#     threshold   actual   NB model        ratio
#     BF <=  8     3.08%      0.11%   27.6x too rare
#     BF <= 10     4.07%      0.58%    7.0x too rare
#     BF <= 12     5.03%      2.18%    2.3x too rare
#     BF <= 18    14.52%     25.51%    0.6x (too COMMON)
#
# A disaster start settles every OVER as a loss and can never settle an
# UNDER that way, so pricing a 1-in-32 event at 1-in-900 inflates P(over)
# on every pitcher. That asymmetry is the mechanism behind the +5.0pp
# OVER bias in A-041, and it is why the model's confident OVERs invert.
#
# No single NB can fix it. The dispersion is ALREADY pinned at its lower
# bound (alpha = exp(-5) exactly, i.e. the optimizer wanted even LESS
# spread, not more), and no negative binomial is left-skewed at any
# alpha. The process is genuinely two-component: a start is either
# hooked early or it is not.
#
# Fitted independently on each training split. The agreement across
# three disjoint fits is the evidence this is real rather than a curve
# through noise:
#
#     train      pi      mu_short   d(logLik)   tail err
#     2024     0.0233      5.96      +0.0689   0.0275 -> 0.0141
#     2025     0.0195      6.02      +0.0798   0.0292 -> 0.0179
#     2024+25  0.0213      5.99      +0.1234   0.0410 -> 0.0287
#
# Gate 2 passes in BOTH temporal directions and forward; tail error is
# roughly halved every time. Constants below are the forward split, the
# production direction. Reproduce with tools/gate_hook_mixture.py.
USE_HOOK_MIXTURE = False
HOOK_PI = 0.0213
HOOK_MU_SHORT = 5.99
# The short component's dispersion fitted to its lower bound on all three
# splits, i.e. Poisson-like. Held at a small positive value rather than
# zero because 1/alpha appears in the log-pmf.
HOOK_ALPHA_SHORT = 1e-3


def _negbin_log_pmf(k, mu, alpha):
    """Log PMF of negative binomial parameterized by mean mu and dispersion alpha.

    P(X = k) = Gamma(k + 1/alpha) / (Gamma(k+1) * Gamma(1/alpha))
               * (1/(1 + alpha*mu))^(1/alpha) * (alpha*mu/(1 + alpha*mu))^k
    """
    r = 1.0 / alpha
    p = r / (r + mu)
    p = np.clip(p, 1e-10, 1 - 1e-10)
    return (gammaln(k + r) - gammaln(k + 1) - gammaln(r)
            + r * np.log(p) + k * np.log(1 - p))


def _negbin_pmf(k, mu, alpha):
    return np.exp(_negbin_log_pmf(k, mu, alpha))


def hook_mixture_pmf(mu, alpha, pi=None, mu_short=None, alpha_short=None):
    """P(BF = n) for n in 0..40 as: hooked early, OR a normal outing.

    THE CONDITIONAL MEAN IS PRESERVED, and that is load-bearing. `mu`
    arriving here is the regression's estimate of expected batters
    faced, and it is unbiased in live data (measured mean BF error
    +0.00 over 264 starts). So the normal component is re-centred to

        mu_normal = (mu - pi*mu_short) / (1 - pi)

    rather than left at mu. Skipping that would shift every prediction
    DOWN by pi*(mu - mu_short) ~ 0.34 batters and trade a tail bias for
    a mean bias -- fixing the OVER lean by breaking the thing that
    already worked.

    Measured against the plain NB on the same mu, the mean moves by
    -0.000 (mu=16) to -0.069 (mu=28) batters. The residual is truncation
    of the 0..40 support, which the existing model carries identically
    (its own mean at mu=28 is 27.69, not 28.00) -- not something the
    mixture introduces.
    """
    pi = HOOK_PI if pi is None else pi
    mu_short = HOOK_MU_SHORT if mu_short is None else mu_short
    alpha_short = HOOK_ALPHA_SHORT if alpha_short is None else alpha_short

    # Floor at 1.0: a pitch limit can drive mu low enough that removing
    # the hook mass would leave a non-positive mean for the normal arm.
    mu_normal = max((mu - pi * mu_short) / (1.0 - pi), 1.0)

    n = np.arange(41)
    short = _negbin_pmf(n, mu_short, alpha_short)
    normal = _negbin_pmf(n, mu_normal, alpha)
    dist = pi * short + (1.0 - pi) * normal
    # Return a proper pmf. Both arms are truncated at the 0..40 support,
    # so the raw sum falls below 1 for long-leash starters (0.975 at
    # mu=28); the caller normalising again is then a no-op.
    return dist / dist.sum()


class StageA:
    """Negative binomial BF model with feature-based mean prediction."""

    def __init__(self):
        self.coefficients = None
        self.alpha = None
        self.feature_names = None

    def _build_X(self, df: pd.DataFrame) -> np.ndarray:
        """Build design matrix from game-level DataFrame."""
        # has_pitch_limit and bp_heavy were REMOVED 2026-08-10 after a
        # three-way screen over 13,170 starts.
        #
        # has_pitch_limit fitted to exactly +0.00000 and always would:
        # prepare_training_data hardcodes it False on every training row
        # (data/manual_pitch_limits.csv has never held a data row), so the
        # column carried no variance. It could not move a price in either
        # direction. Announced limits still bind at serve time through the
        # direct BF cap in predict_bf_distribution -- that is a different
        # mechanism and it stays.
        #
        # bp_heavy fails Gate 2 outright on total K: dRMSE -0.023 / -0.035 /
        # -0.006 with t -0.51 / +1.33 / +0.64, and it is null on batters
        # faced too (t +0.34 / +1.06 / +0.97). The same term measured for the
        # outs target flipped sign by season. A feature that helps in only
        # one temporal direction is rejected by CLAUDE.md; this one helps in
        # none.
        X = np.column_stack([
            np.ones(len(df)),
            df["prior_bf_mean"].values,
            df["season_k_pct"].values,
            df["il_return"].astype(float).values,
        ])
        self.feature_names = [
            "intercept", "prior_bf_mean", "season_k_pct", "il_return",
        ]
        return X

    def _predict_mu(self, X: np.ndarray, beta: np.ndarray) -> np.ndarray:
        """Predict mean BF via log link: mu = exp(X @ beta)."""
        eta = X @ beta
        eta = np.clip(eta, -5, 5)
        return np.exp(eta)

    def _neg_log_lik(self, params, X, y):
        """Negative log-likelihood for NB regression."""
        beta = params[:-1]
        log_alpha = params[-1]
        alpha = np.exp(log_alpha)

        mu = self._predict_mu(X, beta)
        mu = np.clip(mu, 1e-6, 100)

        ll = _negbin_log_pmf(y, mu, alpha)
        return -np.sum(ll)

    def fit(self, df: pd.DataFrame):
        """Fit the model on game-level training data.

        df must have columns: actual_bf, prior_bf_mean, season_k_pct,
        il_return, has_pitch_limit, bp_heavy.
        """
        X = self._build_X(df)
        y = df["actual_bf"].values.astype(float)

        n_features = X.shape[1]
        beta_init = np.zeros(n_features)
        beta_init[0] = np.log(y.mean())
        log_alpha_init = np.log(0.05)
        params_init = np.append(beta_init, log_alpha_init)

        # log_alpha bounded so the dispersion can't collapse to 0 (Poisson
        # degenerate) or explode; betas unbounded.
        bounds = [(None, None)] * n_features + [(-5.0, 1.0)]

        result = minimize(
            self._neg_log_lik, params_init, args=(X, y),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        self.coefficients = result.x[:-1]
        self.alpha = np.exp(result.x[-1])

        print(f"  Stage A fitted: alpha={self.alpha:.4f}")
        for name, coef in zip(self.feature_names, self.coefficients):
            print(f"    {name:20s} = {coef:+.4f}")

    def predict_bf_distribution(self, features: dict) -> np.ndarray:
        """Return P(BF = n) for n = 0..40 as a 41-element array."""
        # AUDIT A-007: never substitute a league default for a missing
        # input. A fabricated workload/rate inflates the projection, and
        # the edge filter then selects that error into the bet list at
        # max stake. Missing input = caller bug = fail loudly.
        prior_bf = features.get("c1_bf_mean")
        if prior_bf is None:
            raise ValueError(
                "Stage A: c1_bf_mean is missing. Refusing to substitute the "
                f"league average ({LEAGUE_BF_MEAN} BF) — that manufactures edge. "
                "Establish the pitcher's real workload or skip him."
            )

        k_pct = features.get("a3_season_k_pct_shrunk")
        if k_pct is None:
            k_pct = features.get("a3_season_k_pct_raw")
        if k_pct is None:
            raise ValueError(
                "Stage A: season K% is missing. Refusing to substitute the "
                "league average — that manufactures edge."
            )

        il_return = float(features.get("c10_il_return", False))
        # c11_pitch_limit is still read -- it binds below as a hard BF cap,
        # which is a serve-time mechanism, not a fitted term. c12_bp_heavy is
        # no longer consumed at all; see _design_matrix for why both left the
        # design.
        pitch_limit = features.get("c11_pitch_limit")

        x = np.array([1.0, prior_bf, k_pct, il_return])

        if self.coefficients is not None:
            mu = float(np.exp(np.clip(x @ self.coefficients, -5, 5)))
            alpha = self.alpha
        else:
            mu = prior_bf if prior_bf else LEAGUE_BF_MEAN
            alpha = 0.1

        if pitch_limit is not None:
            estimated_bf_from_limit = pitch_limit / PITCHES_PER_BF_UNDER_LIMIT
            mu = min(mu, estimated_bf_from_limit)

        if USE_HOOK_MIXTURE:
            dist = hook_mixture_pmf(mu, alpha)
        else:
            dist = np.array([_negbin_pmf(n, mu, alpha) for n in range(41)])
        dist = dist / dist.sum()

        return dist

    def save(self, path: Path | None = None):
        path = path or MODEL_PATH
        with open(path, "wb") as f:
            pickle.dump({
                "coefficients": self.coefficients,
                "alpha": self.alpha,
                "feature_names": self.feature_names,
            }, f)

    def load(self, path: Path | None = None):
        path = path or MODEL_PATH
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.coefficients = data["coefficients"]
        self.alpha = data["alpha"]
        self.feature_names = data["feature_names"]


def prepare_training_data(start_date: date, end_date: date) -> pd.DataFrame:
    """Build the game-level DataFrame with strictly as-of Stage A features.

    Every feature row uses only games BEFORE that row's game (via
    features.asof.asof_pitcher_game_table), matching what the live
    pipeline feeds the model at predict time. Rows without enough
    prior history (< 3 prior starts or < 50 prior BF) are dropped.
    """
    from data.backfill_statcast import load_cached
    from features.asof import (
        asof_pitcher_game_table, bullpen_fatigue_table, IL_GAP_DAYS,
    )

    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    pt = asof_pitcher_game_table(df)
    starters = pt[pt["actual_bf"] >= 9].copy()

    starters = starters[
        (starters["prior_games"] >= 3)
        & (starters["prior_bf"] >= 50)
        & starters["asof_k_pct"].notna()
    ]

    starters = starters.rename(columns={
        "asof_bf_mean": "prior_bf_mean",
        "asof_k_pct_shrunk": "season_k_pct",
    })

    # Leash inputs (Phase 12). il_return: long layoff before this start.
    # bp_heavy: the team's bullpen was taxed the previous day. Announced
    # pitch limits are unknowable historically, so has_pitch_limit stays
    # untrained — the live path applies limits as a direct BF cap.
    starters["il_return"] = starters["days_since_prior"] > IL_GAP_DAYS

    bp = bullpen_fatigue_table(df)
    starters = starters.merge(bp, on=["game_pk", "pitcher"], how="left")
    starters["bp_heavy"] = starters["bp_heavy"].fillna(False).astype(bool)

    starters["has_pitch_limit"] = False

    return starters.reset_index(drop=True)


def fit_and_evaluate(start_date: date = date(2026, 6, 1),
                     end_date: date = date(2026, 8, 4),
                     save_path: Path | None = None):
    """Fit Stage A on as-of training data and evaluate."""
    print("Preparing Stage A training data (as-of features)...")
    train_df = prepare_training_data(start_date, end_date)
    print(f"  {len(train_df)} training rows")

    model = StageA()
    model.fit(train_df)

    pred_mus = []
    for _, row in train_df.iterrows():
        features = {
            "c1_bf_mean": row["prior_bf_mean"],
            "a3_season_k_pct_shrunk": row["season_k_pct"],
            "c10_il_return": row["il_return"],
            "c11_pitch_limit": None,
            "c12_bp_heavy": row["bp_heavy"],
        }
        dist = model.predict_bf_distribution(features)
        pred_mu = np.sum(np.arange(41) * dist)
        pred_mus.append(pred_mu)

    train_df = train_df.copy()
    train_df["pred_bf"] = pred_mus
    train_df["residual"] = train_df["actual_bf"] - train_df["pred_bf"]

    rmse = np.sqrt((train_df["residual"] ** 2).mean())
    mae = train_df["residual"].abs().mean()
    corr = train_df[["actual_bf", "pred_bf"]].corr().iloc[0, 1]

    print(f"\n  Stage A in-sample evaluation:")
    print(f"    RMSE = {rmse:.2f} BF")
    print(f"    MAE  = {mae:.2f} BF")
    print(f"    Corr = {corr:.3f}")
    print(f"    Mean actual = {train_df['actual_bf'].mean():.1f}")
    print(f"    Mean predicted = {train_df['pred_bf'].mean():.1f}")

    model.save(save_path)
    print(f"\n  Model saved to {save_path or MODEL_PATH}")

    return model


if __name__ == "__main__":
    fit_and_evaluate()
