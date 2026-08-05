"""Group C — Workload, leash, and expected batters faced.

T1: C1 (BF distribution), C2 (manager leash), C4 (TTO x arsenal),
    C10 (first start off IL), C11 (announced pitch limit),
    C12 (bullpen usage last 2-3 days).

This group carries most of the reducible variance.
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached

MANUAL_PITCH_LIMITS = Path(__file__).parent.parent / "data" / "manual_pitch_limits.csv"


def _pitcher_bf_distribution(pitches: pd.DataFrame, pitcher_id: int,
                              n_starts: int = 10) -> dict:
    """C1: pitcher's own BF distribution over last N starts.

    Returns the empirical distribution as {bf_value: probability}.
    """
    completed = pitches[pitches["events"].notna()]
    p = completed[completed["pitcher"] == pitcher_id]
    if p.empty or "game_pk" not in p.columns:
        return {"bf_dist": {}, "bf_mean": None, "bf_std": None, "bf_n_starts": 0}

    game_bfs = p.groupby("game_pk").size()
    game_bfs = game_bfs[game_bfs >= 9]

    if game_bfs.empty:
        return {"bf_dist": {}, "bf_mean": None, "bf_std": None, "bf_n_starts": 0}

    recent = game_bfs.sort_index().tail(n_starts)
    bf_values = recent.values

    bf_dist = {}
    for bf in bf_values:
        bf_dist[int(bf)] = bf_dist.get(int(bf), 0) + 1
    total = sum(bf_dist.values())
    bf_dist = {k: v / total for k, v in bf_dist.items()}

    return {
        "bf_dist": bf_dist,
        "bf_mean": float(np.mean(bf_values)),
        "bf_std": float(np.std(bf_values, ddof=1)) if len(bf_values) > 1 else 0.0,
        "bf_n_starts": len(bf_values),
    }


def _manager_leash(pitches: pd.DataFrame, team_id: int | None) -> dict:
    """C2: team-level starter pitch count tendency.

    Uses all starters on the team (by infield_align or other team proxy)
    to compute the manager's leash. Falls back to league average.
    """
    if pitches.empty:
        return {"c2_team_pitch_mean": 88.0, "c2_team_pitch_std": 14.0}

    completed = pitches[pitches["events"].notna()]
    game_pitcher_pitches = pitches.groupby(["game_pk", "pitcher"]).size().reset_index(name="n_pitches")
    game_pitcher_bf = completed.groupby(["game_pk", "pitcher"]).size().reset_index(name="bf")

    starters = game_pitcher_bf[game_pitcher_bf["bf"] >= 9]
    starters = starters.merge(game_pitcher_pitches, on=["game_pk", "pitcher"])

    if starters.empty:
        return {"c2_team_pitch_mean": 88.0, "c2_team_pitch_std": 14.0}

    return {
        "c2_team_pitch_mean": float(starters["n_pitches"].mean()),
        "c2_team_pitch_std": float(starters["n_pitches"].std(ddof=1)),
    }


def _tto_arsenal_interaction(pitch_mix: dict) -> dict:
    """C4: TTO × arsenal breadth interaction.

    FB-heavy pitchers (>60% FF/SI) lose more K% in later TTOs.
    Returns a feature capturing the expected TTO penalty.
    """
    if not pitch_mix:
        return {"c4_fb_heavy": False, "c4_fb_usage": 0.0, "c4_tto_penalty_est": 0.0}

    fb_types = {"FF", "SI", "FC"}
    fb_usage = sum(p["usage"] for pt, p in pitch_mix.items() if pt in fb_types)

    breadth = sum(1 for p in pitch_mix.values() if p["usage"] >= 0.10)

    fb_heavy = fb_usage > 0.60
    if fb_heavy:
        tto_penalty = -0.04
    elif breadth >= 4:
        tto_penalty = -0.02
    else:
        tto_penalty = -0.03

    return {
        "c4_fb_heavy": fb_heavy,
        "c4_fb_usage": float(fb_usage),
        "c4_arsenal_breadth": breadth,
        "c4_tto_penalty_est": tto_penalty,
    }


def _il_return(pitcher_id: int, game_date: date, pitches: pd.DataFrame) -> bool:
    """C10: is this the pitcher's first start off the IL?

    Heuristic: if the gap between this game and the pitcher's last
    appearance is >18 days, flag as IL return.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "game_date" not in p.columns:
        return False

    dates = pd.to_datetime(p["game_date"]).dt.date.unique()
    dates = sorted(dates)
    if not dates:
        return False

    last_appearance = dates[-1]
    gap = (game_date - last_appearance).days
    return gap > 18


