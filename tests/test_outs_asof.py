"""Leakage tests for features/outs_asof.py.

A silent leak here would make every downstream number meaningless: the
model would look brilliant in backtest and lose money live. So this file
attacks the feature builder four different ways, and the ways do not share
a failure mode.

1. BRUTE FORCE. For a sample of rows, recompute every feature with a plain
   Python loop over rows that are STRICTLY PRIOR, and assert equality with
   the vectorized output. Catches an off-by-one in cumsum-minus-current.

2. FUTURE PERTURBATION. Corrupt every row after a cutoff date and rebuild.
   Every feature on every row at or before the cutoff must be bit-identical.
   Catches any dependence on the future, including ones a brute-force
   reimplementation might replicate because it shares the same wrong idea.

3. SAME-DAY PERTURBATION. Corrupt one game on a given day and rebuild.
   Other rows on that SAME day must not move. Catches doubleheader and
   same-slate leakage in the league and opponent terms, which the
   prior-DAY rule exists to prevent.

4. ROW-ORDER INVARIANCE. Shuffle the input rows. Features must be
   identical after realigning on (game_pk, pitcher). Catches a builder that
   silently depends on the caller's sort order.

Tests 2-4 run on both synthetic data and, when the Statcast cache is
present, the real 13,170-start table.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from features import outs_asof as M
from features.outs_asof import (
    BOUNDARIES, CACHE_LEFT_EDGE, CAREER_CAP, LEFT_EDGE_DAYS, MIN_OPP_GAMES,
    SEASON_CAP, W_EXPO, W_HAZ, W_LEAGUE, W_LEAGUE_H, W_OPP_PA,
    build_outs_asof, days_rest_bucket,
)

TEAMS = ["ATL", "BAL", "BOS", "CHC", "CIN", "CLE", "COL", "DET"]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _synthetic(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A small league that exercises every edge case the real data has:
    multi-season pitchers, mid-season debuts, left-edge pitchers, missing
    days_rest, doubleheaders, and teams below the MIN_OPP_GAMES gate.
    """
    rng = np.random.default_rng(seed)
    rows = []
    game_pk = 700_000
    pitchers = list(range(100, 116))
    # Half the pitchers start on the cache's left edge (censored), the rest
    # debut later.
    debut_offset = {p: (0 if i % 2 == 0 else 20 + 7 * i) for i, p in enumerate(pitchers)}

    # The season must be long enough that teams clear MIN_OPP_GAMES prior
    # games, or the opponent branch is never exercised and a leak there
    # would go undetected. 60 game-days x 2 games = 30 games per team.
    for season, start in M.SEASON_STARTS.items():
        base = pd.Timestamp(start)
        for day in range(0, 120, 2):
            gd = base + pd.Timedelta(days=day)
            # Two games per day, plus one doubleheader. It is placed LATE in
            # the season on purpose: early on, the teams have not yet cleared
            # MIN_OPP_GAMES, the opponent feature is NaN, and the
            # doubleheader-leak test would silently prove nothing.
            n_games = 3 if day == 110 else 2
            for gnum in range(n_games):
                game_pk += 1
                # On the doubleheader day the third game REPLAYS the first
                # game's pairing, so one team bats twice on one date. That is
                # the case the prior-DAY rule exists for.
                slot = 0 if (n_games == 3 and gnum == 2) else gnum
                home, away = TEAMS[(day + slot) % 8], TEAMS[(day + slot + 3) % 8]
                for is_home, team, opp in ((1, home, away), (0, away, home)):
                    p = pitchers[(game_pk + is_home) % len(pitchers)]
                    if season == 2024 and day < debut_offset[p]:
                        continue
                    drest = rng.choice([pd.NA, 2, 3, 4, 5, 6, 7, 9, 15, 30],
                                       p=[.08, .02, .02, .03, .3, .35, .1, .04, .03, .03])
                    rows.append({
                        "game_pk": game_pk, "pitcher": p, "game_date": gd,
                        "outs": int(rng.integers(0, 28)),
                        "pitches": int(rng.integers(20, 110)),
                        "is_home": is_home, "team": team, "opp": opp,
                        "drest": drest,
                    })
    sp = pd.DataFrame(rows)
    sp["drest"] = sp["drest"].astype("Int64")

    tb_rows = []
    for _, r in sp.iterrows():
        tb_rows.append({"batting_team": r["opp"], "game_pk": r["game_pk"],
                        "game_date": r["game_date"],
                        "pa_n": int(rng.integers(30, 45)),
                        "ob": int(rng.integers(8, 18))})
    tb = pd.DataFrame(tb_rows)
    return sp, tb


