"""Score the outs model against its own CLOSING lines (Phase 10).

The strikeouts model taught this repo the hard way that skill-vs-naive
says nothing about skill-vs-book (A-041) — and that historical prices
can't be conjured after the fact (A-002). The outs market never had
that gap: closing snapshots have been captured since 2026-08-08,
BEFORE any model priced anything. This scores the shipped model
retroactively over that banked window, honestly:

  * the shipped pkl is trained on 2024+2025 only — it has never seen
    any scored row;
  * every feature row is strictly as-of by construction (the same
    builder training uses);
  * no calibrator is applied because none passed the Gate 5 holdout
    (both candidate maps were refused; serving is raw + clamp);
  * whole-number closing lines are SKIPPED loudly, never folded into
    a side (PUSH is its own outcome).

PRIMARY sample only: posted line, two sides, exact no-vig fair, one
row per start. Same report shape as tools/score_vs_market.py so the
two markets read side by side — and stay separate.

Usage: python tools/score_outs_vs_market.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.calibration import clamp_prob
from models.edge import no_vig_fair_prob
from models.outs_hazard import MODEL_PATH, OutsHazard, load_dataset, p_over
from tools.daily_pipeline import _normalize_name
from tools.score_vs_market import load_closing, paired, brier, slate_index
from tools.outs_serve import calibrate, load_outs_calibrator


def build() -> pd.DataFrame:
    closing = load_closing("closing_outs_2026-*.csv")
    if closing.empty:
        return pd.DataFrame()

    feat = load_dataset(fast_opponent=False, verbose=False)
    feat["year"] = pd.to_datetime(feat["game_date"]).dt.year
    prod = OutsHazard().load(MODEL_PATH)
    if 2026 in set(prod.meta.get("train_seasons", [])):
        raise RuntimeError("shipped pkl trained on 2026 — scored rows "
                           "would not be out-of-sample; refusing")

    keyed = {}
    f26 = feat[feat["year"] == 2026]
    pmf = prod.predict_pmf_frame(f26)
    for i, r in enumerate(f26.itertuples()):
        keyed[(int(r.game_pk), int(r.pitcher))] = (i, int(r.outs))

    cal = load_outs_calibrator()
    rows, skipped_whole = [], 0
    for d, group in closing.groupby("date"):
        idx = slate_index(str(d))   # K slates carry name -> id for the day
        for _, r in group.iterrows():
            p = idx.get(_normalize_name(r["pitcher_name"]))
            if not p:
                continue
            key = (int(p.get("game_pk") or 0), int(p.get("pitcher_id") or 0))
            got = keyed.get(key)
            if got is None:
                continue
            i, actual = got
            try:
                line = float(r["line"])
            except (TypeError, ValueError):
                continue
            if float(line) == int(line):
                skipped_whole += 1
                continue
            try:
                raw = float(p_over(pmf[i][None, :], line)[0])
                nv = no_vig_fair_prob(r["over_odds"], r["under_odds"])
            except (TypeError, ValueError):
                continue
            rows.append({
                "date": str(d),
                "pitcher_id": key[1],
                "game_pk": key[0],
                "line": line,
                "raw": clamp_prob(raw),
                "cal": float(calibrate(raw, cal)),
                "fair": float(nv["fair_over"]),
                "actual_outs": actual,
                "over_hit": float(actual > line),
            })
    if skipped_whole:
        print(f"  ({skipped_whole} whole-number line row(s) skipped — "
              f"PUSH handling is a separate code path)")
    return pd.DataFrame(rows)


SCORECARD_PATH = Path(__file__).parent.parent / "data" / "outs_scorecard.csv"
SCORECARD_FIELDS = ["run_at", "n_starts", "n_dates", "over_rate",
                    "brier_raw", "brier_cal", "brier_market",
                    "z_raw_vs_market", "z_cal_vs_market"]


def _persist(row: dict) -> None:
    import csv as _csv
    import os as _os
    import tempfile as _tempfile
    existing = []
    if SCORECARD_PATH.exists():
        with open(SCORECARD_PATH, encoding="utf-8") as f:
            existing = list(_csv.DictReader(f))
    existing.append(row)
    fd, tmp = _tempfile.mkstemp(dir=SCORECARD_PATH.parent, suffix=".tmp")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=SCORECARD_FIELDS,
                                extrasaction="ignore")
            w.writeheader()
            w.writerows(existing)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, SCORECARD_PATH)
    except Exception:
        if _os.path.exists(tmp):
            _os.unlink(tmp)
        raise


def main() -> int:
    from datetime import datetime, timezone

    df = build()
    if df.empty:
        print("no scoreable closing-outs rows")
        return 1
    y = df["over_hit"].values
    print(f"\n=== OUTS vs CLOSING (posted line, two-sided): {len(df)} starts "
          f"over {df['date'].nunique()} date(s) ===")
    print(f"  base rate (actual over-rate): {y.mean():.4f}")
    for c in ("raw", "cal", "fair"):
        print(f"  Brier {c:<6}: {brier(df[c], y):.4f}")
    print("\n  paired vs MARKET — negative means the model is better:")
    zs = {}
    for c in ("raw", "cal"):
        m, se, z = paired(df[c], df["fair"], y)
        zs[c] = z
        verdict = ("model BETTER" if z < -1.96 else
                   "model WORSE" if z > 1.96 else "indistinguishable")
        print(f"    {c:<6}: {m:+.5f} +/- {se:.5f} (z={z:+.2f})  {verdict}")

    _persist({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_starts": len(df),
        "n_dates": int(df["date"].nunique()),
        "over_rate": round(float(y.mean()), 4),
        "brier_raw": round(brier(df["raw"], y), 5),
        "brier_cal": round(brier(df["cal"], y), 5),
        "brier_market": round(brier(df["fair"], y), 5),
        "z_raw_vs_market": round(float(zs["raw"]), 3),
        "z_cal_vs_market": round(float(zs["cal"]), 3),
    })
    print(f"\n  appended to {SCORECARD_PATH.name} — the outs market's own "
          f"verdict series, separate from the strikeouts scorecard")
    print("\nNOTE: the shipped pkl is 2024+2025-trained; every scored row "
          "is out-of-sample. No calibrator is applied (Gate 5: both maps "
          "refused on the holdout), so cal == raw until one ships. This "
          "window is small — the verdict is provisional and NOTHING "
          "prices off it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
