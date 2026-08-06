"""Reconstruct a slate sidecar from archived odds snapshots.

For dates before slate sidecars existed (the pipeline originally
destroyed 212 of 213 evaluated ladder rungs), this rebuilds
data/slates/YYYY-MM-DD.json from:
  - data/odds/dk_k_YYYY-MM-DD.csv        (primary O/U board snapshot)
  - data/odds/dk_k_alts_YYYY-MM-DD.csv   (milestone alt board snapshot)
  - the Statcast cache (as-of features via features.asof)
  - the CURRENT fitted models + calibrator

The result is flagged "reconstructed": true — probabilities and edges
come from today's FIXED model, not the model that made the original
picks. Actual bet stakes are taken from the picks ledger (the truth of
what was risked); everything else shows what the honest model says.

Usage: python tools/reconstruct_slate.py 2026-08-04
"""
import csv
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.backfill_statcast import load_cached
from data.game_context import fetch_schedule, build_game_context
from features.asof import asof_pitcher_game_table, asof_batter_game_table
from models.stage_b_rate import ROOKIE_BF_THRESHOLD
from models.edge import compute_edge, pick_strength
from models.ladder import evaluate_ladder
from strikeout_predictor import StrikeoutPredictor
from tracker import PICKS_PATH, DATA_STATE_DIR
from tools.daily_pipeline import (
    _normalize_name, _write_json_atomic, SLATES_DIR,
)

ODDS_DIR = DATA_STATE_DIR / "odds"


def _load_odds(iso_date: str):
    primary_path = ODDS_DIR / f"dk_k_{iso_date}.csv"
    alts_path = ODDS_DIR / f"dk_k_alts_{iso_date}.csv"
    if not primary_path.exists():
        print(f"No odds snapshot at {primary_path}")
        sys.exit(1)

    with open(primary_path, encoding="utf-8") as f:
        primary = list(csv.DictReader(f))

    alts = []
    if alts_path.exists():
        with open(alts_path, encoding="utf-8") as f:
            alts = list(csv.DictReader(f))

    return primary, alts


