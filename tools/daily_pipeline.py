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
import re
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
    IL_GAP_DAYS, BP_HEAVY_PITCHES, team_relief_pitches_by_date,
)
from models.edge import (
    compute_edge, pick_strength, american_to_decimal, no_vig_fair_prob,
)
from models.staking import kelly_stake, portfolio_daily_cap, quantize_stake
from models.ladder import evaluate_ladder, LADDER_MAX_UNITS
from tracker import (
    FIELDS, PICKS_PATH, _write_rows, _pick_is_locked, _journal_change,
    MAX_STAKE_UNITS,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

LEAGUE_K_RATE = 0.225
SHRINKAGE_BF = PITCHER_K_PSEUDO_BF

# Role gate (AUDIT A-007). Stage A is trained on starter workloads, so
# pricing a reliever/opener with it is out-of-distribution. A genuine
# starter's typical turn is ~18-22 batters faced; anyone whose recent
# outings sit well below that is not priced at all.
ROLE_LOOKBACK_GAMES = 8
STARTER_TYPICAL_BF = 15.0
MIN_APPEARANCES_TO_PRICE = 3

# --- Prior-season history (docs/PRIOR_SEASON_SCOPE.md) ---------------
# OFF until the gauntlet clears. Flipping this on changes which pitchers
# are priced at all, so it is a promotion decision, not a config tweak.
USE_PRIOR_SEASON = False

# What a batter faced LAST season is worth against one faced this season,
# for the K-rate estimate only. Fitted 0.60 by binomial log-loss on the
# 401 starts the feature recovers in 2025 (prior 2024); the loss curve is
# flat from 0.25 to 1.00 (0.52288 vs 0.52306), so the parameter is weakly
# identified and 0.5 is used rather than implying precision at 0.60.
PRIOR_SEASON_WEIGHT = 0.5

# Weight on CURRENT-season workload when the pitcher has 1-2 outings and
# a usable prior season; the remainder goes to his prior-season p25.
# Measured on both year pairs, this blend beats either source alone on
# both average error and the dangerous upper tail. With zero current
# outings the weight is necessarily 0 and the estimate is pure prior p25.
PRIOR_WORKLOAD_BLEND = 0.5

# A prior season only counts if it is substantial enough to establish
# both a rate and a role. Below this the pitcher stays refused.
MIN_PRIOR_BF = 200
MIN_PRIOR_STARTS = 10

from tracker import DATA_STATE_DIR

SLATES_DIR = DATA_STATE_DIR / "slates"


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, drop a book's team tag, strip suffixes like
    Jr./III, collapse whitespace."""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    # A book disambiguates two players sharing a name by appending the
    # team — "Ryan Johnson (LAA)". That tag is real information (see
    # _team_tag, which uses it to break ties) but it is not part of the
    # name, and leaving it attached made the key unmatchable:
    # "ryan johnson (laa)" never equals "ryan johnson", and the last-name
    # fallback then compared "(laa)" against "johnson" and missed too. He
    # was dropped from every slate DK listed him on — 2026-08-06 and
    # 2026-08-11 — with only a line in the pipeline log to show for it.
    n = re.sub(r"\s*\([^)]*\)", " ", n)
    n = n.strip().lower()
    for suffix in [" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return " ".join(n.split())


def _team_tag(name: str) -> str | None:
    """Return the team abbreviation a book appended to a name, if any.

    "Ryan Johnson (LAA)" -> "LAA". Only a bare 2-4 letter token counts, so
    a parenthetical that is not a team ("(prospect)") yields None rather
    than a bogus tie-breaker.
    """
    m = re.search(r"\(\s*([A-Za-z]{2,4})\s*\)\s*$", name or "")
    return m.group(1).upper() if m else None


def _resolve_probable(candidates: list[dict], team_tag: str | None) -> dict | None:
    """Pick one MLB probable from name-match candidates, or None.

    Returns None when the name is ambiguous and the team tag cannot break
    the tie. Attaching a line to the wrong arm is worse than dropping the
    row: it would price one pitcher's projection against another's number
    and the edge filter selects hardest on exactly that kind of mismatch.
    """
    if not candidates:
        return None
    if team_tag:
        on_team = [c for c in candidates
                   if (c.get("pitcher_team") or "").upper() == team_tag]
        if on_team:
            candidates = on_team
    return candidates[0] if len(candidates) == 1 else None


def _match_dk_to_mlb(dk_props: list[dict], mlb_games: list[dict]) -> list[dict]:
    """Match DK pitcher props to MLB API probable pitchers.

    Full normalized name first, then last name. Either step can turn up
    more than one probable — two pitchers genuinely share a name, or two
    share a surname on the same slate — so candidates are collected as a
    list and resolved by _resolve_probable rather than by taking whichever
    one the dict happened to hold last.
    """
    mlb_pitchers: dict[str, list[dict]] = {}
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
                if not key:
                    continue
                mlb_pitchers.setdefault(key, []).append({
                    "pitcher_id": pid,
                    "pitcher_name": pname,
                    "pitcher_team": team_abbr,
                    "opponent_team": opp_abbr,
                    "is_home": side == "home",
                    "game_pk": game_pk,
                    "venue": game.get("venue_name") or "",
                    "lineup": game.get(f"{opp_side}_lineup") or [],
                    "lineup_source": game.get("lineup_source") or "none",
                })

    matched = []
    unmatched_dk = []

    for prop in dk_props:
        raw_name = prop.get("pitcher_name", "")
        dk_name = _normalize_name(raw_name)
        team_tag = _team_tag(raw_name)

        candidates = mlb_pitchers.get(dk_name, [])
        if not candidates:
            dk_last = dk_name.split()[-1] if dk_name else ""
            if dk_last:
                candidates = [info
                              for key, infos in mlb_pitchers.items()
                              if key.split()[-1] == dk_last
                              for info in infos]

        info = _resolve_probable(candidates, team_tag)
        if info is None:
            why = ("ambiguous, no team tag to break the tie" if candidates
                   else "no MLB probable with that name")
            unmatched_dk.append(f"{raw_name or '???'} [{why}]")
            continue

        entry = {**info, **prop}
        # The book's name wins the merge, and it may still carry the
        # disambiguation tag. The tag has done its job in matching; drop it
        # so "Ryan Johnson (LAA)" never reaches the board, the ledger or
        # the grader. Only the tag is removed — the rest of the book's
        # spelling is left alone, because the ledger and grader join on it.
        if team_tag:
            entry["pitcher_name"] = re.sub(
                r"\s*\([^)]*\)\s*$", "", raw_name).strip()
        matched.append(entry)

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


def _prior_is_usable(prior: dict | None, force: bool = False) -> bool:
    """Is this prior-season row substantial enough to lean on?

    Both bars matter and they do different jobs: MIN_PRIOR_BF says the
    RATE is estimated from enough plate appearances, MIN_PRIOR_STARTS says
    the pitcher was actually a STARTER. A reliever with 250 BF across 60
    relief appearances clears the first and fails the second, which is the
    point — prior-season volume must never launder a reliever into a
    starter (AUDIT A-007).

    `force` bypasses only the USE_PRIOR_SEASON flag, never the substance
    bars. It exists for the shadow path (A-046), which must price the
    counterfactual while the flag is off.
    """
    if not (USE_PRIOR_SEASON or force) or not prior:
        return False
    return (float(prior.get("prior_bf") or 0) >= MIN_PRIOR_BF
            and int(prior.get("prior_starts") or 0) >= MIN_PRIOR_STARTS)


def _compute_pitcher_stats(statcast_df: pd.DataFrame, pitcher_id: int,
                           home_team: str | None = None,
                           target_date: date | None = None,
                           prior: dict | None = None,
                           force_prior: bool = False) -> dict:
    """Compute season K% and BF stats for a pitcher from Statcast data.

    `prior` is that pitcher's row from the previous season's sidecar
    (tools/build_prior_season.py), or None. It widens the history window
    for the RATE and, when the current season is too thin to say, for the
    workload — see docs/PRIOR_SEASON_SCOPE.md. It never relaxes the role
    gate's verdict on a pitcher whose current usage says reliever.

    `force_prior=True` treats the prior as if USE_PRIOR_SEASON were on
    (shadow path only); the substance bars still apply.
    """
    completed = statcast_df[statcast_df["events"].notna()]
    p = completed[completed["pitcher"] == pitcher_id]
    use_prior = _prior_is_usable(prior, force=force_prior)

    if p.empty and not use_prior:
        return {"season_k_pct": None, "bf_mean": None, "total_bf": 0,
                "eff_bf": 0.0, "is_startable": False,
                "skip_reason": "no Statcast history"}

    total_bf = len(p)
    ks = int(p["events"].isin(["strikeout", "strikeout_double_play"]).sum())

    # Widen the sample before shrinking, rather than shrinking a thin
    # current season all the way to the league mean. K rate is the part of
    # a pitcher's line that survives the offseason: r = 0.73 (2024->2025)
    # and 0.68 (2025->2026), and on the recovered starts prior-season rate
    # is unbiased -- 0.226 predicted against 0.226 actual. Workload is not
    # (r = 0.40 / 0.51) and is handled separately below.
    eff_bf = float(total_bf)
    eff_ks = float(ks)
    if use_prior:
        eff_bf += PRIOR_SEASON_WEIGHT * float(prior["prior_bf"])
        eff_ks += PRIOR_SEASON_WEIGHT * float(prior["prior_ks"])

    raw_k_pct = eff_ks / eff_bf if eff_bf > 0 else LEAGUE_K_RATE

    shrunk_k_pct = (eff_bf * raw_k_pct + SHRINKAGE_BF * LEAGUE_K_RATE) / (
        eff_bf + SHRINKAGE_BF
    )

    # --- Workload / role (see AUDIT A-007) ---------------------------
    # This used to fall back to bf_mean = 21.1 (league-average STARTER)
    # whenever a pitcher lacked 3 starter-length games. That is the most
    # bullish possible assumption, and a bug that inflates projections
    # gets selected straight INTO the bet list, because inflated
    # projections are what the edge filter hunts for. On 2026-08-05 it
    # priced a reliever (40 appearances averaging 7 BF) as a 21.1-BF
    # starter, manufacturing a 17pp phantom edge and the day's largest
    # stake. Never default; establish the role from history or skip.
    game_bf = p.groupby("game_pk").size()
    if "game_date" in p.columns:
        order = p.groupby("game_pk")["game_date"].min().sort_values().index
        game_bf = game_bf.reindex(order)
    starter_games = game_bf[game_bf >= 9]

    n_appearances = int(len(game_bf))
    recent = game_bf.tail(ROLE_LOOKBACK_GAMES)
    recent_typical_bf = float(recent.quantile(0.60)) if len(recent) else 0.0

    is_startable = (
        n_appearances >= MIN_APPEARANCES_TO_PRICE
        and recent_typical_bf >= STARTER_TYPICAL_BF
    )
    skip_reason = None
    if n_appearances < MIN_APPEARANCES_TO_PRICE:
        skip_reason = f"only {n_appearances} appearance(s) in cache"
    elif recent_typical_bf < STARTER_TYPICAL_BF:
        skip_reason = (
            f"relief/short-outing usage — typical recent outing "
            f"{recent_typical_bf:.0f} BF (< {STARTER_TYPICAL_BF} needed)"
        )

    # A prior season can establish the ROLE the current one is too short
    # to show -- but only when the current season does not contradict it.
    # Relief-length recent outings veto regardless of last year: that is
    # exactly the A-007 case, and prior volume must not be able to
    # overturn what this season is plainly showing.
    if not is_startable and use_prior:
        contradicts = (n_appearances > 0
                       and recent_typical_bf < STARTER_TYPICAL_BF)
        if not contradicts:
            is_startable = True
            skip_reason = None

    # Real history only. No league default, ever.
    #
    # Workload does NOT travel across seasons the way rate does, so the
    # prior season is used here far more cautiously: his own p25 outing,
    # not his mean. Measured on the recovered starts, the prior mean
    # overstates batters faced by 5+ on 6.5-8.9% of them, and at ~2.45
    # points of P(over) per batter that is a 13-16 point phantom OVER
    # edge -- A-007 magnitude, landing precisely where the edge filter
    # looks hardest. The p25 cuts that to 2.1-2.9% on season debuts.
    #
    # With 1-2 outings already this season, blending the two beats either
    # alone on BOTH average error and that upper tail, in both year pairs
    # tested. Not p10: under-projecting by 3 BF manufactures phantom
    # UNDER edges just as surely. A-007 ran OVER by accident of that
    # particular bug, not by law.
    if len(starter_games) >= 3:
        bf_mean = float(starter_games.mean())
    elif use_prior:
        prior_p25 = float(prior["prior_bf_p25"])
        if n_appearances > 0:
            bf_mean = (PRIOR_WORKLOAD_BLEND * float(game_bf.mean())
                       + (1.0 - PRIOR_WORKLOAD_BLEND) * prior_p25)
        else:
            bf_mean = prior_p25
    else:
        bf_mean = float(game_bf.mean())

    zone_pct = None
    all_pitches = statcast_df[statcast_df["pitcher"] == pitcher_id]
    if "zone" in all_pitches.columns:
        zone_valid = all_pitches[all_pitches["zone"].notna()]
        if len(zone_valid) >= 50:
            in_zone = zone_valid["zone"].isin(range(1, 10)).sum()
            zone_pct = float(in_zone / len(zone_valid))

    from features.t2_candidates import TEAM_TIMEZONES
    eastward_tz = 0.0
    days_since_last = None
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
                if target_date is not None:
                    last_date = pd.to_datetime(
                        last_games.iloc[-1]["game_date"]
                    ).date()
                    days_since_last = (target_date - last_date).days

    return {
        "season_k_pct": float(shrunk_k_pct),
        "season_k_pct_raw": float(raw_k_pct),
        "bf_mean": bf_mean,
        "total_bf": int(total_bf),
        # The sample the rate was actually estimated from. Equals total_bf
        # when no prior season is used, and it -- not total_bf -- is what
        # the caller's data-sufficiency gate must read.
        "eff_bf": float(eff_bf),
        "used_prior_season": bool(use_prior),
        "n_starts": int(len(starter_games)),
        "n_appearances": n_appearances,
        "recent_typical_bf": recent_typical_bf,
        "is_startable": bool(is_startable),
        "skip_reason": skip_reason,
        "zone_pct": zone_pct,
        "eastward_tz": float(eastward_tz),
        "days_since_last": days_since_last,
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


def _compute_team_k_rate(statcast_df: pd.DataFrame, team: str) -> float | None:
    """As-of shrunk K% for a whole team's batters. None if unknown.

    The pre-lineup fallback. It replaces `[LEAGUE_K_RATE] * 9`, a constant
    with ZERO variance that fired on 31.7% of the logged board (40 of 126
    rows) and threw away everything we know about the opponent.

    Measured out-of-sample RMSE on total K, 2024-2026, common n = 9,894:

        opponent representation   24->25    25->24   24+25->26
        real nine (confirmed)     2.2280    2.2355     2.2516
        team as-of K% (this)      2.2419    2.2510     2.2618
        constant 0.225 (was)      2.2720    2.3021     2.2755

    The team rate recovers 68.5% / 76.8% / 57.0% of what a confirmed lineup
    is worth, in every temporal direction. corr(nine, team) = 0.845;
    sd(nine) 0.0156, sd(team) 0.0190, sd(constant) 0.0000.

    Same empirical-Bayes shrinkage as the per-batter path so live inputs
    match the distribution the model was fit on. Returns None rather than
    substituting a league average -- the caller decides, and A-007's rule is
    that a fabricated input manufactures edge and gets selected into the bet
    list.
    """
    if statcast_df.empty or not team:
        return None
    completed = statcast_df[statcast_df["events"].notna()]
    if completed.empty:
        return None
    # The batting side is whichever half-inning the team is not fielding.
    is_home_bat = (completed.get("home_team") == team) & (
        completed.get("inning_topbot") == "Bot")
    is_away_bat = (completed.get("away_team") == team) & (
        completed.get("inning_topbot") == "Top")
    pa = completed[is_home_bat | is_away_bat]
    if pa.empty:
        return None
    ks = pa["events"].isin(["strikeout", "strikeout_double_play"]).sum()
    return shrink_rate(float(ks), float(len(pa)), BATTER_K_PSEUDO_BF)


def _primary_for(pred: dict, all_plays: list[dict]) -> tuple[float, str | None]:
    """(units, side) of this pitcher's PRIMARY play, for the ladder gate.

    A-047: this lookup read `play.get("pick_side")` — a key primary
    plays never carry (they carry `best_side`; `pick_side` is written
    only on ladder plays and in the CSV writer). The side was therefore
    always None, `gate_open` in models/ladder.py never opened, and every
    ladder rung was silently rejected from 2026-08-05 (commit 35bd8be6)
    onward — verified as zero rungs past the gate across six slates of
    sidecars. One word, 19 days of a dead subsystem.
    """
    for play in all_plays:
        if (play.get("pitcher_id") == pred.get("pitcher_id")
                and play.get("pick_type") == "primary"):
            return (play.get("units_risked", 0.0), play.get("best_side"))
    return 0.0, None


def _lineup_inputs(entry: dict, statcast_df: pd.DataFrame):
    """Per-batter K% inputs for this start: (lineup_k_pcts, lineup_source).

    Returns (None, None) when neither a posted lineup nor opponent team
    history exists — the caller must skip rather than fabricate (A-007).
    Factored out so the shadow path (A-046) prices a refused pitcher with
    EXACTLY the inputs production would have used, not a reimplementation.
    """
    lineup = entry.get("lineup", [])
    if lineup and not statcast_df.empty:
        return (_compute_batter_k_rates(statcast_df, lineup),
                entry.get("lineup_source", "confirmed"))
    # No lineup posted yet. Use the opponent TEAM's as-of K% rather than a
    # league constant: it recovers 57-77% of a confirmed lineup's value
    # out-of-sample in all three temporal directions, where the constant
    # recovers none by construction (zero variance).
    team_k = _compute_team_k_rate(statcast_df, entry.get("opponent_team"))
    if team_k is None:
        return None, None
    return [team_k] * 9, "projected"


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


def _write_slate_sidecar(game_date: str, predictions: list,
                         shadow_prior: list | None = None,
                         skipped: list | None = None) -> None:
    """Persist the full evaluated slate to data/slates/YYYY-MM-DD.json.

    Every analyzed pitcher — full P(K=k) distribution, expected K/BF,
    and EVERY evaluated ladder rung (bet or passed with reason) — so the
    dashboard can show the whole board, not just the surviving picks.

    `shadow_prior` (A-046) is the list of pitchers production REFUSED but
    the prior-season window would recover, priced counterfactually. They
    are written to a separate `shadow_prior_pitchers` section — never
    into `pitchers`, which drives the board.
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
            "start_time_utc": str(pred.get("start_time_utc", "") or ""),
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
            # A-046 shadow columns: counterfactual raw P(over) with the
            # hook mixture on / prior-season window on. Diagnostic only —
            # nothing prices or stakes off them.
            "p_over_hookmix": (round(float(pred["p_over_hookmix"]), 4)
                               if pred.get("p_over_hookmix") is not None else None),
            "p_over_prior": (round(float(pred["p_over_prior"]), 4)
                             if pred.get("p_over_prior") is not None else None),
            "p_over_candidate": (round(float(pred["p_over_candidate"]), 4)
                                 if pred.get("p_over_candidate") is not None else None),
            "p_over_re": (round(float(pred["p_over_re"]), 4)
                          if pred.get("p_over_re") is not None else None),
            # H1/H2 (A-049): the day's own market movement for this arm.
            "h1_open_line": pred.get("h1_open_line"),
            "h1_open_fair_over": pred.get("h1_open_fair_over"),
            "h2_line_move": pred.get("h2_line_move"),
            "h2_fair_move": pred.get("h2_fair_move"),
            "h2_n_captures": pred.get("h2_n_captures"),
            # A-050: pre-game weather forecast for the venue (or null).
            "wx": pred.get("wx"),
            "k_dist": [round(float(x), 6) for x in (k_dist if k_dist is not None else [])],
            "ladder": rungs,
        })

    out_path = SLATES_DIR / f"{game_date}.json"

    # MERGE, don't replace. A re-price later in the day sees a smaller
    # board, because DK pulls a pitcher's market once his game starts:
    # the 10:30 run priced 20, the 14:10 re-price saw 14. Overwriting
    # would silently drop the 6 whose games had begun, and those rows
    # are the only record the model log has for them -- roughly a third
    # of the day's testable predictions, thrown away every re-run.
    #
    # Newest wins per pitcher (a lineup-lock price beats a projected
    # one); pitchers absent from this run keep their earlier entry.
    shadow_rows = list(shadow_prior or [])
    skipped_rows = list(skipped or [])
    carried = 0
    if out_path.exists():
        try:
            with open(out_path, encoding="utf-8") as f:
                prior = json.load(f)
            if not prior.get("reconstructed"):
                fresh = {p.get("pitcher_id") for p in pitchers}
                for p in prior.get("pitchers", []):
                    if p.get("pitcher_id") not in fresh:
                        pitchers.append(p)
                        carried += 1
                # Same newest-wins merge for the shadow section — a
                # re-price later in the day must not drop the morning's
                # shadow rows (they are the only record for those arms).
                fresh_shadow = {p.get("pitcher_id") for p in shadow_rows}
                # A pitcher who graduated to the real board (lineup posted,
                # role established) must not linger in the shadow section.
                for p in prior.get("shadow_prior_pitchers", []):
                    if (p.get("pitcher_id") not in fresh_shadow
                            and p.get("pitcher_id") not in fresh):
                        shadow_rows.append(p)
                # Same for the skip ledger, keyed by normalized name (an
                # unmatched prop has no pitcher_id). Fresh run wins; a
                # skip retires once the name is priced.
                priced_names = {_normalize_name(p.get("pitcher_name") or "")
                                for p in pitchers}
                fresh_skip = {_normalize_name(s.get("pitcher_name") or "")
                              for s in skipped_rows}
                for s in prior.get("skipped", []):
                    n = _normalize_name(s.get("pitcher_name") or "")
                    if n and n not in fresh_skip and n not in priced_names:
                        skipped_rows.append(s)
        except (OSError, ValueError) as exc:
            print(f"  (could not read prior sidecar to merge: {exc})")

    payload = {
        "date": game_date,
        "generated_at": datetime.now(UTC).isoformat(),
        "reconstructed": False,
        "pitchers": pitchers,
        "shadow_prior_pitchers": shadow_rows,
        # Every DK prop the run did NOT price, with its reason — the
        # watchdog reconciles the intraday capture against
        # pitchers + shadow + this list, so a silently dropped name
        # (A-038) is a red check by the next morning.
        "skipped": skipped_rows,
    }
    _write_json_atomic(out_path, payload)
    print(f"  Slate sidecar written: {out_path} ({len(pitchers)} pitchers"
          + (f", {carried} carried from an earlier run" if carried else "")
          + ")")


