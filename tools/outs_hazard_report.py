"""Empirical hazard structure of STARTING-PITCHER OUTS RECORDED.

Reads data/outs_starts.parquet (built by tools/build_outs_dataset.py) and
measures the removal process. Nothing here is fitted for production; this is
the descriptive foundation that tells the downstream model what shape to be.

Sections
  0  reconstruction self-checks
  1  outs distribution, overall and per season (mean, sd, full PMF 0..27)
  2  removal hazard h(k) = P(stop at exactly k | reached k), per season
  3  boundary hazard P(not sent out for inning j+1 | completed inning j)
  4  partial-inning outs {0,1,2} conditional on a mid-inning removal
  5  is the hazard indexed by INNING COMPLETED or by OUTS RECORDED?
  6  year-over-year drift in each boundary hazard

    python tools/outs_hazard_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tools.build_outs_dataset import load_outs_starts  # noqa: E402

YEARS = [2024, 2025, 2026]
pd.set_option("display.width", 200)


# --------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """z and two-sided p for p2 - p1. Normal approx, pooled SE."""
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    p1, p2 = k1 / n1, k2 / n2
    pp = (k1 + k2) / (n1 + n2)
    se = np.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    if se == 0:
        return (float("nan"), float("nan"))
    z = (p2 - p1) / se
    from math import erfc

    p = erfc(abs(z) / np.sqrt(2.0))
    return (z, p)


def irls_logit(X: np.ndarray, y: np.ndarray, n: np.ndarray, iters: int = 60):
    """Grouped-binomial logistic regression. y = successes, n = trials.
    Returns (beta, se, loglik, deviance_vs_saturated)."""
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = X @ b
        mu = 1.0 / (1.0 + np.exp(-eta))
        mu = np.clip(mu, 1e-12, 1 - 1e-12)
        W = n * mu * (1 - mu)
        z = eta + (y - n * mu) / np.maximum(W, 1e-12)
        XtW = X.T * W
        H = XtW @ X
        nb = np.linalg.solve(H, XtW @ z)
        if np.max(np.abs(nb - b)) < 1e-10:
            b = nb
            break
        b = nb
    eta = X @ b
    mu = np.clip(1.0 / (1.0 + np.exp(-eta)), 1e-12, 1 - 1e-12)
    ll = float(np.sum(y * np.log(mu) + (n - y) * np.log(1 - mu)))
    # saturated log-lik on the grouped table
    ph = np.clip(y / np.maximum(n, 1), 1e-12, 1 - 1e-12)
    lls = float(np.sum(y * np.log(ph) + (n - y) * np.log(1 - ph)))
    W = n * mu * (1 - mu)
    cov = np.linalg.inv((X.T * W) @ X)
    se = np.sqrt(np.diag(cov))
    return b, se, ll, 2 * (lls - ll)


def hdr(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


# --------------------------------------------------------------------------
def section0(d: pd.DataFrame) -> None:
    hdr("0. RECONSTRUCTION SELF-CHECKS")
    print(f"rows                       : {len(d):,}")
    print(f"distinct game_pk           : {d.game_pk.nunique():,}  (2x = {2*d.game_pk.nunique():,})")
    print(f"outs range                 : {d.outs.min()}..{d.outs.max()}")
    print(f"starts per game (min,max)  : "
          f"{d.groupby('game_pk').size().min()},{d.groupby('game_pk').size().max()}")
    print(f"date range                 : {d.game_date.min().date()} .. {d.game_date.max().date()}")

    # A starter pitches contiguously from inning 1, so outs in innings 1..J-1
    # must be 3 apiece. r = outs - 3(J-1) must therefore land in 0..3.
    r = d["outs"] - 3 * (d["max_inning"] - 1)
    print(f"\nfinal-inning outs r = outs - 3*(max_inning-1)")
    print(f"  in 0..3 : {int(((r >= 0) & (r <= 3)).sum()):,} / {len(d):,} "
          f"({100*((r>=0)&(r<=3)).mean():.3f}%)")
    bad = d[(r < 0) | (r > 3)]
    print(f"  violations: {len(bad):,}  "
          f"(expected: the 574 half-innings that do not sum to 3 -- rain/walkoff)")
    print(f"  r value counts (all rows): {dict(r.value_counts().sort_index())}")


def section1(d: pd.DataFrame) -> None:
    hdr("1. OUTS DISTRIBUTION")
    rows = []
    for lab, sub in [("ALL", d)] + [(str(y), d[d.game_year == y]) for y in YEARS]:
        rows.append({
            "split": lab, "n": len(sub),
            "mean": sub.outs.mean(), "sd": sub.outs.std(ddof=1),
            "median": sub.outs.median(),
            "p_mult3": (sub.outs % 3 == 0).mean(),
            "min": sub.outs.min(), "max": sub.outs.max(),
        })
    print(pd.DataFrame(rows).to_string(index=False,
          float_format=lambda x: f"{x:.4f}"))

    print("\nFull PMF 0..27 (count / probability), by season")
    tab = pd.DataFrame(index=range(28))
    tab["n_ALL"] = d.outs.value_counts().reindex(range(28), fill_value=0)
    tab["p_ALL"] = tab["n_ALL"] / len(d)
    for y in YEARS:
        s = d[d.game_year == y]
        tab[f"n_{y}"] = s.outs.value_counts().reindex(range(28), fill_value=0)
        tab[f"p_{y}"] = tab[f"n_{y}"] / len(s)
    tab.index.name = "outs"
    print(tab.to_string(float_format=lambda x: f"{x:.4f}"))

    print("\nLattice ratios (ALL):")
    p = tab["p_ALL"]
    for a, b in [(15, 16), (18, 19), (21, 22), (12, 13), (24, 25)]:
        r = p[a] / p[b] if p[b] > 0 else float("inf")
        print(f"  P({a})/P({b}) = {p[a]:.4f}/{p[b]:.4f} = {r:.1f}x")


def hazard_table(sub: pd.DataFrame) -> pd.DataFrame:
    o = sub["outs"].to_numpy()
    rows = []
    for k in range(28):
        at_risk = int((o >= k).sum())
        stop = int((o == k).sum())
        h = stop / at_risk if at_risk else float("nan")
        lo, hi = wilson(stop, at_risk)
        rows.append({"k": k, "at_risk": at_risk, "stop": stop, "h": h,
                     "lo95": lo, "hi95": hi})
    return pd.DataFrame(rows).set_index("k")


def section2(d: pd.DataFrame) -> pd.DataFrame:
    hdr("2. REMOVAL HAZARD  h(k) = P(outs == k | outs >= k)")
    allh = hazard_table(d)
    out = pd.DataFrame(index=range(28))
    out["at_risk"] = allh["at_risk"]
    out["stop"] = allh["stop"]
    out["h_ALL"] = allh["h"]
    out["lo95"] = allh["lo95"]
    out["hi95"] = allh["hi95"]
    for y in YEARS:
        t = hazard_table(d[d.game_year == y])
        out[f"n{y}"] = t["at_risk"]
        out[f"h{y}"] = t["h"]
    out.index.name = "k"
    print(out.to_string(float_format=lambda x: f"{x:.4f}"))
    print("\nboundary k (k%3==0) mean h = "
          f"{allh['h'][[3,6,9,12,15,18,21,24]].mean():.4f}   "
          "non-boundary k in 3..26 mean h = "
          f"{allh['h'][[k for k in range(3,27) if k%3]].mean():.4f}")
    return allh


def section3(d: pd.DataFrame) -> pd.DataFrame:
    hdr("3. BOUNDARY HAZARD  P(not sent out for inning j+1 | completed inning j)")
    print("completed inning j  <=>  outs >= 3j (starter pitches contiguously from inning 1)")
    print("not sent out for j+1 <=> max_inning == j")
    print("EXCLUDES starts where the game itself ended at inning j (game_max_inning == j),")
    print("where there was no inning j+1 to be sent out for. Raw column shown alongside.\n")
    rows = []
    for j in range(1, 9):
        elig_all = d[d.outs >= 3 * j]
        elig = elig_all[elig_all.game_max_inning > j]
        n, k = len(elig), int((elig.max_inning == j).sum())
        na, ka = len(elig_all), int((elig_all.max_inning == j).sum())
        lo, hi = wilson(k, n)
        r = {"j": j, "n_elig": n, "n_pulled": k, "H_j": k / n if n else np.nan,
             "lo95": lo, "hi95": hi,
             "H_j_raw": ka / na if na else np.nan, "n_raw": na,
             "n_gameEndedAtJ": na - n}
        for y in YEARS:
            e = elig[elig.game_year == y]
            r[f"n{y}"] = len(e)
            r[f"H{y}"] = (e.max_inning == j).sum() / len(e) if len(e) else np.nan
        rows.append(r)
    t = pd.DataFrame(rows).set_index("j")
    print(t.to_string(float_format=lambda x: f"{x:.4f}"))
    return t


def section4(d: pd.DataFrame) -> None:
    hdr("4. PARTIAL-INNING OUTS GIVEN A MID-INNING REMOVAL")
    d = d.copy()
    d["J"] = d["max_inning"]
    d["r"] = d["outs"] - 3 * (d["J"] - 1)          # outs recorded in final inning
    d = d[(d.r >= 0) & (d.r <= 3)]                  # drop reconstruction anomalies
    mid = d[d.r < 3].copy()
    clean = mid[mid.game_max_inning > mid.J]        # game did not end in that inning

    print(f"starts entering an inning and leaving before 3 outs: {len(mid):,} "
          f"({100*len(mid)/len(d):.2f}% of {len(d):,})")
    print(f"  of which the game itself ended that inning: {len(mid)-len(clean):,}\n")

    for lab, s in [("ALL mid-inning removals", mid), ("game continued past that inning", clean)]:
        vc = s.r.value_counts().reindex([0, 1, 2], fill_value=0)
        p = vc / vc.sum()
        print(f"{lab}:  n={vc.sum():,}")
        print(f"  r=0 {vc[0]:>6,} ({p[0]:.4f})   r=1 {vc[1]:>6,} ({p[1]:.4f})   "
              f"r=2 {vc[2]:>6,} ({p[2]:.4f})    uniform would be 0.3333 each")

    print("\nBy inning entered J (game continued past that inning):")
    g = (clean.groupby("J")["r"].value_counts().unstack().reindex(columns=[0, 1, 2])
         .fillna(0).astype(int))
    g["n"] = g.sum(axis=1)
    for c in [0, 1, 2]:
        g[f"p{c}"] = g[c] / g["n"]
    print(g.to_string(float_format=lambda x: f"{x:.4f}"))

    # chi-square of independence r _||_ J, over J with n >= 100
    obs = g.loc[g["n"] >= 100, [0, 1, 2]].to_numpy(dtype=float)
    rt = obs.sum(axis=1, keepdims=True)
    ct = obs.sum(axis=0, keepdims=True)
    exp = rt @ ct / obs.sum()
    chi2 = float(((obs - exp) ** 2 / exp).sum())
    dof = (obs.shape[0] - 1) * (obs.shape[1] - 1)
    from math import lgamma, exp as _e

    def chi2_sf(x, k):  # regularized upper incomplete gamma, series+CF
        a = k / 2.0
        x = x / 2.0
        if x < a + 1:
            s, term = 1.0 / a, 1.0 / a
            for i in range(1, 500):
                term *= x / (a + i)
                s += term
                if abs(term) < abs(s) * 1e-14:
                    break
            return 1.0 - s * _e(-x + a * np.log(x) - lgamma(a))
        b, c, dd, h = x + 1 - a, 1e300, 1 / (x + 1 - a), 1 / (x + 1 - a)
        for i in range(1, 500):
            an = -i * (i - a)
            b += 2
            dd = an * dd + b
            if abs(dd) < 1e-300:
                dd = 1e-300
            c = b + an / c
            if abs(c) < 1e-300:
                c = 1e-300
            dd = 1 / dd
            de = dd * c
            h *= de
            if abs(de - 1) < 1e-14:
                break
        return h * _e(-x + a * np.log(x) - lgamma(a))

    print(f"\nchi-square independence of r from J (J with n>=100): "
          f"chi2={chi2:.2f}, dof={dof}, p={chi2_sf(chi2, dof):.3g}")
    return chi2_sf


def section5(d: pd.DataFrame, allh: pd.DataFrame, chi2_sf) -> None:
    hdr("5. IS THE HAZARD INDEXED BY INNING COMPLETED OR BY OUTS RECORDED?")
    o = d["outs"].to_numpy()
    print(f"stops on a multiple of 3 : {int((o % 3 == 0).sum()):,}/{len(o):,} "
          f"= {100*(o%3==0).mean():.2f}%   (chance under out-indexing ~33%)")

    # grouped discrete-time survival table, k = 1..26 (k=27 is a forced stop)
    ks = np.array([k for k in range(1, 27) if allh.loc[k, "at_risk"] >= 100])
    n = allh.loc[ks, "at_risk"].to_numpy(float)
    y = allh.loc[ks, "stop"].to_numpy(float)
    one = np.ones_like(ks, dtype=float)
    kf = ks.astype(float)
    b0 = (ks % 3 == 0).astype(float)
    ph1 = (ks % 3 == 1).astype(float)

    models = {
        "M0 intercept only            ": np.c_[one],
        "M1 a + b*k                   ": np.c_[one, kf],
        "M2 a + b*k + c*1[k%3==0]     ": np.c_[one, kf, b0],
        "M3 a + b*k + c*1[k%3==0] + d*1[k%3==1]": np.c_[one, kf, b0, ph1],
        "M4 a + b*k + c*1[k%3==0] + e*k*1[k%3==0]": np.c_[one, kf, b0, kf * b0],
    }
    print(f"\ndiscrete-time survival, grouped over k in {ks.min()}..{ks.max()} "
          f"({len(ks)} risk points, {int(n.sum()):,} start-at-risk observations)")
    print(f"{'model':<42}{'p':>3}{'logLik':>12}{'deviance':>11}{'AIC':>11}")
    res = {}
    for name, X in models.items():
        b, se, ll, dev = irls_logit(X, y, n)
        res[name] = (b, se, ll, dev)
        print(f"{name:<42}{X.shape[1]:>3}{ll:>12.1f}{dev:>11.1f}"
              f"{2*X.shape[1]-2*ll:>11.1f}")
    print(f"{'saturated (free h per k)':<42}{len(ks):>3}"
          f"{res['M1 a + b*k                   '][2] + res['M1 a + b*k                   '][3]/2:>12.1f}"
          f"{0.0:>11.1f}"
          f"{2*len(ks) - 2*(res['M1 a + b*k                   '][2] + res['M1 a + b*k                   '][3]/2):>11.1f}")

    b, se, ll1, dev1 = res["M1 a + b*k                   "]
    b2, se2, ll2, dev2 = res["M2 a + b*k + c*1[k%3==0]     "]
    lr = 2 * (ll2 - ll1)
    print(f"\nLR test, adding the inning-boundary indicator to a smooth-in-k hazard:")
    print(f"  chi2 = {lr:.1f} on 1 df,  p = {chi2_sf(lr, 1):.3g}")
    print(f"  boundary coefficient c = {b2[2]:+.4f}  (se {se2[2]:.4f}, "
          f"z {b2[2]/se2[2]:+.1f})  odds ratio {np.exp(b2[2]):.2f}x")
    print(f"  deviance vs saturated: M1 {dev1:.1f} -> M2 {dev2:.1f} "
          f"({100*(1-dev2/dev1):.1f}% of M1's lack of fit removed by one boundary term)")

    print("\nDirect contrast -- hazard at a boundary vs the two out-counts after it:")
    print(f"{'j':>3}{'k=3j':>8}{'h(3j)':>9}{'h(3j+1)':>10}{'h(3j+2)':>10}{'ratio h(3j)/h(3j+1)':>22}")
    for j in range(1, 9):
        k = 3 * j
        if k + 2 > 27:
            continue
        h0 = allh.loc[k, "h"]
        h1 = allh.loc[k + 1, "h"]
        h2 = allh.loc[k + 2, "h"]
        print(f"{j:>3}{k:>8}{h0:>9.4f}{h1:>10.4f}{h2:>10.4f}"
              f"{(h0/h1 if h1 else float('inf')):>22.1f}")

    print("\nINTERPRETATION (inference, not measurement): for a starter, out-count k")
    print("and (inning j, phase k mod 3) are in bijection, so the two indexings are")
    print("not separately identified from start data alone. The evidence is a")
    print("parsimony/fit argument: one boundary indicator recovers the bulk of the")
    print("lack of fit that a smooth-in-k hazard leaves behind, and the boundary")
    print("hazards are 1-2 orders of magnitude above their mid-inning neighbours.")


def section6(t3: pd.DataFrame, d: pd.DataFrame) -> None:
    hdr("6. YEAR-OVER-YEAR DRIFT IN THE BOUNDARY HAZARD")
    print("H_j = P(not sent out for inning j+1 | completed inning j), game-continued subset\n")
    rows = []
    for j in range(1, 9):
        elig = d[(d.outs >= 3 * j) & (d.game_max_inning > j)]
        cnt = {}
        for y in YEARS:
            e = elig[elig.game_year == y]
            cnt[y] = (int((e.max_inning == j).sum()), len(e))
        z1, p1 = two_prop_z(*cnt[2024], *cnt[2025])
        z2, p2 = two_prop_z(*cnt[2025], *cnt[2026])
        r = {"j": j}
        for y in YEARS:
            k, n = cnt[y]
            r[f"n{y}"] = n
            r[f"H{y}"] = k / n if n else np.nan
        r["d_24_25"] = r["H2025"] - r["H2024"]
        r["z_24_25"] = z1
        r["p_24_25"] = p1
        r["d_25_26"] = r["H2026"] - r["H2025"]
        r["z_25_26"] = z2
        r["p_25_26"] = p2
        rows.append(r)
    t = pd.DataFrame(rows).set_index("j")
    print(t.to_string(float_format=lambda x: f"{x:.4f}"))

    print("\nSeason mean outs, and a date-matched window (through Aug 6) for a fair 2026 read:")
    cut = d.game_date.dt.strftime("%m-%d") <= "08-06"
    for y in YEARS:
        s = d[d.game_year == y]
        sm = d[(d.game_year == y) & cut]
        print(f"  {y}: full n={len(s):>5} mean={s.outs.mean():.4f} | "
              f"through 08-06 n={len(sm):>5} mean={sm.outs.mean():.4f} "
              f"sd={sm.outs.std(ddof=1):.4f}")
    a = d[(d.game_year == 2025) & cut].outs
    b = d[(d.game_year == 2026) & cut].outs
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    print(f"  matched-window 2025->2026 delta = {b.mean()-a.mean():+.4f} outs, "
          f"t = {(b.mean()-a.mean())/se:+.2f}")


def section7(d: pd.DataFrame) -> None:
    """The 2026 drift is not one thing. Split it before specifying a regime term."""
    hdr("7. DECOMPOSING THE 2026 DRIFT  (how the regime term must be specified)")
    d = d[d.game_date.dt.strftime("%m-%d") <= "08-06"].copy()

    print("Date-matched window (through Aug 6) so 2026 is not penalised for being short.\n")
    for y in YEARS:
        s = d[d.game_year == y]
        print(f"  {y}  n={len(s):>5}  mean={s.outs.mean():.4f}  "
              f"P(outs<=6)={(s.outs<=6).mean():.4f} (n={int((s.outs<=6).sum())})  "
              f"mean|outs>6={s[s.outs>6].outs.mean():.4f}")

    a = d[d.game_year == 2025].outs
    b = d[d.game_year == 2026].outs
    pa, pb = (a <= 6).mean(), (b <= 6).mean()
    ma, mb = a[a <= 6].mean(), b[b <= 6].mean()
    Ma, Mb = a[a > 6].mean(), b[b > 6].mean()
    tot = b.mean() - a.mean()
    mix, sh, lo = (pb - pa) * (ma - Ma), pb * (mb - ma), (1 - pb) * (Mb - Ma)
    print(f"\n2025 -> 2026 mean-outs change decomposed (total {tot:+.4f}):")
    print(f"  mixture: more short starts (openers/bullpen games)  {mix:+.4f}  ({100*mix/tot:.0f}%)")
    print(f"  within-short mean shift                             {sh:+.4f}  ({100*sh/tot:.0f}%)")
    print(f"  within-normal mean shift ('earlier hook')           {lo:+.4f}  ({100*lo/tot:.0f}%)")

    for thr in (3, 6, 9):
        z, p = two_prop_z(int((a <= thr).sum()), len(a), int((b <= thr).sum()), len(b))
        print(f"  P(outs<={thr}): {(a<=thr).mean():.4f} -> {(b<=thr).mean():.4f}  "
              f"z={z:+.2f} p={p:.5f}")

    print("\nSame comparison on the market-relevant population only (5-6 days rest,")
    print("i.e. a conventional rotation turn -- DK lists Outs O/U at 13.5-19.5 for these):")
    s = d[(d.days_since_prev_game >= 5) & (d.days_since_prev_game <= 6)]
    for y0, y1 in ((2024, 2025), (2025, 2026)):
        x = s[s.game_year == y0].outs
        y = s[s.game_year == y1].outs
        se = np.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y))
        t = (y.mean() - x.mean()) / se
        from math import erfc
        print(f"  {y0}->{y1}: {x.mean():.4f} (n={len(x)}) -> {y.mean():.4f} (n={len(y)})  "
              f"delta {y.mean()-x.mean():+.4f}  t={t:+.2f}  p={erfc(abs(t)/np.sqrt(2)):.4f}")

    print("\n  boundary hazard H_j within that subset:")
    for j in (4, 5, 6, 7):
        parts = []
        for y in YEARS:
            e = s[(s.game_year == y) & (s.outs >= 3 * j) & (s.game_max_inning > j)]
            parts.append(f"{y} {(e.max_inning==j).sum()/len(e):.4f} (n={len(e)})")
        print(f"    j={j}   " + "   ".join(parts))


def main() -> int:
    d = load_outs_starts()
    section0(d)
    section1(d)
    allh = section2(d)
    t3 = section3(d)
    chi2_sf = section4(d)
    section5(d, allh, chi2_sf)
    section6(t3, d)
    section7(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
