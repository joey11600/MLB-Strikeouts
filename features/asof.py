"""
As-of-date utility for the Strikeouts Model.

EVERY rate feature in training must flow through this module. It takes a
game timestamp and returns the state of the world *before* that game.

Season-to-date leaderboard aggregates include the game being predicted
and are BANNED for training. This module is the single enforcement point.

The core idea: given a game_pk, load all Statcast pitches from the
current season BEFORE that game's first pitch, then compute rolling
and season-level aggregates from that filtered dataset.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached, CACHE_DIR


def _season_start(year: int) -> date:
    """Approximate MLB season start dates."""
    starts = {2024: date(2024, 3, 28), 2025: date(2025, 3, 27), 2026: date(2026, 3, 26)}
    return starts.get(year, date(year, 3, 28))


def load_pitches_before_game(game_pk: int, game_date: date, season_year: int | None = None) -> pd.DataFrame:
    """Load all Statcast pitches from the season up to but NOT including game_pk.

    This is the foundational anti-leakage filter. Every rate feature
    computed for training must use this function, never raw season data.
    """
    if season_year is None:
        season_year = game_date.year

    season_start = _season_start(season_year)
    df = load_cached(season_start, game_date)

    if df.empty:
        return df

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    df = df[df["game_pk"] != game_pk]

    if "game_date" in df.columns:
        game_date_ts = pd.Timestamp(game_date)
        df = df[df["game_date"] <= game_date_ts]

    return df


def pitcher_season_stats(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """Compute season-level pitcher stats from filtered pitch data.

    All computed as-of: the pitches dataframe must already be filtered
    to exclude the game being predicted.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty:
        return _empty_pitcher_stats()

    total_pitches = len(p)

    swinging_strikes = p["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul_tip",
    ]).sum()

    called_strikes = (p["description"] == "called_strike").sum()

    all_strikes = swinging_strikes + called_strikes + (
        p["description"].isin(["foul", "foul_bunt"]).sum()
    )

    abs_completed = p["events"].notna()
    strikeouts = p.loc[abs_completed, "events"].isin([
        "strikeout", "strikeout_double_play",
    ]).sum()

    total_batters = abs_completed.sum()

    swstr_pct = swinging_strikes / total_pitches if total_pitches > 0 else 0
    csw_pct = (called_strikes + swinging_strikes) / total_pitches if total_pitches > 0 else 0
    k_pct = strikeouts / total_batters if total_batters > 0 else 0

    swings = p["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "foul_bunt",
        "hit_into_play",
    ]).sum()
    whiffs = swinging_strikes
    contact = swings - whiffs
    contact_pct = contact / swings if swings > 0 else 0

    outside_zone = p["zone"].isin([11, 12, 13, 14]) if "zone" in p.columns else pd.Series(dtype=bool)
    swings_outside = (outside_zone & p["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "hit_into_play",
    ])).sum() if len(outside_zone) > 0 else 0
    pitches_outside = outside_zone.sum() if len(outside_zone) > 0 else 0
    chase_rate = swings_outside / pitches_outside if pitches_outside > 0 else 0

    two_strike = p[((p["strikes"] == 2) & abs_completed)]
    two_strike_ks = two_strike["events"].isin(["strikeout", "strikeout_double_play"]).sum()
    put_away_rate = two_strike_ks / len(two_strike) if len(two_strike) > 0 else 0

    return {
        "swstr_pct": swstr_pct,
        "csw_pct": csw_pct,
        "k_pct": k_pct,
        "contact_pct": contact_pct,
        "chase_rate": chase_rate,
        "put_away_rate": put_away_rate,
        "total_pitches": total_pitches,
        "total_batters_faced": total_batters,
        "total_strikeouts": strikeouts,
    }


def _empty_pitcher_stats() -> dict:
    return {
        "swstr_pct": None,
        "csw_pct": None,
        "k_pct": None,
        "contact_pct": None,
        "chase_rate": None,
        "put_away_rate": None,
        "total_pitches": 0,
        "total_batters_faced": 0,
        "total_strikeouts": 0,
    }


