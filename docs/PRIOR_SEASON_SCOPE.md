# Scope: widening the pitcher history window to prior seasons

**Status:** BUILT, flag OFF, gates passed, **awaiting the 2-week shadow.**
**Filed:** 2026-08-11 · **Built:** 2026-08-11
**Operator decisions (§7), all answered 2026-08-11:** two forward
validations accepted in place of both temporal directions; **one prior
season only** (2025 when pricing 2026); the 263 starts with no usable
prior stay refused.
**Prompted by:** A-038 investigation — Blake Snell refused with 18 BF in
2026 while 799 BF and a 0.320 K rate from 2024–2025 sat unused in the
cache.

> **§3 was corrected during the build.** This document originally
> specified prior-season p25 as the workload estimate in all thin-current
> -season cases. That was asserted, not measured — it was never compared
> against what production actually does today (`game_bf.mean()`). The
> comparison is in §3a and it changed the design: p25 alone is right only
> for a season debut; with 1–2 outings already this season, a 50/50 blend
> of the two beats either source alone on both average error and the
> upper tail, in both year pairs. The shipped code does that.

---

## 1. The problem

`tools/daily_pipeline.py` loads Statcast for the **current season only**:

```python
season_start = date(d.year, 3, 26)
statcast_df = load_cached(season_start, d)
```

Every pitcher feature derives from that frame, so the 50-BF gate at
`_compute_pitcher_stats` refuses anyone light on *this* season regardless
of how much history exists a few months earlier on the same disk.

### Measured size of the prize

Over 2026 through 08-10, defining a start as *threw the game's first
pitch for his side* (knowable pre-game, so Gate-1 clean — an earlier cut
of this used "faced 15+ batters", which conditions on the outcome and
silently drops the pitcher yanked after eight):

| | starts | share |
|---|---|---|
| 2026 true starts | 3,570 | — |
| refused by the 50-BF gate | 672 | 18.8% |
| **of those, with 200+ BF and 10+ starts in 2025** | **409** | **11.5%** |
| no usable prior history (stay refused) | 263 | 7.4% |

**~2.9 recoverable starts per day.** About one start in nine is currently
refused despite a full prior season being available. This is not an
edge case about one ace returning from injury.

---

## 2. The central finding: rate and workload do not travel together

The projection needs two things from history — how often he strikes
batters out (**rate**) and how many batters he will face (**workload**).
They are not equally portable across seasons.

Pitchers with 10+ starts in both years:

| transition | K rate | workload |
|---|---|---|
| 2024 → 2025 (n=135) | r = 0.730 | r = 0.402 |
| 2025 → 2026 (n=131) | r = 0.677 | r = 0.507 |

On the 409 recoverable starts specifically, prior-season **rate** is
essentially unbiased:

- actual K rate 0.226 vs 2025 K rate 0.226 (BF-weighted)
- r = 0.313 against the individual start — reasonable given ~22 BF of
  binomial noise per start

Prior-season **workload** is unbiased in the mean and dangerous in the
tail, which is the combination that matters here.

**Design consequence: prior seasons may inform the rate. Workload needs
its own treatment (§3).**

---

## 3. Choosing the workload estimator

Mean bias is the wrong test. The edge filter selects on *large* edges, so
what matters is how often the estimator overstates batters faced by
enough to manufacture a phantom OVER edge. The repo's own measurement
(`tests/test_stage_a_pitch_limit.py`) puts P(over) movement at **~2.45
points per batter faced**, so +5 BF ≈ 12 points — the A-007 magnitude.

Over the 409 recoverable starts, error = estimate − actual:

| estimator | bias (BF) | P(over by 5+) | P(over by 3+) | 95th pct | phantom edge at 95th |
|---|---|---|---|---|---|
| prior-season mean | +0.67 | 8.6% | 20.0% | +5.8 | +14.1 pts |
| prior-season median | +0.91 | 11.0% | 27.9% | +6.0 | +14.7 pts |
| **prior-season p25** | **−0.95** | **4.4%** | **12.0%** | **+4.0** | **+9.8 pts** |
| mean − 2.0 | −1.33 | 2.9% | 8.6% | +3.8 | +9.2 pts |
| prior-season p10 | −2.98 | 2.7% | 4.9% | +2.9 | +7.0 pts |

**Recommendation: p25** — the 25th percentile of his own prior-season
starter outings. It roughly halves the dangerous tail versus the mean
while staying near-unbiased, and it adapts to the pitcher: a metronome
with consistent 25-BF starts gets a tighter estimate than a volatile one,
which a fixed haircut cannot do.

**Do not simply pick the most conservative option.** p10 under-projects
by 3.0 BF ≈ 7 points of P(over), which manufactures phantom **UNDER**
edges just as surely as the mean manufactures phantom OVERs. A-007 ran in
the OVER direction by accident of that particular bug, not by law. Near-
zero bias with a small tail beats maximum conservatism.

