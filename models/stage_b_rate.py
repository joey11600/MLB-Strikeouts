"""Stage B — Per-batter strikeout probability model.

For each batter position i in the lineup sequence, predicts p_i:
the probability that the i-th batter faced is struck out.

Uses logistic regression with:
  - Pitcher's as-of season K% (A3, shrunk)
  - TTO indicator (times through order 1/2/3)
  - Batter K% vs pitcher hand (B3)
  - Matchup K rate (B2, from normalized formula)

Output: array of p_i values for the ordered batter sequence.
"""
import pickle
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.matchup import matchup_k_rate

MODEL_PATH = Path(__file__).parent / "stage_b_fitted.pkl"

LEAGUE_K_RATE = 0.225
LEAGUE_ZONE_PCT = 0.45
TTO_RATIOS = {1: 1.053, 2: 0.930, 3: 0.884, 4: 1.0}


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


# Optional Stage B features, in canonical design-matrix order. The core
# design (intercept, pitcher/batter logits, TTO dummies) is fixed.
EXTRA_FEATURES = ["zone_pct", "eastward_tz", "n_rookies"]

# What production actually uses. The cross-season re-gauntlet
# (tools/regauntlet.py, 12,653 OOS starts) demoted all three extras:
# no feature cleared drop-delta t>=2 in both temporal directions, and
# the core model matched the full model to within 0.00006 Brier on
# every split. Promotion back requires passing that same bar.
PRODUCTION_EXTRA_FEATURES: list[str] = []


