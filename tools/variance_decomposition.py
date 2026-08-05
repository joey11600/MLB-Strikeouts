"""Recompute the §1.1 variance decomposition on real data.

Uses each pitcher's season K% as the talent-level "true p" rather than
the game-level realized K/BF (which would be circular — K = BF * p by
definition).

Decomposes Var(K) into:
  1. Bernoulli (irreducible) — E[BF_i * p_talent * (1-p_talent)]
  2. Batters-faced variance — Var(BF_i) * E[p_talent]^2
  3. True-rate / matchup variance — Var(p_talent) * E[BF_i]^2
  4. Covariance term — 2 * E[BF_i] * Cov(BF_i, p_talent)

Also splits BF variance into predictable (pitcher-level mean) vs
residual (game-to-game noise).
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached


def compute_game_level_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-pitcher-per-game stats from pitch-level data."""
    completed_abs = df[df["events"].notna()].copy()

    ks = completed_abs[completed_abs["events"].isin([
        "strikeout", "strikeout_double_play"
    ])]

    game_stats = completed_abs.groupby(["game_pk", "pitcher"]).agg(
        bf=("events", "count"),
    ).reset_index()

    k_counts = ks.groupby(["game_pk", "pitcher"]).size().reset_index(name="strikeouts")
    game_stats = game_stats.merge(k_counts, on=["game_pk", "pitcher"], how="left")
    game_stats["strikeouts"] = game_stats["strikeouts"].fillna(0).astype(int)
    game_stats["game_k_rate"] = game_stats["strikeouts"] / game_stats["bf"]

    game_stats = game_stats[game_stats["bf"] >= 9]

    return game_stats


def attach_season_k_rate(game_stats: pd.DataFrame) -> pd.DataFrame:
    """Compute each pitcher's season K% and attach as the talent proxy."""
    season = game_stats.groupby("pitcher").agg(
        season_k=("strikeouts", "sum"),
        season_bf=("bf", "sum"),
        n_starts=("game_pk", "count"),
    ).reset_index()
    season["season_k_pct"] = season["season_k"] / season["season_bf"]

    game_stats = game_stats.merge(
        season[["pitcher", "season_k_pct", "n_starts"]],
        on="pitcher", how="left"
    )
    game_stats = game_stats[game_stats["n_starts"] >= 3]
    return game_stats


def decompose_variance(gs: pd.DataFrame) -> dict:
    """Four-component decomposition using season K% as talent proxy."""
    K = gs["strikeouts"].values.astype(float)
    BF = gs["bf"].values.astype(float)
    p = gs["season_k_pct"].values

    E_BF = np.mean(BF)
    E_p = np.mean(p)
    Var_BF = np.var(BF, ddof=1)
    Var_p = np.var(p, ddof=1)
    Cov_BF_p = np.cov(BF, p, ddof=1)[0, 1]

    bernoulli = np.mean(BF * p * (1 - p))

    signal_per_game = BF * p
    signal_var = np.var(signal_per_game, ddof=1)

    bf_component = E_p**2 * Var_BF
    rate_component = E_BF**2 * Var_p
    cov_component = 2 * E_BF * Cov_BF_p

    total_theoretical = bernoulli + signal_var
    total_empirical = np.var(K, ddof=1)

    pitcher_bf_means = gs.groupby("pitcher")["bf"].mean()
    bf_between = np.var(pitcher_bf_means, ddof=1)
    pitcher_bf_vars = gs.groupby("pitcher")["bf"].var(ddof=1).dropna()
    bf_within = np.mean(pitcher_bf_vars)
    bf_predictable_share = bf_between / (bf_between + bf_within) * 100 if (bf_between + bf_within) > 0 else 0

    return {
        "n_starts": len(gs),
        "n_pitchers": gs["pitcher"].nunique(),
        "E_BF": E_BF,
        "E_p": E_p,
        "SD_BF": np.sqrt(Var_BF),
        "SD_p": np.sqrt(Var_p),
        "Var_K_empirical": total_empirical,
        "SD_K_empirical": np.sqrt(total_empirical),
        "bernoulli": bernoulli,
        "signal_var": signal_var,
        "bf_component": bf_component,
        "rate_component": rate_component,
        "cov_component": cov_component,
        "total_theoretical": total_theoretical,
        "bernoulli_pct": bernoulli / total_empirical * 100,
        "signal_pct": signal_var / total_empirical * 100,
        "residual": total_empirical - total_theoretical,
        "residual_pct": (total_empirical - total_theoretical) / total_empirical * 100,
        "Cov_BF_p": Cov_BF_p,
        "bf_between": bf_between,
        "bf_within": bf_within,
        "bf_predictable_share": bf_predictable_share,
    }


