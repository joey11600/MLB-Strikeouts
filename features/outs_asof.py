"""Leakage-safe as-of features for the OUTS RECORDED model.

The strikeouts model asks "how many batters, and what fraction struck out".
This module serves a different target: STARTING-PITCHER TOTAL OUTS RECORDED
(0..27), which is not a count process but a STOPPING TIME ON A LATTICE.
65.5% of starts end on an exact multiple of 3 and the removal hazard is
concentrated at the inning boundaries 12/15/18/21. The feature set below is
built for a hazard model, not for a mean regression, which is why the
pitcher's own history enters twice: as a scalar mean AND as an empirical
stop-rate vector at those four boundaries.

CONVENTIONS INHERITED FROM features/asof.py
-------------------------------------------
* Season starts are the hardcoded dates in ``SEASON_STARTS`` and aggregates
  do NOT carry across seasons (see ``asof._season_start``).
* Strictly-prior aggregates use the cumsum-minus-current pattern
  (``asof._add_prior_stats``), never a rolling window that can include the
  current row.
* Shrinkage is the pseudo-count form of ``asof.shrink_rate``:
  ``(successes + w * prior_rate) / (trials + w)``.

TWO AS-OF GRANULARITIES ARE USED, AND THE DIFFERENCE MATTERS
------------------------------------------------------------
* PITCHER-level features use strictly-prior STARTS. A starter never starts
  twice in a day, so cumsum-minus-current on (pitcher, season) ordered by
  (game_date, game_pk) is already strictly before first pitch.
* LEAGUE- and OPPONENT-level features use strictly-prior DAYS. Games earlier
  on the same calendar day are excluded even though they finish before an
  evening first pitch, because slate ordering is not reliably knowable at
  feature-build time and a doubleheader would otherwise leak.

NOTHING IN THIS MODULE READS A SAME-GAME OUTCOME. The blacklist from the
factor review (pitcher_days_until_next_game, batter_days_until_next_game,
and every same-game outcome: pitches, bf, tto_max, so, max_inning, runs) is
never referenced except as the LABEL (``outs``) and as strictly-prior
history (``pitches`` -> the prior-5 budget).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Season starts, copied from features.asof._season_start. Aggregates reset here.
SEASON_STARTS = {
    2024: date(2024, 3, 28),
    2025: date(2025, 3, 27),
    2026: date(2026, 3, 26),
}

#: Left edge of the Statcast cache. A pitcher whose first CACHED start falls
#: within LEFT_EDGE_DAYS of this date may have an arbitrarily long pre-2024
#: career that the cache cannot see, so his apparent career start number is
#: censored and he must be excluded from the is_debut treatment.
CACHE_LEFT_EDGE = date(2024, 3, 28)
LEFT_EDGE_DAYS = 14

#: Inning boundaries at which the removal hazard is concentrated. Measured
#: pooled hazards on 13,170 starts: 12 -> .0829, 15 -> .2779, 18 -> .5659,
#: 21 -> .7764 (vs .0161/.0155/.0289 at 3/6/9, which carry no usable signal).
BOUNDARIES = (12, 15, 18, 21)

#: Shrinkage prior weights, in units of the corresponding denominator.
#:
#: W_EXPO = 1.0 start. Chosen by grid search over {0.25 .. 30} scoring
#: exp_o_shrunk as a point predictor of outs on the COMMON SUPPORT of rows
#: with >=1 prior start (so W=0 is well defined and every W is scored on the
#: same 12,102 rows). RMSE argmin: 1.00 (2024), 1.25 (2025), 0.75 (2026) --
#: unanimous to within 0.006 outs, so a single value satisfies the
#: both-directions rule. The weight is small because between-pitcher
#: variance in outs is driven by ROLE (opener vs ace), and shrinking toward
#: the league mean destroys that signal faster than it removes noise.
W_EXPO = 1.0
#: W_HAZ = 24 starts-that-reached-b. Grid search over {1 .. 64} scoring the
#: pooled log-loss of stop_rate_b against 1[outs == b] on rows with
#: outs >= b. Argmin 24 (2024), 32 (2025), 24 (2026); the curve is flat to
#: 1e-4 across 16-48, so the choice is insensitive. Much larger than W_EXPO
#: because a binary hazard estimated off <=30 prior starts is far noisier
#: than a mean.
W_HAZ = 24.0
#: League-level stabilizers. The denominators reach thousands of starts
#: within days, so these matter only at a season's left edge; their job is
#: to carry the previous season's completed mean into the first week rather
#: than to be fitted. Measured: raising W_LEAGUE from 0 to 1000 moves the
#: correlation of league_asof_outs with outs by 0.0006 (+0.0332 -> +0.0333),
#: but W_LEAGUE=0 leaves 76 rows NaN where 200 leaves 26.
W_LEAGUE = 200.0    # starts
W_LEAGUE_H = 200.0  # starts-that-reached-b
W_OPP_PA = 200.0    # plate appearances; negligible behind the MIN_OPP_GAMES gate

#: Hard gate on the opponent term: fewer than this many prior team games in
#: the season and the opponent features are NaN (never imputed).
MIN_OPP_GAMES = 20

#: Prior-5 pitch budget. Exactly ONE budget variable is emitted; the other
#: candidates correlate 0.85-0.92 with it and would fail Gate 4.
BUDGET_N = 5

#: days_rest bucket labels, in order. "unknown" is a real level, not a
#: missing value: its mean outs (14.71) sits between the short-rest and
#: normal-rest bands and imputing it would destroy that.
DAYS_REST_BUCKETS = ["<=2", "3", "4", "5", "6", "7", "8-10", "11-20", "21+", "unknown"]

#: Caps for the ramp-up counters.
CAREER_CAP = 10
SEASON_CAP = 8

#: GATE 4 RESOLUTION -- the columns that may enter a fitted model together.
#:
#: Measured on all 13,170 starts, the full emitted set contains exactly two
#: pairs above the |r| > 0.85 collinearity threshold:
#:   exp_o          <-> exp_o_shrunk     r = +0.976
#:   season_start_number <-> career_x_season  r = +0.974
#: Both are by construction, and both are resolved by dropping one member:
#:
#:  * Drop ``exp_o``, keep ``exp_o_shrunk``. They are two encodings of the
#:    same history; the shrunk form additionally covers 13,144 rows against
#:    exp_o's 12,102 (it is defined on a pitcher's first start of a season)
#:    and it is the weaker half of the ``p5_pitches`` pair (+0.774 vs
#:    +0.805). ``exp_o`` is still emitted, for diagnostics and for the
#:    honest as-of baseline.
#:  * Drop ``season_start_number`` as an ADDITIVE term, keep
#:    ``career_x_season``. This is the factor review's instruction ("fit as
#:    an INTERACTION ... not an additive term"); the +0.974 measurement is
#:    why. ``season_start_number`` is still emitted so the interaction can
#:    be rebuilt and inspected.
#:
#: With those two dropped, no remaining pair exceeds 0.85. The largest are
#: exp_o_shrunk <-> p5_pitches (+0.774) and
#: career_start_number <-> career_x_season (+0.753). Max VIF is 4.93.
RECOMMENDED_MODEL_SET = [
    "exp_o_shrunk",
    "stop_rate_12", "stop_rate_15", "stop_rate_18", "stop_rate_21",
    "days_rest_bucket",
    "career_start_number", "is_debut", "career_left_censored",
    "career_x_season",
    "is_home",
    "league_asof_outs",
    "opp_obp_asof",
    "p5_pitches",
]

#: STRUCTURAL MISSINGNESS the modeler must handle, not impute:
#:   is_debut == 1  =>  exp_o, p5_pitches are NaN (there is no prior start).
#:   exp_o          NaN on 1,068 rows = every first start of a season.
#:   exp_o_shrunk   NaN on    26 rows = 2024 opening day (no prior day, and
#:                                      no 2023 in the cache to anchor to).
#:   career_start_number NaN on 1,518 rows = left-censored pitchers whose
#:                                      apparent count is below the cap.
#:   opp_obp_asof   NaN on 1,802 rows = opponent below MIN_OPP_GAMES.
#: Every one of these is a real "we do not know", and every one of them is
#: informative about the row. Filling any of them with a mean is a leak of
#: the population into the row.

#: The feature columns this module contributes, in the order reported.
FEATURE_COLS = [
    "exp_o",
    "exp_o_shrunk",
    "stop_rate_12", "stop_rate_15", "stop_rate_18", "stop_rate_21",
    "days_rest_bucket",
    "career_start_number", "is_debut", "career_left_censored",
    "season_start_number", "career_x_season",
    "is_home",
    "league_asof_outs",
    "opp_obp_asof",
    "p5_pitches",
]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _season_of(ts: pd.Series) -> pd.Series:
    """Season year. The cache holds only regular seasons, so calendar year
    is the season, matching features.asof._season_start's keys."""
    return ts.dt.year.astype("int16")


