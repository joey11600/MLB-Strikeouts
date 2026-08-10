"""Incremental-signal screen for starting-pitcher strikeout factors.

The question this tool answers is NOT "does factor X predict strikeouts".
Almost everything predicts strikeouts. The question is "does factor X predict
the part of strikeouts the posted line does not already contain".

Measured motivation (see docs/ and the task brief):
  corr(posted line, actual_K)      = 0.502   (n=126 logged pitcher-games)
  corr(shipped model E[K], actual) = 0.409
  w* (Brier-optimal weight on model-minus-market) = -0.775

So the benchmark here is a market-shaped baseline, and a factor earns its
place only by beating that baseline out-of-sample in ALL THREE mandated
temporal directions (CLAUDE.md "Test methodology rules", docs/GATES.md).

Stages
------
  scan      one pass over data/statcast_cache -> pitch/PA/entity aggregates
  features  as-of assembly (cumsum-minus-current, per-season reset)
  screen    the screen itself; prints the ranked table

Usage
-----
  python tools/factor_screen_k.py scan
  python tools/factor_screen_k.py features
  python tools/factor_screen_k.py screen

Intermediates are cached under $FSK_CACHE (default data/_fsk_cache, which is
regenerable and should not be committed).
"""
from __future__ import annotations

import os
import sys
import glob
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("FSK_CACHE", REPO / "data" / "_fsk_cache"))
CACHE.mkdir(parents=True, exist_ok=True)
STATCAST = REPO / "data" / "statcast_cache"

# --------------------------------------------------------------------------
# Shared vocabulary. Defined ONCE so a new factor cannot silently disagree
# with the shipped A1/A3 definitions and manufacture a fake (or fake-null)
# result. NOTE: features/asof.py counts foul_tip as a whiff; Statcast/Savant
# does not (foul_tip is contact). We follow Savant here and say so.
# --------------------------------------------------------------------------
SWING_DESCS = {
    "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
    "foul_bunt", "bunt_foul_tip", "hit_into_play", "missed_bunt",
}
WHIFF_DESCS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
TAKEN_DESCS = {"ball", "called_strike", "blocked_ball"}
STRIKE_DESCS = {
    "called_strike", "swinging_strike", "swinging_strike_blocked", "foul",
    "foul_tip", "foul_bunt", "bunt_foul_tip", "hit_into_play", "missed_bunt",
}
K_EVENTS = {"strikeout", "strikeout_double_play"}

FB = {"FF", "SI", "FC"}
BB = {"SL", "ST", "CU", "KC", "SV", "CS"}
OS = {"CH", "FS", "FO", "EP", "KN"}

ZONE_HALF_WIDTH = 0.7083          # ft, plate half-width + ball radius
SHADOW = 0.25                     # ft, +/- 3 inches around the rulebook edge
NBIN = 6                          # shadow band bins (1 inch each)

SCAN_COLS = [
    "game_pk", "game_date", "game_year", "game_type", "pitcher", "batter",
    "events", "description", "pitch_type", "release_speed", "release_pos_x",
    "release_pos_z", "pfx_x", "pfx_z", "plate_x", "plate_z", "sz_top",
    "sz_bot", "strikes", "stand", "p_throws", "home_team", "away_team",
    "inning_topbot", "at_bat_number", "pitch_number", "fielder_2",
    "bat_speed", "swing_length", "arm_angle", "release_spin_rate",
    "n_thruorder_pitcher",
]


def cache_files() -> list[str]:
    """All readable cache files. 26 of 571 carry a zero-column schema and
    raise ArrowInvalid on a columns= read; guard them."""
    fs = sorted(glob.glob(str(STATCAST / "**" / "*.parquet"), recursive=True))
    return [f for f in fs if len(pq.ParquetFile(f).schema_arrow.names) > 0]


def _pclass(pt: pd.Series) -> pd.Series:
    out = pd.Series("XX", index=pt.index, dtype=object)
    out[pt.isin(FB)] = "FB"
    out[pt.isin(BB)] = "BB"
    out[pt.isin(OS)] = "OS"
    return out


# ==========================================================================
# STAGE 1 -- scan
# ==========================================================================
def scan_one(path: str):
    df = pq.read_table(path, columns=SCAN_COLS).to_pandas()
    df = df[df["game_type"] == "R"]
    if df.empty:
        return None
    df = df.reset_index(drop=True)

    desc = df["description"].astype(object)
    sw = desc.isin(SWING_DESCS).to_numpy()
    wh = desc.isin(WHIFF_DESCS).to_numpy()
    tk = desc.isin(TAKEN_DESCS).to_numpy()
    cs = (desc == "called_strike").to_numpy()
    strike = desc.isin(STRIKE_DESCS).to_numpy()
    isk = df["events"].isin(K_EVENTS).to_numpy()

    px = df["plate_x"].astype(float).to_numpy()
    pz = df["plate_z"].astype(float).to_numpy()
    st = df["sz_top"].astype(float).to_numpy()
    sb = df["sz_bot"].astype(float).to_numpy()
    with np.errstate(invalid="ignore"):
        d_edge = np.fmax(np.fmax(np.abs(px) - ZONE_HALF_WIDTH, sb - pz), pz - st)
    inzone = d_edge <= 0
    ozband = (d_edge > 0) & (d_edge <= 0.5)     # the temptable band
    shadow = np.abs(d_edge) <= SHADOW
    geo_ok = np.isfinite(d_edge)

    pt = df["pitch_type"].astype(object)
    cls = _pclass(pt)
    two = (df["strikes"] == 2).to_numpy()
    fp = (df["pitch_number"] == 1).to_numpy()

    pitching_team = np.where(df["inning_topbot"].to_numpy() == "Top",
                             df["home_team"].to_numpy(), df["away_team"].to_numpy())
    df["pitching_team"] = pitching_team

    # ---------------- PA table -------------------------------------------
    base = pd.DataFrame({
        "game_pk": df["game_pk"], "at_bat_number": df["at_bat_number"],
        "game_date": df["game_date"], "game_year": df["game_year"],
        "pitcher": df["pitcher"], "batter": df["batter"],
        "stand": df["stand"], "p_throws": df["p_throws"],
        "inning_topbot": df["inning_topbot"], "pitching_team": pitching_team,
        "isk": isk.astype(np.int8), "two": two.astype(np.int8),
        "tto": df["n_thruorder_pitcher"],
        "fps": (fp & strike).astype(np.int8), "fp": fp.astype(np.int8),
    })
    g = base.groupby(["game_pk", "at_bat_number"], sort=False)
    pa = g.agg(
        game_date=("game_date", "first"), game_year=("game_year", "first"),
        pitcher=("pitcher", "first"), batter=("batter", "first"),
        stand=("stand", "first"), p_throws=("p_throws", "first"),
        inning_topbot=("inning_topbot", "first"),
        pitching_team=("pitching_team", "first"),
        npitch=("isk", "size"), isk=("isk", "max"), two=("two", "max"),
        tto=("tto", "max"), fps=("fps", "max"), fp=("fp", "max"),
    ).reset_index()

    # ---------------- pitcher x pitch_type (long) ------------------------
    rx = df["release_pos_x"].astype(float).to_numpy()
    rz = df["release_pos_z"].astype(float).to_numpy()
    vel = df["release_speed"].astype(float).to_numpy()
    fx = df["pfx_x"].astype(float).to_numpy()
    fz = df["pfx_z"].astype(float).to_numpy()
    spin = df["release_spin_rate"].astype(float).to_numpy()

    ptt = pd.DataFrame({
        "game_pk": df["game_pk"], "game_date": df["game_date"],
        "game_year": df["game_year"], "pitcher": df["pitcher"],
        "pitch_type": pt.fillna("NA"),
        "n": 1, "sw": sw.astype(np.int32), "wh": wh.astype(np.int32),
        "ts_n": two.astype(np.int32), "ts_k": (two & isk).astype(np.int32),
        "vel_s": np.where(np.isfinite(vel), vel, 0.0),
        "vel_n": np.isfinite(vel).astype(np.int32),
        "fx_s": np.where(np.isfinite(fx), fx, 0.0),
        "fz_s": np.where(np.isfinite(fz), fz, 0.0),
        "f_n": (np.isfinite(fx) & np.isfinite(fz)).astype(np.int32),
        "sp_s": np.where(np.isfinite(spin), spin, 0.0),
        "sp_n": np.isfinite(spin).astype(np.int32),
        "rx_s": np.where(np.isfinite(rx), rx, 0.0),
        "rx_q": np.where(np.isfinite(rx), rx * rx, 0.0),
        "rz_s": np.where(np.isfinite(rz), rz, 0.0),
        "rz_q": np.where(np.isfinite(rz), rz * rz, 0.0),
        "r_n": (np.isfinite(rx) & np.isfinite(rz)).astype(np.int32),
    })
    PT = ptt.groupby(["game_pk", "pitcher", "pitch_type"], sort=False).agg(
        game_date=("game_date", "first"), game_year=("game_year", "first"),
        **{c: (c, "sum") for c in ["n", "sw", "wh", "ts_n", "ts_k", "vel_s",
                                   "vel_n", "fx_s", "fz_s", "f_n", "sp_s",
                                   "sp_n", "rx_s", "rx_q", "rz_s", "rz_q", "r_n"]}
    ).reset_index()

    # ---------------- pitcher-game ---------------------------------------
    arm = df["arm_angle"].astype(float).to_numpy()
    pgd = pd.DataFrame({
        "game_pk": df["game_pk"], "game_date": df["game_date"],
        "game_year": df["game_year"], "pitcher": df["pitcher"],
        "pitching_team": pitching_team, "at_bat_number": df["at_bat_number"],
        "pitches": 1, "sw": sw.astype(np.int32), "wh": wh.astype(np.int32),
        "tk": tk.astype(np.int32), "cs": cs.astype(np.int32),
        "zone_valid": geo_ok.astype(np.int32),
        "zone_in": (geo_ok & inzone).astype(np.int32),
        "ts_pitch": two.astype(np.int32),
        "arm_s": np.where(np.isfinite(arm), arm, 0.0),
        "arm_n": np.isfinite(arm).astype(np.int32),
    })
    PG = pgd.groupby(["game_pk", "pitcher"], sort=False).agg(
        game_date=("game_date", "first"), game_year=("game_year", "first"),
        pitching_team=("pitching_team", "first"),
        first_ab=("at_bat_number", "min"),
        **{c: (c, "sum") for c in ["pitches", "sw", "wh", "tk", "cs",
                                   "zone_valid", "zone_in", "ts_pitch",
                                   "arm_s", "arm_n"]}
    ).reset_index()

    # ---------------- batter-game ----------------------------------------
    bs = df["bat_speed"].astype(float).to_numpy()
    sl = df["swing_length"].astype(float).to_numpy()
    bsw = sw & np.isfinite(bs)
    bgd = pd.DataFrame({
        "game_pk": df["game_pk"], "game_date": df["game_date"],
        "game_year": df["game_year"], "batter": df["batter"],
        "pitches": 1, "sw": sw.astype(np.int32), "wh": wh.astype(np.int32),
        "iz_sw": (geo_ok & inzone & sw).astype(np.int32),
        "iz_wh": (geo_ok & inzone & wh).astype(np.int32),
        "oz_p": (geo_ok & ozband).astype(np.int32),
        "oz_sw": (geo_ok & ozband & sw).astype(np.int32),
        "fb_sw": (sw & (cls == "FB")).astype(np.int32),
        "fb_wh": (wh & (cls == "FB")).astype(np.int32),
        "bb_sw": (sw & (cls == "BB")).astype(np.int32),
        "bb_wh": (wh & (cls == "BB")).astype(np.int32),
        "os_sw": (sw & (cls == "OS")).astype(np.int32),
        "os_wh": (wh & (cls == "OS")).astype(np.int32),
        "bs_s": np.where(bsw, bs, 0.0), "bs_n": bsw.astype(np.int32),
        "sl_s": np.where(bsw & np.isfinite(sl), sl, 0.0),
    })
    BG = bgd.groupby(["game_pk", "batter"], sort=False).agg(
        game_date=("game_date", "first"), game_year=("game_year", "first"),
        **{c: (c, "sum") for c in ["pitches", "sw", "wh", "iz_sw", "iz_wh",
                                   "oz_p", "oz_sw", "fb_sw", "fb_wh", "bb_sw",
                                   "bb_wh", "os_sw", "os_wh", "bs_s", "bs_n",
                                   "sl_s"]}
    ).reset_index()

    # ---------------- shadow-band bins (pitcher and catcher) --------------
    m = shadow & tk & geo_ok
    if m.sum() > 0:
        b = np.clip(((d_edge[m] + SHADOW) / (2 * SHADOW / NBIN)).astype(int), 0, NBIN - 1)
        sh = pd.DataFrame({
            "game_pk": df["game_pk"].to_numpy()[m],
            "game_date": df["game_date"].to_numpy()[m],
            "game_year": df["game_year"].to_numpy()[m],
            "pitcher": df["pitcher"].to_numpy()[m],
            "catcher": df["fielder_2"].to_numpy()[m],
            "pitching_team": pitching_team[m],
            "stand": df["stand"].to_numpy()[m],
            "bin": b, "tk": 1, "cs": cs[m].astype(np.int32),
        })
        PSH = sh.groupby(["game_pk", "pitcher", "bin", "stand"], sort=False).agg(
            game_date=("game_date", "first"), game_year=("game_year", "first"),
            tk=("tk", "sum"), cs=("cs", "sum")).reset_index()
        CSH = sh.groupby(["game_pk", "catcher", "bin", "stand"], sort=False).agg(
            game_date=("game_date", "first"), game_year=("game_year", "first"),
            tk=("tk", "sum"), cs=("cs", "sum")).reset_index()
        TSH = sh.groupby(["game_pk", "pitching_team", "bin", "stand"], sort=False).agg(
            game_date=("game_date", "first"), game_year=("game_year", "first"),
            tk=("tk", "sum"), cs=("cs", "sum")).reset_index()
    else:
        PSH = CSH = TSH = None

    # ---------------- park-day FF vertical break --------------------------
    ffm = (pt == "FF").to_numpy() & np.isfinite(fz)
    PARK = pd.DataFrame({
        "game_date": df["game_date"].to_numpy()[ffm],
        "home_team": df["home_team"].to_numpy()[ffm],
        "fz": fz[ffm], "n": 1,
    }).groupby(["game_date", "home_team"], sort=False).agg(
        ff_fz_s=("fz", "sum"), ff_n=("n", "sum")).reset_index()

    # pitcher -> catcher link (for the team-framing join)
    PCAT = pd.DataFrame({
        "game_pk": df["game_pk"], "pitcher": df["pitcher"],
        "catcher": df["fielder_2"], "pitching_team": pitching_team, "n": 1,
    }).groupby(["game_pk", "pitcher", "catcher", "pitching_team"],
               as_index=False)["n"].sum()

    return pa, PT, PG, BG, PSH, CSH, TSH, PARK, PCAT


