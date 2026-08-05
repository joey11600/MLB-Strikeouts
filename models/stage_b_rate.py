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


class StageB:
    """Logistic regression for per-batter K probability."""

    def __init__(self):
        self.coefficients = None
        self.feature_names = None

    def _build_X(self, df: pd.DataFrame) -> np.ndarray:
        """Build design matrix from batter-level DataFrame."""
        tto_2 = (df["tto"] == 2).astype(float).values
        tto_3 = (df["tto"] >= 3).astype(float).values
        pitcher_k = df["pitcher_k_pct"].values
        batter_k = df["batter_k_pct"].values
        zone_pct = df["zone_pct"].values if "zone_pct" in df.columns else np.full(len(df), LEAGUE_ZONE_PCT)
        eastward_tz = df["eastward_tz"].values if "eastward_tz" in df.columns else np.zeros(len(df))
        n_rookies = df["n_rookies"].values if "n_rookies" in df.columns else np.zeros(len(df))

        X = np.column_stack([
            np.ones(len(df)),
            _logit(pitcher_k),
            _logit(batter_k),
            tto_2,
            tto_3,
            zone_pct,
            eastward_tz,
            n_rookies,
        ])

        self.feature_names = [
            "intercept", "logit_pitcher_k", "logit_batter_k",
            "tto_2", "tto_3", "zone_pct", "eastward_tz", "n_rookies",
        ]
        return X

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
        """Predict K probability for one batter."""
        tto_2 = float(tto == 2)
        tto_3 = float(tto >= 3)
        zp = zone_pct if zone_pct is not None else LEAGUE_ZONE_PCT

        x = np.array([
            1.0,
            _logit(pitcher_k_pct),
            _logit(batter_k_pct),
            tto_2,
            tto_3,
            zp,
            eastward_tz,
            n_rookies,
        ])

        if self.coefficients is not None:
            return float(_sigmoid(x @ self.coefficients))
        else:
            return matchup_k_rate(batter_k_pct, pitcher_k_pct) * TTO_RATIOS.get(tto, 1.0)

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
            }, f)

    def load(self, path: Path | None = None):
        path = path or MODEL_PATH
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.coefficients = data["coefficients"]
        self.feature_names = data["feature_names"]