def _prior_within_group(df: pd.DataFrame, keys: list[str], cols: list[str]) -> pd.DataFrame:
    """Strictly-prior cumulative sums via cumsum-minus-current.

    ``df`` must already be ordered so that every row's group-predecessors
    appear above it. Returns a frame of the same index with one column per
    entry of ``cols``, holding the sum over rows STRICTLY ABOVE that row
    within its group.
    """
    grp = df.groupby(keys, sort=False)
    out = {}
    for c in cols:
        out[c] = grp[c].cumsum() - df[c]
    return pd.DataFrame(out, index=df.index)


def _prior_day_totals(day_table: pd.DataFrame, keys: list[str],
                      cols: list[str], date_col: str = "game_date") -> pd.DataFrame:
    """Strictly-prior-DAY cumulative sums for a per-(key, day) aggregate.

    ``day_table`` has one row per (keys..., date). Within each key group,
    ordered by date, each row receives the sum over STRICTLY EARLIER dates
    (cumsum-minus-current on the day aggregate). Because the aggregate is
    per-day, "minus current" removes the whole day, not just one game --
    which is exactly the prior-day rule.
    """
    d = day_table.sort_values(keys + [date_col], kind="mergesort").copy()
    prior = _prior_within_group(d, keys, cols)
    for c in cols:
        d["prior_" + c] = prior[c]
    return d