# A-050: phrases that ANNOUNCE a limit. Deliberately narrow — "threw 95
# pitches last time out" must never match, so every pattern anchors on
# limit/cap/build-up phrasing rather than a bare number-of-pitches.
_LIMIT_PATTERNS = [
    re.compile(r"pitch (?:limit|count) (?:of |around |near |about )?(\d{2,3})", re.I),
    re.compile(r"limit(?:ed)? (?:him )?to (?:about |around |roughly |~)?(\d{2,3})\s*(?:-\s*(\d{2,3}))?\s*pitches", re.I),
    re.compile(r"capped at (?:about |around |roughly |~)?(\d{2,3})\s*pitches", re.I),
    re.compile(r"(?:around|about|roughly|~)\s*(\d{2,3})\s*(?:-\s*(\d{2,3}))?\s*pitches", re.I),
]
_LIMIT_EXCLUDE = re.compile(
    r"(threw|thrown|toss(?:ed)?|last (?:time|outing|start)|previous)", re.I)


def _match_pitch_limit(note: str) -> tuple[int, str] | None:
    """(suggested_limit, excerpt) from one beat note, or None.

    Pure so the regex layer is testable: false positives here would put
    a phantom cap in front of the operator daily, and a miss is just a
    note the operator reads themselves.
    """
    if not note:
        return None
    for pat in _LIMIT_PATTERNS:
        m = pat.search(note)
        if not m:
            continue
        window = note[max(0, m.start() - 40):m.end() + 40]
        if _LIMIT_EXCLUDE.search(window):
            continue
        nums = [int(x) for x in m.groups() if x]
        limit = min(nums)              # a range suggests its floor
        if not (30 <= limit <= 130):
            continue
        return limit, window.strip().replace("\n", " ")[:160]
    return None