def stage_scan():
    files = cache_files()
    print(f"[scan] {len(files)} readable cache files")
    acc = {k: [] for k in ["pa", "pt", "pg", "bg", "psh", "csh", "tsh",
                           "park", "pcat"]}
    for i, f in enumerate(files):
        r = scan_one(f)
        if r is None:
            continue
        for k, v in zip(acc.keys(), r):
            if v is not None and len(v):
                acc[k].append(v)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(files)}")
    for k, v in acc.items():
        d = pd.concat(v, ignore_index=True)
        d.to_parquet(CACHE / f"{k}.parquet", index=False)
        print(f"[scan] {k}: {d.shape}")


# ==========================================================================
# STAGE 2 -- as-of features
# ==========================================================================
def _prior_within_season(df, keys, cols, order=("game_date", "game_pk")):
    """cumsum-minus-current within (keys + season). The sanctioned
    leakage-safe path: every value is the state of the world strictly
    BEFORE this row's game."""
    d = df.sort_values(list(keys) + list(order)).copy()
    g = d.groupby(list(keys), sort=False)
    for c in cols:
        d["prior_" + c] = g[c].cumsum() - d[c]
    return d


def _prior_day(day_tbl, keys, cols):
    """Prior calendar DAY totals within season. Strictly earlier dates only,
    so a doubleheader cannot leak into its own twin."""
    d = day_tbl.sort_values(list(keys) + ["game_date"]).copy()
    g = d.groupby(list(keys), sort=False)
    for c in cols:
        d["prior_" + c] = g[c].cumsum() - d[c]
    return d


def _shrink(num, den, prior_rate, w):
    return (num + w * prior_rate) / (den + w)