def main():
    print("Loading cached Statcast data...")
    df = load_cached(date(2026, 6, 1), date(2026, 8, 3))

    if df.empty:
        print("No data. Run backfill first: python data/backfill_statcast.py")
        return

    print(f"Loaded {len(df):,} pitches")

    game_stats = compute_game_level_stats(df)
    print(f"{len(game_stats)} pitcher-game appearances (>=9 BF)")

    game_stats = attach_season_k_rate(game_stats)
    print(f"{len(game_stats)} starts from {game_stats['pitcher'].nunique()} pitchers (>=3 starts)")

    result = decompose_variance(game_stats)

    print("\n" + "=" * 60)
    print("VARIANCE DECOMPOSITION OF STRIKEOUTS PER START")
    print("=" * 60)
    print(f"\nSample: {result['n_starts']} starts, {result['n_pitchers']} pitchers, June-Aug 2026")
    print(f"E[BF] = {result['E_BF']:.1f}   E[K%] = {result['E_p']:.3f}")
    print(f"SD(BF) = {result['SD_BF']:.2f}   SD(K%) = {result['SD_p']:.4f}")
    print(f"Cov(BF, K%) = {result['Cov_BF_p']:.4f}")
    print(f"\nEmpirical Var(K) = {result['Var_K_empirical']:.3f}  (SD = {result['SD_K_empirical']:.2f})")

    print(f"\n{'Component':<35} {'Variance':>10} {'% of Var(K)':>12}")
    print("-" * 60)
    print(f"{'Bernoulli (irreducible)':<35} {result['bernoulli']:>10.3f} {result['bernoulli_pct']:>11.1f}%")
    print(f"{'Signal = Var(BF * p_talent)':<35} {result['signal_var']:>10.3f} {result['signal_pct']:>11.1f}%")
    print(f"{'  - from Var(BF)':<35} {result['bf_component']:>10.3f}")
    print(f"{'  - from Var(K%)':<35} {result['rate_component']:>10.3f}")
    print(f"{'  - from 2*Cov(BF,K%)':<35} {result['cov_component']:>10.3f}")
    print(f"{'Residual (unexplained)':<35} {result['residual']:>10.3f} {result['residual_pct']:>11.1f}%")
    print("-" * 60)
    print(f"{'Empirical Var(K)':<35} {result['Var_K_empirical']:>10.3f} {'100.0':>11}%")

    print(f"\n--- BF Variance Split ---")
    print(f"Between-pitcher (predictable):  {result['bf_between']:.2f} ({result['bf_predictable_share']:.0f}%)")
    print(f"Within-pitcher (game noise):    {result['bf_within']:.2f} ({100-result['bf_predictable_share']:.0f}%)")

    if result['Cov_BF_p'] > 0:
        print(f"\nCov(BF, K%) is POSITIVE ({result['Cov_BF_p']:.4f}):")
        print("  Better K pitchers also face more batters (go deeper). This")
        print("  inflates the signal — our model must capture this dependence.")
    else:
        print(f"\nCov(BF, K%) is NEGATIVE ({result['Cov_BF_p']:.4f}):")
        print("  Pitchers who face more batters have LOWER K rates.")

    print(f"\nKey insight for modeling:")
    print(f"  {result['bernoulli_pct']:.0f}% of Var(K) is irreducible Bernoulli noise.")
    print(f"  {result['signal_pct']:.0f}% is signal from talent and workload differences.")
    print(f"  {result['residual_pct']:.0f}% is residual (game-level p deviation from season talent).")
    print(f"  A perfect model (knowing true BF and true p) explains at most {result['signal_pct']:.0f}%.")
    print(f"  Realistic ceiling with estimated BF and p: ~{result['signal_pct']*0.5:.0f}-{result['signal_pct']*0.7:.0f}%.")


if __name__ == "__main__":
    main()
