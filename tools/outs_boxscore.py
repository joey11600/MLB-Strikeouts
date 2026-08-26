"""Same-night outs grading from the MLB boxscore API.

Statcast — the authoritative grading source — publishes a finished day
around 09:00 ET the NEXT morning, so the outs board spent every night
showing a finished slate with no results. The public MLB boxscore
posts final innings-pitched the moment a game ends, and it was
validated against the Statcast reconstruction on 548/548 starts
(tools/validate_outs_vs_mlb.py). This module grades from it early;
the morning Statcast pass (outs_serve.log_dates) re-derives the same
values through the same keyed union and thereby confirms them.

Discipline:
- FINAL games only (schedule abstractGameState), never live ones.
- A board pitcher grades only when he is the game's actual starter
  (first pitcher used) with pitching stats — a scratch stays ungraded
  and settles VOID downstream.
- Rows go through outs_serve.union_into_log — append-mostly, atomic,
  never shrinking. A later Statcast overwrite is bit-identical on
  agreement, and disagreement surfaces in the log's history.
- Only recent past ET dates are touched; the in-progress slate is
  never graded (the paper tracks' ET-clock rule, same clock).
"""
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.outs_serve import (
    LOG_FIELDS, OUTS_LOG_PATH, available_dates, load_slate, union_into_log)
from tools.validate_outs_vs_mlb import ip_to_outs

ET = ZoneInfo("America/New_York")
SCHED_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={}"
BOX_URL = "https://statsapi.mlb.com/api/v1/game/{}/boxscore"


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r)


def _graded_ids(iso_date: str) -> set:
    import csv
    got = set()
    if not OUTS_LOG_PATH.exists():
        return got
    with open(OUTS_LOG_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("date") == iso_date:
                got.add(str(r.get("pitcher_id")))
    return got


def _starter_outs(box: dict, pitcher_id) -> int | None:
    """The pitcher's final outs, only if he was a team's actual starter."""
    for side in ("home", "away"):
        team = box.get("teams", {}).get(side, {})
        order = team.get("pitchers", [])
        if not order or order[0] != pitcher_id:
            continue
        try:
            ip = team["players"][f"ID{pitcher_id}"]["stats"][
                "pitching"]["inningsPitched"]
        except (KeyError, TypeError):
            continue
        return ip_to_outs(ip)
    return None


def boxscore_rows(iso_date: str, board: list[dict],
                  fetch=_fetch_json) -> list[dict]:
    """Graded log rows for the board pitchers whose games are FINAL."""
    sched = fetch(SCHED_URL.format(iso_date))
    status = {}
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            status[g["gamePk"]] = g.get("status", {}).get(
                "abstractGameState", "")

    now = datetime.now(timezone.utc).isoformat()
    boxes: dict = {}
    fresh = []
    for r in board:
        gpk = r.get("game_pk")
        if status.get(gpk) != "Final":
            continue
        if gpk not in boxes:
            boxes[gpk] = fetch(BOX_URL.format(gpk))
        got = _starter_outs(boxes[gpk], r.get("pitcher_id"))
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
    return fresh


def grade_recent_finals(days: int = 2, fetch=_fetch_json) -> int:
    """Grade every recent past ET slate that still has ungraded
    pitchers. Returns rows written; a slate with nothing missing costs
    zero API calls."""
    today = datetime.now(ET).date()
    total = 0
    for d in sorted(available_dates()):
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        if not (today - timedelta(days=days) <= dd < today):
            continue
        board = (load_slate(d) or {}).get("board") or []
        graded = _graded_ids(d)
        missing = [r for r in board
                   if str(r.get("pitcher_id")) not in graded]
        if not missing:
            continue
        rows = boxscore_rows(d, missing, fetch=fetch)
        if rows:
            total += union_into_log(rows)
    return total


if __name__ == "__main__":
    n = grade_recent_finals()
    print(f"{n} row(s) graded from final boxscores")
