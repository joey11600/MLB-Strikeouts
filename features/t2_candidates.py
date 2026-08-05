"""T2 candidate features — Phase 6.

Each feature is a standalone function returning a flat dict. Features
stay here until they pass the 5-gate gauntlet, then get promoted into
their group module (pitcher.py, workload.py, etc.).

All rate features use load_pitches_before_game() for anti-leakage.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from features.asof import load_pitches_before_game


# ── Group A: Pitcher skill ─────────────────────────────────────────

def a9_zone_pct(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """A9: In-zone rate. Zones 1-9 are the strike zone in Statcast."""
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "zone" not in p.columns:
        return {"a9_zone_pct": None}

    valid = p["zone"].notna()
    if valid.sum() < 50:
        return {"a9_zone_pct": None}

    in_zone = p.loc[valid, "zone"].isin(range(1, 10)).sum()
    return {"a9_zone_pct": float(in_zone / valid.sum())}


def a10_first_pitch_strike(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """A10: First-pitch strike %. Filter to count 0-0 pitches."""
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "strikes" not in p.columns or "balls" not in p.columns:
        return {"a10_fps_pct": None}

    first_pitches = p[(p["strikes"] == 0) & (p["balls"] == 0)]
    if len(first_pitches) < 20:
        return {"a10_fps_pct": None}

    strikes = first_pitches["description"].isin([
        "called_strike", "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "foul_bunt", "hit_into_play",
    ]).sum()
    return {"a10_fps_pct": float(strikes / len(first_pitches))}


def a18_spin_delta(pitches: pd.DataFrame, pitcher_id: int,
                   baseline_days: int = 30) -> dict:
    """A18: Spin rate delta vs own 30-day baseline.

    Post-2021 crackdown made this heterogeneous. Fit on post-June-2021.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "release_spin_rate" not in p.columns:
        return {"a18_spin_delta": None, "a18_spin_recent": None}

    spin = p[p["release_spin_rate"].notna()]
    if len(spin) < 50 or "game_date" not in spin.columns:
        return {"a18_spin_delta": None, "a18_spin_recent": None}

    latest = spin["game_date"].max()
    cutoff = latest - pd.Timedelta(days=baseline_days)
    recent = spin[spin["game_date"] > cutoff]
    baseline = spin[spin["game_date"] <= cutoff]

    if recent.empty or baseline.empty:
        return {"a18_spin_delta": None, "a18_spin_recent": None}

    delta = float(recent["release_spin_rate"].mean()
                  - baseline["release_spin_rate"].mean())
    return {
        "a18_spin_delta": delta,
        "a18_spin_recent": float(recent["release_spin_rate"].mean()),
    }