def pitcher_rolling_k_pct(pitches: pd.DataFrame, pitcher_id: int, n_starts: int = 10) -> dict:
    """Compute rolling K% over last N starts from filtered data."""
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "game_pk" not in p.columns:
        return {"rolling_k_pct": None, "n_starts_found": 0}

    games = sorted(p["game_pk"].unique())
    recent_games = games[-n_starts:]

    recent = p[p["game_pk"].isin(recent_games)]
    abs_completed = recent["events"].notna()
    ks = recent.loc[abs_completed, "events"].isin([
        "strikeout", "strikeout_double_play",
    ]).sum()
    bf = abs_completed.sum()

    return {
        "rolling_k_pct": ks / bf if bf > 0 else None,
        "n_starts_found": len(recent_games),
    }


def pitcher_velocity_delta(pitches: pd.DataFrame, pitcher_id: int, baseline_days: int = 30) -> dict:
    """Compute velocity delta vs own baseline from filtered data.

    A13: −1.5 mph from personal baseline is real signal.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "release_speed" not in p.columns:
        return {"velo_delta": None}

    fastballs = p[p["pitch_type"].isin(["FF", "SI", "FC"])]
    if fastballs.empty:
        return {"velo_delta": None}

    if "game_date" in fastballs.columns:
        latest = fastballs["game_date"].max()
        baseline_start = latest - pd.Timedelta(days=baseline_days)

        recent = fastballs[fastballs["game_date"] > baseline_start]
        baseline = fastballs[fastballs["game_date"] <= baseline_start]

        if recent.empty or baseline.empty:
            return {"velo_delta": None}

        return {
            "velo_delta": float(recent["release_speed"].mean() - baseline["release_speed"].mean()),
        }

    return {"velo_delta": None}


def pitcher_arsenal(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """Compute pitch arsenal stats from filtered data.

    A15: pitch-type whiff% × usage
    A16: arsenal breadth (# pitches >= 10% usage)
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "pitch_type" not in p.columns:
        return {"arsenal_breadth": None, "pitch_mix": {}}

    pitch_counts = p["pitch_type"].value_counts()
    total = len(p)
    usage = (pitch_counts / total)

    breadth = int((usage >= 0.10).sum())

    pitch_mix = {}
    for pt in pitch_counts.index:
        pt_pitches = p[p["pitch_type"] == pt]
        pt_swings = pt_pitches["description"].isin([
            "swinging_strike", "swinging_strike_blocked",
            "foul", "foul_tip", "foul_bunt", "hit_into_play",
        ])
        pt_whiffs = pt_pitches["description"].isin([
            "swinging_strike", "swinging_strike_blocked", "foul_tip",
        ])
        whiff_pct = pt_whiffs.sum() / pt_swings.sum() if pt_swings.sum() > 0 else 0
        pitch_mix[pt] = {
            "usage": float(usage.get(pt, 0)),
            "whiff_pct": float(whiff_pct),
            "count": int(pitch_counts.get(pt, 0)),
        }

    return {
        "arsenal_breadth": breadth,
        "pitch_mix": pitch_mix,
    }


def batter_k_rate(pitches: pd.DataFrame, batter_id: int) -> dict:
    """Compute batter K% from filtered data."""
    b = pitches[pitches["batter"] == batter_id]
    abs_completed = b[b["events"].notna()]
    if abs_completed.empty:
        return {"batter_k_pct": None, "batter_bf": 0}

    ks = abs_completed["events"].isin(["strikeout", "strikeout_double_play"]).sum()
    return {
        "batter_k_pct": float(ks / len(abs_completed)),
        "batter_bf": int(len(abs_completed)),
    }


def batter_k_rate_by_hand(pitches: pd.DataFrame, batter_id: int) -> dict:
    """Compute batter K% split by pitcher handedness (B3)."""
    b = pitches[pitches["batter"] == batter_id]
    abs_completed = b[b["events"].notna()]
    if abs_completed.empty:
        return {"batter_k_pct_vs_r": None, "batter_k_pct_vs_l": None}

    result = {}
    for hand, label in [("R", "vs_r"), ("L", "vs_l")]:
        hand_abs = abs_completed[abs_completed["p_throws"] == hand]
        if hand_abs.empty:
            result[f"batter_k_pct_{label}"] = None
        else:
            ks = hand_abs["events"].isin(["strikeout", "strikeout_double_play"]).sum()
            result[f"batter_k_pct_{label}"] = float(ks / len(hand_abs))
    return result


# Empirical-Bayes shrinkage pseudo-counts: the sample size at which a
# K% observation is weighted 50/50 against league average (stabilization
# points from published reliability research: ~70 BF pitchers, ~60 PA batters).
PITCHER_K_PSEUDO_BF = 70
BATTER_K_PSEUDO_BF = 60
LEAGUE_K_RATE = 0.225


def shrink_rate(successes: float, trials: float,
                pseudo_count: float, league_rate: float = LEAGUE_K_RATE) -> float:
    """Shrink an observed rate toward league average by sample size.

    shrunk = (successes + pseudo * league) / (trials + pseudo)

    With 0 trials this returns exactly the league rate; as trials grow
    the observed rate dominates. This is the required transform for
    as-of rates early in a season, where raw values are mostly noise.
    """
    trials = trials or 0
    successes = successes or 0
    return (successes + pseudo_count * league_rate) / (trials + pseudo_count)


def _add_prior_stats(per_game: pd.DataFrame, entity_col: str,
                     k_pseudo_count: float | None = None) -> pd.DataFrame:
    """Add strictly-prior cumulative stats to a per-entity per-game table.

    per_game must have columns [entity_col, game_pk, game_date, bf, k].
    Rows are sorted by (entity, game_date, game_pk); each row's prior_*
    columns are computed from rows strictly before it via
    cumsum-minus-current, so the current game never leaks into its own
    features. Doubleheader ordering falls back to game_pk within a date.

    Adds: prior_games, prior_bf, prior_k, asof_k_pct,
          asof_k_pct_shrunk (if k_pseudo_count given),
          asof_bf_mean, asof_bf_std (expanding, ddof=1).
    """
    g = per_game.sort_values([entity_col, "game_date", "game_pk"]).copy()
    grp = g.groupby(entity_col, sort=False)

    g["prior_games"] = grp.cumcount()
    g["prior_bf"] = grp["bf"].cumsum() - g["bf"]
    g["prior_k"] = grp["k"].cumsum() - g["k"]

    with np.errstate(invalid="ignore", divide="ignore"):
        g["asof_k_pct"] = np.where(
            g["prior_bf"] > 0, g["prior_k"] / g["prior_bf"], np.nan
        )

    if k_pseudo_count is not None:
        g["asof_k_pct_shrunk"] = (
            (g["prior_k"] + k_pseudo_count * LEAGUE_K_RATE)
            / (g["prior_bf"] + k_pseudo_count)
        )

    # Expanding mean/std of per-game BF over prior games only:
    # prior_sum = cumsum - current, prior_sumsq = cumsum(x^2) - current^2,
    # var = (sumsq - sum^2/n) / (n-1).
    bf = g["bf"].astype(float)
    bf_sq = bf ** 2
    prior_sum = grp["bf"].cumsum().astype(float) - bf
    prior_sumsq = bf_sq.groupby(g[entity_col], sort=False).cumsum() - bf_sq
    n = g["prior_games"].astype(float)

    with np.errstate(invalid="ignore", divide="ignore"):
        g["asof_bf_mean"] = np.where(n > 0, prior_sum / n, np.nan)
        var = np.where(
            n > 1,
            (prior_sumsq - prior_sum ** 2 / np.where(n > 0, n, 1)) / np.where(n > 1, n - 1, 1),
            np.nan,
        )
    g["asof_bf_std"] = np.sqrt(np.clip(var, 0, None))

    return g


def asof_pitcher_game_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (pitcher, game): labels + strictly-prior features.

    This is the vectorized bulk counterpart to load_pitches_before_game
    for backtests and training-set assembly. Every asof_* column reflects
    only games before that row's game.

    Columns: pitcher, game_pk, game_date, home_team,
             actual_bf, actual_k (labels),
             prior_games, prior_bf, prior_k, asof_k_pct,
             asof_bf_mean, asof_bf_std, asof_zone_pct, eastward_tz.
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    completed = df[df["events"].notna()]
    is_k = completed["events"].isin(["strikeout", "strikeout_double_play"])

    pg = completed.assign(is_k=is_k.astype(int)).groupby(
        ["pitcher", "game_pk"]
    ).agg(
        bf=("events", "count"),
        k=("is_k", "sum"),
        game_date=("game_date", "first"),
    ).reset_index()

    # Zone counts use every pitch, not just PA-ending ones.
    if "zone" in df.columns:
        zdf = df[df["zone"].notna()]
        zg = zdf.assign(in_zone=zdf["zone"].isin(range(1, 10)).astype(int)).groupby(
            ["pitcher", "game_pk"]
        ).agg(
            zone_valid=("in_zone", "count"),
            zone_in=("in_zone", "sum"),
        ).reset_index()
        pg = pg.merge(zg, on=["pitcher", "game_pk"], how="left")
        pg[["zone_valid", "zone_in"]] = pg[["zone_valid", "zone_in"]].fillna(0)
    else:
        pg["zone_valid"] = 0
        pg["zone_in"] = 0

    if "home_team" in df.columns:
        teams = df.groupby("game_pk").agg(home_team=("home_team", "first")).reset_index()
        pg = pg.merge(teams, on="game_pk", how="left")
    else:
        pg["home_team"] = None

    pg = _add_prior_stats(pg, "pitcher", k_pseudo_count=PITCHER_K_PSEUDO_BF)

    grp = pg.groupby("pitcher", sort=False)
    prior_zone_valid = grp["zone_valid"].cumsum() - pg["zone_valid"]
    prior_zone_in = grp["zone_in"].cumsum() - pg["zone_in"]
    with np.errstate(invalid="ignore", divide="ignore"):
        pg["asof_zone_pct"] = np.where(
            prior_zone_valid >= 50, prior_zone_in / prior_zone_valid, np.nan
        )

    from features.t2_candidates import TEAM_TIMEZONES
    pg["curr_tz"] = pg["home_team"].map(TEAM_TIMEZONES)
    pg["prev_tz"] = grp["curr_tz"].shift(1)
    pg["eastward_tz"] = (pg["curr_tz"] - pg["prev_tz"]).clip(lower=0).fillna(0.0)

    pg = pg.rename(columns={"bf": "actual_bf", "k": "actual_k"})
    return pg.drop(columns=["zone_valid", "zone_in", "curr_tz", "prev_tz"])


def asof_batter_game_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (batter, game): strictly-prior batter K stats.

    Columns: batter, game_pk, game_date, bf, k,
             prior_games, prior_bf, prior_k, asof_k_pct,
             asof_bf_mean, asof_bf_std.
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    completed = df[df["events"].notna()]
    is_k = completed["events"].isin(["strikeout", "strikeout_double_play"])

    bg = completed.assign(is_k=is_k.astype(int)).groupby(
        ["batter", "game_pk"]
    ).agg(
        bf=("events", "count"),
        k=("is_k", "sum"),
        game_date=("game_date", "first"),
    ).reset_index()

    return _add_prior_stats(bg, "batter", k_pseudo_count=BATTER_K_PSEUDO_BF)


def as_of_features(game_pk: int, game_date: date, pitcher_id: int) -> dict:
    """Return all as-of pitcher features for a game.

    This is the main entry point. Every rate feature is computed from
    pitch-level data up to but NOT including the target game.
    """
    pitches = load_pitches_before_game(game_pk, game_date)
    if pitches.empty:
        return _empty_pitcher_stats()

    features = {}
    features.update(pitcher_season_stats(pitches, pitcher_id))

    for n in [3, 5, 10]:
        rolling = pitcher_rolling_k_pct(pitches, pitcher_id, n_starts=n)
        features[f"rolling_k_pct_{n}"] = rolling["rolling_k_pct"]
    features["n_starts_found"] = pitcher_rolling_k_pct(pitches, pitcher_id, 10)["n_starts_found"]

    features.update(pitcher_velocity_delta(pitches, pitcher_id))
    features.update(pitcher_arsenal(pitches, pitcher_id))

    return features