#: Accepted spellings for the required input columns. The per-start table in
#: data/outs_starts.parquet names them differently from this module's
#: internal builder; both are accepted so callers never hand-rename.
COLUMN_ALIASES = {
    "opp": ("opp", "opponent_team", "batting_team"),
    "drest": ("drest", "days_since_prev_game", "pitcher_days_since_prev_game"),
    "team": ("team", "pitcher_team", "pitching_team"),
}


def normalize_starts(df: pd.DataFrame) -> pd.DataFrame:
    """Rename a per-start table's columns to this module's canonical names.

    Idempotent. Raises if a required column is missing under every accepted
    spelling, because silently producing an all-NaN feature is worse than
    failing.
    """
    out = df.copy()
    for canon, spellings in COLUMN_ALIASES.items():
        if canon in out.columns:
            continue
        for s in spellings:
            if s in out.columns:
                out = out.rename(columns={s: canon})
                break
    missing = [c for c in ("game_pk", "pitcher", "game_date", "outs",
                           "pitches", "is_home", "opp", "drest")
               if c not in out.columns]
    if missing:
        raise KeyError(f"per-start table is missing required columns: {missing}. "
                       f"Accepted aliases: {COLUMN_ALIASES}")
    out["is_home"] = out["is_home"].astype("int8")
    out["outs"] = out["outs"].astype("int64")
    out["pitches"] = out["pitches"].astype("float64")
    out["drest"] = pd.to_numeric(out["drest"], errors="coerce").astype("Int64")
    return out