def _scan_pitch_limit_notes(reg_games: list[dict], iso_date: str) -> int:
    """Parse probable-pitcher notes for announced pitch limits and write
    SUGGESTIONS (never the live cap) to data/pitch_limit_suggestions.csv.

    Union-merged by (date, pitcher_id), atomic write. The operator
    reviews and copies confirmed rows into manual_pitch_limits.csv —
    the same trust boundary as manual odds overrides: a parser guess
    must not become a pricing input without a human seeing it.
    """
    suggestions = []
    now = datetime.now(UTC).isoformat()
    for g in reg_games:
        for side in ("home", "away"):
            note = g.get(f"{side}_probable_note") or ""
            pid = g.get(f"{side}_probable_id")
            pname = g.get(f"{side}_probable_name") or ""
            if not note or not pid:
                continue
            hit = _match_pitch_limit(note)
            if hit is None:
                continue
            limit, excerpt = hit
            suggestions.append({
                "date": iso_date,
                "game_pk": g.get("game_pk", ""),
                "pitcher_id": pid,
                "pitcher_name": pname,
                "suggested_limit": limit,
                "note_excerpt": excerpt,
                "captured_at": now,
            })
            print(f"    NOTE {pname}: suggested pitch limit {limit} "
                  f"(\"{excerpt[:70]}...\")")

    if not suggestions:
        return 0

    path = Path(__file__).parent.parent / "data" / "pitch_limit_suggestions.csv"
    fields = ["date", "game_pk", "pitcher_id", "pitcher_name",
              "suggested_limit", "note_excerpt", "captured_at"]
    existing = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    merged = {(r.get("date"), str(r.get("pitcher_id"))): r for r in existing}
    for s in suggestions:
        merged[(s["date"], str(s["pitcher_id"]))] = s
    rows = sorted(merged.values(), key=lambda r: (r["date"], str(r["pitcher_name"])))

    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(suggestions)


