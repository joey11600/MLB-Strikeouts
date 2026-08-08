# Outs Recorded — Model Form Specification

Target: **starting-pitcher total outs recorded (0..27)**, to price the
DraftKings "Outs Recorded O/U" market (subcategory 17413, observed lines
13.5–19.5, all half-integers).

Reference implementation: `models/outs_hazard_proto.py` (runnable, self-testing).

Everything below was **measured this session** on 13,170 regular-season starts
reconstructed from `data/statcast_cache/` (2024-03-28 .. 2026-08-06).
Statements labelled *inference* were not measured.

---

## 0. Why a new model family

| Quantity | Measured |
|---|---|
| mean / sd / median | 15.521 / 4.248 / 16 |
| support | 0..27, zero exceptions |
| P(outs is a multiple of 3) | 0.655 |
| P(18) / P(19) | 8.3x |
| P(21) / P(22) | 18.8x |

Outs is a **stopping time on a lattice**, not a count. The Stage-A x Stage-B
compound used for strikeouts cannot produce the sawtooth and is not reused.

### Target reconstruction (fixed; do not re-derive)

Sort by `(at_bat_number, pitch_number)` within `(game_pk, inning, inning_topbot)`;
`outs_on_play[i] = max(0, outs_when_up[i+1] - outs_when_up[i])`; the final PA of
a half-inning gets `3 - outs_when_up`, overridden to 0 on a walk-off. Filter
`game_type == 'R'`. Guard the 26 zero-column cache files with
`len(pq.ParquetFile(f).schema_arrow.names) > 0`.

Reproduced this session: 99.51% of 117,164 half-innings sum to exactly 3;
exactly `2 x n_games` = 13,170 starter rows; 575 walk-off overrides.
**Never count outs from the `events` column** — this cache's events never carry
caught-stealing or pickoff, so an events-only count is biased low.

---

## 1. The state chain

For inning `j = 1..9`, with `A_j` = "the starter throws at least one pitch in
inning j" (`A_1` is true by definition of *starter*):

- `X_j ∈ {0,1,2,3}` — outs he records in inning `j`, given `A_j`
- `R_j ∈ {0,1}` — he comes back out for inning `j+1`, given `X_j == 3`

Transitions:

| Event | Outcome |
|---|---|
| `X_j < 3` | start ends at `3(j-1) + X_j` |
| `X_j == 3`, `R_j == 0` | start ends at `3j` |
| `X_j == 3`, `R_j == 1` | continue to inning `j+1` |
| `j == 9`, `X_9 == 3` | start ends at 27 (structural ceiling) |

This is a **complete, lossless re-encoding** of the target: feeding the measured
marginal hazards through the recursion in §3 reproduces the empirical PMF to
±0.0001 on every multiple of 3, mean 15.522 vs 15.521, P(mult of 3) 0.655 vs
0.655, P(18)/P(19) 8.3 vs 8.3, P(21)/P(22) 18.9 vs 18.8
(`models/outs_hazard_proto.py`, final block).

### Measured state table (13,170 starts)

| j | N active | X=0 | X=1 | X=2 | X=3 | P(X<3) | N completed | P(R=1 \| X=3) |
|---|---|---|---|---|---|---|---|---|
| 1 | 13170 | 4 | 15 | 61 | 13090 | 0.0061 | 13090 | 0.9859 |
| 2 | 12905 | 28 | 74 | 90 | 12713 | 0.0149 | 12713 | 0.9877 |
| 3 | 12557 | 39 | 88 | 129 | 12301 | 0.0204 | 12301 | 0.9767 |
| 4 | 12015 | 76 | 206 | 291 | 11442 | 0.0477 | 11442 | 0.9364 |
| 5 | 10714 | 220 | 543 | 650 | 9301 | 0.1319 | 9301 | 0.7626 |
| 6 | 7093 | 375 | 777 | 817 | 5124 | 0.2776 | 5124 | 0.4690 |
| 7 | 2403 | 178 | 350 | 316 | 1559 | 0.3512 | 1559 | 0.2502 |
| 8 | 390 | 41 | 64 | 56 | 229 | 0.4128 | 229 | 0.3537 |
| 9 | 81 | 9 | 8 | 8 | 56 | 0.3086 | 56 | **0.0000** |

### The two-path structure at `outs = 3j`