def team_batting_from_starts(starts: pd.DataFrame) -> pd.DataFrame:
    """Fallback opponent table built from the per-start table alone.

    Uses the batting team's on-base events against the STARTING pitcher only
    (``reached_base`` / ``bf``), so it is a narrower sample than the
    full-game version ``build_starts_table`` derives from the PA table --
    roughly 60% of each team's plate appearances. Prefer the full-game table
    when the PA data is available; this exists so the feature set can be
    built from data/outs_starts.parquet with no Statcast re-read.
    """
    s = normalize_starts(starts)
    if not {"reached_base", "bf"}.issubset(s.columns):
        raise KeyError("need reached_base and bf to derive team batting from starts")
    return pd.DataFrame({
        "batting_team": s["opp"],
        "game_pk": s["game_pk"],
        "game_date": pd.to_datetime(s["game_date"]),
        "pa_n": s["bf"].astype("float64"),
        "ob": s["reached_base"].astype("float64"),
    })


def days_rest_bucket(x) -> str:
    """Map a days-since-previous-appearance value to its bucket label.

    NOT a fatigue variable. Within the 5-7 day band the correlation with
    outs is ~0; the bucket is a ROLE detector. Measured mean outs by bucket
    on 13,170 starts: <=2 -> 4.32 (bullpen game / opener), 3 -> 6.23,
    4 -> 8.88, 5 -> 16.04, 6 -> 16.08, 7 -> 15.85, 8-10 -> 15.60,
    11-20 -> 14.50, 21+ -> 13.60, unknown -> 14.71.
    """
    if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA or pd.isna(x):
        return "unknown"
    x = int(x)
    if x <= 2:
        return "<=2"
    if x <= 7:
        return str(x)
    if x <= 10:
        return "8-10"
    if x <= 20:
        return "11-20"
    return "21+"


# --------------------------------------------------------------------------
# The feature builder
# --------------------------------------------------------------------------

