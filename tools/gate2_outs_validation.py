"""Gate 2 — three-way out-of-sample validation of the OUTS RECORDED model.

Pass/fail. CLAUDE.md: "Three-way split: train 2024 -> test 2025, train 2025 ->
test 2024, train 2024+2025 -> test 2026. A feature that helps in only one split
direction is rejected."

Runs the SHIPPED code (models.outs_hazard.OutsHazard) on all three mandated
splits and reports, per split:

  * Brier on P(outs > L) at every market line, model and honest baseline,
    with the per-line and overall improvement;
  * MAE / RMSE / signed bias on E[outs] -- a one-directional bias is a live
    money leak, because the edge filter selects it into the bet list;
  * the calibration curve on P(outs > 17.5), deciles of predicted probability
    against realized frequency;
  * on the DECISION split only, predicted vs empirical P(outs == k) for
    k = 12..21 -- the lattice check. A model that gets the mean right and the
    spikes wrong is useless here, because the line sits on the lattice.

Usage:
    python tools/gate2_outs_validation.py                 # all three splits
    python tools/gate2_outs_validation.py --fast-opponent # skip Statcast re-read
    python tools/gate2_outs_validation.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.outs_hazard import (  # noqa: E402
    MARKET_LINES, SPLITS, OutsHazard, asof_baseline_pmf, brier_at_lines,
    load_dataset,
)
from models.outs_hazard_proto import N_OUTCOMES, p_over  # noqa: E402

K_GRID = np.arange(N_OUTCOMES)


def point_metrics(pmf: np.ndarray, outs: np.ndarray) -> dict:
    """MAE / RMSE / signed bias of E[outs] against the realized outs."""
    mu = (pmf * K_GRID).sum(axis=1)
    err = mu - outs
    return {
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "bias": float(err.mean()),
        "bias_se": float(err.std(ddof=1) / np.sqrt(len(err))),
        "mean_pred": float(mu.mean()),
        "mean_actual": float(outs.mean()),
    }


def calibration_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Equal-count bins of predicted probability vs realized frequency."""
    order = np.argsort(p, kind="mergesort")
    ps, ys = p[order], y[order]
    edges = np.linspace(0, len(ps), n_bins + 1).astype(int)
    rows = []
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:]), start=1):
        if b <= a:
            continue
        pb, yb = ps[a:b], ys[a:b]
        n = b - a
        obs = float(yb.mean())
        # Wilson 95% interval on the realized frequency
        z = 1.959963985
        den = 1.0 + z * z / n
        ctr = (obs + z * z / (2 * n)) / den
        half = z * np.sqrt(obs * (1 - obs) / n + z * z / (4 * n * n)) / den
        rows.append({
            "decile": i, "n": int(n),
            "p_lo": float(pb.min()), "p_hi": float(pb.max()),
            "pred": float(pb.mean()), "obs": obs,
            "obs_lo": float(ctr - half), "obs_hi": float(ctr + half),
            "gap": float(pb.mean() - obs),
        })
    return pd.DataFrame(rows)


def lattice_table(pmf: np.ndarray, outs: np.ndarray, ks=range(12, 22)) -> pd.DataFrame:
    """Row-averaged predicted P(outs == k) vs the empirical histogram."""
    rows = []
    n = len(outs)
    for k in ks:
        pred = float(pmf[:, k].mean())
        cnt = int((outs == k).sum())
        emp = cnt / n
        se = float(np.sqrt(max(emp * (1 - emp), 1e-12) / n))
        rows.append({
            "k": int(k), "count": cnt, "empirical": emp, "predicted": pred,
            "abs_err": abs(pred - emp), "ratio": pred / emp if emp > 0 else float("nan"),
            "z": (pred - emp) / se if se > 0 else float("nan"),
            "boundary": (k % 3 == 0),
        })
    return pd.DataFrame(rows)


