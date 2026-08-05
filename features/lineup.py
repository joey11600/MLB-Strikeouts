"""Group B — Opponent lineup features.

T1: B1 (lineup-weighted K%), B2 (matchup K%), B3 (batter K% by hand),
    B4 (pitcher K% vs LHB/RHB), B5 (platoon-split regression),
    B6 (batter whiff% by pitch type), B7 (batter chase rate),
    B10 (lineup handedness), B13 (bench bats), B15 (contact%),
    B16 (batting-order expected-PA weights).

Requires the posted lineup (~3h pre-game). The model runs twice:
early pass on projected lineups, late pass on confirmed.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from features.asof import (
    load_pitches_before_game,
    batter_k_rate,
    batter_k_rate_by_hand,
)
from models.matchup import matchup_k_rate

PA_WEIGHTS_9 = [4.65, 4.53, 4.41, 4.29, 4.17, 4.05, 3.93, 3.82, 3.71]
PA_WEIGHT_TOTAL = sum(PA_WEIGHTS_9)

PLATOON_HARMONIC_PA = {"R": 1670, "L": 570}


def _batter_whiff_by_pitch(pitches, batter_id: int) -> dict:
    """B6: batter's whiff% broken out by pitch type."""
    b = pitches[pitches["batter"] == batter_id]
    if b.empty or "pitch_type" not in b.columns:
        return {}

    result = {}
    for pt in b["pitch_type"].dropna().unique():
        pt_pitches = b[b["pitch_type"] == pt]
        swings = pt_pitches["description"].isin([
            "swinging_strike", "swinging_strike_blocked",
            "foul", "foul_tip", "foul_bunt", "hit_into_play",
        ])
        whiffs = pt_pitches["description"].isin([
            "swinging_strike", "swinging_strike_blocked", "foul_tip",
        ])
        n_swings = swings.sum()
        if n_swings >= 5:
            result[pt] = float(whiffs.sum() / n_swings)
    return result


def _batter_chase_rate(pitches, batter_id: int) -> float | None:
    """B7: batter-side chase rate (O-Swing%)."""
    b = pitches[pitches["batter"] == batter_id]
    if b.empty or "zone" not in b.columns:
        return None

    outside = b["zone"].isin([11, 12, 13, 14])
    swings_outside = (outside & b["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "hit_into_play",
    ])).sum()
    pitches_outside = outside.sum()
    if pitches_outside < 10:
        return None
    return float(swings_outside / pitches_outside)


def _batter_contact_pct(pitches, batter_id: int) -> float | None:
    """B15: batter-side contact%."""
    b = pitches[pitches["batter"] == batter_id]
    if b.empty:
        return None

    swings = b["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "foul_bunt", "hit_into_play",
    ]).sum()
    whiffs = b["description"].isin([
        "swinging_strike", "swinging_strike_blocked", "foul_tip",
    ]).sum()
    if swings < 10:
        return None
    return float((swings - whiffs) / swings)


def _pitcher_k_by_hand(pitches, pitcher_id: int) -> dict:
    """B4: pitcher K% vs LHB and RHB."""
    p = pitches[pitches["pitcher"] == pitcher_id]
    completed = p[p["events"].notna()]
    if completed.empty:
        return {"pitcher_k_vs_l": None, "pitcher_k_vs_r": None}

    result = {}
    for hand in ["L", "R"]:
        hand_abs = completed[completed["stand"] == hand]
        if len(hand_abs) < 10:
            result[f"pitcher_k_vs_{hand.lower()}"] = None
        else:
            ks = hand_abs["events"].isin(["strikeout", "strikeout_double_play"]).sum()
            result[f"pitcher_k_vs_{hand.lower()}"] = float(ks / len(hand_abs))
    return result


def _platoon_regressed_k(raw_k: float | None, bf: int, pitcher_hand: str,
                          league_split: float) -> float:
    """B5: regress platoon-split K% toward league prior.

    RHP need ~1670 harmonic PA; LHP ~570. Use league_split as prior.
    """
    if raw_k is None or bf < 5:
        return league_split
    n_star = PLATOON_HARMONIC_PA.get(pitcher_hand, 1670)
    weight = bf / (bf + n_star)
    return weight * raw_k + (1 - weight) * league_split


def _is_bench_bat(batter_stats: dict) -> bool:
    """B13: heuristic for bench/backup players.

    A batter with fewer PA than ~60% of league average for the season
    window is likely a bench bat or recent call-up.
    """
    bf = batter_stats.get("batter_bf", 0)
    return bf < 30