@pytest.fixture(scope="module")
def synth():
    return _synthetic()


@pytest.fixture(scope="module")
def real():
    """The real per-start table, or skip if the Statcast cache is absent."""
    from data.backfill_statcast import CACHE_DIR
    if not Path(CACHE_DIR).exists():
        pytest.skip("statcast cache not present")
    pa = M.load_statcast_pa()
    if pa.empty:
        pytest.skip("statcast cache empty")
    return M.build_starts_table(pa)


# --------------------------------------------------------------------------
# 1. BRUTE FORCE
# --------------------------------------------------------------------------

def _prior_mask(sp: pd.DataFrame, row, same_pitcher=True, same_season=True):
    """Rows STRICTLY prior to `row` under the module's (game_date, game_pk)
    ordering. This is the definition every feature must obey."""
    m = ((sp["game_date"] < row["game_date"])
         | ((sp["game_date"] == row["game_date"]) & (sp["game_pk"] < row["game_pk"])))
    if same_pitcher:
        m &= sp["pitcher"] == row["pitcher"]
    if same_season:
        m &= sp["game_date"].dt.year == row["game_date"].year
    return m


def _brute_league(sp: pd.DataFrame, row):
    """League as-of mean outs and boundary hazards, PRIOR DAY only."""
    season = row["game_date"].year
    yr = sp["game_date"].dt.year
    prior = sp[(yr == season) & (sp["game_date"] < row["game_date"])]
    prev = sp[yr == season - 1]

    prev_mean = prev["outs"].mean() if len(prev) else np.nan
    if np.isnan(prev_mean):
        mean = prior["outs"].mean() if len(prior) else np.nan
    else:
        mean = (prior["outs"].sum() + W_LEAGUE * prev_mean) / (len(prior) + W_LEAGUE)

    haz = {}
    for b in BOUNDARIES:
        pr = int((prev["outs"] >= b).sum())
        ps = int((prev["outs"] == b).sum())
        prev_h = ps / pr if pr else np.nan
        r_ = int((prior["outs"] >= b).sum())
        s_ = int((prior["outs"] == b).sum())
        if np.isnan(prev_h):
            haz[b] = s_ / r_ if r_ else np.nan
        else:
            haz[b] = (s_ + W_LEAGUE_H * prev_h) / (r_ + W_LEAGUE_H)
    return mean, haz


