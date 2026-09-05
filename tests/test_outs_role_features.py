"""A-054: the ROLE block of the outs feature builder and the hazard model's
feature-set plumbing.

Attacks the block the same ways tests/test_outs_asof.py attacks the rest:
brute force against a plain loop over strictly-prior appearances, future
perturbation, and a serve-vs-train identity check -- the builder's columns
for a today-row must equal what tools/outs_serve._appearance_lookup reads
off the raw pitch cache for the same pitcher, or the model would be fitted
on one definition and served another.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.backfill_statcast as BS
import models.outs_hazard as H
import tools.outs_serve as OS
from features import outs_asof as M
from features.outs_asof import (
    ROLE_RELIEF_CAP, build_appearances_table, build_outs_asof)
from tests.test_outs_asof import _synthetic

ROLE_COLS = ["prev_app_relief", "prev_app_pitches", "relief_since_start"]


# --------------------------------------------------------------------------
# fixtures: the synthetic league plus relief appearances for some pitchers
# --------------------------------------------------------------------------

def _with_appearances(seed: int = 3):
    """Starts from the shared synthetic league, plus an appearance table
    that holds every start AND relief outings: some pitchers relieve
    between starts, a few relieve BEFORE their first start of a season,
    and one relieves in game two of a doubleheader day he started game
    one of (the same-date tie the builder must resolve to the later game).
    """
    sp, tb = _synthetic()
    rng = np.random.default_rng(seed)
    app = sp[["game_pk", "pitcher", "game_date", "pitches"]].copy()
    app["is_start"] = 1
    extra = []
    pk = 900_000
    pitchers = sorted(sp["pitcher"].unique())
    dates = np.sort(sp["game_date"].unique())
    for p in pitchers[::3]:                      # a third of the league relieves
        for d in rng.choice(dates, size=6, replace=False):
            pk += 1
            extra.append({"game_pk": pk, "pitcher": p, "game_date": pd.Timestamp(d),
                          "pitches": int(rng.integers(8, 50)), "is_start": 0})
    # relief before the first start of a season, for two pitchers
    for p in pitchers[1:3]:
        for season, start in M.SEASON_STARTS.items():
            base = pd.Timestamp(start)
            for k in (1, 3):
                pk += 1
                extra.append({"game_pk": pk, "pitcher": p, "game_date": base + pd.Timedelta(days=k),
                              "pitches": 15 + k, "is_start": 0})
    # doubleheader: start game one, relieve game two, same date
    row = sp.iloc[10]
    pk += 1
    extra.append({"game_pk": pk, "pitcher": int(row["pitcher"]), "game_date": row["game_date"],
                  "pitches": 12, "is_start": 0})
    apps = pd.concat([app, pd.DataFrame(extra)], ignore_index=True)
    apps["pitches"] = apps["pitches"].astype("float64")
    apps["is_start"] = apps["is_start"].astype("int8")
    return sp, tb, apps


@pytest.fixture(scope="module")
def league():
    return _with_appearances()


def _brute_role(apps: pd.DataFrame, row) -> dict:
    """Plain loop: the pitcher's appearances this season strictly before the
    start's date, ordered (date, game_pk); the last one is 'previous'."""
    season = row["game_date"].year
    mine = apps[(apps["pitcher"] == row["pitcher"])
                & (apps["game_date"].dt.year == season)
                & (apps["game_date"] < row["game_date"])]
    mine = mine.sort_values(["game_date", "game_pk"], kind="mergesort")
    if mine.empty:
        return {"prev_app_relief": 0.0, "prev_app_pitches": np.nan,
                "relief_since_start": np.nan}
    last = mine.iloc[-1]
    starts = np.flatnonzero(mine["is_start"].to_numpy() == 1)
    if len(starts):
        since = int((mine["is_start"].to_numpy()[starts[-1] + 1:] == 0).sum())
    else:
        since = int((mine["is_start"].to_numpy() == 0).sum())
    return {"prev_app_relief": float(last["is_start"] == 0),
            "prev_app_pitches": float(last["pitches"]),
            "relief_since_start": float(min(since, ROLE_RELIEF_CAP))}


# --------------------------------------------------------------------------
# 1. brute force
# --------------------------------------------------------------------------

