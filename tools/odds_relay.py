#!/usr/bin/env python3
"""odds_relay.py -- publish a DraftKings odds snapshot from a machine
DraftKings will actually talk to.

The problem
-----------
DraftKings answers our requests from the operator's home internet
connection and returns HTTP 403 to the Railway container.  Measured
2026-08-06: every curl_cffi impersonation profile, and even plain
`requests` with browser headers, returns 200 from the residential IP;
the identical code in the container gets 403.  The discriminator is
egress IP reputation -- sportsbooks routinely block cloud ranges -- and
no client-side header or TLS knob changes that.

The fix that needs no credentials and no deploy
-----------------------------------------------
Run this on the operator's PC.  It fetches the live board from the
residential IP and writes the same snapshot CSVs the cloud worker
already knows how to read:

    data/odds/dk_k_<date>.csv        primary O/U board
    data/odds/dk_k_alts_<date>.csv   milestone / alt board

The worker picks them up because:

  1. `tools/railway_worker.py::sync_repo()` already runs
     `git pull --rebase` before the morning, lineups and night tasks.
  2. `scrape_dk_odds.odds_dirs()` searches the git-pulled checkout as
     well as the persistent volume, and prefers the freshest copy.

So the whole transport is: run `snapshot`, commit, push.  Nothing to
deploy, nothing to pay for, no proxy credentials anywhere.

The worker still tries DraftKings live first and only reads the
snapshot when the live call raises, and only when
DK_ODDS_SNAPSHOT_FALLBACK=1 is set on the service.  A snapshot older
than DK_ODDS_SNAPSHOT_MAX_AGE_H (default 6h) is refused outright rather
than priced -- see scrape_dk_odds.OddsSnapshotUnavailable.

Usage
-----
    python tools/odds_relay.py snapshot
        Fetch live and write both boards.  Prints the git commands to
        publish -- it never runs git for you.

    python tools/odds_relay.py watch --every 20 --until 19:15
        Re-snapshot every 20 minutes until 19:15 ET, so the published
        board keeps up with line movement while the PC is on.

    python tools/odds_relay.py status
        Report which snapshot the fallback would serve and how old it
        is.  No network call.
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from scrape_dk_odds import (  # noqa: E402
    ALT_FIELDS,
    OU_FIELDS,
    SNAPSHOT_FALLBACK_ENV,
    check_snapshot,
    fetch_dk_strikeout_alts,
    fetch_dk_strikeout_props,
    write_csv,
)
from tracker import DATA_STATE_DIR  # noqa: E402

ET = ZoneInfo("America/New_York")
ODDS_DIR = DATA_STATE_DIR / "odds"


def _log(msg: str) -> None:
    print(f"[{datetime.now(ET):%H:%M:%S ET}] {msg}", flush=True)


def take_snapshot(iso_date: str | None = None, alts: bool = True) -> dict:
    """Fetch the live board and write it to the odds directory.

    allow_snapshot=False throughout: this is the PRODUCER.  If it were
    allowed to fall back to reading a snapshot, a failed fetch would
    rewrite the existing file and reset its mtime -- laundering stale
    odds into apparently-fresh ones.  A failure here must stay a
    failure.
    """
    iso_date = iso_date or datetime.now(ET).strftime("%Y-%m-%d")
    written: dict[str, int] = {}

    props = fetch_dk_strikeout_props(allow_snapshot=False)
    if not props:
        raise RuntimeError(
            "DraftKings returned an empty O/U board -- refusing to write an "
            "empty snapshot over a good one. The slate may not be posted yet."
        )
    ou_path = ODDS_DIR / f"dk_k_{iso_date}.csv"
    write_csv(props, ou_path, OU_FIELDS)
    written[str(ou_path)] = len(props)
    _log(f"wrote {len(props)} O/U lines -> {ou_path}")

    if alts:
        try:
            alt_rows = fetch_dk_strikeout_alts(allow_snapshot=False)
        except Exception as exc:
            # The ladder degrades gracefully without alts; the primary
            # board is what the slate actually needs.
            _log(f"alt board fetch failed ({exc}) -- primary board still written")
            alt_rows = []
        if alt_rows:
            alt_path = ODDS_DIR / f"dk_k_alts_{iso_date}.csv"
            write_csv(alt_rows, alt_path, ALT_FIELDS)
            written[str(alt_path)] = len(alt_rows)
            _log(f"wrote {len(alt_rows)} alt lines -> {alt_path}")

    return written


def _publish_hint(written: dict) -> None:
    """Print the commands to publish.  Deliberately does not run them --
    committing on the operator's behalf is their call, not this
    script's."""
    if not written:
        return
    stamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    print("\nTo publish this snapshot to the cloud worker, run:\n")
    print("    git add data/odds")
    print(f'    git commit -m "chore(odds): relay snapshot {stamp} ET"')
    print("    git push origin master\n")
    print("The worker git-pulls before each scheduled task and will read")
    print(f"these files when its own DK fetch 403s (needs {SNAPSHOT_FALLBACK_ENV}=1).")


def cmd_snapshot(args) -> int:
    try:
        written = take_snapshot(args.date, alts=not args.no_alts)
    except Exception as exc:
        _log(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    _publish_hint(written)
    return 0


def cmd_watch(args) -> int:
    stop = None
    if args.until:
        try:
            hh, mm = (int(x) for x in args.until.split(":", 1))
        except ValueError:
            print(f"--until must look like 19:15, got {args.until!r}")
            return 2
        now = datetime.now(ET)
        stop = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if stop <= now:
            print(f"--until {args.until} is already past.")
            return 2

    _log(f"watching every {args.every} min"
         + (f" until {stop:%H:%M ET}" if stop else " (Ctrl-C to stop)"))
    last: dict = {}
    while True:
        try:
            last = take_snapshot(args.date, alts=not args.no_alts)
        except Exception as exc:
            # A single failed poll is not fatal: the previous snapshot
            # stays on disk and ages, and the staleness ceiling in
            # scrape_dk_odds is what decides whether it is still usable.
            _log(f"snapshot failed ({type(exc).__name__}: {exc}) -- will retry")

        nxt = datetime.now(ET).timestamp() + args.every * 60
        if stop and nxt > stop.timestamp():
            break
        try:
            time.sleep(max(0.0, nxt - datetime.now(ET).timestamp()))
        except KeyboardInterrupt:
            _log("stopped")
            break
    _publish_hint(last)
    return 0


def cmd_status(args) -> int:
    return check_snapshot(args.date)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Publish DK odds snapshots from a residential IP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date", metavar="YYYY-MM-DD",
                   help="Slate date (default: today ET)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="Fetch live and write both boards")
    s.add_argument("--no-alts", action="store_true",
                   help="Skip the milestone/alt board")
    s.set_defaults(func=cmd_snapshot)

    w = sub.add_parser("watch", help="Re-snapshot on an interval")
    w.add_argument("--every", type=float, default=20.0,
                   help="Minutes between snapshots (default 20)")
    w.add_argument("--until", metavar="HH:MM",
                   help="Stop at this ET time (default: run until Ctrl-C)")
    w.add_argument("--no-alts", action="store_true")
    w.set_defaults(func=cmd_watch)

    st = sub.add_parser("status", help="Report snapshot freshness (no network)")
    st.set_defaults(func=cmd_status)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
