"""Fit the CANDIDATE Stage B — core + the A-049 re-gauntlet keepers.

The 2026-08-24 cross-season re-gauntlet produced the first KEEP
verdicts in the repo's history:

    p5_pitches  drop-delta t = +3.4 / +7.3 / +3.4   (all three splits)
    is_home     drop-delta t = +2.7 / +2.9 / +0.5   (both cross-dirs)

Per CLAUDE.md a KEEP does not ship: it earns a 2-week shadow. This fits
core + [p5_pitches, is_home] on all cached seasons and saves it to
models/stage_b_candidate.pkl — a SEPARATE file the pipeline loads only
to log the nightly p_over_candidate shadow column (A-046 pattern).
Production pickles are never touched here.

Usage: python tools/fit_candidate_stage_b.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import SEASONS
from models.stage_b_rate import StageB, prepare_training_data

CANDIDATE_EXTRAS = ["p5_pitches", "is_home"]
CANDIDATE_PATH = Path(__file__).parent.parent / "models" / "stage_b_candidate.pkl"


def main():
    years = sorted(SEASONS)
    print(f"Fitting CANDIDATE Stage B (core + {CANDIDATE_EXTRAS}) on "
          f"{'+'.join(str(y) for y in years)}")
    frames = []
    for y in years:
        f = prepare_training_data(*SEASONS[y])
        print(f"  {y}: {len(f)} rows")
        frames.append(f)
    train = pd.concat(frames, ignore_index=True)
    sb = StageB(extra_features=CANDIDATE_EXTRAS)
    sb.fit(train)
    sb.save(CANDIDATE_PATH)
    print(f"\nCandidate saved to {CANDIDATE_PATH} — production pickles untouched.")
    print("The pipeline logs its p_over_candidate shadow nightly; decide "
          "after 14 dates via tools/flag_shadow_report.py.")


if __name__ == "__main__":
    main()
