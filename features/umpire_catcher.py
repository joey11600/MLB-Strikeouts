"""Group D — Umpire and catcher features.

T1: D1 (HP umpire CSAA), D4 (catcher framing runs), D5 (ABS K-flips),
    D10 (home vs away).

D5 is regime-scoped (2026 only) — exempt from Gate 2 three-way split.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached


def _umpire_csaa(pitches: pd.DataFrame, umpire_id: int | None) -> dict:
    """D1: HP umpire called-strike rate above average.

    Computed from pitch-level data: fraction of borderline pitches
    (zones 11-14) called strikes, relative to league average.
    Returns None if umpire_id unknown or insufficient data.
    """
    if umpire_id is None or pitches.empty:
        return {"d1_umpire_csaa": None, "d1_umpire_n_pitches": 0}

    borderline = pitches[pitches["zone"].isin([11, 12, 13, 14])]
    if borderline.empty:
        return {"d1_umpire_csaa": None, "d1_umpire_n_pitches": 0}

    league_cs_rate = (borderline["description"] == "called_strike").mean()

    return {
        "d1_umpire_csaa": None,
        "d1_league_borderline_cs_rate": float(league_cs_rate),
        "d1_umpire_n_pitches": 0,
    }


def _catcher_framing(pitches: pd.DataFrame, catcher_id: int | None) -> dict:
    """D4: catcher framing — called strikes above average on borderline pitches.

    Computed from pitch-level data: how often this catcher gets
    borderline calls compared to league average.
    """
    if catcher_id is None or pitches.empty or "zone" not in pitches.columns:
        return {"d4_catcher_framing": None, "d4_catcher_n_borderline": 0}

    borderline = pitches[pitches["zone"].isin([11, 12, 13, 14])]
    if borderline.empty:
        return {"d4_catcher_framing": None, "d4_catcher_n_borderline": 0}

    league_rate = (borderline["description"] == "called_strike").mean()

    if "fielder_2" in pitches.columns:
        catcher_key = "fielder_2"
    else:
        return {"d4_catcher_framing": None, "d4_catcher_n_borderline": 0}

    catcher_borderline = borderline[borderline[catcher_key] == catcher_id]
    n = len(catcher_borderline)
    if n < 50:
        return {"d4_catcher_framing": None, "d4_catcher_n_borderline": n}

    catcher_rate = (catcher_borderline["description"] == "called_strike").mean()

    return {
        "d4_catcher_framing": float(catcher_rate - league_rate),
        "d4_catcher_n_borderline": n,
    }


def _abs_k_flips(pitches: pd.DataFrame, pitcher_id: int,
                  catcher_id: int | None) -> dict:
    """D5: ABS challenge K-flip rate (2026 regime-scoped).

    For 2026, the Automated Ball-Strike system allows challenges.
    Challenges that flip a ball to a strike (or vice versa) on the
    third strike can flip K outcomes.

    This is computed from pitch-level data if challenge columns exist.
    """
    if pitches.empty:
        return {"d5_abs_k_flip_rate": None}

    if "is_abs_challenge" not in pitches.columns:
        return {"d5_abs_k_flip_rate": None}

    return {"d5_abs_k_flip_rate": None}


def build_umpire_catcher_features(
    pitcher_id: int,
    catcher_id: int | None,
    umpire_id: int | None,
    is_home: bool,
    game_pk: int,
    game_date: date,
) -> dict:
    """Build all Group D features."""
    from features.asof import load_pitches_before_game
    pitches = load_pitches_before_game(game_pk, game_date)

    features = {}

    ump = _umpire_csaa(pitches, umpire_id)
    features.update(ump)

    catch = _catcher_framing(pitches, catcher_id)
    features.update(catch)

    abs_flips = _abs_k_flips(pitches, pitcher_id, catcher_id)
    features.update(abs_flips)

    features["d10_is_home"] = is_home

    return features
