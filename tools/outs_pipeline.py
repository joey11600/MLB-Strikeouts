"""Daily outs pipeline (Phase 10) — board, evidence, payload. NO BETS.

The outs market's daily driver, deliberately shaped like the strikeouts
pipeline's evidence half with the money half absent:

  1. refresh the per-start dataset (labels through yesterday)
  2. price today's board (tools/outs_serve.price_board — hazard pmf,
     Gate-5-audited raw probabilities, no-vig fair alongside)
  3. write the day's sidecar (data/outs_slates/) and grade every past
     sidecar row that has settled into data/outs_model_log.csv
  4. rebuild dashboard/public/outs.json — the outs page's OWN payload,
     never touching data.json (separate product, separate artifact)

Usage:
    python tools/outs_pipeline.py            # full daily pass
    python tools/outs_pipeline.py --grade    # grade + payload only
"""
import argparse
import json
import sys

import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.outs_serve import (
    OUTS_LOG_PATH, OUTS_SLATES_DIR, _write_json_atomic, available_dates,
    load_outs_calibrator, load_slate, log_dates, price_board, write_slate)

ET = ZoneInfo("America/New_York")
PAYLOAD_PATH = Path(__file__).parent.parent / "dashboard" / "public" / "outs.json"
SCORECARD_PATH = Path(__file__).parent.parent / "data" / "outs_scorecard.csv"
PAYLOAD_DATES = 7


def _refresh_dataset(force: bool = False) -> None:
    """Rebuild the per-start table when it is behind, else skip.

    The rebuild reads every cached Statcast day across all three
    seasons (~2M PAs) and is the heaviest thing the worker runs —
    measured 1.47 GB peak / 16 s warm for a full pricing pass, most of
    it here and again in the feature build. It only has anything to
    learn once Savant publishes the previous day (A-022: 0 pitches at
    03:21 ET, 3,530 by 08:59), so the morning pass refreshes and the
    16:45 re-price skips. Halves the memory-and-time cost of every
    pass that cannot possibly gain a row.
    """
    from tools.build_outs_dataset import build, OUT_PATH, atomic_write_parquet

    yesterday = (datetime.now(ET) - timedelta(days=1)).date()
    if not force and OUT_PATH.exists():
        try:
            have = pd.read_parquet(OUT_PATH, columns=["game_date"])["game_date"].max()
            if pd.notna(have) and pd.Timestamp(have).date() >= yesterday:
                print(f"  dataset already current through "
                      f"{pd.Timestamp(have).date()} — skipping rebuild")
                return
        except Exception as exc:
            print(f"  (could not read the cached dataset: {exc}; rebuilding)")

    df = build(verbose=False)
    atomic_write_parquet(df, OUT_PATH)
    print(f"  dataset refreshed: {len(df):,} starts through "
          f"{df['game_date'].max().date()}")


def _actuals() -> dict:
    import csv
    out = {}
    if not OUTS_LOG_PATH.exists():
        return out
    with open(OUTS_LOG_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(str(r["date"]), int(r["pitcher_id"]))] = int(r["actual_outs"])
            except (TypeError, ValueError, KeyError):
                continue
    return out


def build_payload() -> dict:
    """The outs page's own artifact: last PAYLOAD_DATES boards with any
    settled results merged in, plus the latest market-scorecard row."""
    import csv

    # Every source, so a board committed from another host is on the
    # page as soon as the repo syncs — a payload rebuilt from one
    # directory would silently DROP yesterday's board (the exact way
    # the date stepper would come up empty tomorrow morning).
    from tools.outs_paper import board_paper_columns

    dates = available_dates()[:PAYLOAD_DATES]
    actual = _actuals()
    slates = {}
    for d in sorted(dates):
        slate = load_slate(d)
        if slate is None:
            continue
        # Side / stake / gates flags through the paper-track code path
        # itself (operator direction 2026-08-26) — hypothetical numbers
        # on a blocked market, but computed by the code money would use.
        paper = board_paper_columns(slate.get("board", []))
        board = []
        for r in slate.get("board", []):
            row = {k: v for k, v in r.items() if k != "pmf"}
            row["actual_outs"] = actual.get((d, int(r.get("pitcher_id", 0))))
            pcols = paper.get(str(r.get("pitcher_id")))
            row["paper_side"] = pcols["side"] if pcols else None
            row["paper_stake_units"] = pcols["stake_units"] if pcols else 0.0
            row["clears_gates"] = bool(pcols and pcols.get("clears_gates"))
            board.append(row)
        # biggest model-vs-market disagreement first — the page's point
        # is watching the disagreements get graded, not implying picks
        board.sort(key=lambda r: -abs((r.get("p_over_cal") or 0.5)
                                      - (r.get("fair_over") or 0.5)))
        slates[d] = {"generated_at": slate.get("generated_at"),
                     "board": board}

    scorecard = None
    if SCORECARD_PATH.exists():
        with open(SCORECARD_PATH, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if rows:
            scorecard = rows[-1]

    from tools.outs_paper import paper_summary

    cal = load_outs_calibrator()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "OUTS",
        "diagnostic_only": True,
        "latest_date": dates[0] if dates else None,
        "slates": slates,
        "scorecard": scorecard,
        "paper_tracks": paper_summary(),
        "calibration": ("gate5: both candidate maps refused on the 2026 "
                        "holdout; serving raw + clamp" if cal is None
                        else f"gate5: {cal['kind']} shipped"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--force-refresh", action="store_true",
                    help="rebuild the dataset even if current")
    ap.add_argument("--grade", action="store_true",
                    help="grade + payload only (no pricing)")
    a = ap.parse_args()
    iso = a.date or datetime.now(ET).strftime("%Y-%m-%d")

    print(f"OUTS PIPELINE — {iso} (shadow only; nothing here is a bet)")
    print("[1/4] refreshing dataset...")
    _refresh_dataset(force=a.force_refresh)

    if not a.grade:
        print("[2/4] pricing today's board...")
        try:
            board = price_board(iso)
        except Exception as exc:
            print(f"  pricing failed: {exc}")
            board = []
        if board:
            path = write_slate(iso, board)
            print(f"  {len(board)} pitchers -> {path}")
        else:
            print("  no outs board available (not posted / nothing matched)")
    else:
        print("[2/4] pricing skipped (--grade)")

    print("[3/4] grading settled sidecar rows...")
    # Boxscore first: MLB posts final innings-pitched at the last out,
    # so the 03:00 job fills the board the same night. Statcast
    # (log_dates, below and every morning) re-derives the same values
    # through the same keyed union — early never replaces authoritative.
    # Fail-soft: an MLB API outage must not cost the board or payload.
    try:
        from tools.outs_boxscore import grade_recent_finals
        nb = grade_recent_finals()
        print(f"  {nb} row(s) graded early from final boxscores")
    except Exception as exc:
        print(f"  boxscore grading skipped ({type(exc).__name__}: {exc})")
    n = log_dates()
    print(f"  {n} row(s) scored into {OUTS_LOG_PATH.name}")

    from tools.outs_paper import PAPER_PATH, log_paper_tracks
    np_ = log_paper_tracks()
    print(f"  {np_} paper-track row(s) scored into {PAPER_PATH.name}")

    print("[4/4] rebuilding outs payload...")
    payload = build_payload()
    _write_json_atomic(PAYLOAD_PATH, payload)
    print(f"  -> {PAYLOAD_PATH} ({len(payload.get('slates', {}))} date(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