`outs = 3j` is reachable two disjoint ways: complete inning `j` and not return,
**or** return for inning `j+1` and be removed before recording an out. Both are
real; a model that collapses them is misspecified.

| 3j | N(outs=3j) | via no-return | via `X_{j+1}=0` | fraction via 0-out |
|---|---|---|---|---|
| 3 | 213 | 185 | 28 | 0.132 |
| 6 | 195 | 156 | 39 | 0.200 |
| 9 | 362 | 286 | 76 | 0.210 |
| 12 | 948 | 728 | 220 | **0.232** |
| 15 | 2583 | 2208 | 375 | 0.145 |
| 18 | 2899 | 2721 | 178 | 0.061 |
| 21 | 1210 | 1169 | 41 | 0.034 |
| 24 | 157 | 148 | 9 | 0.057 |
| 27 | 56 | 56 | 0 | 0.000 |

---

## 2. Q1 — Hazard parameterization: **shared beta + boundary-varying quality block**

```
logit P(X_j == 3 | A_j, x)      = gamma_j + x'delta + x_Q' delta_j     j = 1..9
logit P(R_j == 1 | X_j == 3, x) = alpha_j + x'beta  + x_Q' beta_j      j = 1..8
P(X_j = r | A_j, X_j < 3)       = softmax(psi_j)_r                     r ∈ {0,1,2}
```

`x_Q` is the as-of pitcher-quality block: `exp_o`, `exp_ge12`, `exp_ge15`,
`exp_ge18`, `exp_ge21`. Every other covariate effect is shared across
boundaries. There is **no** `alpha_9` — see §5.

### Why, with the fit comparison

Three-way out-of-sample, paired bootstrap (2000 resamples of test starts) on
mean Brier across the seven market lines, each model given its **own**
inner-split regularization. `P(>M2)` is the bootstrap probability the variant
beats the plain shared-beta model.

| Form | S1 (24→25) | S2 (25→24) | S3 (24+25→26) | passes three-way |
|---|---|---|---|---|
| M2 shared beta | — | — | — | (reference) |
| M3 **fully separate** per-boundary beta | 0.524 | 0.584 | 0.950 | **NO** |
| M4 proportional odds on outs | 1.000 | 0.010 | 0.868 | **NO** |
| M5 continuation-ratio on the outs lattice | 0.000 | 0.138 | 0.366 | **NO** |
| M2x + covariates in the partial-inning shape | 1.000 | 0.010 | 0.812 | **NO** |
| M2i vary `exp_o` only | 1.000 | 0.998 | 0.974 | borderline |
| M2i vary `exp_o, exp_ge18` | 1.000 | 0.998 | 0.994 | **YES** |
| **M2i vary the 5-term quality block** | **1.000** | **1.000** | **0.995** | **YES** |

Reasoning behind each rejection:

- **Fully separate betas (M3) are rejected.** They looked like the winner
  (+0.00150 Brier, bootstrap P=1.000 in S1) until the shared model was given its
  own regularization strength. Under matched, honestly-selected regularization
  M3's advantage collapses to 0.524 / 0.584 / 0.950 — it helps in **no** split
  at the 0.975 level. The original signal was M2 being under-regularized in S1,
  not M3 being more flexible. Boundaries 7/8/9 carry 1559 / 229 / 56 completion
  events; 27 free slopes on 56 observations is not a model.
- **Proportional odds (M4) is rejected** — significantly *worse* in S2
  (P=0.010) while better in S1. That is exactly the "helps in only one split
  direction" pattern CLAUDE.md forbids. It also has no mechanism: it cannot
  answer "will he come out for the 7th", so a known pitch limit
  (`data/manual_pitch_limits.csv`) has nowhere to enter.
- **Continuation-ratio on the outs lattice (M5) is rejected.** Tied-to-worse in
  all three splits (0.000 / 0.138 / 0.366). It spends 27 free thresholds
  re-learning the 3-out periodicity that the inning lattice gets for free, and
  it forces a single covariate effect across intra-inning steps where the
  manager has no decision to make.
- **The quality block genuinely interacts with the boundary.** How much a
  pitcher's own durability history moves the return decision depends on which
  inning the manager is standing at — a large `exp_o` matters far more at the
  6th/7th boundary than at the 2nd, where essentially everyone returns.

### Fitted intercepts (train 2024+2025, lambda = 3)