def _p5_pitches(statcast_df: pd.DataFrame, pitcher_id: int) -> float | None:
    """Mean pitch count over the pitcher's last 5 cached games.

    Serve-time mirror of features/asof.py's p5_pitches (the cache ends
    yesterday, so every game in it is strictly prior). Ordered by
    (game_date, game_pk) — never game_pk alone.
    """
    if statcast_df.empty or not pitcher_id:
        return None
    p = statcast_df[statcast_df["pitcher"] == pitcher_id]
    if p.empty or "game_date" not in p.columns:
        return None
    per_game = p.groupby("game_pk").agg(
        n=("pitcher", "size"), game_date=("game_date", "first")
    ).reset_index().sort_values(["game_date", "game_pk"])
    if per_game.empty:
        return None
    return float(per_game["n"].tail(5).mean())


def _load_pitch_limits(iso_date: str) -> dict:
    """Operator-entered pitch limits for a date: {pitcher_id_str: limit}.

    Source: data/manual_pitch_limits.csv (date, game_pk, pitcher_name,
    pitcher_id, pitch_limit, source, notes).
    """
    path = Path(__file__).parent.parent / "data" / "manual_pitch_limits.csv"
    limits = {}
    if not path.exists():
        return limits
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("date") != iso_date:
                continue
            try:
                limits[str(row.get("pitcher_id", "")).strip()] = int(
                    float(row["pitch_limit"])
                )
            except (ValueError, TypeError, KeyError):
                continue
    return limits


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

    # A-050: pre-game weather forecast per venue (clients existed since
    # Phase 0 with zero callers). Capture-only — rides the sidecar as a
    # diagnostic; None stays None (A-007).
    wx_by_gamepk = {}
    try:
        from data.game_context import game_weather
        for g in reg_games:
            wx = game_weather(g.get("venue_id"), g.get("game_date") or "")
            if wx is not None:
                wx_by_gamepk[g.get("game_pk")] = wx
        if wx_by_gamepk:
            print(f"  weather forecasts: {len(wx_by_gamepk)}/{len(reg_games)} venues")
    except Exception as exc:
        print(f"  (weather capture failed: {exc})")

    # A-050: scan the probable-pitcher beat notes for announced pitch
    # limits — the information data/manual_pitch_limits.csv has waited
    # on since birth (A-024a). SUGGESTIONS ONLY: the serve-time cap
    # still reads the manual CSV, which the operator confirms by hand.
    try:
        n_sugg = _scan_pitch_limit_notes(reg_games, game_date)
        if n_sugg:
            print(f"  !! {n_sugg} pitch-limit suggestion(s) written to "
                  f"data/pitch_limit_suggestions.csv — review and confirm "
                  f"into data/manual_pitch_limits.csv")
    except Exception as exc:
        print(f"  (pitch-limit note scan failed: {exc})")

    # 2. Fetch DK odds — primary O/U + milestone alt lines
    print("\n[2/8] Fetching DraftKings strikeout props...")
    try:
        dk_props = fetch_dk_strikeout_props(iso_date=game_date)
    except Exception as exc:
        print(f"  DK fetch failed: {exc}")
        return []

    today_props = [p for p in dk_props if p.get("date") == game_date]
    print(f"  {len(today_props)} pitcher O/U props for {game_date}")

    # A-049 H1/H2: persist this capture — the sidecar's newest-wins merge
    # overwrites the morning price at every reprice, so without this the
    # OPEN is never durably archived (the input that can't be backfilled).
    try:
        from features.market import record_intraday_snapshot, load_intraday, movement_features
        n_rec = record_intraday_snapshot(game_date, today_props)
        intraday = load_intraday(game_date, _normalize_name)
        print(f"  intraday archive: +{n_rec} rows this capture")
    except Exception as exc:
        print(f"  (intraday odds archive failed: {exc})")
        intraday = {}
        movement_features = None

    # A-050: the OPEN game lines (moneyline / run line / total) — the
    # close job snapshots them too; this captures the morning state.
    try:
        from tools.closing_odds import capture_game_lines
        n_gl = capture_game_lines(game_date, datetime.now(UTC).isoformat())
        if n_gl:
            print(f"  game lines captured: {n_gl} rows")
    except Exception as exc:
        print(f"  (game-lines capture failed: {exc})")

    snap = [p for p in today_props if p.get("odds_source") == "snapshot"]
    if snap:
        ages = [p.get("snapshot_age_hours") or 0 for p in snap]
        print(f"  !! {len(snap)} of {len(today_props)} priced from a SNAPSHOT "
              f"({min(ages):.1f}-{max(ages):.1f}h old), not live prices.")

    if not today_props:
        print("  No props available (slate may not be posted yet).")
        return []

    alt_lines_by_pitcher = {}
    if enable_ladder:
        print("  Fetching milestone/alt lines...")
        try:
            dk_alts = fetch_dk_strikeout_alts(iso_date=game_date)
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

    # Prior-season sidecar, keyed by pitcher id. A precomputed summary,
    # not a second season of pitches: the worker prices six times a day
    # and loading another ~750K rows per run is not affordable.
    #
    # Loaded even with USE_PRIOR_SEASON off: the shadow path (A-046)
    # prices the counterfactual every day so the 2-week shadow the
    # promotion decision requires actually accumulates. The flag only
    # controls whether the PRODUCTION price uses it.
    prior_by_id = {}
    try:
        from tools.build_prior_season import load_prior_season
        prior_df = load_prior_season(d.year - 1)
        if prior_df.empty:
            print(f"  !! prior-season sidecar for {d.year - 1} is missing; "
                  f"prior-season {'pricing' if USE_PRIOR_SEASON else 'shadow'} "
                  f"falls back to current-season-only")
        else:
            prior_by_id = {int(r["pitcher"]): r
                           for r in prior_df.to_dict("records")}
            print(f"  prior season {d.year - 1}: {len(prior_by_id)} pitchers"
                  + ("" if USE_PRIOR_SEASON else " (shadow only — flag off)"))
    except Exception as exc:
        print(f"  !! prior-season sidecar load failed: {exc} — shadow degraded")

    # Leash-input context: operator pitch limits for today, and each
    # team's relief usage yesterday (both feed Stage A's leash).
    pitch_limits = _load_pitch_limits(game_date)
    if pitch_limits:
        print(f"  {len(pitch_limits)} manual pitch limit(s) for {game_date}")

    relief_yesterday = {}
    if not statcast_df.empty:
        yesterday = pd.Timestamp(d) - pd.Timedelta(days=1)
        relief_yesterday = {
            team: n
            for (team, dt), n in team_relief_pitches_by_date(statcast_df).items()
            if dt == yesterday
        }

    # 5. Load model and run predictions
    print("\n[5/8] Running predictions...")
    predictor = StrikeoutPredictor()
    predictor.load_models()

    # A-049: the candidate Stage B (core + p5_pitches + is_home — the
    # first re-gauntlet KEEPs) rides shadow-only. Absent pickle = no
    # shadow column, never a crash.
    candidate_b = None
    try:
        from models.stage_b_rate import StageB as _StageB
        _cand_path = Path(__file__).parent.parent / "models" / "stage_b_candidate.pkl"
        if _cand_path.exists():
            candidate_b = _StageB(extra_features=["p5_pitches", "is_home"])
            candidate_b.load(_cand_path)
            print("  candidate Stage B loaded (shadow only)")
    except Exception as exc:
        print(f"  (candidate Stage B unavailable: {exc})")
        candidate_b = None

    predictions = []
    shadow_prior_rows = []
    skipped_rows = []

    def _record_skip(name, pid, reason):
        """Every DK prop must be accounted for: priced, shadow-priced,
        or skipped WITH REASON in the sidecar. A prop that silently
        matches nothing is the A-038 failure mode, and it survived two
        slates precisely because the only trace was one stdout line on
        a scheduled run. The watchdog reconciles the intraday capture
        against this ledger daily."""
        skipped_rows.append({"pitcher_name": name,
                             "pitcher_id": pid,
                             "reason": reason})

    # Props whose name matched no MLB probable (or an ambiguous one) —
    # they never reach `matched` and would otherwise vanish untracked.
    matched_names = {_normalize_name(m.get("pitcher_name", "")) for m in matched}
    for p in today_props:
        n = _normalize_name(p.get("pitcher_name", ""))
        if n and n not in matched_names:
            _record_skip(p.get("pitcher_name", ""), None,
                         "no MLB probable matched (A-038 class)")

    def _shadow_prior_price(entry, stats_prior, skip_reason):
        """Price a production-refused pitcher under the prior-season window.

        A-046: with USE_PRIOR_SEASON off, the pitchers the feature would
        RECOVER never reach the board, so nothing accumulates the shadow
        evidence the promotion decision needs. This prices them with the
        exact production inputs and stashes the result for the sidecar's
        shadow_prior_pitchers section — never for the board, never for a
        bet.
        """
        if stats_prior is None or stats_prior.get("season_k_pct") is None:
            return
        if stats_prior.get("eff_bf", 0) < 50 or not stats_prior.get("is_startable"):
            return
        lineup_k_pcts, lineup_source = _lineup_inputs(entry, statcast_df)
        if lineup_k_pcts is None:
            return
        sfeatures = {
            "a3_season_k_pct_shrunk": stats_prior["season_k_pct"],
            "c1_bf_mean": stats_prior["bf_mean"],
            "c10_il_return": bool(
                stats_prior.get("days_since_last") is not None
                and stats_prior["days_since_last"] > IL_GAP_DAYS),
            "c11_pitch_limit": pitch_limits.get(str(entry.get("pitcher_id"))),
        }
        try:
            line = float(entry.get("line", 5.5))
            sres = predictor.predict(sfeatures, lineup_k_pcts=lineup_k_pcts,
                                     lines=[line])
            nv = no_vig_fair_prob(entry.get("over_odds", "-110"),
                                  entry.get("under_odds", "-110"))
        except Exception as exc:
            print(f"      (prior shadow failed for "
                  f"{entry.get('pitcher_name')}: {exc})")
            return
        shadow_prior_rows.append({
            "pitcher_id": entry.get("pitcher_id"),
            "pitcher_name": entry.get("pitcher_name"),
            "pitcher_team": entry.get("pitcher_team"),
            "opponent_team": entry.get("opponent_team"),
            "is_home": bool(entry.get("is_home")),
            "game_pk": entry.get("game_pk"),
            "start_time_utc": str(entry.get("start_time_utc", "") or ""),
            "line": entry.get("line"),
            "over_odds": str(entry.get("over_odds", "")),
            "under_odds": str(entry.get("under_odds", "")),
            "lineup_source": lineup_source,
            "expected_k": round(float(sres["expected_k"]), 2),
            "expected_bf": round(float(sres["expected_bf"]), 2),
            "p_over_raw": round(float(sres["per_line_raw"][line]), 4),
            "fair_over": round(float(nv["fair_over"]), 4),
            "recovered_reason": skip_reason or "",
        })
        print(f"      (prior-season shadow priced "
              f"{entry.get('pitcher_name')}: P(over)="
              f"{sres['per_line_raw'][line]:.1%})")

    for entry in matched:
        pitcher_id = entry.get("pitcher_id")
        pitcher_name = entry.get("pitcher_name", "???")
        dk_line = float(entry.get("line", 5.5))
        over_odds = entry.get("over_odds", "-110")
        under_odds = entry.get("under_odds", "-110")

        home_team = entry.get("pitcher_team") if entry.get("is_home") else entry.get("opponent_team")
        if not statcast_df.empty and pitcher_id:
            stats = _compute_pitcher_stats(
                statcast_df, pitcher_id, home_team=home_team, target_date=d,
                prior=prior_by_id.get(int(pitcher_id)),
            )
        else:
            stats = {"season_k_pct": None, "bf_mean": None, "total_bf": 0,
                     "eff_bf": 0.0, "is_startable": False,
                     "skip_reason": "no Statcast data loaded"}

        # A-046 shadow: the same pitcher's stats as if USE_PRIOR_SEASON
        # were ON. Computed before the gates so a refused pitcher still
        # produces shadow evidence. With the flag already on, production
        # IS the prior path and the shadow is just the production stats.
        stats_prior = stats
        if not USE_PRIOR_SEASON and not statcast_df.empty and pitcher_id:
            prior_row = prior_by_id.get(int(pitcher_id))
            if prior_row is not None:
                stats_prior = _compute_pitcher_stats(
                    statcast_df, pitcher_id, home_team=home_team,
                    target_date=d, prior=prior_row, force_prior=True,
                )

        # Gate on the sample the rate was actually estimated from. Without
        # a prior season eff_bf IS total_bf, so this is unchanged from the
        # original 50-BF rule; with one, a pitcher two starts into the
        # season is no longer refused over history sitting on disk.
        if stats["season_k_pct"] is None or stats.get("eff_bf", 0) < 50:
            detail = f"{stats['total_bf']} BF"
            if stats.get("used_prior_season"):
                detail += f", {stats.get('eff_bf', 0):.0f} effective"
            print(f"    {pitcher_name}: insufficient data ({detail}), skipping")
            _record_skip(pitcher_name, pitcher_id,
                         f"insufficient data ({detail})")
            _shadow_prior_price(entry, stats_prior,
                                f"insufficient data ({detail})")
            continue

        # Role gate: never price a pitcher whose workload we can't
        # establish as a starter's. A bad workload input manufactures
        # edge and the staking engine then concentrates on it.
        if not stats.get("is_startable", False):
            print(f"    {pitcher_name}: SKIP — {stats.get('skip_reason')}")
            _record_skip(pitcher_name, pitcher_id, stats.get("skip_reason"))
            _shadow_prior_price(entry, stats_prior, stats.get("skip_reason"))
            continue

        lineup_k_pcts, lineup_source = _lineup_inputs(entry, statcast_df)
        if lineup_k_pcts is None:
            # Refuse rather than fabricate. A league average here is an
            # invented input, and the edge filter selects invented inputs
            # into the bet list precisely because they flatter the
            # projection (AUDIT A-007).
            print(f"    {pitcher_name}: SKIP — no lineup and no opponent "
                  f"batting history for {entry.get('opponent_team')}")
            _record_skip(pitcher_name, pitcher_id,
                         f"no lineup and no opponent batting history "
                         f"for {entry.get('opponent_team')}")
            continue

        lineup = entry.get("lineup", [])
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

        # Leash inputs (Phase 12): long-layoff flag from the cache,
        # operator-entered pitch limits, and prior-day bullpen usage.
        il_return = (
            stats.get("days_since_last") is not None
            and stats["days_since_last"] > IL_GAP_DAYS
        )
        pitch_limit = pitch_limits.get(str(pitcher_id))
        team_abbr = entry.get("pitcher_team", "")
        bp_heavy = relief_yesterday.get(team_abbr, 0) >= BP_HEAVY_PITCHES
        if il_return or pitch_limit or bp_heavy:
            flags = [f for f, on in [
                (f"IL return ({stats.get('days_since_last')}d layoff)", il_return),
                (f"pitch limit {pitch_limit}", pitch_limit),
                (f"bullpen heavy ({relief_yesterday.get(team_abbr, 0)} relief pitches yday)", bp_heavy),
            ] if on]
            print(f"    {pitcher_name}: leash flags — {', '.join(flags)}")

        features = {
            "a3_season_k_pct_shrunk": stats["season_k_pct"],
            "a3_season_k_pct_raw": stats.get("season_k_pct_raw", stats["season_k_pct"]),
            "c1_bf_mean": stats["bf_mean"],
            "c10_il_return": bool(il_return),
            "c11_pitch_limit": pitch_limit,
            "c12_bp_heavy": bool(bp_heavy),
            "a9_zone_pct": stats.get("zone_pct"),
            "f1_eastward_tz": stats.get("eastward_tz", 0.0),
            "b14_n_rookies": n_rookies,
        }

        lines_to_check = [dk_line]
        result = predictor.predict(features, lineup_k_pcts=lineup_k_pcts, lines=lines_to_check)

        # --- A-046 shadow columns. Neither touches the served price. ----
        # p_over_hookmix: raw P(over) with the A-042 hook mixture ON,
        # whatever the flag says. Identical inputs, identical Stage B —
        # only Stage A's family differs, so the column isolates exactly
        # the promotion question.
        try:
            hook_res = predictor.predict(
                features, lineup_k_pcts=lineup_k_pcts, lines=lines_to_check,
                use_hook_mixture=True)
            p_over_hookmix = float(hook_res["per_line_raw"][dk_line])
        except Exception as exc:
            print(f"      (hook-mixture shadow failed: {exc})")
            p_over_hookmix = None

        # p_over_prior: raw P(over) with the prior-season window ON. When
        # the widened stats are identical (no usable prior, or flag
        # already on) this equals the production raw and the report reads
        # it as "feature changes nothing here".
        p_over_prior = None
        try:
            if (stats_prior is not stats
                    and stats_prior.get("season_k_pct") is not None
                    and (stats_prior.get("season_k_pct") != stats.get("season_k_pct")
                         or stats_prior.get("bf_mean") != stats.get("bf_mean"))):
                pfeatures = dict(features)
                pfeatures["a3_season_k_pct_shrunk"] = stats_prior["season_k_pct"]
                pfeatures["c1_bf_mean"] = stats_prior["bf_mean"]
                prior_res = predictor.predict(
                    pfeatures, lineup_k_pcts=lineup_k_pcts, lines=lines_to_check)
                p_over_prior = float(prior_res["per_line_raw"][dk_line])
            else:
                p_over_prior = float(result["per_line_raw"][dk_line])
        except Exception as exc:
            print(f"      (prior-season shadow failed: {exc})")

        # p_over_re (A-051): the production distribution re-compounded
        # with the mean-preserving per-start rate random effect at the
        # cross-season sigma*. Shadow only.
        p_over_re = None
        try:
            from models.compound import (
                compound_k_distribution_re as _ckdre,
                prob_k_geq as _pkg_re, RATE_RE_SIGMA)
            re_kd = _ckdre(result["bf_dist"], result["per_batter_probs"],
                           RATE_RE_SIGMA)
            p_over_re = float(_pkg_re(re_kd, dk_line))
        except Exception as exc:
            print(f"      (rate-RE shadow failed: {exc})")

        # p_over_candidate: production Stage A distribution, candidate
        # Stage B rates (core + p5_pitches + is_home). Shadow only.
        p_over_candidate = None
        if candidate_b is not None:
            try:
                from models.compound import (
                    compound_k_distribution as _ckd, prob_k_geq as _pkg)
                cand_extras = {
                    "p5_pitches": _p5_pitches(statcast_df, pitcher_id),
                    "is_home": 1.0 if entry.get("is_home") else 0.0,
                }
                cand_pb = candidate_b.predict_per_batter_k_prob(
                    stats["season_k_pct"], lineup_k_pcts, n_max=40,
                    extras=cand_extras)
                cand_kd = _ckd(result["bf_dist"], cand_pb)
                p_over_candidate = float(_pkg(cand_kd, dk_line))
            except Exception as exc:
                print(f"      (candidate shadow failed: {exc})")

        model_prob_over = result["per_line"][dk_line]
        # A-008: an unposted lineup is real uncertainty (~5pp on P(over)),
        # so it costs edge rather than being ignored.
        lineup_confirmed = lineup_source == "confirmed"
        edge_info = compute_edge(
            model_prob_over, over_odds, under_odds,
            lineup_confirmed=lineup_confirmed,
        )
        strength = pick_strength(edge_info["best_edge"], edge_info["threshold"])

        # H1/H2 market movement (diagnostic + screen input; prices
        # nothing). Uses this pitcher's own capture series for the day.
        mkt = (movement_features(intraday.get(_normalize_name(pitcher_name)))
               if movement_features else {})
        wx = wx_by_gamepk.get(entry.get("game_pk"))

        entry_result = {
            **entry,
            "model_prob_over": model_prob_over,
            "model_prob_under": 1.0 - model_prob_over,
            "model_prob_over_raw": result["per_line_raw"][dk_line],
            "p_over_hookmix": p_over_hookmix,
            "p_over_prior": p_over_prior,
            "p_over_candidate": p_over_candidate,
            "p_over_re": p_over_re,
            "wx": wx,
            **mkt,
            "expected_k": result["expected_k"],
            "expected_bf": result["expected_bf"],
            "pitcher_k_pct": stats["season_k_pct"],
            "lineup_source": lineup_source,
            # Provenance rides with the prediction all the way to the
            # ledger, so a snapshot-priced bet stays identifiable after
            # the fact.
            "odds_source": entry.get("odds_source", ""),
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

    # Systemic-failure gate. A slate where DK gave us pitchers to price
    # but NONE could be priced is an environment fault (empty Statcast
    # cache, unreadable models), not a real empty board. Writing the
    # sidecar anyway publishes a 0-pitcher board over a good one and
    # deletes the day's evidence -- which is exactly what a CI runner
    # with no cache did on 2026-08-06. Refuse loudly instead: an empty
    # board we could not compute is the same class of lie as an odds
    # figure we did not observe.
    if matched and not predictions:
        raise RuntimeError(
            f"Priced 0 of {len(matched)} matched pitchers for {game_date}. "
            f"Every one was skipped, which means the inputs are missing, "
            f"not that the slate is empty -- check the Statcast cache at "
            f"{os.environ.get('STATCAST_CACHE_DIR', 'data/statcast_cache')} "
            f"(a fresh CI runner has none). Refusing to overwrite the "
            f"board with an empty one."
        )

    # 6. Filter primary picks + evaluate ladder
    print(f"\n[6/8] Filtering primary picks and evaluating ladder...")
    primary_plays = [p for p in predictions if p["clears_threshold"]]
    print(f"  {len(primary_plays)} primary picks clear edge threshold")

    all_plays = []

    for play in primary_plays:
        decimal_odds = american_to_decimal(play["best_odds"])
        raw_units = kelly_stake(play["model_prob_best"], decimal_odds)
        play["units_risked"] = quantize_stake(min(raw_units, MAX_STAKE_UNITS))
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

            primary_units, primary_side = _primary_for(pred, all_plays)

            rungs = evaluate_ladder(
                k_dist, pitcher_alts,
                primary_line=dk_line,
                primary_units=primary_units,
                calibrate_fn=predictor.calibrate_prob,
                expected_k=expected_k,
                primary_side=primary_side,
                lineup_confirmed=pred.get("lineup_source") == "confirmed",
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
        # A no-bet day is still a day of ~25 testable predictions. Bailing
        # out here used to discard the whole board: the dashboard showed
        # nothing for the date (indistinguishable from "the job never
        # ran"), and model_log.py lost the evidence, since it reads
        # slate sidecars. Record the board, then stop.
        print("  No qualifying plays today.")
        for pred in predictions:
            pred["primary_units_final"] = 0.0
        if not dry_run:
            _write_slate_sidecar(game_date, predictions,
                                 shadow_prior=shadow_prior_rows,
                                 skipped=skipped_rows)
        else:
            print("  DRY RUN — not writing the slate sidecar.")
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
    _write_slate_sidecar(game_date, predictions,
                         shadow_prior=shadow_prior_rows,
                         skipped=skipped_rows)
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
            "odds_source": play.get("odds_source", ""),
            "notes": "ladder" if play.get("pick_type") == "ladder" else "",
        }

        if key in existing_picks:
            existing = existing_picks[key]
            if (existing.get("bet_placed") or "").upper() == "Y":
                # Money rule: once bet_placed=Y the captured odds are
                # LOCKED — a re-run (e.g. the lineup-lock pass) may only
                # refresh the model's view. Side, stake, odds, and
                # created_at stay frozen; a side/strength flip is
                # journaled, never applied to the placed bet.
                if pick_row["pick_label"] != existing.get("pick_label", ""):
                    _journal_change(
                        game_date,
                        play.get("game_pk", ""),
                        play.get("pitcher_name", ""),
                        existing.get("pick_label", ""),
                        pick_row["pick_label"],
                    )
                    print(f"    JOURNAL: {play['pitcher_name']} "
                          f"{existing.get('pick_label', '')} -> {pick_row['pick_label']} "
                          f"(placed bet unchanged)")
                merged = dict(existing)
                for f in ("model_prob_over", "model_prob_under",
                          "lineup_source", "updated_at"):
                    merged[f] = pick_row[f]
                pick_row = merged
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