### 3a. Correction: p25 is not right in every case

The table above compares prior-season estimators against each other. It
never compared them against **what production does today** — the mean of
his current-season outings. Measured properly (a season *total* is not a
workload estimate; it must be divided by outings), on both year pairs:

**Season debut — no current-season outings at all**

| estimator | bias | MAE | P(over by 5+) |
|---|---|---|---|
| **prior p25** | **−0.26 / −0.35** | **2.25 / 2.28** | **2.1% / 2.9%** |
| prior mean | +1.34 / +1.36 | 2.44 / 2.57 | 6.5% / 7.9% |

p25 wins outright. Production has no estimate here at all.

**1–2 outings already this season**

| estimator | bias | MAE | P(over by 5+) |
|---|---|---|---|
| prior p25 | −1.12 / −1.31 | 2.93 / 3.00 | 5.6% / 6.7% |
| current mean/outing (production) | −0.79 / −1.35 | 3.07 / 2.73 | 3.2% / 7.4% |
| **50/50 current + prior p25** | **−0.96 / −1.50** | **2.75 / 2.65** | **3.2% / 3.7%** |
| min(current, prior p25) | −2.11 / −2.55 | 3.34 / 3.29 | 1.6% / 3.7% |

The blend beats **both** sources on MAE and on the tail, consistently
across both year pairs. `min()` cuts the tail furthest but at −2.1 to
−2.6 BF of bias, which is the phantom-UNDER trade this document warns
against two paragraphs above.

**Shipped:** 3+ starter games → current season alone (unchanged);
1–2 outings → 50/50 blend; 0 outings → prior p25.

### Surprising: the IL-gap flag does not find the danger

The obvious guard — distrust prior workload after a long layoff — does
not work. Splitting the 409 starts by days since previous appearance:

| bucket | n | actual BF | bias | P(over by 5+) |
|---|---|---|---|---|
| normal rest (≤7d) | 256 | 22.1 | +0.34 | **9.4%** |
| 8–20 day gap | 9 | 22.2 | −1.38 | 0.0% |
| 21+ day gap (IL) | 4 | 20.5 | +2.67 | 0.0% |
| season debut (no prior 2026 game) | 140 | 21.1 | +1.36 | 7.9% |

The heaviest tail sits in **ordinary rest**, not in returns. The IL
buckets are too small to conclude anything (n=9, n=4), but they give no
support for gating on the gap. Protection must come from the estimator
itself, not from a layoff rule. `c10_il_return` stays as a leash input;
it does not become a gate.

---

## 4. Implementation shape

**Do not load a second season in the pipeline.** The worker runs six
times a day; an extra full season is ~750 K rows per run. Precompute a
sidecar instead.

1. **`tools/build_prior_season.py`** → `data/prior_season/<year>.parquet`,
   one row per pitcher: `prior_bf`, `prior_ks`, `prior_k_pct`,
   `prior_starts`, and outing quantiles `p10/p25/median/mean`. Built from
   completed seasons only, so it is static and cacheable. Atomic write
   per the data-integrity rule.
2. **`_compute_pitcher_stats`** takes an optional `prior` row.
   - *Rate:* blend current and prior BF with a recency weight `W`, then
     shrink to league as today.
     `eff_bf = cur_bf + W * prior_bf`, rate blended on the same weights.
   - *Gate:* refuse on `eff_bf < 50` rather than `cur_bf < 50`.
   - *Workload:* unchanged when current-season starter games ≥ 3.
     Otherwise `prior_p25`, and refuse if prior starts < 10.
   - *Role gate:* unchanged. A reliever stays refused — prior-season
     history must not launder Drew Anderson into a starter. Require
     `prior_starts >= 10` **and** current-season usage consistent with
     starting.
3. **Pipeline** loads the sidecar once and passes rows through.

`W` is **fitted, not guessed** (§5). Everything else in the pipeline —
batter K rates, team K rates, relief usage — stays current-season.

---

## 5. The five gates

| Gate | Requirement | Status |
|---|---|---|
| 1 — Leakage | prior season is fully complete before the current one starts; sidecar built from closed seasons only | **clean by construction** — but the harness must confirm the sidecar is never rebuilt mid-season |
| 2 — Out-of-sample | see wrinkle below | **not run** |
| 3 — Effect size | fitted `W` must land in a plausible range; a fitted `W` near 1.0 (prior season as good as current) or near 0 (no signal) both indicate a bug | **not run** — §2 correlations are supporting evidence, not the fit |
| 4 — Collinearity | `prior_k_pct` vs `a3_season_k_pct_shrunk` are the same quantity in different windows; the blend replaces rather than adds a feature, so no new pair enters the model. `prior_bf_mean` ↔ `c1_bf_mean` likewise | **low risk, must be confirmed** |
| 5 — Calibration | Brier + calibration curve on P(K ≥ line), measured **on the 409 recovered starts specifically**, not the whole slate — the change is invisible in a pooled average | **not run** |