class StageB:
    """Logistic regression for per-batter K probability.

    extra_features selects which optional features (from EXTRA_FEATURES)
    enter the design matrix — the re-gauntlet fits variants with subsets
    to measure each feature's marginal out-of-sample value.
    """

    def __init__(self, extra_features: list[str] | None = None):
        self.coefficients = None
        self.feature_names = None
        if extra_features is None:
            extra_features = list(EXTRA_FEATURES)
        unknown = set(extra_features) - set(EXTRA_FEATURES)
        if unknown:
            raise ValueError(f"Unknown Stage B extra features: {unknown}")
        # canonical order regardless of caller order
        self.extra_features = [f for f in EXTRA_FEATURES if f in extra_features]

    def _extra_column(self, df: pd.DataFrame, name: str) -> np.ndarray:
        if name == "zone_pct":
            return df["zone_pct"].values if "zone_pct" in df.columns else np.full(len(df), LEAGUE_ZONE_PCT)
        if name == "eastward_tz":
            return df["eastward_tz"].values if "eastward_tz" in df.columns else np.zeros(len(df))
        if name == "n_rookies":
            return df["n_rookies"].values if "n_rookies" in df.columns else np.zeros(len(df))
        raise ValueError(name)

    def _build_X(self, df: pd.DataFrame) -> np.ndarray:
        """Build design matrix from batter-level DataFrame."""
        tto_2 = (df["tto"] == 2).astype(float).values
        tto_3 = (df["tto"] >= 3).astype(float).values

        cols = [
            np.ones(len(df)),
            _logit(df["pitcher_k_pct"].values),
            _logit(df["batter_k_pct"].values),
            tto_2,
            tto_3,
        ]
        names = ["intercept", "logit_pitcher_k", "logit_batter_k", "tto_2", "tto_3"]

        for name in self.extra_features:
            cols.append(self._extra_column(df, name))
            names.append(name)

        self.feature_names = names
        return np.column_stack(cols)

    def _neg_log_lik(self, beta, X, y):
        """Negative log-likelihood for logistic regression."""
        eta = X @ beta
        p = _sigmoid(eta)
        ll = y * np.log(p + 1e-15) + (1 - y) * np.log(1 - p + 1e-15)
        return -np.sum(ll)

    def _grad(self, beta, X, y):
        """Gradient of negative log-likelihood."""
        eta = X @ beta
        p = _sigmoid(eta)
        return -X.T @ (y - p)

    def fit(self, df: pd.DataFrame):
        """Fit on batter-level data.

        df must have: is_k, tto, pitcher_k_pct, batter_k_pct.
        """
        from scipy.optimize import minimize

        X = self._build_X(df)
        y = df["is_k"].values.astype(float)

        beta_init = np.zeros(X.shape[1])
        beta_init[0] = _logit(y.mean())

        result = minimize(
            self._neg_log_lik, beta_init, args=(X, y),
            jac=self._grad,
            method="L-BFGS-B",
            options={"maxiter": 1000, "ftol": 1e-12},
        )

        self.coefficients = result.x

        print(f"  Stage B fitted:")
        for name, coef in zip(self.feature_names, self.coefficients):
            print(f"    {name:20s} = {coef:+.4f}")

        p_hat = _sigmoid(X @ self.coefficients)
        brier = np.mean((p_hat - y) ** 2)
        print(f"    In-sample Brier = {brier:.4f}")

    def predict_single(self, pitcher_k_pct: float, batter_k_pct: float,
                        tto: int, zone_pct: float | None = None,
                        eastward_tz: float = 0.0,
                        n_rookies: float = 0.0) -> float:
        """Predict K probability for one batter.

        Callers always pass the full feature set; only the features this
        model instance was fit with enter the design vector.
        """
        extra_values = {
            "zone_pct": zone_pct if zone_pct is not None else LEAGUE_ZONE_PCT,
            "eastward_tz": eastward_tz,
            "n_rookies": n_rookies,
        }

        x = np.array(
            [1.0, _logit(pitcher_k_pct), _logit(batter_k_pct),
             float(tto == 2), float(tto >= 3)]
            + [extra_values[name] for name in self.extra_features]
        )

        if self.coefficients is None:
            # Silently falling back to the matchup formula would mean a
            # failed model load produces different numbers instead of an
            # error — the pipeline would price a whole slate off the
            # wrong model and never say so (AUDIT A-007).
            raise RuntimeError(
                "Stage B has no fitted coefficients — load() was not called "
                "or the pickle is missing. Refusing to silently fall back to "
                "the matchup formula."
            )
        return float(_sigmoid(x @ self.coefficients))

    def predict_per_batter_k_prob(
        self,
        pitcher_k_pct: float,
        lineup_k_pcts: list[float],
        n_max: int = 40,
        zone_pct: float | None = None,
        eastward_tz: float = 0.0,
        n_rookies: float = 0.0,
    ) -> np.ndarray:
        """Return p_i for i = 1..n_max.

        lineup_k_pcts is a 9-element list of batter K% values in
        batting order. The sequence wraps with TTO decay applied.
        """
        if len(lineup_k_pcts) == 0:
            lineup_k_pcts = [LEAGUE_K_RATE] * 9

        probs = np.zeros(n_max)
        for i in range(n_max):
            slot = i % 9
            if i < 9:
                tto = 1
            elif i < 18:
                tto = 2
            elif i < 27:
                tto = 3
            else:
                tto = 4

            batter_k = lineup_k_pcts[slot] if slot < len(lineup_k_pcts) else LEAGUE_K_RATE
            probs[i] = self.predict_single(pitcher_k_pct, batter_k, tto, zone_pct,
                                           eastward_tz, n_rookies)

        return probs

    def save(self, path: Path | None = None):
        path = path or MODEL_PATH
        with open(path, "wb") as f:
            pickle.dump({
                "coefficients": self.coefficients,
                "feature_names": self.feature_names,
                "extra_features": self.extra_features,
            }, f)

    def load(self, path: Path | None = None):
        path = path or MODEL_PATH
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.coefficients = data["coefficients"]
        self.feature_names = data["feature_names"]
        # Legacy pickles predate feature subsets and carry the full trio.
        self.extra_features = data.get("extra_features", list(EXTRA_FEATURES))


ROOKIE_BF_THRESHOLD = 100


