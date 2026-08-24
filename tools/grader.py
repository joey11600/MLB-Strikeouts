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
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.game_context import fetch_boxscore, fetch_schedule
from workers.live_strikeouts import starter_is_relieved
from models.edge import no_vig_fair_prob, american_to_implied, ALT_SIDE_MARGIN
from tracker import (
    FIELDS, PICKS_PATH, DATA_STATE_DIR, _write_rows, _calc_pnl, grade_pick,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

ODDS_DIR = DATA_STATE_DIR / "odds"


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return " ".join(n.strip().lower().split())


def _load_closing_snapshots(pick_date: str) -> tuple[list[dict], list[dict]]:
    """Load closing-odds snapshots for a date (primary, alts)."""
    primary, alts = [], []
    p_path = ODDS_DIR / f"closing_{pick_date}.csv"
    a_path = ODDS_DIR / f"closing_alts_{pick_date}.csv"
    if p_path.exists():
        with open(p_path, encoding="utf-8") as f:
            primary = list(csv.DictReader(f))
    if a_path.exists():
        with open(a_path, encoding="utf-8") as f:
            alts = list(csv.DictReader(f))
    return primary, alts


def _last_snapshot(rows: list[dict]) -> dict | None:
    """Last capture at or before the game's start; else the latest one."""
    if not rows:
        return None
    pregame = [
        r for r in rows
        if r.get("start_time_utc") and r.get("captured_at")
        and r["captured_at"] <= r["start_time_utc"]
    ]
    pool = pregame or rows
    return max(pool, key=lambda r: r.get("captured_at") or "")


def _fill_closing_and_clv(row: dict, closing_primary: list[dict],
                          closing_alts: list[dict]) -> None:
    """Write closing odds + CLV into a pick row, if a snapshot matches.

    CLV = fair probability of the pick side at CLOSE minus at OPEN.
    Positive means the market moved toward our side after we bet.
    """
    name = _norm_name(row.get("pitcher_name", ""))
    line = str(row.get("line", ""))
    side = (row.get("pick_side") or "").strip().upper()
    is_ladder = line.endswith("+")

    try:
        if is_ladder:
            milestone = line.rstrip("+")
            matches = [
                r for r in closing_alts
                if _norm_name(r.get("pitcher_name", "")) == name
                and str(r.get("milestone", "")) == milestone
            ]
            snap = _last_snapshot(matches)
            if snap is None:
                return
            close_odds = str(snap.get("odds", ""))
            open_odds = row.get("opened_over_odds", "")
            if not close_odds or not open_odds:
                return
            fair_close = american_to_implied(close_odds) / (1.0 + ALT_SIDE_MARGIN)
            fair_open = american_to_implied(open_odds) / (1.0 + ALT_SIDE_MARGIN)
            row["closing_over_odds"] = f"{int(close_odds):+d}"
            row["closing_under_odds"] = ""
            row["clv_pct"] = f"{fair_close - fair_open:.4f}"
        else:
            matches = [
                r for r in closing_primary
                if _norm_name(r.get("pitcher_name", "")) == name
                and str(r.get("line", "")) == line
            ]
            snap = _last_snapshot(matches)
            if snap is None:
                return
            c_over = snap.get("over_odds", "")
            c_under = snap.get("under_odds", "")
            o_over = row.get("opened_over_odds", "")
            o_under = row.get("opened_under_odds", "")
            if not (c_over and c_under and o_over and o_under):
                return
            nv_close = no_vig_fair_prob(c_over, c_under)
            nv_open = no_vig_fair_prob(o_over, o_under)
            key = "fair_over" if side == "OVER" else "fair_under"
            row["closing_over_odds"] = c_over
            row["closing_under_odds"] = c_under
            row["clv_pct"] = f"{nv_close[key] - nv_open[key]:.4f}"
    except (ValueError, TypeError):
        return


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


def _starter_line_is_settled(game_pk: int, pitcher_id: int) -> bool:
    """True once THIS pitcher's strikeout total can no longer change.

    A starter pulled in the 5th is settled hours before his game ends.
    Waiting for the whole game meant a pick decided at 7:20pm graded at
    03:00 the next morning.

    Note this is strictly narrower than the game being over: it requires
    the pitcher to have ACTUALLY pitched and then been relieved. A
    pitcher who has not appeared is not settled -- that stays a
    game-final question, so an opener or a delayed first pitch can never
    settle a bet that has not begun.
    """
    try:
        return starter_is_relieved(fetch_boxscore(game_pk), pitcher_id)
    except Exception:
        return False


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
    closing_cache = {}

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

        # Grade as soon as THIS pitcher's line is settled, not when the
        # whole game ends. _game_is_postponed above still runs first, so
        # a suspended game never grades early.
        game_final = _game_is_final(gpk, pick_date)
        settled_early = (not game_final) and _starter_line_is_settled(gpk, pid)
        if not (game_final or settled_early):
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

        # A pitcher who never appeared is only a VOID once the GAME is
        # over. Before that he may simply not have entered yet, and
        # voiding a live bet would be unrecoverable.
        if actual_k is None and not game_final:
            skipped_count += 1
            continue

        row["graded_result"] = result
        row["actual_strikeouts"] = str(actual_k) if actual_k is not None else ""
        # Provenance: which condition settled this row. Statcast
        # reconciles both overnight (tools/watchdog.py), so a live grade
        # that turns out wrong is a loud finding rather than a silent
        # error in the money ledger.
        row["graded_source"] = "starter_relieved" if settled_early else "game_final"
        row["profit_loss_units"] = str(round(_calc_pnl({**row, 'graded_result': result}), 4))
        row["updated_at"] = datetime.now(UTC).isoformat()

        # CLV: fill closing odds from pre-game snapshots, if captured.
        if pick_date not in closing_cache:
            closing_cache[pick_date] = _load_closing_snapshots(pick_date)
        _fill_closing_and_clv(row, *closing_cache[pick_date])

        modified = True
        graded_count += 1

        pnl = float(row["profit_loss_units"])
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        clv_str = ""
        if row.get("clv_pct"):
            clv_val = float(row["clv_pct"])
            clv_str = f"  CLV={clv_val:+.1%}"
        print(f"  {result:>4}: {pitcher_name:<22} {line:<6} actual={actual_k}  P&L={pnl_str}u{clv_str}")

    if modified:
        _write_rows(PICKS_PATH, all_rows)
        print(f"\nWrote updated picks to {PICKS_PATH}")

    summary = {"graded": graded_count, "skipped": skipped_count, "errors": error_count}
    print(f"\nGrading complete: {graded_count} graded, {skipped_count} not final, {error_count} errors")

    # A-050: archive the day's home-plate umpires while we're already
    # in post-game territory. Non-fatal — a failed capture never costs
    # a grade.
    if target_date:
        try:
            from tools.umpires import record_umpires_for_date
            record_umpires_for_date(target_date)
        except Exception as exc:
            print(f"  (umpire capture failed: {exc})")

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
