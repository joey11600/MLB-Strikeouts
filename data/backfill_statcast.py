"""Statcast pitch-level data backfill.

Downloads pitch-by-pitch data for 2024-2026 using pybaseball.
Stores locally as parquet files partitioned by year-month.

IMPORTANT:
- parallel=False to avoid hammering Savant
- Queries one day at a time (Savant caps at 30,000 rows per request)
- Caches to avoid re-downloading
- Stale-cache footgun: querying a future date caches empty results
  permanently, so we stop at yesterday

Usage:
    python data/backfill_statcast.py [--start 2024-03-28] [--end 2026-08-03]
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# Overridable so the Railway worker can point at its persistent volume
# (STATCAST_CACHE_DIR=/data/statcast_cache). Defaults to the repo path.
CACHE_DIR = Path(
    os.environ.get("STATCAST_CACHE_DIR")
    or (Path(__file__).parent / "statcast_cache")
)


# A parquet holding only a schema and zero rows is ~636 bytes. Any real
# MLB day is hundreds of KB, so this threshold cleanly separates "we
# fetched and there was nothing" from "we fetched too early".
EMPTY_PARQUET_BYTES = 20_000


def _today_et() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def backfill(start_date: date, end_date: date, sleep_sec: float = 1.0) -> None:
    from pybaseball import statcast

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    current = start_date
    total_days = (end_date - start_date).days + 1
    day_num = 0

    while current <= end_date:
        day_num += 1
        date_str = current.strftime("%Y-%m-%d")
        ym = current.strftime("%Y-%m")
        month_dir = CACHE_DIR / ym
        month_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = month_dir / f"{date_str}.parquet"

        if parquet_path.exists():
            size = parquet_path.stat().st_size
            # A cached day counts as FINAL only if it is genuinely in the
            # past AND holds real rows.
            #
            # The old rule was `size > 500`, and an empty parquet (schema
            # only, no rows) is 636 bytes. So a day fetched while its
            # games were still in progress got written empty and then
            # skipped forever: 2026-08-05 sat at 636 bytes / 0 rows, and
            # the whole slate's 28 prospective observations were lost
            # from model_log.csv with nothing raised anywhere. That would
            # have recurred every single day.
            #
            # EMPTY_PARQUET_BYTES is well above a schema-only file but
            # far below any real day (a light slate is ~450 KB).
            fresh_enough = size > EMPTY_PARQUET_BYTES
            settled = current < _today_et()
            if fresh_enough and settled:
                print(f"  [{day_num}/{total_days}] {date_str} — cached ({size:,} bytes)")
                current += timedelta(days=1)
                continue
            reason = ("today or later, games may still be live" if not settled
                      else f"only {size} bytes, looks empty")
            print(f"  [{day_num}/{total_days}] {date_str} — re-fetching ({reason})")

        print(f"  [{day_num}/{total_days}] {date_str} — fetching...", end=" ", flush=True)
        try:
            df = statcast(start_dt=date_str, end_dt=date_str, parallel=False)
            if df is None or df.empty:
                print("no data (off day or future)")
                df = pd.DataFrame()
            else:
                print(f"{len(df)} pitches")
            df.to_parquet(parquet_path, index=False)
        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(sleep_sec)
        current += timedelta(days=1)

    print(f"\nBackfill complete. {day_num} days processed.")
    print(f"Cache at: {CACHE_DIR}")


def load_cached(start_date: date, end_date: date) -> pd.DataFrame:
    """Load cached Statcast data for a date range."""
    frames = []
    current = start_date
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        ym = current.strftime("%Y-%m")
        parquet_path = CACHE_DIR / ym / f"{date_str}.parquet"
        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            if not df.empty:
                frames.append(df)
        current += timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description="Backfill Statcast data")
    parser.add_argument("--start", default="2024-03-28",
                        help="Start date (default: 2024 Opening Day)")
    parser.add_argument("--end", default=None,
                        help="End date (default: yesterday)")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="Seconds between requests")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)

    if end >= date.today():
        end = date.today() - timedelta(days=1)
        print(f"Capping end date at yesterday ({end}) to avoid stale-cache footgun")

    print(f"Backfilling Statcast: {start} to {end}")
    print(f"That's {(end - start).days + 1} days\n")

    backfill(start, end, sleep_sec=args.sleep)


if __name__ == "__main__":
    main()
