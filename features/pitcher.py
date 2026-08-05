"""Group A — Pitcher skill and form features.

T1: A1 (SwStr%), A2 (CSW%), A3 (season K%), A4 (rolling K%),
    A5 (K% trend), A7 (chase rate), A8 (put-away rate),
    A13 (velocity delta), A14 (age), A15 (pitch-type whiff% x usage),
    A16 (arsenal breadth).

All rate features go through features.asof to enforce anti-leakage.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from features.asof import (
    load_pitches_before_game,
    pitcher_season_stats,
    pitcher_rolling_k_pct,
    pitcher_velocity_delta,
    pitcher_arsenal,
)

LEAGUE_K_RATE_2026 = 0.225
SHRINKAGE_BF = 70


def _shrunk_k_pct(raw_k_pct: float | None, bf: int) -> float | None:
    """A3: Bayesian shrinkage of season K% toward league mean.

    Stabilizes at ~70 BF. Formula: (bf * raw + SHRINKAGE_BF * league) / (bf + SHRINKAGE_BF)
    """
    if raw_k_pct is None:
        return None
    return (bf * raw_k_pct + SHRINKAGE_BF * LEAGUE_K_RATE_2026) / (bf + SHRINKAGE_BF)


def _k_pct_trend(pitches, pitcher_id: int) -> float | None:
    """A5: K% slope — last 5 starts vs prior 10.

    Positive = improving form. Returns the difference (recent - prior).
    """
    recent = pitcher_rolling_k_pct(pitches, pitcher_id, n_starts=5)
    prior = pitcher_rolling_k_pct(pitches, pitcher_id, n_starts=10)

    if recent["rolling_k_pct"] is None or prior["rolling_k_pct"] is None:
        return None
    if recent["n_starts_found"] < 3 or prior["n_starts_found"] < 5:
        return None

    return recent["rolling_k_pct"] - prior["rolling_k_pct"]


def _pitcher_age(pitcher_id: int, game_date: date) -> float | None:
    """A14: Pitcher age in years on game day.

    Uses the Chadwick crosswalk for birth date. Returns None if unknown.
    """
    try:
        from data.id_crosswalk import load_crosswalk
        xwalk = load_crosswalk()
        entry = xwalk.get(pitcher_id, {})
        birth_year = entry.get("birth_year", "").strip()
        birth_month = entry.get("birth_month", "").strip()
        birth_day = entry.get("birth_day", "").strip()

        if not birth_year or not birth_month or not birth_day:
            return None

        birth = date(int(birth_year), int(birth_month), int(birth_day))
        age_days = (game_date - birth).days
        return age_days / 365.25
    except Exception:
        return None


def _arsenal_weighted_whiff(pitch_mix: dict) -> float | None:
    """A15: usage-weighted whiff% across the arsenal."""
    if not pitch_mix:
        return None
    total_usage = sum(p["usage"] for p in pitch_mix.values())
    if total_usage == 0:
        return None
    weighted = sum(p["usage"] * p["whiff_pct"] for p in pitch_mix.values())
    return weighted / total_usage


def _zone_pct(pitches, pitcher_id: int) -> float | None:
    """A9: Fraction of pitches in the strike zone (Statcast zones 1-9)."""
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "zone" not in p.columns:
        return None
    valid = p["zone"].notna()
    if valid.sum() < 50:
        return None
    in_zone = p.loc[valid, "zone"].isin(range(1, 10)).sum()
    return float(in_zone / valid.sum())


def build_pitcher_features(pitcher_id: int, game_pk: int, game_date: date) -> dict:
    """Build all Group A features for a pitcher in a specific game.

    Returns a flat dict of feature name -> value.
    """
    pitches = load_pitches_before_game(game_pk, game_date)

    features = {}

    if pitches.empty:
        return _empty_pitcher_features()

    season = pitcher_season_stats(pitches, pitcher_id)
    features["a1_swstr_pct"] = season["swstr_pct"]
    features["a2_csw_pct"] = season["csw_pct"]
    features["a3_season_k_pct_raw"] = season["k_pct"]
    features["a3_season_k_pct_shrunk"] = _shrunk_k_pct(
        season["k_pct"], season["total_batters_faced"]
    )
    features["a7_chase_rate"] = season["chase_rate"]
    features["a8_put_away_rate"] = season["put_away_rate"]
    features["a_total_pitches"] = season["total_pitches"]
    features["a_total_bf"] = season["total_batters_faced"]

    for n in [3, 5, 10]:
        rolling = pitcher_rolling_k_pct(pitches, pitcher_id, n_starts=n)
        features[f"a4_rolling_k_pct_{n}"] = rolling["rolling_k_pct"]
    features["a4_n_starts_found"] = pitcher_rolling_k_pct(
        pitches, pitcher_id, 10
    )["n_starts_found"]

    features["a5_k_pct_trend"] = _k_pct_trend(pitches, pitcher_id)

    velo = pitcher_velocity_delta(pitches, pitcher_id)
    features["a13_velo_delta"] = velo["velo_delta"]

    features["a14_age"] = _pitcher_age(pitcher_id, game_date)

    arsenal = pitcher_arsenal(pitches, pitcher_id)
    features["a15_arsenal_whiff"] = _arsenal_weighted_whiff(arsenal["pitch_mix"])
    features["a16_arsenal_breadth"] = arsenal["arsenal_breadth"]
    features["a_pitch_mix"] = arsenal["pitch_mix"]

    features["a9_zone_pct"] = _zone_pct(pitches, pitcher_id)

    return features


def _empty_pitcher_features() -> dict:
    return {
        "a1_swstr_pct": None,
        "a2_csw_pct": None,
        "a3_season_k_pct_raw": None,
        "a3_season_k_pct_shrunk": None,
        "a4_rolling_k_pct_3": None,
        "a4_rolling_k_pct_5": None,
        "a4_rolling_k_pct_10": None,
        "a4_n_starts_found": 0,
        "a5_k_pct_trend": None,
        "a7_chase_rate": None,
        "a8_put_away_rate": None,
        "a13_velo_delta": None,
        "a14_age": None,
        "a15_arsenal_whiff": None,
        "a16_arsenal_breadth": None,
        "a_total_pitches": 0,
        "a_total_bf": 0,
        "a_pitch_mix": {},
        "a9_zone_pct": None,
    }