def build_outs_asof(starts: pd.DataFrame,
                    team_batting: pd.DataFrame | None = None) -> pd.DataFrame:
    """Attach the Tier-1 as-of feature set to a per-start OUTS table.

    Parameters
    ----------
    starts
        One row per (game_pk, starting pitcher). Required columns:

        ``game_pk`` int, ``pitcher`` int, ``game_date`` datetime64,
        ``outs`` int (LABEL, 0..27), ``pitches`` int (same-game pitch count;
        used ONLY as strictly-prior history), ``is_home`` 0/1,
        ``opp`` str (batting team abbreviation),
        ``drest`` nullable int (Statcast ``pitcher_days_since_prev_game``
        read off the starter's first pitch of the game).

    team_batting
        One row per (batting_team, game_pk): ``batting_team``, ``game_pk``,
        ``game_date``, ``pa_n``, ``ob``. Optional; when omitted the opponent
        features are emitted as all-NaN with n_prior = 0.

    Returns
    -------
    The input frame plus the columns in ``FEATURE_COLS`` and their
    bookkeeping companions (``exp_o_n``, ``stop_n_*``, ``opp_games_prior``,
    ``league_asof_n``, ``p5_n``). Row order and index are preserved.
    """
    if starts.empty:
        return starts.copy()

    g = normalize_starts(starts)
    g["game_date"] = pd.to_datetime(g["game_date"])
    g["season"] = _season_of(g["game_date"])
    original_index = g.index
    # Merges below reset the index, so carry the caller's row order in a
    # column and restore it at the end. The returned frame is row-for-row
    # aligned with the input.
    g["_row_id"] = np.arange(len(g))

    # Deterministic as-of ordering. game_pk breaks ties inside a date; a
    # starter never starts twice in a day so the tie-break is cosmetic.
    g = g.sort_values(["pitcher", "game_date", "game_pk"], kind="mergesort")

    outs = g["outs"].astype("float64")

    # ---------------------------------------------------------------- league
    # As-of expanding league mean outs, within season, through the PRIOR DAY.
    # Anchored to the previous season's completed final mean so the first
    # days of a season are not driven by 30 starts. Managers hook earlier
    # every year (measured full-season means 15.655 -> 15.568 -> 15.268);
    # omitting this term biases 2026 predictions high.
    g["_one"] = 1.0
    day_cols = ["_one", "_outs_sum"]
    g["_outs_sum"] = outs
    for b in BOUNDARIES:
        g[f"_reach_{b}"] = (g["outs"] >= b).astype("float64")
        g[f"_stop_{b}"] = (g["outs"] == b).astype("float64")
        day_cols += [f"_reach_{b}", f"_stop_{b}"]

    daily = g.groupby(["season", "game_date"], as_index=False)[day_cols].sum()
    daily = _prior_day_totals(daily, ["season"], day_cols)

    # Previous-season anchors: a fully completed past season, as-of safe for
    # every row of the following season.
    season_tot = g.groupby("season")[day_cols].sum()
    prev_mean = {}
    prev_haz = {b: {} for b in BOUNDARIES}
    for s in season_tot.index:
        p = s - 1
        if p in season_tot.index:
            prev_mean[s] = season_tot.loc[p, "_outs_sum"] / season_tot.loc[p, "_one"]
            for b in BOUNDARIES:
                prev_haz[b][s] = (season_tot.loc[p, f"_stop_{b}"]
                                  / season_tot.loc[p, f"_reach_{b}"])
        else:
            prev_mean[s] = np.nan
            for b in BOUNDARIES:
                prev_haz[b][s] = np.nan

    daily["_prev_mean"] = daily["season"].map(prev_mean).astype("float64")
    n = daily["prior__one"]
    s_ = daily["prior__outs_sum"]
    anchored = (s_ + W_LEAGUE * daily["_prev_mean"]) / (n + W_LEAGUE)
    with np.errstate(invalid="ignore", divide="ignore"):
        plain = np.where(n > 0, s_ / n.replace(0, np.nan), np.nan)
    daily["league_asof_outs"] = anchored.where(daily["_prev_mean"].notna(),
                                               pd.Series(plain, index=daily.index))
    daily["league_asof_n"] = n

    for b in BOUNDARIES:
        daily[f"_prev_h_{b}"] = daily["season"].map(prev_haz[b]).astype("float64")
        rb = daily[f"prior__reach_{b}"]
        sb = daily[f"prior__stop_{b}"]
        anch = (sb + W_LEAGUE_H * daily[f"_prev_h_{b}"]) / (rb + W_LEAGUE_H)
        with np.errstate(invalid="ignore", divide="ignore"):
            pl = np.where(rb > 0, sb / rb.replace(0, np.nan), np.nan)
        daily[f"league_h_{b}"] = anch.where(daily[f"_prev_h_{b}"].notna(),
                                            pd.Series(pl, index=daily.index))

    keep = ["season", "game_date", "league_asof_outs", "league_asof_n"] + \
           [f"league_h_{b}" for b in BOUNDARIES]
    g = g.merge(daily[keep], on=["season", "game_date"], how="left")

    # -------------------------------------------------------------- exp_o
    # The pitcher's expanding mean outs over PRIOR starts THIS SEASON.
    # Single best predictor in the factor review (r = +0.328).
    prior = _prior_within_group(g, ["pitcher", "season"],
                                ["_one", "_outs_sum"] +
                                [f"_reach_{b}" for b in BOUNDARIES] +
                                [f"_stop_{b}" for b in BOUNDARIES])
    g["exp_o_n"] = prior["_one"].astype("int16")
    with np.errstate(invalid="ignore", divide="ignore"):
        g["exp_o"] = np.where(prior["_one"] > 0,
                              prior["_outs_sum"] / prior["_one"].replace(0, np.nan),
                              np.nan)
    # Shrunk toward the as-of league mean. W_EXPO is in starts: at 6 prior
    # starts the pitcher's own mean carries half the weight.
    g["exp_o_shrunk"] = ((prior["_outs_sum"] + W_EXPO * g["league_asof_outs"])
                         / (prior["_one"] + W_EXPO))

    # ------------------------------------------------- stop-rate vector
    # The distributional form of exp_o: the pitcher's own empirical
    # P(stop at exactly b | reached b) over prior starts this season,
    # shrunk toward the as-of LEAGUE hazard at the same boundary.
    for b in BOUNDARIES:
        rb = prior[f"_reach_{b}"]
        sb = prior[f"_stop_{b}"]
        g[f"stop_n_{b}"] = rb.astype("int16")
        g[f"stop_rate_{b}"] = (sb + W_HAZ * g[f"league_h_{b}"]) / (rb + W_HAZ)

    # ---------------------------------------------------------- days_rest
    g["days_rest_bucket"] = pd.Categorical(
        [days_rest_bucket(v) for v in g["drest"]],
        categories=DAYS_REST_BUCKETS, ordered=False,
    )

    # ------------------------------------------------- career / season ramp
    # career_start_number is CAREER, so it does NOT reset per season.
    career_prior = g.groupby("pitcher", sort=False).cumcount()
    first_cached = g.groupby("pitcher", sort=False)["game_date"].transform("min")
    edge = pd.Timestamp(CACHE_LEFT_EDGE) + pd.Timedelta(days=LEFT_EDGE_DAYS)
    censored = first_cached <= edge
    g["career_left_censored"] = censored.astype("int8")

    csn = (career_prior + 1).clip(upper=CAREER_CAP).astype("float64")
    # For a left-censored pitcher the count is wrong by his unseen pre-2024
    # career, but only while it is below the cap -- once it saturates at
    # CAREER_CAP it is correct again. NaN in the wrong region; never imputed.
    csn[censored & (career_prior < CAREER_CAP)] = np.nan
    g["career_start_number"] = csn
    g["is_debut"] = ((career_prior == 0) & (~censored)).astype("int8")

    season_prior = g.groupby(["pitcher", "season"], sort=False).cumcount()
    g["season_start_number"] = (season_prior + 1).clip(upper=SEASON_CAP).astype("int16")

    # Fit season_start_number as an INTERACTION with career_start_number,
    # not as an additive term: ramp-up is steepest for pitchers who are also
    # early in their careers. The product is emitted; the modeler may also
    # use the two components, but not season_start_number additively alone.
    g["career_x_season"] = (g["career_start_number"]
                            * g["season_start_number"].astype("float64"))

    # ------------------------------------------------------------- is_home
    g["is_home"] = g["is_home"].astype("int8")

    # ---------------------------------------------------------- opponent
    if team_batting is not None and not team_batting.empty:
        tb = team_batting.copy()
        tb["game_date"] = pd.to_datetime(tb["game_date"])
        tb["season"] = _season_of(tb["game_date"])
        tb["_g"] = 1.0
        tb["pa_n"] = tb["pa_n"].astype("float64")
        tb["ob"] = tb["ob"].astype("float64")
        tdaily = tb.groupby(["batting_team", "season", "game_date"],
                            as_index=False)[["_g", "pa_n", "ob"]].sum()
        tdaily = _prior_day_totals(tdaily, ["batting_team", "season"],
                                   ["_g", "pa_n", "ob"])
        # League as-of on-base rate, prior day, within season (the shrink target).
        ldaily = tb.groupby(["season", "game_date"], as_index=False)[["pa_n", "ob"]].sum()
        ldaily = _prior_day_totals(ldaily, ["season"], ["pa_n", "ob"])
        with np.errstate(invalid="ignore", divide="ignore"):
            ldaily["league_obp_asof"] = np.where(
                ldaily["prior_pa_n"] > 0,
                ldaily["prior_ob"] / ldaily["prior_pa_n"].replace(0, np.nan), np.nan)
        tdaily = tdaily.merge(ldaily[["season", "game_date", "league_obp_asof"]],
                              on=["season", "game_date"], how="left")
        tdaily["opp_obp_asof"] = ((tdaily["prior_ob"]
                                   + W_OPP_PA * tdaily["league_obp_asof"])
                                  / (tdaily["prior_pa_n"] + W_OPP_PA))
        # Hard gate: fewer than MIN_OPP_GAMES prior team games -> NaN.
        tdaily.loc[tdaily["prior__g"] < MIN_OPP_GAMES, "opp_obp_asof"] = np.nan
        tdaily = tdaily.rename(columns={"batting_team": "opp",
                                        "prior__g": "opp_games_prior"})
        g = g.merge(tdaily[["opp", "season", "game_date",
                            "opp_obp_asof", "opp_games_prior"]],
                    on=["opp", "season", "game_date"], how="left")
    else:
        g["opp_obp_asof"] = np.nan
        g["opp_games_prior"] = 0.0

    # ------------------------------------------------------ pitch budget
    # Mean pitch count over the last BUDGET_N prior starts THIS SEASON.
    # shift(1) BEFORE rolling, so the window structurally cannot reach the
    # current row. Exactly one budget variable is emitted (Gate 4).
    grp = g.groupby(["pitcher", "season"], sort=False)["pitches"]
    g["p5_pitches"] = grp.transform(
        lambda s: s.shift(1).rolling(BUDGET_N, min_periods=1).mean()).astype("float64")
    g["p5_n"] = grp.transform(
        lambda s: s.shift(1).rolling(BUDGET_N, min_periods=1).count()).fillna(0).astype("int16")

    # ------------------------------------------------------------- tidy up
    g = g.sort_values("_row_id", kind="mergesort")
    drop = [c for c in g.columns if c.startswith("_")]
    g = g.drop(columns=drop + [f"league_h_{b}" for b in BOUNDARIES])
    g.index = original_index
    return g


