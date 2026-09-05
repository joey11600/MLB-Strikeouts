"""Gates 2-5 for the ROLE block (A-054) of the OUTS RECORDED model.

Candidate feature sets against the SHIPPED set, refit and scored on the three
mandated splits (CLAUDE.md: train 2024 -> test 2025, train 2025 -> test 2024,
train 2024+2025 -> test 2026; a feature that helps in only one direction is
rejected), plus the population the block exists for.

Per split and candidate:
  * Gate 2 -- mean Brier on P(outs > L) over the seven market lines, the
    decision metric the penalty is selected on, and over the wider
    9.5..19.5 grid the live board actually posts (the role rows sit at
    9.5-12.5). Paired per-start Brier difference vs the shipped set with its
    z, so "better" is a statement with an error bar.
  * The ROLE population: test rows whose previous appearance was relief --
    predicted vs actual mean outs and P(outs >= 12), which is the miss the
    block was built for.
  * Gate 3 -- d E[outs] from moving each role term over the TRAINING rows,
    against the measured direction (the sign contract), and the raw slopes.
  * Gate 4 -- VIF of every column of the fitted design, and the largest
    pairwise |r| involving a role column.
  * Gate 5 -- ECE of P(outs > L) at 12.5 / 15.5 / 17.5 and the calibration
    deciles at 12.5, base vs candidate.
  * A-024a check -- the SERVED rows (data/outs_model_log.csv joined on
    game_pk + pitcher): Brier at the actual DK line, all rows and the
    low-line (<= 12.5) subset. Small n; reported, not gated.

The training table on disk is a 2026-only slice, so the full 2024-2026
per-start table is rebuilt from the Statcast cache here (never written back)
and the finished feature frame is cached under --frame for reruns.

Usage:
    python tools/gate_outs_role.py --frame /tmp/outs_role_frame.parquet \\
        --sets base,role --json /tmp/gate_role.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.outs_hazard import (  # noqa: E402
    BASE_FEATURES, MARKET_LINES, ROLE_BINARY, ROLE_COLUMN_BLOCK,
    ROLE_MISSING_BLOCK, ROLE_NUMERIC, SPLITS, OutsHazard, build_design,
    per_row_brier,
)
from models.outs_hazard_proto import N_OUTCOMES, p_over  # noqa: E402

K_GRID = np.arange(N_OUTCOMES)
WIDE_LINES = (9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5)
ROLE_TERMS = tuple(ROLE_NUMERIC) + tuple(ROLE_BINARY) + ("prev_app_short", "relief_short")

#: The candidate sets. `relief` is the binary alone (no new missing block:
#: "no prior appearance" reads as not-relief, the honest 0); `pitches` is
#: the count alone with its block; `role` is the pair; `role3` adds the
#: capped relief-since-start count.
CANDIDATES = {
    "base": BASE_FEATURES,
    "relief": BASE_FEATURES.extend("relief", binary=("prev_app_relief",)),
    "pitches": BASE_FEATURES.extend("pitches", numeric=("prev_app_pitches",),
                                    missing_blocks=ROLE_MISSING_BLOCK,
                                    column_blocks={"prev_app_pitches": "miss_role"}),
    "role": BASE_FEATURES.extend("role", numeric=("prev_app_pitches",),
                                 binary=("prev_app_relief",),
                                 missing_blocks=ROLE_MISSING_BLOCK,
                                 column_blocks={"prev_app_pitches": "miss_role"}),
    "role3": BASE_FEATURES.extend("role3", numeric=ROLE_NUMERIC, binary=ROLE_BINARY,
                                  missing_blocks=ROLE_MISSING_BLOCK,
                                  column_blocks=ROLE_COLUMN_BLOCK),
    # `_mb` variants route "no prior appearance this season" through the
    # EXISTING miss_budget block instead of a new indicator. The rows are a
    # strict subset of miss_budget's (no appearance => no start), so a
    # separate indicator is a near-linear combination of miss_budget,
    # rest_unknown and is_debut (VIF 44-61 on the first pass) while the
    # value itself, with prev_app_relief, still separates relief-before-
    # first-start from a returning starter.
    "pitches_mb": BASE_FEATURES.extend("pitches_mb", numeric=("prev_app_pitches",),
                                       column_blocks={"prev_app_pitches": "miss_budget"}),
    "role_mb": BASE_FEATURES.extend("role_mb", numeric=("prev_app_pitches",),
                                    binary=("prev_app_relief",),
                                    column_blocks={"prev_app_pitches": "miss_budget"}),
    # Concavity probe: the measured effect saturates (<=40 pitches -> 8
    # outs, 41-60 -> 13.6, 76+ -> 16.2), so a hinge on the short tail is
    # tested beside the straight line. Derived in load_frame.
    "short_mb": BASE_FEATURES.extend("short_mb", numeric=("prev_app_short",),
                                     column_blocks={"prev_app_short": "miss_budget"}),
    "pitches_hinge_mb": BASE_FEATURES.extend(
        "pitches_hinge_mb", numeric=("prev_app_pitches", "prev_app_short"),
        column_blocks={"prev_app_pitches": "miss_budget",
                       "prev_app_short": "miss_budget"}),
}
SHORT_KNEE = 60.0
CANDIDATES["relief_short_mb"] = BASE_FEATURES.extend(
    "relief_short_mb", numeric=("prev_app_pitches", "relief_short"),
    column_blocks={"prev_app_pitches": "miss_budget", "relief_short": "miss_budget"})


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def build_frame(verbose: bool = True) -> pd.DataFrame:
    """The full 2024-2026 feature frame WITH the role block, from the cache."""
    from features.outs_asof import (
        build_appearances_table, build_outs_asof, build_starts_table,
        load_statcast_pa)
    from tools.build_outs_dataset import build

    t0 = time.time()
    starts = build(verbose=False)                 # validated reconstruction
    pa = load_statcast_pa()
    _, tb = build_starts_table(pa)
    apps = build_appearances_table(pa)
    feat = build_outs_asof(starts, tb, appearances=apps)
    n_games = feat["game_pk"].nunique()
    if len(feat) != 2 * n_games:
        raise ValueError(f"{len(feat)} rows for {n_games} games; expected 2x")
    if verbose:
        yrs = feat["game_date"].dt.year.value_counts().sort_index().to_dict()
        print(f"[data] {len(feat):,} starts {yrs}, appearances {len(apps):,}, "
              f"role rows (prev relief) {int((feat['prev_app_relief'] == 1).sum()):,} "
              f"({time.time() - t0:.0f}s)")
    return feat


def _derive(feat: pd.DataFrame) -> pd.DataFrame:
    """Probe columns derived from the emitted block (NaN stays NaN)."""
    feat["prev_app_short"] = np.maximum(0.0, SHORT_KNEE - feat["prev_app_pitches"])
    # short RELIEF outings specifically: the Morris profile
    feat["relief_short"] = feat["prev_app_relief"] * feat["prev_app_short"]
    return feat


def load_frame(path: Path | None, verbose: bool = True) -> pd.DataFrame:
    if path is not None and path.exists():
        feat = pd.read_parquet(path)
        feat["game_date"] = pd.to_datetime(feat["game_date"])
        if verbose:
            print(f"[data] cached frame {path}: {len(feat):,} rows")
        return _derive(feat)
    feat = _derive(build_frame(verbose=verbose))
    if path is not None:
        out = feat.copy()
        if isinstance(out["days_rest_bucket"].dtype, pd.CategoricalDtype):
            out["days_rest_bucket"] = out["days_rest_bucket"].astype(str)
        out.to_parquet(path)
        if verbose:
            print(f"[data] cached -> {path}")
    return feat


def served_rows() -> pd.DataFrame:
    """The graded served rows, keyed for the A-024a join."""
    p = Path(__file__).resolve().parent.parent / "data" / "outs_model_log.csv"
    if not p.exists():
        return pd.DataFrame()
    m = pd.read_csv(p)
    m = m[m["actual_outs"].notna()]
    return m[["game_pk", "pitcher_id", "line", "actual_outs", "p_over_cal",
              "fair_over"]].rename(columns={"pitcher_id": "pitcher"})


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def brier_lines(pmf, outs, lines):
    return {float(L): float(np.mean((p_over(pmf, L) - (outs > L)) ** 2)) for L in lines}


def ece(p, y, n_bins=10):
    order = np.argsort(p, kind="mergesort")
    p, y = p[order], y[order]
    edges = np.linspace(0, len(p), n_bins + 1).astype(int)
    tot = e = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        if b > a:
            e += (b - a) * abs(p[a:b].mean() - y[a:b].mean())
            tot += b - a
    return float(e / tot) if tot else float("nan")


def deciles(p, y, n_bins=10):
    order = np.argsort(p, kind="mergesort")
    p, y = p[order], y[order]
    edges = np.linspace(0, len(p), n_bins + 1).astype(int)
    rows = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b > a:
            rows.append({"n": int(b - a), "pred": float(p[a:b].mean()),
                         "obs": float(y[a:b].mean())})
    return rows


def vif_table(X: np.ndarray, names: list[str]) -> pd.DataFrame:
    """Variance inflation of every column against all the others."""
    Xc = X - X.mean(axis=0)
    rows = []
    for k, nm in enumerate(names):
        y = Xc[:, k]
        others = np.delete(Xc, k, axis=1)
        if y.std() < 1e-12:
            rows.append({"feature": nm, "vif": float("nan")})
            continue
        coef, *_ = np.linalg.lstsq(others, y, rcond=None)
        resid = y - others @ coef
        r2 = 1.0 - float(resid.var()) / float(y.var())
        rows.append({"feature": nm, "vif": float(1.0 / max(1.0 - r2, 1e-12))})
    return pd.DataFrame(rows)


def top_correlations(X: np.ndarray, names: list[str], focus: tuple[str, ...]):
    c = np.corrcoef(X, rowvar=False)
    out = []
    for i, a in enumerate(names):
        if a not in focus:
            continue
        for j, b in enumerate(names):
            if i != j and np.isfinite(c[i, j]):
                out.append({"a": a, "b": b, "r": float(c[i, j])})
    out.sort(key=lambda r: -abs(r["r"]))
    return out[:8]


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(feat: pd.DataFrame, sets: list[str], verbose: bool = True) -> dict:
    year = feat["game_date"].dt.year
    served = served_rows()
    results: dict = {"splits": {}, "sets": sets}

    for sname, cfg in SPLITS.items():
        train = feat[year.isin(list(cfg["train"]))].copy()
        pos = np.flatnonzero((year == int(cfg["test"])).to_numpy())
        test = feat.iloc[pos].copy()
        outs = test["outs"].to_numpy(int)
        role_mask = (test["prev_app_relief"] == 1).to_numpy()
        label = f"train {'+'.join(map(str, cfg['train']))} -> test {cfg['test']}"
        print("\n" + "=" * 78)
        print(f"{sname}   {label}   ({len(train):,} train, {len(test):,} test, "
              f"{int(role_mask.sum()):,} test rows with a relief previous appearance)")
        print("=" * 78)

        split_res: dict = {"label": label, "n_train": int(len(train)),
                           "n_test": int(len(test)), "n_role": int(role_mask.sum()),
                           "cands": {}}
        base_rows = None
        for cname in sets:
            fs = CANDIDATES[cname]
            t0 = time.time()
            model = OutsHazard()
            model.fit(train, test_df=test, verbose=False, features=fs)
            pmf = model.predict_pmf_frame(test)
            rows7 = per_row_brier(pmf, outs, MARKET_LINES)
            rows_wide = per_row_brier(pmf, outs, WIDE_LINES)
            mu = (pmf * K_GRID).sum(axis=1)
            p12 = p_over(pmf, 11.5)

            r = {
                "lambda": float(model.lam),
                "brier7": float(rows7.mean()),
                "brier_wide": float(rows_wide.mean()),
                "per_line": brier_lines(pmf, outs, WIDE_LINES),
                "ece": {str(L): ece(p_over(pmf, L), (outs > L).astype(float))
                        for L in (12.5, 15.5, 17.5)},
                "deciles_12_5": deciles(p_over(pmf, 12.5), (outs > 12.5).astype(float)),
                "bias": float((mu - outs).mean()),
                "role_pop": {
                    "n": int(role_mask.sum()),
                    "mean_actual": float(outs[role_mask].mean()) if role_mask.any() else None,
                    "mean_pred": float(mu[role_mask].mean()) if role_mask.any() else None,
                    "p_ge12_actual": float((outs[role_mask] >= 12).mean()) if role_mask.any() else None,
                    "p_ge12_pred": float(p12[role_mask].mean()) if role_mask.any() else None,
                    "brier7": float(rows7[role_mask].mean()) if role_mask.any() else None,
                    "brier_wide": float(rows_wide[role_mask].mean()) if role_mask.any() else None,
                },
                "signs": None, "coef": None, "vif": None, "corr": None,
                "served": None, "fit_seconds": round(time.time() - t0, 1),
            }
            # paired vs base
            if cname == "base":
                base_rows = (rows7.copy(), rows_wide.copy())
                r["vs_base"] = None
            elif base_rows is not None:
                d7 = rows7 - base_rows[0]
                dw = rows_wide - base_rows[1]
                se7 = d7.std(ddof=1) / np.sqrt(len(d7))
                sew = dw.std(ddof=1) / np.sqrt(len(dw))
                dr = d7[role_mask]
                ser = dr.std(ddof=1) / np.sqrt(len(dr)) if len(dr) > 1 else float("nan")
                r["vs_base"] = {
                    "d_brier7": float(d7.mean()), "z7": float(d7.mean() / se7),
                    "d_brier_wide": float(dw.mean()), "z_wide": float(dw.mean() / sew),
                    "d_brier7_role": float(dr.mean()) if len(dr) else None,
                    "z7_role": float(dr.mean() / ser) if len(dr) > 1 and ser > 0 else None,
                }
            # Gate 3: signs on the TRAINING rows (the fit already computed them)
            signs = model.fit_report["signs"]
            signs = signs[signs["term"].isin(ROLE_TERMS)]
            r["signs"] = [{"term": t, "want": int(w), "d_mean_outs": (None if not np.isfinite(d) else float(d)), "ok": bool(o)}
                          for t, w, d, o in zip(signs["term"], signs["expected"], signs["d_mean_outs"], signs["ok"])]
            co = model.coefficients()
            co = co[co["feature"].isin(ROLE_TERMS + ("miss_role", "p5_pitches", "exp_o_shrunk"))]
            r["coef"] = co.to_dict("records")
            # Gate 4 on the fitted design
            X, names = build_design(train, model.spec, strict=False)
            vt = vif_table(X, names)
            r["vif"] = {"max": float(vt["vif"].max()),
                        "argmax": str(vt.loc[vt["vif"].idxmax(), "feature"]),
                        "role": {nm: float(v) for nm, v in zip(vt["feature"], vt["vif"])
                                 if nm in ROLE_TERMS or nm == "miss_role"}}
            r["corr"] = top_correlations(X, names, ROLE_TERMS + ("miss_role",))
            # A-024a: served rows, decision split only
            if sname == "S3" and len(served):
                key = test[["game_pk", "pitcher"]].reset_index(drop=True)
                key["_i"] = np.arange(len(key))
                j = served.merge(key, on=["game_pk", "pitcher"], how="inner")
                if len(j):
                    pp = np.array([p_over(pmf[i][None, :], L)[0]
                                   for i, L in zip(j["_i"], j["line"])])
                    yy = (j["actual_outs"].to_numpy() > j["line"].to_numpy()).astype(float)
                    low = (j["line"] <= 12.5).to_numpy()
                    mk = j["fair_over"].to_numpy(float)
                    r["served"] = {
                        "n": int(len(j)), "brier": float(np.mean((pp - yy) ** 2)),
                        "brier_market": float(np.nanmean((mk - yy) ** 2)),
                        "n_low": int(low.sum()),
                        "brier_low": float(np.mean((pp[low] - yy[low]) ** 2)) if low.any() else None,
                        "brier_low_market": float(np.nanmean((mk[low] - yy[low]) ** 2)) if low.any() else None,
                        "mean_p_low": float(pp[low].mean()) if low.any() else None,
                        "hit_low": float(yy[low].mean()) if low.any() else None,
                    }
            split_res["cands"][cname] = r

            if verbose:
                vb = r.get("vs_base")
                print(f"\n  [{cname:<7}] lambda={r['lambda']:<6g} Brier7={r['brier7']:.5f} "
                      f"wide={r['brier_wide']:.5f} bias={r['bias']:+.3f}  "
                      f"({r['fit_seconds']}s)")
                if vb:
                    print(f"            vs base: dBrier7={vb['d_brier7']:+.5f} (z={vb['z7']:+.2f})  "
                          f"dWide={vb['d_brier_wide']:+.5f} (z={vb['z_wide']:+.2f})  "
                          f"role rows dBrier7={vb['d_brier7_role']:+.5f} (z={vb['z7_role']:+.2f})"
                          if vb["z7_role"] is not None else
                          f"            vs base: dBrier7={vb['d_brier7']:+.5f} (z={vb['z7']:+.2f})")
                rp = r["role_pop"]
                if rp["n"]:
                    print(f"            role rows n={rp['n']}: mean outs actual {rp['mean_actual']:.2f} "
                          f"pred {rp['mean_pred']:.2f}; P(>=12) actual {rp['p_ge12_actual']:.3f} "
                          f"pred {rp['p_ge12_pred']:.3f}; Brier7 {rp['brier7']:.5f}")
                print(f"            ECE 12.5/15.5/17.5 = {r['ece']['12.5']:.4f} / "
                      f"{r['ece']['15.5']:.4f} / {r['ece']['17.5']:.4f}   "
                      f"max VIF {r['vif']['max']:.2f} ({r['vif']['argmax']})"
                      + (f"   role VIF {r['vif']['role']}" if r["vif"]["role"] else ""))
                for sg in r["signs"]:
                    d = "n/a" if sg["d_mean_outs"] is None else f"{sg['d_mean_outs']:+.3f}"
                    print(f"            sign {sg['term']:<20} want {sg['want']:+d}  dE[outs]={d}  "
                          f"{'OK' if sg['ok'] else '*** WRONG ***'}")
                if r["served"]:
                    sv = r["served"]
                    line = (f"            served rows n={sv['n']}: Brier {sv['brier']:.4f} "
                            f"(market {sv['brier_market']:.4f})")
                    if sv["n_low"]:
                        line += (f"; low lines n={sv['n_low']}: Brier {sv['brier_low']:.4f} "
                                 f"(market {sv['brier_low_market']:.4f}), mean P(over) "
                                 f"{sv['mean_p_low']:.3f} vs hit {sv['hit_low']:.3f}")
                    else:
                        line += "; no low-line (<= 12.5) served rows inside the cached test window"
                    print(line)
        results["splits"][sname] = split_res

    # ------------------------------------------------------------ verdicts
    print("\n" + "=" * 78)
    print("VERDICTS -- a candidate must beat the shipped set in EVERY split")
    print("=" * 78)
    verdicts = {}
    for cname in sets:
        if cname == "base":
            continue
        rows = []
        for sname in SPLITS:
            r = results["splits"][sname]["cands"][cname]
            b = results["splits"][sname]["cands"]["base"]
            rows.append({
                "split": sname,
                "brier7_better": r["brier7"] < b["brier7"],
                "wide_better": r["brier_wide"] < b["brier_wide"],
                "z7": r["vs_base"]["z7"], "z_wide": r["vs_base"]["z_wide"],
                "signs_ok": all(sg["ok"] for sg in r["signs"]),
                "vif_max": r["vif"]["max"],
                "vif_base_max": b["vif"]["max"],
                "vif_new_max": max([v for v in r["vif"]["role"].values()] or [0.0]),
                "ece_not_worse": np.mean(list(r["ece"].values())) <= np.mean(list(b["ece"].values())) + 0.002,
                "role_improved": (r["role_pop"]["brier7"] or 0) < (b["role_pop"]["brier7"] or 0),
            })
        g2 = all(x["brier7_better"] for x in rows) and all(x["wide_better"] for x in rows)
        g3 = all(x["signs_ok"] for x in rows)
        # Gate 4 is about the columns being ADDED: their own inflation must
        # stay under 10 (the shipped design already carries miss_budget at
        # 12.3 in S1, so a whole-design bar would fail the base set itself).
        g4 = all(x["vif_new_max"] < 10 for x in rows)
        g5 = all(x["ece_not_worse"] for x in rows)
        verdicts[cname] = {"rows": rows, "gate2": g2, "gate3": g3, "gate4": g4,
                           "gate5": g5, "pass": g2 and g3 and g4 and g5}
        print(f"\n  {cname}:")
        for x in rows:
            print(f"    {x['split']}  Brier7 {'better' if x['brier7_better'] else 'WORSE '} "
                  f"(z={x['z7']:+.2f})  wide {'better' if x['wide_better'] else 'WORSE '} "
                  f"(z={x['z_wide']:+.2f})  signs {'OK' if x['signs_ok'] else 'WRONG'}  "
                  f"newVIF {x['vif_new_max']:.2f} (design max {x['vif_max']:.1f}, base {x['vif_base_max']:.1f})  "
                  f"ECE {'ok' if x['ece_not_worse'] else 'WORSE'}  "
                  f"role rows {'better' if x['role_improved'] else 'WORSE'}")
        print(f"    Gate 2 {'PASS' if g2 else 'FAIL'} | Gate 3 {'PASS' if g3 else 'FAIL'} | "
              f"Gate 4 {'PASS' if g4 else 'FAIL'} | Gate 5 {'PASS' if g5 else 'FAIL'}  ==> "
              f"{'PASS' if verdicts[cname]['pass'] else 'REJECT'}")
    results["verdicts"] = verdicts
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frame", default=None, help="parquet cache of the feature frame")
    ap.add_argument("--sets", default="base,pitches_mb,relief,pitches,role_mb,role,role3,short_mb,pitches_hinge_mb")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    sets = [s.strip() for s in a.sets.split(",") if s.strip()]
    if "base" not in sets:
        sets = ["base"] + sets
    bad = [s for s in sets if s not in CANDIDATES]
    if bad:
        ap.error(f"unknown set(s) {bad}; choose from {list(CANDIDATES)}")
    feat = load_frame(Path(a.frame) if a.frame else None)
    res = run(feat, sets)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
