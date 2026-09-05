"""Outs model serving core (Phase 10) — SHADOW ONLY, prices nothing.

The outs market is a separate product from strikeouts (operator
directive 2026-08-24) and this module is its serving spine:

  * today_board_inputs()  — today's probables matched to the DK outs
    board, expressed as rows the as-of feature builder accepts
  * feature_frame()       — history + today-rows through the SAME
    leakage-safe builder training uses (features/outs_asof). A
    today-row's label placeholders are structurally excluded from its
    own features: pitcher aggregates are cumsum-minus-current /
    shift-before-roll, league and opponent aggregates are prior-DAY.
  * price_board()         — hazard pmf -> P(outs > line), calibrated
    (models/outs_calibrator.pkl, Gate 5), no-vig fair prob alongside,
    plus the row's ROLE facts (what his previous appearance was —
    A-054; facts only, the rule lives in tools/outs_paper)
  * write_slate() / log_dates() — its own sidecar + evidence log,
    union-merged, never touching any strikeouts artifact

NOTHING here computes an edge, a stake, or a pick. The roadmap's rule
stands: the outs model does not touch models/edge.py until a
market-scored sample says its calibrated probabilities survive contact
with real prices (tools/score_outs_vs_market.py measures exactly that).
"""
from __future__ import annotations

import csv
import json
import os
import pickle
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.outs_hazard import MODEL_PATH, ROLE_MODEL_PATH, OutsHazard, p_over
from tracker import DATA_STATE_DIR

CALIBRATOR_PATH = Path(__file__).parent.parent / "models" / "outs_calibrator.pkl"
OUTS_SLATES_DIR = DATA_STATE_DIR / "outs_slates"
OUTS_LOG_PATH = DATA_STATE_DIR / "outs_model_log.csv"
# The git checkout's copy. On the worker DATA_STATE_DIR is the volume,
# so a slate committed from ANOTHER host (or by CI) lands here first
# and only reaches the volume at the next boot's seeding pass. Reading
# both means a board is on the page as soon as the repo syncs, instead
# of disappearing from the site until a redeploy.
REPO_SLATES_DIR = Path(__file__).parent.parent / "data" / "outs_slates"


def slate_dirs() -> list[Path]:
    """Every directory that may hold outs sidecars, freshest first.
    Deduplicated: off the worker both paths are the same directory."""
    seen, out = set(), []
    for d in (OUTS_SLATES_DIR, REPO_SLATES_DIR):
        r = d.resolve()
        if r not in seen and d.exists():
            seen.add(r)
            out.append(d)
    return out


def available_dates() -> list[str]:
    """Sidecar dates across every source, newest first."""
    dates = set()
    for d in slate_dirs():
        dates.update(p.stem for p in d.glob("*.json"))
    return sorted(dates, reverse=True)