def run(fast_opponent: bool = False) -> dict:
    feat = load_dataset(fast_opponent=fast_opponent)
    base_all = asof_baseline_pmf(feat)
    year = feat["game_date"].dt.year

    results = {}
    for name, cfg in SPLITS.items():
        train = feat[year.isin(list(cfg["train"]))].copy()
        pos = np.flatnonzero((year == int(cfg["test"])).to_numpy())
        test = feat.iloc[pos].copy()
        base = base_all[pos]
        outs = test["outs"].to_numpy(int)

        label = f"train {'+'.join(map(str, cfg['train']))} -> test {cfg['test']}"
        print("\n" + "=" * 78)
        print(f"{name}   {label}   ({len(train):,} train starts, "
              f"{len(test):,} test starts)")
        print("=" * 78)

        model = OutsHazard()
        model.fit(train, test_df=test, verbose=True)
        pmf = model.predict_pmf_frame(test)

        sums = pmf.sum(axis=1)
        worst = float(np.abs(sums - 1.0).max())
        print(f"\n  PMF normalisation: worst |sum-1| = {worst:.3e} over "
              f"{len(test):,} rows")

        mper = brier_at_lines(pmf, outs)
        bper = brier_at_lines(base, outs)
        mmean = float(np.mean(list(mper.values())))
        bmean = float(np.mean(list(bper.values())))

        print(f"\n  BRIER on P(outs > L)   n = {len(test):,}")
        print(f"  {'line':>6} {'n over':>8} {'base rate':>10} {'model':>9} "
              f"{'naive':>9} {'improve %':>10}")
        for L in MARKET_LINES:
            y = (outs > L).astype(float)
            sk = 100.0 * (bper[L] - mper[L]) / bper[L]
            print(f"  {L:>6} {int(y.sum()):>8} {y.mean():>10.4f} "
                  f"{mper[L]:>9.5f} {bper[L]:>9.5f} {sk:>+10.2f}")
        skill = 100.0 * (bmean - mmean) / bmean
        print(f"  {'MEAN':>6} {'':>8} {'':>10} {mmean:>9.5f} {bmean:>9.5f} "
              f"{skill:>+10.2f}")

        pm = point_metrics(pmf, outs)
        pb = point_metrics(base, outs)
        print(f"\n  E[outs] POINT ACCURACY")
        print(f"  {'':<10} {'MAE':>8} {'RMSE':>8} {'bias':>9} {'bias SE':>9} "
              f"{'mean pred':>10} {'mean act':>9}")
        for nm, d in (("model", pm), ("naive", pb)):
            print(f"  {nm:<10} {d['mae']:>8.4f} {d['rmse']:>8.4f} "
                  f"{d['bias']:>+9.4f} {d['bias_se']:>9.4f} "
                  f"{d['mean_pred']:>10.4f} {d['mean_actual']:>9.4f}")
        print(f"  bias t-stat (model): {pm['bias'] / pm['bias_se']:+.2f}")

        cal = calibration_table(p_over(pmf, 17.5), (outs > 17.5).astype(float))
        print(f"\n  CALIBRATION, P(outs > 17.5), equal-count deciles")
        print(f"  {'bin':>4} {'n':>6} {'p range':>17} {'pred':>8} {'obs':>8} "
              f"{'gap':>8} {'obs 95% CI':>18}")
        for _, r in cal.iterrows():
            rng = f"[{r['p_lo']:.3f},{r['p_hi']:.3f}]"
            ci = f"[{r['obs_lo']:.3f},{r['obs_hi']:.3f}]"
            print(f"  {int(r['decile']):>4} {int(r['n']):>6} {rng:>17} "
                  f"{r['pred']:>8.4f} {r['obs']:>8.4f} {r['gap']:>+8.4f} "
                  f"{ci:>18}")
        ece = float((cal["n"] * cal["gap"].abs()).sum() / cal["n"].sum())
        print(f"  ECE(17.5) = {ece:.4f}   max |gap| = {cal['gap'].abs().max():.4f}")

        lat = None
        if name == "S3":
            lat = lattice_table(pmf, outs)
            print(f"\n  LATTICE CHECK — predicted vs empirical P(outs == k), "
                  f"k = 12..21, decision split")
            print(f"  {'k':>3} {'bnd':>4} {'count':>7} {'empirical':>10} "
                  f"{'predicted':>10} {'abs err':>9} {'ratio':>7} {'z':>7}")
            for _, r in lat.iterrows():
                print(f"  {int(r['k']):>3} {'*' if r['boundary'] else '':>4} "
                      f"{int(r['count']):>7} {r['empirical']:>10.4f} "
                      f"{r['predicted']:>10.4f} {r['abs_err']:>9.4f} "
                      f"{r['ratio']:>7.3f} {r['z']:>+7.2f}")
            tv = float(lat["abs_err"].sum())
            print(f"  sum |err| over k=12..21 = {tv:.4f}")
            # spike ratios, the thing a count model gets wrong
            print(f"\n  SPIKE RATIOS (a negative binomial gets these ~3x wrong)")
            for a, b in ((15, 16), (18, 19), (21, 22)):
                pe = (outs == a).mean() / max((outs == b).mean(), 1e-12)
                pp = pmf[:, a].mean() / pmf[:, b].mean()
                print(f"  P({a})/P({b}):  empirical {pe:>6.2f}   "
                      f"predicted {pp:>6.2f}")

        results[name] = {
            "label": label, "lambda": model.lam,
            "n_train": int(len(train)), "n_test": int(len(test)),
            "pmf_sum_worst": worst,
            "brier": {str(L): mper[L] for L in MARKET_LINES},
            "baseline_brier": {str(L): bper[L] for L in MARKET_LINES},
            "improve_pct": {str(L): 100.0 * (bper[L] - mper[L]) / bper[L]
                            for L in MARKET_LINES},
            "brier_mean": mmean, "baseline_brier_mean": bmean, "skill_pct": skill,
            "point": pm, "point_baseline": pb,
            "calibration_17_5": cal.to_dict("records"),
            "ece_17_5": ece,
            "lattice": None if lat is None else lat.to_dict("records"),
        }

    print("\n" + "=" * 78)
    print("GATE 2 VERDICT — must be positive in EVERY direction")
    print("=" * 78)
    print(f"  {'split':<5} {'direction':<26} {'n test':>7} {'lam':>5} "
          f"{'Brier':>9} {'naive':>9} {'skill %':>9} {'bias':>8}")
    for name in SPLITS:
        r = results[name]
        print(f"  {name:<5} {r['label']:<26} {r['n_test']:>7,} {r['lambda']:>5} "
              f"{r['brier_mean']:>9.5f} {r['baseline_brier_mean']:>9.5f} "
              f"{r['skill_pct']:>+9.2f} {r['point']['bias']:>+8.3f}")
    allpos = all(results[n]["skill_pct"] > 0 for n in SPLITS)
    perline = all(v > 0 for n in SPLITS for v in results[n]["improve_pct"].values())
    print(f"\n  all three splits positive overall : {allpos}")
    print(f"  all 21 split-x-line cells positive : {perline}")
    print(f"  GATE 2: {'PASS' if allpos and perline else 'REVIEW'}")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fast-opponent", action="store_true")
    ap.add_argument("--json", default=None, help="write the full result dict here")
    a = ap.parse_args(argv)
    res = run(fast_opponent=a.fast_opponent)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2, default=float),
                                encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