def test_role_block_matches_a_plain_loop(league):
    sp, tb, apps = league
    feat = build_outs_asof(sp, tb, appearances=apps)
    assert feat["prev_app_relief"].notna().all()
    assert (feat["prev_app_relief"] == 1).sum() >= 20, "relief rows not exercised"
    assert feat["prev_app_pitches"].isna().sum() >= 5, "no-prior-appearance rows not exercised"
    rng = np.random.default_rng(5)
    idx = set(rng.choice(feat.index, size=150, replace=False).tolist())
    for mask in [feat["prev_app_relief"] == 1, feat["prev_app_pitches"].isna(),
                 feat["relief_since_start"] >= ROLE_RELIEF_CAP,
                 feat["season_start_number"] == 1]:
        idx.update(feat.index[mask.to_numpy()][:8].tolist())
    for i in sorted(idx):
        row = feat.loc[i]
        want = _brute_role(apps, row)
        for k, v in want.items():
            got = float(row[k]) if not pd.isna(row[k]) else np.nan
            if np.isnan(v):
                assert np.isnan(got), f"row {i} {k}: expected NaN, got {got}"
            else:
                assert got == pytest.approx(v), f"row {i} {k}: {got} != {v}"


def test_relief_before_first_start_is_seen_and_counted(league):
    sp, tb, apps = league
    feat = build_outs_asof(sp, tb, appearances=apps)
    p = sorted(sp["pitcher"].unique())[1]
    first = feat[(feat["pitcher"] == p)].sort_values("game_date").iloc[0]
    # his first START of the season follows two relief outings
    assert first["season_start_number"] == 1
    assert first["prev_app_relief"] == 1
    assert first["relief_since_start"] == 2
    assert first["prev_app_pitches"] == 18                 # the k=3 outing
    # ...while p5_pitches, built over prior STARTS, cannot see them
    assert pd.isna(first["p5_pitches"])


def test_doubleheader_relief_on_the_start_date_is_invisible(league):
    """Strictly prior DATE: a relief outing later the same day as a start
    is not that start's previous appearance, and neither is game one to
    game two -- the serve path's cache ends yesterday."""
    sp, tb, apps = league
    feat = build_outs_asof(sp, tb, appearances=apps)
    row = sp.iloc[10]
    f = feat[(feat["game_pk"] == row["game_pk"]) & (feat["pitcher"] == row["pitcher"])].iloc[0]
    want = _brute_role(apps, f)
    assert f["prev_app_pitches"] == pytest.approx(want["prev_app_pitches"]) or \
        (pd.isna(f["prev_app_pitches"]) and np.isnan(want["prev_app_pitches"]))
    # the same-date relief outing (12 pitches) is not what the row sees
    assert f["prev_app_pitches"] != 12


# --------------------------------------------------------------------------
# 2. future perturbation
# --------------------------------------------------------------------------

def test_future_appearances_cannot_move_earlier_rows(league):
    sp, tb, apps = league
    cut = pd.Timestamp("2025-06-15")
    a = build_outs_asof(sp, tb, appearances=apps)
    bad = apps.copy()
    late = bad["game_date"] > cut
    bad.loc[late, "pitches"] = 999.0
    bad.loc[late, "is_start"] = 1 - bad.loc[late, "is_start"]
    b = build_outs_asof(sp, tb, appearances=bad)
    mask = (a["game_date"] <= cut).to_numpy()
    for c in ROLE_COLS:
        x = a.loc[mask, c].to_numpy(float)
        y = b.loc[mask, c].to_numpy(float)
        same = (np.isnan(x) & np.isnan(y)) | np.isclose(x, y, rtol=0, atol=1e-12)
        assert same.all(), f"{c}: {int((~same).sum())} rows before the cutoff moved"
    # and rows AFTER the cutoff did move, or the test proves nothing
    assert not np.allclose(a.loc[~mask, "prev_app_pitches"].fillna(-1),
                           b.loc[~mask, "prev_app_pitches"].fillna(-1))


def test_no_appearance_table_leaves_the_block_blank(league):
    sp, tb, _ = league
    f = build_outs_asof(sp, tb)
    for c in ROLE_COLS:
        assert f[c].isna().all(), c