| j | gamma_j (completion) | alpha_j (return) |
|---|---|---|
| 1 | +4.541 | +4.245 |
| 2 | +3.596 | +4.388 |
| 3 | +3.091 | +3.738 |
| 4 | +2.221 | +2.689 |
| 5 | +1.011 | +1.167 |
| 6 | +0.026 | −0.124 |
| 7 | −0.447 | −1.098 |
| 8 | −0.817 | −0.603 |
| 9 | −0.249 | *(none — structural)* |

---

## 3. Q2 — Mid-inning removal: **per-inning multinomial, no covariates**

34.5% of starts do not end on an inning boundary. Partial innings enter as a
**per-inning multinomial over {0,1,2}**, conditional on `X_j < 3`, with no
covariates.

Decision: **per-inning `{0,1,2}` multinomial**, NOT a within-inning per-batter
hazard, and NOT a single pooled shape.

- *Against a per-batter within-inning hazard*: it would need a batter-level
  state (outs, baserunners, pitch count) that is not knowable before first
  pitch, so it cannot be scored as a pre-game prediction without simulating the
  half-inning. It buys nothing measurable — the {0,1,2} split is only 5,513
  events total and is already well described by 9 free shapes.
- *Against a single pooled shape*: the shape **drifts significantly with the
  inning**. Chi-square homogeneity over j=2..8 gives chi2 = 53.36, dof = 12,
  p = 3.6e-07. Fitting `logit P(X=0 | X ∈ {0,2}) ~ a + b·j` gives
  **b = +0.2147, se = 0.0287, z = +7.48** — late removals are progressively
  more likely to happen before any out is recorded.
- *Against adding covariates* (M2x): measured **worse or neutral** —
  bootstrap P(>M2) = 1.000 / 0.010 / 0.812. Fails the three-way rule. Do not
  add them.

### Measured partial shape

| j | N | P(0 \| X<3) | P(1 \| X<3) | P(2 \| X<3) |
|---|---|---|---|---|
| 1 | 80 | 0.0500 | 0.1875 | 0.7625 |
| 2 | 192 | 0.1458 | 0.3854 | 0.4688 |
| 3 | 256 | 0.1523 | 0.3438 | 0.5039 |
| 4 | 573 | 0.1326 | 0.3595 | 0.5079 |
| 5 | 1413 | 0.1557 | 0.3843 | 0.4600 |
| 6 | 1969 | 0.1905 | 0.3946 | 0.4149 |
| 7 | 844 | 0.2109 | 0.4147 | 0.3744 |
| 8 | 161 | 0.2547 | 0.3975 | 0.3478 |
| 9 | 25 | 0.3600 | 0.3200 | 0.3200 |
| **all** | **5513** | **0.1759** | **0.3855** | **0.4386** |

---

## 4. Q3 — Composition into a PMF over 0..27

Let `S_j = P(A_j)`, `S_1 = 1`. Write `c_j = P(X_j = 3 | A_j, x)`,
`r_j = P(R_j = 1 | X_j = 3, x)`, `q_{j,r} = P(X_j = r | A_j, X_j < 3)`.

```
pmf[0..27] = 0
S = 1
for j = 1..9:
    base = 3*(j-1)
    for r in {0,1,2}:
        pmf[base + r] += S * (1 - c_j) * q_{j,r}      # removed mid-inning
    if j < 9:
        pmf[3*j] += S * c_j * (1 - r_j)               # completed j, did not return
        S        =  S * c_j * r_j                     # carried forward
    else:
        pmf[27]  += S * c_9                           # ceiling
        S         = 0
```

### Normalisation proof

In inning `j` the mass emitted to terminal states is
`S_j(1 - c_j) + S_j c_j (1 - r_j)` and the mass carried forward is
`S_{j+1} = S_j c_j r_j`. Their sum is

```
S_j [ (1 - c_j) + c_j(1 - r_j) + c_j r_j ] = S_j
```

exactly, for any `c_j, r_j ∈ [0,1]`. Summing over `j` telescopes to
`S_1 - S_10`. At `j = 9` the return step is skipped and `S_9 c_9` is emitted to
`outs = 27`, so `S_10 = 0` and the total is `S_1 = 1`. No normalising constant
is ever computed, and no clipping is required.

Note `pmf[3j]` accumulates from two disjoint paths — the `j < 9` branch at
inning `j`, and the `r = 0` term at inning `j+1`. That is the §1 two-path
structure, and it is why `+=` rather than `=` is mandatory.

