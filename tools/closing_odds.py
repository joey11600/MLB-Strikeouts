"""Closing-odds capture for CLV (closing line value) tracking.

CLV is the fastest honest signal of model quality: if the picks we make
consistently sit on the right side of where the market CLOSES, the
model is finding real edge — measurable in weeks, long before the W/L
record means anything. If our picks show negative CLV, the market is
telling us the model is wrong, whatever the short-term P&L says.

Run this shortly before first pitch (or several times through the
evening — every snapshot is kept, stamped with captured_at):

    python run.py close
    python tools/closing_odds.py

Snapshots append to:
    data/odds/closing_YYYY-MM-DD.csv        (primary O/U board)
    data/odds/closing_alts_YYYY-MM-DD.csv   (milestone alt board)

The grader picks the last snapshot at or before each game's start time
and writes closing_over_odds / closing_under_odds / clv_pct into the
picks ledger as it grades.
"""
import csv
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from scrape_dk_odds import fetch_dk_strikeout_props, fetch_dk_strikeout_alts

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

from tracker import DATA_STATE_DIR

ODDS_DIR = DATA_STATE_DIR / "odds"

PRIMARY_FIELDS = [
    "captured_at", "date", "pitcher_name", "team", "line",
    "over_odds", "under_odds", "event_id", "start_time_utc",
]
ALT_FIELDS = [
    "captured_at", "date", "pitcher_name", "team", "milestone",
    "odds", "event_id", "start_time_utc",
]


def _append_rows_atomic(path: Path, fields: list[str], new_rows: list[dict]) -> None:
    """Append rows via read-all + atomic rewrite (repo atomic-write rule)."""
    existing = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    all_rows = existing + new_rows

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def capture_closing(iso_date: str | None = None) -> dict:
    """Snapshot the current DK board into the closing-odds files."""
    if iso_date is None:
        iso_date = datetime.now(ET).strftime("%Y-%m-%d")
    captured_at = datetime.now(UTC).isoformat()

    print(f"Capturing closing odds snapshot for {iso_date} at {captured_at}...")

    # allow_snapshot=False is load-bearing, not defensive boilerplate.
    # This function is what DEFINES the closing price: it re-dates every
    # row to today and re-stamps captured_at to now. Feed it a snapshot
    # and a stale board gets laundered into closing_<today>.csv wearing a
    # fresh timestamp — which then (a) becomes the closing line the CLV
    # grader writes into the ledger, (b) walks straight through
    # daily_pipeline's date== filter, the only guard against pricing an
    # old board, and (c) resets the staleness clock forever, since each
    # close run re-stamps it. Losing CLV on a blocked day is the cheap
    # failure; recording a wrong closing price is the expensive one.
    props = fetch_dk_strikeout_props(allow_snapshot=False)
    print(f"  {len(props)} primary O/U props")
    primary_rows = []
    for p in props:
        primary_rows.append({
            "captured_at": captured_at,
            "date": iso_date,
            "pitcher_name": p.get("pitcher_name", ""),
            "team": p.get("team", ""),
            "line": p.get("line", ""),
            "over_odds": p.get("over_odds", ""),
            "under_odds": p.get("under_odds", ""),
            "event_id": p.get("event_id", ""),
            "start_time_utc": p.get("start_time_utc", ""),
        })
    if primary_rows:
        _append_rows_atomic(
            ODDS_DIR / f"closing_{iso_date}.csv", PRIMARY_FIELDS, primary_rows
        )

    alts = fetch_dk_strikeout_alts(allow_snapshot=False)  # see note above
    print(f"  {len(alts)} milestone alt rungs")
    alt_rows = []
    for a in alts:
        alt_rows.append({
            "captured_at": captured_at,
            "date": iso_date,
            "pitcher_name": a.get("pitcher_name", ""),
            "team": a.get("team", ""),
            "milestone": a.get("milestone", ""),
            "odds": a.get("odds", ""),
            "event_id": a.get("event_id", ""),
            "start_time_utc": a.get("start_time_utc", ""),
        })
    if alt_rows:
        _append_rows_atomic(
            ODDS_DIR / f"closing_alts_{iso_date}.csv", ALT_FIELDS, alt_rows
        )

    print(f"  Snapshot appended to {ODDS_DIR}")
    return {"primary": len(primary_rows), "alts": len(alt_rows)}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    capture_closing(target)
