"""Dashboard data generator — reads picks CSV, writes dashboard JSON.

All P&L numbers are computed via tracker._calc_pnl (the canonical
source). Never mental-math the column.

Outputs two tagged P&L types enforced by the FlatUnits/CumulativeUnits
guard:
  - FlatUnits: per-bet profit/loss at fixed 100u basis
  - CumulativeUnits: running total of FlatUnits, same fixed basis

Moving-basis sums (where bankroll changes between bets) NEVER reach
the renderer. The dashboard reads only from this script's output.

Usage:
    python tools/dashboard_data.py              # default output
    python tools/dashboard_data.py --out FILE   # custom output path
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from tracker import _calc_pnl, PICKS_PATH
from tools.pnl_guard import guard as pnl_guard

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

OUTPUT_PATH = Path(__file__).parent.parent / "dashboard" / "data.json"

BASIS_LABEL = "flat_100u"


def build_dashboard_data() -> dict:
    """Read picks CSV and compute all dashboard data."""
    rows = []
    if PICKS_PATH.exists():
        with open(PICKS_PATH, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    picks = []
    wins = 0
    losses = 0
    voids = 0
    pushes = 0
    postponed = 0
    pending = 0
    total_pnl = 0.0
    total_risked = 0.0
    daily_pnl = defaultdict(float)
    daily_record = defaultdict(lambda: {"w": 0, "l": 0})
    cumulative_by_date = {}

    for row in rows:
        graded = (row.get("graded_result") or "").strip().upper()
        bet_placed = (row.get("bet_placed") or "").strip().upper()

        pnl = _calc_pnl(row)
        units = float(row.get("units_risked") or 0)
        pick_date = row.get("date", "")

        if graded == "WIN":
            wins += 1
            daily_record[pick_date]["w"] += 1
        elif graded == "LOSS":
            losses += 1
            daily_record[pick_date]["l"] += 1
        elif graded == "VOID":
            voids += 1
        elif graded == "PUSH":
            pushes += 1
        elif graded == "POSTPONED":
            postponed += 1
        else:
            pending += 1

        if bet_placed == "Y":
            total_pnl += pnl
            total_risked += units
            daily_pnl[pick_date] += pnl

        is_ladder = (row.get("line") or "").endswith("+")

        picks.append({
            "date": pick_date,
            "pitcher_name": row.get("pitcher_name", ""),
            "pitcher_team": row.get("pitcher_team", ""),
            "opponent_team": row.get("opponent_team", ""),
            "is_home": row.get("is_home", "") == "Y",
            "venue": row.get("venue", ""),
            "line": row.get("line", ""),
            "pick_side": row.get("pick_side", ""),
            "pick_strength": row.get("pick_strength", ""),
            "pick_label": row.get("pick_label", ""),
            "model_prob_over": _safe_float(row.get("model_prob_over")),
            "edge_pct": _safe_float(row.get("edge_pct")),
            "market_over_odds": row.get("market_over_odds", ""),
            "market_under_odds": row.get("market_under_odds", ""),
            "units_risked": units,
            "bet_placed": bet_placed == "Y",
            "graded_result": graded if graded else None,
            "actual_strikeouts": _safe_int(row.get("actual_strikeouts")),
            "profit_loss_units": {"value": pnl, "basis": BASIS_LABEL},
            "is_ladder": is_ladder,
            "lineup_source": row.get("lineup_source", ""),
        })

    sorted_dates = sorted(daily_pnl.keys())
    running = 0.0
    cumulative_series = []
    for d in sorted_dates:
        running += daily_pnl[d]
        cumulative_series.append({
            "date": d,
            "daily_pnl": {"value": round(daily_pnl[d], 2), "basis": BASIS_LABEL},
            "cumulative_pnl": {"value": round(running, 2), "basis": BASIS_LABEL},
            "record": daily_record.get(d, {"w": 0, "l": 0}),
        })

    graded_total = wins + losses
    hit_rate = (wins / graded_total) if graded_total > 0 else 0.0
    roi = (total_pnl / total_risked) if total_risked > 0 else 0.0

    today_et = datetime.now(ET).strftime("%Y-%m-%d")
    today_picks = [p for p in picks if p["date"] == today_et]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "basis": BASIS_LABEL,
        "record": {
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "pushes": pushes,
            "postponed": postponed,
            "pending": pending,
            "total_graded": graded_total,
            "hit_rate": round(hit_rate, 4),
        },
        "pnl": {
            "total": {"value": round(total_pnl, 2), "basis": BASIS_LABEL},
            "total_risked": {"value": round(total_risked, 2), "basis": BASIS_LABEL},
            "roi": round(roi, 4),
        },
        "cumulative_series": cumulative_series,
        "today": {
            "date": today_et,
            "picks": today_picks,
            "pick_count": len(today_picks),
            "primary_count": sum(1 for p in today_picks if not p["is_ladder"]),
            "ladder_count": sum(1 for p in today_picks if p["is_ladder"]),
            "total_units": sum(p["units_risked"] for p in today_picks),
        },
        "all_picks": picks,
    }


def _safe_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def write_dashboard_json(output_path: Path | None = None):
    """Generate and write dashboard JSON."""
    path = output_path or OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = build_dashboard_data()

    pnl_guard(data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    n_picks = len(data["all_picks"])
    n_today = data["today"]["pick_count"]
    print(f"Dashboard data written to {path}")
    print(f"  {n_picks} total picks, {n_today} today")
    print(f"  Record: {data['record']['wins']}W-{data['record']['losses']}L")
    print(f"  P&L: {data['pnl']['total']['value']:+.2f}u ({BASIS_LABEL})")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate dashboard data JSON")
    parser.add_argument("--out", metavar="FILE", help="Output path")
    args = parser.parse_args()

    out = Path(args.out) if args.out else None
    write_dashboard_json(out)


if __name__ == "__main__":
    main()
