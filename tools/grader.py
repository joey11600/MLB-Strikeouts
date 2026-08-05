"""Auto-grading pipeline — grade picks after games complete.

Usage:
    python tools/grader.py                 # grade today's ungraded picks
    python tools/grader.py 2026-08-04      # grade a specific date
    python tools/grader.py --all           # grade all ungraded picks

Fetches actual strikeout counts from MLB Stats API boxscores, then
applies tracker.grade_pick() for each ungraded row. Handles:
  - WIN / LOSS for standard O/U and milestone bets
  - PUSH on whole-number lines (stake returned)
  - VOID if the listed starter didn't throw a pitch (scratched)
  - POSTPONED games stay until rescheduled

Graded picks are locked (defensive lock #1 in tracker.py) and cannot
be overwritten by subsequent pipeline runs.
"""
import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.game_context import fetch_boxscore, fetch_schedule
from tracker import (
    FIELDS, PICKS_PATH, _write_rows, _calc_pnl, grade_pick,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _fetch_actual_strikeouts(game_pk: int, pitcher_id: int) -> int | None:
    """Fetch actual strikeout count for a pitcher from the boxscore.

    Returns None if the pitcher didn't appear (scratched) or the game
    hasn't finished.
    """
    try:
        box = fetch_boxscore(game_pk)
    except Exception:
        return None

    for side in ["home", "away"]:
        team = box.get("teams", {}).get(side, {})
        players = team.get("players", {})
        for key, pdata in players.items():
            pid = pdata.get("person", {}).get("id")
            if pid == pitcher_id:
                pitching = pdata.get("stats", {}).get("pitching", {})
                k_count = pitching.get("strikeOuts")
                if k_count is not None:
                    return int(k_count)
                return None

    return None


def _game_is_final(game_pk: int, game_date: str) -> bool:
    """Check if a game has reached Final status."""
    try:
        games = fetch_schedule(game_date)
        for g in games:
            if g.get("gamePk") == game_pk:
                status = g.get("status", {}).get("detailedState", "")
                return status in ("Final", "Game Over", "Completed Early")
    except Exception:
        pass
    return False


def _game_is_postponed(game_pk: int, game_date: str) -> bool:
    """Check if a game was postponed."""
    try:
        games = fetch_schedule(game_date)
        for g in games:
            if g.get("gamePk") == game_pk:
                status = g.get("status", {}).get("detailedState", "")
                return "Postponed" in status or "Suspended" in status
    except Exception:
        pass
    return False


def grade_milestone_pick(actual_k: int | None, milestone: int) -> str:
    """Grade a milestone/ladder pick (e.g. 6+ K).

    Milestone bets win if K >= milestone, lose otherwise.
    No push (milestones are whole numbers, win/lose only).
    """
    if actual_k is None:
        return "VOID"
    if actual_k >= milestone:
        return "WIN"
    return "LOSS"


def run_grader(
    target_date: str | None = None,
    grade_all: bool = False,
) -> dict:
    """Grade ungraded picks.

    Returns dict with counts: graded, skipped, errors.
    """
    if not PICKS_PATH.exists():
        print("No picks file found.")
        return {"graded": 0, "skipped": 0, "errors": 0}

    with open(PICKS_PATH, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    if not all_rows:
        print("No picks to grade.")
        return {"graded": 0, "skipped": 0, "errors": 0}

    if target_date is None and not grade_all:
        target_date = datetime.now(ET).strftime("%Y-%m-%d")

    print(f"Grading picks" + (f" for {target_date}" if target_date else " (all dates)") + "...")

    graded_count = 0
    skipped_count = 0
    error_count = 0
    modified = False

    boxscore_cache = {}

    for i, row in enumerate(all_rows):
        existing_grade = (row.get("graded_result") or "").strip().upper()
        if existing_grade in ("WIN", "LOSS", "VOID", "PUSH", "POSTPONED"):
            continue

        pick_date = row.get("date", "")
        if target_date and pick_date != target_date:
            continue

        game_pk = row.get("game_pk", "")
        pitcher_id = row.get("pitcher_id", "")
        pitcher_name = row.get("pitcher_name", "")
        line = row.get("line", "")

        try:
            gpk = int(game_pk)
            pid = int(pitcher_id)
        except (ValueError, TypeError):
            error_count += 1
            continue

        if _game_is_postponed(gpk, pick_date):
            row["graded_result"] = "POSTPONED"
            row["updated_at"] = datetime.now(UTC).isoformat()
            modified = True
            graded_count += 1
            print(f"  POSTPONED: {pitcher_name} ({line})")
            continue

        if not _game_is_final(gpk, pick_date):
            skipped_count += 1
            continue

        cache_key = (gpk, pid)
        if cache_key in boxscore_cache:
            actual_k = boxscore_cache[cache_key]
        else:
            actual_k = _fetch_actual_strikeouts(gpk, pid)
            boxscore_cache[cache_key] = actual_k

        is_ladder = line.endswith("+")
        if is_ladder:
            try:
                milestone = int(line.rstrip("+"))
            except ValueError:
                error_count += 1
                continue
            result = grade_milestone_pick(actual_k, milestone)
        else:
            result = grade_pick(row, actual_k)

        row["graded_result"] = result
        row["actual_strikeouts"] = str(actual_k) if actual_k is not None else ""
        row["profit_loss_units"] = f"{_calc_pnl({**row, 'graded_result': result}):.2f}"
        row["updated_at"] = datetime.now(UTC).isoformat()
        modified = True
        graded_count += 1

        pnl = float(row["profit_loss_units"])
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        print(f"  {result:>4}: {pitcher_name:<22} {line:<6} actual={actual_k}  P&L={pnl_str}u")

    if modified:
        _write_rows(PICKS_PATH, all_rows)
        print(f"\nWrote updated picks to {PICKS_PATH}")

    summary = {"graded": graded_count, "skipped": skipped_count, "errors": error_count}
    print(f"\nGrading complete: {graded_count} graded, {skipped_count} not final, {error_count} errors")
    return summary


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Grade strikeout picks")
    parser.add_argument("date", nargs="?", default=None, help="Date to grade (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="Grade all ungraded picks")
    args = parser.parse_args()

    run_grader(target_date=args.date, grade_all=args.all)


if __name__ == "__main__":
    main()