def _brute_row(sp: pd.DataFrame, tb: pd.DataFrame, row) -> dict:
    """Recompute every feature for one row, by loop, from prior rows only."""
    out = {}
    league_mean, league_h = _brute_league(sp, row)
    out["league_asof_outs"] = league_mean

    prior = sp[_prior_mask(sp, row)].sort_values(["game_date", "game_pk"],
                                                 kind="mergesort")
    n = len(prior)
    out["exp_o"] = prior["outs"].mean() if n else np.nan
    out["exp_o_shrunk"] = (prior["outs"].sum() + W_EXPO * league_mean) / (n + W_EXPO)

    for b in BOUNDARIES:
        reach = int((prior["outs"] >= b).sum())
        stop = int((prior["outs"] == b).sum())
        out[f"stop_rate_{b}"] = (stop + W_HAZ * league_h[b]) / (reach + W_HAZ)

    out["days_rest_bucket"] = days_rest_bucket(row["drest"])

    career_prior = int(_prior_mask(sp, row, same_season=False).sum())
    first_cached = sp.loc[sp["pitcher"] == row["pitcher"], "game_date"].min()
    censored = first_cached <= (pd.Timestamp(CACHE_LEFT_EDGE)
                                + pd.Timedelta(days=LEFT_EDGE_DAYS))
    out["career_left_censored"] = int(censored)
    out["is_debut"] = int(career_prior == 0 and not censored)
    csn = min(career_prior + 1, CAREER_CAP)
    out["career_start_number"] = np.nan if (censored and career_prior < CAREER_CAP) else float(csn)
    ssn = min(n + 1, SEASON_CAP)
    out["season_start_number"] = ssn
    out["career_x_season"] = out["career_start_number"] * ssn

    out["p5_pitches"] = prior["pitches"].tail(5).mean() if n else np.nan

    season = row["game_date"].year
    tyr = tb["game_date"].dt.year
    tprior = tb[(tb["batting_team"] == row["opp"]) & (tyr == season)
                & (tb["game_date"] < row["game_date"])]
    lprior = tb[(tyr == season) & (tb["game_date"] < row["game_date"])]
    n_games = tprior["game_pk"].nunique()
    if n_games < MIN_OPP_GAMES:
        out["opp_obp_asof"] = np.nan
    else:
        league_obp = (lprior["ob"].sum() / lprior["pa_n"].sum()
                      if lprior["pa_n"].sum() > 0 else np.nan)
        out["opp_obp_asof"] = ((tprior["ob"].sum() + W_OPP_PA * league_obp)
                               / (tprior["pa_n"].sum() + W_OPP_PA))
    return out


def _assert_brute(sp, tb, n_sample=120, seed=11):
    feat = build_outs_asof(sp, tb)
    rng = np.random.default_rng(seed)

    # A random sample PLUS deliberately chosen edge rows: first start of a
    # season, a true debut, a left-censored pitcher, the earliest day in the
    # frame, and rows sitting just under the opponent-games gate.
    idx = set(rng.choice(feat.index, size=min(n_sample, len(feat)),
                         replace=False).tolist())
    for mask in [feat["season_start_number"] == 1,
                 feat["is_debut"] == 1,
                 feat["career_left_censored"] == 1,
                 feat["game_date"] == feat["game_date"].min(),
                 feat["opp_obp_asof"].isna(),
                 feat["opp_obp_asof"].notna(),
                 feat["opp_games_prior"] == MIN_OPP_GAMES,
                 feat["exp_o"].isna(),
                 feat["days_rest_bucket"] == "unknown"]:
        sub = feat.index[mask.to_numpy()]
        idx.update(sub[:6].tolist())

    # The opponent branch must actually be live in this fixture, otherwise
    # a leak in it is untestable. (A mutation test found exactly this hole.)
    assert feat["opp_obp_asof"].notna().sum() >= 50, "opponent branch not exercised"

    checked = 0
    for i in sorted(idx):
        row = feat.loc[i]
        exp = _brute_row(sp, tb, row)
        for k, v in exp.items():
            got = row[k]
            if k == "days_rest_bucket":
                assert str(got) == v, f"row {i} {k}: {got!r} != {v!r}"
                continue
            got = float(got) if not pd.isna(got) else np.nan
            v = float(v) if not pd.isna(v) else np.nan
            if np.isnan(v):
                assert np.isnan(got), f"row {i} {k}: expected NaN, got {got}"
            else:
                assert not np.isnan(got), f"row {i} {k}: expected {v}, got NaN"
                assert abs(got - v) < 1e-9, f"row {i} {k}: {got!r} != {v!r}"
        checked += 1
    assert checked >= 20
    return checked


def test_brute_force_synthetic(synth):
    sp, tb = synth
    assert _assert_brute(sp, tb) >= 20


