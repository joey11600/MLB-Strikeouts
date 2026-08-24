"""Fit the outs model's calibrator — the Gate 5 blocker (Phase 10).

Gate 5's recorded failure: ECE 0.017-0.026 with single-bin gaps to
5.1pp against a measured break-even requirement of ~3.6pp per side —
an edge filter would fire on calibration error as often as on edge.

Design, shaped by the strikeouts model's scars:

  * FIT on pooled cross-season OOS predictions only: S1 (fit 2024,
    predict 2025) + S2 (fit 2025, predict 2024). No 2026 row is ever
    fit on, so 2026 stays an untouched holdout AND the retro market
    scorecard window stays clean.
  * VALIDATE on 2026 via the shipped production pkl (trained on
    2024+2025 — it has never seen 2026 either).
  * TWO candidate maps, judged on the holdout: Platt (2 parameters —
    a univariate map that cannot overfit bins) and isotonic (the
    map that measured WORSE than raw for the K model, A-044 — it must
    EARN its way past Platt here, not be assumed).
  * SHIP only if the winner improves holdout ECE and shrinks the max
    single-bin gap vs raw. Otherwise exit 1 and write nothing — a
    calibrator that doesn't calibrate is A-044 again.
  * Output clamped to [PROB_EPS, 1-PROB_EPS] (A-043: a served 1.0000
    is an impossible assertion).

Usage: python tools/fit_outs_calibrator.py
"""
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.calibration import PROB_EPS
from models.outs_hazard import (
    MARKET_LINES, MODEL_PATH, OutsHazard, ece, load_dataset, p_over)

CALIBRATOR_PATH = Path(__file__).parent.parent / "models" / "outs_calibrator.pkl"
OOS_SPLITS = [((2024,), 2025), ((2025,), 2024)]
N_BINS = 10


OOS_CACHE = Path(__file__).parent.parent / "data" / "_outs_cal_oos.parquet"