def stage_features():
    pa = pd.read_parquet(CACHE / "pa.parquet")
    PT = pd.read_parquet(CACHE / "pt.parquet")
    PG = pd.read_parquet(CACHE / "pg.parquet")
    BG = pd.read_parquet(CACHE / "bg.parquet")
    PSH = pd.read_parquet(CACHE / "psh.parquet")
    CSH = pd.read_parquet(CACHE / "csh.parquet")
    PARK = pd.read_parquet(CACHE / "park.parquet")
    for d in (pa, PT, PG, BG, PSH, CSH, PARK):
        d["game_date"] = pd.to_datetime(d["game_date"])

    # ---- starters ---------------------------------------------------------
    # pitcher of the first PA of the game for each pitching side.
    first = pa.sort_values(["game_pk", "at_bat_number"]).groupby(
        ["game_pk", "pitching_team"], as_index=False).first()
    starters = first[["game_pk", "pitching_team", "pitcher", "game_date",
                      "game_year", "inning_topbot"]].copy()
    starters["is_starter"] = 1
    PG = PG.merge(starters[["game_pk", "pitcher", "is_starter"]],
                  on=["game_pk", "pitcher"], how="left")
    PG["is_starter"] = PG["is_starter"].fillna(0).astype(int)

    # ---- start-level spine (from the validated per-start table) -----------
    st = starters.rename(columns={"pitching_team": "team"}).copy()
    hg = pa.groupby("game_pk").agg(home_team=("pitching_team", "first")).reset_index()
    gm = pa.groupby(["game_pk"]).agg(
        t1=("pitching_team", "min"), t2=("pitching_team", "max")).reset_index()
    st = st.merge(gm, on="game_pk", how="left")
    st["opponent_team"] = np.where(st["team"] == st["t1"], st["t2"], st["t1"])
    st["is_home"] = (st["inning_topbot"] == "Top").astype(int)
    st = st.drop(columns=["t1", "t2"])

    # per-start outcome + workload from the PA table (starter's PAs only)
    sp = pa.merge(st[["game_pk", "pitcher", "team"]], on=["game_pk", "pitcher"],
                  how="inner")
    agg = sp.groupby(["game_pk", "pitcher"], as_index=False).agg(
        bf=("isk", "size"), actual_k=("isk", "sum"),
        pa_pitches=("npitch", "sum"), two_pa=("two", "sum"),
        fps_n=("fps", "sum"))
    st = st.merge(agg, on=["game_pk", "pitcher"], how="left")

    print(f"[features] starts spine {st.shape}, mean K {st.actual_k.mean():.3f}")

    # ======================================================================
    # PITCHER as-of  (prior GAMES within season)
    # ======================================================================
    P = PG.copy()
    P = P.merge(pa.groupby(["game_pk", "pitcher"], as_index=False).agg(
        bf_g=("isk", "size"), k_g=("isk", "sum"), two_g=("two", "sum"),
        fps_g=("fps", "sum"), fp_g=("fp", "sum")),
        on=["game_pk", "pitcher"], how="left")
    pcols = ["pitches", "sw", "wh", "tk", "cs", "zone_valid", "zone_in",
             "ts_pitch", "arm_s", "arm_n", "bf_g", "k_g", "two_g", "fps_g",
             "fp_g"]
    P = _prior_within_season(P, ["pitcher", "game_year"], pcols)
    P["start_idx"] = P.groupby(["pitcher", "game_year"]).cumcount()

    # league as-of rates (prior day, per season) for shrink targets
    lg = P.groupby(["game_year", "game_date"], as_index=False)[
        ["pitches", "sw", "wh", "cs", "tk", "zone_in", "zone_valid", "bf_g",
         "k_g", "fps_g", "fp_g"]].sum()
    lg = _prior_day(lg, ["game_year"], ["pitches", "sw", "wh", "cs", "tk",
                                        "zone_in", "zone_valid", "bf_g",
                                        "k_g", "fps_g", "fp_g"])
    lg["lg_swstr"] = lg["prior_wh"] / lg["prior_pitches"].replace(0, np.nan)
    lg["lg_csw"] = (lg["prior_wh"] + lg["prior_cs"]) / lg["prior_pitches"].replace(0, np.nan)
    lg["lg_whsw"] = lg["prior_wh"] / lg["prior_sw"].replace(0, np.nan)
    lg["lg_zone"] = lg["prior_zone_in"] / lg["prior_zone_valid"].replace(0, np.nan)
    lg["lg_kpct"] = lg["prior_k_g"] / lg["prior_bf_g"].replace(0, np.nan)
    lg["lg_fps"] = lg["prior_fps_g"] / lg["prior_fp_g"].replace(0, np.nan)
    LG = lg[["game_year", "game_date", "lg_swstr", "lg_csw", "lg_whsw",
             "lg_zone", "lg_kpct", "lg_fps"]]

    P = P.merge(LG, on=["game_year", "game_date"], how="left")
    P["a_swstr"] = _shrink(P.prior_wh, P.prior_pitches, P.lg_swstr, 300)
    P["a_csw"] = _shrink(P.prior_wh + P.prior_cs, P.prior_pitches, P.lg_csw, 300)
    P["a_zone_pct"] = _shrink(P.prior_zone_in, P.prior_zone_valid, P.lg_zone, 200)
    P["a_fps"] = _shrink(P.prior_fps_g, P.prior_fp_g, P.lg_fps, 200)
    P["a_arm_angle"] = P.prior_arm_s / P.prior_arm_n.replace(0, np.nan)
    P["a_kpct"] = _shrink(P.prior_k_g, P.prior_bf_g, P.lg_kpct, 100)

    # recent-form windows on SwStr%, expressed as deviation from his own
    # season-to-date rate (so the feature is orthogonal to the level the
    # book prices).  Windows in STARTS and in PITCHES.
    P = P.sort_values(["pitcher", "game_year", "game_date", "game_pk"])
    gp = P.groupby(["pitcher", "game_year"], sort=False)
    for n in (1, 2, 3, 5, 8, 10, 15):
        wh_n = gp["wh"].shift(1).rolling(n, min_periods=max(1, n // 2)).sum()
        pi_n = gp["pitches"].shift(1).rolling(n, min_periods=max(1, n // 2)).sum()
        P[f"a_swstr_w{n}"] = _shrink(wh_n, pi_n, P["a_swstr"], 200) - P["a_swstr"]
        cs_n = gp["cs"].shift(1).rolling(n, min_periods=max(1, n // 2)).sum()
        P[f"a_csw_w{n}"] = _shrink(wh_n + cs_n, pi_n, P["a_csw"], 200) - P["a_csw"]
    P["a_p5_pitches"] = gp["pitches"].shift(1).rolling(5, min_periods=2).mean()
    P["a_p15d_pitches"] = np.nan  # filled below (calendar window)

    # 15-day calendar pitch budget
    tmp = P[["pitcher", "game_date", "pitches"]].copy()
    tmp = tmp.sort_values(["pitcher", "game_date"])
    bud = []
    for pid, grp in tmp.groupby("pitcher", sort=False):
        d = grp["game_date"].to_numpy()
        c = np.concatenate([[0.0], np.cumsum(grp["pitches"].to_numpy(dtype=float))])
        lo = np.searchsorted(d, d - np.timedelta64(15, "D"), side="left")
        hi = np.arange(len(d))            # strictly prior games
        bud.append(pd.Series(c[hi] - c[lo], index=grp.index))
    P["a_p15d_pitches"] = pd.concat(bud).reindex(P.index)

    # days rest across seasons (role detector), from game_date diffs
    P = P.sort_values(["pitcher", "game_date", "game_pk"])
    P["drest"] = P.groupby("pitcher")["game_date"].diff().dt.days

    keep_p = ["game_pk", "pitcher", "a_swstr", "a_csw", "a_zone_pct", "a_fps",
              "a_arm_angle", "a_kpct", "a_p5_pitches", "a_p15d_pitches",
              "drest", "start_idx", "prior_pitches", "prior_bf_g",
              "prior_k_g", "is_starter", "pitching_team"] + \
        [f"a_swstr_w{n}" for n in (1, 2, 3, 5, 8, 10, 15)] + \
        [f"a_csw_w{n}" for n in (1, 2, 3, 5, 8, 10, 15)]
    P[keep_p].to_parquet(CACHE / "f_pitcher.parquet", index=False)
    print(f"[features] pitcher as-of {P[keep_p].shape}")

    # ---- pitcher HISTORY on starts only ---------------------------------
    # These six are the market-proxy baseline: Task A measured that the
    # posted line regresses on exactly this information (season K%, prior BF
    # mean, opponent lineup K%) with r=0.893 between proxy and posted line.
    H = PG[PG.is_starter == 1][["game_pk", "pitcher", "game_date", "game_year"]].merge(
        pa.groupby(["game_pk", "pitcher"], as_index=False).agg(
            bf_s=("isk", "size"), k_s=("isk", "sum")),
        on=["game_pk", "pitcher"], how="left")
    H = H.merge(PG[["game_pk", "pitcher", "pitches"]], on=["game_pk", "pitcher"],
                how="left")
    H = _prior_within_season(H, ["pitcher", "game_year"], ["bf_s", "k_s", "pitches"])
    H["ng"] = H.groupby(["pitcher", "game_year"]).cumcount()
    H["h_prior_bf_mean"] = H.prior_bf_s / H.ng.replace(0, np.nan)
    H["h_season_k_pct"] = H.prior_k_s / H.prior_bf_s.replace(0, np.nan)
    H["h_prior_k_per_start"] = H.prior_k_s / H.ng.replace(0, np.nan)
    gh = H.sort_values(["pitcher", "game_year", "game_date", "game_pk"]).groupby(
        ["pitcher", "game_year"], sort=False)
    for n in (3, 5):
        H[f"h_roll{n}_k"] = (gh["k_s"].shift(1).rolling(n, min_periods=2).sum() /
                             gh["bf_s"].shift(1).rolling(n, min_periods=2).sum()
                             .replace(0, np.nan))
    H.loc[H.prior_bf_s < 50, ["h_season_k_pct", "h_prior_bf_mean",
                              "h_prior_k_per_start"]] = np.nan
    H[["game_pk", "pitcher", "h_prior_bf_mean", "h_season_k_pct",
       "h_prior_k_per_start", "h_roll3_k", "h_roll5_k", "ng"]].to_parquet(
        CACHE / "f_hist.parquet", index=False)
    print(f"[features] pitcher history {H.shape}")

    # ---- first nine batters faced (lineup proxy for backtest) ------------
    sp2 = sp.sort_values(["game_pk", "pitcher", "at_bat_number"]).copy()
    sp2["seq"] = sp2.groupby(["game_pk", "pitcher"]).cumcount()
    nine = sp2[sp2["seq"] < 9][["game_pk", "pitcher", "batter", "seq", "stand"]]
    nine.to_parquet(CACHE / "f_nine.parquet", index=False)

    # ---- career / season start counters (career does NOT reset) ----------
    S = PG[PG.is_starter == 1][["game_pk", "pitcher", "game_date", "game_year"]].copy()
    S = S.sort_values(["pitcher", "game_date", "game_pk"])
    S["career_start_number"] = S.groupby("pitcher").cumcount()
    S["season_start_number"] = S.groupby(["pitcher", "game_year"]).cumcount()
    first_seen = S.groupby("pitcher")["game_date"].transform("min")
    S["career_left_censored"] = (first_seen <= pd.Timestamp("2024-04-11")).astype(int)
    S["career_start_number"] = S["career_start_number"].clip(upper=10)
    S["season_start_number"] = S["season_start_number"].clip(upper=8)
    S.loc[S.career_left_censored == 1, "career_start_number"] = np.nan
    S[["game_pk", "pitcher", "career_start_number", "season_start_number",
       "career_left_censored"]].to_parquet(CACHE / "f_career.parquet", index=False)

    # ======================================================================
    # PITCH-TYPE as-of : stuff, put-away, velo/movement deltas, mix shift
    # ======================================================================
    T = PT.sort_values(["pitcher", "game_year", "pitch_type", "game_date", "game_pk"]).copy()
    tcols = ["n", "sw", "wh", "ts_n", "ts_k", "vel_s", "vel_n", "fx_s",
             "fz_s", "f_n", "sp_s", "sp_n", "rx_s", "rx_q", "rz_s", "rz_q", "r_n"]
    T = _prior_within_season(T, ["pitcher", "game_year", "pitch_type"], tcols)

    # league-by-type as-of shrink targets (prior day)
    lt = PT.groupby(["game_year", "game_date", "pitch_type"], as_index=False)[
        ["n", "sw", "wh", "ts_n", "ts_k"]].sum()
    lt = _prior_day(lt, ["game_year", "pitch_type"], ["n", "sw", "wh", "ts_n", "ts_k"])
    lt["lt_whsw"] = lt["prior_wh"] / lt["prior_sw"].replace(0, np.nan)
    lt["lt_pa"] = lt["prior_ts_k"] / lt["prior_ts_n"].replace(0, np.nan)
    T = T.merge(lt[["game_year", "game_date", "pitch_type", "lt_whsw", "lt_pa"]],
                on=["game_year", "game_date", "pitch_type"], how="left")

    T["u"] = T["prior_n"]
    T["whsw_t"] = _shrink(T.prior_wh, T.prior_sw, T.lt_whsw, 150)
    T["pa_t"] = _shrink(T.prior_ts_k, T.prior_ts_n, T.lt_pa, 100)

    # rolling velo / movement windows PER TYPE (repairs the pooled-fastball
    # artifact in features/asof.py:pitcher_velocity_delta)
    gt = T.groupby(["pitcher", "game_year", "pitch_type"], sort=False)
    for col, nm in [("vel_s", "vel"), ("fx_s", "fx"), ("fz_s", "fz")]:
        num_r = gt[col].shift(1).rolling(3, min_periods=2).sum()
        den_r = gt["vel_n" if nm == "vel" else "f_n"].shift(1).rolling(3, min_periods=2).sum()
        num_b = gt[col].shift(4).rolling(7, min_periods=3).sum()
        den_b = gt["vel_n" if nm == "vel" else "f_n"].shift(4).rolling(7, min_periods=3).sum()
        T[f"d_{nm}"] = (num_r / den_r.replace(0, np.nan)) - (num_b / den_b.replace(0, np.nan))
    # within-start release scatter, averaged over prior 3 starts
    var_x = (T["rx_q"] / T["r_n"].replace(0, np.nan)) - (T["rx_s"] / T["r_n"].replace(0, np.nan)) ** 2
    var_z = (T["rz_q"] / T["r_n"].replace(0, np.nan)) - (T["rz_s"] / T["r_n"].replace(0, np.nan)) ** 2
    T["rel_sd"] = np.sqrt(np.clip(var_x, 0, None)) + np.sqrt(np.clip(var_z, 0, None))
    T["rel_sd_w"] = T["rel_sd"] * T["n"]
    T["rel_sd_prior"] = gt["rel_sd_w"].shift(1).rolling(3, min_periods=2).sum() / \
        gt["n"].shift(1).rolling(3, min_periods=2).sum().replace(0, np.nan)
    # recent usage share, for the mix-shift TVD
    T["n3"] = gt["n"].shift(1).rolling(3, min_periods=2).sum()

    T["tot_u"] = T.groupby(["game_pk", "pitcher"])["u"].transform("sum")
    T["tot_n3"] = T.groupby(["game_pk", "pitcher"])["n3"].transform("sum")
    T["w"] = T["u"] / T["tot_u"].replace(0, np.nan)
    T["w3"] = T["n3"] / T["tot_n3"].replace(0, np.nan)
    T["tsu"] = T["prior_ts_n"]
    T["tot_tsu"] = T.groupby(["game_pk", "pitcher"])["tsu"].transform("sum")
    T["w2"] = T["tsu"] / T["tot_tsu"].replace(0, np.nan)

    # Weighted arsenal aggregates. Renormalise over the weight that is
    # actually COVERED -- a plain groupby-sum would silently treat a missing
    # per-type value as zero and manufacture a null result.
    specs = [("a_stuff_whiff", "whsw_t", "w"), ("a_stuff_whiff3", "whsw_t", "w3"),
             ("a_putaway", "pa_t", "w2"), ("a_velo_delta", "d_vel", "w"),
             ("a_release_sd", "rel_sd_prior", "w")]
    T["d_mov"] = np.sqrt(T.d_fx.fillna(0) ** 2 + T.d_fz.fillna(0) ** 2)
    T.loc[T.d_fx.isna() & T.d_fz.isna(), "d_mov"] = np.nan
    specs.append(("a_move_delta", "d_mov", "w"))
    parts = {}
    for nm, val, wt in specs:
        T["_v"] = T[val] * T[wt]
        T["_w"] = T[wt].where(T[val].notna() & T[wt].notna())
        gg = T.groupby(["game_pk", "pitcher"], as_index=False).agg(
            v=("_v", "sum"), wc=("_w", "sum"))
        gg[nm] = gg["v"] / gg["wc"].replace(0, np.nan)
        gg.loc[gg["wc"] < 0.5, nm] = np.nan      # <50% of the arsenal covered
        parts[nm] = gg[["game_pk", "pitcher", nm]]

    T["_tvd"] = (T["w3"] - T["w"]).abs()
    tv = T.groupby(["game_pk", "pitcher"], as_index=False).agg(
        a_mix_tvd=("_tvd", "sum"), cov3=("w3", "sum"), u=("u", "sum"))
    tv["a_mix_tvd"] = 0.5 * tv["a_mix_tvd"]
    tv.loc[tv.cov3 < 0.9, "a_mix_tvd"] = np.nan

    A = tv[["game_pk", "pitcher", "a_mix_tvd", "u"]]
    for nm, part in parts.items():
        A = A.merge(part, on=["game_pk", "pitcher"], how="outer")
    A["a_stuff_shift"] = A["a_stuff_whiff3"] - A["a_stuff_whiff"]
    A.loc[A.u < 200, [c for c in A.columns if c.startswith("a_")]] = np.nan
    A.drop(columns=["u", "a_stuff_whiff3"]).to_parquet(
        CACHE / "f_arsenal.parquet", index=False)
    print(f"[features] arsenal as-of {A.shape}")

    # ======================================================================
    # SHADOW-BAND called-strike-over-expected (pitcher and catcher)
    # p_league fitted per training-season set at screen time; here we emit
    # the raw prior-game bin counts so the baseline can be frozen properly.
    # ======================================================================
    for nm, key, src in [("pcsoe", "pitcher", PSH), ("ccsoe", "catcher", CSH)]:
        d = src.sort_values([key, "game_year", "bin", "stand", "game_date", "game_pk"]).copy()
        d = _prior_within_season(d, [key, "game_year", "bin", "stand"], ["tk", "cs"])
        d[["game_pk", key, "bin", "stand", "game_year", "game_date",
           "prior_tk", "prior_cs", "tk", "cs"]].to_parquet(
            CACHE / f"f_{nm}.parquet", index=False)
        print(f"[features] {nm} bins {d.shape}")

    # game -> catcher of record for the opposing side (for backtest join)
    cg = CSH.groupby(["game_pk", "catcher"], as_index=False)["tk"].sum()
    cg = cg.sort_values("tk", ascending=False).groupby("game_pk").head(2)
    cg.to_parquet(CACHE / "f_gamecatchers.parquet", index=False)

    # ======================================================================
    # BATTER as-of  -> team-level opponent aggregates (prior DAY)
    # ======================================================================
    B = BG.merge(pa.groupby(["game_pk", "batter"], as_index=False).agg(
        pa_n=("isk", "size"), k_n=("isk", "sum"), two_n=("two", "sum"),
        two_k=("isk", "sum")), on=["game_pk", "batter"], how="left")
    # two-strike survival needs K among two-strike PA specifically
    ts = pa[pa["two"] == 1].groupby(["game_pk", "batter"], as_index=False).agg(
        tspa=("isk", "size"), tsk=("isk", "sum"))
    B = B.merge(ts, on=["game_pk", "batter"], how="left")
    B[["tspa", "tsk"]] = B[["tspa", "tsk"]].fillna(0)
    B = B.merge(pa.groupby(["game_pk", "batter"], as_index=False).agg(
        team=("pitching_team", "first")), on=["game_pk", "batter"], how="left")
    # batting team = the OTHER side
    gt2 = pa.groupby("game_pk", as_index=False).agg(
        t1=("pitching_team", "min"), t2=("pitching_team", "max"))
    B = B.merge(gt2, on="game_pk", how="left")
    B["bat_team"] = np.where(B["team"] == B["t1"], B["t2"], B["t1"])

    bcols = ["pitches", "sw", "wh", "iz_sw", "iz_wh", "oz_p", "oz_sw",
             "fb_sw", "fb_wh", "bb_sw", "bb_wh", "os_sw", "os_wh", "bs_s",
             "bs_n", "sl_s", "pa_n", "k_n", "tspa", "tsk"]
    # TEAM-level, prior DAY (no posted lineup required)
    TD = B.groupby(["bat_team", "game_year", "game_date"], as_index=False)[bcols].sum()
    TD = _prior_day(TD, ["bat_team", "game_year"], bcols)
    lgd = B.groupby(["game_year", "game_date"], as_index=False)[bcols].sum()
    lgd = _prior_day(lgd, ["game_year"], bcols)
    for c in bcols:
        lgd = lgd.rename(columns={"prior_" + c: "L_" + c})
    TD = TD.merge(lgd[["game_year", "game_date"] + ["L_" + c for c in bcols]],
                  on=["game_year", "game_date"], how="left")

    def _tr(num, den):
        return TD["L_" + num] / TD["L_" + den].replace(0, np.nan)

    TD["o_k_pct"] = _shrink(TD.prior_k_n, TD.prior_pa_n, _tr("k_n", "pa_n"), 200)
    TD["o_whiff_sw"] = _shrink(TD.prior_wh, TD.prior_sw, _tr("wh", "sw"), 200)
    TD["o_chase"] = _shrink(TD.prior_oz_sw, TD.prior_oz_p, _tr("oz_sw", "oz_p"), 300)
    TD["o_zone_miss"] = _shrink(TD.prior_iz_wh, TD.prior_iz_sw, _tr("iz_wh", "iz_sw"), 200)
    TD["o_ppa"] = _shrink(TD.prior_pitches, TD.prior_pa_n, _tr("pitches", "pa_n"), 200)
    TD["o_ts_surv"] = _shrink(TD.prior_tsk, TD.prior_tspa, _tr("tsk", "tspa"), 100)
    TD["o_bb_whiff"] = _shrink(TD.prior_bb_wh, TD.prior_bb_sw, _tr("bb_wh", "bb_sw"), 150)
    TD["o_fb_whiff"] = _shrink(TD.prior_fb_wh, TD.prior_fb_sw, _tr("fb_wh", "fb_sw"), 150)
    TD["o_os_whiff"] = _shrink(TD.prior_os_wh, TD.prior_os_sw, _tr("os_wh", "os_sw"), 150)
    TD["o_swing_len"] = TD.prior_sl_s / TD.prior_bs_n.replace(0, np.nan)
    TD["o_bat_speed"] = TD.prior_bs_s / TD.prior_bs_n.replace(0, np.nan)
    TD["o_games"] = TD.groupby(["bat_team", "game_year"]).cumcount()
    ocols = [c for c in TD.columns if c.startswith("o_")]
    TD.loc[TD.o_games < 20, ocols] = np.nan
    TD[["bat_team", "game_year", "game_date"] + ocols].to_parquet(
        CACHE / "f_oppteam.parquet", index=False)
    print(f"[features] opponent team as-of {TD.shape}")

    # per-BATTER as-of (for the confirmed-lineup versions)
    Bp = _prior_within_season(B, ["batter", "game_year"], bcols)
    Bp = Bp.merge(lgd[["game_year", "game_date"] + ["L_" + c for c in bcols]],
                  on=["game_year", "game_date"], how="left")

    def _br(num, den):
        return Bp["L_" + num] / Bp["L_" + den].replace(0, np.nan)

    Bp["b_k_pct"] = _shrink(Bp.prior_k_n, Bp.prior_pa_n, _br("k_n", "pa_n"), 100)
    Bp["b_whiff_sw"] = _shrink(Bp.prior_wh, Bp.prior_sw, _br("wh", "sw"), 200)
    Bp["b_chase"] = _shrink(Bp.prior_oz_sw, Bp.prior_oz_p, _br("oz_sw", "oz_p"), 300)
    Bp["b_zone_miss"] = _shrink(Bp.prior_iz_wh, Bp.prior_iz_sw, _br("iz_wh", "iz_sw"), 200)
    Bp["b_ppa"] = _shrink(Bp.prior_pitches, Bp.prior_pa_n, _br("pitches", "pa_n"), 200)
    Bp["b_ts_surv"] = _shrink(Bp.prior_tsk, Bp.prior_tspa, _br("tsk", "tspa"), 100)
    Bp["b_swing_len"] = Bp.prior_sl_s / Bp.prior_bs_n.replace(0, np.nan)
    Bp["b_bat_speed"] = Bp.prior_bs_s / Bp.prior_bs_n.replace(0, np.nan)
    Bp["b_bb_whiff"] = _shrink(Bp.prior_bb_wh, Bp.prior_bb_sw, _br("bb_wh", "bb_sw"), 150)
    Bp["b_fb_whiff"] = _shrink(Bp.prior_fb_wh, Bp.prior_fb_sw, _br("fb_wh", "fb_sw"), 150)
    Bp["b_os_whiff"] = _shrink(Bp.prior_os_wh, Bp.prior_os_sw, _br("os_wh", "os_sw"), 150)
    bkeep = ["game_pk", "batter", "game_year", "game_date"] + \
        [c for c in Bp.columns if c.startswith("b_")]
    Bp[bkeep].to_parquet(CACHE / "f_batter.parquet", index=False)
    print(f"[features] batter as-of {Bp[bkeep].shape}")

    # ======================================================================
    # PARK / ENVIRONMENT / BULLPEN / OPPOSING STARTER
    # ======================================================================
    # park K rate as-of (prior day, keyed on home team)
    pk = pa.merge(st[["game_pk", "pitcher"]].assign(_s=1), on=["game_pk", "pitcher"],
                  how="left")
    pk = pk[pk["_s"] == 1]
    hometeam = pa.groupby("game_pk", as_index=False).agg(
        home_team=("pitching_team", lambda s: s.iloc[0]))
    ht = st[st.is_home == 1][["game_pk", "team"]].rename(columns={"team": "home_team"})
    pk = pk.merge(ht, on="game_pk", how="left")
    PKD = pk.groupby(["home_team", "game_year", "game_date"], as_index=False).agg(
        k=("isk", "sum"), bf=("isk", "size"))
    PKD = _prior_day(PKD, ["home_team", "game_year"], ["k", "bf"])
    lgk = pk.groupby(["game_year", "game_date"], as_index=False).agg(
        k=("isk", "sum"), bf=("isk", "size"))
    lgk = _prior_day(lgk, ["game_year"], ["k", "bf"])
    lgk["Lk"] = lgk.prior_k / lgk.prior_bf.replace(0, np.nan)
    PKD = PKD.merge(lgk[["game_year", "game_date", "Lk"]], on=["game_year", "game_date"],
                    how="left")
    PKD["e_park_k"] = _shrink(PKD.prior_k, PKD.prior_bf, PKD.Lk, 400)
    PKD["_g"] = PKD.groupby(["home_team", "game_year"]).cumcount()
    PKD.loc[PKD._g < 20, "e_park_k"] = np.nan
    PKD[["home_team", "game_year", "game_date", "e_park_k"]].to_parquet(
        CACHE / "f_park.parquet", index=False)

    # air-density proxy: park x week league FF vertical break, prior days only
    PARK["wk"] = PARK["game_date"].dt.isocalendar().week.astype(int)
    PARK["game_year"] = PARK["game_date"].dt.year
    PW = PARK.groupby(["home_team", "game_year", "game_date"], as_index=False)[
        ["ff_fz_s", "ff_n"]].sum()
    PW = _prior_day(PW, ["home_team", "game_year"], ["ff_fz_s", "ff_n"])
    LW = PARK.groupby(["game_year", "game_date"], as_index=False)[["ff_fz_s", "ff_n"]].sum()
    LW = _prior_day(LW, ["game_year"], ["ff_fz_s", "ff_n"])
    LW["Lfz"] = LW.prior_ff_fz_s / LW.prior_ff_n.replace(0, np.nan)
    PW = PW.merge(LW[["game_year", "game_date", "Lfz"]], on=["game_year", "game_date"],
                  how="left")
    PW["e_air_pfx"] = (PW.prior_ff_fz_s / PW.prior_ff_n.replace(0, np.nan)) / PW.Lfz
    PW.loc[PW.prior_ff_n < 300, "e_air_pfx"] = np.nan
    PW[["home_team", "game_year", "game_date", "e_air_pfx"]].to_parquet(
        CACHE / "f_air.parquet", index=False)

    # bullpen: relief pitches by team-date, prior 1/2/3 days (continuous)
    rel = PG[PG.is_starter == 0].groupby(
        ["pitching_team", "game_year", "game_date"], as_index=False).agg(
        rp=("pitches", "sum"), nrel=("pitcher", "nunique"))
    rel = rel.sort_values(["pitching_team", "game_date"])
    out = []
    for tm, grp in rel.groupby("pitching_team", sort=False):
        d = grp["game_date"].to_numpy()
        c = np.concatenate([[0.0], np.cumsum(grp["rp"].to_numpy(dtype=float))])
        row = {"pitching_team": tm, "game_date": grp["game_date"].to_numpy(),
               "game_year": grp["game_year"].to_numpy()}
        for w in (1, 2, 3):
            lo = np.searchsorted(d, d - np.timedelta64(w, "D"), side="left")
            row[f"c_bp{w}d"] = c[np.arange(len(d))] - c[lo]
        out.append(pd.DataFrame(row))
    BPD = pd.concat(out, ignore_index=True)
    BPD.to_parquet(CACHE / "f_bullpen.parquet", index=False)

    # manager leash: team's own starters' prior pitch counts / BF, prior day
    sg = PG[PG.is_starter == 1].merge(
        pa.groupby(["game_pk", "pitcher"], as_index=False).agg(bfx=("isk", "size")),
        on=["game_pk", "pitcher"], how="left")
    ML = sg.groupby(["pitching_team", "game_year", "game_date"], as_index=False).agg(
        pit=("pitches", "sum"), bfx=("bfx", "sum"), g=("pitches", "size"))
    ML = _prior_day(ML, ["pitching_team", "game_year"], ["pit", "bfx", "g"])
    ML["c_leash_pit"] = ML.prior_pit / ML.prior_g.replace(0, np.nan)
    ML["c_leash_bf"] = ML.prior_bfx / ML.prior_g.replace(0, np.nan)
    ML.loc[ML.prior_g < 20, ["c_leash_pit", "c_leash_bf"]] = np.nan
    ML[["pitching_team", "game_year", "game_date", "c_leash_pit",
        "c_leash_bf"]].to_parquet(CACHE / "f_leash.parquet", index=False)

    st.to_parquet(CACHE / "starts.parquet", index=False)
    print("[features] done")




# ==========================================================================
# STAGE 3 -- assemble the start-level design matrix
# ==========================================================================
#: (name, train seasons, test season). Mandated by CLAUDE.md: a factor that
#: helps in only ONE temporal direction is REJECTED.
SPLITS = [("2024->2025", [2024], 2025),
          ("2025->2024", [2025], 2024),
          ("24+25->2026", [2024, 2025], 2026)]

#: The market-shaped baseline. Task A measured that the POSTED LINE regresses
#: on exactly this information set (season K% +21.9, prior BF mean +0.164,
#: opponent lineup K% +19.4) and that a proxy built from it correlates 0.893
#: with the real posted line. A candidate must beat THIS, not a naive mean.
BASE = ["h_season_k_pct", "h_prior_bf_mean", "h_prior_k_per_start",
        "h_roll3_k", "h_roll5_k", "lineup_k_mean"]


def _csoe(fname, key, train_years, w):
    """Shadow-band called-strike-over-expected. p_league(bin, stand) is fitted
    on TRAINING seasons only and frozen -- refitting it per test year would
    leak the test distribution."""
    d = pd.read_parquet(CACHE / fname)
    tr = d[d.game_year.isin(train_years)]
    lg = tr.groupby(["bin", "stand"], as_index=False).agg(
        Tk=("tk", "sum"), Cs=("cs", "sum"))
    lg["p"] = lg.Cs / lg.Tk.replace(0, np.nan)
    d = d.merge(lg[["bin", "stand", "p"]], on=["bin", "stand"], how="left")
    d["e"] = d["prior_tk"] * d["p"]
    g = d.groupby(["game_pk", key], as_index=False).agg(
        tk=("prior_tk", "sum"), cs=("prior_cs", "sum"), e=("e", "sum"))
    g["v"] = (g.cs - g.e) / (g.tk + w)
    g.loc[g.tk < w / 3, "v"] = np.nan
    return g[["game_pk", key, "v"]]


def assemble() -> pd.DataFrame:
    st = pd.read_parquet(CACHE / "starts.parquet")
    st["game_date"] = pd.to_datetime(st["game_date"])
    D = st.copy()

    D = D.merge(pd.read_parquet(CACHE / "f_hist.parquet"),
                on=["game_pk", "pitcher"], how="left")
    P = pd.read_parquet(CACHE / "f_pitcher.parquet")
    D = D.merge(P.drop(columns=["is_starter", "pitching_team"]),
                on=["game_pk", "pitcher"], how="left")
    D = D.merge(pd.read_parquet(CACHE / "f_arsenal.parquet"),
                on=["game_pk", "pitcher"], how="left")
    D = D.merge(pd.read_parquet(CACHE / "f_career.parquet"),
                on=["game_pk", "pitcher"], how="left")

    # --- opponent LINEUP (first nine faced) as-of -------------------------
    # NOTE: the first nine batters actually faced stands in for the posted
    # lineup. It is what the repo's own backtest does, and it is mildly
    # optimistic (a live bet sees a projected lineup). Flagged in the report.
    nine = pd.read_parquet(CACHE / "f_nine.parquet")
    B = pd.read_parquet(CACHE / "f_batter.parquet")
    bcols = [c for c in B.columns if c.startswith("b_")]
    n9 = nine.merge(B[["game_pk", "batter"] + bcols], on=["game_pk", "batter"],
                    how="left")
    PA_W = np.array([4.65, 4.53, 4.41, 4.29, 4.17, 4.05, 3.93, 3.82, 3.71])
    n9["w"] = PA_W[n9["seq"].to_numpy()]
    for c in bcols:
        n9["_" + c] = n9[c] * n9["w"]
    ag = n9.groupby(["game_pk", "pitcher"], as_index=False).agg(
        **{("L" + c): ("_" + c, "sum") for c in bcols},
        **{("N" + c): (c, "count") for c in bcols},
        wsum=("w", "sum"), lhb=("stand", lambda s: float((s == "L").mean())))
    for c in bcols:
        ag["L" + c] = ag["L" + c] / ag["wsum"]
        ag.loc[ag["N" + c] < 8, "L" + c] = np.nan
    ag = ag.drop(columns=[("N" + c) for c in bcols] + ["wsum"])
    ag = ag.rename(columns={"Lb_k_pct": "lineup_k_mean"})
    D = D.merge(ag, on=["game_pk", "pitcher"], how="left")

    # --- opponent TEAM as-of (needs no posted lineup) ---------------------
    TD = pd.read_parquet(CACHE / "f_oppteam.parquet")
    TD["game_date"] = pd.to_datetime(TD["game_date"])
    D = D.merge(TD.rename(columns={"bat_team": "opponent_team"}),
                on=["opponent_team", "game_year", "game_date"], how="left")

    # --- park / air -------------------------------------------------------
    home = D[D.is_home == 1][["game_pk", "team"]].rename(
        columns={"team": "home_team"})
    D = D.merge(home, on="game_pk", how="left")
    for f in ("f_park.parquet", "f_air.parquet"):
        t = pd.read_parquet(CACHE / f)
        t["game_date"] = pd.to_datetime(t["game_date"])
        D = D.merge(t, on=["home_team", "game_year", "game_date"], how="left")

    # --- bullpen / manager leash (own team) -------------------------------
    for f in ("f_bullpen.parquet", "f_leash.parquet"):
        t = pd.read_parquet(CACHE / f)
        t["game_date"] = pd.to_datetime(t["game_date"])
        D = D.merge(t.rename(columns={"pitching_team": "team"}),
                    on=["team", "game_year", "game_date"], how="left")

    # --- opposing starter (the model knows nothing about him today) -------
    opp = D[["game_pk", "pitcher", "h_season_k_pct", "h_prior_bf_mean",
             "a_p5_pitches", "a_swstr"]].rename(columns={
                 "pitcher": "opp_pitcher", "h_season_k_pct": "c_oppsp_kpct",
                 "h_prior_bf_mean": "c_oppsp_bf", "a_p5_pitches": "c_oppsp_p5",
                 "a_swstr": "c_oppsp_swstr"})
    j = D[["game_pk", "pitcher"]].merge(opp, on="game_pk")
    j = j[j["pitcher"] != j["opp_pitcher"]].drop(columns=["opp_pitcher"])
    D = D.merge(j, on=["game_pk", "pitcher"], how="left")

    # --- derived / structural --------------------------------------------
    D["c_dr_short"] = (D["drest"] <= 4).astype(float)
    D["c_dr_long"] = (D["drest"] >= 11).astype(float)
    D.loc[D["drest"].isna(), ["c_dr_short", "c_dr_long"]] = np.nan
    D["e_is_home"] = D["is_home"].astype(float)
    D["c_career_start"] = D["career_start_number"]
    D["c_season_start"] = D["season_start_number"]
    D["c_bp_heavy"] = (D["c_bp1d"] >= 90).astype(float)   # shipped incumbent
    D["c_il_return"] = (D["drest"] > 25).astype(float)    # shipped incumbent
    D.loc[D["drest"].isna(), "c_il_return"] = 0.0
    D["b_lhb_share"] = D["lhb"]
    D["c_pitch_budget_15d"] = D["a_p15d_pitches"]
    # pitch budget is 80% a durability term wearing a fatigue costume; the
    # honest version is the residual against the leash/BF history.
    fit = D[["c_pitch_budget_15d", "h_prior_bf_mean", "a_p5_pitches"]].dropna()
    if len(fit) > 500:
        Xr = np.column_stack([np.ones(len(fit)),
                              fit[["h_prior_bf_mean", "a_p5_pitches"]].to_numpy()])
        br, _, _ = _ols(Xr, fit["c_pitch_budget_15d"].to_numpy(float))
        pred = (br[0] + br[1] * D["h_prior_bf_mean"] + br[2] * D["a_p5_pitches"])
        D["c_budget_resid"] = D["c_pitch_budget_15d"] - pred

    # negative controls: pure noise through the identical pipeline
    rng = np.random.default_rng(20260809)
    for i in range(50):
        D[f"nc_rand{i:02d}"] = rng.normal(size=len(D))
    return D


# ==========================================================================
# STAGE 3b -- the screen
# ==========================================================================
def _ols(X, y):
    inv = np.linalg.pinv(X.T @ X)
    b = inv @ (X.T @ y)
    r = y - X @ b
    dof = max(len(y) - X.shape[1], 1)
    s2 = float(r @ r) / dof
    se = np.sqrt(np.clip(np.diag(inv) * s2, 0, None))
    return b, se, r


def _norm_cdf(z):
    from scipy.stats import norm
    return norm.cdf(z)


def eval_candidate(D, cand, base=BASE, splits=SPLITS, target="actual_k"):
    """Paired out-of-sample evaluation of BASE vs BASE+cand.

    Every row is used for BOTH models, so the RMSE difference is paired and
    its standard error is the SE of the per-row squared-error difference."""
    base = [b for b in base if b != cand]
    cols = list(dict.fromkeys(list(base) + [cand, target, "game_year"]))
    m = D[cols].replace([np.inf, -np.inf], np.nan).dropna()
    rows = []
    for name, tr_y, te_y in splits:
        tr = m[m.game_year.isin(tr_y)]
        te = m[m.game_year == te_y]
        if len(tr) < 300 or len(te) < 300:
            rows.append(dict(split=name, n_tr=len(tr), n_te=len(te)))
            continue
        mu, sd = tr[cand].mean(), tr[cand].std()
        sd = sd if sd > 1e-12 else 1.0
        ztr = ((tr[cand] - mu) / sd).to_numpy()
        zte = ((te[cand] - mu) / sd).to_numpy()
        Xb_tr = np.column_stack([np.ones(len(tr)), tr[base].to_numpy(float)])
        Xb_te = np.column_stack([np.ones(len(te)), te[base].to_numpy(float)])
        Xc_tr = np.column_stack([Xb_tr, ztr])
        Xc_te = np.column_stack([Xb_te, zte])
        ytr = tr[target].to_numpy(float)
        yte = te[target].to_numpy(float)

        bb, _, rb_tr = _ols(Xb_tr, ytr)
        bc, sec, rc_tr = _ols(Xc_tr, ytr)
        pb, pc = Xb_te @ bb, Xc_te @ bc
        eb, ec = yte - pb, yte - pc
        rmse_b, rmse_c = np.sqrt((eb ** 2).mean()), np.sqrt((ec ** 2).mean())
        d = eb ** 2 - ec ** 2                     # >0 = candidate helps
        se_d = d.std(ddof=1) / np.sqrt(len(d))
        # Gate 5: calibration at a line, not accuracy. The line is set ONCE
        # from the baseline forecast so both models price the same bet.
        line = np.round(pb * 2) / 2
        line = np.where(np.isclose(line % 1, 0.0), line + 0.5, line)
        over = (yte > line).astype(float)
        sb = float(np.sqrt((rb_tr ** 2).mean()))
        sc = float(np.sqrt((rc_tr ** 2).mean()))
        Pb = 1 - _norm_cdf((line - pb) / sb)
        Pc = 1 - _norm_cdf((line - pc) / sc)
        rows.append(dict(
            split=name, n_tr=len(tr), n_te=len(te),
            coef=float(bc[-1]),
            t_train=float(bc[-1] / sec[-1]) if sec[-1] > 0 else np.nan,
            d_rmse_pct=float((rmse_b - rmse_c) / rmse_b * 100),
            t_paired=float(d.mean() / se_d) if se_d > 0 else np.nan,
            r_resid=float(np.corrcoef(zte, eb)[0, 1]),
            d_brier=float(((over - Pb) ** 2).mean() - ((over - Pc) ** 2).mean()),
        ))
    return rows


def summarize(rows, cand):
    ok = [r for r in rows if "d_rmse_pct" in r]
    if len(ok) < 3:
        return dict(factor=cand, verdict="INSUFFICIENT",
                    n_te=sum(r.get("n_te", 0) for r in rows))
    dr = [r["d_rmse_pct"] for r in ok]
    tt = [r["t_train"] for r in ok]
    co = [r["coef"] for r in ok]
    db = [r["d_brier"] for r in ok]
    sign_ok = all(c > 0 for c in co) or all(c < 0 for c in co)
    passes = (min(dr) > 0) and (min(np.abs(tt)) >= 2.0) and sign_ok
    out = dict(factor=cand, verdict="SURVIVOR" if passes else "REJECT",
               d_rmse_min=min(dr), sign_ok=sign_ok,
               n_te=sum(r["n_te"] for r in ok))
    for i, k in enumerate("ABC"):
        out[f"dR_{k}"] = dr[i]
        out[f"t_{k}"] = tt[i]
        out[f"tp_{k}"] = ok[i]["t_paired"]
        out[f"cf_{k}"] = co[i]
        out[f"rr_{k}"] = ok[i]["r_resid"]
        out[f"dB_{k}"] = db[i]
    return out


# --------------------------------------------------------------------------
# candidate registry: name -> hypothesised sign against TOTAL strikeouts
# --------------------------------------------------------------------------
CANDIDATES = {
    # --- A: pitcher stuff / form -----------------------------------------
    "a_stuff_whiff": +1, "a_putaway": +1, "a_swstr": +1, "a_csw": +1,
    "a_zone_pct": +1, "a_fps": +1, "a_velo_delta": +1, "a_move_delta": -1,
    "a_release_sd": -1, "a_mix_tvd": 0, "a_stuff_shift": +1,
    "a_arm_angle": 0, "a_swstr_w1": +1, "a_swstr_w2": +1, "a_swstr_w3": +1,
    "a_swstr_w5": +1, "a_swstr_w8": +1, "a_swstr_w10": +1, "a_swstr_w15": +1,
    "a_csw_w3": +1, "a_csw_w5": +1, "a_csw_w10": +1,
    # --- B: opponent, team level (no posted lineup required) --------------
    "o_k_pct": +1, "o_whiff_sw": +1, "o_chase": +1, "o_zone_miss": +1,
    "o_ppa": 0, "o_ts_surv": +1, "o_bb_whiff": +1, "o_fb_whiff": +1,
    "o_os_whiff": +1, "o_swing_len": +1, "o_bat_speed": 0,
    # --- B: opponent, lineup level (needs the posted nine) ----------------
    "Lb_whiff_sw": +1, "Lb_chase": +1, "Lb_zone_miss": +1, "Lb_ppa": 0,
    "Lb_ts_surv": +1, "Lb_swing_len": +1, "Lb_bat_speed": 0,
    "Lb_bb_whiff": +1, "Lb_fb_whiff": +1, "Lb_os_whiff": +1,
    "b_lhb_share": 0,
    # --- C: context / workload / role -------------------------------------
    "c_dr_short": -1, "c_dr_long": -1, "c_career_start": +1,
    "c_season_start": +1, "a_p5_pitches": +1, "c_pitch_budget_15d": -1,
    "c_budget_resid": -1, "c_bp1d": +1, "c_bp2d": +1, "c_bp3d": +1,
    "c_leash_pit": +1, "c_leash_bf": +1, "c_oppsp_kpct": +1,
    "c_oppsp_bf": +1, "c_oppsp_p5": +1, "c_oppsp_swstr": +1,
    # --- D/E: battery + environment ---------------------------------------
    "e_is_home": +1, "e_park_k": +1, "e_air_pfx": +1,
    # --- incumbents, re-tested on the SAME footing ------------------------
    "c_bp_heavy": +1, "c_il_return": -1,
}


# ==========================================================================
# STAGE 3c -- driver
# ==========================================================================
def _split_specific_csoe(D):
    """CSOE features whose league baseline must be refit per training set."""
    out = {}
    for nm, fname, key, w, joincol in [
            ("d_pitcher_csoe", "f_pcsoe.parquet", "pitcher", 300, "pitcher"),
            ("d_catcher_csoe", "f_ccsoe.parquet", "catcher", 500, "catcher"),
            ("d_team_framing", "f_tsh.parquet", "pitching_team", 800, "team")]:
        out[nm] = {}
        for name, tr_y, _ in SPLITS:
            if nm == "d_team_framing":
                d = pd.read_parquet(CACHE / "tsh.parquet")
                d["game_date"] = pd.to_datetime(d["game_date"])
                d = d.sort_values(["pitching_team", "game_year", "bin", "stand",
                                   "game_date", "game_pk"])
                d = _prior_within_season(
                    d, ["pitching_team", "game_year", "bin", "stand"], ["tk", "cs"])
                tr = d[d.game_year.isin(tr_y)]
                lg = tr.groupby(["bin", "stand"], as_index=False).agg(
                    Tk=("tk", "sum"), Cs=("cs", "sum"))
                lg["p"] = lg.Cs / lg.Tk.replace(0, np.nan)
                d = d.merge(lg[["bin", "stand", "p"]], on=["bin", "stand"], how="left")
                d["e"] = d["prior_tk"] * d["p"]
                g = d.groupby(["game_pk", "pitching_team"], as_index=False).agg(
                    tk=("prior_tk", "sum"), cs=("prior_cs", "sum"), e=("e", "sum"))
                g["v"] = (g.cs - g.e) / (g.tk + w)
                g.loc[g.tk < w / 3, "v"] = np.nan
                g = g.rename(columns={"pitching_team": "team"})
            else:
                g = _csoe(fname, key, tr_y, w)
            if joincol == "catcher":
                pc = pd.read_parquet(CACHE / "pcat.parquet")
                pc = pc.sort_values("n", ascending=False).groupby(
                    ["game_pk", "pitcher"], as_index=False).first()
                g = pc[["game_pk", "pitcher", "catcher"]].merge(
                    g, on=["game_pk", "catcher"], how="left")
                key_cols = ["game_pk", "pitcher"]
            elif joincol == "team":
                key_cols = ["game_pk", "team"]
            else:
                key_cols = ["game_pk", "pitcher"]
            s = D[key_cols].merge(g[key_cols + ["v"]], on=key_cols, how="left")["v"]
            s.index = D.index
            out[nm][name] = s
    return out


def eval_split_specific(D, cand, colmap):
    rows = []
    for sp in SPLITS:
        D2 = D.assign(**{cand: colmap[sp[0]].to_numpy()})
        rows += eval_candidate(D2, cand, splits=[sp])
    return rows


def _fmt(df, cols=None):
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:+.4f}")
    return d.to_string(index=False)


def stage_screen():
    D = assemble()
    ss = _split_specific_csoe(D)
    print(f"\n{'='*78}\nDESIGN MATRIX {D.shape}   target = actual_k (starter K)")
    print(f"BASE (market-shaped) = {BASE}")
    b = D[BASE + ['actual_k', 'game_year']].dropna()
    print(f"rows with complete BASE: {len(b)}  "
          f"(2024 {int((b.game_year==2024).sum())} / "
          f"2025 {int((b.game_year==2025).sum())} / "
          f"2026 {int((b.game_year==2026).sum())})")

    # ---- baseline sanity: does BASE actually behave like a market proxy? --
    for name, tr_y, te_y in SPLITS:
        tr, te = b[b.game_year.isin(tr_y)], b[b.game_year == te_y]
        X = np.column_stack([np.ones(len(tr)), tr[BASE].to_numpy(float)])
        bb, _, _ = _ols(X, tr.actual_k.to_numpy(float))
        p = np.column_stack([np.ones(len(te)), te[BASE].to_numpy(float)]) @ bb
        print(f"  BASE {name}: OOS RMSE {np.sqrt(((te.actual_k-p)**2).mean()):.4f}  "
              f"r(pred,K) {np.corrcoef(p, te.actual_k)[0,1]:.4f}  n_te {len(te)}")

    # ---- run every candidate ---------------------------------------------
    res = []
    for cand in CANDIDATES:
        if cand not in D.columns:
            print(f"  !! missing column {cand}")
            continue
        res.append(summarize(eval_candidate(D, cand), cand))
    for cand, colmap in ss.items():
        res.append(summarize(eval_split_specific(D, cand, colmap), cand))
    R = pd.DataFrame(res)

    # ---- negative controls ------------------------------------------------
    nc = [summarize(eval_candidate(D, f"nc_rand{i:02d}"), f"nc_rand{i:02d}")
          for i in range(50)]
    NC = pd.DataFrame(nc)
    NC = NC[NC.verdict != "INSUFFICIENT"]
    npass = int((NC.verdict == "SURVIVOR").sum())
    print(f"\n{'='*78}\nNEGATIVE CONTROLS  n={len(NC)} seeded-random columns")
    print(f"  passed the three-way rule: {npass}/{len(NC)} "
          f"({npass/len(NC)*100:.1f}%)  <-- the false-positive rate of this screen")
    print(f"  dRMSE_min: mean {NC.d_rmse_min.mean():+.5f}%  sd {NC.d_rmse_min.std():.5f}"
          f"  max {NC.d_rmse_min.max():+.5f}%  p95 {NC.d_rmse_min.quantile(.95):+.5f}%")
    print(f"  |t_train| max over controls: {NC[['t_A','t_B','t_C']].abs().max().max():.2f}")
    NOISE_FLOOR = float(NC.d_rmse_min.quantile(0.95))
    print(f"  NOISE FLOOR (95th pct of dRMSE_min under the null) = {NOISE_FLOOR:+.5f}%")

    R["above_noise"] = R.d_rmse_min > NOISE_FLOOR
    R["hyp_sign"] = R.factor.map(lambda f: CANDIDATES.get(f, 0))
    R["sign_matches_hyp"] = [
        (h == 0) or (np.sign(r) == h) if pd.notna(r) else False
        for h, r in zip(R.hyp_sign, R.cf_A)]
    R = R.sort_values("d_rmse_min", ascending=False)

    cols = ["factor", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
            "t_A", "t_B", "t_C", "tp_A", "tp_B", "tp_C",
            "cf_A", "cf_B", "cf_C", "rr_A", "rr_B", "rr_C",
            "dB_A", "dB_B", "dB_C", "hyp_sign", "sign_matches_hyp",
            "above_noise", "n_te"]
    R[cols].to_csv(CACHE / "screen_results.csv", index=False)
    NC.to_csv(CACHE / "screen_controls.csv", index=False)
    print(f"\n{'='*78}\nFULL RANKED TABLE (dR = OOS RMSE improvement %, "
          f"+ = candidate helps; t = train t-stat; tp = paired t on test SE diff)")
    show = R[["factor", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
              "t_A", "t_B", "t_C", "tp_A", "tp_B", "tp_C", "cf_A", "cf_B",
              "cf_C", "above_noise", "n_te"]]
    with pd.option_context("display.width", 250, "display.max_rows", 200):
        print(show.round(4).to_string(index=False))
    return D, R, NC, NOISE_FLOOR


# ==========================================================================
# STAGE 4 -- deep dives: alternate baselines, drop-one, Gate 4, incumbents,
#            the real posted line, PA-level Stage B, shuffled-label control
# ==========================================================================
SURVIVOR_SET = ["a_swstr", "e_is_home", "a_p5_pitches", "a_stuff_whiff",
                "a_swstr_w15", "a_csw"]


def _tab(rows, cols=None):
    d = pd.DataFrame(rows)
    with pd.option_context("display.width", 250, "display.max_rows", 300):
        print(d.round(4).to_string(index=False))
    return d


def deep_alt_baseline(D):
    """Which OPPONENT representation belongs in the model?

    The main screen puts lineup_k_mean (as-of K% of the nine faced) in BASE,
    so a team-level opponent rate has nothing left to explain. The real
    question is which one to CARRY -- that needs a baseline with no opponent
    term at all."""
    print(f"\n{'='*78}\nA. OPPONENT REPRESENTATION "
          f"(baseline = pitcher history only, NO opponent term)")
    nb = [c for c in BASE if c != "lineup_k_mean"]
    rows = []
    for c in ["lineup_k_mean", "o_k_pct", "Lb_whiff_sw", "o_whiff_sw",
              "Lb_chase", "o_chase", "Lb_zone_miss", "o_zone_miss",
              "Lb_ppa", "o_ppa", "Lb_ts_surv", "o_ts_surv", "b_lhb_share"]:
        rows.append(summarize(eval_candidate(D, c, base=nb), c))
    d = pd.DataFrame(rows).sort_values("d_rmse_min", ascending=False)
    _tab(d[["factor", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
            "t_A", "t_B", "t_C", "cf_A", "cf_B", "cf_C", "n_te"]])

    print("\n   ... and with lineup_k_mean ALREADY in the baseline "
          "(does a team rate add anything on top?)")
    rows = []
    for c in ["o_k_pct", "o_whiff_sw", "o_chase", "o_ppa"]:
        rows.append(summarize(eval_candidate(D, c, base=BASE), c))
    _tab(pd.DataFrame(rows)[["factor", "verdict", "d_rmse_min", "dR_A", "dR_B",
                             "dR_C", "t_A", "t_B", "t_C", "n_te"]])

    # how much does the projected-lineup fallback actually cost?
    print("\n   [repair check] league-average fallback vs team as-of rate, "
          "as a substitute for the real nine:")
    m = D[["lineup_k_mean", "o_k_pct", "actual_k", "game_year"]].dropna()
    print(f"      corr(lineup_k_mean, o_k_pct) = "
          f"{m.lineup_k_mean.corr(m.o_k_pct):+.4f}   n={len(m)}")
    print(f"      sd(lineup nine)={m.lineup_k_mean.std():.4f}  "
          f"sd(team as-of)={m.o_k_pct.std():.4f}  "
          f"sd(constant 0.225)=0.0000")
    return d


def deep_joint(D, survivors=SURVIVOR_SET):
    """Joint fit of BASE + all survivors, then drop-one on each."""
    print(f"\n{'='*78}\nB. JOINT FIT: BASE + survivors, drop-one "
          f"(dR = OOS RMSE lost by dropping that term)")
    full = list(BASE) + list(survivors)
    rows = []
    for c in full:
        rows.append(summarize(eval_candidate(D, c, base=full), c))
    d = pd.DataFrame(rows).sort_values("d_rmse_min", ascending=False)
    d["block"] = ["BASE" if f in BASE else "candidate" for f in d.factor]
    _tab(d[["factor", "block", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
            "t_A", "t_B", "t_C", "cf_A", "cf_B", "cf_C",
            "dB_A", "dB_B", "dB_C", "n_te"]])

    # block value: BASE alone vs BASE+survivors
    print("\n   BLOCK: BASE vs BASE+survivors, out-of-sample")
    cols = full + ["actual_k", "game_year"]
    m = D[cols].replace([np.inf, -np.inf], np.nan).dropna()
    for name, tr_y, te_y in SPLITS:
        tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
        out = []
        for cs in (BASE, full):
            X = np.column_stack([np.ones(len(tr)), tr[cs].to_numpy(float)])
            b, _, _ = _ols(X, tr.actual_k.to_numpy(float))
            p = np.column_stack([np.ones(len(te)), te[cs].to_numpy(float)]) @ b
            out.append((np.sqrt(((te.actual_k - p) ** 2).mean()),
                        np.corrcoef(p, te.actual_k)[0, 1]))
        print(f"     {name}: RMSE {out[0][0]:.4f} -> {out[1][0]:.4f} "
              f"({(out[0][0]-out[1][0])/out[0][0]*100:+.3f}%)   "
              f"r {out[0][1]:.4f} -> {out[1][1]:.4f}   n_te {len(te)}")
    return d


def deep_gate4(D, cols):
    print(f"\n{'='*78}\nC. GATE 4 -- pairwise |r| (fails at |r| > 0.85)")
    m = D[cols].replace([np.inf, -np.inf], np.nan).dropna()
    C = m.corr()
    with pd.option_context("display.width", 250):
        print(C.round(3).to_string())
    bad = [(a, b, C.loc[a, b]) for i, a in enumerate(cols) for b in cols[i+1:]
           if abs(C.loc[a, b]) > 0.85]
    print(f"   pairs above 0.85: {bad if bad else 'NONE'}   (n={len(m)})")
    return C


def deep_market(D):
    """The operator's real benchmark: does the factor predict actual_K minus
    the POSTED line? n is small (the cache ends 2026-08-06) -- report power."""
    print(f"\n{'='*78}\nD. AGAINST THE REAL POSTED LINE (data/model_log.csv)")
    ml = pd.read_csv(REPO / "data" / "model_log.csv")
    print(f"   model_log rows: {len(ml)}")
    j = ml.merge(D, left_on=["game_pk", "pitcher_id"],
                 right_on=["game_pk", "pitcher"], how="inner",
                 suffixes=("", "_d"))
    print(f"   joinable to as-of features: {len(j)}  "
          f"(cache ends {D.game_date.max().date()})")
    j["resid"] = j["actual_k_d"] - j["line"]
    print(f"   corr(line, actual_K)          = "
          f"{ml['line'].corr(ml['actual_k']):+.4f}  n={len(ml)}")
    print(f"   corr(model E[K], actual_K)    = "
          f"{ml['expected_k'].corr(ml['actual_k']):+.4f}  n={len(ml)}")
    sdres = j["resid"].std()
    print(f"   sd(actual_K - line) = {sdres:.3f}; with n={len(j)} a factor "
          f"needs |r|>={2/np.sqrt(len(j)):.3f} for t>=2")
    rows = []
    for c in SURVIVOR_SET + ["o_k_pct", "lineup_k_mean", "h_season_k_pct",
                             "h_prior_bf_mean", "c_bp_heavy", "c_il_return",
                             "a_zone_pct", "e_park_k"]:
        s = j[[c, "resid"]].dropna()
        if len(s) < 20:
            rows.append(dict(factor=c, n=len(s)))
            continue
        z = (s[c] - s[c].mean()) / (s[c].std() if s[c].std() > 0 else 1)
        X = np.column_stack([np.ones(len(s)), z])
        b, se, _ = _ols(X, s["resid"].to_numpy(float))
        rows.append(dict(factor=c, n=len(s), beta_K=float(b[1]),
                         se=float(se[1]), t=float(b[1] / se[1]),
                         r=float(np.corrcoef(z, s["resid"])[0, 1])))
    _tab(rows)
    # is_home is structural, so it is testable on ALL logged rows
    import json
    hh = {}
    for f in sorted((REPO / "data" / "slates").glob("*.json")):
        try:
            for p in json.load(open(f)).get("pitchers", []):
                if p.get("game_pk") and p.get("pitcher_id"):
                    hh[(int(p["game_pk"]), int(p["pitcher_id"]))] = \
                        1.0 if p.get("is_home") else 0.0
        except Exception:
            pass
    ml["is_home"] = [hh.get((int(a), int(b)), np.nan)
                     for a, b in zip(ml.game_pk, ml.pitcher_id)]
    s = ml[["is_home", "line", "actual_k"]].dropna()
    if len(s) > 20:
        r = s["actual_k"] - s["line"]
        X = np.column_stack([np.ones(len(s)), s["is_home"].to_numpy(float)])
        b, se, _ = _ols(X, r.to_numpy(float))
        print(f"   is_home on ALL logged rows with a recoverable home flag: "
              f"beta {b[1]:+.4f} K  se {se[1]:.4f}  t {b[1]/se[1]:+.3f}  n={len(s)}")
    return j


def deep_stage_b(D):
    """PA-level re-test of the shipped Stage B terms: logit_batter_k (the
    largest opponent weight) and the times-through-order penalties."""
    print(f"\n{'='*78}\nE. STAGE B (per-batter K rate) -- incumbent re-test, PA level")
    pa = pd.read_parquet(CACHE / "pa.parquet")
    pa["game_date"] = pd.to_datetime(pa["game_date"])
    st = pd.read_parquet(CACHE / "starts.parquet")
    pa = pa.merge(st[["game_pk", "pitcher"]].assign(_s=1),
                  on=["game_pk", "pitcher"], how="inner")
    B = pd.read_parquet(CACHE / "f_batter.parquet")[
        ["game_pk", "batter", "b_k_pct", "b_whiff_sw", "b_chase", "b_ppa",
         "b_ts_surv", "b_zone_miss"]]
    pa = pa.merge(B, on=["game_pk", "batter"], how="left")
    P = pd.read_parquet(CACHE / "f_pitcher.parquet")[
        ["game_pk", "pitcher", "a_kpct", "a_swstr"]]
    pa = pa.merge(P, on=["game_pk", "pitcher"], how="left")

    def lg(p):
        p = np.clip(p, 1e-4, 1 - 1e-4)
        return np.log(p / (1 - p))

    pa["lp"] = lg(pa["a_kpct"])
    pa["lb"] = lg(pa["b_k_pct"])
    pa["lw"] = lg(pa["b_whiff_sw"])
    pa["tto2"] = (pa["tto"] == 2).astype(float)
    pa["tto3"] = (pa["tto"] >= 3).astype(float)
    m = pa[["isk", "lp", "lb", "lw", "tto2", "tto3", "game_year",
            "b_ppa", "b_ts_surv", "b_chase"]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    print(f"   PA rows from genuine starts, complete: {len(m)}")

    from scipy.optimize import minimize

    def fit(X, y):
        def nll(b):
            z = X @ b
            return float(np.logaddexp(0, z).sum() - (y * z).sum())

        def gr(b):
            p = 1 / (1 + np.exp(-X @ b))
            return X.T @ (p - y)
        r = minimize(nll, np.zeros(X.shape[1]), jac=gr, method="L-BFGS-B")
        return r.x

    FULL = ["lp", "lb", "tto2", "tto3"]
    for drop in [None, "lb", "tto2", "tto3"]:
        cs = [c for c in FULL if c != drop]
        line = f"   drop {str(drop):>5}: "
        for name, tr_y, te_y in SPLITS:
            tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
            Xtr = np.column_stack([np.ones(len(tr)), tr[cs].to_numpy(float)])
            Xte = np.column_stack([np.ones(len(te)), te[cs].to_numpy(float)])
            b = fit(Xtr, tr.isk.to_numpy(float))
            p = 1 / (1 + np.exp(-Xte @ b))
            line += f"{name} Brier {((te.isk-p)**2).mean():.6f}  "
        print(line)
    # coefficient magnitudes, for the Gate 3 effect-size check
    for name, tr_y, te_y in SPLITS:
        tr = m[m.game_year.isin(tr_y)]
        X = np.column_stack([np.ones(len(tr)), tr[FULL].to_numpy(float)])
        b = fit(X, tr.isk.to_numpy(float))
        print(f"   {name} train coefs: " +
              "  ".join(f"{n} {v:+.5f}" for n, v in zip(["int"] + FULL, b)))
    # is whiff-per-swing a better opponent term than batter K%?
    print("   opponent term swap (test-season Brier, lower is better):")
    for cs, lab in [(["lp", "lb", "tto2", "tto3"], "batter K%   (shipped)"),
                    (["lp", "lw", "tto2", "tto3"], "whiff/swing (candidate)"),
                    (["lp", "lb", "lw", "tto2", "tto3"], "both")]:
        line = f"     {lab:24s}"
        for name, tr_y, te_y in SPLITS:
            tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
            Xtr = np.column_stack([np.ones(len(tr)), tr[cs].to_numpy(float)])
            Xte = np.column_stack([np.ones(len(te)), te[cs].to_numpy(float)])
            b = fit(Xtr, tr.isk.to_numpy(float))
            p = 1 / (1 + np.exp(-Xte @ b))
            line += f"  {((te.isk-p)**2).mean():.6f}"
        print(line)


def deep_shuffle(D):
    """Shuffled-label refit. If anything survives, the screen is broken."""
    print(f"\n{'='*78}\nF. SHUFFLED-LABEL CONTROL "
          f"(actual_k permuted within season; nothing should survive)")
    rng = np.random.default_rng(7)
    D2 = D.copy()
    D2["actual_k"] = D2.groupby("game_year")["actual_k"].transform(
        lambda s: rng.permutation(s.to_numpy()))
    rows = []
    for c in SURVIVOR_SET + ["o_k_pct", "c_pitch_budget_15d", "d_team_framing"]:
        if c not in D2.columns:
            continue
        rows.append(summarize(eval_candidate(D2, c), c))
    d = pd.DataFrame(rows)
    _tab(d[["factor", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
            "t_A", "t_B", "t_C"]])
    print(f"   survivors under shuffled labels: "
          f"{int((d.verdict=='SURVIVOR').sum())} (expected 0)")


def deep_window(D):
    print(f"\n{'='*78}\nG. RECENT-FORM WINDOW SWEEP "
          f"(the brief asks for the CURVE, not just the argmax)")
    rows = []
    for n in (1, 2, 3, 5, 8, 10, 15):
        for pre, lab in [("a_swstr_w", "SwStr%"), ("a_csw_w", "CSW%")]:
            c = f"{pre}{n}"
            if c in D.columns:
                s = summarize(eval_candidate(D, c), c)
                s["metric"] = lab
                s["window_starts"] = n
                rows.append(s)
    d = pd.DataFrame(rows).sort_values(["metric", "window_starts"])
    _tab(d[["metric", "window_starts", "factor", "verdict", "d_rmse_min",
            "dR_A", "dR_B", "dR_C", "t_A", "t_B", "t_C"]])


def deep_leak(D):
    """Leakage spot-check: recompute one as-of value from raw cache."""
    print(f"\n{'='*78}\nH. LEAKAGE SPOT-CHECK (as-of value rebuilt from raw cache)")
    row = D[(D.game_year == 2025) & D.a_swstr.notna()].sort_values(
        "game_date").iloc[3000]
    pid, gd, gpk = int(row.pitcher), row.game_date, int(row.game_pk)
    tot = wh = 0
    for f in cache_files():
        d = pq.read_table(f, columns=["game_pk", "game_date", "game_year",
                                      "game_type", "pitcher", "description"]).to_pandas()
        d = d[(d.game_type == "R") & (d.pitcher == pid) & (d.game_year == 2025)]
        if d.empty:
            continue
        d["game_date"] = pd.to_datetime(d["game_date"])
        d = d[(d.game_date < gd) | ((d.game_date == gd) & (d.game_pk < gpk))]
        tot += len(d)
        wh += int(d.description.isin(WHIFF_DESCS).sum())
    print(f"   pitcher {pid} on {gd.date()} (game_pk {gpk})")
    print(f"   independent recount of strictly-prior 2025 pitches: "
          f"{tot} pitches, {wh} whiffs -> raw {wh/max(tot,1):.5f}")
    print(f"   pipeline prior_pitches={int(row.prior_pitches)}  "
          f"a_swstr (shrunk to as-of league, w=300) = {row.a_swstr:.5f}")
    print(f"   pitch counts match: {tot == int(row.prior_pitches)}")


def stage_deep():
    D = assemble()
    ss = _split_specific_csoe(D)
    for nm, cm in ss.items():
        D[nm] = cm["24+25->2026"].to_numpy()   # for correlation/market use
    deep_alt_baseline(D)
    deep_joint(D)
    deep_gate4(D, list(BASE) + SURVIVOR_SET + ["o_k_pct", "a_kpct"])
    deep_market(D)
    deep_stage_b(D)
    deep_shuffle(D)
    deep_window(D)
    deep_leak(D)


# ==========================================================================
# STAGE 5 -- two-channel screen, parsimonious re-base, final model
# ==========================================================================
#: Stage A target is BATTERS FACED. The brief's warning: a factor that moves
#: BF and per-batter K% in OPPOSITE directions nets to nothing on total K and
#: a single-stage screen reports a spurious null. So screen both channels.
BASE_BF = ["h_prior_bf_mean", "h_prior_k_per_start", "a_p5_pitches"]


def deep_two_channel(D):
    print(f"\n{'='*78}\nI. TWO-CHANNEL SCREEN -- Stage A (batters faced) separately")
    print(f"   baseline = {BASE_BF}  (pitcher workload history; there is no "
          f"market line on BF, so this arm is NOT market-anchored)")
    cands = ["o_ppa", "Lb_ppa", "o_k_pct", "lineup_k_mean", "c_bp1d", "c_bp2d",
             "c_bp3d", "c_bp_heavy", "c_leash_pit", "c_leash_bf", "c_il_return",
             "c_dr_short", "c_dr_long", "c_oppsp_kpct", "c_oppsp_bf",
             "c_pitch_budget_15d", "c_budget_resid", "e_is_home",
             "c_career_start", "c_season_start", "a_swstr", "o_ts_surv",
             "o_chase", "Lb_chase", "b_lhb_share"]
    rows = [summarize(eval_candidate(D, c, base=BASE_BF, target="bf"), c)
            for c in cands if c in D.columns]
    d = pd.DataFrame(rows).sort_values("d_rmse_min", ascending=False)
    _tab(d[["factor", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
            "t_A", "t_B", "t_C", "cf_A", "cf_B", "cf_C", "n_te"]])

    print("\n   Stage B channel (per-batter K rate, PA level): the same "
          "opponent-patience terms, logistic on isk")
    pa = pd.read_parquet(CACHE / "pa.parquet")
    st = pd.read_parquet(CACHE / "starts.parquet")
    pa = pa.merge(st[["game_pk", "pitcher", "opponent_team", "game_year"]].rename(
        columns={"game_year": "gy"}), on=["game_pk", "pitcher"], how="inner")
    B = pd.read_parquet(CACHE / "f_batter.parquet")[["game_pk", "batter",
                                                     "b_k_pct", "b_ppa"]]
    pa = pa.merge(B, on=["game_pk", "batter"], how="left")
    P = pd.read_parquet(CACHE / "f_pitcher.parquet")[["game_pk", "pitcher", "a_kpct"]]
    pa = pa.merge(P, on=["game_pk", "pitcher"], how="left")

    def lg(p):
        p = np.clip(p, 1e-4, 1 - 1e-4)
        return np.log(p / (1 - p))
    pa["lp"], pa["lb"] = lg(pa["a_kpct"]), lg(pa["b_k_pct"])
    pa["tto2"] = (pa["tto"] == 2).astype(float)
    pa["tto3"] = (pa["tto"] >= 3).astype(float)
    m = pa[["isk", "lp", "lb", "tto2", "tto3", "b_ppa", "game_year"]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    from scipy.optimize import minimize

    def fit(X, y):
        def nll(b):
            z = X @ b
            return float(np.logaddexp(0, z).sum() - (y * z).sum())

        def gr(b):
            return X.T @ (1 / (1 + np.exp(-X @ b)) - y)
        return minimize(nll, np.zeros(X.shape[1]), jac=gr, method="L-BFGS-B").x

    for name, tr_y, te_y in SPLITS:
        tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
        mu, sd = tr.b_ppa.mean(), tr.b_ppa.std()
        cols = ["lp", "lb", "tto2", "tto3"]
        Xtr = np.column_stack([np.ones(len(tr)), tr[cols].to_numpy(float),
                               (tr.b_ppa.to_numpy() - mu) / sd])
        Xte = np.column_stack([np.ones(len(te)), te[cols].to_numpy(float),
                               (te.b_ppa.to_numpy() - mu) / sd])
        b = fit(Xtr, tr.isk.to_numpy(float))
        p1 = 1 / (1 + np.exp(-Xte @ b))
        b0 = fit(Xtr[:, :-1], tr.isk.to_numpy(float))
        p0 = 1 / (1 + np.exp(-Xte[:, :-1] @ b0))
        y = te.isk.to_numpy(float)
        print(f"     {name}: batter PPA coef {b[-1]:+.5f} (per SD)   "
              f"Brier {((y-p0)**2).mean():.6f} -> {((y-p1)**2).mean():.6f} "
              f"({((y-p0)**2).mean()-((y-p1)**2).mean():+.6f})  n_te {len(te)}")
    return d


def deep_parsimony(D):
    """The pitcher-history block is mutually collinear (r up to 0.94). Test
    whether the market-shaped baseline can be reduced without OOS loss, then
    re-screen the borderline candidates against the reduced base."""
    print(f"\n{'='*78}\nJ. PARSIMONY -- can the baseline be reduced?")
    variants = {
        "BASE (6)": BASE,
        "drop roll3": [c for c in BASE if c != "h_roll3_k"],
        "drop roll3+roll5": [c for c in BASE if c not in ("h_roll3_k", "h_roll5_k")],
        "drop k_per_start": [c for c in BASE if c != "h_prior_k_per_start"],
        "lean(3)": ["h_season_k_pct", "h_prior_bf_mean", "lineup_k_mean"],
        "lean(3)+swstr+home+p5": ["h_season_k_pct", "h_prior_bf_mean",
                                  "lineup_k_mean", "a_swstr", "e_is_home",
                                  "a_p5_pitches"],
    }
    allc = sorted({c for v in variants.values() for c in v})
    m = D[allc + ["actual_k", "game_year"]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    print(f"   common complete-case n={len(m)}")
    rows = []
    for lab, cs in variants.items():
        r = {"model": lab, "k": len(cs)}
        for (name, tr_y, te_y) in SPLITS:
            tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
            X = np.column_stack([np.ones(len(tr)), tr[cs].to_numpy(float)])
            b, _, res = _ols(X, tr.actual_k.to_numpy(float))
            p = np.column_stack([np.ones(len(te)), te[cs].to_numpy(float)]) @ b
            y = te.actual_k.to_numpy(float)
            r[f"RMSE_{name}"] = float(np.sqrt(((y - p) ** 2).mean()))
            line = np.round(p * 2) / 2
            line = np.where(np.isclose(line % 1, 0.0), line + 0.5, line)
            sd = float(np.sqrt((res ** 2).mean()))
            P = 1 - _norm_cdf((line - p) / sd)
            r[f"Brier_{name}"] = float((((y > line).astype(float) - P) ** 2).mean())
        rows.append(r)
    _tab(rows)

    print("\n   re-screen of the split-C failures against the LEAN(3) base "
          "(were they only colliding with redundant history terms?)")
    lean = ["h_season_k_pct", "h_prior_bf_mean", "lineup_k_mean"]
    rows = []
    for c in ["a_swstr", "e_is_home", "a_p5_pitches", "a_stuff_whiff", "a_csw",
              "a_swstr_w15", "c_pitch_budget_15d", "c_budget_resid",
              "c_dr_long", "c_dr_short", "c_il_return", "c_career_start",
              "c_season_start", "e_park_k", "e_air_pfx", "a_zone_pct",
              "c_bp_heavy", "o_k_pct", "d_team_framing", "d_catcher_csoe",
              "a_velo_delta", "a_move_delta", "a_release_sd", "a_putaway",
              "a_fps", "a_mix_tvd", "c_leash_pit", "c_oppsp_kpct"]:
        if c in D.columns:
            rows.append(summarize(eval_candidate(D, c, base=lean), c))
    d = pd.DataFrame(rows).sort_values("d_rmse_min", ascending=False)
    _tab(d[["factor", "verdict", "d_rmse_min", "dR_A", "dR_B", "dR_C",
            "t_A", "t_B", "t_C", "cf_A", "cf_B", "cf_C", "n_te"]])
    return d


def stage_deep2():
    D = assemble()
    ss = _split_specific_csoe(D)
    for nm, cm in ss.items():
        D[nm] = cm["24+25->2026"].to_numpy()
    deep_two_channel(D)
    deep_parsimony(D)


# ==========================================================================
# STAGE 6 -- the consolidated model, Gate 4/5, and w* against real prices
# ==========================================================================
#: What the screen actually recommends. Three market-priced terms (kept),
#: three replacements for the redundant pitcher-history terms (added).
LEAN = ["h_season_k_pct", "h_prior_bf_mean", "lineup_k_mean"]
FINAL = LEAN + ["a_swstr", "e_is_home", "a_p5_pitches"]


def deep_final(D):
    print(f"\n{'='*78}\nK. CONSOLIDATED MODEL")
    print(f"   BASE  = {BASE}")
    print(f"   FINAL = {FINAL}")
    cols = sorted(set(BASE + FINAL))
    m = D[cols + ["actual_k", "game_year", "game_pk", "pitcher"]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    print(f"   complete-case n={len(m)}")

    print("\n   Gate 4 on FINAL (fails at |r|>0.85):")
    C = m[FINAL].corr()
    mx = max(abs(C.loc[a, b]) for i, a in enumerate(FINAL) for b in FINAL[i+1:])
    with pd.option_context("display.width", 200):
        print(C.round(3).to_string())
    print(f"   max off-diagonal |r| = {mx:.3f} -> "
          f"{'PASS' if mx <= 0.85 else 'FAIL'}")

    print("\n   Gate 2 + Gate 5, three-way out-of-sample:")
    for name, tr_y, te_y in SPLITS:
        tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
        out = {}
        for lab, cs in (("BASE", BASE), ("FINAL", FINAL)):
            X = np.column_stack([np.ones(len(tr)), tr[cs].to_numpy(float)])
            b, _, res = _ols(X, tr.actual_k.to_numpy(float))
            p = np.column_stack([np.ones(len(te)), te[cs].to_numpy(float)]) @ b
            y = te.actual_k.to_numpy(float)
            sd = float(np.sqrt((res ** 2).mean()))
            out[lab] = (p, y, sd, np.sqrt(((y - p) ** 2).mean()),
                        np.corrcoef(p, y)[0, 1])
        pb, y, sdb, rmb, rb = out["BASE"]
        pf, _, sdf, rmf, rf = out["FINAL"]
        line = np.round(pb * 2) / 2
        line = np.where(np.isclose(line % 1, 0.0), line + 0.5, line)
        ov = (y > line).astype(float)
        Bb = ((ov - (1 - _norm_cdf((line - pb) / sdb))) ** 2).mean()
        Bf = ((ov - (1 - _norm_cdf((line - pf) / sdf))) ** 2).mean()
        d = (y - pb) ** 2 - (y - pf) ** 2
        print(f"     {name}: RMSE {rmb:.4f}->{rmf:.4f} "
              f"({(rmb-rmf)/rmb*100:+.3f}%, paired t {d.mean()/(d.std(ddof=1)/np.sqrt(len(d))):+.2f})"
              f"   r {rb:.4f}->{rf:.4f}   Brier {Bb:.5f}->{Bf:.5f} "
              f"({Bb-Bf:+.5f})   n_te {len(te)}")

    # ---- calibration curve on the pooled test predictions -----------------
    print("\n   Gate 5 calibration, FINAL model, pooled out-of-sample "
          "P(K > line) deciles:")
    P, O = [], []
    for name, tr_y, te_y in SPLITS:
        tr, te = m[m.game_year.isin(tr_y)], m[m.game_year == te_y]
        Xb = np.column_stack([np.ones(len(tr)), tr[BASE].to_numpy(float)])
        bb, _, _ = _ols(Xb, tr.actual_k.to_numpy(float))
        pb = np.column_stack([np.ones(len(te)), te[BASE].to_numpy(float)]) @ bb
        line = np.round(pb * 2) / 2
        line = np.where(np.isclose(line % 1, 0.0), line + 0.5, line)
        Xf = np.column_stack([np.ones(len(tr)), tr[FINAL].to_numpy(float)])
        bf, _, res = _ols(Xf, tr.actual_k.to_numpy(float))
        pf = np.column_stack([np.ones(len(te)), te[FINAL].to_numpy(float)]) @ bf
        sd = float(np.sqrt((res ** 2).mean()))
        P.append(1 - _norm_cdf((line - pf) / sd))
        O.append((te.actual_k.to_numpy(float) > line).astype(float))
    P, O = np.concatenate(P), np.concatenate(O)
    q = pd.qcut(P, 10, labels=False, duplicates="drop")
    cal = pd.DataFrame({"decile": q, "pred": P, "obs": O}).groupby("decile").agg(
        n=("obs", "size"), pred=("pred", "mean"), obs=("obs", "mean"))
    cal["gap"] = cal["obs"] - cal["pred"]
    print(cal.round(4).to_string())
    print(f"   pooled Brier {(np.mean((O-P)**2)):.5f}  mean|gap| "
          f"{cal.gap.abs().mean():.4f}  n={len(P)}")


def deep_wstar(D):
    """The decisive test. Take the real posted lines and prices, price them
    with the FINAL model fitted on 2024+2025 only, and recompute w* -- the
    Brier-optimal weight on the model's disagreement with the market.
    Shipped model measures w* = -0.775. A model carrying information has
    w* > 0."""
    print(f"\n{'='*78}\nL. w* AGAINST REAL POSTED PRICES")
    ml = pd.read_csv(REPO / "data" / "model_log.csv")
    j = ml.merge(D, left_on=["game_pk", "pitcher_id"],
                 right_on=["game_pk", "pitcher"], how="inner", suffixes=("", "_d"))
    cols = FINAL + ["actual_k", "game_year"]
    tr = D[cols].replace([np.inf, -np.inf], np.nan).dropna()
    tr = tr[tr.game_year.isin([2024, 2025])]
    X = np.column_stack([np.ones(len(tr)), tr[FINAL].to_numpy(float)])
    b, _, res = _ols(X, tr.actual_k.to_numpy(float))
    sd = float(np.sqrt((res ** 2).mean()))
    print(f"   FINAL fitted on 2024+2025 only (n={len(tr)}), residual sd {sd:.3f}")

    j = j.dropna(subset=FINAL + ["line", "fair_over", "over_hit", "actual_k_d"])
    p = np.column_stack([np.ones(len(j)), j[FINAL].to_numpy(float)]) @ b
    j = j.assign(pred=p)
    j["p_over"] = 1 - _norm_cdf((j["line"].to_numpy() - p) / sd)
    print(f"   evaluable logged rows: {len(j)} "
          f"(the cache ends {D.game_date.max().date()}; the 2026-08-07/08 "
          f"slates have no pitch data so no as-of feature exists for them)")

    def wstar(pm, fo, oh):
        d = pm - fo
        r = oh - fo
        return float((d * r).mean() / (d * d).mean())

    w_new = wstar(j.p_over.to_numpy(), j.fair_over.to_numpy(),
                  j.over_hit.to_numpy(float))
    w_old = wstar(j.p_over_calibrated.to_numpy(), j.fair_over.to_numpy(),
                  j.over_hit.to_numpy(float))
    print(f"   w* shipped model (p_over_calibrated) = {w_old:+.4f}")
    print(f"   w* FINAL model                       = {w_new:+.4f}")
    print(f"   corr(line, actual_K)     = {j['line'].corr(j['actual_k_d']):+.4f}")
    print(f"   corr(shipped E[K], act.) = {j['expected_k'].corr(j['actual_k_d']):+.4f}")
    print(f"   corr(FINAL pred, actual) = {j['pred'].corr(j['actual_k_d']):+.4f}")
    r = j["actual_k_d"] - j["line"]
    for lab, v in (("shipped E[K] - line", j["expected_k"] - j["line"]),
                   ("FINAL pred  - line", j["pred"] - j["line"])):
        z = (v - v.mean()) / v.std()
        Xr = np.column_stack([np.ones(len(z)), z])
        bb, se, _ = _ols(Xr, r.to_numpy(float))
        print(f"   {lab}: vs (actual-line) beta {bb[1]:+.4f} K/SD  "
              f"se {se[1]:.4f}  t {bb[1]/se[1]:+.3f}  r {np.corrcoef(z, r)[0,1]:+.4f}")
    # Brier curve in w, as in the brief
    print("   Brier on P(over) as a function of the blend weight w:")
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        pb = j.fair_over + w * (j.p_over - j.fair_over)
        print(f"     w={w:.2f}  Brier {(((j.over_hit - pb)**2).mean()):.5f}")
    return j


def deep_inert(D):
    print(f"\n{'='*78}\nM. PROVABLY INERT INPUT")
    f = REPO / "data" / "manual_pitch_limits.csv"
    txt = f.read_text().strip().splitlines()
    print(f"   data/manual_pitch_limits.csv: {len(txt)} line(s) "
          f"({len(txt)-1} data rows). has_pitch_limit is therefore False on "
          f"every training row and its fitted coefficient is +0.00000.")
    print(f"   header: {txt[0] if txt else '(empty)'}")


def stage_final():
    D = assemble()
    ss = _split_specific_csoe(D)
    for nm, cm in ss.items():
        D[nm] = cm["24+25->2026"].to_numpy()
    deep_final(D)
    deep_wstar(D)
    deep_inert(D)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("scan", "all"):
        stage_scan()
    if cmd in ("features", "all"):
        stage_features()
    if cmd in ("screen", "all"):
        stage_screen()
    if cmd in ("deep", "all"):
        stage_deep()
    if cmd in ("deep2", "all"):
        stage_deep2()
    if cmd in ("final", "all"):
        stage_final()