def _slate_stamp(slate: dict) -> float | None:
    """A sidecar's own generated_at as a POSIX timestamp, or None."""
    try:
        dt = datetime.fromisoformat(slate.get("generated_at"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def load_slate(iso_date: str) -> dict | None:
    """One day's sidecar -- the FRESHEST copy across every source.

    NOT "the state dir wins", which is what this did on the assumption
    that the volume is always the newest copy. It is not. The 16:45
    re-price is usually run by CI, which has no volume: it writes the
    sidecar into the checkout and commits it, and the worker's boot
    seeding is a gap-fill merge in which the volume's existing copy
    wins. So the volume can hold the 09:00 board while the checkout
    holds the 16:45 one.

    That was latent until the payload started being rebuilt on the
    five-minute publish pass (2026-08-26): before, a rebuild only
    happened on a host that had just written the sidecar itself, so
    the two could not disagree. Afterwards the worker served -- and
    committed -- the morning's 27-row pricing all evening while the
    checkout held the 28-row re-price.

    Ranking by the sidecar's own generated_at is what was always
    meant. Directory order only breaks ties, and an unstamped sidecar
    never displaces a stamped one.
    """
    best, best_key = None, None
    for i, d in enumerate(slate_dirs()):
        p = d / f"{iso_date}.json"
        if not p.exists():
            continue
        try:
            slate = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stamp = _slate_stamp(slate)
        key = (stamp is not None, stamp or 0.0, -i)
        if best_key is None or key > best_key:
            best, best_key = slate, key
    return best

LOG_FIELDS = [
    "date", "game_pk", "pitcher_id", "pitcher_name", "pitcher_team",
    "opponent_team", "is_home", "line", "over_odds", "under_odds",
    "odds_source", "expected_outs", "p_over_raw", "p_over_cal",
    "fair_over", "hold_pct", "actual_outs", "over_hit", "logged_at",
    # The ROLE-set shadow model's P(over) (A-054), blank on rows priced
    # before it existed. tools/score_outs_vs_market.py scores the shadow
    # pkl beside production whenever it is on disk; the promotion decision.
    "p_over_shadow",
]


# ------------------------------------------------------------ calibrator
def load_outs_calibrator() -> dict | None:
    if not CALIBRATOR_PATH.exists():
        return None
    with open(CALIBRATOR_PATH, "rb") as f:
        return pickle.load(f)


def calibrate(p, cal: dict | None):
    """Apply the shipped Gate 5 map; identity (clamped) when absent."""
    p = np.asarray(p, dtype=float)
    eps = (cal or {}).get("prob_eps", 1e-3)
    if cal is None:
        return np.clip(p, eps, 1 - eps)
    if cal["kind"] == "platt":
        x = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
        z = np.clip(cal["a"] + cal["b"] * x, -25, 25)
        return np.clip(1 / (1 + np.exp(-z)), eps, 1 - eps)
    if cal["kind"] == "isotonic":
        out = np.interp(p, np.asarray(cal["iso_x"]), np.asarray(cal["iso_y"]))
        return np.clip(out, eps, 1 - eps)
    raise ValueError(f"unknown calibrator kind {cal['kind']!r}")


def median_outs(pmf) -> int:
    """Smallest k with P(outs <= k) >= 0.5 — the ONE number to print
    beside an over/under side.

    Every market line is a half-integer, so ``median > line`` is exactly
    ``P(over) > 0.5``: the displayed projection can never sit on the
    other side of the line from the displayed side. The mean cannot
    promise that. Outs are left-skewed — blow-up starts at ~7 outs drag
    the mean down while a 26% spike at exactly 15 holds the median up —
    and on the live boards the mean disagreed with the model's own lean
    on 31% of rows (247 rows, 2026-08-26..09-02; the median on 0%). The
    operator read "E[outs] 14.4" beside "OVER 14.5" and, reasonably,
    called it a contradiction (A-052 amendment 2026-09-02). It was: the
    wrong statistic was labelled as the projection.
    """
    c = np.cumsum(np.asarray(pmf, dtype=float))
    return int(np.searchsorted(c, 0.5))


# ---------------------------------------------------------- today inputs
#: The role facts a sidecar row carries (A-054). Facts about the
#: pitcher's PREVIOUS appearance only — the rule that reads them lives in
#: tools/outs_paper.relief_role, never here.
ROLE_FIELDS = ("prev_app_date", "prev_app_pitches", "prev_app_was_start",
               "relief_apps_since_last_start", "days_since_prev_start")

_ROLE_COLS = ("game_pk", "at_bat_number", "inning_topbot",
              "home_team", "away_team")


def _appearance_lookup(iso_date: str, pitcher_ids: list[int]) -> dict[int, dict]:
    """Each pitcher's appearance history this season, from the pitch
    cache (which ends yesterday — strictly prior by construction).

    ``days_since_prev_app`` is _drest_lookup's number and keeps its
    contract: ANY appearance, not just starts, matching training. The
    rest are the ROLE facts the model has no feature for (A-054):

      * was that previous appearance a START — the first pitcher his
        team used in that game, the same definition
        features/outs_asof.build_starts_table and
        features/asof.team_relief_pitches_by_date use
      * how many pitches it was
      * how many relief outings he has made since his last start
      * how long since that last start

    exp_o, the stop rates and p5_pitches are all built over prior STARTS,
    so a reliever making a spot start is priced as a generic starter
    (Kade Morris, 2026-09-04: four relief outings of 35-68 pitches since
    his one June start; model median 15 outs, line 9.5). Facts only —
    nothing here decides anything.
    """
    from data.backfill_statcast import load_cached
    d = date.fromisoformat(iso_date)
    df = load_cached(date(d.year, 3, 26), d)
    out: dict[int, dict] = {}
    if df.empty or not {"pitcher", "game_date"}.issubset(df.columns):
        return out
    wanted = {int(p) for p in pitcher_ids}
    today = pd.Timestamp(d)

    if not set(_ROLE_COLS).issubset(df.columns):
        # Degrade to the old one-number lookup rather than lose days-rest.
        mine = df[df["pitcher"].isin(wanted)]
        last = pd.to_datetime(mine.groupby("pitcher")["game_date"].max())
        for pid, ts in last.items():
            out[int(pid)] = {"days_since_prev_app":
                             float((today - ts.normalize()).days),
                             **{k: None for k in ROLE_FIELDS}}
        return out

    df = df[["pitcher", "game_date", *_ROLE_COLS]].copy()
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.normalize()
    df["pitching_team"] = np.where(df["inning_topbot"] == "Top",
                                   df["home_team"], df["away_team"])
    # The starter is the first pitcher his team used. Computed over
    # EVERY pitcher before narrowing to the ones asked for — a reliever's
    # game must still have its starter to compare against.
    starter = (df.sort_values(["game_pk", "at_bat_number"], kind="mergesort")
                 .groupby(["game_pk", "pitching_team"], sort=False)["pitcher"]
                 .first().to_dict())
    df = df[df["pitcher"].isin(wanted)]
    if df.empty:
        return out
    # One row per appearance; a pitch row is a pitch, so size() is the
    # pitch count (the same count build_starts_table sums per PA).
    app = (df.groupby(["pitcher", "game_pk"], sort=False)
             .agg(game_date=("game_date", "first"),
                  pitches=("at_bat_number", "size"),
                  pitching_team=("pitching_team", "first"))
             .reset_index())
    app["is_start"] = [
        starter.get((g, t)) == p
        for g, t, p in zip(app["game_pk"], app["pitching_team"], app["pitcher"])
    ]
    app = app.sort_values(["pitcher", "game_date", "game_pk"], kind="mergesort")
    for pid, g in app.groupby("pitcher", sort=False):
        is_start = g["is_start"].to_numpy(dtype=bool)
        dates = g["game_date"].to_numpy()
        pitches = g["pitches"].to_numpy()
        starts_at = np.flatnonzero(is_start)
        if len(starts_at):
            last_start = int(starts_at[-1])
            relief_since = int((~is_start[last_start + 1:]).sum())
            days_since_start = float(
                (today - pd.Timestamp(dates[last_start])).days)
        else:
            relief_since = int((~is_start).sum())
            days_since_start = None
        out[int(pid)] = {
            "days_since_prev_app": float((today - pd.Timestamp(dates[-1])).days),
            "prev_app_date": pd.Timestamp(dates[-1]).strftime("%Y-%m-%d"),
            "prev_app_pitches": int(pitches[-1]),
            "prev_app_was_start": bool(is_start[-1]),
            "relief_apps_since_last_start": relief_since,
            "days_since_prev_start": days_since_start,
        }
    return out


def _drest_lookup(iso_date: str, pitcher_ids: list[int]) -> dict[int, float]:
    """Days since each pitcher's previous appearance — the one-number
    view of _appearance_lookup, kept for callers that want only it.
    ANY appearance, not just starts (training's semantics)."""
    return {pid: a["days_since_prev_app"]
            for pid, a in _appearance_lookup(iso_date, pitcher_ids).items()}


def role_block(apps: dict[int, dict], pid: int) -> dict:
    """The sidecar row's role facts. ALWAYS present on a row priced by
    this code — an absent block means the row predates it, which is
    how tools/outs_paper tells "not relief work" from "never looked".
    Every value None for a pitcher with no appearance this season."""
    a = apps.get(int(pid)) or {}
    return {k: a.get(k) for k in ROLE_FIELDS}


def today_board_inputs(iso_date: str) -> list[dict]:
    """Today's probables matched to the DK outs board.

    Returns matched entries (the daily_pipeline matcher's shape: MLB
    identity + the prop's line/odds). Empty when the board isn't
    posted or nothing matches — the caller treats that as "no outs
    board today", never as an error to paper over.
    """
    from data.game_context import build_game_context, fetch_schedule
    from scrape_dk_odds import fetch_dk_outs_props
    from tools.daily_pipeline import _match_dk_to_mlb

    games = [build_game_context(g) for g in fetch_schedule(iso_date)]
    reg = [g for g in games if g.get("game_type") == "R"]
    if not reg:
        return []
    props = [p for p in fetch_dk_outs_props(iso_date=iso_date, allow_snapshot=False)
             if p.get("date") == iso_date]
    if not props:
        return []
    return _match_dk_to_mlb(props, reg)


def feature_frame(today_rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """History (+ optional today-rows) through the training feature
    builder. Label placeholders on today-rows (outs=0, pitches=0) are
    provably inert: every pitcher feature is cumsum-minus-current or
    shift-before-roll, and league/opponent features are prior-DAY."""
    from features.outs_asof import (
        build_appearances_table, build_outs_asof, build_starts_table,
        load_statcast_pa)
    from tools.build_outs_dataset import load_outs_starts

    starts = load_outs_starts()
    if today_rows is not None and len(today_rows):
        starts = pd.concat([starts, today_rows], ignore_index=True)
    pa = load_statcast_pa()
    _, tb = build_starts_table(pa)
    # The ROLE block (A-054) rides the same PA frame: every pitcher's
    # appearances through yesterday, so a today-row's previous appearance
    # is read the way training reads it. The shipped base pkl ignores the
    # columns; a ROLE-set pkl requires them.
    return build_outs_asof(starts, tb, appearances=build_appearances_table(pa))


def _today_rows(iso_date: str, matched: list[dict],
                apps: dict[int, dict] | None = None) -> pd.DataFrame:
    if apps is None:
        apps = _appearance_lookup(iso_date,
                                  [int(m["pitcher_id"]) for m in matched])
    drest = {pid: a["days_since_prev_app"] for pid, a in apps.items()}
    rows = []
    for m in matched:
        pid = int(m["pitcher_id"])
        rows.append({
            "game_pk": int(m["game_pk"]),
            "game_date": pd.Timestamp(iso_date),
            "pitcher": pid,
            "is_home": 1 if m.get("is_home") else 0,
            "opponent_team": m.get("opponent_team", ""),
            # label placeholders — structurally excluded from this
            # row's own features (see feature_frame docstring)
            "outs": 0,
            "pitches": 0.0,
            "days_since_prev_game": drest.get(pid),
        })
    return pd.DataFrame(rows)


def load_shadow_model() -> OutsHazard | None:
    """The ROLE-set shadow pkl, or None when it is not on disk. Absence is
    the normal state before the shadow is fitted, not an error."""
    if not ROLE_MODEL_PATH.exists():
        return None
    try:
        return OutsHazard().load(ROLE_MODEL_PATH)
    except Exception as exc:              # a bad file must not cost the board
        print(f"    outs: shadow model unreadable ({type(exc).__name__}: {exc}); "
              "serving without it")
        return None


# --------------------------------------------------------------- pricing
def price_board(iso_date: str, matched: list[dict] | None = None) -> list[dict]:
    """The day's outs board: model vs market, diagnostic only."""
    from models.edge import no_vig_fair_prob

    if matched is None:
        matched = today_board_inputs(iso_date)
    if not matched:
        return []

    # One pass over the pitch cache serves both the days-rest input and
    # the role facts (A-054): the same frame, read once.
    apps = _appearance_lookup(iso_date, [int(m["pitcher_id"]) for m in matched])
    today = _today_rows(iso_date, matched, apps)
    feat = feature_frame(today)
    feat_today = feat[
        (pd.to_datetime(feat["game_date"]) == pd.Timestamp(iso_date))
        & feat["game_pk"].isin(today["game_pk"])
    ].copy()

    model = OutsHazard().load(MODEL_PATH)
    cal = load_outs_calibrator()
    pmf = model.predict_pmf_frame(feat_today)
    exp_outs = pmf @ np.arange(pmf.shape[1])
    # The shadow rides the same feature rows; the ROLE block is already on
    # them. Nothing here prices, stakes, or picks off the shadow number.
    shadow = load_shadow_model()
    pmf_s = shadow.predict_pmf_frame(feat_today) if shadow is not None else None
    by_key = {(int(r.game_pk), int(r.pitcher)): i
              for i, r in enumerate(feat_today.itertuples())}

    board = []
    for m in matched:
        key = (int(m["game_pk"]), int(m["pitcher_id"]))
        i = by_key.get(key)
        if i is None:
            continue
        try:
            line = float(m.get("line"))
        except (TypeError, ValueError):
            continue
        try:
            raw = float(p_over(pmf[i][None, :], line)[0])
        except ValueError as exc:
            # whole-number line: PUSH needs its own branch; refuse to
            # price rather than fold it into a side (CLAUDE.md).
            print(f"    outs: refusing {m.get('pitcher_name')} at line "
                  f"{line}: {exc}")
            continue
        calp = float(calibrate(raw, cal))
        try:
            nv = no_vig_fair_prob(m.get("over_odds"), m.get("under_odds"))
        except (TypeError, ValueError):
            nv = {"fair_over": None, "hold_pct": None}
        board.append({
            "date": iso_date,
            "game_pk": int(m["game_pk"]),
            "pitcher_id": int(m["pitcher_id"]),
            "pitcher_name": m.get("pitcher_name", ""),
            "pitcher_team": m.get("pitcher_team", ""),
            "opponent_team": m.get("opponent_team", ""),
            "is_home": bool(m.get("is_home")),
            "venue": m.get("venue", ""),
            "start_time_utc": str(m.get("start_time_utc", "") or ""),
            "line": line,
            "over_odds": str(m.get("over_odds", "")),
            "under_odds": str(m.get("under_odds", "")),
            "odds_source": m.get("odds_source", ""),
            "expected_outs": round(float(exp_outs[i]), 2),
            "median_outs": median_outs(pmf[i]),
            "p_over_raw": round(raw, 4),
            "p_over_cal": round(calp, 4),
            "fair_over": (round(float(nv["fair_over"]), 4)
                          if nv.get("fair_over") is not None else None),
            "hold_pct": (round(float(nv["hold_pct"]), 4)
                         if nv.get("hold_pct") is not None else None),
            "pmf": [round(float(x), 5) for x in pmf[i]],
            # What he did in his PREVIOUS appearance (A-054). Facts only:
            # the rule that reads them is tools/outs_paper.relief_role.
            "role": role_block(apps, int(m["pitcher_id"])),
            # The ROLE-set shadow model's read of the same row (A-054):
            # scored beside p_over_cal for two weeks, never a pick.
            "p_over_shadow": (round(float(p_over(pmf_s[i][None, :], line)[0]), 4)
                              if pmf_s is not None else None),
            "expected_outs_shadow": (round(float(pmf_s[i] @ np.arange(pmf_s.shape[1])), 2)
                                     if pmf_s is not None else None),
            "median_outs_shadow": (median_outs(pmf_s[i]) if pmf_s is not None else None),
            # No edge, no stake, no side, on purpose.
            "diagnostic_only": True,
        })
    return board


# --------------------------------------------------------------- storage
def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_slate(iso_date: str, board: list[dict]) -> Path:
    """Sidecar for the day, merged newest-wins per pitcher (a re-price
    must not drop pitchers whose games have started — the K sidecar's
    hard-won rule)."""
    path = OUTS_SLATES_DIR / f"{iso_date}.json"
    rows = list(board)
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            fresh = {r["pitcher_id"] for r in rows}
            rows += [r for r in prior.get("board", [])
                     if r.get("pitcher_id") not in fresh]
        except (OSError, ValueError):
            pass
    _write_json_atomic(path, {
        "date": iso_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "OUTS",
        "diagnostic_only": True,
        "board": rows,
    })
    return path


def log_dates(targets: list[str] | None = None) -> int:
    """Score every sidecar row that has a settled result into the outs
    evidence log (union by key, never shrinking — the model_log rules)."""
    from tools.build_outs_dataset import load_outs_starts

    dates = sorted(available_dates())
    if targets:
        dates = [d for d in dates if d in targets]
    if not dates:
        return 0

    starts = load_outs_starts()
    actual = {(int(r.game_pk), int(r.pitcher)): int(r.outs)
              for r in starts.itertuples()}

    now = datetime.now(timezone.utc).isoformat()
    fresh = []
    for d in dates:
        slate = load_slate(d)
        if slate is None:
            continue
        for r in slate.get("board", []):
            got = actual.get((int(r.get("game_pk", 0)),
                              int(r.get("pitcher_id", 0))))
            if got is None:
                continue
            line = float(r["line"])
            fresh.append({
                **{k: r.get(k, "") for k in LOG_FIELDS
                   if k not in ("actual_outs", "over_hit", "logged_at",
                                "is_home")},
                "is_home": 1 if r.get("is_home") else 0,
                "actual_outs": got,
                "over_hit": 1 if got > line else 0,
                "logged_at": now,
            })

    return union_into_log(fresh)


def union_into_log(fresh: list[dict]) -> int:
    """Merge graded rows into the evidence log by (date, game_pk,
    pitcher_id) — append-mostly, atomic, refuses to shrink. Shared by
    the Statcast grader above and the boxscore grader
    (tools/outs_boxscore.py); a second copy of this merge would drift.
    """
    existing = []
    if OUTS_LOG_PATH.exists():
        with open(OUTS_LOG_PATH, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    merged = {(str(r.get("date")), str(r.get("game_pk")),
               str(r.get("pitcher_id"))): r for r in existing}
    kept = len(merged)
    for r in fresh:
        merged[(str(r["date"]), str(r["game_pk"]), str(r["pitcher_id"]))] = r
    rows = sorted(merged.values(),
                  key=lambda r: (str(r["date"]), str(r["pitcher_name"])))
    if len(rows) < kept:
        raise RuntimeError(
            f"outs log would shrink {kept} -> {len(rows)}; refusing")
    if rows:
        OUTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=OUTS_LOG_PATH.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=LOG_FIELDS,
                                   extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, OUTS_LOG_PATH)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    return len(fresh)
