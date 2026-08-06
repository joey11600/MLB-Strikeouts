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


class StageA:
    """Negative binomial BF model with feature-based mean prediction."""

    def __init__(self):
        self.coefficients = None
        self.alpha = None
        self.feature_names = None

    def _build_X(self, df: pd.DataFrame) -> np.ndarray:
        """Build design matrix from game-level DataFrame."""
        X = np.column_stack([
            np.ones(len(df)),
            df["prior_bf_mean"].values,
            df["season_k_pct"].values,
            df["il_return"].astype(float).values,
            df["has_pitch_limit"].astype(float).values,
            df["bp_heavy"].astype(float).values,
        ])
        self.feature_names = [
            "intercept", "prior_bf_mean", "season_k_pct",
            "il_return", "has_pitch_limit", "bp_heavy",
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
        pitch_limit = features.get("c11_pitch_limit")
        has_limit = float(pitch_limit is not None)
        bp_heavy = float(features.get("c12_bp_heavy", False))

        x = np.array([1.0, prior_bf, k_pct, il_return, has_limit, bp_heavy])

        if self.coefficients is not None:
            mu = float(np.exp(np.clip(x @ self.coefficients, -5, 5)))
            alpha = self.alpha
        else:
            mu = prior_bf if prior_bf else LEAGUE_BF_MEAN
            alpha = 0.1

        if pitch_limit is not None:
            estimated_bf_from_limit = pitch_limit / 4.0
            mu = min(mu, estimated_bf_from_limit)

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
