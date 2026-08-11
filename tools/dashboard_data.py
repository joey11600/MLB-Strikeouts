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
  live_model              — LIVE calibration from data/model_log.csv
                            (every evaluated pitcher, not just the bets)
                            measured against the backtest baseline

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
from tracker import DATA_STATE_DIR

SLATES_DIR = DATA_STATE_DIR / "slates"
MODEL_LOG_PATH = DATA_STATE_DIR / "model_log.csv"
PREDICTIONS_PATH = ROOT / "data" / "backtest_predictions.csv"
GAUNTLET_PATH = ROOT / "data" / "gauntlet_results.json"

BASIS_LABEL = "flat_100u"

# Validated out-of-sample Brier from data/backtest_predictions.csv. This
# is the FLAT average across the whole backtest line grid and is NOT a
# valid yardstick for the live log on its own — see _line_matched_baseline.
# Kept only as a last-resort fallback when per-line figures are missing.
BACKTEST_BRIER_BASELINE = 0.1491

# Below this many genuinely prospective (reconstructed==0) rows the live
# numbers are published with a sample_warning instead of standing alone.
MIN_LIVE_ROWS = 20


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


def _live_rows_for(iso: str) -> dict:
    """{pitcher_id: live line} from the MLB-API watcher, for ONE date.

    Was "today only", against a single live_state.json the poller
    overwrites. Two things went wrong with that (A-035). The file rolls
    to the new date at midnight ET and drops yesterday's finals, and the
    today-guard then discarded whatever was left -- so between midnight
    and the ~09:00 Statcast publish (A-022) the previous day's board
    showed a blank K total for every starter who was not a graded bet.
    Results that had been on screen all evening vanished overnight and
    came back mid-morning, which is exactly how the operator described
    it, twice.

    The guard itself was load-bearing and is not simply dropped: these
    rows are keyed only by pitcher_id, and a starter appears on many
    dates, so a payload applied to the wrong slate would attach one
    night's strikeouts to another night's start. Hence per-date lookup
    rather than a global one.
    """
    payload = {}
    path = DATA_STATE_DIR / "live" / f"{iso}.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
    if not payload:
        # Fall back to the current-day file for the window before the
        # first archive write, and for any deployment still running the
        # old poller. Same date check as before -- it must match the
        # slate being rendered, not merely exist.
        legacy = DATA_STATE_DIR / "live_state.json"
        try:
            candidate = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if candidate.get("date") != iso:
            return {}
        payload = candidate
    if payload.get("date") != iso:
        return {}
    return {int(r["pitcher_id"]): r for r in payload.get("pitchers", [])
            if r.get("pitcher_id") is not None}