def _collect(model: OutsHazard, test: pd.DataFrame):
    pmf = model.predict_pmf_frame(test)
    outs = test["outs"].to_numpy(int)
    ps, ys, ls = [], [], []
    for L in MARKET_LINES:
        ps.append(p_over(pmf, L))
        ys.append((outs > L).astype(float))
        ls.append(np.full(len(outs), L))
    return (np.concatenate(ps), np.concatenate(ys), np.concatenate(ls))


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _fit_platt(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    from scipy.optimize import minimize
    x = _logit(p)

    def nll(t):
        z = np.clip(t[0] + t[1] * x, -25, 25)
        return float(np.logaddexp(0, z).sum() - y @ z)

    r = minimize(nll, [0.0, 1.0], method="L-BFGS-B")
    return float(r.x[0]), float(r.x[1])


def _apply_platt(a: float, b: float, p: np.ndarray) -> np.ndarray:
    z = np.clip(a + b * _logit(np.asarray(p, float)), -25, 25)
    return np.clip(1.0 / (1.0 + np.exp(-z)), PROB_EPS, 1 - PROB_EPS)


def _fit_isotonic(p: np.ndarray, y: np.ndarray, n_bins: int = 50):
    """Equal-count binned PAV — same construction as models/calibration."""
    order = np.argsort(p)
    ps, ys = p[order], y[order]
    edges = np.linspace(0, len(ps), n_bins + 1).astype(int)
    bx, by, bw = [], [], []
    for i in range(n_bins):
        s = slice(edges[i], edges[i + 1])
        if edges[i + 1] > edges[i]:
            bx.append(float(ps[s].mean()))
            by.append(float(ys[s].mean()))
            bw.append(float(edges[i + 1] - edges[i]))
    # pool adjacent violators
    x_, y_, w_ = list(bx), list(by), list(bw)
    i = 0
    while i < len(y_) - 1:
        if y_[i] > y_[i + 1]:
            tot = w_[i] + w_[i + 1]
            y_[i] = (y_[i] * w_[i] + y_[i + 1] * w_[i + 1]) / tot
            x_[i] = (x_[i] * w_[i] + x_[i + 1] * w_[i + 1]) / tot
            w_[i] = tot
            del y_[i + 1], x_[i + 1], w_[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    return np.array(x_), np.array(y_)


def _apply_isotonic(xk: np.ndarray, yk: np.ndarray, p: np.ndarray) -> np.ndarray:
    out = np.interp(np.asarray(p, float), xk, yk)
    return np.clip(out, PROB_EPS, 1 - PROB_EPS)


def _max_bin_gap(p: np.ndarray, y: np.ndarray, n_bins: int = N_BINS) -> float:
    order = np.argsort(p)
    gaps = []
    for cp, cy in zip(np.array_split(p[order], n_bins),
                      np.array_split(y[order], n_bins)):
        if len(cp):
            gaps.append(abs(float(cp.mean()) - float(cy.mean())))
    return max(gaps)


def _per_line_metrics(p: np.ndarray, y: np.ndarray, lines: np.ndarray) -> dict:
    """The Gate 5 quantities PER LINE — pooling across lines lets one
    line's over-bias cancel another's and hides exactly the structure
    the gate exists to catch (measured: pooled ECE 0.0092 while line
    15.5 alone carried a 7.4pp bin gap)."""
    per = {}
    for L in MARKET_LINES:
        m = lines == L
        per[L] = {"ece": float(ece(p[m], y[m], N_BINS)),
                  "max_bin_gap": _max_bin_gap(p[m], y[m])}
    return {
        "mean_line_ece": round(float(np.mean([v["ece"] for v in per.values()])), 6),
        "worst_line_gap": round(max(v["max_bin_gap"] for v in per.values()), 6),
        "per_line": {str(k): {kk: round(vv, 6) for kk, vv in v.items()}
                     for k, v in per.items()},
    }


def _oos_sample(feat: pd.DataFrame) -> pd.DataFrame:
    """Pooled cross-season OOS predictions, cached — the two full fits
    cost minutes and the sample is deterministic given the dataset."""
    if OOS_CACHE.exists():
        cached = pd.read_parquet(OOS_CACHE)
        if len(cached):
            print(f"OOS sample from cache: {len(cached):,} pairs "
                  f"({OOS_CACHE.name}; delete to refit)")
            return cached
    parts = []
    for train_years, test_year in OOS_SPLITS:
        train = feat[feat["year"].isin(train_years)]
        test = feat[feat["year"] == test_year]
        print(f"\nOOS split: fit {train_years} -> predict {test_year} "
              f"({len(train)} / {len(test)} starts)")
        m = OutsHazard()
        m.fit(train, test, verbose=False)
        p, y, ls = _collect(m, test)
        parts.append(pd.DataFrame({"p": p, "y": y, "line": ls,
                                   "split": f"{train_years}->{test_year}"}))
    out = pd.concat(parts, ignore_index=True)
    OOS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OOS_CACHE)
    return out


def main() -> int:
    feat = load_dataset(fast_opponent=False, verbose=True)
    feat["year"] = pd.to_datetime(feat["game_date"]).dt.year

    oos = _oos_sample(feat)
    p_fit = oos["p"].to_numpy(float)
    y_fit = oos["y"].to_numpy(float)
    l_fit = oos["line"].to_numpy(float)
    print(f"\npooled OOS calibration sample: {len(p_fit):,} "
          f"(start x line) pairs, base rate {y_fit.mean():.4f}")

    a, b = _fit_platt(p_fit, y_fit)
    xk, yk = _fit_isotonic(p_fit, y_fit)
    per_line_ab = {}
    for L in MARKET_LINES:
        m = l_fit == L
        per_line_ab[L] = _fit_platt(p_fit[m], y_fit[m])
    print(f"Platt: a={a:+.4f} b={b:+.4f}   isotonic: {len(xk)} knots   "
          f"per-line Platt: {len(per_line_ab)} maps")

    # ---- untouched holdout: 2026 via the shipped production pkl ------
    prod = OutsHazard().load(MODEL_PATH)
    trained_years = set(prod.meta.get("train_seasons", []))
    if 2026 in trained_years:
        raise RuntimeError(
            f"shipped pkl was trained on {trained_years} — 2026 is not a "
            f"holdout for it; refusing to validate on contaminated data")
    hold = feat[feat["year"] == 2026]
    p_h, y_h, l_h = _collect(prod, hold)

    def _apply_per_line(p, lines):
        out = np.empty_like(p)
        for L, (aa, bb) in per_line_ab.items():
            m = lines == L
            out[m] = _apply_platt(aa, bb, p[m])
        return out

    candidates = {
        "raw": np.clip(p_h, PROB_EPS, 1 - PROB_EPS),
        "platt": _apply_platt(a, b, p_h),
        "isotonic": _apply_isotonic(xk, yk, p_h),
        "platt_per_line": _apply_per_line(p_h, l_h),
    }
    report = {}
    for name, ph in candidates.items():
        report[name] = {
            "brier": round(float(np.mean((ph - y_h) ** 2)), 6),
            "pooled_ece": round(float(ece(ph, y_h, N_BINS)), 6),
            **_per_line_metrics(ph, y_h, l_h),
        }
        print(f"  holdout 2026 {name:<15} Brier {report[name]['brier']:.5f}  "
              f"pooledECE {report[name]['pooled_ece']:.4f}  "
              f"mean line ECE {report[name]['mean_line_ece']:.4f}  "
              f"worst line gap {report[name]['worst_line_gap']:.4f}")

    # The gate judges the PER-LINE quantities (what an edge filter at a
    # posted line actually meets): the winner must improve BOTH mean
    # per-line ECE and the worst single-line bin gap vs raw.
    maps = ("platt", "isotonic", "platt_per_line")
    cand = min(maps, key=lambda k: report[k]["mean_line_ece"])
    ok = (report[cand]["mean_line_ece"] < report["raw"]["mean_line_ece"]
          and report[cand]["worst_line_gap"] < report["raw"]["worst_line_gap"])
    if not ok:
        print(f"\nNOT SHIPPED: {cand} does not improve both mean per-line "
              f"ECE and the worst line gap on the untouched holdout — a "
              f"calibrator that doesn't calibrate is A-044 again. Serving "
              f"stays raw + clamp.")
        return 1

    payload = {
        "kind": cand,
        "a": a, "b": b,
        "iso_x": xk.tolist(), "iso_y": yk.tolist(),
        "per_line": {str(k): list(v) for k, v in per_line_ab.items()},
        "prob_eps": PROB_EPS,
        "fitted_on": "pooled OOS: fit2024->2025 + fit2025->2024",
        "holdout": {"year": 2026, "n_pairs": int(len(p_h)), **report},
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(CALIBRATOR_PATH, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"\nSHIPPED {cand} -> {CALIBRATOR_PATH}")
    print("(gate: holdout mean per-line ECE and worst line gap both "
          "improved vs raw)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