def prepare_training_data(start_date: date, end_date: date) -> pd.DataFrame:
    """Build batter-level training DataFrame with strictly as-of features.

    Every feature reflects only games BEFORE the row's game (via
    features.asof tables), matching what the live pipeline feeds the
    model. Starter games without enough pitcher history (< 50 prior BF)
    are dropped; batters with no prior history fall back to league rate.
    """
    from data.backfill_statcast import load_cached
    from features.asof import asof_pitcher_game_table, asof_batter_game_table

    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    completed = df[df["events"].notna()].copy()

    pt = asof_pitcher_game_table(df)
    bt = asof_batter_game_table(df)

    starters = pt[
        (pt["actual_bf"] >= 9)
        & (pt["prior_bf"] >= 50)
        & pt["asof_k_pct"].notna()
    ]

    starter_abs = completed.merge(
        starters[["game_pk", "pitcher"]],
        on=["game_pk", "pitcher"], how="inner"
    )

    starter_abs = starter_abs.sort_values(["game_pk", "pitcher", "at_bat_number"])
    starter_abs["bf_seq"] = starter_abs.groupby(["game_pk", "pitcher"]).cumcount() + 1
    starter_abs["tto"] = np.select(
        [starter_abs["bf_seq"] <= 9, starter_abs["bf_seq"] <= 18, starter_abs["bf_seq"] <= 27],
        [1, 2, 3], default=4,
    )
    starter_abs["is_k"] = starter_abs["events"].isin(
        ["strikeout", "strikeout_double_play"]
    ).astype(int)

    batter_df = starter_abs[[
        "game_pk", "pitcher", "batter", "bf_seq", "tto", "is_k",
    ]].copy()

    batter_df = batter_df.merge(
        starters[["game_pk", "pitcher", "asof_k_pct_shrunk", "asof_zone_pct", "eastward_tz"]].rename(
            columns={"asof_k_pct_shrunk": "pitcher_k_pct"}
        ),
        on=["game_pk", "pitcher"], how="inner",
    )
    batter_df["zone_pct"] = batter_df["asof_zone_pct"].fillna(LEAGUE_ZONE_PCT)
    batter_df = batter_df.drop(columns=["asof_zone_pct"])

    batter_df = batter_df.merge(
        bt[["batter", "game_pk", "asof_k_pct_shrunk", "prior_bf"]].rename(
            columns={"asof_k_pct_shrunk": "batter_k_pct", "prior_bf": "batter_prior_bf"}
        ),
        on=["batter", "game_pk"], how="left",
    )
    batter_df["batter_k_pct"] = batter_df["batter_k_pct"].fillna(LEAGUE_K_RATE)
    batter_df["batter_prior_bf"] = batter_df["batter_prior_bf"].fillna(0)

    # n_rookies: as-of prior BF of the first 9 unique batters faced.
    lineups = starter_abs.drop_duplicates(["game_pk", "pitcher", "batter"])
    lineups = lineups.groupby(["game_pk", "pitcher"]).head(9)[["game_pk", "pitcher", "batter"]]
    lineups = lineups.merge(
        bt[["batter", "game_pk", "prior_bf"]],
        on=["batter", "game_pk"], how="left",
    )
    lineups["is_rookie"] = (lineups["prior_bf"].fillna(0) < ROOKIE_BF_THRESHOLD).astype(int)
    rookie_counts = lineups.groupby(["game_pk", "pitcher"])["is_rookie"].sum().reset_index(name="n_rookies")
    batter_df = batter_df.merge(rookie_counts, on=["game_pk", "pitcher"], how="left")
    batter_df["n_rookies"] = batter_df["n_rookies"].fillna(0.0)

    return batter_df


def fit_and_evaluate(start_date: date = date(2026, 6, 1),
                     end_date: date = date(2026, 8, 4),
                     save_path: Path | None = None):
    """Fit Stage B on as-of training data and evaluate."""
    print("Preparing Stage B training data (as-of features)...")
    train_df = prepare_training_data(start_date, end_date)
    print(f"  {len(train_df)} batter-level rows")
    print(f"  Overall K rate: {train_df['is_k'].mean():.3f}")

    model = StageB()
    model.fit(train_df)

    print("\n  TTO effect verification:")
    for tto in [1, 2, 3]:
        p = model.predict_single(0.225, 0.225, tto)
        print(f"    TTO {tto}: p(K) = {p:.3f} (avg pitcher vs avg batter)")

    print("\n  Matchup range:")
    for pk in [0.15, 0.225, 0.30]:
        for bk in [0.15, 0.225, 0.30]:
            p = model.predict_single(pk, bk, 1)
            print(f"    P_K%={pk:.0%} vs B_K%={bk:.0%}: p(K) = {p:.3f}")

    model.save(save_path)
    print(f"\n  Model saved to {save_path or MODEL_PATH}")

    return model


if __name__ == "__main__":
    fit_and_evaluate()
