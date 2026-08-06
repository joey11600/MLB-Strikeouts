"""How much of the error is even reducible? Model vs the pre-game ceiling.

Before spending effort on a stage, ask what a PERFECT pre-game model
could achieve there. Batters faced and strikeouts both split into:

  between-pitcher variance  — predictable from who is pitching
  within-pitcher variance   — game-level noise (shelled in the 3rd, a
                              rain delay, a manager's hunch), which no
                              amount of pre-game information can reach

A model sitting on the within-pitcher floor is finished. Effort spent
there buys nothing, however unimpressive the raw error looks.

This was written after "the leash is the weak link, mean |error| 2.60
BF" turned out to be wrong: the ceiling is 2.71 BF, so the leash was
already better than a perfect model would look on a small sample. The
same check on strikeouts came back at 92% of ceiling. Both stages are
essentially done, which redirects the question entirely — see AUDIT
A-018.

Usage:
    python tools/ceiling.py                  # 2026 season
    python tools/ceiling.py --start 2026-03-26 --end 2026-08-05
    python tools/ceiling.py --min-starts 5
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# mean|X| for X ~ N(0, sd) is sd * sqrt(2/pi)
_MEAN_ABS = math.sqrt(2.0 / math.pi)


def decompose(df, col: str, min_starts: int) -> dict:
    n = df.groupby("pitcher")[col].count()
    keep = n[n >= min_starts].index
    d = df[df["pitcher"].isin(keep)]
    if d.empty:
        return {}
    total = d[col].var(ddof=1)
    between = d.groupby("pitcher")[col].mean().var(ddof=1)
    dev = d[col] - d.groupby("pitcher")[col].transform("mean")
    within = dev.var(ddof=1)
    sd = math.sqrt(within)
    nd = NormalDist(0, sd)
    return {
        "n_starts": len(d),
        "n_pitchers": len(keep),
        "total_var": total,
        "between_var": between,
        "within_var": within,
        "floor_sd": sd,
        "floor_mean_abs": sd * _MEAN_ABS,
        "floor_within_3": nd.cdf(3) - nd.cdf(-3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-03-26")
    ap.add_argument("--end", default=None)
    ap.add_argument("--min-starts", type=int, default=5)
    args = ap.parse_args()

    from models.stage_a_bf import prepare_training_data

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    print(f"Building as-of table {start} .. {end} ...", flush=True)
    df = prepare_training_data(start, end)
    if df.empty:
        print("no rows")
        return 1

    print("=" * 70)
    print("PRE-GAME CEILING — what a perfect model could achieve")
    print("=" * 70)
    for col, unit in (("actual_bf", "BF"), ("actual_k", "K")):
        s = decompose(df, col, args.min_starts)
        if not s:
            continue
        label = "LEASH (batters faced)" if col == "actual_bf" else "STRIKEOUTS"
        print(f"\n{label} — {s['n_starts']} starts, {s['n_pitchers']} pitchers")
        print(f"  total variance      {s['total_var']:.2f} "
              f"(SD {math.sqrt(s['total_var']):.2f} {unit})")
        print(f"  between-pitcher     {s['between_var']:.2f}   <- reachable pre-game")
        print(f"  within-pitcher      {s['within_var']:.2f}   <- irreducible noise")
        print(f"  PERFECT model still misses: SD {s['floor_sd']:.2f} {unit}, "
              f"mean |error| {s['floor_mean_abs']:.2f} {unit}, "
              f"within +/-3 {s['floor_within_3']:.0%}")
    print()
    print("Compare a stage's live mean |error| against its floor before")
    print("investing in it. A stage at its floor cannot be improved with")
    print("pre-game information, no matter how large the raw error looks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