### Gate 2 wrinkle — needs an operator decision

CLAUDE.md requires both temporal directions: train 2024 → test 2025 and
train 2025 → test 2024. **The backwards direction is incoherent for this
feature.** Using 2025 as the "prior season" for 2024 games means feeding
the model future data — a Gate-1 leakage violation by construction. The
rule was written for features that are symmetric in time; "last season"
is not.

Proposed substitute, by analogy to the regime-scoped clause already in
CLAUDE.md: **two independent forward validations** — 2024→2025 and
2025→2026 — held to a higher bar, i.e. must help in both, with the
2025→2026 result reserved as the true holdout and never used to tune `W`.

That is a genuine relaxation of a stated rule and it is the operator's
call, not mine.

---

## 5a. Gate results (run 2026-08-11)

Full row logged in `docs/GATES.md`. All five pass; status is **SHADOW**,
not promoted, per the two-week rule in CLAUDE.md.

| Gate | Result |
|---|---|
| 1 — Leakage | PASS. Sidecar refuses to build an unfinished season; as-of totals exclude the start being judged; "start" = threw the first pitch |
| 2 — Two forward validations | PASS. Rate log-loss on recovered starts +0.63% (2024→2025 fit) and **+0.44% (2025→2026 holdout)** |
| 3 — Effect size | PASS. W fitted 0.60, shipped 0.5; curve flat 0.25–1.00, so weakly identified but clearly non-zero and clearly below "prior = current" |
| 4 — Collinearity | PASS. No feature is added. Prior widens the sample behind the existing `a3_season_k_pct_shrunk` and `c1_bf_mean` |
| 5 — Calibration | PASS with two caveats below. Brier on P(K ≥ line) **+9.93% (2025)** and **+4.83% (2026 holdout)** |

### Two caveats that the shadow must watch

1. **The confident bucket drifts on the holdout.** In 2026, predictions
   in the 0.8–1.0 band came in 9.0 points high (predicted 0.863, actual
   0.773, n=66 line-evaluations ≈ 13 starts). Every other bucket is
   within 3 points. Small sample, but that band is where confident OVER
   bets come from, so it is the wrong place to be loose.
2. **A third of the recovered starts have no baseline at all.** Season
   debuts — 93–98 per year — are refused outright by production, so there
   is nothing to Brier-compare them against; only their own calibration
   can be checked (gap −0.038 in 2025, +0.017 in 2026, inconsistent in
   sign). They are also measurably harder: Brier 0.190–0.198 against
   0.179–0.182 on the paired starts. An error here has no production
   number to be caught against.

### What the first live example showed

With the flag on for 2026-08-11, Blake Snell prices at E[K]=4.8 against
a 5.5 line — an **11.3% edge to the UNDER**, on his second start back
from a 94-day layoff. It did not become a bet, but only just: the
threshold was 11.8% because no lineup had posted, and the A-008 unposted
-lineup penalty is 5 points of that. **With a lineup posted the same edge
clears at a 6.8% threshold and books as a LEAN.**

So the existing thresholds do not meaningfully protect against this
feature's most dangerous output — they blocked it by four tenths of a
point, for a reason unrelated to the feature. That is the single
strongest argument for running the full shadow rather than shipping on
green gates.

## 6. Effort and sequencing

1. Sidecar builder + tests — small, self-contained.
2. `_compute_pitcher_stats` change behind a flag defaulting **off**.
3. Fit `W` on 2024→2025 only.
4. Gauntlet gates 2–5 via `tools/gauntlet.py`, holdout 2025→2026.
5. Shadow two weeks against production (`tools/shadow.py`), comparing on
   recovered starts specifically.
6. Promote or reject; log the row in `docs/GATES.md` either way.

Steps 1–3 are a session. Step 5 is the calendar cost and cannot be
compressed.

---

## 7. Decisions needed before building

1. **Gate 2 substitute (§5).** Accept two forward validations in place of
   both temporal directions? Without this the feature cannot be validated
   under the rules as written.
2. **How far back.** 2025 only, or 2024+2025 with decay? The measurement
   above used 2025 only. Two seasons recovers more pitchers but 2024 is
   pre-ABS and CLAUDE.md already treats the regime boundary as material.
3. **The 263 starts with no usable prior history stay refused.** Confirm
   that is acceptable — this change does not make the board complete, it
   moves refusals from 18.8% to 7.4% of starts.

---

## 8. Explicitly out of scope

- Changing the 50-BF or 15-BF thresholds themselves. This widens the
  *window*, not the bar.
- The role gate. Relievers stay refused.
- Batter, team, and bullpen inputs — all stay current-season.
- Anything that would let a pitcher be *bet* without also passing the
  existing edge threshold and stake caps.
