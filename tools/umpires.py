"""Home-plate umpire archive (A-050).

Umpire assignment moves strikeout totals on the order of a few tenths
of a K and the books price it; this repo never ingested it — the
Statcast cache has no umpire column and `get_home_plate_umpire` sat
unwired since Phase 0. Assignments are only reliably knowable from the
boxscore, so this is a POST-GAME capture: it cannot feed a same-day
price, but it builds the as-of history (umpire K tendencies over prior
games) that a future gauntlet candidate needs, and crew rotation makes
tomorrow's plate umpire predictable from today's archive.

data/umpires.csv: date, game_pk, umpire_id, umpire_name. Union-merged
by game_pk, atomic writes, append-only in spirit (rows never deleted).

Usage:
    python tools/umpires.py                       # yesterday (ET)
    python tools/umpires.py 2026-08-20            # one date
    python tools/umpires.py --backfill 2026-03-26 2026-08-23
"""
import csv
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.game_context import (  # noqa: E402
    fetch_boxscore, fetch_schedule, get_home_plate_umpire)
from tracker import DATA_STATE_DIR  # noqa: E402

ET = ZoneInfo("America/New_York")
UMPIRES_PATH = DATA_STATE_DIR / "umpires.csv"
FIELDS = ["date", "game_pk", "umpire_id", "umpire_name"]
SLEEP_BETWEEN_CALLS = 0.4


def _write_atomic(rows: list[dict]) -> None:
    UMPIRES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=UMPIRES_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, UMPIRES_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _load() -> dict:
    if not UMPIRES_PATH.exists():
        return {}
    with open(UMPIRES_PATH, encoding="utf-8") as f:
        return {str(r.get("game_pk")): r for r in csv.DictReader(f)}


def record_umpires_for_date(iso_date: str, verbose: bool = True) -> int:
    """Record the home-plate umpire for every finished regular-season
    game on a date. Skips game_pks already archived (no re-fetch)."""
    existing = _load()
    try:
        games = fetch_schedule(iso_date)
    except Exception as exc:
        print(f"  umpires {iso_date}: schedule fetch failed ({exc})")
        return 0

    new_rows = []
    for g in games:
        gpk = g.get("gamePk")
        if not gpk or str(gpk) in existing:
            continue
        if g.get("gameType") != "R":
            continue
        state = (g.get("status", {}) or {}).get("detailedState", "")
        if not state.startswith("Final") and state != "Completed Early":
            continue
        try:
            box = fetch_boxscore(int(gpk))
            ump = get_home_plate_umpire(box)
        except Exception as exc:
            if verbose:
                print(f"  umpires: boxscore {gpk} failed ({exc})")
            continue
        if not ump:
            continue
        new_rows.append({
            "date": iso_date,
            "game_pk": gpk,
            "umpire_id": ump.get("id", ""),
            "umpire_name": ump.get("fullName", ""),
        })
        time.sleep(SLEEP_BETWEEN_CALLS)

    if not new_rows:
        return 0
    merged = _load()
    for r in new_rows:
        merged[str(r["game_pk"])] = r
    rows = sorted(merged.values(), key=lambda r: (str(r.get("date")),
                                                  str(r.get("game_pk"))))
    _write_atomic(rows)
    if verbose:
        print(f"  umpires {iso_date}: +{len(new_rows)} "
              f"(archive now {len(rows)} games)")
    return len(new_rows)


def backfill(start_iso: str, end_iso: str) -> int:
    d = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    total = 0
    while d <= end:
        total += record_umpires_for_date(d.isoformat())
        d += timedelta(days=1)
    print(f"backfill complete: {total} games recorded")
    return total


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--backfill":
        backfill(args[1], args[2])
        return 0
    target = args[0] if args else (
        datetime.now(ET).date() - timedelta(days=1)).isoformat()
    record_umpires_for_date(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
