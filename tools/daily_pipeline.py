"""Daily prediction pipeline — fetch slate, predict, find edge, generate picks.

Usage:
    python tools/daily_pipeline.py              # today's slate
    python tools/daily_pipeline.py 2026-08-04   # specific date
    python tools/daily_pipeline.py --dry-run    # predict but don't write picks
    python tools/daily_pipeline.py --no-ladder  # skip milestone/alt lines

Orchestrates:
  1. Fetch tonight's slate from MLB Stats API
  2. Fetch DK strikeout prop odds (O/U + milestone alt lines)
  3. Match DK pitchers to MLB API probables
  4. Compute pitcher/batter features from Statcast cache
  5. Run compound model -> P(K = k) full distribution
  6. Compute edge on primary O/U line
  7. Evaluate ladder: check every available milestone for edge
  8. Size all bets via quarter-Kelly with per-pitcher + daily caps
  9. Write qualifying picks to tracker CSV
"""
import csv
import json
import math
import os
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.game_context import fetch_schedule, build_game_context
from scrape_dk_odds import fetch_dk_strikeout_props, fetch_dk_strikeout_alts
from strikeout_predictor import StrikeoutPredictor
from features.asof import (
    shrink_rate, PITCHER_K_PSEUDO_BF, BATTER_K_PSEUDO_BF,
)
from models.edge import (
    compute_edge, pick_strength, american_to_decimal, no_vig_fair_prob,
)
from models.staking import kelly_stake, portfolio_daily_cap
from models.ladder import evaluate_ladder, LADDER_MAX_UNITS
from tracker import FIELDS, PICKS_PATH, _write_rows, _pick_is_locked, MAX_STAKE_UNITS

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

LEAGUE_K_RATE = 0.225
SHRINKAGE_BF = PITCHER_K_PSEUDO_BF