# --------------------------------------------------------------------------
# Self-contained table construction (the validated outs reconstruction)
# --------------------------------------------------------------------------

_PA_CACHE_COLS = ["game_pk", "game_date", "game_type", "pitcher", "events",
                  "inning", "inning_topbot", "at_bat_number", "pitch_number",
                  "outs_when_up", "home_team", "away_team",
                  "post_home_score", "post_away_score",
                  "pitcher_days_since_prev_game"]

ON_BASE_EVENTS = {"single", "double", "triple", "home_run",
                  "walk", "intent_walk", "hit_by_pitch"}

#: Statcast ``events`` values that are baserunning notes, not plate
#: appearances. Excluded from the opponent on-base denominator.
NON_PA_EVENTS = {
    "caught_stealing_2", "caught_stealing_3", "caught_stealing_home",
    "pickoff_1b", "pickoff_2b", "pickoff_3b",
    "pickoff_caught_stealing_2b", "pickoff_caught_stealing_3b",
    "pickoff_caught_stealing_home",
    "stolen_base_2b", "stolen_base_3b", "stolen_base_home",
    "wild_pitch", "passed_ball", "other_advance", "runner_double_play",
    "balk", "game_advisory", "ejection", "catcher_interf",
}


def load_statcast_pa(cache_dir: Path | None = None) -> pd.DataFrame:
    """Load every cached Statcast day and collapse to one row per PA.

    Guards the zero-column-schema trap: 26 of the 571 cache files hold a
    schema with no columns (off-days, All-Star break) and raise
    pyarrow.ArrowInvalid on a ``columns=`` read.
    """
    import pyarrow.parquet as pq
    from data.backfill_statcast import CACHE_DIR

    cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    frames = []
    for f in sorted(Path(cache_dir).glob("*/*.parquet")):
        try:
            names = pq.ParquetFile(f).schema_arrow.names
        except Exception:
            continue
        if len(names) == 0:
            continue
        have = [c for c in _PA_CACHE_COLS if c in names]
        d = pq.read_table(f, columns=have).to_pandas()
        if not d.empty:
            frames.append(d)
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df[df["game_type"] == "R"]          # regular season only
    df = df.sort_values(["game_pk", "inning", "inning_topbot",
                         "at_bat_number", "pitch_number"], kind="mergesort")
    pa = df.groupby(["game_pk", "at_bat_number"], sort=False).agg(
        game_date=("game_date", "first"),
        inning=("inning", "first"),
        inning_topbot=("inning_topbot", "first"),
        outs_when_up=("outs_when_up", "first"),
        pitcher=("pitcher", "first"),
        home_team=("home_team", "first"),
        away_team=("away_team", "first"),
        events=("events", "last"),
        n_pitches=("pitch_number", "count"),
        post_home_score=("post_home_score", "last"),
        post_away_score=("post_away_score", "last"),
        drest=("pitcher_days_since_prev_game", "first"),
    ).reset_index()
    return pa.sort_values(["game_pk", "inning", "inning_topbot", "at_bat_number"],
                          kind="mergesort")


