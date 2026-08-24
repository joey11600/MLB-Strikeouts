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
from datetime import datetime, timezone
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


def _refresh_dataset() -> None:
    from tools.build_outs_dataset import build, OUT_PATH, atomic_write_parquet
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
    dates = available_dates()[:PAYLOAD_DATES]
    actual = _actuals()
    slates = {}
    for d in sorted(dates):
        slate = load_slate(d)
        if slate is None:
            continue
        board = []
        for r in slate.get("board", []):
            row = {k: v for k, v in r.items() if k != "pmf"}
            row["actual_outs"] = actual.get((d, int(r.get("pitcher_id", 0))))
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

    cal = load_outs_calibrator()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "OUTS",
        "diagnostic_only": True,
        "latest_date": dates[0] if dates else None,
        "slates": slates,
        "scorecard": scorecard,
        "calibration": ("gate5: both candidate maps refused on the 2026 "
                        "holdout; serving raw + clamp" if cal is None
                        else f"gate5: {cal['kind']} shipped"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--grade", action="store_true",
                    help="grade + payload only (no pricing)")
    a = ap.parse_args()
    iso = a.date or datetime.now(ET).strftime("%Y-%m-%d")

    print(f"OUTS PIPELINE — {iso} (shadow only; nothing here is a bet)")
    print("[1/4] refreshing dataset...")
    _refresh_dataset()

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
    n = log_dates()
    print(f"  {n} row(s) scored into {OUTS_LOG_PATH.name}")

    print("[4/4] rebuilding outs payload...")
    payload = build_payload()
    _write_json_atomic(PAYLOAD_PATH, payload)
    print(f"  -> {PAYLOAD_PATH} ({len(payload.get('slates', {}))} date(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