def _build_slates(picks: list[dict]) -> tuple[dict, list[str]]:
    """Merge slate sidecars with the picks ledger, keyed by date."""
    # Read once per date, not once per pitcher.
    live_by_date: dict[str, dict] = {}
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

                # Live fallback. A starter's K total is settled the
                # moment he is pulled, but Statcast will not publish it
                # for hours, so the board sat blank all evening on
                # results that were already decided. Only fills a gap --
                # never overwrites a Statcast or ledger figure, which
                # stay the graded truth.
                if d not in live_by_date:
                    live_by_date[d] = _live_rows_for(d)
                lv = live_by_date[d].get(int(p.get("pitcher_id") or 0))
                if lv:
                    if entry["actual_strikeouts"] is None:
                        entry["actual_strikeouts"] = lv.get("strikeouts")
                        entry["result_source"] = "live"
                    entry["live"] = {
                        "strikeouts": lv.get("strikeouts"),
                        "batters_faced": lv.get("batters_faced"),
                        "pitches": lv.get("pitches"),
                        "innings": lv.get("innings"),
                        "final": bool(lv.get("final")),
                        "game_state": lv.get("game_state"),
                        "status": lv.get("status"),
                    }
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
                    "start_time_utc": "",
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
            # Irreducible Brier floor: what a PERFECTLY calibrated model
            # would score given exactly this confidence profile. Brier
            # alone is not comparable across samples — a board of
            # coin-flips has a far higher floor than a board of
            # near-certainties. Brier minus floor is, which is what the
            # live block compares against.
            m_floor = sum(p["model_p_over"] * (1 - p["model_p_over"]) for p in sub) / len(sub)
            per_line.append({
                "line": line,
                "naive_brier": round(n_brier, 4),
                "model_brier": round(m_brier, 4),
                "model_floor": round(m_floor, 4),
                "model_excess": round(m_brier - m_floor, 4),
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

        meta_path = ROOT / "data" / "backtest_meta.json"
        train_desc = "games before 2026-07-08"
        test_window = "2026-07-09 to 2026-08-03"
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            train_desc = meta.get("train_desc", train_desc)
            test_window = meta.get("test_window", test_window)

        out["backtest"] = {
            "train_desc": train_desc,
            "test_window": test_window,
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
            # gauntlet_results.json predates the Phase 6 noise-floor
            # re-evaluation (its stored verdicts for a10_fps_pct and
            # f7_month_factor say PROMOTED; the published decision in
            # CHANGELOG/GATES.md rejected them). Display the published
            # promotion set only.
            published_promotions = {"a9_zone_pct", "f1_eastward_tz", "b14_n_rookies"}
            features.append({
                "feature": name,
                "gates": gates,
                "promoted": res.get("verdict") == "PROMOTED" and name in published_promotions,
                "failed_at": res.get("failed_at"),
                "min_improvement_pct": round(min(imps), 3) if imps else None,
            })
        features.sort(key=lambda x: (not x["promoted"], -(x["min_improvement_pct"] or -99)))

        # Cross-season re-gauntlet (Phase 9 harness) supersedes the old
        # leaky-harness rows for the features it re-tested.
        regauntlet_path = ROOT / "data" / "regauntlet_results.json"
        if regauntlet_path.exists():
            with open(regauntlet_path, encoding="utf-8") as f:
                rg = json.load(f)
            name_map = {
                "zone_pct": "a9_zone_pct",
                "eastward_tz": "f1_eastward_tz",
                "n_rookies": "b14_n_rookies",
            }
            splits = {s["split"]: s for s in rg.get("splits", [])}
            cross = [s for k, s in splits.items() if k in ("24to25", "25to24")]
            decision = splits.get("2425to26")
            retested = []
            for feat, verdict in rg.get("verdicts", {}).items():
                deltas_pct = []
                for s in cross:
                    fr = s["features"].get(feat)
                    full_b = s["variant_brier"].get("full")
                    if fr and full_b:
                        deltas_pct.append(fr["mean_delta"] / full_b * 100)
                d_ok = None
                if decision and decision["features"].get(feat):
                    d_ok = decision["features"][feat]["helps"]
                retested.append({
                    "feature": name_map.get(feat, feat) + " ↻",
                    "gates": {
                        "gate1": True,
                        "gate2": all(
                            s["features"][feat]["helps"] and s["features"][feat]["significant"]
                            for s in cross if s["features"].get(feat)
                        ),
                        "gate3": None,
                        "gate4": None,
                        "gate5": d_ok,
                    },
                    "promoted": verdict == "KEEP",
                    "failed_at": None,
                    "min_improvement_pct": round(min(deltas_pct), 3) if deltas_pct else None,
                })
            superseded = set(name_map.values())
            features = retested + [f for f in features if f["feature"] not in superseded]

        out["gauntlet"] = {
            "features": features,
            "noise_floor_pct": 0.167,
        }

    return out


def _equal_chunks(ordered: list, n_bins: int) -> list[list]:
    """Split an already-sorted list into n_bins near-equal contiguous chunks."""
    n = len(ordered)
    chunks = []
    start = 0
    for i in range(n_bins):
        end = round((i + 1) * n / n_bins)
        if end > start:
            chunks.append(ordered[start:end])
        start = end
    return chunks


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


# Values a CSV may carry for reconstructed==true. Anything NOT in this set
# and not a clean falsy value is treated as reconstructed: for a
# contamination guard the safe default is to assume contamination.
_TRUTHY = {"1", "1.0", "true", "t", "yes", "y"}
_FALSY = {"0", "0.0", "false", "f", "no", "n", ""}


def _parse_reconstructed(val) -> bool:
    """Fail CLOSED: an unrecognised value counts as reconstructed.

    A reconstructed row was priced retroactively against outcomes that may
    sit inside the training window, so counting one as live silently
    inflates the validation sample. Counting a live row as reconstructed
    only costs us sample size, which is the cheaper mistake.
    """
    s = str(val).strip().lower() if val is not None else ""
    if s in _FALSY:
        return False
    return True


def _line_matched_baseline(backtest: dict | None, lines: list[float]) -> dict:
    """Backtest Brier re-weighted onto the live sample's line mix.

    Brier is not scale-free: a 8.5 line is near-certain (backtest Brier
    0.065) while a 4.5 line is a coin flip (0.209). The flat backtest
    average spans a fixed six-line grid; the live log scores one row per
    pitcher at whatever number the book actually hung, which clusters on
    coin-flips. Comparing the two directly makes a perfectly healthy model
    read "worse than backtest" 100% of the time — the yardstick, not the
    model. So we re-weight the backtest's per-line Brier by the live line
    mix and compare like for like.

    Returns the baseline plus the provenance needed to say so on the page.
    """
    per_line = (backtest or {}).get("per_line") or []
    grid = {round(float(p["line"]), 1): p for p in per_line if p.get("line") is not None}

    matched = [ln for ln in lines if round(ln, 1) in grid]
    unmatched = sorted({ln for ln in lines if round(ln, 1) not in grid})

    if not matched:
        flat = (backtest or {}).get("model_brier")
        return {
            "baseline": round(flat, 4) if flat is not None else BACKTEST_BRIER_BASELINE,
            "baseline_excess": None,
            "basis": "flat",
            "n_matched": 0,
            "n_unmatched": len(lines),
            "unmatched_lines": unmatched,
        }

    model_b = _mean([grid[round(ln, 1)]["model_brier"] for ln in matched])
    naive_b = _mean([grid[round(ln, 1)]["naive_brier"] for ln in matched])
    excesses = [grid[round(ln, 1)].get("model_excess") for ln in matched]
    excesses = [e for e in excesses if e is not None]
    return {
        "baseline": round(model_b, 4),
        "naive_baseline": round(naive_b, 4),
        "baseline_excess": round(_mean(excesses), 4) if excesses else None,
        "basis": "line_matched",
        "n_matched": len(matched),
        "n_unmatched": len(lines) - len(matched),
        "unmatched_lines": unmatched,
    }


def _build_live_model(backtest: dict | None = None) -> dict | None:
    """LIVE model calibration from data/model_log.csv.

    The backtest tells us the model WAS good out-of-sample. This block
    answers the different, more dangerous question: is it still behaving
    that way tonight? One row per evaluated pitcher per slate, unfiltered
    by the bet thresholds — so it measures the model, not the bet
    selection, and accumulates far faster than the picks ledger.

    Two honesty constraints shape the arithmetic here:

    1. The baseline is re-weighted onto the live sample's line mix
       (_line_matched_baseline). A flat backtest average is a rigged
       comparison — see that docstring.
    2. "Scored" rows (those with BOTH a calibrated probability and a
       settled outcome) are counted separately from rows merely present.
       The sample gate and the displayed count both use the scored
       figure, so the page can never advertise 22 observations next to a
       4-row Brier.

    Rows with reconstructed==1 were priced retroactively and their dates
    may sit inside the training window, so they are NOT validation. Live
    rows are preferred; when there are too few, the block still ships but
    carries a sample_warning naming the limitation.

    Contains no P&L values by design (see tools/pnl_guard.py).
    Returns None when the log is missing or empty.
    """
    if not MODEL_LOG_PATH.exists():
        return None

    with open(MODEL_LOG_PATH, encoding="utf-8", newline="") as f:
        raw = list(csv.DictReader(f))
    if not raw:
        return None

    rows = []
    for r in raw:
        rows.append({
            "date": (r.get("date") or "").strip(),
            "pitcher_name": r.get("pitcher_name") or "",
            "reconstructed": _parse_reconstructed(r.get("reconstructed")),
            "line": _safe_float(r.get("line")),
            "p_cal": _safe_float(r.get("p_over_calibrated")),
            "over_hit": _safe_int(r.get("over_hit")),
            "expected_bf": _safe_float(r.get("expected_bf")),
            "actual_bf": _safe_float(r.get("actual_bf")),
            "expected_k": _safe_float(r.get("expected_k")),
            "actual_k": _safe_float(r.get("actual_k")),
        })

    def _is_scored(r: dict) -> bool:
        return r["p_cal"] is not None and r["over_hit"] is not None

    n_total = len(rows)
    live = [r for r in rows if not r["reconstructed"]]
    n_live = len(live)
    n_reconstructed = n_total - n_live
    # The gate that matters is how many rows can actually be SCORED, not
    # how many exist. A row logged without a calibrated probability or
    # without a settled outcome contributes nothing to the Brier.
    n_live_scored = sum(1 for r in live if _is_scored(r))

    if n_live_scored >= MIN_LIVE_ROWS:
        use, row_basis, sample_warning = live, "live", None
    elif n_reconstructed:
        use, row_basis = rows, "live_plus_reconstructed"
        sample_warning = (
            f"Only {n_live_scored} scored live row(s) — under the "
            f"{MIN_LIVE_ROWS} needed to stand alone, so these figures also "
            f"include {n_reconstructed} reconstructed row(s). Reconstructed "
            "slates were priced retroactively and their dates may sit inside "
            "the training window. Read this as a diagnostic, NOT as "
            "validation."
        )
    else:
        use, row_basis = live, "live"
        sample_warning = (
            f"Only {n_live_scored} scored live row(s) — under the "
            f"{MIN_LIVE_ROWS} needed before this comparison means anything. "
            "Read it as a diagnostic, NOT as validation."
        )

    scored = [r for r in use if _is_scored(r)]
    base = _line_matched_baseline(backtest, [r["line"] for r in scored
                                             if r["line"] is not None])
    baseline = base["baseline"]

    if not scored:
        return {
            "n_total": n_total,
            "n_live": n_live,
            "n_live_scored": n_live_scored,
            "n_reconstructed": n_reconstructed,
            "n_used": len(use),
            "n_scored": 0,
            "row_basis": row_basis,
            "sample_warning": sample_warning,
            "backtest_brier_baseline": baseline,
            "baseline_basis": base["basis"],
            "baseline_naive": base.get("naive_baseline"),
            "baseline_lines_matched": base["n_matched"],
            "baseline_lines_unmatched": base["n_unmatched"],
            "backtest_brier_flat": (backtest or {}).get("model_brier"),
            "brier": None,
            "brier_se": None,
            "verdict_band": None,
            "brier_delta": None,
            "brier_floor": None,
            "excess": None,
            "baseline_excess": base.get("baseline_excess"),
            "excess_delta": None,
            "verdict": "unknown",
            "mean_predicted_over": None,
            "actual_over_rate": None,
            "bias": None,
            "calibration_bins": [],
            "workload": None,
            "strikeouts": None,
            "by_date": [],
            "dates": [],
        }

    def _brier(subset: list[dict]) -> float | None:
        scored = [r for r in subset
                  if r["p_cal"] is not None and r["over_hit"] is not None]
        if not scored:
            return None
        return round(
            sum((r["p_cal"] - r["over_hit"]) ** 2 for r in scored) / len(scored), 4)

    brier = _brier(use)
    mean_pred = _mean([r["p_cal"] for r in scored])
    actual_rate = _mean([float(r["over_hit"]) for r in scored])

    # How much would this Brier bounce around on sample size alone? At
    # n=26 the standard error swamps any plausible real drift, so calling
    # a winner off one slate is noise-reading. The band makes the verdict
    # honest about that instead of flipping red on the first bad night.
    #
    # Two standard errors, not one: a 1-SE trigger fires on roughly a
    # third of healthy slates, and an alarm that cries wolf that often is
    # an alarm the operator learns to ignore. 2 SE fires ~5% of the time.
    # As the log grows the SE shrinks, so the band tightens on its own
    # and real drift becomes detectable without touching this constant.
    sq = [(r["p_cal"] - r["over_hit"]) ** 2 for r in scored]
    if len(sq) > 1:
        m = sum(sq) / len(sq)
        var = sum((x - m) ** 2 for x in sq) / (len(sq) - 1)
        se = (var / len(sq)) ** 0.5
    else:
        se = None
    band = round(max(0.005, 2 * se), 4) if se is not None else 0.005

    # The honest scoreboard. Raw Brier is not comparable across samples:
    # the book sets its line where the market is closest to a coin flip,
    # so a live board's irreducible floor sits far above the backtest's
    # fixed six-line grid. Comparing the two directly renders a perfectly
    # healthy model "worse than backtest" 100% of the time.
    #
    # Excess over floor IS comparable. Floor = mean p(1-p), i.e. the Brier
    # a perfectly calibrated model would post at exactly this confidence.
    # Brier minus floor isolates the part that is genuinely model error.
    floor = _mean([r["p_cal"] * (1 - r["p_cal"]) for r in scored])
    excess = round(brier - floor, 4) if brier is not None else None
    baseline_excess = base.get("baseline_excess")

    # Words, not just colour: hue never carries meaning alone.
    if excess is None or baseline_excess is None:
        verdict = "unknown"
    elif excess <= baseline_excess - band:
        verdict = "better than backtest"
    elif excess >= baseline_excess + band:
        verdict = "worse than backtest"
    else:
        verdict = "in line with backtest"

    # Calibration bins — quantile chunks of calibrated P(over), aiming for
    # ~5 rows a bin so a 26-row log does not fake a 10-point curve.
    bins = []
    if scored:
        n_bins = max(1, min(8, len(scored) // 5))
        ordered = sorted(scored, key=lambda r: r["p_cal"])
        for chunk in _equal_chunks(ordered, n_bins):
            bins.append({
                "pred_mean": round(_mean([r["p_cal"] for r in chunk]), 4),
                "actual_rate": round(_mean([float(r["over_hit"]) for r in chunk]), 4),
                "n": len(chunk),
            })

    # The leash: expected vs actual batters faced. This is the failure
    # mode that has actually cost money (AUDIT A-007).
    bf_rows = [r for r in use
               if r["expected_bf"] is not None and r["actual_bf"] is not None]
    workload = None
    if bf_rows:
        errs = [r["actual_bf"] - r["expected_bf"] for r in bf_rows]
        worst = sorted(
            bf_rows, key=lambda r: r["actual_bf"] - r["expected_bf"])[:3]
        workload = {
            "n": len(bf_rows),
            "mean_bf_error": round(_mean(errs), 2),
            "mean_abs_bf_error": round(_mean([abs(e) for e in errs]), 2),
            "pct_within_3": round(
                sum(1 for e in errs if abs(e) <= 3) / len(errs), 4),
            "pct_badly_short": round(
                sum(1 for e in errs if e <= -6) / len(errs), 4),
            "worst_misses": [{
                "date": r["date"],
                "pitcher_name": r["pitcher_name"],
                "expected_bf": round(r["expected_bf"], 1),
                "actual_bf": round(r["actual_bf"], 1),
                "bf_error": round(r["actual_bf"] - r["expected_bf"], 1),
            } for r in worst],
        }

    k_rows = [r for r in use
              if r["expected_k"] is not None and r["actual_k"] is not None]
    strikeouts = None
    if k_rows:
        kerrs = [r["actual_k"] - r["expected_k"] for r in k_rows]
        strikeouts = {
            "n": len(k_rows),
            "mean_k_error": round(_mean(kerrs), 2),
            "mean_abs_k_error": round(_mean([abs(e) for e in kerrs]), 2),
        }

    by_date = []
    for d in sorted({r["date"] for r in use if r["date"]}):
        sub = [r for r in use if r["date"] == d]
        d_bf = [r["actual_bf"] - r["expected_bf"] for r in sub
                if r["expected_bf"] is not None and r["actual_bf"] is not None]
        # ANY reconstructed row taints the date. all() would render a
        # 24%-reconstructed date as LIVE — hiding exactly the
        # contamination this flag exists to disclose.
        n_recon_d = sum(1 for r in sub if r["reconstructed"])
        d_scored = [r for r in sub if _is_scored(r)]
        d_brier = _brier(sub)
        # Per-date excess uses that date's OWN line mix, so a night of
        # coin-flip lines is not penalised against a night of blowouts.
        d_excess = d_base = None
        if d_scored and d_brier is not None:
            d_floor = _mean([r["p_cal"] * (1 - r["p_cal"]) for r in d_scored])
            d_excess = round(d_brier - d_floor, 4)
            d_base = _line_matched_baseline(
                backtest, [r["line"] for r in d_scored if r["line"] is not None]
            ).get("baseline_excess")
        by_date.append({
            "date": d,
            "n": len(sub),
            "n_scored": len(d_scored),
            "brier": d_brier,
            "excess": d_excess,
            "baseline_excess": d_base,
            "mean_abs_bf_error": round(_mean([abs(e) for e in d_bf]), 2) if d_bf else None,
            "reconstructed": n_recon_d > 0,
            "n_reconstructed": n_recon_d,
        })

    return {
        "n_total": n_total,
        "n_live": n_live,
        "n_live_scored": n_live_scored,
        "n_reconstructed": n_reconstructed,
        "n_used": len(use),
        "n_scored": len(scored),
        "row_basis": row_basis,
        "sample_warning": sample_warning,
        "backtest_brier_baseline": baseline,
        "baseline_basis": base["basis"],
        "baseline_naive": base.get("naive_baseline"),
        "baseline_lines_matched": base["n_matched"],
        "baseline_lines_unmatched": base["n_unmatched"],
        "backtest_brier_flat": (backtest or {}).get("model_brier"),
        "brier": brier,
        "brier_se": round(se, 4) if se is not None else None,
        "verdict_band": band,
        "brier_delta": round(brier - baseline, 4) if brier is not None else None,
        "brier_floor": round(floor, 4) if floor is not None else None,
        "excess": excess,
        "baseline_excess": baseline_excess,
        "excess_delta": (round(excess - baseline_excess, 4)
                         if excess is not None and baseline_excess is not None
                         else None),
        "verdict": verdict,
        "mean_predicted_over": round(mean_pred, 4) if mean_pred is not None else None,
        "actual_over_rate": round(actual_rate, 4) if actual_rate is not None else None,
        "bias": round(mean_pred - actual_rate, 4) if mean_pred is not None else None,
        "calibration_bins": bins,
        "workload": workload,
        "strikeouts": strikeouts,
        "by_date": by_date,
        "dates": sorted({r["date"] for r in use if r["date"]}),
    }


def _build_shadow() -> dict | None:
    """Counterfactual portfolio from tools/shadow.py.

    Answers the question the live filter can't: the bar is currently set
    so high that almost nothing qualifies, which deadlocks AUDIT A-006
    (raise trust only after 100+ graded bets) because we never
    accumulate bets. Scoring what WOULD have been bet gathers that
    evidence at ~20 rows a night with no money at risk.

    Every P&L value here is tagged `shadow_flat_100u` and lives under
    the "shadow" key; tools/pnl_guard.py refuses to let those two facts
    come apart in either direction.
    """
    try:
        from tools.shadow import build as build_shadow
        # Reconstructed rows are priced against known outcomes, so they
        # are diagnostic only. Included until enough live rows exist to
        # stand alone, and flagged in the payload either way.
        live = build_shadow(include_reconstructed=False)
        if live["n_observations"] >= MIN_LIVE_ROWS:
            return live
        return build_shadow(include_reconstructed=True)
    except Exception as exc:
        print(f"  (shadow block skipped: {type(exc).__name__}: {exc})")
        return None


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
    model = _build_model_analytics()

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
        "model": model,
        "live_model": _build_live_model(model.get("backtest")),
        "shadow": _build_shadow(),
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
    lm = data.get("live_model")
    if lm:
        print(f"  Live model: scoring {lm['n_scored']} {lm['row_basis']} row(s) "
              f"— log holds {lm['n_live']} live + {lm['n_reconstructed']} reconstructed")
        # Every one of these can legitimately be None (no scored rows,
        # or no line-matched backtest baseline). Formatting None with
        # :+ raises, which crashed the whole dashboard build rather than
        # printing one less pretty summary line.
        def _n(v, plus=False):
            if v is None:
                return "n/a"
            return f"{v:+}" if plus else f"{v}"
        print(f"    Brier {_n(lm['brier'])} vs floor {_n(lm['brier_floor'])} "
              f"-> excess {_n(lm['excess'], True)} vs backtest excess "
              f"{_n(lm['baseline_excess'], True)} "
              f"(band +/-{_n(lm['verdict_band'])}) — {lm['verdict']}")
        if lm.get("sample_warning"):
            print(f"    WARNING: {lm['sample_warning']}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate dashboard data JSON")
    parser.add_argument("--out", metavar="FILE", help="Output path")
    args = parser.parse_args()
    write_dashboard_json(Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
