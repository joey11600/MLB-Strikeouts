"""Weekly market scorecard (A-002 discipline, automated).

The roadmap's standing instruction — "re-run tools/score_vs_market.py
weekly; the verdict is only provisional" — was a human TODO, and A-046
established what happens to evidence that depends on somebody
remembering. This makes the weekly measurement a scheduled artifact:

  - scores the served model against every banked closing line
    (score_vs_market's PRIMARY sample),
  - appends one summary row to data/market_scorecard.csv — an
    append-only time series of the only verdict that decides betting,
  - prints the flag-shadow report (hook mixture / prior season /
    candidate Stage B) so the three shadow clocks are read in the same
    breath,
  - reminds loudly when the sample crosses 1,000 starts — the
    threshold Phase 11 set for the market-based factor screen.

Usage:
    python tools/market_scorecard.py
"""
import csv
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from tracker import DATA_STATE_DIR

SCORECARD_PATH = DATA_STATE_DIR / "market_scorecard.csv"
FIELDS = ["run_at", "n_starts", "n_dates", "over_rate",
          "brier_raw", "brier_served", "brier_blend", "brier_market",
          "z_raw_vs_market", "z_blend_vs_market"]
FACTOR_SCREEN_THRESHOLD = 1000


def run() -> dict | None:
    from tools.score_vs_market import build, paired, brier

    df = build("closing_2026-*.csv", ladder=False)
    if df.empty or df["fair"].isna().all():
        print("no market-scored rows yet")
        return None
    df = df.dropna(subset=["fair"])
    y = df["over_hit"].values

    _, _, z_raw = paired(df["raw"], df["fair"], y)
    _, _, z_blend = paired(df["blend"], df["fair"], y)
    row = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_starts": len(df),
        "n_dates": int(df["date"].nunique()),
        "over_rate": round(float(np.mean(y)), 4),
        "brier_raw": round(brier(df["raw"], y), 5),
        "brier_served": round(brier(df["served"], y), 5),
        "brier_blend": round(brier(df["blend"], y), 5),
        "brier_market": round(brier(df["fair"], y), 5),
        "z_raw_vs_market": round(float(z_raw), 3),
        "z_blend_vs_market": round(float(z_blend), 3),
    }

    existing = []
    if SCORECARD_PATH.exists():
        with open(SCORECARD_PATH, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    existing.append(row)
    SCORECARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SCORECARD_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(existing)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SCORECARD_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print(f"MARKET SCORECARD — {row['n_starts']} starts over "
          f"{row['n_dates']} dates")
    print(f"  Brier: raw {row['brier_raw']}  blend {row['brier_blend']}  "
          f"market {row['brier_market']}")
    print(f"  paired z vs market: raw {row['z_raw_vs_market']:+.2f}  "
          f"blend {row['z_blend_vs_market']:+.2f}  "
          f"(positive = model worse; +/-1.96 significant)")
    print(f"  appended to {SCORECARD_PATH} "
          f"({len(existing)} rows in the series)")

    if row["n_starts"] >= FACTOR_SCREEN_THRESHOLD:
        print(f"\n  *** {row['n_starts']} >= {FACTOR_SCREEN_THRESHOLD} "
              f"market-scored starts: the Phase 11 market-based factor "
              f"screen is now runnable — python tools/market_factor_screen.py")
    else:
        print(f"  ({FACTOR_SCREEN_THRESHOLD - row['n_starts']} starts to the "
              f"Phase 11 factor-screen threshold)")

    print()
    try:
        from tools.flag_shadow_report import main as shadow_main
        shadow_main()
    except Exception as exc:
        print(f"(flag shadow report failed: {exc})")
    return row


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
