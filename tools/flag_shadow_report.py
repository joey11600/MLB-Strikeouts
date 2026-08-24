"""Shadow report for the two flag-off models (A-046).

USE_HOOK_MIXTURE (A-042) and USE_PRIOR_SEASON both passed their gates and
were parked behind OFF flags "pending a 2-week shadow" — but until
2026-08-24 nothing logged what they would have predicted, so the shadow
could never conclude. The pipeline now writes two counterfactual columns
into model_log.csv every night (p_over_hookmix, p_over_prior) and a
separate shadow_prior_log.csv for pitchers only the prior-season window
can price. This tool reads that evidence and reports the promotion case.

It REPORTS; it does not flip flags. The promotion decision stays with the
operator, per CLAUDE.md (shadow 2 weeks -> compare -> promote).

Usage:
    python tools/flag_shadow_report.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from tools.model_log import LOG_PATH, SHADOW_PRIOR_LOG_PATH

MIN_SHADOW_DATES = 14


def _paired(a: np.ndarray, b: np.ndarray, y: np.ndarray):
    """Mean difference in squared error (a minus b) with its SE and z.
    Negative mean = a is the better probability."""
    d = (a - y) ** 2 - (b - y) ** 2
    m = float(d.mean())
    se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
    return m, se, (m / se if se > 0 else 0.0)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def report_hookmix(df: pd.DataFrame) -> None:
    print("=" * 74)
    print("HOOK MIXTURE (A-042) — shadow vs production Stage A")
    print("=" * 74)
    d = df.dropna(subset=["p_over_hookmix", "p_over_raw", "over_hit"])
    if d.empty:
        print("  no rows with the shadow column yet — wired 2026-08-24, "
              "check back after the next graded slate\n")
        return
    n_dates = d["date"].nunique()
    y = d["over_hit"].values.astype(float)
    mix = d["p_over_hookmix"].values.astype(float)
    raw = d["p_over_raw"].values.astype(float)
    m, se, z = _paired(mix, raw, y)
    print(f"  rows {len(d)} over {n_dates} date(s)   "
          f"[{MIN_SHADOW_DATES}+ dates required before deciding]")
    print(f"  mean P(over): production {raw.mean():.4f}  mixture {mix.mean():.4f}  "
          f"shift {mix.mean()-raw.mean():+.4f}")
    print(f"    (A-042 predicted the OVER lean drops 1-2 points; much more "
          f"than that is suspicious, not lucky)")
    print(f"  actual over-rate: {y.mean():.4f}")
    print(f"  paired Brier, mixture minus production: {m:+.5f} +/- {se:.5f} "
          f"(z={z:+.2f})  {'mixture BETTER' if z < -1.96 else 'mixture WORSE' if z > 1.96 else 'not significant yet'}")

    # The confident-OVER band A-041 flagged (stated 65%, actual 33%).
    band = d[raw >= 0.60] if (raw >= 0.60).any() else pd.DataFrame()
    if len(band) >= 5:
        by = band["over_hit"].astype(float)
        print(f"  confident-OVER band (raw >= 0.60): n={len(band)}  "
              f"prod says {band['p_over_raw'].astype(float).mean():.3f}  "
              f"mixture says {band['p_over_hookmix'].astype(float).mean():.3f}  "
              f"actual {by.mean():.3f}")
    ready = n_dates >= MIN_SHADOW_DATES
    print(f"  VERDICT: {'evidence window complete — decide' if ready else f'NOT YET ({n_dates}/{MIN_SHADOW_DATES} dates)'}\n")


def report_prior(df: pd.DataFrame) -> None:
    print("=" * 74)
    print("PRIOR-SEASON WINDOW — shadow vs production")
    print("=" * 74)
    d = df.dropna(subset=["p_over_prior", "p_over_raw", "over_hit"])
    if d.empty:
        print("  no rows with the shadow column yet\n")
    else:
        moved = d[(_num(d, "p_over_prior") - _num(d, "p_over_raw")).abs() > 1e-6]
        print(f"  board rows with the column: {len(d)} "
              f"({d['date'].nunique()} dates); rows the feature actually "
              f"moves: {len(moved)}")
        if len(moved) >= 5:
            y = moved["over_hit"].values.astype(float)
            m, se, z = _paired(_num(moved, "p_over_prior").values,
                               _num(moved, "p_over_raw").values, y)
            print(f"  paired Brier on moved rows, prior minus production: "
                  f"{m:+.5f} +/- {se:.5f} (z={z:+.2f})")

    if not SHADOW_PRIOR_LOG_PATH.exists():
        print("  no recovered-start log yet (shadow_prior_log.csv) — the "
              "pipeline writes it when a refused pitcher is priceable "
              "under the prior window\n")
        return
    s = pd.read_csv(SHADOW_PRIOR_LOG_PATH)
    s = s.dropna(subset=["p_over_raw", "over_hit"])
    if s.empty:
        print("  recovered-start log exists but holds no graded rows yet\n")
        return
    n_dates = s["date"].nunique()
    y = s["over_hit"].values.astype(float)
    p = _num(s, "p_over_raw").values
    fair = _num(s, "fair_over").values
    print(f"  RECOVERED starts (production refused, prior window priced): "
          f"{len(s)} over {n_dates} date(s)")
    print(f"  Brier: prior-model {np.mean((p-y)**2):.4f}   "
          f"market fair {np.nanmean((fair-y)**2):.4f}")
    ok = ~np.isnan(fair)
    if ok.sum() > 3:
        m, se, z = _paired(p[ok], fair[ok], y[ok])
        print(f"  paired vs market: {m:+.5f} +/- {se:.5f} (z={z:+.2f})  "
              f"(negative = model better; expect to LOSE to the market — "
              f"the question is whether pricing these starts is safe, not "
              f"whether it beats the book)")
    hi = s[p >= 0.80]
    if len(hi):
        print(f"  0.8-1.0 band (the holdout's weak spot, +9pp there): "
              f"n={len(hi)}  stated {p[p >= 0.80].mean():.3f}  "
              f"actual {hi['over_hit'].astype(float).mean():.3f}")
    ready = n_dates >= MIN_SHADOW_DATES
    print(f"  VERDICT: {'evidence window complete — decide' if ready else f'NOT YET ({n_dates}/{MIN_SHADOW_DATES} dates)'}\n")


def report_candidate(df: pd.DataFrame) -> None:
    print("=" * 74)
    print("CANDIDATE STAGE B (A-049: core + p5_pitches + is_home) — shadow")
    print("=" * 74)
    d = df.dropna(subset=["p_over_candidate", "p_over_raw", "over_hit"])
    if d.empty:
        print("  no rows with the shadow column yet — wired 2026-08-24\n")
        return
    n_dates = d["date"].nunique()
    y = d["over_hit"].values.astype(float)
    cand = _num(d, "p_over_candidate").values
    raw = _num(d, "p_over_raw").values
    m, se, z = _paired(cand, raw, y)
    print(f"  rows {len(d)} over {n_dates} date(s)   "
          f"[{MIN_SHADOW_DATES}+ dates required before deciding]")
    print(f"  paired Brier, candidate minus production: {m:+.5f} +/- {se:.5f} "
          f"(z={z:+.2f})  "
          f"{'candidate BETTER' if z < -1.96 else 'candidate WORSE' if z > 1.96 else 'not significant yet'}")
    fair = _num(d, "fair_over").values
    ok = ~np.isnan(fair)
    if ok.sum() > 10:
        mc, sec, zc = _paired(cand[ok], fair[ok], y[ok])
        mr, ser, zr = _paired(raw[ok], fair[ok], y[ok])
        print(f"  vs market fair: candidate {mc:+.5f} (z={zc:+.2f})   "
              f"production {mr:+.5f} (z={zr:+.2f})")
    ready = n_dates >= MIN_SHADOW_DATES
    print(f"  VERDICT: {'evidence window complete — decide' if ready else f'NOT YET ({n_dates}/{MIN_SHADOW_DATES} dates)'}\n")


def report_re(df: pd.DataFrame) -> None:
    print("=" * 74)
    print("RATE RANDOM EFFECT (A-051: sigma*=0.15, mean-preserving) — shadow")
    print("=" * 74)
    d = df.dropna(subset=["p_over_re", "p_over_raw", "over_hit"])
    if d.empty:
        print("  no rows with the shadow column yet — wired 2026-08-24\n")
        return
    n_dates = d["date"].nunique()
    y = d["over_hit"].values.astype(float)
    re_p = _num(d, "p_over_re").values
    raw = _num(d, "p_over_raw").values
    m, se, z = _paired(re_p, raw, y)
    print(f"  rows {len(d)} over {n_dates} date(s)   "
          f"[{MIN_SHADOW_DATES}+ dates required before deciding]")
    print(f"  paired Brier, RE minus production: {m:+.5f} +/- {se:.5f} "
          f"(z={z:+.2f})")
    print(f"  mean |p-0.5|: production {np.abs(raw-0.5).mean():.4f}  "
          f"RE {np.abs(re_p-0.5).mean():.4f}  (the RE should be LESS "
          f"extreme — that is the point)")
    ready = n_dates >= MIN_SHADOW_DATES
    print(f"  VERDICT: {'evidence window complete — decide' if ready else f'NOT YET ({n_dates}/{MIN_SHADOW_DATES} dates)'}\n")


def main() -> int:
    if not LOG_PATH.exists():
        print("no model log yet")
        return 1
    df = pd.read_csv(LOG_PATH)
    for c in ("p_over_hookmix", "p_over_prior", "p_over_candidate",
              "p_over_re"):
        if c not in df.columns:
            df[c] = np.nan
    live = df[df["reconstructed"] == 0].copy()
    report_hookmix(live)
    report_prior(live)
    report_candidate(live)
    report_re(live)
    print("Promotion rules unchanged (CLAUDE.md): a flag flips only after "
          "the shadow window, on this evidence, by operator decision. "
          "Nothing in this tool writes state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
