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
    (models/outs_calibrator.pkl, Gate 5), no-vig fair prob alongside
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

from models.outs_hazard import MODEL_PATH, OutsHazard, p_over
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


def load_slate(iso_date: str) -> dict | None:
    """One day's sidecar, preferring the state dir (the worker's own,
    freshest copy) over the checkout."""
    for d in slate_dirs():
        p = d / f"{iso_date}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return None

LOG_FIELDS = [
    "date", "game_pk", "pitcher_id", "pitcher_name", "pitcher_team",
    "opponent_team", "is_home", "line", "over_odds", "under_odds",
    "odds_source", "expected_outs", "p_over_raw", "p_over_cal",
    "fair_over", "hold_pct", "actual_outs", "over_hit", "logged_at",
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


# ---------------------------------------------------------- today inputs
def _drest_lookup(iso_date: str, pitcher_ids: list[int]) -> dict[int, float]:
    """Days since each pitcher's previous appearance, from the pitch
    cache (which ends yesterday — strictly prior by construction).
    Matches training's semantics: ANY appearance, not just starts."""
    from data.backfill_statcast import load_cached
    d = date.fromisoformat(iso_date)
    df = load_cached(date(d.year, 3, 26), d)
    out: dict[int, float] = {}
    if df.empty:
        return out
    df = df[df["pitcher"].isin(pitcher_ids)]
    if df.empty:
        return out
    last = pd.to_datetime(df.groupby("pitcher")["game_date"].max())
    for pid, ts in last.items():
        out[int(pid)] = float((pd.Timestamp(d) - ts.normalize()).days)
    return out


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
        build_outs_asof, build_starts_table, load_statcast_pa)
    from tools.build_outs_dataset import load_outs_starts

    starts = load_outs_starts()
    if today_rows is not None and len(today_rows):
        starts = pd.concat([starts, today_rows], ignore_index=True)
    pa = load_statcast_pa()
    _, tb = build_starts_table(pa)
    return build_outs_asof(starts, tb)


def _today_rows(iso_date: str, matched: list[dict]) -> pd.DataFrame:
    drest = _drest_lookup(iso_date, [int(m["pitcher_id"]) for m in matched])
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


# --------------------------------------------------------------- pricing
def price_board(iso_date: str, matched: list[dict] | None = None) -> list[dict]:
    """The day's outs board: model vs market, diagnostic only."""
    from models.edge import no_vig_fair_prob

    if matched is None:
        matched = today_board_inputs(iso_date)
    if not matched:
        return []

    today = _today_rows(iso_date, matched)
    feat = feature_frame(today)
    feat_today = feat[
        (pd.to_datetime(feat["game_date"]) == pd.Timestamp(iso_date))
        & feat["game_pk"].isin(today["game_pk"])
    ].copy()

    model = OutsHazard().load(MODEL_PATH)
    cal = load_outs_calibrator()
    pmf = model.predict_pmf_frame(feat_today)
    exp_outs = pmf @ np.arange(pmf.shape[1])
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
            "p_over_raw": round(raw, 4),
            "p_over_cal": round(calp, 4),
            "fair_over": (round(float(nv["fair_over"]), 4)
                          if nv.get("fair_over") is not None else None),
            "hold_pct": (round(float(nv["hold_pct"]), 4)
                         if nv.get("hold_pct") is not None else None),
            "pmf": [round(float(x), 5) for x in pmf[i]],
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