def build_starts_table(pa: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconstruct per-start OUTS and the team-batting on-base table.

    The validated method (99.51% of half-innings sum to exactly 3; the start
    table yields exactly 2 x n_games rows):

        outs_on_play[i] = max(0, outs_when_up[i+1] - outs_when_up[i])
        the final PA of a half-inning gets 3 - outs_when_up,
        overridden to 0 on a walk-off.

    Do NOT count outs from ``events``: this cache's events never carry
    caught_stealing or pickoff, so an events-only count is biased low.
    """
    pa = pa.copy()
    half = ["game_pk", "inning", "inning_topbot"]
    grp = pa.groupby(half, sort=False)
    nxt = grp["outs_when_up"].shift(-1)
    is_last = nxt.isna().to_numpy()
    cur = pa["outs_when_up"].to_numpy()
    outs_play = np.where(is_last, 3 - cur,
                         np.maximum(0, np.where(is_last, 0, nxt.to_numpy()) - cur))

    # Walk-off: the game's final PA (max at_bat_number -- the frame is sorted
    # by inning_topbot as a STRING, where "Bot" < "Top", so .tail(1) is wrong),
    # bottom half, home team ahead afterwards. That half-inning simply stops.
    last_idx = pa.loc[pa.groupby("game_pk", sort=False)["at_bat_number"].idxmax()].index
    cand = pa.loc[last_idx]
    wo = ((cand["inning_topbot"] == "Bot")
          & (cand["post_home_score"] > cand["post_away_score"]))
    wo_mask = pd.Series(False, index=pa.index)
    wo_mask.loc[cand.index[wo.to_numpy()]] = True
    outs_play = np.where(wo_mask.to_numpy() & is_last, 0, outs_play)
    pa["outs_on_play"] = np.clip(outs_play, 0, 3).astype("int8")

    pa["pitching_team"] = np.where(pa["inning_topbot"] == "Top",
                                   pa["home_team"], pa["away_team"])
    pa["batting_team"] = np.where(pa["inning_topbot"] == "Top",
                                  pa["away_team"], pa["home_team"])
    starter = (pa.sort_values(["game_pk", "at_bat_number"], kind="mergesort")
                 .groupby(["game_pk", "pitching_team"], sort=False)["pitcher"]
                 .first().rename("starter").reset_index())
    pa = pa.merge(starter, on=["game_pk", "pitching_team"], how="left")

    sp = (pa[pa["pitcher"] == pa["starter"]]
          .groupby(["game_pk", "pitcher"], sort=False).agg(
              game_date=("game_date", "first"),
              outs=("outs_on_play", "sum"),
              pitches=("n_pitches", "sum"),
              bf=("at_bat_number", "count"),
              team=("pitching_team", "first"),
              opp=("batting_team", "first"),
              home_team=("home_team", "first"),
              drest=("drest", "first"),
          ).reset_index())
    sp["is_home"] = (sp["team"] == sp["home_team"]).astype("int8")
    sp["outs"] = sp["outs"].astype("int16")

    ev = pa[pa["events"].notna() & ~pa["events"].isin(NON_PA_EVENTS)]
    tb = (ev.assign(ob=ev["events"].isin(ON_BASE_EVENTS).astype("int8"))
            .groupby(["batting_team", "game_pk"], sort=False)
            .agg(game_date=("game_date", "first"),
                 pa_n=("ob", "count"), ob=("ob", "sum")).reset_index())
    return sp, tb


def build_all(cache_dir: Path | None = None) -> pd.DataFrame:
    """End-to-end: cache -> PA table -> starts table -> as-of features."""
    pa = load_statcast_pa(cache_dir)
    sp, tb = build_starts_table(pa)
    return build_outs_asof(sp, tb)