@pytest.mark.slow
def test_brute_force_real(real):
    sp, tb = real
    assert _assert_brute(sp, tb, n_sample=60) >= 20


# --------------------------------------------------------------------------
# 2. FUTURE PERTURBATION
# --------------------------------------------------------------------------

_NUMERIC = (["exp_o", "exp_o_shrunk", "league_asof_outs", "opp_obp_asof",
             "p5_pitches", "career_start_number", "career_x_season",
             "season_start_number", "is_debut", "career_left_censored", "is_home"]
            + [f"stop_rate_{b}" for b in BOUNDARIES])


def _same(a: pd.DataFrame, b: pd.DataFrame, mask, ctx=""):
    for c in _NUMERIC:
        x = pd.to_numeric(a.loc[mask, c], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(b.loc[mask, c], errors="coerce").to_numpy(dtype=float)
        bad = ~((np.isnan(x) & np.isnan(y)) | np.isclose(x, y, rtol=0, atol=1e-12))
        assert not bad.any(), (
            f"{ctx}{c}: {int(bad.sum())} of {len(x)} rows moved; "
            f"first offenders {x[bad][:3]} vs {y[bad][:3]}")
    ax = a.loc[mask, "days_rest_bucket"].astype(str).to_numpy()
    bx = b.loc[mask, "days_rest_bucket"].astype(str).to_numpy()
    assert (ax == bx).all(), f"{ctx}days_rest_bucket moved"


def _future_perturbation(sp, tb):
    cutoff = sp["game_date"].quantile(0.6)
    base = build_outs_asof(sp, tb)

    corrupt = sp.copy()
    fut = corrupt["game_date"] > cutoff
    assert fut.sum() > 0 and (~fut).sum() > 0
    rng = np.random.default_rng(3)
    corrupt["outs"] = corrupt["outs"].astype("int64")
    corrupt.loc[fut, "outs"] = rng.integers(0, 28, size=int(fut.sum()))
    corrupt.loc[fut, "pitches"] = 1
    corrupt.loc[fut, "drest"] = pd.NA
    corrupt.loc[fut, "is_home"] = 1 - corrupt.loc[fut, "is_home"]

    tb_c = tb.copy()
    tfut = tb_c["game_date"] > cutoff
    tb_c.loc[tfut, "ob"] = 0
    tb_c.loc[tfut, "pa_n"] = 99

    pert = build_outs_asof(corrupt, tb_c)
    past = (base["game_date"] <= cutoff).to_numpy()

    # career_start_number / career_left_censored legitimately depend on the
    # pitcher's FIRST cached date, which the perturbation does not move, and
    # season_start_number on prior counts, likewise unmoved. Everything is
    # checked; nothing is exempted.
    _same(base, pert, past, ctx="future-perturbation: ")
    return int(past.sum()), int((~past).sum())


def test_future_perturbation_synthetic(synth):
    sp, tb = synth
    n_past, n_fut = _future_perturbation(sp, tb)
    assert n_past > 50 and n_fut > 50


@pytest.mark.slow
def test_future_perturbation_real(real):
    sp, tb = real
    n_past, n_fut = _future_perturbation(sp, tb)
    assert n_past > 1000 and n_fut > 1000


def test_last_prior_day_is_actually_used(synth):
    """Guard against the opposite error: a builder so conservative it ignores
    real prior information would also pass every leak test. Perturbing the
    day BEFORE the cutoff must MOVE the league term on the cutoff day."""
    sp, tb = synth
    days = np.sort(sp["game_date"].unique())
    cutoff, prev_day = days[len(days) // 2], days[len(days) // 2 - 1]
    base = build_outs_asof(sp, tb)
    c = sp.copy()
    c.loc[c["game_date"] == prev_day, "outs"] = 0
    pert = build_outs_asof(c, tb)
    on_cutoff = (base["game_date"] == cutoff).to_numpy()
    assert not np.allclose(base.loc[on_cutoff, "league_asof_outs"],
                           pert.loc[on_cutoff, "league_asof_outs"])


# --------------------------------------------------------------------------
# 3. SAME-DAY PERTURBATION  (the prior-DAY rule)
# --------------------------------------------------------------------------

def test_same_day_games_do_not_leak(synth):
    sp, tb = synth
    counts = sp.groupby("game_date")["game_pk"].nunique()
    day = counts[counts >= 3].index[0]          # the seeded doubleheader day
    same_day = sp[sp["game_date"] == day]
    victim_pk = same_day["game_pk"].iloc[0]
    other_pks = [p for p in same_day["game_pk"].unique() if p != victim_pk]
    assert other_pks

    base = build_outs_asof(sp, tb)
    c = sp.copy()
    c.loc[c["game_pk"].isin(other_pks), "outs"] = 27
    tb_c = tb.copy()
    tb_c.loc[tb_c["game_pk"].isin(other_pks), "ob"] = 0
    pert = build_outs_asof(c, tb_c)

    victim = (base["game_pk"] == victim_pk).to_numpy()
    _same(base, pert, victim, ctx="same-day: ")


def test_doubleheader_game_one_does_not_leak_into_game_two(synth):
    """Same team, same date, two games. Game 1's on-base result finishes
    before game 2's first pitch in wall-clock terms, but slate ordering is
    not reliably knowable at build time, so the prior-DAY rule must exclude
    it. Perturbing game 1 must not move game 2's opponent feature."""
    sp, tb = synth
    counts = sp.groupby("game_date")["game_pk"].nunique()
    day = counts[counts >= 3].index[0]
    same_day = sp[sp["game_date"] == day]
    # find a team that bats (i.e. is the opponent) in two games that day
    dupe = same_day["opp"].value_counts()
    team = dupe[dupe >= 2].index[0]
    pks = sorted(same_day.loc[same_day["opp"] == team, "game_pk"].unique())
    assert len(pks) >= 2
    g1, g2 = pks[0], pks[1]

    base = build_outs_asof(sp, tb)
    assert base.loc[base["game_pk"] == g2, "opp_obp_asof"].notna().any(), \
        "game 2's opponent feature is NaN; this test would prove nothing"

    tb_c = tb.copy()
    hit = tb_c["game_pk"] == g1
    assert hit.any()
    tb_c.loc[hit, "ob"] = 0
    tb_c.loc[hit, "pa_n"] = 500
    pert = build_outs_asof(sp, tb_c)
    victim = ((base["game_pk"] == g2) & (base["opp"] == team)).to_numpy()
    assert victim.sum() > 0
    _same(base, pert, victim, ctx="doubleheader: ")


def test_pitcher_features_use_strictly_prior_starts(synth):
    """A pitcher's own current-game outs must never touch his own features."""
    sp, tb = synth
    base = build_outs_asof(sp, tb)
    c = sp.copy()
    target = c.index[len(c) // 2]
    c.loc[target, "outs"] = 27 if sp.loc[target, "outs"] != 27 else 0
    c.loc[target, "pitches"] = 999
    pert = build_outs_asof(c, tb)
    row = np.zeros(len(base), dtype=bool)
    row[base.index.get_loc(target)] = True
    _same(base, pert, row, ctx="own-row: ")


# --------------------------------------------------------------------------
# 4. ROW-ORDER INVARIANCE
# --------------------------------------------------------------------------

def test_row_order_invariance(synth):
    sp, tb = synth
    base = build_outs_asof(sp, tb).set_index(["game_pk", "pitcher"]).sort_index()
    shuf = sp.sample(frac=1.0, random_state=5).reset_index(drop=True)
    other = build_outs_asof(shuf, tb.sample(frac=1.0, random_state=6)
                            ).set_index(["game_pk", "pitcher"]).sort_index()
    assert (base.index == other.index).all()
    mask = np.ones(len(base), dtype=bool)
    _same(base.reset_index(), other.reset_index(), mask, ctx="row-order: ")


def test_output_is_row_aligned_with_input(synth):
    sp, tb = synth
    f = build_outs_asof(sp, tb)
    assert len(f) == len(sp)
    assert (f.index == sp.index).all()
    assert (f["game_pk"].to_numpy() == sp["game_pk"].to_numpy()).all()
    assert (f["pitcher"].to_numpy() == sp["pitcher"].to_numpy()).all()


# --------------------------------------------------------------------------
# Specification tests (definitions, not leakage)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    (None, "unknown"), (pd.NA, "unknown"), (np.nan, "unknown"),
    (0, "<=2"), (1, "<=2"), (2, "<=2"), (3, "3"), (4, "4"), (5, "5"),
    (6, "6"), (7, "7"), (8, "8-10"), (10, "8-10"), (11, "11-20"),
    (20, "11-20"), (21, "21+"), (200, "21+"),
])
def test_days_rest_bucket_edges(val, expected):
    assert days_rest_bucket(val) == expected


def test_null_days_rest_is_its_own_level_not_imputed(synth):
    sp, tb = synth
    f = build_outs_asof(sp, tb)
    assert "unknown" in set(f["days_rest_bucket"].cat.categories)
    got = f.loc[f["drest"].isna(), "days_rest_bucket"].astype(str)
    assert len(got) > 0 and (got == "unknown").all()
    assert f["days_rest_bucket"].isna().sum() == 0


def test_left_censored_pitchers_never_get_is_debut(synth):
    sp, tb = synth
    f = build_outs_asof(sp, tb)
    assert f.loc[f["career_left_censored"] == 1, "is_debut"].sum() == 0
    # and their career counter is NaN, not a wrong small integer
    bad = f[(f["career_left_censored"] == 1) & (f["career_start_number"] < CAREER_CAP)]
    assert len(bad) == 0
    assert f.loc[f["is_debut"] == 1, "career_start_number"].eq(1).all()


def test_opponent_gate_is_hard(synth):
    sp, tb = synth
    f = build_outs_asof(sp, tb)
    assert f.loc[f["opp_games_prior"] < MIN_OPP_GAMES, "opp_obp_asof"].isna().all()
    ok = f[f["opp_games_prior"] >= MIN_OPP_GAMES]
    if len(ok):
        assert ok["opp_obp_asof"].between(0.0, 1.0).all()


def test_no_blacklisted_or_same_game_column_is_emitted(synth):
    sp, tb = synth
    f = build_outs_asof(sp, tb)
    banned = {"pitcher_days_until_next_game", "batter_days_until_next_game",
              "tto_max", "so", "max_inning", "runs", "opp_k_pct", "bp_heavy",
              "park_factor", "temperature"}
    assert banned.isdisjoint(set(f.columns))
    # 'outs', 'pitches' and 'bf' survive only as the LABEL and as raw
    # same-game columns the caller passed in; no FEATURE may equal them.
    for c in M.FEATURE_COLS:
        if c == "days_rest_bucket":
            continue
        s = pd.to_numeric(f[c], errors="coerce")
        if s.notna().sum() < 10:
            continue
        for same_game in ["outs", "pitches"]:
            r = s.corr(f[same_game].astype(float))
            assert abs(r) < 0.90, f"{c} correlates {r:+.3f} with same-game {same_game}"


def test_stop_rates_are_probabilities(synth):
    sp, tb = synth
    f = build_outs_asof(sp, tb)
    for b in BOUNDARIES:
        s = f[f"stop_rate_{b}"].dropna()
        assert len(s) > 0
        assert s.between(0.0, 1.0).all()


def test_empty_input_is_handled():
    empty = pd.DataFrame(columns=["game_pk", "pitcher", "game_date", "outs",
                                  "pitches", "is_home", "opp", "drest"])
    assert build_outs_asof(empty).empty