**Verified numerically** (`models/outs_hazard_proto.py`): across 800 random
parameter draws x 64 rows spanning mild to saturating logit regimes
(scale 0.5 / 1 / 4 / 20), worst `|sum − 1| = 5.55e-16`, zero negative entries,
zero non-finite entries. On real fitted models across all three splits the
worst row-sum error was `4.4e-16`.

---

## 5. Q4 — Where the ceiling and the floor are enforced

Both are **structural, not clipped**.

- **27-out ceiling.** Enforced by the *absence of a parameter*: `alpha` has 8
  entries for 9 innings, so no `R_9` exists and there is no code path that
  advances past inning 9. `S` is zeroed after the ceiling emission. Measured
  justification: `P(R=1 | completed inning 9) = 0.0000` on 56 completions, and
  **zero** starter-inning rows at inning ≥ 10 in 13,170 starts. Do not estimate
  this parameter and do not let a regulariser resurrect it.
- **0 floor.** Enforced by the support itself: the smallest reachable state is
  `base = 0, r = 0`, so `pmf[0] = (1 - c_1) q_{1,0}` exactly. Verified to
  machine precision in the prototype. Measured: 4 starts of 13,170 ended at 0
  outs — small, but nonzero, and the model must not assign it zero mass.
- **Non-negativity.** Every term is a product of probabilities in `[0,1]`, so
  no entry can go negative without a NaN upstream. `_sigmoid` is
  overflow-safe and saturates rather than returning NaN.

---

## 6. Q5 — Reading `P(outs > L)` off the PMF

All lines in this market are half-integers, so the two sides partition the
support exactly and **no PUSH is reachable**:

```
P(outs > L) = sum_{k = ceil(L)}^{27} pmf[k]
```

| L | over-side states |
|---|---|
| 13.5 | 14..27 |
| 14.5 | 15..27 |
| 15.5 | 16..27 |
| 16.5 | 17..27 |
| 17.5 | 18..27 |
| 18.5 | 19..27 |
| 19.5 | 20..27 |

`p_over()` **raises on a whole-number line** rather than silently guessing.
If DraftKings ever posts one, it needs the PUSH branch from CLAUDE.md's
prop-grading rules — stake returned, not a loss — and that is a separate change.

The half-integer lines straddle the lattice asymmetrically, which is the whole
edge: 15.5 and 16.5 sit either side of the 0.196 spike at 15, and 17.5/18.5
straddle the 0.220 spike at 18. Getting `P(18)` right is worth far more than
getting the mean right.

---

## 7. Q6 — Regularization and shrinkage

**Shrink the slopes, not the intercepts, and never toward a monotone trend in j.**

### Intercepts `alpha_j` — leave free

Even the thinnest estimated boundary is well determined:

| j | n completed | p_return | logit | se(logit) |
|---|---|---|---|---|
| 5 | 9301 | 0.7626 | +1.167 | 0.024 |
| 6 | 5124 | 0.4690 | −0.124 | 0.028 |
| 7 | 1559 | 0.2502 | −1.098 | 0.059 |
| 8 | 229 | 0.3537 | −0.603 | 0.138 |

The tempting move — shrink `alpha_8` toward the decreasing trend of
`alpha_5..alpha_7` — is **wrong**. The `alpha_8 > alpha_7` uptick is real:

```
logit(alpha_8) − logit(alpha_7) = +0.4950,  se = 0.1501,  z = +3.30,  p = 0.00097
```

*Inference (not measured):* this is survivorship — a pitcher who has completed
8 innings is an efficient outlier that night, and the complete game is a live
goal, so the manager's decision rule genuinely inverts. A monotone or smooth
`f(j)` prior would erase a t=3.3 effect. **Do not impose one.**

### Slopes — partial pooling toward the shared effect

The thin quantity is the *slope*, not the intercept. `delta_j` and `beta_j`
are ridge-penalised toward **zero deviation from the shared `delta` / `beta`**
(that is exactly what the interaction parameterisation does: shared main effect
plus a penalised per-boundary deviation). This is the partial-pooling middle
ground between M2 and M3, and it is the only form that passed the three-way
rule.

### Partial-inning shape at thin `j`

`j = 9` has 25 partial events and `j = 8` has 161. Shrink `psi_j` toward the
**fitted linear-in-j trend** (`b = +0.2147, se = 0.0287, z = +7.48`), not toward
the pooled constant — the shape demonstrably drifts (§3).

