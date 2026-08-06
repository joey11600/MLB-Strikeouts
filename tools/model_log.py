"""Prediction-vs-outcome log for EVERY evaluated pitcher.

Why this exists
---------------
Each slate produces ~28 model predictions but only ~3 bets. P&L and CLV
can only ever evaluate the bets, so at 3 bets/night it takes months to
say anything about the model. The other ~25 predictions are equally
testable — we just never wrote the answers down.

Recording all of them turns one night into ~28 observations instead of
3, a ~9x faster feedback loop, and it measures the MODEL rather than
the bet selection (which is filtered by thresholds and therefore a
biased sample of the model's opinions).

It also watches the failure mode that has actually cost money: the
leash. `expected_bf` vs `actual_bf` is logged per start, so a
systematically wrong workload assumption shows up in aggregate long
before it prices a bet (see AUDIT A-007 — a reliever priced as a
21-BF starter).

Append-only and idempotent: re-running a date replaces that date's
rows rather than duplicating them.

Usage:
    python tools/model_log.py                # log all completed slates
    python tools/model_log.py 2026-08-05     # one date
    python tools/model_log.py --report       # live calibration report
"""
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from tracker import DATA_STATE_DIR

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SLATES_DIR = DATA_STATE_DIR / "slates"
LOG_PATH = DATA_STATE_DIR / "model_log.csv"

FIELDS = [
    "date", "game_pk", "pitcher_id", "pitcher_name", "pitcher_team",
    "opponent_team", "line", "over_odds", "under_odds", "lineup_source",
    "expected_bf", "expected_k",
    "p_over_raw", "p_over_calibrated", "blended_prob_over", "fair_over",
    "best_side", "edge_best", "threshold", "strength",
    "units_risked",
    "actual_bf", "actual_k", "over_hit",
    "bf_error", "k_error",
    "reconstructed", "logged_at",
]


def _write_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _actuals_for(dates: set[str]) -> dict:
    """{(game_pk, pitcher_id): (actual_bf, actual_k)} from Statcast."""
    from data.backfill_statcast import load_cached

    lo = min(date.fromisoformat(d) for d in dates)
    hi = max(date.fromisoformat(d) for d in dates)
    df = load_cached(lo, hi)
    if df.empty:
        return {}
    completed = df[df["events"].notna()]
    is_k = completed["events"].isin(["strikeout", "strikeout_double_play"])
    grp = completed.assign(k=is_k.astype(int)).groupby(["game_pk", "pitcher"])
    out = {}
    for (gpk, pid), g in grp:
        out[(int(gpk), int(pid))] = (int(len(g)), int(g["k"].sum()))
    return out