def build_lineup_features(
    pitcher_id: int,
    pitcher_hand: str,
    pitcher_k_pct: float,
    lineup: list[dict],
    game_pk: int,
    game_date: date,
    lineup_source: str = "confirmed",
) -> dict:
    """Build all Group B features.

    Parameters
    ----------
    pitcher_id : int
        Pitcher's MLBAM ID.
    pitcher_hand : str
        "L" or "R".
    pitcher_k_pct : float
        Pitcher's as-of season K% (from Group A).
    lineup : list of dict
        Ordered batting lineup. Each dict must have at minimum:
        {"batter_id": int, "bats": str ("L"/"R"/"S")}
    game_pk : int
    game_date : date
    lineup_source : str
        "projected" or "confirmed".
    """
    pitches = load_pitches_before_game(game_pk, game_date)
    features = {"b_lineup_source": lineup_source}

    if pitches.empty or not lineup:
        return {**features, **_empty_lineup_features()}

    pitcher_splits = _pitcher_k_by_hand(pitches, pitcher_id)
    features["b4_pitcher_k_vs_l"] = pitcher_splits["pitcher_k_vs_l"]
    features["b4_pitcher_k_vs_r"] = pitcher_splits["pitcher_k_vs_r"]

    batter_k_pcts = []
    matchup_k_rates = []
    batter_chase_rates = []
    batter_contact_pcts = []
    n_lhb = 0
    n_bench = 0
    batter_whiff_profiles = []
    per_batter_data = []

    for slot_idx, batter_info in enumerate(lineup[:9]):
        batter_id = batter_info["batter_id"]
        bats = batter_info.get("bats", "R")

        effective_hand = bats
        if bats == "S":
            effective_hand = "R" if pitcher_hand == "L" else "L"

        if effective_hand == "L":
            n_lhb += 1

        stats = batter_k_rate(pitches, batter_id)
        hand_stats = batter_k_rate_by_hand(pitches, batter_id)

        batter_kpct = stats.get("batter_k_pct")
        hand_key = f"batter_k_pct_vs_{pitcher_hand.lower()}"
        batter_kpct_vs_hand = hand_stats.get(hand_key)

        best_k = batter_kpct_vs_hand if batter_kpct_vs_hand is not None else batter_kpct
        if best_k is None:
            best_k = 0.225

        batter_k_pcts.append(best_k)

        mk = matchup_k_rate(best_k, pitcher_k_pct)
        matchup_k_rates.append(mk)

        chase = _batter_chase_rate(pitches, batter_id)
        if chase is not None:
            batter_chase_rates.append(chase)

        contact = _batter_contact_pct(pitches, batter_id)
        if contact is not None:
            batter_contact_pcts.append(contact)

        whiff_profile = _batter_whiff_by_pitch(pitches, batter_id)
        batter_whiff_profiles.append(whiff_profile)

        if _is_bench_bat(stats):
            n_bench += 1

        per_batter_data.append({
            "slot": slot_idx + 1,
            "batter_id": batter_id,
            "bats": bats,
            "effective_hand": effective_hand,
            "batter_k_pct": best_k,
            "matchup_k_rate": mk,
            "pa_weight": PA_WEIGHTS_9[slot_idx] if slot_idx < 9 else 3.71,
        })

    pa_weights = [PA_WEIGHTS_9[i] if i < 9 else 3.71 for i in range(len(batter_k_pcts))]
    total_w = sum(pa_weights)

    features["b1_lineup_weighted_k_pct"] = (
        sum(k * w for k, w in zip(batter_k_pcts, pa_weights)) / total_w
        if total_w > 0 else None
    )

    features["b2_lineup_matchup_k_pct"] = (
        sum(m * w for m, w in zip(matchup_k_rates, pa_weights)) / total_w
        if total_w > 0 else None
    )

    features["b3_lineup_k_by_hand_mean"] = (
        np.mean(batter_k_pcts) if batter_k_pcts else None
    )

    pitcher_k_vs_l = pitcher_splits["pitcher_k_vs_l"]
    pitcher_k_vs_r = pitcher_splits["pitcher_k_vs_r"]
    features["b5_pitcher_k_vs_l_regressed"] = _platoon_regressed_k(
        pitcher_k_vs_l, sum(1 for b in per_batter_data if b["effective_hand"] == "L") * 30,
        pitcher_hand, 0.225,
    )
    features["b5_pitcher_k_vs_r_regressed"] = _platoon_regressed_k(
        pitcher_k_vs_r, sum(1 for b in per_batter_data if b["effective_hand"] == "R") * 30,
        pitcher_hand, 0.225,
    )

    features["b7_lineup_chase_rate"] = (
        float(np.mean(batter_chase_rates)) if batter_chase_rates else None
    )

    features["b10_n_lhb"] = n_lhb
    features["b10_lhb_share"] = n_lhb / len(lineup[:9]) if lineup else 0

    features["b13_n_bench_bats"] = n_bench

    features["b15_lineup_contact_pct"] = (
        float(np.mean(batter_contact_pcts)) if batter_contact_pcts else None
    )

    features["b16_pa_weights"] = pa_weights[:9]

    features["b_per_batter"] = per_batter_data
    features["b_batter_whiff_profiles"] = batter_whiff_profiles

    return features


def _empty_lineup_features() -> dict:
    return {
        "b1_lineup_weighted_k_pct": None,
        "b2_lineup_matchup_k_pct": None,
        "b3_lineup_k_by_hand_mean": None,
        "b4_pitcher_k_vs_l": None,
        "b4_pitcher_k_vs_r": None,
        "b5_pitcher_k_vs_l_regressed": None,
        "b5_pitcher_k_vs_r_regressed": None,
        "b7_lineup_chase_rate": None,
        "b10_n_lhb": 0,
        "b10_lhb_share": 0,
        "b13_n_bench_bats": 0,
        "b15_lineup_contact_pct": None,
        "b16_pa_weights": PA_WEIGHTS_9[:],
        "b_per_batter": [],
        "b_batter_whiff_profiles": [],
    }