# --------------------------------------------------------------------------
# 3. serve == train
# --------------------------------------------------------------------------

def _pitch_rows(game_pk, game_date, home, away, top, bot):
    rows, ab = [], 0
    for half, plist in (("Top", top), ("Bot", bot)):
        for pid, n in plist:
            for k in range(n):
                if k % 5 == 0:
                    ab += 1
                rows.append({"pitcher": pid, "game_date": game_date, "game_pk": game_pk,
                             "game_type": "R", "inning": ab // 6 + 1,
                             "at_bat_number": ab, "pitch_number": k % 5 + 1,
                             "inning_topbot": half, "home_team": home, "away_team": away,
                             "events": None, "outs_when_up": 0,
                             "post_home_score": 0, "post_away_score": 0,
                             "pitcher_days_since_prev_game": None})
    return rows


def _pitch_cache():
    rows = []
    rows += _pitch_rows(1, "2026-06-01", "NYY", "BOS", top=[(1, 40)], bot=[(2, 45), (5, 20)])
    rows += _pitch_rows(2, "2026-06-05", "NYY", "TB", top=[(3, 30), (1, 30)], bot=[(4, 50), (5, 15)])
    df = pd.DataFrame(rows)
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


def test_builder_and_serve_lookup_agree_on_a_today_row(monkeypatch):
    """The same pitch rows through both paths: the appearance table +
    builder (training) and _appearance_lookup (the sidecar's role block)."""
    cache = _pitch_cache()
    monkeypatch.setattr(BS, "load_cached", lambda a, b: cache)
    serve = OS._appearance_lookup("2026-06-10", [1, 3, 5])

    # training path: PA table -> appearances -> builder on a today-row
    pa = (cache.sort_values(["game_pk", "inning", "inning_topbot", "at_bat_number", "pitch_number"])
                .groupby(["game_pk", "at_bat_number"], sort=False)
                .agg(game_date=("game_date", "first"), inning=("inning", "first"),
                     inning_topbot=("inning_topbot", "first"), pitcher=("pitcher", "first"),
                     home_team=("home_team", "first"), away_team=("away_team", "first"),
                     n_pitches=("pitch_number", "count")).reset_index())
    apps = build_appearances_table(pa)
    assert set(apps.columns) == {"game_pk", "pitcher", "game_date", "pitches", "is_start"}
    assert apps.set_index(["game_pk", "pitcher"]).loc[(2, 1), "is_start"] == 0
    assert apps.set_index(["game_pk", "pitcher"]).loc[(2, 3), "is_start"] == 1
    today = pd.DataFrame([
        {"game_pk": 7, "pitcher": p, "game_date": pd.Timestamp("2026-06-10"),
         "outs": 0, "pitches": 0.0, "is_home": 1, "opp": "SEA", "drest": pd.NA}
        for p in (1, 3, 5)])
    feat = build_outs_asof(today, None, appearances=apps).set_index("pitcher")
    for p in (1, 3, 5):
        s = serve[p]
        assert feat.loc[p, "prev_app_relief"] == float(not s["prev_app_was_start"])
        assert feat.loc[p, "prev_app_pitches"] == float(s["prev_app_pitches"])
        assert feat.loc[p, "relief_since_start"] == float(
            min(s["relief_apps_since_last_start"], ROLE_RELIEF_CAP))


# --------------------------------------------------------------------------
# 4. the model's feature-set plumbing
# --------------------------------------------------------------------------