def prepare_training_data(start_date: date, end_date: date) -> pd.DataFrame:
    """Build batter-level training DataFrame with features."""
    from data.backfill_statcast import load_cached

    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    completed = df[df["events"].notna()].copy()

    pitcher_stats = completed.groupby("pitcher").agg(
        pitcher_total_bf=("events", "count"),
    ).reset_index()
    pitcher_ks = completed[completed["events"].isin(["strikeout", "strikeout_double_play"])]
    pitcher_k_counts = pitcher_ks.groupby("pitcher").size().reset_index(name="pitcher_total_k")
    pitcher_stats = pitcher_stats.merge(pitcher_k_counts, on="pitcher", how="left")
    pitcher_stats["pitcher_total_k"] = pitcher_stats["pitcher_total_k"].fillna(0)
    pitcher_stats["pitcher_k_pct"] = pitcher_stats["pitcher_total_k"] / pitcher_stats["pitcher_total_bf"]
    pitcher_stats = pitcher_stats[pitcher_stats["pitcher_total_bf"] >= 50]

    batter_stats = completed.groupby("batter").agg(
        batter_total_bf=("events", "count"),
    ).reset_index()
    batter_ks = completed[completed["events"].isin(["strikeout", "strikeout_double_play"])]
    batter_k_counts = batter_ks.groupby("batter").size().reset_index(name="batter_total_k")
    batter_stats = batter_stats.merge(batter_k_counts, on="batter", how="left")
    batter_stats["batter_total_k"] = batter_stats["batter_total_k"].fillna(0)
    batter_stats["batter_k_pct"] = batter_stats["batter_total_k"] / batter_stats["batter_total_bf"]

    game_pitcher_bf = completed.groupby(["game_pk", "pitcher"]).size().reset_index(name="game_bf")
    starters = game_pitcher_bf[game_pitcher_bf["game_bf"] >= 9]

    starter_abs = completed.merge(
        starters[["game_pk", "pitcher"]],
        on=["game_pk", "pitcher"], how="inner"
    )

    records = []
    for (game_pk, pitcher_id), group in starter_abs.groupby(["game_pk", "pitcher"]):
        sorted_abs = group.sort_values("at_bat_number")
        for seq, (_, row) in enumerate(sorted_abs.iterrows(), 1):
            if seq <= 9:
                tto = 1
            elif seq <= 18:
                tto = 2
            elif seq <= 27:
                tto = 3
            else:
                tto = 4

            records.append({
                "game_pk": game_pk,
                "pitcher": pitcher_id,
                "batter": row["batter"],
                "bf_seq": seq,
                "tto": tto,
                "is_k": 1 if row["events"] in ("strikeout", "strikeout_double_play") else 0,
            })

    batter_df = pd.DataFrame(records)

    batter_df = batter_df.merge(
        pitcher_stats[["pitcher", "pitcher_k_pct"]],
        on="pitcher", how="inner"
    )
    batter_df = batter_df.merge(
        batter_stats[["batter", "batter_k_pct"]],
        on="batter", how="left"
    )
    batter_df["batter_k_pct"] = batter_df["batter_k_pct"].fillna(LEAGUE_K_RATE)

    if "zone" in df.columns:
        zone_valid = df[df["zone"].notna()]
        pitcher_zone = zone_valid.groupby("pitcher").apply(
            lambda g: g["zone"].isin(range(1, 10)).sum() / len(g)
            if len(g) >= 50 else None,
            include_groups=False,
        ).reset_index(name="zone_pct")
        batter_df = batter_df.merge(pitcher_zone, on="pitcher", how="left")
    else:
        batter_df["zone_pct"] = LEAGUE_ZONE_PCT
    batter_df["zone_pct"] = batter_df["zone_pct"].fillna(LEAGUE_ZONE_PCT)

    from features.t2_candidates import TEAM_TIMEZONES
    if "home_team" in df.columns and "game_date" in df.columns:
        game_teams = df.groupby("game_pk").agg(
            home_team=("home_team", "first"),
            game_date=("game_date", "first"),
        ).reset_index()
        pitcher_games = starter_abs.groupby(["game_pk", "pitcher"]).first().reset_index()[["game_pk", "pitcher"]]
        pitcher_games = pitcher_games.merge(game_teams, on="game_pk", how="left")
        pitcher_games = pitcher_games.sort_values(["pitcher", "game_date"])
        pitcher_games["curr_tz"] = pitcher_games["home_team"].map(TEAM_TIMEZONES)
        pitcher_games["prev_tz"] = pitcher_games.groupby("pitcher")["curr_tz"].shift(1)
        pitcher_games["eastward_tz"] = (pitcher_games["curr_tz"] - pitcher_games["prev_tz"]).clip(lower=0).fillna(0)
        batter_df = batter_df.merge(
            pitcher_games[["game_pk", "pitcher", "eastward_tz"]],
            on=["game_pk", "pitcher"], how="left",
        )
    else:
        batter_df["eastward_tz"] = 0.0
    batter_df["eastward_tz"] = batter_df["eastward_tz"].fillna(0.0)

    if "batter" in completed.columns:
        game_lineups = starter_abs.groupby(["game_pk", "pitcher"]).apply(
            lambda g: g.drop_duplicates("batter").head(9), include_groups=False
        ).reset_index(level=[0, 1])[["game_pk", "pitcher", "batter"]]
        rookie_threshold = 100
        game_lineups = game_lineups.merge(
            batter_stats[["batter", "batter_total_bf"]], on="batter", how="left"
        )
        game_lineups["is_rookie"] = (game_lineups["batter_total_bf"].fillna(0) < rookie_threshold).astype(int)
        rookie_counts = game_lineups.groupby(["game_pk", "pitcher"])["is_rookie"].sum().reset_index(name="n_rookies")
        batter_df = batter_df.merge(rookie_counts, on=["game_pk", "pitcher"], how="left")
    else:
        batter_df["n_rookies"] = 0.0
    batter_df["n_rookies"] = batter_df["n_rookies"].fillna(0.0)

    return batter_df


def fit_and_evaluate():
    """Fit Stage B and evaluate."""
    print("Preparing Stage B training data...")
    train_df = prepare_training_data(date(2026, 6, 1), date(2026, 8, 3))
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

    model.save()
    print(f"\n  Model saved to {MODEL_PATH}")

    return model


if __name__ == "__main__":
    fit_and_evaluate()