SLATES_DIR = Path(__file__).parent.parent / "data" / "slates"


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, strip suffixes like Jr./III, collapse whitespace."""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.strip().lower()
    for suffix in [" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return " ".join(n.split())


def _match_dk_to_mlb(dk_props: list[dict], mlb_games: list[dict]) -> list[dict]:
    """Match DK pitcher props to MLB API probable pitchers."""
    mlb_pitchers = {}
    for game in mlb_games:
        game_pk = game.get("game_pk")
        for side in ["home", "away"]:
            pid = game.get(f"{side}_probable_id")
            pname = game.get(f"{side}_probable_name") or ""
            team_abbr = game.get(f"{side}_team_abbr") or ""
            opp_side = "away" if side == "home" else "home"
            opp_abbr = game.get(f"{opp_side}_team_abbr") or ""

            if pid and pname:
                key = _normalize_name(pname)
                mlb_pitchers[key] = {
                    "pitcher_id": pid,
                    "pitcher_name": pname,
                    "pitcher_team": team_abbr,
                    "opponent_team": opp_abbr,
                    "is_home": side == "home",
                    "game_pk": game_pk,
                    "venue": game.get("venue_name") or "",
                    "lineup": game.get(f"{opp_side}_lineup") or [],
                    "lineup_source": game.get("lineup_source") or "none",
                }

    matched = []
    unmatched_dk = []

    for prop in dk_props:
        dk_name = _normalize_name(prop.get("pitcher_name", ""))
        if dk_name in mlb_pitchers:
            entry = {**mlb_pitchers[dk_name], **prop}
            matched.append(entry)
            continue

        dk_last = dk_name.split()[-1] if dk_name else ""
        found = False
        for key, info in mlb_pitchers.items():
            if key.split()[-1] == dk_last and not found:
                entry = {**info, **prop}
                matched.append(entry)
                found = True
                break

        if not found:
            unmatched_dk.append(prop.get("pitcher_name", "???"))

    if unmatched_dk:
        print(f"  Unmatched DK pitchers: {', '.join(unmatched_dk)}")

    return matched


def _group_alt_lines_by_pitcher(alt_lines: list[dict]) -> dict:
    """Group milestone alt lines by normalized pitcher name.

    Returns {normalized_name: [{'milestone': int, 'odds': str}, ...]}.
    """
    grouped = {}
    for alt in alt_lines:
        name = _normalize_name(alt.get("pitcher_name", ""))
        if name not in grouped:
            grouped[name] = []
        grouped[name].append(alt)
    return grouped


def _compute_pitcher_stats(statcast_df: pd.DataFrame, pitcher_id: int,
                           home_team: str | None = None) -> dict:
    """Compute season K% and BF stats for a pitcher from Statcast data."""
    completed = statcast_df[statcast_df["events"].notna()]
    p = completed[completed["pitcher"] == pitcher_id]

    if p.empty:
        return {"season_k_pct": None, "bf_mean": None, "total_bf": 0}

    total_bf = len(p)
    ks = p["events"].isin(["strikeout", "strikeout_double_play"]).sum()
    raw_k_pct = ks / total_bf if total_bf > 0 else LEAGUE_K_RATE

    shrunk_k_pct = (total_bf * raw_k_pct + SHRINKAGE_BF * LEAGUE_K_RATE) / (
        total_bf + SHRINKAGE_BF
    )

    game_bf = p.groupby("game_pk").size()
    starter_games = game_bf[game_bf >= 9]

    if len(starter_games) >= 3:
        bf_mean = float(starter_games.mean())
    else:
        bf_mean = 21.1

    zone_pct = None
    all_pitches = statcast_df[statcast_df["pitcher"] == pitcher_id]
    if "zone" in all_pitches.columns:
        zone_valid = all_pitches[all_pitches["zone"].notna()]
        if len(zone_valid) >= 50:
            in_zone = zone_valid["zone"].isin(range(1, 10)).sum()
            zone_pct = float(in_zone / len(zone_valid))

    from features.t2_candidates import TEAM_TIMEZONES
    eastward_tz = 0.0
    if home_team and "home_team" in statcast_df.columns and "game_date" in statcast_df.columns:
        pitcher_all = statcast_df[statcast_df["pitcher"] == pitcher_id]
        if not pitcher_all.empty:
            last_games = pitcher_all.groupby("game_pk").agg(
                home_team=("home_team", "first"),
                game_date=("game_date", "first"),
            ).reset_index().sort_values("game_date")
            if len(last_games) >= 1:
                prev_home = last_games.iloc[-1]["home_team"]
                prev_tz = TEAM_TIMEZONES.get(prev_home)
                curr_tz = TEAM_TIMEZONES.get(home_team)
                if prev_tz is not None and curr_tz is not None:
                    eastward_tz = max(0, curr_tz - prev_tz)

    return {
        "season_k_pct": float(shrunk_k_pct),
        "season_k_pct_raw": float(raw_k_pct),
        "bf_mean": bf_mean,
        "total_bf": int(total_bf),
        "n_starts": int(len(starter_games)),
        "zone_pct": zone_pct,
        "eastward_tz": float(eastward_tz),
    }


def _compute_batter_k_rates(
    statcast_df: pd.DataFrame, lineup: list[dict]
) -> list[float]:
    """Compute shrunk K% for each batter in the lineup from Statcast data.

    Uses the same empirical-Bayes shrinkage as training
    (features.asof.BATTER_K_PSEUDO_BF) so live inputs match the
    distribution the model was fit on. A batter with no history gets
    exactly the league rate.
    """
    completed = statcast_df[statcast_df["events"].notna()]
    k_rates = []

    for batter_info in lineup[:9]:
        batter_id = batter_info.get("player_id")
        if batter_id is None:
            k_rates.append(LEAGUE_K_RATE)
            continue

        b = completed[completed["batter"] == batter_id]
        ks = b["events"].isin(["strikeout", "strikeout_double_play"]).sum() if not b.empty else 0
        k_rates.append(shrink_rate(float(ks), float(len(b)), BATTER_K_PSEUDO_BF))

    while len(k_rates) < 9:
        k_rates.append(LEAGUE_K_RATE)

    return k_rates


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Atomic JSON write: tempfile + fsync + os.replace (repo rule)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _write_slate_sidecar(game_date: str, predictions: list) -> None:
    """Persist the full evaluated slate to data/slates/YYYY-MM-DD.json.

    Every analyzed pitcher — full P(K=k) distribution, expected K/BF,
    and EVERY evaluated ladder rung (bet or passed with reason) — so the
    dashboard can show the whole board, not just the surviving picks.
    """
    pitchers = []
    for pred in predictions:
        k_dist = pred.get("k_dist")
        rungs = []
        for r in pred.get("ladder_eval") or []:
            rungs.append({
                "milestone": r["milestone"],
                "odds": r["odds_str"],
                "raw_model_prob": round(float(r["raw_model_prob"]), 4),
                "model_prob": round(float(r["model_prob"]), 4),
                "fair_prob": round(float(r["fair_prob"]), 4),
                "blended_prob": round(float(r["blended_prob"]), 4),
                "edge": round(float(r["edge"]), 4),
                "strength": r["strength"],
                "units_risked": float(r["units_risked"]),
                "status": r["status"],
            })

        pitchers.append({
            "pitcher_id": pred.get("pitcher_id"),
            "pitcher_name": pred.get("pitcher_name"),
            "pitcher_team": pred.get("pitcher_team"),
            "opponent_team": pred.get("opponent_team"),
            "is_home": bool(pred.get("is_home")),
            "venue": pred.get("venue"),
            "game_pk": pred.get("game_pk"),
            "line": pred.get("line"),
            "over_odds": str(pred.get("over_odds", "")),
            "under_odds": str(pred.get("under_odds", "")),
            "lineup_source": pred.get("lineup_source"),
            "expected_k": round(float(pred.get("expected_k", 0)), 2),
            "expected_bf": round(float(pred.get("expected_bf", 0)), 2),
            "p_over_raw": round(float(pred.get("model_prob_over_raw", 0)), 4),
            "p_over_calibrated": round(float(pred.get("model_prob_over", 0)), 4),
            "blended_prob_over": round(float(pred.get("blended_prob_over", 0)), 4),
            "fair_over": round(float(pred.get("fair_over", 0)), 4),
            "hold_pct": round(float(pred.get("hold_pct", 0)), 4),
            "best_side": pred.get("best_side"),
            "edge_best": round(float(pred.get("best_edge", 0)), 4),
            "threshold": round(float(pred.get("threshold", 0)), 4),
            "strength": pred.get("strength"),
            "primary_units_risked": float(pred.get("primary_units_final", 0.0)),
            "k_dist": [round(float(x), 6) for x in (k_dist if k_dist is not None else [])],
            "ladder": rungs,
        })

    payload = {
        "date": game_date,
        "generated_at": datetime.now(UTC).isoformat(),
        "reconstructed": False,
        "pitchers": pitchers,
    }
    out_path = SLATES_DIR / f"{game_date}.json"
    _write_json_atomic(out_path, payload)
    print(f"  Slate sidecar written: {out_path} ({len(pitchers)} pitchers)")


def _load_existing_picks(iso_date: str) -> dict:
    """Load existing picks for a date, keyed by (game_pk, pitcher_id, line)."""
    if not PICKS_PATH.exists():
        return {}

    existing = {}
    with open(PICKS_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("date") == iso_date:
                key = (
                    row.get("game_pk", ""),
                    row.get("pitcher_id", ""),
                    row.get("line", ""),
                )
                existing[key] = row
    return existing


def run_daily(
    game_date: str | None = None,
    dry_run: bool = False,
    enable_ladder: bool = True,
) -> list[dict]:
    """Run the full daily prediction pipeline.

    Returns list of pick dicts (primary O/U + ladder milestones).
    """
    if game_date is None:
        game_date = datetime.now(ET).strftime("%Y-%m-%d")

    print(f"{'=' * 70}")
    print(f"DAILY PIPELINE — {game_date}")
    if enable_ladder:
        print(f"  Ladder betting: ON")
    print(f"{'=' * 70}")

    # 1. Fetch MLB schedule
    print("\n[1/8] Fetching MLB schedule...")
    try:
        raw_games = fetch_schedule(game_date)
    except Exception as exc:
        print(f"  Schedule fetch failed: {exc}")
        return []

    games = [build_game_context(g) for g in raw_games]
    reg_games = [g for g in games if g.get("game_type") == "R"]
    print(f"  {len(reg_games)} regular-season games")

    if not reg_games:
        print("  No games today.")
        return []

    for g in reg_games:
        hp = g.get("home_probable_name") or "TBD"
        ap = g.get("away_probable_name") or "TBD"
        lu = "lineups" if g.get("home_lineup") else "no lineups"
        print(f"    {g['away_team_abbr']} @ {g['home_team_abbr']}: {ap} vs {hp} ({lu})")

    # 2. Fetch DK odds — primary O/U + milestone alt lines
    print("\n[2/8] Fetching DraftKings strikeout props...")
    try:
        dk_props = fetch_dk_strikeout_props()
    except Exception as exc:
        print(f"  DK fetch failed: {exc}")
        return []

    today_props = [p for p in dk_props if p.get("date") == game_date]
    print(f"  {len(today_props)} pitcher O/U props for {game_date}")

    if not today_props:
        print("  No props available (slate may not be posted yet).")
        return []

    alt_lines_by_pitcher = {}
    if enable_ladder:
        print("  Fetching milestone/alt lines...")
        try:
            dk_alts = fetch_dk_strikeout_alts()
            today_alts = [a for a in dk_alts if a.get("date") == game_date]
            alt_lines_by_pitcher = _group_alt_lines_by_pitcher(today_alts)
            total_milestones = sum(len(v) for v in alt_lines_by_pitcher.values())
            print(f"  {total_milestones} milestone lines across {len(alt_lines_by_pitcher)} pitchers")
        except Exception as exc:
            print(f"  Alt-line fetch failed: {exc} (continuing without ladder)")

    # 3. Match DK to MLB API
    print("\n[3/8] Matching DK pitchers to MLB probables...")
    matched = _match_dk_to_mlb(today_props, reg_games)
    print(f"  {len(matched)} matched")

    if not matched:
        print("  No matches found.")
        return []

    # 4. Load Statcast data for feature computation
    print("\n[4/8] Loading Statcast data for features...")
    try:
        from data.backfill_statcast import load_cached
        d = date.fromisoformat(game_date)
        season_start = date(d.year, 3, 26)
        statcast_df = load_cached(season_start, d)
        print(f"  {len(statcast_df)} pitch rows loaded")
    except Exception as exc:
        print(f"  Statcast load failed: {exc}")
        print("  Falling back to league-average features.")
        statcast_df = pd.DataFrame()

    # 5. Load model and run predictions
    print("\n[5/8] Running predictions...")
    predictor = StrikeoutPredictor()
    predictor.load_models()

    predictions = []
    for entry in matched:
        pitcher_id = entry.get("pitcher_id")
        pitcher_name = entry.get("pitcher_name", "???")
        dk_line = float(entry.get("line", 5.5))
        over_odds = entry.get("over_odds", "-110")
        under_odds = entry.get("under_odds", "-110")

        home_team = entry.get("pitcher_team") if entry.get("is_home") else entry.get("opponent_team")
        if not statcast_df.empty and pitcher_id:
            stats = _compute_pitcher_stats(statcast_df, pitcher_id, home_team=home_team)
        else:
            stats = {"season_k_pct": LEAGUE_K_RATE, "bf_mean": 21.1, "total_bf": 0}

        if stats["season_k_pct"] is None or stats["total_bf"] < 50:
            print(f"    {pitcher_name}: insufficient data ({stats['total_bf']} BF), skipping")
            continue

        lineup = entry.get("lineup", [])
        if lineup and not statcast_df.empty:
            lineup_k_pcts = _compute_batter_k_rates(statcast_df, lineup)
            lineup_source = entry.get("lineup_source", "confirmed")
        else:
            lineup_k_pcts = [LEAGUE_K_RATE] * 9
            lineup_source = "projected"

        n_rookies = 0.0
        if lineup and not statcast_df.empty:
            completed_all = statcast_df[statcast_df["events"].notna()]
            for batter_info in lineup[:9]:
                bid = batter_info.get("player_id")
                if bid is None:
                    continue
                batter_bf = len(completed_all[completed_all["batter"] == bid])
                if batter_bf < 100:
                    n_rookies += 1

        features = {
            "a3_season_k_pct_shrunk": stats["season_k_pct"],
            "a3_season_k_pct_raw": stats.get("season_k_pct_raw", stats["season_k_pct"]),
            "c1_bf_mean": stats["bf_mean"],
            "c10_il_return": False,
            "c11_pitch_limit": None,
            "c12_bp_heavy": False,
            "a9_zone_pct": stats.get("zone_pct"),
            "f1_eastward_tz": stats.get("eastward_tz", 0.0),
            "b14_n_rookies": n_rookies,
        }

        lines_to_check = [dk_line]
        result = predictor.predict(features, lineup_k_pcts=lineup_k_pcts, lines=lines_to_check)

        model_prob_over = result["per_line"][dk_line]
        edge_info = compute_edge(model_prob_over, over_odds, under_odds)
        strength = pick_strength(edge_info["best_edge"], edge_info["threshold"])

        entry_result = {
            **entry,
            "model_prob_over": model_prob_over,
            "model_prob_under": 1.0 - model_prob_over,
            "model_prob_over_raw": result["per_line_raw"][dk_line],
            "expected_k": result["expected_k"],
            "expected_bf": result["expected_bf"],
            "pitcher_k_pct": stats["season_k_pct"],
            "lineup_source": lineup_source,
            "k_dist": result["k_dist"],
            **edge_info,
        }
        entry_result["strength"] = strength

        predictions.append(entry_result)

        side_arrow = "OVER" if edge_info["best_side"] == "OVER" else "UNDER"
        print(
            f"    {pitcher_name:<22} line={dk_line}  "
            f"E[K]={result['expected_k']:.1f}  "
            f"P(over)={model_prob_over:.1%}  "
            f"fair={edge_info['fair_over']:.1%}  "
            f"edge={edge_info['best_edge']:+.1%} {side_arrow}  "
            f"[{strength}]"
        )

    # 6. Filter primary picks + evaluate ladder
    print(f"\n[6/8] Filtering primary picks and evaluating ladder...")
    primary_plays = [p for p in predictions if p["clears_threshold"]]
    print(f"  {len(primary_plays)} primary picks clear edge threshold")

    all_plays = []

    for play in primary_plays:
        decimal_odds = american_to_decimal(play["best_odds"])
        raw_units = kelly_stake(play["model_prob_best"], decimal_odds)
        play["units_risked"] = min(raw_units, MAX_STAKE_UNITS)
        play["pick_type"] = "primary"
        all_plays.append(play)

    # 7. Ladder evaluation
    if enable_ladder and alt_lines_by_pitcher:
        print(f"\n[7/8] Evaluating ladder milestones...")
        ladder_count = 0

        for pred in predictions:
            pitcher_name = pred.get("pitcher_name", "")
            norm_name = _normalize_name(pitcher_name)
            k_dist = pred.get("k_dist")
            dk_line = float(pred.get("line", 5.5))
            expected_k = pred.get("expected_k", 0)

            pitcher_alts = alt_lines_by_pitcher.get(norm_name, [])
            if not pitcher_alts or k_dist is None:
                continue

            primary_units = 0.0
            for play in all_plays:
                if (play.get("pitcher_id") == pred.get("pitcher_id")
                        and play.get("pick_type") == "primary"):
                    primary_units = play.get("units_risked", 0.0)

            rungs = evaluate_ladder(
                k_dist, pitcher_alts,
                primary_line=dk_line,
                primary_units=primary_units,
                calibrate_fn=predictor.calibrate_prob,
            )
            pred["ladder_eval"] = rungs

            bet_rungs = [r for r in rungs if r["status"] == "bet"]
            if bet_rungs:
                print(f"    {pitcher_name}: {len(bet_rungs)} ladder rungs "
                      f"({len(rungs)} evaluated)")
                for rung in bet_rungs:
                    print(
                        f"      {rung['milestone']}+ K  "
                        f"@ {rung['odds']:>+4}  "
                        f"P={rung['model_prob']:.1%}  "
                        f"edge={rung['edge']:+.1%}  "
                        f"stake={rung['units_risked']:.2f}u  "
                        f"[{rung['strength']}]"
                    )

                    ladder_play = {
                        **pred,
                        "line": str(rung["milestone"]) + "+",
                        "pick_side": "OVER",
                        "best_side": "OVER",
                        "best_edge": rung["edge"],
                        "best_odds": rung["odds"],
                        "model_prob_over": rung["model_prob"],
                        "model_prob_under": 1.0 - rung["model_prob"],
                        "fair_prob": rung["fair_prob"],
                        "over_odds": rung["odds_str"],
                        "under_odds": "",
                        "strength": rung["strength"],
                        "units_risked": rung["units_risked"],
                        "pick_type": "ladder",
                        "milestone": rung["milestone"],
                    }
                    all_plays.append(ladder_play)
                    ladder_count += 1

        print(f"  {ladder_count} ladder picks added")
    else:
        print(f"\n[7/8] Ladder: skipped")

    if not all_plays:
        print("  No qualifying plays today.")
        return []

    # Apply portfolio daily cap across ALL plays (primary + ladder)
    all_plays = portfolio_daily_cap(all_plays)
    all_plays = [p for p in all_plays if p["units_risked"] > 0]

    # Reconcile final stakes back into the full-board evaluation so the
    # slate sidecar records what was actually bet after every cap.
    final_primary = {}
    final_ladder = {}
    for p in all_plays:
        pid = p.get("pitcher_id")
        if p.get("pick_type") == "primary":
            final_primary[pid] = p.get("units_risked", 0.0)
        elif p.get("pick_type") == "ladder":
            final_ladder[(pid, p.get("milestone"))] = p.get("units_risked", 0.0)

    for pred in predictions:
        pid = pred.get("pitcher_id")
        pred["primary_units_final"] = final_primary.get(pid, 0.0)
        for rung in pred.get("ladder_eval") or []:
            if rung["status"] == "bet":
                final = final_ladder.get((pid, rung["milestone"]), 0.0)
                rung["units_risked"] = final
                if final <= 0:
                    rung["status"] = "passed_daily_cap"

    print(f"\n  ALL PICKS (primary + ladder):")
    total_units = 0
    current_pitcher = None
    for p in sorted(all_plays, key=lambda x: (x.get("pitcher_name", ""), x.get("pick_type", ""))):
        pname = p.get("pitcher_name", "")
        if pname != current_pitcher:
            if current_pitcher is not None:
                print(f"    {'':>6}   {'':>22}")
            current_pitcher = pname

        ptype = "   " if p.get("pick_type") == "primary" else " L "
        line_str = str(p.get("line", ""))
        print(
            f"    {p['strength']:>6} |{ptype}{pname:<22} "
            f"{p['best_side']:<5} {line_str:<5}  "
            f"@ {p['best_odds']:>+4}  "
            f"edge={p['best_edge']:+.1%}  "
            f"stake={p['units_risked']:.2f}u"
        )
        total_units += p["units_risked"]
    print(f"    {'':>6}   {'Total':>22} {'':>13} {total_units:.2f}u")

    # 8. Write to tracker
    if dry_run:
        print(f"\n[8/8] DRY RUN — not writing picks.")
        return all_plays

    print(f"\n[8/8] Writing picks to {PICKS_PATH}...")
    _write_slate_sidecar(game_date, predictions)
    existing_picks = _load_existing_picks(game_date)

    all_rows = []
    if PICKS_PATH.exists():
        with open(PICKS_PATH, encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))

    now_utc = datetime.now(UTC).isoformat()
    new_count = 0

    for play in all_plays:
        line_val = str(play.get("line", ""))
        if play.get("pick_type") == "ladder":
            line_val = str(play.get("milestone", "")) + "+"

        key = (
            str(play.get("game_pk", "")),
            str(play.get("pitcher_id", "")),
            line_val,
        )

        if key in existing_picks:
            if _pick_is_locked(existing_picks[key], game_date):
                print(f"    LOCKED: {play['pitcher_name']} {line_val}")
                continue

        if play.get("pick_type") == "ladder":
            # True de-vigged fair prob — NOT the model prob (old bug).
            nv_fair = f"{play.get('fair_prob', 0.0):.4f}"
            over_odds_str = str(play.get("over_odds", ""))
            under_odds_str = ""
            pick_label = f"OVER {line_val} ({play['strength']})"
        else:
            nv = no_vig_fair_prob(play["over_odds"], play["under_odds"])
            nv_fair = f"{nv['fair_over']:.4f}"
            over_odds_str = play.get("over_odds", "")
            under_odds_str = play.get("under_odds", "")
            pick_label = f"{play['best_side']} {line_val} ({play['strength']})"

        pick_row = {
            "date": game_date,
            "game_pk": str(play.get("game_pk", "")),
            "pitcher_name": play.get("pitcher_name", ""),
            "pitcher_id": str(play.get("pitcher_id", "")),
            "pitcher_team": play.get("pitcher_team", ""),
            "opponent_team": play.get("opponent_team", ""),
            "is_home": "Y" if play.get("is_home") else "N",
            "venue": play.get("venue", ""),
            "line": line_val,
            "pick_side": play.get("best_side", play.get("pick_side", "")),
            "pick_strength": play["strength"],
            "pick_label": pick_label,
            "model_prob_over": f"{play['model_prob_over']:.4f}",
            "model_prob_under": f"{play['model_prob_under']:.4f}",
            "market_over_odds": over_odds_str,
            "market_under_odds": under_odds_str,
            "opened_over_odds": over_odds_str,
            "opened_under_odds": under_odds_str,
            "no_vig_fair_prob": nv_fair,
            "edge_pct": f"{play['best_edge']:.4f}",
            "units_risked": f"{play['units_risked']:.2f}",
            "bet_placed": "Y",
            "graded_result": "",
            "actual_strikeouts": "",
            "profit_loss_units": "",
            "created_at": now_utc,
            "updated_at": now_utc,
            "lineup_source": play.get("lineup_source", ""),
            "notes": "ladder" if play.get("pick_type") == "ladder" else "",
        }

        if key in existing_picks:
            for i, row in enumerate(all_rows):
                rk = (
                    row.get("game_pk", ""),
                    row.get("pitcher_id", ""),
                    row.get("line", ""),
                )
                if rk == key and row.get("date") == game_date:
                    all_rows[i] = pick_row
                    break
        else:
            all_rows.append(pick_row)
            new_count += 1

    _write_rows(PICKS_PATH, all_rows)
    print(f"  Wrote {new_count} new picks ({len(all_rows)} total rows)")

    return all_plays


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Daily strikeout prediction pipeline")
    parser.add_argument("date", nargs="?", default=None, help="Game date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Predict but don't write picks")
    parser.add_argument("--no-ladder", action="store_true", help="Skip milestone/alt line ladder")
    args = parser.parse_args()

    plays = run_daily(
        game_date=args.date,
        dry_run=args.dry_run,
        enable_ladder=not args.no_ladder,
    )

    if plays:
        print(f"\nDone. {len(plays)} picks generated.")
    else:
        print("\nDone. No qualifying plays.")


if __name__ == "__main__":
    main()
