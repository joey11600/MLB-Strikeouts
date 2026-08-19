"""Score the model against the CLOSING line — the only benchmark that pays.

A-041's root finding: `data/backtest_predictions.csv` carries no odds at
all. The model has only ever been scored against `naive_p_over` on a
synthetic 3.5-8.5 line grid, so "+3.2% Brier vs naive" was never a claim
about beating a book. This answers the question that was never asked.

It CANNOT be run over the backtest window. Measured 2026-08-16: the
backtest spans 2026-04-11..2026-08-04 and the only strikeout odds inside
it is a single opening snapshot on 08-04. Historical prop lines were
never sourced (A-002, still open), so the largest honest sample is the
closing captures the worker has taken since 2026-08-05.

Two samples, deliberately kept separate:

  PRIMARY   one row per start at the posted line, both sides priced, so
            the no-vig fair probability is exact. Independent rows.
  LADDER    every alternate milestone (K>=3, K>=4, ...) the book posted.
            Far more rows, but they are the SAME START measured at
            several thresholds, so they are heavily correlated and the
            naive standard error would be a lie. Clustered by start.

The model side is reconstructed exactly as production computes it:
k_dist -> raw P(K > line) -> isotonic calibrator -> blend with market at
MODEL_TRUST_WEIGHT. All four stages are scored so it is visible WHERE
any edge appears or disappears.

Usage:
    python tools/score_vs_market.py
    python tools/score_vs_market.py --ladder    # add the alt-line sample
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from models.calibration import (  # noqa: E402
    CALIBRATOR_PATH, USE_CALIBRATOR, IsotonicCalibrator, clamp_prob)
from models.edge import MODEL_TRUST_WEIGHT, no_vig_fair_prob  # noqa: E402
from tools.daily_pipeline import _normalize_name  # noqa: E402
from tracker import DATA_STATE_DIR  # noqa: E402

ODDS_DIR = DATA_STATE_DIR / "odds"
SLATES_DIR = DATA_STATE_DIR / "slates"


def p_over_from_kdist(k_dist: list[float], line: float) -> float | None:
    """P(K > line) from the model's distribution over K.

    Lines are X.5, so "over 5.5" is K >= 6 with no push to worry about.
    A whole-number alt (K >= 6) is the same sum, which is why the ladder
    milestones drop straight in.
    """
    if not k_dist:
        return None
    need = int(math.floor(line)) + 1 if line != int(line) else int(line)
    if need >= len(k_dist):
        return 0.0
    return float(sum(k_dist[need:]))


def load_closing(pattern: str) -> pd.DataFrame:
    """Closing snapshots, reduced to the LAST capture per key.

    The close job runs several times a day, so the file holds every
    snapshot. The closing line is the last one — taking any other row
    scores the model against a price that was still moving.
    """
    files = sorted(glob.glob(str(ODDS_DIR / pattern)))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["cap"] = pd.to_datetime(df["captured_at"], format="mixed", utc=True)
    key = ["date", "pitcher_name"]
    if "milestone" in df.columns:
        key.append("milestone")
    return df.sort_values("cap").groupby(key, as_index=False).last()


def slate_index(date: str) -> dict:
    try:
        payload = json.loads((SLATES_DIR / f"{date}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {_normalize_name(p["pitcher_name"]): p
            for p in payload.get("pitchers", []) if p.get("pitcher_name")}


def actual_k_lookup(dates: set[str]) -> dict:
    """(game_pk, pitcher_id) -> actual K, via the sanctioned path."""
    from tools.dashboard_data import _actual_k_lookup
    return _actual_k_lookup(dates)


def build(pattern: str, ladder: bool) -> pd.DataFrame:
    closing = load_closing(pattern)
    if closing.empty:
        return pd.DataFrame()

    cal = IsotonicCalibrator()
    if CALIBRATOR_PATH.exists():
        cal.load(CALIBRATOR_PATH)

    dates = set(closing["date"].astype(str))
    actual = actual_k_lookup(dates)

    out = []
    for date, group in closing.groupby("date"):
        idx = slate_index(str(date))
        if not idx:
            continue
        for _, r in group.iterrows():
            p = idx.get(_normalize_name(r["pitcher_name"]))
            if not p:
                continue          # model never priced him (role gate)
            gp, pid = int(p.get("game_pk") or 0), int(p.get("pitcher_id") or 0)
            k = actual.get((gp, pid))
            if k is None:
                continue          # no settled result yet

            if ladder:
                line = float(r["milestone"]) - 0.5   # "K>=6" == over 5.5
                fair = None                          # one-sided, see below
            else:
                line = float(r["line"])
                nv = no_vig_fair_prob(r["over_odds"], r["under_odds"])
                fair = nv["fair_over"]

            raw = p_over_from_kdist(p.get("k_dist") or [], line)
            if raw is None:
                continue
            out.append({
                "date": date, "pitcher_id": pid, "game_pk": gp,
                "start_key": f"{date}:{pid}", "line": line,
                "raw": raw, "cal": cal.predict(raw) if cal.is_fitted else raw,
                "fair": fair, "over_hit": float(k > line), "actual_k": k,
                "milestone": r.get("milestone"),
                "odds": r.get("odds"),
                # Carried so the one-sided ladder can borrow this start's
                # measured margin instead of assuming one.
                "total_implied": (None if ladder else nv["total_implied"]),
            })
    df = pd.DataFrame(out)
    if not df.empty:
        # "served" is what production actually ships. Since A-044 the
        # isotonic map is off, so blending "cal" would score a stage the
        # board never sees. raw and cal are still reported side by side
        # -- that comparison is the evidence for the switch.
        df["served"] = (df["cal"] if USE_CALIBRATOR
                        else df["raw"].map(clamp_prob))
    if not df.empty and not ladder:
        df["blend"] = (MODEL_TRUST_WEIGHT * df["served"]
                       + (1 - MODEL_TRUST_WEIGHT) * df["fair"])
    return df


def devig_ladder(df: pd.DataFrame, holds: dict) -> pd.DataFrame:
    """De-vig a one-sided alt ladder using that start's measured hold.

    An alt ladder prices K>=3, K>=4, ... on ONE side only, so there is no
    opposite price to divide the vig out against, and any de-vig is an
    assumption. This makes the assumption explicit and measured:
    proportional de-vig by the total_implied of the SAME start's
    two-sided posted line.

    The tempting alternative -- normalise the implied PMF so it sums to
    1 -- is wrong here, and the code measured its own wrongness before
    this replaced it. Books post the ladder truncated (typically K>=3
    upward), so the differenced PMF sums to about P(K>=3), not 1;
    dividing by it produced a median "overround" of 0.946, i.e. a book
    with NEGATIVE vig, which does not exist. Starts with no two-sided
    quote are dropped rather than guessed at.
    """
    from models.edge import american_to_implied
    rows = []
    for key, g in df.groupby("start_key"):
        total = holds.get(key)
        if not total or total <= 0:
            continue
        g = g.sort_values("line").copy()
        imp = np.array([american_to_implied(o) for o in g["odds"]], float)
        if len(imp) == 0:
            continue
        # The survival curve must be non-increasing in the milestone; a
        # crossed quote would otherwise imply a negative P(K = n).
        imp = np.minimum.accumulate(imp)
        g["fair"] = np.clip(imp / total, 1e-6, 1 - 1e-6)
        g["overround"] = total
        rows.append(g)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def paired(a, b, y, clusters=None) -> tuple[float, float, float]:
    """Paired mean difference in squared error, with optional clustering.

    Clustered because alt-line rows are the same start measured at
    several thresholds: treating them as independent would shrink the
    standard error by roughly sqrt(rows per start) and manufacture
    significance that is not there.
    """
    d = (np.asarray(a, float) - np.asarray(y, float)) ** 2 \
        - (np.asarray(b, float) - np.asarray(y, float)) ** 2
    m = float(d.mean())
    if clusters is None:
        se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
    else:
        means = pd.Series(d).groupby(np.asarray(clusters)).mean().values
        se = float(means.std(ddof=1) / np.sqrt(len(means))) if len(means) > 1 else 0.0
    return m, se, (m / se if se > 0 else 0.0)


def report(df: pd.DataFrame, label: str, cols: list[str], clustered: bool):
    y = df["over_hit"].values
    clusters = df["start_key"].values if clustered else None
    n_starts = df["start_key"].nunique()
    print(f"\n=== {label}: {len(df)} rows over {n_starts} start(s), "
          f"{df['date'].nunique()} date(s) ===")
    print(f"  base rate (actual over-rate): {y.mean():.4f}")
    for c in cols:
        print(f"  Brier {c:<22}: {brier(df[c], y):.4f}")
    if "fair" not in cols:
        return
    print(f"\n  paired vs MARKET{' (clustered by start)' if clustered else ''}"
          f" — negative means the model is better:")
    for c in cols:
        if c == "fair":
            continue
        m, se, z = paired(df[c], df["fair"], y, clusters)
        verdict = ("model BETTER" if z < -1.96 else
                   "model WORSE" if z > 1.96 else "indistinguishable")
        print(f"    {c:<22}: {m:+.5f} +/- {se:.5f} (z={z:+.2f})  {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder", action="store_true",
                    help="also score the alternate-line ladder")
    args = ap.parse_args()

    primary = build("closing_2026-*.csv", ladder=False)
    if primary.empty:
        print("No primary closing rows could be built.")
        return 1
    report(primary, "PRIMARY (posted line, two-sided, independent rows)",
           ["raw", "cal", "served", "blend", "fair"], clustered=False)

    if args.ladder:
        lad = build("closing_alts_2026-*.csv", ladder=True)
        holds = (primary.dropna(subset=["total_implied"])
                 .set_index("start_key")["total_implied"].to_dict())
        if not lad.empty:
            lad = devig_ladder(lad, holds)
        if not lad.empty:
            lad["blend"] = (MODEL_TRUST_WEIGHT * lad["served"]
                            + (1 - MODEL_TRUST_WEIGHT) * lad["fair"])
            med = lad.groupby("start_key")["overround"].first().median()
            print(f"\n  (ladder de-vigged by each start's own two-sided "
                  f"margin; median total_implied {med:.3f} — 1.0 would be a "
                  f"vig-free book. One-sided, so this is an ASSUMPTION.)")
            report(lad, "LADDER (alt milestones, one-sided, CORRELATED)",
                   ["raw", "cal", "served", "blend", "fair"], clustered=True)

    print("\nNOTE: this window is 2026-08-05 onward only. The backtest "
          "(2026-04-11..08-04) cannot be scored this way — historical prop "
          "lines were never sourced (A-002).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