def log_dates(targets: list[str] | None = None) -> int:
    if not SLATES_DIR.exists():
        print("no slates directory")
        return 0

    available = sorted(p.stem for p in SLATES_DIR.glob("*.json"))
    dates = [d for d in available if not targets or d in targets]
    if not dates:
        print("no matching slates")
        return 0

    actuals = _actuals_for(set(dates))
    if not actuals:
        print("no Statcast data for those dates yet — nothing to log")
        return 0

    existing = []
    if LOG_PATH.exists():
        with open(LOG_PATH, encoding="utf-8") as f:
            existing = [r for r in csv.DictReader(f) if r["date"] not in dates]

    now = datetime.now(UTC).isoformat()
    rows = []
    for d in dates:
        with open(SLATES_DIR / f"{d}.json", encoding="utf-8") as f:
            slate = json.load(f)
        recon = bool(slate.get("reconstructed"))
        logged = 0
        for p in slate.get("pitchers", []):
            gpk, pid = p.get("game_pk"), p.get("pitcher_id")
            if gpk is None or pid is None:
                continue
            got = actuals.get((int(gpk), int(pid)))
            if got is None:
                continue  # didn't pitch, or data not in yet
            abf, ak = got
            try:
                line = float(p.get("line"))
            except (TypeError, ValueError):
                continue
            ebf = p.get("expected_bf")
            ek = p.get("expected_k")
            rows.append({
                "date": d,
                "game_pk": gpk,
                "pitcher_id": pid,
                "pitcher_name": p.get("pitcher_name"),
                "pitcher_team": p.get("pitcher_team"),
                "opponent_team": p.get("opponent_team"),
                "line": line,
                "over_odds": p.get("over_odds"),
                "under_odds": p.get("under_odds"),
                "lineup_source": p.get("lineup_source"),
                "expected_bf": ebf,
                "expected_k": ek,
                "p_over_raw": p.get("p_over_raw"),
                "p_over_calibrated": p.get("p_over_calibrated"),
                "blended_prob_over": p.get("blended_prob_over"),
                "fair_over": p.get("fair_over"),
                "best_side": p.get("best_side"),
                "edge_best": p.get("edge_best"),
                "threshold": p.get("threshold"),
                "strength": p.get("strength"),
                "units_risked": (p.get("pick") or {}).get("units_risked",
                                                          p.get("primary_units_risked", 0)),
                "actual_bf": abf,
                "actual_k": ak,
                "over_hit": 1 if ak > line else 0,
                "bf_error": round(abf - ebf, 2) if ebf is not None else "",
                "k_error": round(ak - ek, 2) if ek is not None else "",
                "reconstructed": int(recon),
                "logged_at": now,
            })
            logged += 1
        print(f"  {d}: logged {logged} pitchers"
              + ("  (reconstructed slate)" if recon else ""))

    all_rows = existing + rows
    all_rows.sort(key=lambda r: (r["date"], str(r["pitcher_name"])))
    _write_atomic(LOG_PATH, all_rows)
    print(f"\nmodel log now holds {len(all_rows)} prediction/outcome pairs -> {LOG_PATH}")
    return len(rows)


def report() -> None:
    if not LOG_PATH.exists():
        print("no model log yet — run: python tools/model_log.py")
        return
    df = pd.read_csv(LOG_PATH)
    live = df[df["reconstructed"] == 0]
    print(f"MODEL LOG: {len(df)} pairs ({len(live)} from live slates, "
          f"{len(df)-len(live)} reconstructed)")
    if df.empty:
        return

    use = live if len(live) >= 20 else df
    if use is live:
        tag = "live slates — genuinely prospective predictions"
    else:
        tag = ("INCLUDES RECONSTRUCTED ROWS — those were priced retroactively "
               "and their dates may sit inside the training window, so treat "
               "these as diagnostic only, NOT as model validation")
    print(f"\nUsing {len(use)} rows\n  {tag}\n")

    p = use["p_over_calibrated"].astype(float)
    a = use["over_hit"].astype(int)
    brier = float(((p - a) ** 2).mean())
    print(f"  calibrated P(over) mean {p.mean():.3f} vs actual over-rate {a.mean():.3f}"
          f"   (bias {p.mean()-a.mean():+.3f})")
    print(f"  live Brier: {brier:.4f}   [backtest baseline 0.1491]")

    # The leash is where the model actually breaks — watch it explicitly.
    bf = use.dropna(subset=["expected_bf", "actual_bf"])
    if len(bf):
        err = bf["actual_bf"].astype(float) - bf["expected_bf"].astype(float)
        print(f"\n  workload (batters faced):")
        print(f"    mean error {err.mean():+.2f} BF   mean |error| {err.abs().mean():.2f}")
        print(f"    within +/-3 BF: {(err.abs()<=3).mean():.0%}   "
              f"badly short (<= -6): {(err<=-6).mean():.0%}")
        worst = bf.assign(e=err).nsmallest(3, "e")
        for r in worst.itertuples():
            print(f"      {r.pitcher_name:<20} expected {r.expected_bf:.1f} BF, "
                  f"faced {r.actual_bf}  ({r.e:+.1f})")

    ke = use.dropna(subset=["expected_k", "actual_k"])
    if len(ke):
        kerr = ke["actual_k"].astype(float) - ke["expected_k"].astype(float)
        print(f"\n  strikeouts: mean error {kerr.mean():+.2f} K   "
              f"mean |error| {kerr.abs().mean():.2f}")


def main():
    args = [a for a in sys.argv[1:]]
    if "--report" in args:
        report()
        return
    log_dates(args or None)


if __name__ == "__main__":
    main()