def _announced_pitch_limit(pitcher_id: int, game_date: date) -> int | None:
    """C11: check manual_pitch_limits.csv for announced limits."""
    if not MANUAL_PITCH_LIMITS.exists():
        return None

    try:
        with open(MANUAL_PITCH_LIMITS, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (str(row.get("pitcher_id", "")).strip() == str(pitcher_id)
                        and row.get("game_date", "").strip() == str(game_date)):
                    limit = row.get("pitch_limit", "").strip()
                    if limit:
                        return int(limit)
    except Exception:
        pass
    return None


def _bullpen_fatigue(pitches: pd.DataFrame, team_pitchers: list[int] | None,
                      game_date: date) -> dict:
    """C12: bullpen usage in last 2-3 days.

    Heavy recent bullpen usage = longer leash for the starter.
    """
    if pitches.empty or "game_date" not in pitches.columns:
        return {"c12_bp_pitches_2d": 0, "c12_bp_pitches_3d": 0, "c12_bp_heavy": False}

    cutoff_2d = pd.Timestamp(game_date - timedelta(days=2))
    cutoff_3d = pd.Timestamp(game_date - timedelta(days=3))
    game_ts = pd.Timestamp(game_date)

    recent = pitches[
        (pitches["game_date"] >= cutoff_3d) &
        (pitches["game_date"] < game_ts)
    ]

    if recent.empty:
        return {"c12_bp_pitches_2d": 0, "c12_bp_pitches_3d": 0, "c12_bp_heavy": False}

    completed = recent[recent["events"].notna()]
    game_pitcher_bf = completed.groupby(["game_pk", "pitcher"]).size().reset_index(name="bf")
    relievers = game_pitcher_bf[game_pitcher_bf["bf"] < 9]

    bp_pitches_3d = len(recent[recent["pitcher"].isin(relievers["pitcher"])])
    recent_2d = recent[recent["game_date"] >= cutoff_2d]
    bp_pitches_2d = len(recent_2d[recent_2d["pitcher"].isin(relievers["pitcher"])])

    bp_heavy = bp_pitches_2d > 120

    return {
        "c12_bp_pitches_2d": int(bp_pitches_2d),
        "c12_bp_pitches_3d": int(bp_pitches_3d),
        "c12_bp_heavy": bp_heavy,
    }


def build_workload_features(
    pitcher_id: int,
    game_pk: int,
    game_date: date,
    pitch_mix: dict | None = None,
    team_pitchers: list[int] | None = None,
) -> dict:
    """Build all Group C features."""
    from features.asof import load_pitches_before_game
    pitches = load_pitches_before_game(game_pk, game_date)

    features = {}

    bf_info = _pitcher_bf_distribution(pitches, pitcher_id)
    features["c1_bf_mean"] = bf_info["bf_mean"]
    features["c1_bf_std"] = bf_info["bf_std"]
    features["c1_bf_n_starts"] = bf_info["bf_n_starts"]
    features["c1_bf_dist"] = bf_info["bf_dist"]

    leash = _manager_leash(pitches, None)
    features.update(leash)

    tto = _tto_arsenal_interaction(pitch_mix or {})
    features.update(tto)

    features["c10_il_return"] = _il_return(pitcher_id, game_date, pitches)

    features["c11_pitch_limit"] = _announced_pitch_limit(pitcher_id, game_date)

    bp = _bullpen_fatigue(pitches, team_pitchers, game_date)
    features.update(bp)

    return features
