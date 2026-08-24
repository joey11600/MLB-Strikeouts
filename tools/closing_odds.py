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
    data/odds/closing_YYYY-MM-DD.csv        (primary strikeout O/U board)
    data/odds/closing_alts_YYYY-MM-DD.csv   (milestone alt board)
    data/odds/closing_outs_YYYY-MM-DD.csv   (Outs Recorded O/U board)

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

from scrape_dk_odds import (
    fetch_dk_game_lines,
    fetch_dk_outs_props,
    fetch_dk_strikeout_alts,
    fetch_dk_strikeout_props,
)

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
GAME_LINE_FIELDS = [
    "captured_at", "date", "event_id", "event_name", "start_time_utc",
    "home_team", "away_team", "market", "side", "line", "odds",
]


def capture_game_lines(iso_date: str, captured_at: str) -> int:
    """Snapshot the Game Lines board (moneyline / run line / total).

    A-049: game odds are the market's forecast of game SCRIPT — the
    blowout-risk input (c14) has been untestable since Phase 6 because
    nothing ingested them. Capture-only; nothing prices off these.
    Live-only for the same laundering reason as the boards above.
    """
    rows = []
    for g in fetch_dk_game_lines(iso_date=iso_date):
        rows.append({"captured_at": captured_at, **g})
    if rows:
        _append_rows_atomic(
            ODDS_DIR / f"game_lines_{iso_date}.csv", GAME_LINE_FIELDS, rows)
    return len(rows)


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

    # Outs Recorded O/U. No model prices this market yet -- we capture it
    # now purely because closing prices are perishable: a day not captured
    # is gone permanently, and without a price history there is no way to
    # ever score an outs model against the market (the same gap AUDIT
    # A-002 records for strikeouts). Kept last and wrapped so a failure
    # here cannot cost us the strikeout closing line, which does back
    # money today. allow_snapshot=False for the reason above.
    outs_rows = []
    try:
        outs = fetch_dk_outs_props(allow_snapshot=False)
        print(f"  {len(outs)} outs O/U props")
        for o in outs:
            outs_rows.append({
                "captured_at": captured_at,
                "date": iso_date,
                "pitcher_name": o.get("pitcher_name", ""),
                "team": o.get("team", ""),
                "line": o.get("line", ""),
                "over_odds": o.get("over_odds", ""),
                "under_odds": o.get("under_odds", ""),
                "event_id": o.get("event_id", ""),
                "start_time_utc": o.get("start_time_utc", ""),
            })
        if outs_rows:
            _append_rows_atomic(
                ODDS_DIR / f"closing_outs_{iso_date}.csv",
                PRIMARY_FIELDS,
                outs_rows,
            )
    except Exception as exc:
        print(f"  outs board unavailable ({type(exc).__name__}: {exc}) "
              f"-- strikeout closing lines still captured")

    # Game lines (A-049) — same perishability argument as outs; same
    # isolation so a failure here can't cost the strikeout board.
    n_gl = 0
    try:
        n_gl = capture_game_lines(iso_date, captured_at)
        print(f"  {n_gl} game-line rows (ML / run line / total)")
    except Exception as exc:
        print(f"  game-lines board unavailable ({type(exc).__name__}: {exc}) "
              f"-- strikeout closing lines still captured")

    print(f"  Snapshot appended to {ODDS_DIR}")
    return {
        "primary": len(primary_rows),
        "alts": len(alt_rows),
        "outs": len(outs_rows),
        "game_lines": n_gl,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    capture_closing(target)
