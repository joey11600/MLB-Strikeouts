"""Re-gauntlet Stage B candidate features on the honest harness.

Round 1 (2026-08-11) re-tested the Phase 6 promotions (a9_zone_pct,
f1_eastward_tz, b14_n_rookies) and demoted all three; production Stage B
has been core-only since. Round 2 (2026-08-24, A-049) runs the Tier A
audit candidates through the same machinery: strictly as-of features,
per-season priors, train-season-only fits, three splits.

Candidates and why:
  swstr_pct     prior swinging-strike rate — whiff quality stabilizes
                far faster than K%, aimed at the low-line / low-history
                population where the model measurably loses to the
                market (audit: line <= 4.5 deficit z=+3.10)
  p5_pitches    mean pitch count over last 5 starts (workload ramp)
  velo_trend    last start's FB velo vs the pitcher's own baseline
  is_home       home/away (the one factor of 68 with signal vs the LINE)
  opp_zcontact  opponent team in-zone contact rate, prior-day
  (csw_pct is kept OUT of the drop-one set: measured r with swstr_pct
   is 0.62, and a correlated pair inside FULL splits its shared signal
   so the drop-delta demotes both even when the pair helps. Instead a
   SWAP variant — csw in swstr's seat — answers the only question that
   matters for the pair: which one earns the seat.)

Design — drop-one marginal value:
  Variants: FULL (all candidates), BASE (none), FULL minus each.
  Every variant scores the identical test starts (complete-case on all
  candidate columns), so each feature's value is a PAIRED per-start
  Brier delta:

      delta_i = brier_i(drop-F) - brier_i(full)     (mean across lines)

  delta > 0 means removing F hurts, i.e. F carries real signal.

Verdict per feature (repo bar: both temporal directions must help):
  KEEP    mean delta > 0 with t >= 2 in BOTH cross-season directions
  DEMOTE  otherwise (marginal features don't ride)

A KEEP here does NOT ship: it earns a serve-time implementation and a
2-week shadow (A-046 infrastructure), then the operator decides.

Writes data/regauntlet_results.json. Runtime ~45-70 min.

Usage: python tools/regauntlet.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import SEASONS, SPLITS, _build_test_set, LINES
from models.stage_a_bf import StageA, prepare_training_data as prepare_stage_a
from models.stage_b_rate import (
    StageB, prepare_training_data as prepare_stage_b,
)
from models.compound import compound_k_distribution, prob_k_geq

RESULTS_PATH = Path(__file__).parent.parent / "data" / "regauntlet_results.json"

# Round-2 candidate set (A-049). The demoted trio stays out; csw_pct is
# excluded for collinearity with swstr_pct (see module docstring).
CANDIDATES = ["swstr_pct", "p5_pitches", "velo_trend", "is_home",
              "opp_zcontact"]

# Test-row column per feature (asof table naming). csw_pct rides along
# for the swap variant even though it is not in CANDIDATES.
ROW_COLS = {
    "swstr_pct": "asof_swstr_pct",
    "csw_pct": "asof_csw_pct",
    "p5_pitches": "p5_pitches",
    "velo_trend": "velo_trend",
    "is_home": "is_home",
    "opp_zcontact": "opp_zcontact",
}

VARIANTS = {
    "full": list(CANDIDATES),
    "base": [],
    **{f"drop_{f}": [g for g in CANDIDATES if g != f] for f in CANDIDATES},
    # csw_pct in swstr_pct's seat (see docstring). Reported, not verdicted.
    "swap_csw": ["csw_pct"] + [f for f in CANDIDATES if f != "swstr_pct"],
}


def _predict_start_brier(stage_a: StageA, stage_b: StageB, row,
                         lineup_kpcts: list[float]) -> float:
    """Mean Brier across LINES for one start, one Stage B variant."""
    features = {
        "a3_season_k_pct_shrunk": row.asof_k_pct_shrunk,
        "c1_bf_mean": row.asof_bf_mean,
        "c10_il_return": False,
        "c11_pitch_limit": None,
        "c12_bp_heavy": False,
    }
    bf_dist = stage_a.predict_bf_distribution(features)
    extras = {name: getattr(row, col) for name, col in ROW_COLS.items()}
    per_batter = stage_b.predict_per_batter_k_prob(
        row.asof_k_pct_shrunk, lineup_kpcts, n_max=40,
        zone_pct=row.zone_pct, eastward_tz=row.eastward_tz,
        n_rookies=row.n_rookies, extras=extras,
    )
    k_dist = compound_k_distribution(bf_dist, per_batter)

    total = 0.0
    for line in LINES:
        p = prob_k_geq(k_dist, line)
        actual = 1.0 if row.actual_k >= np.ceil(line) else 0.0
        total += (p - actual) ** 2
    return total / len(LINES)


def run_split(split_name: str) -> dict:
    cfg = SPLITS[split_name]
    train_label = "+".join(str(y) for y in cfg["train"])
    print(f"\n{'#' * 72}")
    print(f"SPLIT {split_name}: train {train_label} -> test {cfg['test']}")
    print(f"{'#' * 72}")

    print("Preparing training data (as-of, per-season)...")
    train_a = pd.concat(
        [prepare_stage_a(*SEASONS[y]) for y in cfg["train"]], ignore_index=True
    )
    train_b = pd.concat(
        [prepare_stage_b(*SEASONS[y]) for y in cfg["train"]], ignore_index=True
    )
    print(f"  Stage A rows: {len(train_a)}, Stage B rows: {len(train_b)}")

    stage_a = StageA()
    stage_a.fit(train_a)

    print("Building test set...")
    test_df, lineup_dict = _build_test_set(cfg["test"])
    n_all = len(test_df)
    # Complete-case on every candidate column: the drop-one design needs
    # every variant scored on IDENTICAL rows, and a row whose missing
    # value fell to a fill constant would grade the feature on data it
    # never saw.
    for col in ROW_COLS.values():
        test_df = test_df[pd.to_numeric(
            test_df[col], errors="coerce").notna()]
    test_df = test_df.copy()
    print(f"  {len(test_df)} test starts "
          f"({n_all - len(test_df)} dropped as incomplete-case)")

    variant_models = {}
    variant_coefs = {}
    for vname, extras in VARIANTS.items():
        sb = StageB(extra_features=extras)
        sb.fit(train_b)
        variant_models[vname] = sb
        variant_coefs[vname] = dict(zip(sb.feature_names, [round(float(c), 4) for c in sb.coefficients]))

    print("Scoring variants on identical test starts...")
    briers = {vname: np.zeros(len(test_df)) for vname in VARIANTS}
    rows = list(test_df.itertuples())
    for i, row in enumerate(rows):
        if i % 1000 == 0:
            print(f"  {i}/{len(rows)}...", flush=True)
        lineup = lineup_dict.get((row.game_pk, row.pitcher), [0.225] * 9)
        for vname, sb in variant_models.items():
            briers[vname][i] = _predict_start_brier(stage_a, sb, row, lineup)

    out = {
        "split": split_name,
        "n_starts": len(test_df),
        "variant_brier": {v: round(float(b.mean()), 5) for v, b in briers.items()},
        "full_coefficients": variant_coefs["full"],
        "features": {},
    }

    print(f"\n  {'variant':>18} {'Brier':>9}")
    for vname, b in briers.items():
        print(f"  {vname:>18} {b.mean():>9.5f}")

    for feat in CANDIDATES:
        drop_name = f"drop_{feat}"
        delta = briers[drop_name] - briers["full"]  # >0 => dropping hurts
        mean = float(delta.mean())
        se = float(delta.std(ddof=1) / np.sqrt(len(delta)))
        t = mean / se if se > 0 else 0.0
        out["features"][feat] = {
            "mean_delta": round(mean, 7),
            "se": round(se, 7),
            "t": round(t, 2),
            "helps": bool(mean > 0),
            "significant": bool(t >= 2.0),
        }
        print(f"  {feat:>18}: drop-delta {mean:+.6f} (t={t:+.2f}) "
              f"{'HELPS' if mean > 0 else 'no value'}")

    base_delta = briers["base"] - briers["full"]
    bmean = float(base_delta.mean())
    bse = float(base_delta.std(ddof=1) / np.sqrt(len(base_delta)))
    out["candidates_vs_base"] = {
        "mean_delta": round(bmean, 7),
        "t": round(bmean / bse, 2) if bse > 0 else 0.0,
    }

    return out


def main():
    split_results = [run_split(name) for name in SPLITS]

    cross_dirs = [r for r in split_results if r["split"] in ("24to25", "25to24")]
    verdicts = {}
    for feat in CANDIDATES:
        keep = all(
            r["features"][feat]["helps"] and r["features"][feat]["significant"]
            for r in cross_dirs
        )
        verdicts[feat] = "KEEP" if keep else "DEMOTE"

    print(f"\n{'=' * 72}")
    print("RE-GAUNTLET VERDICTS (bar: helps with t>=2 in BOTH cross-directions)")
    print(f"{'=' * 72}")
    for feat, v in verdicts.items():
        per_split = "  ".join(
            f"{r['split']}: {r['features'][feat]['mean_delta']:+.6f} (t={r['features'][feat]['t']:+.1f})"
            for r in split_results
        )
        print(f"  {feat:>14} -> {v:6}  {per_split}")

    payload = {
        "harness": "cross-season as-of drop-one (Phase 9)",
        "bar": "drop-delta > 0 with t >= 2 in both cross-season directions",
        "verdicts": verdicts,
        "splits": split_results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"\nResults saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
