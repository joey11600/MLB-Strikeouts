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