### Selecting the penalty strength — on Brier, not NLL

**This matters more than the choice of form.** Selecting `lambda` on inner-split
log-likelihood picked lambda=1 for S1 and produced Brier 0.18389; selecting it
on inner-split Brier at the seven market lines picked lambda=30 and produced
**0.18143** — a larger gain than any difference between model forms. NLL
selection is also unstable for richer variants (it chose lambda=0.3 for the
5-term variant in S1, which then lost to the plain shared model).

Rule: **select the penalty on the decision metric — mean Brier across the seven
market lines — using a temporal split inside the training years (first 70% of
training dates fit, last 30% score).** Never touch the test years.
Grid: `{0.3, 1, 3, 10, 30}`. Intercepts are never penalised.

---

## 8. Q7 — Should the first inning be special? **No.**

Beyond its own free `gamma_1` / `alpha_1` / `psi_1` intercepts, inning 1 needs
no special treatment. Measured:

- `P(X_1 < 3) = 0.0061` — the **minimum** of a monotonically increasing
  sequence (0.0061, 0.0149, 0.0204, 0.0477, 0.1319, 0.2776, ...). Inning 1 is
  the *least* eventful inning, not a special hazard spike.
- **Openers do not show up as a 0-out spike.** All 13 starts on ≤2 days rest
  recorded outs of `[3,3,3,3,3,3,3,3,3,4,4,6,6]` — every one of them at 3+
  outs. They are 1–2 inning outings, already captured by the `rest ≤ 2` bucket
  acting through the ordinary inning-2/3 hazards.
- Only 4 starts of 13,170 ended at 0 outs, and 80 at ≤2 outs. That tail is an
  injury/ejection process, and the model already gives it mass through
  `(1 − c_1) q_{1,0}`. Zero-inflation would be fitting 4 observations.

*Inference:* if MLB opener usage returns to 2018–2019 levels this conclusion
should be re-measured; it is a fact about 2024–2026, not a law.

---

## 9. Features

Tier-1, all strictly as-of via `features/asof.py` discipline (expanding,
cumsum-minus-current, per-season reset). Values reproduced this session:

| # | Feature | Encoding | Check |
|---|---|---|---|
| 1 | `exp_o` | expanding mean outs, prior starts this season | r = +0.4145 with outs |
| 1b | `exp_ge{12,15,18,21}` | his own prior stop-rates (the quality block) | boundary-varying |
| 2 | `days_rest` | buckets {≤2,3,4,5,6,7,8–10,11–20,21+,unknown} | ≤2 → 3.62 outs; 5–6 → 16.1 |
| 3 | `career_start_number` | `min(n,10)` + `is_debut` | debut 10.79 vs 16.11 at 10+ |
| 4 | `season_start_number` | `min(n,8)`, **interacted** with (3) | opener 12.56 vs 16.25 at 8+ |
| 5 | `is_home` | binary | +0.449 outs |
| 6 | league regime | expanding league-mean outs through prior day | 15.66 → 15.57 → 15.26 |
| 7 | opponent as-of OBP | team level, ≥20 prior opponent games | r = −0.042 |
| 8 | recent pitch budget | prior-5 mean pitch count — **pick one only** | r(exp_o, p5_p) = 0.805 |

### Two traps found this session

- **`days_rest` must be computed ACROSS seasons.** With a per-season reset the
  `unknown` bucket becomes *identical* to `season_start_number == 0` (589 of 589
  rows) — a perfect collinearity that fails Gate 4. Computed across seasons, the
  offseason gap correctly lands in `21+` and `unknown` is reserved for genuine
  first-career appearances.
- **`career_n == 0` left edge.** 166 rows are 2023 debutants caught at the
  cache's left edge. Excluded from `is_debut`; including them dilutes the effect.

**Do not include** (measured null elsewhere, confirmed here as not needed):
opponent strikeout rate, bullpen fatigue, park factors, temperature.
**Blacklisted**: `pitcher_days_until_next_game`, `batter_days_until_next_game`,
and every same-game outcome (pitches, bf, tto_max, so, max_inning, runs).

---

## 10. Measured performance

Three-way out-of-sample. Baseline **M0** is the honest one from
`docs/FACTORS.md` C1 — the pitcher's own strictly as-of empirical outs
*distribution*, shrunk toward the as-of league PMF. Penalty selected on
inner-split Brier.

