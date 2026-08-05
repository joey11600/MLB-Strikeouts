"""Production run script — single entry point for daily operations.

Usage:
    python run.py                  # full daily cycle
    python run.py predict          # only generate today's picks
    python run.py grade            # only grade yesterday's + today's picks
    python run.py grade 2026-08-03 # grade a specific date
    python run.py status           # show current record and P&L
    python run.py backfill         # refresh Statcast cache for this week

Full daily cycle (no arguments):
  1. Grade yesterday's picks (if any ungraded)
  2. Show current record and P&L
  3. Run today's predictions with ladder betting
  4. Print summary

Designed for a non-developer operator: clear output, plain English,
no stack traces unless --debug is passed.
"""
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

ET = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def _yesterday_et() -> str:
    return (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")


def cmd_grade(target_date: str | None = None):
    """Grade picks for a date (default: yesterday)."""
    from tools.grader import run_grader

    if target_date is None:
        target_date = _yesterday_et()
    print(f"\n--- GRADING {target_date} ---\n")
    run_grader(target_date=target_date)


def cmd_predict(dry_run: bool = False, no_ladder: bool = False):
    """Run today's predictions."""
    from tools.daily_pipeline import run_daily

    today = _today_et()
    plays = run_daily(
        game_date=today,
        dry_run=dry_run,
        enable_ladder=not no_ladder,
    )
    return plays


def cmd_status():
    """Show current record and P&L via pl_calc."""
    print(f"\n--- RECORD & P&L ---\n")
    from tools.pl_calc import main as pl_main
    pl_main()


def cmd_backfill():
    """Refresh Statcast cache for recent days."""
    from datetime import date
    from data.backfill_statcast import backfill_range

    today = date.fromisoformat(_today_et())
    start = today - timedelta(days=7)
    print(f"\n--- BACKFILLING STATCAST {start} to {today} ---\n")
    backfill_range(start, today)
    print("Backfill complete.")


def cmd_full_cycle(dry_run: bool = False, no_ladder: bool = False):
    """Full daily cycle: grade yesterday, show status, predict today."""
    print("=" * 60)
    print(f"STRIKEOUTS MODEL — DAILY RUN")
    print(f"Date: {_today_et()} ET")
    print("=" * 60)

    # 1. Grade yesterday
    yesterday = _yesterday_et()
    print(f"\nStep 1: Grading yesterday's picks ({yesterday})...")
    try:
        cmd_grade(yesterday)
    except Exception as exc:
        print(f"  Grading failed: {exc}")

    # 2. Show status
    try:
        cmd_status()
    except Exception as exc:
        print(f"  Status failed: {exc}")

    # 3. Today's predictions
    print(f"\nStep 3: Today's predictions...")
    try:
        plays = cmd_predict(dry_run=dry_run, no_ladder=no_ladder)
    except Exception as exc:
        print(f"  Prediction failed: {exc}")
        plays = []

    # Summary
    print(f"\n{'=' * 60}")
    if plays:
        total_u = sum(p.get("units_risked", 0) for p in plays)
        primary = sum(1 for p in plays if p.get("pick_type") != "ladder")
        ladder = sum(1 for p in plays if p.get("pick_type") == "ladder")
        print(f"TODAY: {len(plays)} picks ({primary} primary, {ladder} ladder), {total_u:.1f}u total")
    else:
        print("TODAY: No qualifying plays.")
    print(f"{'=' * 60}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Strikeouts Model — daily operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (none)     Full daily cycle: grade yesterday, show P&L, predict today
  predict    Generate today's picks only
  grade      Grade yesterday's picks (or specify a date)
  status     Show current record and P&L
  backfill   Refresh Statcast cache for the last week
        """,
    )
    parser.add_argument("command", nargs="?", default=None,
                        choices=["predict", "grade", "status", "backfill"])
    parser.add_argument("date", nargs="?", default=None,
                        help="Date for grade command (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Predict but don't write picks")
    parser.add_argument("--no-ladder", action="store_true",
                        help="Skip milestone/alt line ladder")
    parser.add_argument("--debug", action="store_true",
                        help="Show full stack traces on errors")
    args = parser.parse_args()

    try:
        if args.command is None:
            cmd_full_cycle(dry_run=args.dry_run, no_ladder=args.no_ladder)
        elif args.command == "predict":
            cmd_predict(dry_run=args.dry_run, no_ladder=args.no_ladder)
        elif args.command == "grade":
            cmd_grade(target_date=args.date)
        elif args.command == "status":
            cmd_status()
        elif args.command == "backfill":
            cmd_backfill()
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        else:
            print(f"\nError: {exc}")
            print("Run with --debug for full details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