def _load_actual_bets(iso_date: str) -> dict:
    """Map (pitcher_id, line_str) -> {units, side} actually risked."""
    bets = {}
    if not PICKS_PATH.exists():
        return bets
    with open(PICKS_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("date") == iso_date and (row.get("bet_placed") or "").upper() == "Y":
                key = (str(row.get("pitcher_id")), str(row.get("line")))
                bets[key] = {
                    "units": float(row.get("units_risked") or 0),
                    "side": (row.get("pick_side") or "").upper(),
                }
    return bets


def reconstruct(iso_date: str):
    primary_odds, alt_odds = _load_odds(iso_date)
    print(f"Odds snapshot: {len(primary_odds)} primaries, {len(alt_odds)} alt rungs")

    raw_games = fetch_schedule(iso_date)
    games = [build_game_context(g) for g in raw_games]
    reg_games = [g for g in games if g.get("game_type") == "R"]

    probables = {}
    for game in reg_games:
        for side in ["home", "away"]:
            pid = game.get(f"{side}_probable_id")
            pname = game.get(f"{side}_probable_name") or ""
            opp_side = "away" if side == "home" else "home"
            if pid and pname:
                probables[_normalize_name(pname)] = {
                    "pitcher_id": pid,
                    "pitcher_name": pname,
                    "pitcher_team": game.get(f"{side}_team_abbr") or "",
                    "opponent_team": game.get(f"{opp_side}_team_abbr") or "",
                    "is_home": side == "home",
                    "game_pk": game.get("game_pk"),
                    "venue": game.get("venue_name") or "",
                }

    alts_by_name = {}
    for alt in alt_odds:
        key = _normalize_name(alt.get("pitcher_name", ""))
        alts_by_name.setdefault(key, []).append(
            {"milestone": alt.get("milestone"), "odds": alt.get("odds")}
        )

    d = date.fromisoformat(iso_date)
    season_start = date(d.year, 3, 26)
    df = load_cached(season_start, d)
    pt = asof_pitcher_game_table(df)
    bt = asof_batter_game_table(df)
    completed = df[df["events"].notna()]

    batter_map = {
        (r.batter, r.game_pk): (r.asof_k_pct_shrunk, r.prior_bf)
        for r in bt.itertuples()
    }

    predictor = StrikeoutPredictor()
    predictor.load_models()

    actual_bets = _load_actual_bets(iso_date)
    date_ts = pd.Timestamp(iso_date)

    pitchers = []
    skipped = []

    for prop in primary_odds:
        name = prop.get("pitcher_name", "")
        norm = _normalize_name(name)
        info = probables.get(norm)
        if info is None:
            last = norm.split()[-1] if norm else ""
            for key, cand in probables.items():
                if key.split()[-1] == last:
                    info = cand
                    break
        if info is None:
            skipped.append(f"{name} (no schedule match)")
            continue

        pid = info["pitcher_id"]
        row = pt[(pt["pitcher"] == pid) & (pt["game_date"] == date_ts)]
        if row.empty:
            skipped.append(f"{name} (did not start)")
            continue
        row = row.iloc[0]

        if row["prior_bf"] < 50 or row["prior_games"] < 3:
            skipped.append(f"{name} (insufficient prior history)")
            continue

        game_pk = row["game_pk"]
        g = completed[(completed["game_pk"] == game_pk) & (completed["pitcher"] == pid)]
        seen = []
        for b in g.sort_values("at_bat_number")["batter"]:
            if b not in seen:
                seen.append(b)
            if len(seen) >= 9:
                break
        kpcts = []
        n_rookies = 0
        for b in seen[:9]:
            k_pct, prior_bf = batter_map.get((b, game_pk), (0.225, 0))
            kpcts.append(float(k_pct) if k_pct is not None and not np.isnan(k_pct) else 0.225)
            if (prior_bf or 0) < ROOKIE_BF_THRESHOLD:
                n_rookies += 1
        while len(kpcts) < 9:
            kpcts.append(0.225)

        dk_line = float(prop["line"])
        over_odds = prop.get("over_odds", "-110")
        under_odds = prop.get("under_odds", "-110")

        features = {
            "a3_season_k_pct_shrunk": float(row["asof_k_pct_shrunk"]),
            "c1_bf_mean": float(row["asof_bf_mean"]),
            "a9_zone_pct": float(row["asof_zone_pct"]) if not np.isnan(row["asof_zone_pct"]) else None,
            "f1_eastward_tz": float(row["eastward_tz"]),
            "b14_n_rookies": float(n_rookies),
            "c10_il_return": False,
            "c11_pitch_limit": None,
            "c12_bp_heavy": False,
        }
        result = predictor.predict(features, lineup_k_pcts=kpcts, lines=[dk_line])
        p_over = result["per_line"][dk_line]
        # Reconstructions use the actual batters faced, i.e. a known lineup.
        ei = compute_edge(p_over, over_odds, under_odds, lineup_confirmed=True)
        strength = pick_strength(ei["best_edge"], ei["threshold"])

        primary_key = (str(pid), str(dk_line))
        primary_bet = actual_bets.get(primary_key, {})
        primary_units = primary_bet.get("units", 0.0)

        rungs_out = []
        rungs = evaluate_ladder(
            result["k_dist"], alts_by_name.get(norm, []),
            primary_line=dk_line, primary_units=primary_units,
            calibrate_fn=predictor.calibrate_prob,
            expected_k=float(result["expected_k"]),
            primary_side=primary_bet.get("side"),
        )
        for r in rungs:
            ladder_key = (str(pid), f"{r['milestone']}+")
            actual_units = actual_bets.get(ladder_key, {}).get("units", 0.0)
            rungs_out.append({
                "milestone": r["milestone"],
                "odds": r["odds_str"],
                "raw_model_prob": round(float(r["raw_model_prob"]), 4),
                "model_prob": round(float(r["model_prob"]), 4),
                "fair_prob": round(float(r["fair_prob"]), 4),
                "blended_prob": round(float(r["blended_prob"]), 4),
                "edge": round(float(r["edge"]), 4),
                "strength": r["strength"],
                "units_risked": actual_units,
                "status": "bet" if actual_units > 0 else (
                    r["status"] if r["status"] != "bet" else "passed_below_threshold"
                ),
            })

        pitchers.append({
            "pitcher_id": pid,
            "pitcher_name": info["pitcher_name"],
            "pitcher_team": info["pitcher_team"],
            "opponent_team": info["opponent_team"],
            "is_home": info["is_home"],
            "venue": info["venue"],
            "game_pk": int(game_pk),
            "start_time_utc": prop.get("start_time_utc", ""),
            "line": dk_line,
            "over_odds": over_odds,
            "under_odds": under_odds,
            "lineup_source": "reconstructed",
            "expected_k": round(float(result["expected_k"]), 2),
            "expected_bf": round(float(result["expected_bf"]), 2),
            "p_over_raw": round(float(result["per_line_raw"][dk_line]), 4),
            "p_over_calibrated": round(float(p_over), 4),
            "blended_prob_over": round(float(ei["blended_prob_over"]), 4),
            "fair_over": round(float(ei["fair_over"]), 4),
            "hold_pct": round(float(ei["hold_pct"]), 4),
            "best_side": ei["best_side"],
            "edge_best": round(float(ei["best_edge"]), 4),
            "threshold": round(float(ei["threshold"]), 4),
            "strength": strength,
            "primary_units_risked": primary_units,
            "k_dist": [round(float(x), 6) for x in result["k_dist"]],
            "ladder": rungs_out,
        })

    from datetime import datetime
    from zoneinfo import ZoneInfo
    payload = {
        "date": iso_date,
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "reconstructed": True,
        "note": ("Rebuilt from archived odds snapshots with the current "
                 "(leakage-fixed, calibrated, market-blended) model. Edges "
                 "shown are NOT the edges the original picks claimed. "
                 "units_risked reflects actual bets from the ledger."),
        "skipped": skipped,
        "pitchers": pitchers,
    }

    out_path = SLATES_DIR / f"{iso_date}.json"
    _write_json_atomic(out_path, payload)
    print(f"Reconstructed slate written: {out_path}")
    print(f"  {len(pitchers)} pitchers, {len(skipped)} skipped")
    for s in skipped:
        print(f"    skipped: {s}")


if __name__ == "__main__":
    iso = sys.argv[1] if len(sys.argv) > 1 else "2026-08-04"
    reconstruct(iso)