| Split | lambda | NLL | Brier | M0 Brier | skill | P(>M0) |
|---|---|---|---|---|---|---|
| train 2024 → test 2025 | 30 | 2.4553 | 0.18119 | 0.18907 | **+4.17%** | 1.000 |
| train 2025 → test 2024 | 3 | 2.4705 | 0.18449 | 0.19198 | **+3.90%** | 1.000 |
| train 2024+25 → test 2026 | 3 | 2.4587 | 0.17607 | 0.18903 | **+6.86%** | 1.000 |

Per-line Brier skill vs M0 (%):

| Split | 13.5 | 14.5 | 15.5 | 16.5 | 17.5 | 18.5 | 19.5 |
|---|---|---|---|---|---|---|---|
| 24→25 | +5.9 | +4.8 | +5.1 | +4.7 | +3.4 | +2.0 | +1.5 |
| 25→24 | +5.6 | +4.9 | +4.4 | +3.7 | +3.6 | +2.0 | +2.1 |
| 24+25→26 | +10.3 | +8.7 | +7.3 | +7.3 | +5.5 | +3.1 | +3.1 |

**On the prior review's "+7.69% on the 2026 decision split":** partially
reproduced. This session measures **+6.86%** on that split for the best form
that survives the three-way rule (and +6.66% for plain shared-beta). The claim
is directionally right and the same order of magnitude, but **+7.69% was not
reproduced** — treat 6.9% as the number until the earlier baseline definition is
recovered. Baseline definition (shrinkage constant, career vs season history) is
the likely source of the gap.

Also worth recording: the league as-of PMF alone scores **worse** than M0 on
Brier (−4.1% / −4.7% / −4.6%) while scoring *better* on NLL. The pitcher's own
distribution is what carries the decision-relevant signal at the lines. Any
future baseline comparison must use Brier at the lines, not NLL.

### Gate 5 — calibration (2026 split, 10 equal-count bins)

| Line | Brier | ECE | base rate |
|---|---|---|---|
| 13.5 | 0.16401 | 0.0173 | 0.738 |
| 14.5 | 0.18451 | 0.0185 | 0.693 |
| 15.5 | 0.22177 | 0.0192 | 0.499 |
| 16.5 | 0.21880 | 0.0244 | 0.434 |
| 17.5 | 0.21026 | 0.0264 | 0.371 |
| 18.5 | 0.12519 | 0.0155 | 0.158 |
| 19.5 | 0.10794 | 0.0140 | 0.132 |

Calibration is usable but shows a mild S-shape — the model under-predicts in
bins 6–7 by 4–6 points at 15.5 and 17.5. **Route the output through the
existing post-hoc calibrator (`models/calibration.py`) before pricing.** Do not
bet the raw hazard output.

---

## 11. Implementation checklist

1. Build the per-start outs table by the §0 reconstruction. Assert 99.5%+ of
   half-innings sum to 3 and exactly `2 x n_games` rows, or fail loudly.
2. Explode to per-(start, inning) states `A_j`, `X_j`, `R_j`.
3. Build the as-of feature matrix; honour both traps in §9.
4. Fit three components: completion logit, return logit, partial multinomial.
   Intercepts unpenalised; quality-block deviations ridge-penalised.
5. Select lambda on inner-split Brier at the seven lines. Never on the test years.
6. Compose with the §4 recursion. **Assert `|sum − 1| < 1e-10` on every row.**
7. Read `P(outs > L)` per §6; refuse whole-number lines.
8. Pass through `models/calibration.py`, then into the existing edge / staking
   path (`models/edge.py`, `models/staking.py`) — `MAX_STAKE_UNITS` and the
   vig-adjusted edge threshold are unchanged by this model.
9. Grading: the prop rules in CLAUDE.md are unchanged — VOID if the listed
   starter throws no pitch, VOID if the game is called before he is removed.

## 12. Open items for whoever ships this

- `CHANGELOG.md` / `ROADMAP.md` / `docs/GATES.md` entries are **not** written —
  this session produced a spec and a prototype, not a shipped model.
- The full gate gauntlet has not been run on the individual features under this
  new target; §9 reproduces effect sizes but Gates 1–5 were run for the
  strikeouts target, not for outs.
- `exp_o` measures r = +0.4145 here against the +0.328 quoted in the brief;
  the definitions differ somewhere and it should be reconciled before the
  feature is written into `features/`.