def _fit_frame(league):
    sp, tb, apps = league
    feat = build_outs_asof(sp, tb, appearances=apps)
    # explode_states needs the inning context the synthetic table lacks
    outs = feat["outs"].to_numpy(int)
    feat["max_inning"] = np.maximum(1, (outs + 2) // 3)
    feat.loc[outs % 3 == 0, "max_inning"] = np.maximum(1, outs[outs % 3 == 0] // 3)
    feat["game_max_inning"] = 9
    return feat


def test_role_fit_round_trips_its_spec(tmp_path, league):
    feat = _fit_frame(league)
    yr = feat["game_date"].dt.year
    train, test = feat[yr == 2024], feat[yr == 2025]
    m = H.OutsHazard()
    m.fit(train, test_df=test, lam=30.0, verbose=False, features=H.ROLE_FEATURES)
    # the gated set: the pitch count alone, NaN through the EXISTING block
    assert "prev_app_pitches" in m.spec.names
    assert "miss_role" not in m.spec.names
    assert not ({"prev_app_relief", "relief_since_start"} & set(m.spec.names))
    assert m.spec.column_blocks["prev_app_pitches"] == "miss_budget"
    assert m.spec.feature_set == "role" and m.meta["feature_set"] == "role"
    d = m.spec.to_dict()
    assert "miss_role" not in d["missing_blocks"]
    back = H.DesignSpec.from_dict(d)
    assert back.names == m.spec.names and back.column_blocks == m.spec.column_blocks
    # an OLD pkl dict without the block fields still loads as the base set
    old = {k: v for k, v in d.items() if k not in ("missing_blocks", "column_blocks", "feature_set")}
    legacy = H.DesignSpec.from_dict(old)
    assert legacy.missing_blocks == H.MISSING_BLOCKS and legacy.feature_set == "base"
    # predictions are a proper PMF with the block present
    pmf = m.predict_pmf_frame(test)
    assert np.allclose(pmf.sum(axis=1), 1.0, atol=1e-9)


def test_the_rejected_separate_indicator_still_fits_for_the_record(league):
    """tools/gate_outs_role.py keeps the miss_role routing as a REJECTED
    candidate; the plumbing must still build it so the record is rerunnable."""
    feat = _fit_frame(league)
    yr = feat["game_date"].dt.year
    fs = H.BASE_FEATURES.extend("role3", numeric=H.ROLE_NUMERIC, binary=H.ROLE_BINARY,
                                missing_blocks=H.ROLE_MISSING_BLOCK,
                                column_blocks=H.ROLE_COLUMN_BLOCK)
    m = H.OutsHazard()
    m.fit(feat[yr == 2024], test_df=feat[yr == 2025], lam=30.0, verbose=False, features=fs)
    assert "miss_role" in m.spec.names
    assert m.spec.to_dict()["missing_blocks"]["miss_role"] == "prev_app_pitches"


def test_shadow_loader_is_quiet_when_the_pkl_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(OS, "ROLE_MODEL_PATH", tmp_path / "nope.pkl")
    assert OS.load_shadow_model() is None
    assert "p_over_shadow" in OS.LOG_FIELDS


def test_base_fit_ignores_the_role_columns_and_the_flag_is_off(league):
    feat = _fit_frame(league)
    yr = feat["game_date"].dt.year
    assert H.ROLE_FEATURES_ENABLED is False
    assert H.default_features() is H.BASE_FEATURES
    m = H.OutsHazard()
    m.fit(feat[yr == 2024], test_df=feat[yr == 2025], lam=30.0, verbose=False)
    assert not (set(H.ROLE_NUMERIC + H.ROLE_BINARY) & set(m.spec.names))
    assert "miss_role" not in m.spec.names
    # the sign contract reports the role terms as absent, not as failures
    signs = m.fit_report["signs"]
    role = signs[signs["term"].isin(H.ROLE_NUMERIC + H.ROLE_BINARY)]
    assert len(role) == 3 and role["ok"].all() and (role["note"] == "not in design").all()


def test_role_fit_refuses_without_the_block(league):
    sp, tb, _ = league
    feat = build_outs_asof(sp, tb)                       # no appearances
    outs = feat["outs"].to_numpy(int)
    feat["max_inning"] = np.maximum(1, (outs + 2) // 3)
    feat["game_max_inning"] = 9
    yr = feat["game_date"].dt.year
    with pytest.raises(ValueError, match="entirely missing"):
        H.OutsHazard().fit(feat[yr == 2024], lam=30.0, verbose=False,
                           features=H.ROLE_FEATURES)


def test_serve_fields_are_none_without_a_shadow(monkeypatch):
    """price_board's shadow fields must be None, not absent, when no shadow
    pkl exists -- the log field is blank and the page tolerates null."""
    monkeypatch.setattr(OS, "load_shadow_model", lambda: None)
    assert OS.load_shadow_model() is None
