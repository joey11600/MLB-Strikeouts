"""Dashboard data generator v2 — full slate archive + performance + model analytics.

All P&L numbers are computed via tracker._calc_pnl (the canonical
source). Never mental-math the column. Every P&L value is tagged
{"value": x, "basis": "flat_100u"} and the FlatUnits guard runs before
every write.

v2 payload (consumed by the Next.js dashboard):
  record / pnl            — headline aggregates
  performance             — daily series, per-bet ledger, splits, CLV
  available_dates         — every date with a slate or picks, desc
  slates[date]            — full evaluated board per date, merged with
                            the picks ledger (grades, stakes, P&L) and
                            actual strikeouts per pitcher
  model                   — honest backtest analytics (per-line Brier,
                            calibration bins raw vs calibrated),
                            gauntlet summary

Usage:
    python tools/dashboard_data.py              # writes dashboard/public/data.json
    python tools/dashboard_data.py --out FILE
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

ROOT = Path(__file__).parent.parent
OUTPUT_PATH = ROOT / "dashboard" / "public" / "data.json"
SLATES_DIR = ROOT / "data" / "slates"
PREDICTIONS_PATH = ROOT / "data" / "backtest_predictions.csv"
GAUNTLET_PATH = ROOT / "data" / "gauntlet_results.json"

BASIS_LABEL = "flat_100u"


def _safe_float(val):
    if val is None or val == "":
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _tag(v: float) -> dict:
    return {"value": round(v, 4), "basis": BASIS_LABEL}


def _load_picks() -> list[dict]:
    if not PICKS_PATH.exists():
        return []
    with open(PICKS_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _pick_payload(row: dict) -> dict:
    graded = (row.get("graded_result") or "").strip().upper()
    return {
        "pick_side": row.get("pick_side", ""),
        "pick_strength": row.get("pick_strength", ""),
        "pick_label": row.get("pick_label", ""),
        "units_risked": _safe_float(row.get("units_risked")) or 0.0,
        "bet_placed": (row.get("bet_placed") or "").upper() == "Y",
        "graded_result": graded or None,
        "actual_strikeouts": _safe_int(row.get("actual_strikeouts")),
        "profit_loss_units": _tag(_calc_pnl(row)),
        "clv_pct": _safe_float(row.get("clv_pct")),
        "edge_pct": _safe_float(row.get("edge_pct")),
        "model_prob_over": _safe_float(row.get("model_prob_over")),
        "market_over_odds": row.get("market_over_odds", ""),
        "market_under_odds": row.get("market_under_odds", ""),
        "lineup_source": row.get("lineup_source", ""),
    }


def _actual_k_lookup(dates: set[str]) -> dict:
    """Actual strikeouts per (game_pk, pitcher_id) for slate dates.

    Degrades gracefully when the Statcast cache is unavailable.
    """
    lookup = {}
    if not dates:
        return lookup
    try:
        from datetime import date as _date
        from data.backfill_statcast import load_cached

        lo = min(_date.fromisoformat(d) for d in dates)
        hi = max(_date.fromisoformat(d) for d in dates)
        df = load_cached(lo, hi)
        if df.empty:
            return lookup
        completed = df[df["events"].notna()]
        ks = completed[completed["events"].isin(
            ["strikeout", "strikeout_double_play"])]
        counts = ks.groupby(["game_pk", "pitcher"]).size()
        bf = completed.groupby(["game_pk", "pitcher"]).size()
        for key, n_bf in bf.items():
            lookup[(int(key[0]), int(key[1]))] = int(counts.get(key, 0))
    except Exception as exc:
        print(f"  (actual-K lookup skipped: {exc})")
    return lookup


def _build_slates(picks: list[dict]) -> tuple[dict, list[str]]:
    """Merge slate sidecars with the picks ledger, keyed by date."""
    slates = {}
    pick_index = {}
    for row in picks:
        key = (row.get("date"), str(row.get("pitcher_id")), str(row.get("line")))
        pick_index[key] = row

    slate_dates = set()
    if SLATES_DIR.exists():
        for f in sorted(SLATES_DIR.glob("*.json")):
            slate_dates.add(f.stem)

    pick_dates = {row.get("date") for row in picks if row.get("date")}
    all_dates = sorted(slate_dates | pick_dates, reverse=True)

    actual_k = _actual_k_lookup(slate_dates)

    for d in all_dates:
        sidecar_path = SLATES_DIR / f"{d}.json"
        if sidecar_path.exists():
            with open(sidecar_path, encoding="utf-8") as f:
                sidecar = json.load(f)

            pitchers = []
            for p in sidecar.get("pitchers", []):
                pid = str(p.get("pitcher_id"))
                line_key = str(p.get("line"))
                pick_row = pick_index.get((d, pid, line_key))

                rungs = []
                for r in p.get("ladder", []):
                    ladder_row = pick_index.get((d, pid, f"{r['milestone']}+"))
                    rung = dict(r)
                    if ladder_row is not None:
                        rung["pick"] = _pick_payload(ladder_row)
                    rungs.append(rung)

                entry = dict(p)
                entry["ladder"] = rungs
                entry["pick"] = _pick_payload(pick_row) if pick_row else None
                entry["actual_strikeouts"] = actual_k.get(
                    (int(p.get("game_pk") or 0), int(p.get("pitcher_id") or 0))
                )
                if entry["actual_strikeouts"] is None and pick_row is not None:
                    entry["actual_strikeouts"] = _safe_int(
                        pick_row.get("actual_strikeouts"))
                pitchers.append(entry)

            def _sort_key(e):
                units = (e.get("pick") or {}).get("units_risked", 0) or 0
                return (-units, -(e.get("edge_best") or 0))

            pitchers.sort(key=_sort_key)

            slates[d] = {
                "date": d,
                "reconstructed": bool(sidecar.get("reconstructed")),
                "note": sidecar.get("note"),
                "generated_at": sidecar.get("generated_at"),
                "pitcher_count": len(pitchers),
                "bet_count": sum(
                    1 for p in pitchers
                    if (p.get("pick") or {}).get("bet_placed")
                    or any((r.get("pick") or {}).get("bet_placed") for r in p["ladder"])
                ),
                "pitchers": pitchers,
            }
        else:
            # Ledger-only date (no sidecar): synthesize minimal entries.
            date_rows = [r for r in picks if r.get("date") == d]
            pitchers = []
            seen = set()
            for row in date_rows:
                pid = str(row.get("pitcher_id"))
                if (row.get("line") or "").endswith("+"):
                    continue
                seen.add(pid)
                pitchers.append({
                    "pitcher_id": _safe_int(row.get("pitcher_id")),
                    "pitcher_name": row.get("pitcher_name"),
                    "pitcher_team": row.get("pitcher_team"),
                    "opponent_team": row.get("opponent_team"),
                    "is_home": row.get("is_home") == "Y",
                    "venue": row.get("venue"),
                    "line": _safe_float(row.get("line")),
                    "over_odds": row.get("market_over_odds", ""),
                    "under_odds": row.get("market_under_odds", ""),
                    "strength": row.get("pick_strength"),
                    "edge_best": _safe_float(row.get("edge_pct")),
                    "best_side": row.get("pick_side"),
                    "k_dist": [],
                    "ladder": [
                        {**{
                            "milestone": int((lr.get("line") or "0+").rstrip("+")),
                            "odds": lr.get("market_over_odds", ""),
                            "edge": _safe_float(lr.get("edge_pct")),
                            "status": "bet",
                            "units_risked": _safe_float(lr.get("units_risked")) or 0.0,
                        }, "pick": _pick_payload(lr)}
                        for lr in date_rows
                        if str(lr.get("pitcher_id")) == pid
                        and (lr.get("line") or "").endswith("+")
                    ],
                    "pick": _pick_payload(row),
                    "actual_strikeouts": _safe_int(row.get("actual_strikeouts")),
                })
            slates[d] = {
                "date": d,
                "reconstructed": False,
                "note": "Ledger-only date (no slate sidecar).",
                "generated_at": None,
                "pitcher_count": len(pitchers),
                "bet_count": len(pitchers),
                "pitchers": pitchers,
            }

    return slates, all_dates


def _build_performance(picks: list[dict]) -> dict:
    daily_pnl = defaultdict(float)
    daily_units = defaultdict(float)
    daily_record = defaultdict(lambda: {"w": 0, "l": 0})

    def _bucket():
        return {"wins": 0, "losses": 0, "pnl": 0.0, "risked": 0.0}

    by_side = defaultdict(_bucket)
    by_strength = defaultdict(_bucket)
    by_type = defaultdict(_bucket)

    ledger = []
    clv_vals = []

    for row in picks:
        graded = (row.get("graded_result") or "").strip().upper()
        bet = (row.get("bet_placed") or "").upper() == "Y"
        pnl = _calc_pnl(row)
        units = _safe_float(row.get("units_risked")) or 0.0
        d = row.get("date", "")
        is_ladder = (row.get("line") or "").endswith("+")

        if bet:
            daily_pnl[d] += pnl
            daily_units[d] += units
        if graded == "WIN":
            daily_record[d]["w"] += 1
        elif graded == "LOSS":
            daily_record[d]["l"] += 1

        if bet and graded in ("WIN", "LOSS"):
            for bucket, key in [
                (by_side, row.get("pick_side", "?")),
                (by_strength, row.get("pick_strength", "?")),
                (by_type, "ladder" if is_ladder else "primary"),
            ]:
                b = bucket[key]
                b["wins" if graded == "WIN" else "losses"] += 1
                b["pnl"] += pnl
                b["risked"] += units

        clv = _safe_float(row.get("clv_pct"))
        if clv is not None:
            clv_vals.append(clv)

        side = (row.get("pick_side") or "").upper()
        odds = row.get("market_over_odds" if side == "OVER" else "market_under_odds", "")
        if is_ladder:
            odds = row.get("market_over_odds", "")

        ledger.append({
            "date": d,
            "pitcher_name": row.get("pitcher_name", ""),
            "pick_label": row.get("pick_label", ""),
            "line": row.get("line", ""),
            "pick_side": side,
            "odds": odds,
            "units_risked": units,
            "bet_placed": bet,
            "graded_result": graded or None,
            "actual_strikeouts": _safe_int(row.get("actual_strikeouts")),
            "profit_loss_units": _tag(pnl),
            "is_ladder": is_ladder,
            "clv_pct": clv,
        })

    ledger.sort(key=lambda r: (r["date"], r["pitcher_name"]), reverse=True)

    sorted_dates = sorted(daily_pnl.keys())
    running = 0.0
    daily = []
    for d in sorted_dates:
        running += daily_pnl[d]
        daily.append({
            "date": d,
            "daily_pnl": _tag(daily_pnl[d]),
            "cumulative_pnl": _tag(running),
            "units": round(daily_units[d], 2),
            "w": daily_record[d]["w"],
            "l": daily_record[d]["l"],
        })

    def _finalize(bucket: dict) -> dict:
        out = {}
        for key, b in bucket.items():
            n = b["wins"] + b["losses"]
            out[key] = {
                "wins": b["wins"],
                "losses": b["losses"],
                "hit_rate": round(b["wins"] / n, 4) if n else None,
                "pnl": _tag(b["pnl"]),
                "roi": round(b["pnl"] / b["risked"], 4) if b["risked"] else None,
            }
        return out

    return {
        "daily": daily,
        "ledger": ledger,
        "splits": {
            "by_side": _finalize(by_side),
            "by_strength": _finalize(by_strength),
            "by_type": _finalize(by_type),
        },
        "clv": {
            "n": len(clv_vals),
            "avg_clv_pct": round(sum(clv_vals) / len(clv_vals), 4) if clv_vals else None,
        },
    }


def _build_model_analytics() -> dict:
    out = {"backtest": None, "gauntlet": None}

    if PREDICTIONS_PATH.exists():
        with open(PREDICTIONS_PATH, encoding="utf-8") as f:
            preds = list(csv.DictReader(f))

        for p in preds:
            p["model_p_over"] = float(p["model_p_over"])
            p["naive_p_over"] = float(p["naive_p_over"])
            p["actual_over"] = int(p["actual_over"])
            p["line"] = float(p["line"])

        per_line = []
        for line in sorted({p["line"] for p in preds}):
            sub = [p for p in preds if p["line"] == line]
            n_brier = sum((p["naive_p_over"] - p["actual_over"]) ** 2 for p in sub) / len(sub)
            m_brier = sum((p["model_p_over"] - p["actual_over"]) ** 2 for p in sub) / len(sub)
            per_line.append({
                "line": line,
                "naive_brier": round(n_brier, 4),
                "model_brier": round(m_brier, 4),
                "improvement_pct": round((n_brier - m_brier) / n_brier * 100, 1) if n_brier else 0,
                "n": len(sub),
            })

        overall_n = sum((p["naive_p_over"] - p["actual_over"]) ** 2 for p in preds) / len(preds)
        overall_m = sum((p["model_p_over"] - p["actual_over"]) ** 2 for p in preds) / len(preds)

        # Calibration bins on RAW model probs, with the production
        # calibrator's mapping overlaid.
        calibrate = None
        try:
            from models.calibration import IsotonicCalibrator, CALIBRATOR_PATH
            if CALIBRATOR_PATH.exists():
                cal = IsotonicCalibrator()
                cal.load()
                calibrate = cal.predict
        except Exception:
            pass

        sorted_preds = sorted(preds, key=lambda p: p["model_p_over"])
        n_bins = 10
        bin_size = max(1, len(sorted_preds) // n_bins)
        bins = []
        for start in range(0, len(sorted_preds), bin_size):
            chunk = sorted_preds[start:start + bin_size]
            if not chunk:
                continue
            pred_mean = sum(p["model_p_over"] for p in chunk) / len(chunk)
            actual_rate = sum(p["actual_over"] for p in chunk) / len(chunk)
            bins.append({
                "pred_mean": round(pred_mean, 4),
                "actual_rate": round(actual_rate, 4),
                "calibrated_mean": round(calibrate(pred_mean), 4) if calibrate else None,
                "n": len(chunk),
            })

        out["backtest"] = {
            "train_cutoff": "2026-07-08",
            "test_window": "2026-07-09 to 2026-08-03",
            "n_predictions": len(preds),
            "n_starts": len({(p["game_pk"], p["pitcher"]) for p in preds}),
            "naive_brier": round(overall_n, 4),
            "model_brier": round(overall_m, 4),
            "improvement_pct": round((overall_n - overall_m) / overall_n * 100, 1),
            "per_line": per_line,
            "calibration_bins": bins,
        }

    if GAUNTLET_PATH.exists():
        with open(GAUNTLET_PATH, encoding="utf-8") as f:
            gauntlet = json.load(f)
        features = []
        for name, res in gauntlet.items():
            gates = {}
            for g in ["gate1", "gate2", "gate3", "gate4", "gate5"]:
                gres = res.get(g) or {}
                # tri-state: True / False / None (gate not evaluated)
                gates[g] = gres.get("passed") if "passed" in gres else None
            splits = (res.get("gate2") or {}).get("splits") or {}
            imps = [
                s.get("improvement_pct")
                for s in splits.values()
                if isinstance(s, dict) and s.get("improvement_pct") is not None
            ]
            features.append({
                "feature": name,
                "gates": gates,
                "promoted": res.get("verdict") == "PROMOTED",
                "failed_at": res.get("failed_at"),
                "min_improvement_pct": round(min(imps), 3) if imps else None,
            })
        features.sort(key=lambda x: (not x["promoted"], -(x["min_improvement_pct"] or -99)))
        out["gauntlet"] = {
            "features": features,
            "noise_floor_pct": 0.167,
        }

    return out


def build_dashboard_data() -> dict:
    picks = _load_picks()

    wins = losses = voids = pushes = postponed = pending = 0
    total_pnl = 0.0
    total_risked = 0.0

    for row in picks:
        graded = (row.get("graded_result") or "").strip().upper()
        bet = (row.get("bet_placed") or "").upper() == "Y"
        if graded == "WIN":
            wins += 1
        elif graded == "LOSS":
            losses += 1
        elif graded == "VOID":
            voids += 1
        elif graded == "PUSH":
            pushes += 1
        elif graded == "POSTPONED":
            postponed += 1
        else:
            pending += 1
        if bet:
            total_pnl += _calc_pnl(row)
            total_risked += _safe_float(row.get("units_risked")) or 0.0

    graded_total = wins + losses
    slates, available_dates = _build_slates(picks)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "basis": BASIS_LABEL,
        "today_et": datetime.now(ET).strftime("%Y-%m-%d"),
        "record": {
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "pushes": pushes,
            "postponed": postponed,
            "pending": pending,
            "total_graded": graded_total,
            "hit_rate": round(wins / graded_total, 4) if graded_total else 0.0,
        },
        "pnl": {
            "total": _tag(total_pnl),
            "total_risked": _tag(total_risked),
            "roi": round(total_pnl / total_risked, 4) if total_risked else 0.0,
        },
        "available_dates": available_dates,
        "slates": slates,
        "performance": _build_performance(picks),
        "model": _build_model_analytics(),
    }


def write_dashboard_json(output_path: Path | None = None):
    path = output_path or OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = build_dashboard_data()
    pnl_guard(data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

    size_kb = path.stat().st_size / 1024
    print(f"Dashboard data written to {path} ({size_kb:.0f} KB)")
    print(f"  Dates: {', '.join(data['available_dates']) or '(none)'}")
    print(f"  Record: {data['record']['wins']}W-{data['record']['losses']}L")
    print(f"  P&L: {data['pnl']['total']['value']:+.2f}u ({BASIS_LABEL})")
    if data["model"]["backtest"]:
        bt = data["model"]["backtest"]
        print(f"  Model: Brier {bt['model_brier']} vs naive {bt['naive_brier']} "
              f"({bt['improvement_pct']:+.1f}%)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate dashboard data JSON")
    parser.add_argument("--out", metavar="FILE", help="Output path")
    args = parser.parse_args()
    write_dashboard_json(Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