def a20_extension(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """A20: Release extension (distance toward plate at release).

    Longer extension = higher perceived velocity = more K.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "release_extension" not in p.columns:
        return {"a20_extension": None, "a20_ext_delta": None}

    ext = p[p["release_extension"].notna()]
    if len(ext) < 50:
        return {"a20_extension": None, "a20_ext_delta": None}

    mean_ext = float(ext["release_extension"].mean())

    if "game_date" in ext.columns:
        latest = ext["game_date"].max()
        cutoff = latest - pd.Timedelta(days=30)
        recent = ext[ext["game_date"] > cutoff]
        baseline = ext[ext["game_date"] <= cutoff]
        if not recent.empty and not baseline.empty:
            delta = float(recent["release_extension"].mean()
                          - baseline["release_extension"].mean())
        else:
            delta = None
    else:
        delta = None

    return {"a20_extension": mean_ext, "a20_ext_delta": delta}


# ── Group B: Opponent lineup ───────────────────────────────────────

def b12_lineup_recent_k(pitches: pd.DataFrame,
                        batter_ids: list[int],
                        n_games: int = 15) -> dict:
    """B12: Lineup's recent K% over last N games.

    Form vs talent. Weight below season rate.
    """
    if not batter_ids:
        return {"b12_lineup_recent_k_pct": None}

    recent_k_pcts = []
    for batter_id in batter_ids[:9]:
        b = pitches[pitches["batter"] == batter_id]
        completed = b[b["events"].notna()]
        if completed.empty or "game_pk" not in completed.columns:
            continue

        recent_games = sorted(completed["game_pk"].unique())[-n_games:]
        recent = completed[completed["game_pk"].isin(recent_games)]
        if len(recent) < 10:
            continue

        ks = recent["events"].isin([
            "strikeout", "strikeout_double_play",
        ]).sum()
        recent_k_pcts.append(float(ks / len(recent)))

    if not recent_k_pcts:
        return {"b12_lineup_recent_k_pct": None}

    return {"b12_lineup_recent_k_pct": float(np.mean(recent_k_pcts))}


def b14_rookies(pitches: pd.DataFrame,
                batter_ids: list[int],
                rookie_bf_threshold: int = 100) -> dict:
    """B14: Rookie / call-up hitters in lineup.

    Proxy: batter with fewer than threshold career PA in Statcast data.
    Rookies tend to chase more and strike out at higher rates initially.
    """
    if not batter_ids:
        return {"b14_n_rookies": 0, "b14_rookie_share": 0.0}

    n_rookies = 0
    n_counted = 0
    for batter_id in batter_ids[:9]:
        b = pitches[pitches["batter"] == batter_id]
        completed = b[b["events"].notna()]
        bf = len(completed)
        n_counted += 1
        if bf < rookie_bf_threshold:
            n_rookies += 1

    share = n_rookies / n_counted if n_counted > 0 else 0.0
    return {"b14_n_rookies": n_rookies, "b14_rookie_share": float(share)}


# ── Group C: Workload and leash ────────────────────────────────────

def c5_tto_penalty(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """C5: TTO penalty base — raw K% decay per TTO.

    Compute this pitcher's personal TTO decay from their Statcast data.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    completed = p[p["events"].notna()].copy()
    if completed.empty or len(completed) < 50:
        return {"c5_tto1_k_pct": None, "c5_tto3_k_pct": None,
                "c5_tto_decay": None}

    completed = completed.sort_values(["game_pk", "at_bat_number"])
    completed["bf_seq"] = completed.groupby("game_pk").cumcount() + 1

    tto1 = completed[completed["bf_seq"] <= 9]
    tto3 = completed[(completed["bf_seq"] >= 19) & (completed["bf_seq"] <= 27)]

    if tto1.empty or tto3.empty:
        return {"c5_tto1_k_pct": None, "c5_tto3_k_pct": None,
                "c5_tto_decay": None}

    k1 = tto1["events"].isin(["strikeout", "strikeout_double_play"]).mean()
    k3 = tto3["events"].isin(["strikeout", "strikeout_double_play"]).mean()

    return {
        "c5_tto1_k_pct": float(k1),
        "c5_tto3_k_pct": float(k3),
        "c5_tto_decay": float(k3 - k1),
    }


def c7_prior_pitch_count(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """C7: Pitch count in the pitcher's most recent start.

    High prior pitch count (110+) may indicate fatigue carryover.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "game_pk" not in p.columns:
        return {"c7_prior_pitches": None}

    completed = p[p["events"].notna()]
    game_bfs = completed.groupby("game_pk").size()
    starter_games = game_bfs[game_bfs >= 9]
    if starter_games.empty:
        return {"c7_prior_pitches": None}

    last_game_pk = starter_games.sort_index().index[-1]
    last_game_pitches = len(p[p["game_pk"] == last_game_pk])

    return {"c7_prior_pitches": int(last_game_pitches)}


def c8_days_rest(pitches: pd.DataFrame, pitcher_id: int,
                 game_date: date) -> dict:
    """C8: Days of rest since last appearance."""
    p = pitches[pitches["pitcher"] == pitcher_id]
    if p.empty or "game_date" not in p.columns:
        return {"c8_days_rest": None}

    dates = pd.to_datetime(p["game_date"]).dt.date.unique()
    dates = sorted(dates)
    if not dates:
        return {"c8_days_rest": None}

    last_appearance = dates[-1]
    rest = (game_date - last_appearance).days
    return {"c8_days_rest": int(rest)}


def c9_season_innings(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """C9: Season cumulative innings (estimated from BF).

    Workload fatigue proxy. ~3 BF per inning on average.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    completed = p[p["events"].notna()]
    if completed.empty:
        return {"c9_season_bf": 0, "c9_season_ip_est": 0.0}

    bf = len(completed)
    ip_est = bf / 3.0

    return {"c9_season_bf": int(bf), "c9_season_ip_est": float(ip_est)}


def c13_doubleheader(game_number: int | None) -> dict:
    """C13: Doubleheader context.

    game_number from MLB Stats API: 1 = single/game1, 2 = game2.
    Game 2 starters typically get shorter leash (27th-man rules).
    """
    is_dh = game_number is not None and game_number > 1
    return {
        "c13_is_doubleheader": is_dh,
        "c13_game_number": game_number or 1,
    }


def c14_blowout_risk(run_line: float | None,
                     game_total: float | None) -> dict:
    """C14: Pre-game blowout risk from market lines.

    Large run line + high total = blowout risk = shorter start.
    """
    if run_line is None or game_total is None:
        return {"c14_blowout_risk": None}

    spread_factor = abs(run_line) / 3.0
    total_factor = max(0, (game_total - 8.0)) / 4.0
    risk = min(1.0, (spread_factor + total_factor) / 2.0)

    return {"c14_blowout_risk": float(risk)}


def c16_mlb_debut(pitches: pd.DataFrame, pitcher_id: int) -> dict:
    """C16: Is this the pitcher's MLB debut start?

    If no prior appearances in Statcast data this season, flag as debut.
    Short leash expected. Stage A feature.
    """
    p = pitches[pitches["pitcher"] == pitcher_id]
    completed = p[p["events"].notna()]
    is_debut = completed.empty or len(completed) < 3
    return {"c16_is_debut": is_debut}


# ── Group D: Umpire ────────────────────────────────────────────────

def d6_umpire_age(umpire_birth_year: int | None,
                  game_year: int = 2026) -> dict:
    """D6: Umpire age / experience.

    Older umps have higher error rates. Runs backwards.
    """
    if umpire_birth_year is None:
        return {"d6_umpire_age": None}

    age = game_year - umpire_birth_year
    return {"d6_umpire_age": float(age)}


# ── Group E: Park and weather ──────────────────────────────────────

def e8_humidity_grip(humidity_pct: float | None) -> dict:
    """E8: Humidity as grip factor.

    Dry air (<30% RH) = worst grip condition for pitchers.
    High humidity (>70%) may also affect grip but less studied.
    Deviation from ideal range (40-60%) penalizes K expectation.
    """
    if humidity_pct is None:
        return {"e8_humidity_grip": None, "e8_grip_penalty": 0.0}

    ideal_center = 50.0
    deviation = abs(humidity_pct - ideal_center) / 50.0
    penalty = deviation * -0.005 if humidity_pct < 35 else 0.0

    return {
        "e8_humidity_grip": float(humidity_pct),
        "e8_grip_penalty": float(penalty),
    }


# ── Group F: Schedule and travel ───────────────────────────────────

TEAM_TIMEZONES = {
    "NYY": -5, "NYM": -5, "BOS": -5, "BAL": -5, "TB": -5,
    "PHI": -5, "PIT": -5, "WSH": -5, "MIA": -5, "ATL": -5,
    "CLE": -5, "DET": -5, "TOR": -5, "CIN": -5,
    "MIL": -6, "CHC": -6, "CWS": -6, "MIN": -6, "STL": -6,
    "KC": -6, "HOU": -6, "TEX": -6,
    "COL": -7, "ARI": -7,
    "LAD": -8, "LAA": -8, "SD": -8, "SF": -8, "SEA": -8, "OAK": -8,
}


def f1_eastward_travel(pitcher_team: str | None,
                       prev_venue_tz: int | None,
                       curr_venue_tz: int | None) -> dict:
    """F1: Eastward travel fatigue.

    Almost entirely eastward jet lag affects MLB performance.
    46,535 games show eastward travel can erase home-field advantage.
    Compute timezone difference (positive = eastward).
    """
    if prev_venue_tz is None or curr_venue_tz is None:
        return {"f1_eastward_tz": 0, "f1_travel_fatigue": 0.0}

    tz_diff = curr_venue_tz - prev_venue_tz
    eastward = max(0, tz_diff)

    fatigue = eastward * 0.15 if eastward > 0 else 0.0

    return {
        "f1_eastward_tz": int(eastward),
        "f1_travel_fatigue": float(fatigue),
    }


def f3_days_in_timezone(days_at_venue: int | None) -> dict:
    """F3: Days since arriving in current time zone.

    Body clock shifts ~1 hr/day. Mechanism behind F1.
    """
    if days_at_venue is None:
        return {"f3_days_in_tz": None, "f3_adjusted": True}

    adjusted = days_at_venue >= 3
    return {"f3_days_in_tz": int(days_at_venue), "f3_adjusted": adjusted}


def f4_consecutive_road(n_road_games: int | None) -> dict:
    """F4: Consecutive road games.

    Long road trips may affect pitcher performance through accumulated
    travel fatigue.
    """
    if n_road_games is None:
        return {"f4_consec_road": 0}
    return {"f4_consec_road": int(n_road_games)}


def f7_chase_rate_decay(game_date: date,
                        pitches: pd.DataFrame | None = None) -> dict:
    """F7: Late-season chase-rate decay.

    24/30 teams showed monotonic chase-rate decline April->September.
    Use month as a proxy; compute from Statcast if available.
    """
    month = game_date.month
    month_factors = {
        3: 1.02, 4: 1.02, 5: 1.01, 6: 1.00,
        7: 0.99, 8: 0.98, 9: 0.97, 10: 0.96,
    }
    factor = month_factors.get(month, 1.00)

    if pitches is not None and not pitches.empty and "zone" in pitches.columns:
        outside = pitches["zone"].isin([11, 12, 13, 14])
        swings_outside = (outside & pitches["description"].isin([
            "swinging_strike", "swinging_strike_blocked",
            "foul", "foul_tip", "hit_into_play",
        ])).sum()
        pitches_outside = outside.sum()
        if pitches_outside > 1000:
            league_chase = float(swings_outside / pitches_outside)
        else:
            league_chase = None
    else:
        league_chase = None

    return {
        "f7_month_factor": float(factor),
        "f7_league_chase_rate": league_chase,
    }


# ── Group H: Market ───────────────────────────────────────────────

def h4_ladder_shape(model_k_dist: list[float] | None,
                    dk_milestone_odds: dict | None) -> dict:
    """H4: Alt-line ladder shape divergence.

    Compare the model's implied P(K >= milestone) against DK's
    alt-line implied probabilities. Large divergence in tails = edge.
    """
    if model_k_dist is None or dk_milestone_odds is None:
        return {"h4_shape_divergence": None, "h4_tail_edge": None}

    k_dist = np.array(model_k_dist)

    divergences = []
    for milestone_str, odds in dk_milestone_odds.items():
        try:
            milestone = int(str(milestone_str).rstrip("+"))
        except (ValueError, TypeError):
            continue

        model_prob = float(np.sum(k_dist[milestone:])) if milestone < len(k_dist) else 0.0

        if odds < 0:
            dk_implied = abs(odds) / (abs(odds) + 100)
        else:
            dk_implied = 100 / (odds + 100)

        divergences.append(model_prob - dk_implied)

    if not divergences:
        return {"h4_shape_divergence": None, "h4_tail_edge": None}

    return {
        "h4_shape_divergence": float(np.std(divergences)),
        "h4_tail_edge": float(max(divergences, key=abs)),
    }


# ── Master builder ─────────────────────────────────────────────────

def build_t2_features(
    pitcher_id: int,
    game_pk: int,
    game_date: date,
    batter_ids: list[int] | None = None,
    game_number: int | None = None,
    run_line: float | None = None,
    game_total: float | None = None,
    umpire_birth_year: int | None = None,
    humidity_pct: float | None = None,
    pitcher_team: str | None = None,
    prev_venue_tz: int | None = None,
    curr_venue_tz: int | None = None,
    days_at_venue: int | None = None,
    n_road_games: int | None = None,
    model_k_dist: list[float] | None = None,
    dk_milestone_odds: dict | None = None,
) -> dict:
    """Build all T2 candidate features for one pitcher-game.

    Returns a flat dict of feature_name -> value.
    """
    pitches = load_pitches_before_game(game_pk, game_date)
    features = {}

    features.update(a9_zone_pct(pitches, pitcher_id))
    features.update(a10_first_pitch_strike(pitches, pitcher_id))
    features.update(a18_spin_delta(pitches, pitcher_id))
    features.update(a20_extension(pitches, pitcher_id))

    features.update(b12_lineup_recent_k(pitches, batter_ids or []))
    features.update(b14_rookies(pitches, batter_ids or []))

    features.update(c5_tto_penalty(pitches, pitcher_id))
    features.update(c7_prior_pitch_count(pitches, pitcher_id))
    features.update(c8_days_rest(pitches, pitcher_id, game_date))
    features.update(c9_season_innings(pitches, pitcher_id))
    features.update(c13_doubleheader(game_number))
    features.update(c14_blowout_risk(run_line, game_total))
    features.update(c16_mlb_debut(pitches, pitcher_id))

    features.update(d6_umpire_age(umpire_birth_year, game_date.year))

    features.update(e8_humidity_grip(humidity_pct))

    features.update(f1_eastward_travel(pitcher_team, prev_venue_tz, curr_venue_tz))
    features.update(f3_days_in_timezone(days_at_venue))
    features.update(f4_consecutive_road(n_road_games))
    features.update(f7_chase_rate_decay(game_date, pitches))

    features.update(h4_ladder_shape(model_k_dist, dk_milestone_odds))

    return features


# ── Feature registry for gauntlet ──────────────────────────────────

T2_REGISTRY = {
    "a9_zone_pct": {
        "group": "A", "tier": "T2",
        "description": "In-zone rate, interaction with umpire zone",
        "gate1_known_before_pitch": True,
        "expected_sign": None,
        "expected_magnitude": None,
        "collinear_with": [],
    },
    "a10_fps_pct": {
        "group": "A", "tier": "T2",
        "description": "First-pitch strike percentage",
        "gate1_known_before_pitch": True,
        "expected_sign": "positive",
        "expected_magnitude": "weak",
        "collinear_with": ["a2_csw_pct"],
    },
    "a18_spin_delta": {
        "group": "A", "tier": "T2",
        "description": "Spin rate change vs 30-day baseline",
        "gate1_known_before_pitch": True,
        "expected_sign": None,
        "expected_magnitude": "weak",
        "collinear_with": ["a13_velo_delta"],
    },
    "a20_extension": {
        "group": "A", "tier": "T2",
        "description": "Release extension toward plate",
        "gate1_known_before_pitch": True,
        "expected_sign": "positive",
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "b12_lineup_recent_k_pct": {
        "group": "B", "tier": "T2",
        "description": "Lineup recent K% (form vs talent)",
        "gate1_known_before_pitch": True,
        "expected_sign": "positive",
        "expected_magnitude": "weak",
        "collinear_with": ["b1_lineup_weighted_k_pct"],
    },
    "b14_n_rookies": {
        "group": "B", "tier": "T2",
        "description": "Rookie/call-up hitters in lineup",
        "gate1_known_before_pitch": True,
        "expected_sign": "positive",
        "expected_magnitude": "weak",
        "collinear_with": ["b13_n_bench_bats"],
    },
    "c5_tto_decay": {
        "group": "C", "tier": "T2",
        "description": "Pitcher-specific TTO K% decay",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "moderate",
        "collinear_with": ["c4_tto_penalty_est"],
    },
    "c7_prior_pitches": {
        "group": "C", "tier": "T2",
        "description": "Prior start pitch count",
        "gate1_known_before_pitch": True,
        "expected_sign": None,
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "c8_days_rest": {
        "group": "C", "tier": "T2",
        "description": "Days of rest between starts",
        "gate1_known_before_pitch": True,
        "expected_sign": None,
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "c9_season_bf": {
        "group": "C", "tier": "T2",
        "description": "Season cumulative batters faced (fatigue proxy)",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": ["a_total_bf"],
    },
    "c13_is_doubleheader": {
        "group": "C", "tier": "T2",
        "description": "Game is part of doubleheader",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "c14_blowout_risk": {
        "group": "C", "tier": "T2",
        "description": "Pre-game blowout risk from market lines",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "c16_is_debut": {
        "group": "C", "tier": "T2",
        "description": "MLB debut start (short leash)",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "moderate",
        "collinear_with": [],
    },
    "d6_umpire_age": {
        "group": "D", "tier": "T2",
        "description": "Umpire age (older = higher error rate)",
        "gate1_known_before_pitch": True,
        "expected_sign": None,
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "e8_grip_penalty": {
        "group": "E", "tier": "T2",
        "description": "Humidity grip penalty (dry air)",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": ["e17_humidity_pct"],
    },
    "f1_eastward_tz": {
        "group": "F", "tier": "T2",
        "description": "Eastward timezone crossings",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": ["f3_days_in_tz"],
    },
    "f3_days_in_tz": {
        "group": "F", "tier": "T2",
        "description": "Days since arriving in current timezone",
        "gate1_known_before_pitch": True,
        "expected_sign": "positive",
        "expected_magnitude": "weak",
        "collinear_with": ["f1_eastward_tz"],
    },
    "f4_consec_road": {
        "group": "F", "tier": "T2",
        "description": "Consecutive road games",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "f7_month_factor": {
        "group": "F", "tier": "T2",
        "description": "Late-season chase-rate decay multiplier",
        "gate1_known_before_pitch": True,
        "expected_sign": "negative",
        "expected_magnitude": "weak",
        "collinear_with": [],
    },
    "h4_shape_divergence": {
        "group": "H", "tier": "T2",
        "description": "Alt-line ladder shape divergence vs model",
        "gate1_known_before_pitch": True,
        "expected_sign": "positive",
        "expected_magnitude": "moderate",
        "collinear_with": [],
    },
}
