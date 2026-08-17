
# Changelog

## 2026-08-16 - Bound-pinning sweep: the calibrator was serving certainty (A-043)

Swept every fitted artifact after `alpha = exp(-5)` surfaced in A-042.
Three findings, one of which was live on the board.

    OPTIMIZER BOUND  stage_a_*.pkl   alpha = exactly exp(-5)   (A-042)
    GRID EDGE        outs_hazard.pkl lambda = max(LAMBDA_GRID)
    SATURATION       calibrator.pkl  top knot y = 1.0 @ raw 0.9404

Stage B is clean — fit unbounded, no bound to sit on.

**The calibrator was manufacturing certainty.** The RAW model never
exceeded 0.9959 across any 2026 slate, yet **53 ladder rungs were served
at model_prob == 1.0000**, produced by interpolating toward a saturated
PAV knot. Of the 46 with settled outcomes, **5 LOST (10.9%)** — Drew
Anderson needed K>=1 and recorded 0; Davis Martin needed K>=2 and
recorded 1, twice.

Every failure is a low milestone killed by a short outing, which is
exactly the tail Stage A cannot produce (A-042). The workload model
hides disaster starts, the calibrator never sees them fail, so it prices
them as impossible. The two audit items are one disease from both ends.

p=1.0 makes log-loss infinite and Kelly size unbounded. Production
survived on two guards unrelated to probability hygiene: MAX_STAKE_UNITS
capping the stake, and the 50/50 market blend holding the served number
to 0.9688.

Fixed with `PROB_EPS = 1e-3` clamping `IsotonicCalibrator.predict()`
away from {0, 1}, applied on the way OUT so the shipped artifact is safe
without a refit. Blast radius measured before shipping: 61 of 1001 raw
values move at all, max change 0.00100, none above 0.01, mid-range
bit-identical. A guard, not a recalibration — the top bin is still
genuinely miscalibrated (A-041, open).

NOT fixed: `outs_hazard` lambda sits at the top of its grid and the
per-lambda CV scores are printed but never persisted, so the curve
cannot be inspected after the fact. That model is a research artifact
with Gate 5 unpassed and touches no bet today, so it is filed rather
than rushed.

New `tools/audit_param_bounds.py` (exit 1 on any finding) imports each
bound from the module that declares it rather than copying it, and
checks what the calibrator SERVES rather than what it stores. 8 tests in
`tests/test_param_bounds.py`; 172 pass.

## 2026-08-16 - Workload model had the wrong shape: hook mixture, flag OFF (A-042)

The mechanism under A-041's OVER bias, fixed at the source. Built,
gated, and **not promoted** — `USE_HOOK_MIXTURE = False` pending the
2-week shadow.

**The defect.** Batters faced is left-skewed; the negative binomial is
right-skewed. Measured on 13,170 starts: empirical skew **-1.58** vs the
fitted model's **+0.24**.

    threshold   actual   NB model        ratio
    BF <=  8     3.08%      0.11%   27.6x too rare
    BF <= 10     4.07%      0.58%    7.0x too rare
    BF <= 18    14.52%     25.51%    0.6x (too COMMON)

A disaster start settles every OVER as a loss and can never settle an
UNDER that way, so pricing a 1-in-32 event at 1-in-900 inflates P(over)
on every pitcher — in one direction. That is A-041's +5.0pp lean.

**A better-fitted NB cannot fix it, and the pickle already said so.**
`alpha` = exactly `exp(-5)`, the lower bound of `log_alpha` in the fit:
the optimizer wanted LESS dispersion and hit the wall. No NB is
left-skewed at any alpha. The process is two-component.

**The fix** is a hook mixture in `models/stage_a_bf.py`. The conditional
mean is preserved by re-centring the normal arm — live mean BF error is
+0.00 over 264 starts, and a change that dragged the mean down would
trade a tail bias for a mean bias while still looking like progress.

**Gates** (`tools/gate_hook_mixture.py`):

    train      test    d(logLik)   tail err          pi   mu_short
    2024       2025     +0.0689   0.0275 -> 0.0141  0.0233   5.96
    2025       2024     +0.0798   0.0292 -> 0.0179  0.0195   6.02
    2024+2025  2026     +0.1234   0.0410 -> 0.0287  0.0213   5.99

Gate 2 passes in both temporal directions and forward. Gate 3 passes on
the agreement of three disjoint fits (pi 0.0195-0.0233, mu_short
5.96-6.02). Gate 5 is PARTIAL — tail calibration improves every split,
but P(K >= line) needs the full compound path, which is what the shadow
is for.

**Two bugs caught in the analysis before they became results**, both by
an impossible number: the first fit drove alpha to 0 and reported a mean
log-likelihood of +4.67 (log p cannot exceed 0) with a "tail error" of
331; then single-start L-BFGS-B collapsed two splits onto the boundary
and reported a dead heat. The fit is now multi-start and `_check()`
refuses any positive log-pmf.

**Expected effect, recorded before the shadow:** ~1-2 points off P(over),
against A-041's 5.0-point lean. A partial correction. A shadow showing
it fully closed should be treated as suspicious.

164 tests pass; 9 new in `tests/test_hook_mixture.py`, including that
the mean tracks the model being replaced and that a pitch-limited start
cannot produce a negative component mean.

## 2026-08-16 - Scored against the closing line: the model loses (A-041)

Operator asked to score the backtest against closing odds. **That cannot
be done**, and the reason is an open audit item: the backtest spans
2026-04-11..08-04 and closing captures begin 08-05, so the overlap is a
single OPENING snapshot. Historical prop lines were never sourced
(A-002, now escalated from a Phase 3 nicety to the binding constraint).

Scored instead on the largest window that has prices, via new
`tools/score_vs_market.py`. Model probabilities are reconstructed
exactly as production computes them — `k_dist` -> raw P(K > line) ->
isotonic calibrator -> blend at MODEL_TRUST_WEIGHT — so it is visible
which stage helps.

    PRIMARY  262 starts, posted line, two-sided (exact no-vig), independent
      market 0.2526   raw 0.2646   cal 0.2664   blend 0.2575
      vs market:  raw +0.01195 +/- 0.00569 (z=+2.10)  WORSE
                  cal +0.01376 +/- 0.00578 (z=+2.38)  WORSE
                blend +0.00486 +/- 0.00283 (z=+1.72)  not significant

    LADDER   1,956 alt milestones, same 262 starts, CLUSTERED by start
      vs market:  raw +0.00625 +/- 0.00265 (z=+2.36)  WORSE
                  cal +0.00704 +/- 0.00276 (z=+2.55)  WORSE
                blend +0.00237 +/- 0.00133 (z=+1.79)  not significant

Two samples, one conclusion: the model is significantly worse than the
closing line, and the production calibrator makes it worse still in
every case. The only configuration that escapes significance is the
50/50 blend, and only because it is half market — it is never better.

**The ladder de-vig caught its own bug.** The first pass normalised each
ladder's implied PMF, but books post ladders truncated (K>=3 upward), so
that sum is ~P(K>=3) not 1. It reported a median overround of 0.946 — a
book with negative vig — and flipped the ladder verdict from WORSE
(z=+2.36) to indistinguishable (z=+0.03). Replaced with proportional
de-vig by each start's own two-sided margin (median 1.060, a 6% hold),
which is an assumption and is labelled as one in the output.

Scope, stated plainly: 262 starts over 11 days is enough to refuse a
promotion and to stop betting. It is NOT enough to conclude the model
has no edge in general. At ~25 starts/day, 1,000 market-scored starts is
about 40 days away, and nothing shortens that except buying historical
lines.

9 tests in `tests/test_market_scoring.py`, including that de-vig can
only LOWER a probability, that clustered SEs exceed naive ones, and that
a zero-variance paired difference fails closed to z=0. 155 tests pass.

## 2026-08-16 - Recalibration measured, and refused (A-041 amended)

Operator asked for the calibrator refit. Built it, measured it, and it
is the wrong lever — so it is NOT promoted. `models/calibrator.pkl` is
untouched.

**The calibrator was already fit on 2026.** `backtest_predictions.csv`
is 2026-04-11..2026-08-04, 18,798 rows, and `fit_calibrator.py` fits on
exactly that. On it the model is well calibrated in every probability
band (gaps +0.008 to +0.030), including 2,494 rows between 0.55 and
0.75 where bets actually live. At 0.65-0.70 the backtest actual is
0.705; the live sample says 0.333 in the same band. One p -> p map
cannot be responsible for both numbers.

**The real defect is conditional on the market:**

    model vs market      n    model pred   actual      gap
    much UNDER          41        0.350     0.488    +0.138
    AGREES              79        0.493     0.506    +0.014
    much OVER           26        0.638     0.308    -0.331

Where the model agrees with the book it is calibrated to 1.4 points.
Where it disagrees it is wrong in the direction of the disagreement,
and worse the further it goes — adverse selection, and the edge filter
selects precisely those rows. A univariate calibrator cannot express
this: the same probability is calibrated in one column and inverted in
the other.

**The model does not beat the market.** Paired over 264 live rows,
blended minus market +0.00531 +/- 0.00276 (z=+1.92); standalone
calibrated minus market +0.01444 +/- 0.00562 (z=+2.57). Sweeping the
market trust weight on a within-2026 time split, held-out Brier rises
monotonically with model weight: w=0.0 -> 0.2489, w=0.5 (production) ->
0.2516, w=1.0 -> 0.2582.

**The refit itself** improves held-out Brier 0.2516 -> 0.2483 and is not
significant (z=-0.89, 137 held-out rows).

**Root cause of the backtest/live gap:** `backtest_predictions.csv` has
no odds columns. The model has only ever been scored against a NAIVE
baseline, never against the market. Beating naive on a synthetic
3.5-8.5 line grid says nothing about beating a book at the one posted
line — the same omission already recorded for the outs model.

New `tools/recalibrate_live.py` re-derives all of this on demand and
refuses to promote (`promotion_blockers`: sample floor 300 held-out
rows, |z|>1.96 against production, and an outright block when the model
is significantly worse than the market). 7 tests in
`tests/test_recalibration_gate.py`, including that the gate can open —
a gate that never opens is a wall.

## 2026-08-16 - Worker wedged 27h (A-040); model's confident OVERs are inverted (A-041)

Operator: "theres been so many failed runs and issues. picks arent being
graded. i think the model itself is wrong." Three claims, three
different answers.

**Failed runs — true, one cause.** The Railway worker stopped pulling at
2026-08-15 12:32 ET and served a board generated 08-15 09:33 ET for 27
hours. `sync_repo()` recorded a failed fetch and moved on; nothing
retried and nothing cleared the wedge, so the first failure was terminal
for the life of the container. CI was healthy the whole time (12
commits/day, correct boards for both days) — the worker could not
RECEIVE the work. Worker commits: 285 (08-14) -> 89 (08-15) -> 0 (08-16).

Ruled out by measuring rather than guessing: disk 1.21 GB and growing,
repo 38 MB, token valid 23 more days.

A failed pull now clears abandoned git lock files and retries once,
recording `last_pull.recovered` so a limping container is
distinguishable from a healthy one. Lock removal is age-gated at 600s —
deleting a lock a live git still holds turns a stall into corruption.
The exact wedge is unconfirmed (Railway keeps logs only for the latest
deployment, and all of them are SKIPPED by `watchPatterns` design), so
the fix targets any transient failure, not one guessed cause.

**The alarm fired; nobody was told.** `tools/watchdog.py` caught this
precisely and exits 1, and the CI step has no `continue-on-error` —
every CI run since 08-15 was RED. Monitoring was not the gap. Also
`/health` reported `can_push_to_git: true` throughout, because that is
computed once at boot; `git.checked` still read 2026-08-13.

**Picks not being graded — no.** 12 of 12 graded, 0 pending, and the
worker's own reconcile agrees. No NEW picks since 08-14 because nothing
clears the vig gate: best edge 8.8/10.2/10.4/9.0% against a ~13%
threshold on 08-13..08-16. A frozen dashboard plus no new picks reads
exactly like broken grading.

**The model — the record is noise, the calibration is not.** 4W-8L on 12
bets proves nothing (~19% on a fair coin). But across 264 scored starts
the top confidence bin is inverted: stated 65.4% OVER, actual 33.3%
(11/33, z = -3.9). Live Brier excess +0.0225 vs backtest -0.0006, band
+/-0.0136, positive on 9 of 11 days. That is the bin the edge filter
selects from — A-007's shape — and the bets show it: OVER 2W-6L, UNDER
2W-2L, every OVER from the 0.62-0.94 range.

Mechanism is workload, not strikeout skill: the K estimate is unbiased
(+0.02 K) but only 61.4% of starts land within 3 batters faced and 5.3%
come in badly short (Davis Martin 23.1 projected, 6.0 actual). An early
hook kills an OVER and never an UNDER. The backtest already showed
positive excess at lines 5.5+ and it was read past.

Filed as **A-041, open**. No bets until the calibrator is refit — which
the edge gate is already enforcing on its own.

## 2026-08-13 - The board sat on yesterday, late games stuck "IN GAME" (A-039)

Operator: "the dashboard still has yesterdays games, and some of them
still say in game." Three independent causes, each sufficient on its
own. The data feed was never at fault — the worker was current
throughout (`generated_at` 10:20 ET, today's slate present, CORS open),
which is why this looked like a dead pipeline and was not one.

**No money was affected.** All three frozen pitchers were no-bet rows,
and the live watcher stays read-only with respect to the ledger.
Statcast remained the graded source of truth, so P&L, record and
grading were correct the whole time. This was a display fault.

**1. The page loaded once and never again.** `DataProvider` fetched in a
`useEffect` with `[]` deps — no interval, no visibility handler. A
terminal left open overnight showed the date it was opened on
indefinitely. Now re-fetches every 60s and on tab focus. A failed
*refresh* keeps the board on screen; only the first load surfaces an
error, because trading a good slate for an error page over one blip is
strictly worse than showing a slightly old one.

**2. A ~9-hour window every morning where the newest board IS
yesterday.** A date enters `available_dates` only once its slate is
written, and that is the 09:00 ET job (measured 09:19 ET today).
`page.tsx` defaults to `dates[0]`, so from midnight until then it opened
onto yesterday silently. It now says so, naming the date and the 9:00 ET
build, and only when the date was defaulted rather than chosen.

**3. Late starts frozen mid-game, permanently.** `poll_once()` computed
`iso = today_et()` internally and `main()` only ever asked for today, so
at 00:00 ET the poller moved to the new date and never looked back. Any
starter still on the mound was archived `status: in_game` forever, and
the board reads `live.final` to decide whether a total can still move.

Every affected row is a late first pitch, and every date with an archive
has one — 2 days out of 2, not occasionally:

    2026-08-11  Nick Martinez                21:40 ET
    2026-08-12  Eric Lauer, George Klassen   22:10 ET

No early game was ever hit, which is the signature of a midnight cutoff
rather than a bad feed.

Fixed in two places, because they fail independently. `poll_once` takes
the date as a parameter, and the loops finish yesterday before starting
today — bounded by "every starter is final" AND by `CARRYOVER_UNTIL_H`
(12:00 ET), so a suspended game cannot pin the poller to the past.
`archive_state()` is split from `write_state()` so a carryover updates
yesterday's record without overwriting the single-file view of *now*.
Separately, `dashboard_data.py` treats a settled Statcast total as
outranking a stopped poll — that is what clears the rows already frozen
on disk, which no future poll will revisit — and flags them
`stale_poll` rather than silently rewriting the observed counts.

**Second bug found underneath it.** The archive's "only ever grow a
date's record" guard caught a *fully* empty payload, but one failed
boxscore fetch `continue`s past that pitcher, so a partial cycle could
still blank a starter who was already final — A-035 reopened through a
different door. `_merge_rows` now merges per pitcher.

**Measured after.** Re-polling 2026-08-12 through the carryover path
returned both stuck starters Final — Lauer 6 K in 27 BF, Klassen 5 K in
24 BF. Lauer's 6 matches the Statcast total his card was already
showing, so the fix agrees with the graded source rather than merely
flipping a flag. `tests/test_live_carryover.py` (7 tests); full suite
134 passed; dashboard typecheck clean; banner and 60s refresh verified
in a browser against a simulated pre-09:00 payload.

## 2026-08-11 - Prior-season history window: built, gated, not shipped

Operator answered the three §7 decisions: two forward validations in
place of both temporal directions, **one** prior season, and the 263
starts with no usable prior stay refused. Built on that basis.

`USE_PRIOR_SEASON` is **False**. All five gates pass; CLAUDE.md requires
a two-week shadow before promotion and this has had none.

**What it does.** `tools/build_prior_season.py` writes a per-pitcher
summary of a completed season to `data/prior_season/<year>.parquet` —
rate, start count, and the distribution of starter outings. The pipeline
loads that summary, not a second season of pitches: the worker prices six
times a day and another ~750K rows per run is not affordable.

The rate blend widens the sample before shrinking rather than shrinking a
thin current season all the way to the league mean. Fitted W=0.60 by
binomial log-loss on 2024->2025; shipped 0.5, because the loss curve is
flat from 0.25 to 1.00 (0.52288 vs 0.52306) and 0.60 implies precision
that is not there.

**The scope doc was wrong about workload and the build corrected it.**
It specified prior p25 everywhere, which was asserted, never compared
against what production actually does (`game_bf.mean()`). Measured — and
a season TOTAL is not a workload estimate, it has to be divided by
outings — p25 wins outright only on season debuts. With 1-2 outings
already this season a 50/50 blend beats BOTH sources on average error and
on the upper tail, in both year pairs. Shipped: 3+ starter games ->
current season alone; 1-2 -> blend; 0 -> prior p25.

**Gates.** 1 leakage PASS (sidecar refuses an unfinished season; as-of
totals exclude the start being judged; "start" = threw the first pitch,
not "faced 15+ BF", which is post-hoc and drops the pitcher yanked after
eight). 2 PASS, +0.63% rate log-loss on the fit and +0.44% on the
untouched 2025->2026 holdout. 3 PASS. 4 PASS — no feature is added.
5 PASS, Brier on P(K >= line) +9.93% and +4.83%, measured on the
recovered starts only because pooling would average it away.

**Two caveats into the shadow.** The holdout's 0.8-1.0 prediction band
came in 9.0 points high (n=66 line-evaluations) where every other band is
within 3 — that band is where confident OVER bets come from. And season
debuts, a third of the recovered starts, have no production baseline to
be compared against at all.

**The first live example is the reason to shadow rather than ship.** With
the flag on for 2026-08-11, Snell prices at E[K]=4.8 against a 5.5 line:
an 11.3% edge to the UNDER on his second start back from a 94-day
layoff. It did not book — but the threshold was 11.8% only because no
lineup had posted, 5 points of which is the A-008 penalty. With a lineup
posted it clears at 6.8% and books as a LEAN. The existing thresholds
blocked this by four tenths of a point, for a reason that has nothing to
do with the feature.

`tests/test_prior_season.py` — 13 tests. The load-bearing one is that the
flag OFF is a byte-for-byte no-op even when a prior row is passed; a
feature that perturbs production while "disabled" is not disabled.
Suite 127 passed.

## 2026-08-11 - A pitcher the book had to disambiguate was unmatchable

The operator asked why the board was short and where one specific
starter had gone. Two different answers, and only one of them a bug.

**Not a bug.** Blake Snell has thrown one game all season — 2026-05-09,
18 batters faced. The `total_bf < 50` gate refuses him, which is the
A-007 rule working: a thin-history arm gets filled in with optimistic
assumptions and the edge filter then hunts for exactly those. Same for
Cody Bradford (20 BF). Drew Anderson and Mason Barnett are refused by
the role gate — typical recent outing 5 and 12 BF against a 15 minimum.
Four correct refusals.

**A bug.** DraftKings appends the team to a name when two players share
one: `Ryan Johnson (LAA)`. `_normalize_name` stripped accents and
Jr./III but not the parenthetical, so `ryan johnson (laa)` never matched
`ryan johnson`, and the last-name fallback then compared `(laa)` against
`johnson` and missed too. Dropped from both slates DK ever listed him on
— 2026-08-06 and 2026-08-11 — while carrying a live posted line.

It went unseen because a dropped pitcher leaves no row anywhere. No
zero, no null, no VOID; the board just looks like a light day. The one
`Unmatched DK pitchers:` line goes to stdout on a scheduled run.

Underneath it, a second bug that had never fired: `mlb_pitchers` was a
dict keyed by normalized name and built by assignment, so two probables
sharing a name would have silently overwritten each other, and the
last-name fallback took whichever candidate iteration reached first. The
tag DK supplies is precisely the disambiguator — and it was being thrown
away instead of used.

**Fixed** in `tools/daily_pipeline.py`. The tag is stripped for matching
and parsed by `_team_tag` for tie-breaking. Candidates are collected as a
list and resolved by `_resolve_probable`, which refuses when the name is
ambiguous and the tag cannot settle it: attaching a line to the wrong arm
prices one pitcher's projection against another's number, and the edge
filter selects hardest on that mismatch. The tag is stripped from the
display name too, so it never reaches the board, ledger, or grader —
nothing else about the book's spelling is touched, since those joins are
historical.

Measured: 30 props -> 30 matched, 0 unmatched (was 29/1). Ryan Johnson
prices at E[K]=3.6 against a 4.5 line, +4.1% UNDER, NO_PLAY — he would
not have been a bet today, but he was never given the chance to be one.

Also refreshed the Statcast cache, which stopped at 2026-08-06 (A-037
fixed the worker's schedule; these four days predate the fix). Checked
what it actually cost: **nothing on today's starters** — all 30 last
pitched 08-04..08-06, inside the old cache. What it did kill was the
bullpen-fatigue input, which reads *yesterday's* relief usage and so
returned "no" for every team regardless of truth.

`tests/test_dk_name_matching.py` — 12 tests covering the tag, the
tie-break, both refusal paths, and that untagged names keep the book's
exact spelling. Suite 114 passed.

## 2026-08-11 - Dispatching the task outsourced the cache refresh with it

A-036 fixed the display against a stale cache. This fixes the cache.

The worker's Statcast cache was refreshed **once per boot** and never on
a schedule. Not because nobody thought of it — `_log_evidence()` calls
`refresh_cache()` first, six times a day, and its docstring explains
that this is precisely what stopped the 2026-08-07 evidence loss.

But `_log_evidence` runs inside the task, and the scheduler reads:

    if not dispatch_github(name):
        TASKS[name]()

Once a GitHub token existed and dispatch started succeeding every time,
the task ran on CI and this container stopped executing that path at
all. The cache refresh went with it. Nothing failed. The log said the
window ran — and it had, on another machine.

Measured: booted 2026-08-10 18:41 ET mid-games, and 2026-08-11 07:58 ET
before Savant had published the previous day. Nothing in between. Hence
CI at 18/18 and the worker at 1/18 for the same date, from the same
commit, four minutes apart.

This is the third bug of the shape *"a mechanism that worked while the
fallback was the normal path, and stopped the day the primary path
started succeeding"* — A-025 for publishing, A-036 for rendering, this
for the cache. The publish_pass docstring already stated the general
lesson about a different symptom; it was never generalised.

It matters beyond the board: bullpen fatigue reads yesterday's relief
usage, so a stale cache degrades the leash inputs on any locally-run
pricing — which happens exactly when dispatch fails, i.e. when the
container is already having a bad day.

**Fixed** with `_run_or_dispatch(name)`: dispatch, and on success still
refresh the cache here; on failure run the task locally where
`_log_evidence` refreshes as before, so no double fetch. Both the
scheduler and the RUN_TASK_ON_BOOT hatch go through it.

Removed a latent bug on the way past: the boot hatch blanked the task
name after a successful dispatch and then called `TASKS[""]`, raising
`KeyError('')` into a handler that logged `BOOT TASK ERROR : ''` on the
one path that had worked. Reproduced before deleting.

Nine regression tests, negative-controlled: the old loop body refreshes
zero times on a dispatched task where the new one refreshes once.

**Also made it observable.** `/health` now reports `statcast_cache` —
the latest cached date, how many days are held, the byte size of each of
the last five days (`null` = absent, 636 = schema-only, ~450 KB = a real
light slate) and `last_refresh` with its timestamp, ok flag and window.
Answering "is 2026-08-10 actually in the cache?" needed container-log
access during this investigation, and the Railway session expired
mid-diagnosis — which is exactly when a health field earns its keep. The
same invariant as A-029: report the operation, not the configuration.

Not closed: `backfill_statcast` skips any day over 2 days old and over
20 KB, so a file written mid-games is large-but-incomplete and can
freeze that way. Six refreshes a day makes it far less likely; it does
not make it impossible. Filed as an A-016 follow-up.

## 2026-08-11 - The worker overwrote CI's correct board with its own blank one

A-035 predicted that 2026-08-10's strikeout totals would fill in at the
09:00 ET run. A watcher was armed to confirm it. They did not, and the
watcher said so at 09:28 — which is the only reason this was found
rather than assumed fixed.

The morning job HAD run. Today's board was built, 23 pitchers. The
evidence table `data/model_log.csv` had all 18 rows for 08-10 with
`actual_k` populated, committed by the worker itself at 09:05. The join
to the slate is sound — 18/18 pitcher_id overlap, 0 game_pk mismatches.
The numbers were sitting on the worker's own disk.

The renderer was reading the wrong source. `_actual_k_lookup` read the
Statcast cache and nothing else, and that cache is a ~90 MB per-season
tree each host tops up on its own schedule. Same commit, four minutes
apart:

    chore(ci): 09:01 ET   2026-08-10 -> 18/18 actual K totals
    worker,    09:05 ET   2026-08-10 ->  1/18

CI restores the cache every run. The worker refreshes it at boot and on
the 03:00 job, both of which land before Statcast publishes the previous
day — so the worker sits a day behind.

Two mechanisms turned that into a wrong site. The dashboard *prefers*
the worker's payload over the committed one, so the blank board is what
you see; and the worker commits `data.json` every five minutes, so it
overwrote CI's correct 09:01 build within four. The good artifact
existed, was published, and was destroyed on a timer.

**Fixed** by having the lookup fall back to `model_log.csv` for any key
the Statcast cache does not supply. Statcast still wins wherever it
answers — it stays the graded truth — but the evidence table carries the
same numbers, rides the ledger reconcile so every host agrees within one
publish pass, and is never-delete-rows by policy. A blank `actual_k` is
skipped rather than read as zero; a fabricated zero is worse than a
blank.

This does not fix the cache lag itself, which still degrades the leash
inputs `refresh_cache` exists to keep current. That is a separate item
on the roadmap.

Six regression tests, negative-controlled: the old lookup returns
nothing on the worker's exact condition where the new one returns
everything.

## 2026-08-11 - Yesterday's results vanished at midnight, every night

The operator reported the results disappearing, was told the ledger was
intact, and reported it again: "the results are still missing from
yesterday august 10th." They were right, and the first answer was
looking at the wrong table.

The bet ledger *was* intact — 11 rows, -7.05u, verified in five places.
What had gone missing was the per-pitcher strikeout total shown for
every starter on the board, bet or not. Measured at 07:48 ET: the
2026-08-10 slate served **1 of 18** pitchers with an actual K total,
against 26/26, 28/28, 20/20, 25/25, 27/27 and 27/28 on every other date
in the same payload.

Two sources can supply that number, and there is a window each night
where neither does. Statcast doesn't publish yesterday's games until
around 09:00 ET, so overnight the MLB Stats API watcher is the only
source — and the watcher was throwing its own data away. It wrote a
single `live_state.json`, overwritten every 30 seconds with today's
date; at midnight it rolled over to `pitchers: []` and yesterday's
finals stopped existing. The dashboard then discarded the file anyway
unless its date matched today's.

So results that were on screen all evening went blank overnight and
returned mid-morning. `git log` on `data/model_log.csv` dates the refill
exactly: each day's actuals first land in the 09:00 ET run the next
morning — 09:02, 09:05, 09:01 on the three prior days. The hole had been
there since the watcher shipped on 08-06.

**Fixed** by archiving each poll under the date it is *about*
(`data/live/<date>.json`) and looking up live rows per slate date
instead of "today". The date guard is kept rather than dropped — these
rows are keyed by pitcher_id alone and a starter appears on many dates,
so applying a payload to the wrong slate would attach one night's
strikeouts to another night's start, which is worse than a blank. An
empty poll can never overwrite a date that already has finals.

Provenance is unchanged: live figures still only fill a gap, never
overwrite a Statcast or ledger value, and still carry
`result_source: "live"`. This is a display fix; the graded ledger and
evidence table are untouched. It does not retroactively restore
2026-08-10, whose live rows were overwritten before the archive existed
— Statcast filled those at 09:00 ET as usual.

Five regression tests, negative-controlled against the old loader.

## 2026-08-11 - Four hours of grades committed to a detached HEAD

The operator reported that the results had disappeared. They had not —
every one of the 11 bets and the -7.05u P&L was intact in all five
places it lives, and the missing piece was simply today's board, which
is built at 09:00 ET and was looked for at 07:15. That part needed no
fix beyond saying so.

Underneath it was a real fault, four hours old and silent.

**A lost push race.** At 03:01:23 ET the worker's `git pull` printed
`Already up to date.`; one second later its push was rejected with
`! [rejected] master -> master (fetch first)` — GitHub Actions had
pushed its own `03:01 ET` commit inside that one-second window.

**A rebase turned the lost race into a permanent wedge.** The next pass
ran `git pull --rebase --autostash`, which tried to replay the
container's `dashboard/public/data.json` commit onto CI's commit to the
same file. Both processes rewrite that file in full, so they conflict by
construction. The rebase halted, left `.git/rebase-merge` behind and
HEAD detached, and every subsequent pull died on
`fatal: It seems that there is already a rebase-merge directory` — exit
128, roughly fifty times, 03:16 through 07:22 ET.

The signature was misleading in a specific way: `git-commit` reported OK
every five minutes (onto a HEAD reachable from no branch), and
`git-push origin master` failed non-fast-forward (because `master` sits
frozen at its pre-rebase position while a rebase is in flight). It read
as a push problem. It was not one.

Nothing was lost, and by design rather than luck: the volume is the
source of truth, `reconcile_ledger` kept succeeding on every wedged pass
(`11 pick(s), 11 graded`), and the served board stayed correct
throughout. Only the git mirror froze.

**Fixed by resetting instead of rebasing.** `data.json` and the mirrored
ledger are derived — regenerated from the volume later in the same pass
— so a conflict in them has no correct resolution, only a halt.
`sync_repo` now aborts any halted rebase, reattaches HEAD *before* it
touches the network, fetches, and `git checkout -B master FETCH_HEAD`.
A lost race now costs one pass's commit and self-heals on the next.
`commit_and_push` refuses to commit while detached instead of reporting
success, and `/health` publishes `head: {branch, detached,
rebase_in_progress}` so the next occurrence is one field away rather
than four hours of deploy log.

Three regression tests build a genuine halted rebase rather than faking
the symptom; the negative control confirms the old command leaves it
wedged (exit 128, detached before and after).

## 2026-08-10 - 91 CPU-hours of builds: the skip half-worked, and every build compiled numpy

The operator's Vercel usage for Aug 7-10 read 91 CPU-hours on
`mlb-strikeouts`, 96.4% of all build CPU across their three projects.
A-023 was supposed to have fixed this on 8/7. It had not. Two
independent causes were multiplying.

**Cause 1 — the reach-back for the last built commit never worked.**
Vercel clones ~10 commits deep. The Railway worker pushes a data commit
every 5 minutes, so the last BUILT commit leaves that window in under an
hour. `vercel-ignore-build.sh` tried to fetch it back and, when that
failed, took its designed fail-safe and built. The fetch failed every
time, and silently: all three attempts were redirected to `/dev/null`,
so the only trace was one line in a build log nobody reads.

Measured from Vercel's deployment list rather than assumed — two
independent windows, Aug 9 06:14-07:55 ET and Aug 10 12:36-13:52 UTC,
both show exactly 2 forced builds per ~90 minutes:

    13:35:57  last built commit 68a4ef8... is not reachable in this clone — building

That is ~30 needless 30-core builds a day. The skip itself was working:
the great majority of deployments are CANCELED, and the classifier's
verdicts are still correct. Only the recovery path was broken.

Fixed in two passes, and the first pass guessed the cause wrong.

The guess was that GitHub refuses to serve a raw SHA, so the fix
deepened along the BRANCH instead — plus `--unshallow` behind it, since
no fixed depth can be right once the skip holds and builds become rare,
and LOGGING on every failure instead of `/dev/null`. That last part is
the only reason the guess was caught. The 14:58 UTC build printed:

    fetch[deepen]:    failed — fatal: 'origin' does not appear to be a git repository
    fetch[unshallow]: failed — fatal: 'origin' does not appear to be a git repository
    fetch[by-sha]:    failed — fatal: 'origin' does not appear to be a git repository

**There is no remote named `origin` in Vercel's build container.** It
has the objects and the refs, but no configured remote, so every
`git fetch ... origin ...` was dead on arrival — the original script's
and the replacement's alike. Nothing to do with SHA policy or
credentials. The script now reads the remote list, falls back to
rebuilding the provider URL from `VERCEL_GIT_REPO_OWNER` /
`VERCEL_GIT_REPO_SLUG` when the list is empty, and prints both.

**Cause 2 — every build compiled numpy and pandas from source, for a
static site that runs no Python.** Vercel auto-detects `requirements.txt`
at the repo root and installs it before the build command. It is on
CPython 3.14.3, for which numpy 2.2.5 and pandas 2.2.3 publish no
wheels, so both were built from source on a 30-core machine. Timed from
the 09:47 ET build log:

    13:47:38  Building numpy==2.2.5
    13:48:38     Built numpy==2.2.5        60s
    13:48:58     Built pandas==2.2.3       81s
    13:49:03  > next build
    13:49:22  Build Completed             ← 23s for the actual site

84 seconds of Python against 23 seconds of npm + Next.js: about four
fifths of every build's wall clock, and a larger share of its CPU, since
compiling numpy parallelises across all 30 cores and `next build` does
not. `dashboard/package.json` has no Python dependency and
`dashboard/` contains no `.py` file. `requirements.txt` exists for CI
and Railway, both of which are unaffected — it is only the Vercel
install step that has no use for it. Overridden with `installCommand`.

**Arithmetic that reconciles:** ~30 builds/day x 4 days ~= 120 builds;
91 CPU-hours / 120 ~= 45 CPU-minutes per build, matching the ~45
CPU-min/build figure already recorded in the A-023 header comment.

**Both fixes verified.** Cause 2 in production, on the build this commit
triggered: `Build Completed in /vercel/output [19s]` against `[2m]`
before it, with no `Using CPython`, no `Building numpy`, no `Building
pandas`.

Cause 1 against a local clone that now reproduces the real condition
exactly — depth 10, `git remote remove origin`, a 25-commit data-only
gap:

      remotes configured: []
      using remote: https://github.com/joey11600/MLB-Strikeouts.git
      fetch[deepen]: ok
    only data has changed since the last build (7cad004) — skipping build

and the same harness still BUILDs when a code commit sits in the gap,
and BUILDs with all failures named when no remote can be reached at all.
A-023a's fail-toward-BUILD is intact in every path.

**The lesson is about the `/dev/null`, not the git.** The first pass at
cause 1 was a plausible, confidently-argued, wrong diagnosis, and it
would have shipped as "fixed" if the failure path had stayed silent. A
fail-safe that fires without saying why converts a broken recovery into
a recurring bill. It cost 91 CPU-hours to learn that here.

## 2026-08-10 - Two levers tested: one killed, one instrumented

**Reweighting Stage B: KILLED, and it does not reproduce.**

The 68-factor screen reported `logit_batter_k` fitted at +1.213 to +1.223
against a shipped +1.06479, i.e. "12% light". Refitting through the
PRODUCTION pipeline gives it at +1.0472 (2024), +1.0690 (2025), +1.0916
(2026), +1.0565 (24+25), +1.0648 (pooled) — never +1.21. The screen's
figure came from its own re-implementation with a different feature
construction. Editing the shipped constant to match it would have been
exactly the retune-the-constant antipattern A-009 warns against.

A real finding sat underneath it: both coefficients drift monotonically
across seasons — `tto_3` at -0.1834 / -0.2125 / -0.2503 and
`logit_batter_k` at +1.0472 / +1.0690 / +1.0916 — so the pooled fit lags
the current regime by ~19% on `tto_3`. KB.md already records a 2026
regime break (ABS challenge system).

Tested the way CLAUDE.md requires for a regime-scoped claim, a
within-2026 time split (train 2026 H1, test 2026 H2, 33,354 -> 33,024
PAs):

    fitted on 2024+2025 : Brier 0.166643   tto_3 -0.1982
    fitted on 2026 H1   : Brier 0.166696   tto_3 -0.2650
    improvement -0.032%, paired t = -1.55

The regime-matched fit is WORSE out of sample. The smaller sample costs
more than the regime match gains. **No reweight shipped**, in either
form.

**`is_home` instrumented.** It is the only one of 68 screened factors
with signal against the posted LINE rather than merely against actual
strikeouts: home starters beat their line by +0.300 K and away starters
by -0.500 (gap 0.800, SE 0.407, t = +1.97, positive in 4 of 5 slates),
while the market appears not to price it (`line ~ is_home`, t = -0.21).
On the 74 rows that now carry the column the gap reads +1.146, t = +2.28.

It is one factor of 68 with a slate flipping sign, so it is a LEAD and
nothing sizes on it. Logging it makes it judgable forward instead of
re-derived by joining slate JSON every time someone asks. Backfill
populated 74 of 126 rows — the dates a local cache can re-derive; the
other 52 kept their existing values rather than being dropped, which is
A-030's union rule working as intended.

79 tests pass.

## 2026-08-10 - Delete two dead inputs, stop fabricating the lineup (A-032)

A 68-factor three-way screen over 13,170 starts, run to answer "which
factors are wrong", found three defects that are true regardless of any
edge question.

**`has_pitch_limit` fitted to exactly +0.00000 and always would.**
`prepare_training_data` hardcodes it False on every training row, because
`data/manual_pitch_limits.csv` has never held a data row — the column had
no variance to fit. It could not move a price in either direction.
Removed from the design. Announced limits still bind at serve time
through the direct BF cap in `predict_bf_distribution`; that is a
different mechanism and it stays.

**`bp_heavy` fails Gate 2 in every direction** on total K: dRMSE
-0.023 / -0.035 / -0.006, t -0.51 / +1.33 / +0.64, and it is null on
batters faced too. The same term measured for the outs target flipped
sign by season. CLAUDE.md rejects a feature that helps in only one
temporal direction; this one helps in none. Removed.

**The lineup fallback was a constant with zero variance.** When no lineup
is posted, `daily_pipeline` substituted `[LEAGUE_K_RATE] * 9` — which
fired on 31.7% of the logged board (40 of 126 rows) and discarded
everything we know about the opponent. It now uses the opponent TEAM's
as-of shrunk K%. Measured out-of-sample RMSE on total K, common n=9,894:

    opponent representation   24->25   25->24   24+25->26
    real nine (confirmed)     2.2280   2.2355     2.2516
    team as-of K% (now)       2.2419   2.2510     2.2618
    constant 0.225 (was)      2.2720   2.3021     2.2755

The team rate recovers 68.5% / 76.8% / 57.0% of a confirmed lineup, in
every direction. Verified live across 30 teams: 0.183 (AZ) to 0.254
(CIN), sd 0.0169 against the constant's 0.0000 — at ~22 batters faced
that spread is ~1.6 strikeouts the model previously could not see. When a
team has no batting history the pipeline now SKIPS rather than
substituting a league average, because an invented input is exactly what
the edge filter selects into the bet list (A-007).

Backtest after the change, cross-season: **+3.9% / +4.9% / +3.2%** vs the
naive baseline (was +3.8% / +4.8% / +3.2%). Both directions positive,
decision split positive. Removing the two terms cost nothing, which is
what "they were carrying nothing" looks like. Stage A is now 4
coefficients; the calibrator was refit on the new out-of-sample
predictions.

**What this does NOT fix.** The measured weight on the model's
disagreement with the market is w* = -0.775 (negative in 5/5 slates,
permutation p = 0.040). The cause is not an inverted factor: the model
prices the SAME three variables the line does at nearly the same weights,
so its disagreement is largely estimation noise on shared inputs, and
noise added to a better forecast can only raise Brier. 16-18% of the
line's variance sits outside everything this repo can compute, and THAT
part predicts actual strikeouts at t = +2.62 — the largest t-statistic
anywhere on the board, and it belongs to the market. The 68-factor screen
establishes it is not in the pitch-level cache.

Corrected in passing: `logit_batter_k` is NOT worthless, contrary to a
reading ported from the outs research. It is the most valuable term in
the model (dropping it costs +1.85/+2.84/+1.08% out-of-sample RMSE) and
the shipped +1.06479 is ~12% light against refits of +1.213 to +1.223.
The outs finding did not transfer because a strikeout and a groundout are
both one out.

## 2026-08-09 - Ask the general question: when did the served board stop being current

Third false positive in two days from the same check, and this one was
caused by a guard I added yesterday.

At 20:46:10Z the worker was serving the previous board exactly
(13:05:35, 26 pitchers) while CI had just published 20:45:54 with 27.
`mid_cycle` was satisfied. What failed it was `not shape` — and a worker
one version behind NECESSARILY carries the old content. Requiring
`one_behind and not shape` collapses to "one behind and the regeneration
changed nothing", which is almost never true, since a board usually
regenerates precisely because its content changed. The guard negated the
thing it was bolted onto.

The deeper problem is that each fix answered a narrower question than
the real one:

  - "how many minutes behind?" — wrong, lag is quantised to the gap
    between priced boards, so a correct worker read 417 minutes late
  - "is it exactly one version behind?" — better, but CI can regenerate
    twice inside one publish cycle and leave a healthy worker two behind
  - "one behind AND identical content?" — self-defeating, as above

The question that actually holds: **when did the version the worker is
serving stop being current?** If that was less than GRACE_MIN ago, the
worker is mid-cycle. Serving nothing is the same question with the
answer "since the first board of the day published".

`_superseded_minutes_ago()` reads that from the published history of
`dashboard/public/data.json`, using git commit times rather than a board
stamp the worker itself could have written. It returns None for a
version that is current, unknown, or absent — and every caller treats
None as no grace, so it fails closed.

`shape` is no longer a grace condition. It still does real work in the
equal-age arm, where two boards of the SAME age disagree and one of them
is genuinely wrong. The difference is still printed in the OK message,
so a mid-cycle worker's content gap stays visible rather than silent.

`available` is demoted from gate to clock-sanity bound (>= -2): a repo
board dated in the future means a broken clock, and a broken clock must
not open the window.

Verified against the real history in a worktree at origin/master: the
version the worker was serving reports superseded 7.6 min ago; the
current version, an unknown version, and no version all report None.
Replaying the exact 20:46:10Z state now returns OK. 74 tests pass.

## 2026-08-09 - The first board of the day is not an outage (A-029)

Two runs failed this morning with `worker is serving no slate at all for
2026-08-09`. The worker was fine.

`data/slates/2026-08-09.json` was first committed at 13:05:40Z. The
watchdog ran at 13:05:52Z — **twelve seconds later**, against a 300-second
publish pass. The next two runs passed untouched once the worker pulled.

This is the same false positive fixed yesterday, on the branch that was
deliberately left alone because it is the one that caught the real
outage. Yesterday's fix covered "worker has an older version"; it did not
cover "worker has nothing yet", which is exactly what the first board of
any day looks like. Before that board exists the check warns (no local
slate), so this fires once every morning — on the branch that most needs
to stay trustworthy.

The no-slate branch now takes the same two guards as the stale-slate one:
the board must be inside GRACE_MIN, and the worker must be demonstrably
pulling. Not the version-identity guard — there is no served version to
compare when the worker has nothing.

It stays safe against A-029 because both guards reject that outage
independently: the board was hours old at most check times, and the
worker carried no successful pull at any of them. Ten of that day's
eleven failures came through this branch and every one still fails.

GRACE_MIN and the pulling test are now module-level and shared, rather
than computed inline in one branch — the two paths must not drift.

Three tests: the outage shape (board available 166 min, worker serving
nothing) still fails; the first-board-of-day case passes; a fresh board
with no provable pull still fails. Mutation-checked — dropping the
pulling gate breaks the third. 73 tests pass.

## 2026-08-08 - Fitted OUTS RECORDED hazard model

New `models/outs_hazard.py`: the fitted inning-lattice hazard model
specified in `docs/OUTS_MODEL.md`, wired end to end onto
`tools/build_outs_dataset.py` and `features/outs_asof.py`.

    python -m models.outs_hazard --fit --train 2024,2025 --test 2026
    python -m models.outs_hazard --three-way

Three components fitted by L-BFGS-B MLE: per-inning completion logit,
per-boundary return logit, and a per-inning {0,1,2} partial-inning
multinomial. Free intercepts, covariate effects shared across
boundaries except the quality block, whose per-boundary deviations are
ridge-penalised. The composition recursion is imported from
`models/outs_hazard_proto.py` rather than re-implemented, so there is
one copy of the code that turns hazards into a PMF. All three
components converge; every predicted PMF over all 13,170 starts sums
to 1 within 5.6e-16.

Penalty selected on the DECISION METRIC — mean Brier across the seven
market lines, on a temporal split inside the training years, never on
log-likelihood and never on the test years.

Three-way out-of-sample Brier skill against the honest as-of baseline
(the pitcher's own strictly-prior outs distribution shrunk toward the
as-of league PMF): +4.48% (24 to 25), +4.68% (25 to 24), +7.48%
(24+25 to 26). Positive in every direction, so it clears the
both-directions rule.

The sign gate tests `d E[outs]` from moving each term, not a raw
coefficient. That distinction is the substance of the change: read as
pooled slopes, four terms appeared to contradict the design's measured
directions, and all four were artifacts of the test rather than the
model. `stop_rate_b` is a statement about ONE boundary, and its shared
column averages it over eight; the boundary-matched slope on
`stop_rate_15` is negative in all three splits (-0.065/-0.103/-0.086)
while the pooled one flips (+0.077/-0.086/+0.005). `is_debut` and
`rest_unknown` are structurally coupled to the no-history block — a
debut row necessarily has no prior-5 pitch budget — so flipping either
alone describes a row that cannot exist; the coherent block moves are
-3.47/-4.23/-3.84 and -1.32/-0.92/-1.12 outs, matching the design's
raw 10.79-vs-16.11 and 14.71-vs-16.08. `save()` refuses when the gate
fails, so a sign-violating model cannot reach disk.

Two findings recorded rather than papered over. `stop_rate_12` is
sign-unstable across the three splits and `stop_rate_21` moves outs
the opposite way from the other quality terms where the design
measures a null — both are Gate-2 rejection candidates and are
reported as advisory, not gated. And the S1 direction under-predicts
the mean by 0.36 outs: 90% of that (+0.325) is the career-history
block, whose distribution shifts between 2024 and 2025 purely because
the Statcast cache begins 2024-03-28. The decision split is unaffected
(+0.045 outs).

The design spec is serialized as a plain dict, not as a pickled
dataclass instance. Fitting runs as `python -m models.outs_hazard`,
where the module is `__main__`, so a pickled instance would carry
`__module__ == "__main__"` and be unloadable from the daily pipeline —
a write-only model file. Caught by the API verification.

Calibration is usable but not yet passed: ECE 0.017-0.026 across the
seven lines on the 2026 split. Per `docs/OUTS_MODEL.md` section 10 the
raw hazard output must route through `models/calibration.py` before it
prices anything.

## 2026-08-08 - As-of feature set for the OUTS RECORDED model (Task C)

New `features/outs_asof.py`: the Tier-1 leakage-safe feature set for
starting-pitcher total outs, plus `tests/test_outs_asof.py`.

Follows `features/asof.py` conventions: hardcoded season starts, no
carry-across-seasons, cumsum-minus-current for every strictly-prior
aggregate, `shrink_rate`-style pseudo-count shrinkage. Two as-of
granularities: pitcher features use strictly-prior STARTS, league and
opponent features use strictly-prior DAYS (a doubleheader's game 1 is
excluded from game 2).

`exp_o` ships in both required forms — the scalar expanding mean and the
per-pitcher stop-rate vector at boundaries {12,15,18,21}, each shrunk
toward the as-of league hazard. Prior weights are grid-searched, not
asserted: `W_EXPO = 1.0` start (RMSE argmin 1.00/1.25/0.75 across the
three seasons, scored on common support so W=0 is not evaluated on an
easier subset), `W_HAZ = 24` starts-that-reached-b (log-loss argmin
24/32/24, flat across 16-48).

Career left-edge contamination is handled: 166 of 589 pitchers (28.2%)
have their first cached start within 14 days of 2024-03-28 and are
excluded from the `is_debut` treatment. Not excluding them dilutes the
debut effect by 1.043 outs (10.787 genuine vs 11.830 pooled).

Gate 4: exactly two pairs exceed |r| > 0.85, both by construction —
`exp_o` vs `exp_o_shrunk` (+0.976) and `season_start_number` vs
`career_x_season` (+0.974). `RECOMMENDED_MODEL_SET` resolves both by
dropping one member; nothing else exceeds 0.774, max VIF 4.93.

The test file is the point of the change. Four independent attacks —
brute-force recomputation from strictly-prior rows, future perturbation,
same-day/doubleheader perturbation, and row-order invariance — run on
synthetic data and on the real 13,170-start table. A 7-mutant injection
run (own-row leak, same-day league leak, roll-then-shift budget window,
missing left-edge guard, imputed null rest, softened opponent gate,
same-day opponent leak) kills 7/7. An earlier version killed only 6/7:
the synthetic season was too short for any team to clear
`MIN_OPP_GAMES`, so the opponent branch was never exercised and a leak
in it was undetectable. The fixture now asserts that branch is live.

## 2026-08-08 - The model log deleted what it could not re-derive (A-030)

`log_dates()` dropped every stored row whose date had a slate file, then
regenerated rows only for pitchers Statcast could derive at that moment.
Those are not the same set. A date whose pitches are not in the cache
regenerates zero rows, and the delete stood.

Measured against the real 99-row log: one run on a machine whose cache
stopped at 08-06 destroyed all 25 graded rows for 08-07 — real actual_k
and actual_bf outcomes, unrecoverable. The run printed
`2026-08-07: logged 0 pitchers` and reported success.

That is CLAUDE.md's "Never delete rows", on the evidence table `/model`,
the live-calibration block and the shadow portfolio are scored from.
Truncating it silently corrupts published model-quality numbers.

It never fired in production. `git log` over `data/model_log.csv` shows
26 -> 54 -> 74 -> 99 across 25 commits, monotonically increasing — CI
restores and tops up the cache before the pipeline runs, so the
derivation always had its data. Latent, not realised. But an incomplete
cache is an ordinary transient state and this runs on every close task,
so it was one unlucky ordering away the whole time. It surfaced on a
local machine whose cache stopped a day short, which is exactly the shape
of the accident.

Second time in this area: `Fix nightly evidence loss` (2026-08-07,
54 -> 74 rows) was the same family — a rebuild assuming what it can
derive equals what it has stored.

Now a union by (date, game_pk, pitcher_id). A freshly derived row
supersedes the stored one, which is what lets a backfill correct a row,
but a stored row is never dropped because this run could not re-derive
it. The shrink guard raises before the write, not after, or it would
document the loss rather than prevent it.

Both the new regression test and the previously-red
`test_model_log_backfills_a_missing_date` fail against the old line, so
neither passes by accident. 38 tests, all green — the first clean suite
since this started.

## 2026-08-08 - The alarm that caught the outage was itself broken (A-029)

`served board is current` failed again at 20:45Z, and this time it was
wrong. It measured `lag` as the age gap between two board VERSIONS. The
board had held at 13:49:41Z for seven hours; CI regenerated it at
20:46:20Z and ran the check nine seconds later. lag read 417 minutes
against a 45-minute threshold while the worker was nine seconds behind
and had the new board on the next pass. Every regeneration after a quiet
stretch fires it.

Worth fixing rather than muting, because this is the check that caught
the real outage this morning, and an alarm that cries wolf on every
regeneration is one you stop reading.

It also turns out this entry's earlier reasoning was wrong about which
branch did the catching. Ten of the eleven failures came from
`if not got:` — *serving no slate at all*. `lag > 45` has fired exactly
once in its history and that firing was the false positive. Relaxing it
costs nothing measured; the risk runs the other way.

The first fix was worse than the bug. An adversarial review — four
independent lenses, 26 findings, every one reproduced against the real
function — returned DO NOT SHIP:

- The grace was bounded in minutes but **unbounded in lag**. A
  3.5-day-stale board serving 1 pitcher against the repo's 28 reported
  `ok`; so did a 26-hour-stale board hiding a bet, which is A-025's exact
  harm. Fixed by judging **version identity** — grace applies only to a
  worker exactly one version behind, read from git history.
- The liveness gate used `last_publish.ok`, which is not a liveness
  signal and never was. `publish_pass` wraps the pass in try/except and
  `_run` returns False rather than raising, so a failed `git pull` leaves
  `ok=True` — which is precisely why /health advertised
  `last_publish: {ok: true}` for sixteen hours this morning. Grace on
  that basis would have silenced the check on the very outage it caught.
  Now gated on `last_pull`.
- The `/health` fetch shared a try block with `/data.json`, converting a
  live stale-board outage into a green `warn` reading "worker
  unreachable — site is on the bundled fallback", false in both clauses,
  exit 0. Now its own try, failing closed.
- Negative clocks opened the window: a repo stamp 90 minutes in the
  future held grace open for 102 minutes.
- A `TypeError` the new arithmetic can raise was uncaught, and `run()`
  downgrades that to a warn — silently disabling the one check that must
  never go quiet.
- Shape mismatches had been quietly downgraded from fail to warn.

Shipped in two commits on purpose. `last_pull` had to be deployed and
confirmed on /health before the watchdog could consume it; merged
together the check fails closed against every worker that has not yet
redeployed. A successful bootstrap now also records a pull, or LAST_PULL
sits `{ok: None}` for five minutes after each deploy and reds a CI run
every time — observed live while waiting for this rollout.

`tests/test_watchdog_served_board.py` pins all of it: a branch that grew
from two arms to six had zero coverage. The stale-board test uses
MATCHING pitcher counts so version identity has to be what rejects it,
and that was mutation-checked — stubbing the guard to True makes the test
fail, so it is not passing incidentally.

## 2026-08-08 - The container builds its own checkout (A-029, second pass)

The first fix was wrong about the mechanism. Taking `.git/` out of
`.dockerignore` deployed clean as 5309d93, and the container came up
still reporting `git.is_repo: false`.

Railway's builder does not hand Docker a clone. Its build log reads
`fetching snapshot` then `unpacking archive` — the build context is a
source tarball with no `.git` in it at all. The `.dockerignore`
exclusion was a real bug and it was never the operative one. `/app` has
never been a repository and, on that builder, never could have been.

The detection added in the first pass is what caught this, within one
deploy instead of another sixteen hours. That is the entire argument for
checking capability instead of configuration: the fix failed and said so
immediately, in the same field that had been lying all day.

`_bootstrap_repo()` is the real fix. When `/app` is not a repository the
worker builds one — init, remote add, fetch, `reset --hard
origin/master` — then re-probes. Two things make `reset --hard` safe
here, and both were checked rather than assumed. No symlinks are
involved: `seed_volume_state()` copies image to volume precisely so the
ledger is a real file on `/data/state`, because the atomic-write pattern
destroys symlinked destinations. And nothing tracked by git is excluded
from the image — every `.dockerignore` entry covers gitignored paths
only — so the unpacked archive is a complete checkout and the reset has
no phantom deletions to apply.

It resets to origin/master rather than to the build commit. CI commits
every few minutes, so the archive is usually already behind by boot, and
`_merge_dir` reads FILES out of this checkout rather than git objects. A
repo with a current HEAD over a stale working tree would merge
yesterday's board into the volume and look perfectly healthy doing it.

`configure_git()` now runs first in `main()`, ahead of
`seed_volume_state()` and `reconcile_ledger()`. Both read files out of
the checkout, so bootstrapping after them would have published a
boot-time `data.json` that was already behind — the exact failure this
audit item is about.

Three more tests: the bootstrap builds a populated, clean checkout (run
against this repo as the remote, so it stays offline); an inherited
`.git` is never re-initialised or reset, since that would discard
mirrored ledger rows before they were pushed; and a bootstrap failure
never puts the token in the log.

## 2026-08-08 - The container was never a git repository (A-029)

Eight straight red CI runs, all failing the same watchdog check: the
worker was serving no slate at all for today. origin/master had
`data/slates/2026-08-08.json` with 28 priced pitchers. The worker was
serving 08-04 through 08-07. Since `data-context.tsx` prefers the
worker's `/data.json` whenever it answers, that frozen copy was the
site, and the site had nothing to show for a live slate.

`.dockerignore` excluded `.git/`. So `COPY . .` built an `/app` that is
not a git repository, and every git command in the container failed with
exit 128 -- pull, push, checkout, diff, commit. The Dockerfile installs
git and says so in a comment: "git: the worker commits the ledger back
to the repo." It never could.

A-025 added the pull half on 08-07. A-028 added the push half on 08-07.
The `.git/` exclusion landed 08-05, before both. Neither fix has ever
executed. The worker has been dispatching work to CI and discarding
every result since, serving whatever slates happened to be baked into
the image at build time.

Sixteen hours of that went unnoticed because two mechanisms reported
success they never checked. `configure_git()` ran four subprocess calls
with `capture_output=True`, inspected none of their return codes, and
then logged "git remote configured for joey11600/MLB-Strikeouts". That
line sits in the 23:06 EDT boot log directly beneath four exit-128
failures. And `/health.can_push_to_git` was `bool(GITHUB_TOKEN)` -- it
answered "is an env var set", which is not a question anything depends
on, and answered `true` the whole time.

CI was not broken. CI was the only thing that noticed. The `served board
is current` check fired on the first run after a board existed that the
worker lacked, and on every run since. The red is the alarm working.

Fixed: `.git/` out of `.dockerignore` (9.3 MB), with the reason recorded
inline so it does not get tidied back. `configure_git()` probes
`git rev-parse --git-dir` before anything else and logs FATAL with the
remedy when it fails, checks every exit code after that, and unshallows
a shallow builder clone since `pull --rebase` can refuse against one.
`can_push_to_git` now means what it says, and `/health` carries a `git`
block with `is_repo` / `shallow` / `remote` / `error`.

The general lesson is the one A-025 already paid for once: a check that
inspects configuration instead of capability will pass while the thing
it guards is broken.

## 2026-08-08 - Start capturing outs prices before there is a model

DraftKings sells an Outs Recorded O/U market. It is subcategory 17413,
sitting in category 1031 next to the two strikeout boards we already
scrape, and it covers exactly the pitchers we already price — measured
2026-08-08, both boards returned the same 14 pitchers on the evening
board and 30 on the day board.

We have no model for it and none is proposed here. This commit captures
the prices anyway, because they are perishable in a way the model is
not. A model can be built in November from three seasons of cached
Statcast. A closing line from 2026-08-08 cannot be reconstructed from
anything, at any price, ever. AUDIT A-002 — never having scored the
strikeout model against the market — exists because nobody was writing
those prices down early enough. That mistake costs nothing to avoid
twice.

The two boards are structurally identical: same events/markets/
selections join, same `outcomeType`/`points`/`displayOdds` shape, same
`venueRole` team resolution. So `extract_ou_odds` takes a subcategory
and a market-name suffix rather than being forked. The one real
difference is that the outs subcategory does NOT echo its own name —
the subcategory is "Outs Recorded O/U" but every market inside it is
named "Gerrit Cole Outs O/U", so `OUTS_OU_SUFFIX` is `" Outs O/U"`.

Snapshot prefixes are `dk_outs` / `closing_outs`. That separation is
load-bearing rather than tidy: both boards carry a `line` column and
identical row shapes, so a prefix that matched across markets would
serve 17.5 outs as 17.5 strikeouts with nothing downstream noticing.
`_candidate_snapshots` anchors the date immediately after the prefix,
so the two can never see each other's files, and self-test D7 asserts
it by seeding a `dk_k_*` board and requiring the outs loader to refuse.

Outs capture is ordered last and wrapped in both producers. No
strikeout pick depends on it, so an outs failure must never cost us a
strikeout closing line, which does back money today — the same rule the
alt board already follows.

D6 changed shape. It counted occurrences of the string
`allow_snapshot=False`, and the prose around `capture_closing` says
that phrase several times, so the count passed on comments alone while
a real call could go unpinned. It now checks each fetcher's actual call
site and fails if a named board is missing entirely.

Two claims made while scoping this were wrong and are corrected here
rather than left in the transcript. Outs props are **not** cheaper than
strikeout props: measured head-to-head on the same 14 pitchers at the
same moment, strikeouts hold 5.99% and outs hold 6.97%, with outs
higher on all 14. The earlier reading compared outs against CLAUDE.md's
stated 8-12% prop assumption instead of against the actual strikeout
book. And the line grid is not three coarse values — that was an
artifact of a 14-market late-night board. The full day slate carries
seven distinct lines, 13.5 through 19.5.

Nothing prices an outs bet. `fetch_dk_outs_props` has no caller in
`daily_pipeline`, no ledger column, no grader. This is a writer only.

## 2026-08-07 - The worker could grade a bet but never book it (A-028)

Railway had Payton Tolle graded LOSS, 14 K, -2.0u within minutes of him
being pulled, and reconciled 10 of 10 picks. Git's row was blank, so
pl_calc -- the only sanctioned source of a P&L number -- still reported
the pre-game total. The early-grading work from A-020/A-021 existed and
could not reach the books.

Two independent breaks, and fixing either alone would have changed
nothing. `_merge_csv` unions repo -> volume and never writes back, so
anything the container produces stays on the volume. And
`commit_and_push()` is only called from the four task functions, which
only run when `dispatch_github()` FAILS -- since the token was added it
always succeeds, so the push never ran at all.

This is A-025's mirror image. That was the pull half: work done on GitHub
not reaching the container. This is the push half: work done in the
container not reaching git. Splitting the work across two machines broke
the loop at both ends, and this morning only one end got fixed.

`mirror_volume_to_repo()` copies the volume's ledger into the checkout,
then the publish pass commits and pushes. Ordering is load-bearing:
reconcile unions the pulled rows into the volume first, so the volume is
a superset by the time we copy back and the copy can only add rows, never
drop them. tests/test_volume_mirror.py asserts exactly that, plus the CI
no-op and surviving a missing volume.


## 2026-08-07 - The chart shows which way it leans (A-026, A-027)

The book-line marker had silently disappeared from every pick card.
`kdist-chart.tsx` did `(line + 0.5) * slot`, and the slate stores `line`
as the string "6.5" - so that is `"6.50.5" * slot`, which is NaN, and
`NaN < W - PAD_R` is false. The amber dashed line and its label were
skipped with no error anywhere. Every other consumer of `line` only
prints it, and "6.5" prints fine, which is why only this site broke.

Coerced in the component, not in Python: the ledger's `line` is
legitimately a string for ladder rungs ("6+"), so re-typing the emitted
value would reach well past this bug.

Then the bigger complaint - the chart said nothing about the pick it sat
under. Every bar was the same grey. Now the half of the distribution that
WINS the bet is tinted in the side's colour and washed with a band, the
losing half stays grey, and a caret marks the projection. The caret is a
different shape from the line on purpose; they usually sit within a
batter of each other and two verticals would read as one idea.

Labelled in words so hue never carries meaning alone. And it reads "62%
OF CURVE", not "62%" - that is the raw model's area, while the card
headline is the market-blended 52.9%. Two unlabelled "chance this wins"
numbers on one card is how a board stops being trusted.

Verified in the live DOM against the real Tolle card rather than by eye:
dashed line at x=261.9 matching the computed 6.5 position, band spanning
the UNDER side, 7 bars tinted vs 8 grey, caret at 6.0, and a
screen-reader label reading "book line 6.5, UNDER wins on 62 percent of
outcomes, projection 6.0".

### railway.json - stop rebuilding on ledger churn (A-027)

With the repo now connected, Railway would build on all 10-18 daily data
commits, each restart interrupting the live starter watcher. Watch
patterns: `["**", "!/data/**", "/data/*.py", "!/dashboard/**"]`

Both traps came from reading Railway's docs rather than guessing. The
leading `**` is required - "negations will only work if you include files
in a preceding rule" - so the bare `!/data/**` suggested earlier would
have matched nothing and silently done nothing. And `/data/*.py`
re-includes three worker modules that live in the same directory as the
ledger; excluding `data/` wholesale would have stopped real code changes
from ever deploying.

docs/RAILWAY.md records why Redeploy cannot ship new code (the Dockerfile
bakes it in) and how to verify a deploy actually landed.


## 2026-08-07 — The worker served a seven-hour-old board (A-025)

Found while verifying something else, which is the only reason it was
found at all. At 16:47 ET the lineup lock produced a LEAN — Payton Tolle
UNDER 6.5 at +110, 2.0u, confirmed lineup, two hours to first pitch. The
dashboard was showing the 09:51 morning board: 24 pitchers, projected
lineups, no play. The operator could not see their own pick.

Railway dispatches the real work to GitHub Actions, because it can reach
DraftKings and the container cannot. The loop marks the task done and
moves on — and never pulls the result back. So it kept serving the board
from its own last LOCAL run, and since `data-context.tsx` prefers the
worker's /data.json unconditionally whenever it answers, the worker's
stale copy IS the site.

It only became reachable the day the GITHUB_TOKEN was added. Before that,
dispatch failed, the fallback ran the task locally, and the local run
rebuilt data.json. The bug arrived the day the architecture started
working as designed.

A second mechanism compounded it: `sync_repo()` pulls with
`--rebase --autostash`, and a local rebuild leaves the tracked data.json
modified — so autostash stashes the stale file, pulls the fresh one, then
re-applies the stash on top. The stale copy wins every pull. Reproduced
locally in one command.

`publish_pass()` now drops the derived file, pulls, and rebuilds, every
five minutes at the top of the loop before the schedule is consulted. A
rebuild takes 0.73s.

/health now reports `last_publish` including the generated_at actually
being served. It previously reported only that data.json existed, which
is how seven hours of staleness went unannounced.

And the invariant that should have existed: `check_served_board_is_current()`
compares what the worker serves against what the repo published. Verified
against the live fault — FAIL, "worker serving a board 416 min older than
the repo's." Every other watchdog check reads the repo, so all thirteen
were green while the site showed a seven-hour-old board.

Checked before blaming: this morning's Vercel build-skip did NOT cause it.
`loadData()` reads the bundled copy only when the worker is unreachable,
so the bundle's freshness was never in the path.


## 2026-08-07 — Stage A is finished; the "leash bias" was never real (A-024)

Went looking for a 1.32-batter bias in the leash and found that it does
not exist. Reporting it in the first place was an error: the dashboard's
live-model panel scores only the 48 non-reconstructed rows, and those
happened to be the two days that ran over. `model_log.csv` holds 74
graded starts; the 26 left out ran +1.69 the other way. All 74 pooled:
**-0.27 BF, SE 0.48**.

Measured properly across **11,042 out-of-sample starts** in all three
temporal directions, the bias is **-0.008 BF**, CI [-0.11, +0.09]. Zero
sits inside every interval. A 48-start window landing at |1.32| happens
3.3% of the time by chance, and 5.3% of real consecutive two-day pairs
across three seasons hit it.

Accuracy is at **94% of the pre-game ceiling** — 2.82 BF against a
perfect model's 2.66, leaving 0.16. Four candidates went through the full
three-way gauntlet and **none survived**. The most promising one —
aligning the training feature onto the serving definition — won on the
wide backtest in all three directions and then reversed on the population
that actually becomes bets, because the pipeline already refuses
relief-worked pitchers and that is precisely where the two definitions
disagree.

Worth recording because it is counter-intuitive: the BF distribution
family IS wrong. Real batters-faced is left-skewed and under-dispersed,
the negative binomial is always right-skewed and over-dispersed, and
`alpha` sits pinned at the optimizer's floor in both shipped pickles with
the likelihood still pushing. Tails run 2-4x too fat. **It does not
matter** — P(K >= line) is near-linear in BF, so only the mean survives
the compound integral and the shape cancels out.

The real error is downstream: across 18,798 backtest rows the model says
OVER hits 29.69% and it hits 30.88%, under-calling OVER by 1.19pp, and
the gap persists when BF is exactly right. That points at Stage B, the
TTO decay, or the calibrator — and explains why every leash-shortening
idea lost, since cutting BF pushes OVER down and OVER was already low.

Stop working on batters faced. Re-open only if 150 graded starts come in
near +1.3.

### Also: the pitch-limit cap, which has never once executed (A-024a)

`predict_bf_distribution()` capped a limited starter at `pitch_limit /
4.0`, a number chosen by eye. That path has never run —
`manual_pitch_limits.csv` is a header row, `backtest.py` hardcodes None,
and 0 of 98 priced pitchers carry a limit. Which is exactly how a wrong
constant survives.

Replayed pitches in order on 3,283 real starts, counting batters actually
faced at the Nth pitch: 60 -> 15.83, 75 -> 19.68, 90 -> 23.16, 100 ->
25.04. So 4.0 is right for a ~100-pitch outing, which is not a limit; over
the 60-90 band where limits land it understated batters faced by 0.7-0.9,
worth ~2 points of P(over), always suppressing OVER.

Now `PITCHES_PER_BF_UNDER_LIMIT = 3.8`, with the table in the source.
Provably a no-op today since the path cannot fire — this removes a
landmine rather than changing live pricing. Four tests pin it.

## 2026-08-07 — The build-skip compares against the last BUILD (A-023a)

Red-teamed this morning's build-skip before trusting it, and found the
first implementation wrong in the expensive direction.

It diffed `HEAD^` against `HEAD`, which is only correct when a push
carries exactly one commit. One push can carry several. Put a code
commit underneath a data-only commit in the same push and `HEAD^..HEAD`
sees only the data — build skipped, code silently never live, nothing
red anywhere. Vercel just records a CANCELED deployment that looks
exactly like the healthy case.

Reachable here rather than theoretical: `tools/odds_relay.py`
`_publish_hint()` tells the operator to run a bare `git push origin
master`, which ships everything unpushed, and its odds commit touches
only `data/odds` so it lands on top. It has never actually happened —
zero merge commits in history, every deployment so far advancing by
exactly one commit — but it depended on operator habit rather than on
anything enforced.

Now compares `VERCEL_GIT_PREVIOUS_SHA`, which Vercel documents as the
last SUCCESSFUL deployment and only supplies when an Ignored Build Step
is configured. A skipped build is not a deployment, so that value stays
pinned to the last commit whose code is actually live — the correct
baseline. A run of data commits each compares against real live code,
and a code commit anywhere in the gap is caught. No baseline,
unreachable baseline, and manual redeploys all build.

`tests/test_vercel_ignore_build.py` locks it: 6 cases against a real
throwaway git repo. The load-bearing one constructs the `[code, data]`
push and asserts BUILD — and also asserts the naive `HEAD^` rule would
have wrongly seen data-only, so it fails if anyone puts that back.

The mechanism itself is confirmed by observation. The build log for
d89633b shows Vercel running `bash scripts/vercel-ignore-build.sh` and
printing the script's own output and file list, which proves in one
block that the `ignoreCommand` is being read from vercel.json with no
dashboard override, that bash runs it with no line-ending failure, and
that the parent commit is present in Vercel's clone — a clone too
shallow to see history was the one plausible silent killer.

Also learned why the hours were so large: builds run on a 30-core Turbo
machine, so ~1.5 minutes of wall clock bills as ~45 CPU-minutes.

## 2026-08-07 — Data commits stop rebuilding the site (A-023)

The two MLB projects had eaten 92% of the Vercel build allowance in one
cycle: 78 CPU-hours here, 99 in NRFI. Cause is structural, not a
runaway job — Vercel builds on every push, and CI pushes a `chore(ci)`
commit every time the ledger moves, up to 48 runs a day.

The waste is that the rebuild accomplished nothing. `data-context.tsx`
fetches live from the Railway worker; the bundled `public/data.json` is
only a fallback for when that fetch fails. So the site was being rebuilt
from scratch to refresh a file the browser then overrides.

Confirmed rather than assumed, because the whole fix rests on it: the
worker sends `Access-Control-Allow-Origin: *`, and fetching both
endpoints live returned Railway at `13:51:44Z` against Vercel's bundled
`13:51:36Z`. The runtime path works.

`scripts/vercel-ignore-build.sh` is now the `ignoreCommand`. Diff
touches only `data/` and `dashboard/public/data.json` -> exit 0, build
skipped. Anything else -> exit 1, builds as before. When the diff can't
be computed (shallow clone, no parent) it BUILDS: a needless build costs
minutes, a wrongly skipped one ships stale code and nobody notices.

Replayed over the last 25 commits: 7 `chore(ci)` -> SKIP, 18 code ->
BUILD, no misses.

Sizing this honestly — the 8/5 and 8/6 spikes were 36 and 26 *code*
commits from the dashboard/watchdog build-out. Those were legitimate
rebuilds and this change would not have stopped them. What it stops is
the floor underneath: a normal slate day is ~10–18 automated commits,
and those now cost zero build minutes.

NRFI is the bigger consumer (99h, same pattern, `auto: predict` /
`auto: grade` / `auto: daily backup snapshot`) and is deliberately left
alone — different repo, different production system, operator's call.

## 2026-08-07 — Morning board moves to 09:00; evidence decoupled from it

Operator asked for the board at 09:00 instead of 10:30. Straightforward
on its own, except the morning job had just become the thing that
rescued yesterday's observations (A-022) — and 09:00 sits BEFORE
Baseball Savant reliably publishes. Measured on 8/7: 0 pitches for 8/6
at 03:21 ET, 3,530 by 08:59. Moving the board earlier would have made
the evidence more fragile, not less.

So evidence logging is no longer attached to any single job.
`_log_evidence()` runs `model_log` + `shadow` on EVERY task — night,
morning, all three closes, and the lineup lock. Six attempts a day.
Both steps are idempotent per date and take ~1s when there is nothing
new, so the cheapest guarantee is to keep trying. The board time can now
move again without anyone remembering this coupling exists.

Watchdog threshold moved noon -> 13:00 ET to match. Attempts run at
03:00, 09:00 and 12:15, so a noon cutoff would have opened a
false-alarm window between 12:00 and the 12:15 attempt. A threshold
that fires fifteen minutes before the thing that fixes it is worse than
none, because it teaches you to ignore the alarm. Test updated: 12:00
is now an expected WARN, 13:00 the first FAIL.


## 2026-08-06 — Shadow portfolio breaks the A-006 deadlock (A-015)

The live filter bets almost nothing, and the reason is arithmetic, not
a quiet slate. `MODEL_TRUST_WEIGHT = 0.5` halves every edge before it
meets a ~8% bar, so a bet needs a **~16% raw disagreement with
DraftKings — 26% on a projected lineup**. Real prop edges live at 3-8%.
On 2026-08-06, 5 of 15 confirmed-lineup pitchers had raw gaps of 8-12%
(Mikolas 11.6%) and **zero** cleared.

Each gate was justified alone — half-trust blend, vig margin, EV floor,
lineup penalty, all added after 8/4-8/5 lost money on phantom edges.
Stacked, they multiply into a wall.

Worse, it deadlocks A-006, which permits raising trust only "after 100+
graded bets with positive CLV". At ~0 bets a day that evidence is never
collected: the gate demands proof the configuration prevents gathering.

`tools/shadow.py` resolves this with no money at risk. Every evaluated
pitcher is already logged with its settled outcome (~20/night), so the
counterfactual is directly scorable: for a grid of trust weights
(0.5/0.65/0.8/1.0), which pitchers WOULD have cleared, at what stake,
and what would the portfolio have returned — including CLV, which is
available for the whole board rather than just the bets.

Design constraints that matter:

- **One implementation of the edge formula.** `compute_edge` gained an
  optional `trust_weight` override so shadow sweeps run through the
  production function. A second copy would drift and quietly invalidate
  the evidence we intend to change money rules on. Verified the default
  path is byte-identical to before.
- **Shadow units cannot be mistaken for real ones.** They carry basis
  `shadow_flat_100u` under a `shadow` subtree, and `tools/pnl_guard.py`
  now enforces the separation **in both directions** — a real basis
  inside shadow, or a shadow basis outside it, both fail the build. The
  guard immediately earned its keep by rejecting the first version of
  this work, which emitted bare floats for per-pick P&L.
- The guard also learned that some names (`pnl`) are a container in one
  place and a tagged value in another; a dict carrying neither `value`
  nor `basis` is now treated as a container, while a half-tagged value
  still fails.

First run (26 reconstructed rows, diagnostic only): higher trust would
have lost money too — 0.8 → −6.04u, 1.0 → −4.15u. That is a useful
early answer, and precisely why the decision needs evidence rather than
a guess.

Wired into the nightly job so the evidence accumulates without anyone
remembering to look, and rendered on `/model` with an explicit
progress-to-100 bar and a "these bets were never placed" banner.

### Non-bet pitchers now show the model's read

A 20-pitcher board rendered 20 near-blank rows: a card that was not bet
showed only "no bet". The day's actual work — a probability and an
expected strikeout count for every starter — was invisible without
expanding each card. Non-bet cards now carry the model's side, its
probability, the two prices, **how many points short of the bar it
fell**, and E[K]. Not betting something is not the same as having no
opinion about it.

## 2026-08-06 — Odds run on GitHub Actions; empty-board guard (A-012, A-014)

The operator pushed back on being told the cloud could not fetch odds:
if the first-inning model does it off a server, why can't this one? The
answer is that it doesn't, and I had not checked.

What NRFI actually does, read from its source rather than assumed:

- `workers/predictor_loop.py`: `PREDICTOR_SCRAPE_DK` defaults to
  `"skip"` **"because DK's CDN blocks Railway egress — see T2.56"**.
  Identical 403, identical cause.
- Its `Procfile` runs `workers/live_state.py`, which polls the MLB
  Stats API (free, ungated). Railway there has never touched DK.
- DK scraping runs under **GitHub Actions**, on a self-hosted runner:
  repo variable `RUNNER_LABEL=self-hosted` resolving to a Contabo VPS.

So an always-on cloud path existed the whole time. The earlier option
set (PC relay / paid proxy / paid odds API) was wrong because it was
reasoned from this repo instead of the sibling that already solved it.

**And the free runner works.** NRFI's notes say GitHub's shared runners
are blocked too (Azure ranges, "failing every tick for weeks"), but
that was May. Tested directly rather than inherited: `ubuntu-latest`
fetched **16 O/U props and 119 alt rungs, zero 403s**, and committed
the closing snapshot itself. No VPS, no proxy, no subscription.

`.github/workflows/daily.yml` fires hourly and calls a new
`railway_worker.py --due`, which reuses the SAME ET schedule table,
grace windows and once-per-day state as the resident worker. GitHub
cron is UTC-only, so hand-written ET cron lines silently shift an hour
at every DST boundary; asking the existing scheduler what is due makes
that correct by construction and stops the two schedulers drifting
apart. `runs-on` reads `vars.RUNNER_LABEL` exactly like NRFI, so
moving to a VPS later is a settings change, not a code change.

### A-014 — a run that could not compute published an empty board

The first full CI pricing run "succeeded" and wrote a **0-pitcher**
slate sidecar over a good 20-pitcher one, deleting 3,225 lines of the
day's evidence. Cause: the Statcast cache is gitignored, so a fresh
runner has none, and every pitcher failed with `insufficient data
(0 BF)`.

Two fixes:

- `daily_pipeline` now **raises** when DK supplied pitchers to price
  and none could be priced. That is an environment fault, not an empty
  slate, and publishing it is the same class of lie as an odds figure
  we never observed. Verified by pointing `STATCAST_CACHE_DIR` at an
  empty directory: refuses loudly instead of writing.
- The workflow caches `data/statcast_cache` via `actions/cache` and
  tops it up for the current season each run. CI needs only the current
  season (~88 MB) — `daily_pipeline` loads from Mar 26 of the game year
  — not the full 355 MB, since 2024/2025 are for training and the
  production models are already fitted and committed. `backfill()`
  skips days already on disk, so a warm cache makes this a fast no-op.

Also fixed: `reconcile_ledger()` raised `FileNotFoundError` on CI,
where `DATA_STATE_DIR=data` makes the checkout itself the ledger and
there is no second copy to merge. It now detects that and skips.

The 20-pitcher board was restored from `d2ea288`.

## 2026-08-06 — Ledger split-brain between the PC and the container (A-013)

Found while verifying the deploy above. The container's jobs read and
write `DATA_STATE_DIR` (the Railway volume); `git pull` only updates the
`/app` checkout; and `seed_volume_state()` copies image → volume **only
where the volume is missing a file**. After the first boot, nothing
bridged them.

So there were two independent ledgers. The PC writes picks and pushes
to git; the container grades a volume copy that never sees them. And
because the dashboard **prefers the worker's `/data.json`** over the
bundled copy, the site would have shown a record missing the picks —
not an error, just a quietly wrong number. It had been papered over by
manual `FORCE_SEED` deploys, which is why it looked fine.

It would have bitten tonight: PC writes picks at 16:45 ET → git; the
container's 18:15 close and 03:00 grading run against a ledger without
them.

`reconcile_ledger()` now runs after every pull and merges the checkout
into the volume. Union only — rows are added or advanced, never dropped
or downgraded, so the append-mostly rule holds *across machines* and
not just within one. Conflicts resolve by: graded beats ungraded (a
grade is strictly the later state, and reopening one would violate the
locked-picks rule), then later `updated_at`, then the more populated
row. Merge key is `(date, game_pk, pitcher_id, line)` — the pipeline's
own key, verified unique across the ledger with ladder rungs included.
Slate sidecars and odds files compare timestamps read from *inside* the
file (`generated_at` / `captured_at`), never mtime, which git checkout
resets on every deploy.

Two bugs caught by the tests written for it, both of which would have
silently lost data:

- The write was gated on the row **count** changing, so any repo-side
  update that did not also add a row was discarded — i.e. exactly the
  overnight-grading case, where the repo carries a grade for a row the
  volume already has. Now compares content.
- On an `updated_at` tie the incumbent won unconditionally, dropping a
  column the other side had populated (e.g. `odds_source`). Now the
  more complete row wins.

Verified: new PC picks merge in; container grades survive a newer
*ungraded* repo row; grades flow in the other direction too; identical
repo and volume is a byte-level no-op; running it twice changes
nothing.

Still open: the container has no `GITHUB_TOKEN`, so its writes reach
the dashboard (via `/data.json`) but never reach git. That is a
single-point-of-failure on the volume, and the fix needs a credential
only the operator can create (AUDIT A-013).

## 2026-08-06 — Live calibration on /model + snapshot odds hardening

Two independent pieces, both of which failed adversarial review as
first built and were rebuilt around what the review found.

**Live calibration (A-010).** New `live_model` block in
`tools/dashboard_data.py` and a LIVE MODEL section on `/model`,
scoring `data/model_log.csv` against the backtest.

The first cut compared raw live Brier to the flat backtest Brier
(0.1491). That comparison is rigged. The backtest averages a fixed
six-line grid including near-certainties (8.5 → 0.0654); the live log
scores one row per pitcher at whatever number the book hung, and the
book hangs its line where the game is closest to a coin flip. The
irreducible Brier floor on the 8/4 sample is 0.2385 — the baseline sat
0.0894 *below* what a flawless model could have scored. Monte-Carlo
over 4,000 trials of a perfectly calibrated model: **"worse than
backtest" 100% of the time**, at every sample size. A permanent red
alarm is an alarm the operator learns to ignore.

Fixed by comparing **calibration error** (Brier − floor, where floor =
mean p(1−p)) instead of raw Brier. That quantity is comparable across
line mixes: it isolates the part of the score attributable to model
error from the part attributable to the difficulty of the board.
`per_line` in the backtest block now carries `model_floor` and
`model_excess` so the reference is computed on the live sample's own
line mix. Same Monte-Carlo after the fix: **2.0% false "worse"**, and
the 8/4 slate reads *in line with backtest* (+0.0213 live vs −0.0004
backtest, band ±0.0435).

The verdict band is 2 standard errors of the live Brier, not a fixed
0.005. A 1-SE trigger fires on ~32% of healthy slates. The band
tightens on its own as the log grows, so real drift becomes detectable
without retuning a constant. Detection power at n=26 is genuinely low
— the page says so, and points at the leash numbers and the
calibration curve as the faster reads.

Also fixed from review: the sample gate and the observations tile now
count *scorable* rows (both a probability and an outcome) rather than
rows merely present — the old code could advertise 22 observations
beside a 4-row Brier with the warning banner suppressed;
`by_date[].reconstructed` uses `any()` not `all()`, so a 24%
reconstructed date no longer renders as LIVE; the reconstructed parser
fails closed (an unrecognised value counts as contaminated); the
per-date column applies the same noise band as the headline; an
unverified "9x the picks ledger" claim was removed (actual 6.5x); and
the empty state no longer tells the operator to run a command they
have already run — a payload generated before this block shipped is
indistinguishable from an empty log, and only the honest message
covers both.

**Snapshot odds hardening.** `scrape_dk_odds.py` grew a snapshot
fallback so the Railway container can price a slate when DraftKings
returns 403 to its datacenter IP. Review found the fallback could
launder stale odds into the ledger as live prices, which the repo's
"never fabricate odds" rule exists to prevent. Four fixes:

- `tools/closing_odds.py` now pins `allow_snapshot=False`. It was the
  laundering path: it re-dates every row to today, re-stamps
  `captured_at` to now, and drops `odds_source` — so a snapshot fed
  through it became a fresh-looking closing price that the CLV grader
  wrote into the ledger, walked through `daily_pipeline`'s `date ==`
  filter (the only guard against pricing an old board), and reset the
  staleness clock permanently. Losing CLV on a blocked day is cheap;
  recording a wrong closing price is not.
- `captured_at` is now a real column on `dk_k_*.csv`, and the loader
  **refuses** a board without it. The old code fell back to file mtime,
  which git checkout and Docker `COPY` both reset to build time — a
  week-old board arrived on the container looking freshly captured, and
  the age ceiling could never fire. Verified against a real git clone.
- Staleness is judged **per row**, not on the file's newest stamp. One
  refreshed pitcher in an append-log used to re-validate every stale
  row beside it.
- Candidate ordering puts freshness ahead of filename prefix, so a
  two-minute-old `closing_*` board beats a five-hour-old `dk_k_*` one.
  The reverse was true before and contradicted the documented intent.
- `odds_source` is now a `tracker.FIELDS` column, so a snapshot-priced
  bet stays identifiable in the ledger after the fact. Historical rows
  carry `""` — read as "predates provenance tracking", not as live.
- The slate date is checked in the loader itself rather than left to a
  downstream filter, and `daily_pipeline` prints a loud warning when
  any row was priced from a snapshot.

Measurement, not guesswork, on the 403 itself: DK gates on
**User-Agent**, not TLS/JA3 fingerprint (plain `requests` with a
browser UA → 200; default `python-requests` UA → 403; seven curl_cffi
impersonation profiles → all 200). The container is blocked on egress
IP reputation, which no client-side knob changes. That rules out the
"try another impersonation profile" theory outright. Also fixed a
latent bug: the urllib fallback tier could never have worked — it
advertised gzip and never decoded it (`UnicodeDecodeError: 0x8b`).

Self-test grew from 6 to 11 cases, including the two production
scenarios that were previously untestable: old content with fresh
mtime, and a board carrying no stamp at all.

The fallback remains **off by default** and is not enabled on Railway.

## 2026-08-06 — Model log: track every pitcher, not just the bets

Each slate produced ~28 model predictions but we durably recorded the
outcome for only the ~3 we bet. P&L and CLV can only ever evaluate bet
selection — a threshold-filtered, biased sample of the model's
opinions — and at 3/night it takes months to say anything.

`tools/model_log.py` joins every evaluated pitcher's prediction with
the actual result from Statcast into an append-only
`data/model_log.csv` (idempotent per date): ~28 observations a night
instead of 3, a ~9x faster feedback loop, measuring the MODEL rather
than the bet filter. Wired into both night jobs and persisted on the
Railway volume. `--report` prints live calibration, Brier vs the
0.1491 backtest baseline, and — most importantly — workload error,
which is the failure mode that has actually cost money.

Reconstructed slates are flagged and excluded from validation: they
were priced retroactively and their dates may sit inside the training
window. Only live slates count as prospective predictions.

First run (8/4, 26 pairs, reconstructed → diagnostic only) immediately
surfaced what 3 bets never could: mean absolute workload error 3.38 BF
with only 62% of starts within ±3. The worst miss (Yesavage: expected
22.5 BF, faced 7) is correctly NOT a role-gate case — 17 prior
appearances averaging 25 BF, a genuine starter who got knocked out
early. That distinction matters: Anderson was predictable from
history, Yesavage was not.

## 2026-08-06 — Lineup-uncertainty penalty + real-EV gate (A-008, A-009)

Both audit findings implemented as gates rather than parameter tweaks.

**A-008 — unposted lineups now cost edge.**
`PROJECTED_LINEUP_EDGE_PENALTY = 0.05` is added to the threshold
whenever `lineup_source != "confirmed"`, sized to the measured
uncertainty (league-average lineups move P(over) 5.1pp on average).
Applies to primaries and ladder rungs. The morning run mostly sees
unposted lineups, so this deliberately shifts action to the 4:45pm
lineup-lock re-run, where the inputs are real.

**A-009 — a real-EV gate on the actual posted price.**
`MIN_EV = 0.04`: a bet must clear `blended_prob × decimal_odds − 1 ≥
4%`, computed against the vigged price. Break-even is the *vigged*
implied probability, not the de-vigged fair one, so this is the only
threshold that speaks directly in money and it cannot be gamed by the
`ALT_SIDE_MARGIN` assumption. Deliberately did NOT retune that
constant to the measured 24% overround: because `edge = blended −
fair` and `blended` is half market, raising the margin *lowers* fair
faster than blended and makes the system more aggressive — the
opposite of the intent. Both gates must pass; `clears_edge` and
`clears_ev` are reported separately and stored on every rung.

Checked against real graded bets (not fitted to outcomes):

| Bet | Now |
|---|---|
| Henderson UNDER 6.5, projected lineup, lost 2u | **rejected** — edge 7.8% vs 13.1% bar |
| Burke OVER 6.5, confirmed, lost 1u | still bet — fairly priced, simply lost |
| Detmers UNDER 7.5, confirmed, won 1.32u | still bet |
| 97% model view at −2000 | **rejected** — EV −1.6% despite a positive-looking edge |

Burke surviving is the point: the gates filter *uncertainty*, not
losers with hindsight. Board-level effect: 8/4 goes 5 qualifying picks
with confirmed lineups vs 0 with projected; 8/5 goes 3 vs 1.

## 2026-08-06 — Default audit: 7 landmines now fail loudly (A-007/8/9)

Swept every default in the live pricing path under one test: *if this
fires, does it invent an input?* Seven did. All now raise instead of
substituting a league average — a fabricated input inflates a
projection, and the edge filter selects that error into the bet list
at max stake (the Anderson mechanism).

Fixed: Stage A's `c1_bf_mean` → 21.1 BF and `k_pct` → 0.225;
`predict()`'s `lineup_k_pcts` → league lineup and `pitcher_k` → 0.225;
Stage B silently falling back to the matchup formula when unfitted
(a failed model load would have priced a whole slate off the wrong
model); `american_to_implied/decimal` treating odds of 0 as a coin
flip; and `_calc_pnl` booking a WIN at an invented −110. That last one
mattered doubly — `pl_calc` validates the ledger through the same
function, so the fabrication would have been confirmed by our own
drift check.

Verified: all 7 raise, the normal prediction path is unchanged
(E[K] 5.71 on a reference pitcher), and `pl_calc` still reads
2W-4L / −4.01u with no drift.

Two measured findings filed for decision rather than silently patched:

- **A-008** — symmetric error manufactures edge too. Replacing real
  lineups with league averages (what the 10:30am run does, since
  lineups aren't posted) moves P(over 5.5) by 5.1pp on average and up
  to 10.9pp, with 12 of 25 starters over 5pp. Our edge bar is ~7-8pp.
- **A-009** — the alt board's measured overround is ~24% (implied
  47.1% vs realized 37.9%, n=190), not the assumed 4%. Retuning the
  constant would make the system *more* aggressive, not less, because
  of how the market blend enters the edge formula. A real-EV gate on
  the vigged price is the right fix.

## 2026-08-06 — Role gate: no league defaults, no pricing non-starters

Anderson's 0-K line (3.2 IP, 13 BF, all 3.5u lost) was not variance and
not the market hiding information — it was a bad input. The pipeline
fell back to `bf_mean = 21.1` (league-average starter) because he had
< 3 starter-length games in the loaded cache. He is a reliever: 40
appearances averaging 7 BF. That default inflated E[K] 3.1 -> 5.45,
produced a 17pp phantom edge, made him the #1 pick, and drew the
day's biggest stake. DK's line of 2.5 was correct all along.

- No league default, ever: workload now comes from real history.
- `is_startable` role gate — needs >= `MIN_APPEARANCES_TO_PRICE` (3)
  appearances and a recent typical outing >= `STARTER_TYPICAL_BF`
  (15 BF, vs a real starter's 18-22) over the last
  `ROLE_LOOKBACK_GAMES` (8). Stage A is trained on starter workloads,
  so pricing an opener/reliever with it is out-of-distribution.
- Skips print a reason and appear in the run log.

Verified against all 28 pitchers on the 8/5 board: skips Anderson
only (typical recent outing 5 BF), keeps all 27 real starters
(22-23 BF). Also verified against the thin June-onward cache the
10:30am run actually used — the pick would not have been made and
3.5u would not have been risked.

Filed as AUDIT A-007 with the general lesson recorded: an input error
that inflates a projection is selected INTO the bet list by the edge
filter and concentrated at max stake. It fired on 1 of 28 pitchers but
1 of 3 bets.

## 2026-08-06 — Cloud cutover verified; symlink/atomic-write bug fixed

Forced a live grading run on the Railway worker rather than waiting
for 3am. It graded correctly (Detmers UNDER 7.5 WIN 5K +1.32u, Burke
OVER 6.5 LOSS 4K −1.00u; Anderson's three still in progress) — and
then lost the result on the next deploy, which exposed a real bug.

**Symlinked state cannot survive atomic writes.** `bind_state_to_volume`
symlinked `picks_2026.csv` onto the volume, but the repo's atomic-write
pattern is tempfile + `os.replace`, and `os.replace` REPLACES the
destination path — destroying the symlink and landing the write on
ephemeral container disk. The grade looked successful in the logs and
vanished on redeploy.

Fixed by removing symlinks entirely in favour of a single
`DATA_STATE_DIR` root (env-overridable, defaults to the repo's
`data/`) that `tracker`, `daily_pipeline`, `dashboard_data`,
`closing_odds`, `grader` and `reconstruct_slate` all derive from. The
worker sets it to `/data/state`, a real directory on the volume.

Also fixed in the same pass:
- `data/odds/` was gitignored, so the snapshots CLV is computed from
  never reached the worker image — the cloud graded with no CLV while
  local had it. Now tracked (120KB) and backed up.
- `data.json` is derived but ships in the image, so a fresh deploy
  served a payload older than the volume's ledger. Now rebuilt from
  volume state on every boot.
- `RUN_TASK_ON_BOOT` (force one job immediately) and `FORCE_SEED`
  (resync volume state from the image) added as operational controls.
  Both are removed after use — leaving FORCE_SEED set would overwrite
  the worker's own writes on every deploy.

Verified end to end: worker endpoint and local `pl_calc` agree at
**2W-4L, −4.01u**, first CLV recorded (n=2, avg −0.51%), and
mlb-strikeouts.vercel.app renders it live from the worker.

## 2026-08-06 — Railway worker (cloud migration) + two defect fixes

The pipeline moves off the operator's PC to Railway project
`mlb-strikeouts`, service `worker`, with a persistent volume at
`/data` holding the Statcast cache and the scheduler's job state.

Why Railway rather than GitHub Actions (which NRFI uses): closing-odds
snapshots are unrecoverable once a game starts, and GitHub's
`schedule` trigger is best-effort — NRFI's own workflow documents it
firing 1–3 hours late and compensates with hourly runs plus a Vercel
`workflow_dispatch` poke. A resident worker fires on the minute, keeps
a warm ~350MB cache on disk instead of re-downloading, and can host
the heavy jobs (backtest, retrain, gauntlet) later. Declaring times in
America/New_York also makes the schedule DST-agnostic by construction.

- `tools/railway_worker.py`: ET-aware scheduler, per-task lateness
  grace (close 45m, lineups 2h, morning/night 6h), job state persisted
  to the volume so restarts resume mid-day.
- Ledger, journal, slate sidecars and odds snapshots are symlinked
  onto the volume (seeded from the image once) — without this a
  redeploy would reset the ledger to the last commit.
- The worker serves `/data.json` and `/health` over HTTP and the
  dashboard reads the live endpoint (static build is the fallback),
  so the system needs NO push credential and picks appear without
  waiting for a rebuild. Git mirroring is optional backup.
- Live: `https://worker-production-036c.up.railway.app`.
- `Dockerfile`, `requirements.txt`, `.dockerignore` (cache excluded
  from the build context), `STATCAST_CACHE_DIR` override, and
  `models/*.pkl` now tracked (157KB) so the image carries the model.

**Two real defects found while wiring this up:**

1. `run.py backfill` has been broken since it was written — it
   imported `backfill_range`, but the function is `backfill`. Every
   cache-refresh invocation would have died on ImportError.
2. Neither the local nor the planned cloud automation ever refreshed
   the Statcast cache. Bullpen fatigue reads YESTERDAY's relief usage,
   so the Phase 12 leash inputs were silently degrading as the cache
   aged. Both night tasks now backfill before grading.

Cutover is deliberately staged: the local Windows tasks stay enabled
until the worker is verified pushing to GitHub, then get disabled.

## 2026-08-05 — Daily cap 6u → 10u; full pre-game restake to clean denoms

- DAILY_MAX_UNITS raised 6.0 → 10.0 (operator direction): the 3.5u
  ladder trio plus normal primaries regularly exceeded 6u.
- All 2026-08-05 picks restaked to clean denominations BEFORE first
  pitch (every game verified Pre-Game/Warmup at edit time; all changes
  journaled to pick_changes.csv): Anderson OVER 2.5 → 2.00u, 4+ K
  added at 1.00u @ +134 (latest captured board price), 5+ K → 0.50u;
  Burke OVER 6.5 1.30u → 1.00u; Detmers already clean at 2.00u.
  Day total 6.5u.

## 2026-08-05 — Clean stake denominations (operator rule)

All published stakes quantize to {0.25, 0.5, 1, 1.5, 2} units
(`models/staking.py::quantize_stake`): >= 0.75 rounds to the nearest
whole unit, smaller stakes to 0.5/0.25, below 0.125 is no bet. The
daily 6u cap no longer produces fractional partial fills — a pick that
doesn't fit steps DOWN to the largest denomination that fits or is
dropped. Ladder rungs quantize downward within their halving caps, so
a 2u primary yields exactly 2 / 1 / 0.5; LADDER_MAX_UNITS raised
3.0 → 3.5 to fit the template. Applies from the next slate.

## 2026-08-05 — Descending ladder stakes (line-gap defense, operator rule)

When the market's line sits far below the model's projection (Anderson:
line 2.5, projection 5.4), the line placement itself is leash
information — the book expects a short outing. The operator's answer:
keep the most money on the leash-proof market line and taper up.
Ladder allocation is now nearest-rung-first with stakes that halve per
step: rung cap = primary × 0.5^distance (1.70u primary → 4+ K ≤
0.85u, 5+ K ≤ 0.43u, ~2.97u total under the 3u cap). Replaces
best-edge-first allocation. Gap gate, next-2-rungs, and the 10% edge
bar are unchanged. Applies from the next slate; tonight's placed bets
stand as written.

## 2026-08-05 — Ladder table readability round 2 (operator feedback)

- Rungs display in strict line order; bet rungs are highlighted in
  place, never re-sorted to the top.
- Ladder section header shows parts and stake: "N rungs bet · X.XXu".
- The primary-equivalent rung always carries full odds/model/fair/edge
  data: evaluate_ladder stores it going forward; the 2026-08-05
  sidecar was backfilled from the day's first closing-odds capture
  (real DK prices, never fabricated) + the stored distributions and
  production calibrator — 28 pitchers patched.

## 2026-08-05 — Phase 12: Leash inputs + lineup-lock re-run

The two changes most likely to add real edge (operator-directed):

### Stage A leash inputs (were stubbed to "no" since Phase 2)

- **il_return** — start after a 25+ day absence (`IL_GAP_DAYS`),
  computed as-of from `days_since_prior` in the pitcher table. Trained
  coefficient **−0.122**: a returning pitcher faces ~2-3 fewer batters.
- **bp_heavy** — the team's bullpen threw ≥ 90 relief pitches the
  previous day (`BP_HEAVY_PITCHES`; pitching team via inning_topbot,
  relief = non-starter pitches; `features/asof.py::
  team_relief_pitches_by_date` / `bullpen_fatigue_table`). Trained
  coefficient **+0.028**: a taxed pen stretches the starter (matches
  the raw data: 22.9 vs 21.2 mean BF).
- **pitch_limit** — operator entries in `data/manual_pitch_limits.csv`
  now load live per date/pitcher and cap Stage A's expected BF at
  limit/4. Untrained historically (announced limits unknowable) —
  the live cap does the work.
- Cross-season validation IMPROVED with the new inputs:
  24→25 +4.0% (was +3.8%), 25→24 +4.9% (was +4.8%), 24+25→26 +3.2%
  (unchanged). Production Stage A/B refit; calibrator refit.
- The pipeline logs leash flags per pitcher when any input fires.

### Lineup-lock re-run (4:45 PM ET scheduled task)

Morning picks price mostly with league-average lineups (lineups not
posted at 10:30 AM). A new `lineups` task re-predicts when lineups are
confirmed — tonight's dry run showed Detmers' E[K] move 5.4 → 7.1 on
lineup information alone. To make same-day re-runs safe, the pick
writer now enforces the money rule mechanically: existing
bet_placed=Y rows keep their odds, side, stake, label, and created_at
frozen; only model probs, lineup_source, and updated_at refresh; a
side/strength flip is journaled to data/pick_changes.csv (never
applied to the placed bet). New edges that emerge with lineups become
NEW picks under the normal caps.

## 2026-08-05 — Ladder discipline: gap gate, next-2 rungs, half-stakes

Operator rules, confirmed via questions before implementation
(models/ladder.py):

- **LADDER_GAP_MIN = 1.5** — the ladder fires only when the primary is
  a placed OVER bet and E[K] beats the line by 1.5+ (line 6.5 needs a
  projection of 8.0+). No gate, no rungs. Under primaries and no-bet
  pitchers never ladder.
- **LADDER_RUNG_COUNT = 2** — only the next two lines above the
  primary (6.5 → alt 7.5 + 8.5, i.e. 8+ and 9+ K).
- **LADDER_RUNG_STAKE_FRACTION = 0.5** — each rung caps at half the
  primary stake, under quarter-Kelly, the 2u per-bet cap, and the 3u
  pitcher cap. The 10% edge bar is unchanged.
- New pass statuses (gap gate / beyond next 2 rungs) flow to the
  dashboard ladder table. Synthetic tests cover the gate, eligibility,
  stake cap, under-gate, and no-primary-gate paths. Historical bets in
  reconstructed slates keep their BET flag from the ledger; the pass
  reasons shown are the current rules' view.
- Under the new rules, yesterday's Ginn 6+ ladder (gap 0.7) would not
  fire; today's Anderson 5+ (gap 2.9, next-rung, would size 0.85u vs
  the 1.00u placed) remains a qualifying ladder.

## 2026-08-05 — Chart outcome colors, side-labeled pick line, alt-under probe

- The dashed pick line on the K-distribution now reads "OVER 4.5" /
  "UNDER 6.5" (side + line), not just the number.
- The actual-strikeouts bar is outcome-colored: red only when EVERY
  graded bet on the card lost (primary and any ladder rungs), green
  when any bet won, neutral while ungraded or unbet. Result badges
  still carry the words (hue never carries meaning alone).
- `scrape_dk_odds.py --probe-unders` checks the three candidate DK
  subcategories that could carry an under-side alt strikeout market
  (16217/16268/12975 — all empty at evening probe on 2026-08-05).
  The morning automation runs the probe daily and logs loudly if one
  ever populates, at which point real under prices get wired into the
  ladder display and evaluation.

## 2026-08-05 — Under-card ladders read as unders (operator feedback)

On UNDER cards, non-bet ladder rungs now display as their under-side
twin: "6+ K" (six or more) flips to UNDER 5.5 (five or fewer), with
under-probabilities for model and fair. The primary bet appears in
sequence as its own line ("UNDER 6.5 · = primary bet") with its
result. DK posts no under prices on the alt board, so the over price
shows muted with an "o" prefix and a footnote — provenance without
fabrication (money rule: never fabricate odds). Real over-side bets
keep over framing regardless of card side.

## 2026-08-05 — Pick card readability (operator feedback)

- **Complete ladder sequence.** The rung equal to the primary line's
  ceiling is no longer silently skipped: `evaluate_ladder` keeps it
  with status `primary_equivalent` (never bettable). The card shows it
  in order, labeled "= primary bet (OVER x.5)" — with the primary's
  result badge — or "= inverse of primary (UNDER x.5)". Older sidecars
  without the row get a synthesized marker row (no odds fabricated).
- **Bets stand out.** Bet rungs sort to the top of the ladder table
  with an amber left border, tinted background, and bold BET label;
  passed rungs stay dim below.
- **Every card reads from the pick's side.** On UNDER cards all
  probabilities (model raw/calibrated/blended/market fair) now display
  as under-probabilities, with an explicit caption ("All probabilities
  are P(UNDER 6.5) — the chance this side wins") and a side-aware
  distribution label. Over and under cards now read identically:
  bigger number = better for the bet.
- 8/4 reconstruction re-run (picks up real alt-board odds for the
  equivalent rungs; re-priced by the current Phase 10 core model).

## 2026-08-05 — Phase 11: Daily automation (Windows Task Scheduler)

Five user-level scheduled tasks now run the daily rhythm unattended
(`tools/scheduled_run.py`, machine is Eastern time):

- **10:30 AM — Morning Picks**: grade yesterday, predict today, write
  slate sidecar, regenerate dashboard data, auto-commit ledger, push,
  deploy dashboard.
- **12:15 / 3:00 / 6:15 PM — Closing snapshots**: append-only
  closing-odds captures; the grader uses the last capture before each
  game's own start time, so day games and night games each get an
  honest close.
- **3:00 AM — Night Grading**: grade the finished slate (fills CLV),
  regenerate + commit + push + deploy.

Every step logs to `logs/auto_YYYY-MM-DD.log` (gitignored); a failed
step is logged and the remaining steps still run. Tasks are
StartWhenAvailable + WakeToRun, but run only while the operator is
logged in (no stored credentials, by design) — keep the PC on.
Auto-commits are message-prefixed `chore(auto):`.

## 2026-08-05 — Phase 10: Feature re-gauntlet — all three T2 promotions demoted

Closed AUDIT A-005. The Phase 6 gauntlet promoted a9_zone_pct,
f1_eastward_tz, and b14_n_rookies using leaky full-window aggregates
and ~800-game splits. Re-tested on the Phase 9 cross-season harness
(`tools/regauntlet.py`): five Stage B variants (full / core-only /
drop-one-each) scored on the IDENTICAL 12,653 out-of-sample starts,
feature value measured as paired per-start Brier deltas with
t-statistics — no arbitrary noise floor.

**Verdict: DEMOTE all three.** No feature cleared drop-delta t ≥ 2 in
both temporal directions (all |t| ≤ 1.7); the core model matched the
full model within ±0.00006 Brier on every split and was marginally
BETTER on the 2026 decision split (0.14906 vs 0.14909). The old
promotions were noise laundered by the leaky harness — consistent with
its own negative-control finding that random features passed at that
sample size.

- `models/stage_b_rate.py`: StageB now supports feature subsets
  (extra_features, persisted in the pickle);
  `PRODUCTION_EXTRA_FEATURES = []` is the single source of truth.
- Production Stage B refit core-only (intercept +1.343,
  logit_pitcher_k +0.935, logit_batter_k +1.065, TTO2 −0.142,
  TTO3 −0.211). The model's edge is entirely: pitcher K%, batter K%s,
  TTO decay, and the Stage A leash — everything else must re-earn its
  slot through the cross-season bar.
- Decision-split predictions + calibrator regenerated against the
  core model; dashboard Model view shows the re-tested verdicts
  (marked ↻), superseding the leaky-harness rows.
- Feature extractors remain in `features/t2_candidates.py` and the
  pipeline still records zone/travel/rookie values in slate sidecars
  (they're informational); the model simply doesn't use them.

## 2026-08-05 — Phase 9: Multi-season backfill + cross-season validation + production refit

Closed AUDIT A-004: backfilled Statcast 2024 (724,076 pitches), 2025
(725,775), and the missing Apr–May 2026 (cache now 1.95M pitches,
2024-03-28 .. 2026-08-04). This unlocked the repo's sanctioned
three-way validation, rebuilt in `backtest.py` as a split-driven
harness (seasons loaded separately so as-of priors RESET at season
boundaries, matching live serving).

**Cross-season results (all features as-of, models fit on train
seasons only):**

| Split | Test starts | Naive Brier | Model Brier | Improvement |
|---|---|---|---|---|
| train 2024 → test 2025 | 4,807 | 0.1539 | 0.1480 | +3.8% |
| train 2025 → test 2024 | 4,713 | 0.1572 | 0.1496 | +4.8% |
| train 2024+2025 → test 2026 | 3,133 | 0.1540 | 0.1491 | +3.2% |

Positive in BOTH temporal directions and on the decision split —
12,653 out-of-sample starts, positive at every line in every split.
The promotion gate passed, so:

- Production Stage A/B refit on 2024+2025+2026 via
  `tools/retrain_production.py` (12,653 starts / 267,257 PA).
  Stage A `season_k_pct` resolves to +0.317 — the "strikeout pitchers
  earn longer leashes" effect the two-month sample couldn't identify.
  Stage B: pitcher +0.938, batter +1.066, TTO2 −0.141, TTO3 −0.210,
  zone +0.287; eastward_tz (−0.016) and n_rookies (−0.006) are
  near-zero — re-gauntlet pending (A-005).
- Calibrator refit on the 18,798 decision-split OOS predictions
  (cross-fit check): mid-line bias −2pp → within ±1pp.
- `data/backtest_meta.json` now records the split; the dashboard
  Model view reads it instead of hardcoded labels.
- Placed picks are untouched (ledger locks); the new model prices
  slates from 2026-08-06 onward.

## 2026-08-05 — Phase 8: Dashboard rebuild (Next.js + 21st.dev)

Replaced the single-file static dashboard with a Next.js App Router app
(static export, same Vercel project). Built with Tailwind v4 + real
21st.dev components retrieved via the operator's account:
@originui/accordion (ladder tables), @ssicevs/market-snapshot (P&L
chart: pointer scrubbing, hovered value reads into the header, period
switcher), @aghasisahakyan1/expandable-card interaction (card corner
button rotates 45° on expand; operator's bookmark). Interaction
vocabulary ported from the NRFI Terminal survey.

- **Slate view (/)** — date stepper ◂ select ▸ with LIVE / PAST · nd
  ago (click → jump to newest) / SCHEDULED badge; `?date=` URL param is
  the source of truth (shareable slate links); segmented filters
  (side / bets / graded) + pitcher find persisted to URL +
  localStorage; expandable pick cards (whole card is the button,
  aria-expanded, multi-pin Set) showing the probability pipeline (raw →
  calibrated → blended vs fair → edge vs bar), the full P(K=k)
  histogram with book line + actual K marked, and the COMPLETE ladder
  table — every evaluated rung with model/fair/edge and its
  bet-or-passed reason. #1 badge on the slate's best-edge bet.
- **Performance view** — KPI tiles (record, P&L, ROI, avg CLV),
  cumulative P&L line + daily bars with hover scrubbing and 7D/30D/
  Season tabs, splits by side / strength / primary-vs-ladder,
  every-bet ledger with CLV column and date links into slates.
- **Model view** — honest out-of-sample backtest front and center
  (0.1481 vs 0.1505, 618 starts, split methodology in plain English),
  calibration curve (raw vs calibrated vs perfect diagonal),
  Brier-by-line bars, full gauntlet table with 5 gate dots and
  verdicts from data/gauntlet_results.json.
- **/brief** — filming page: today's bets, big type, zero chrome.
- Data layer v2 (`tools/dashboard_data.py`): per-date slates (sidecar +
  ledger merged, actual K from Statcast cache), performance aggregates,
  model analytics; P&L exclusively via tracker._calc_pnl; FlatUnits
  guard runs before every write; output to dashboard/public/data.json.
- Deploy: vercel.json builds the subdirectory app (static export,
  cleanUrls); trailingSlash for dumb-static-host compatibility.

## 2026-08-05 — Phase 7: Model truth audit — leakage fix, calibration, market shrinkage

A full pipeline audit after the first live slate (1W-3L, -4.34u) found
three structural defects. All fixed in this release. **Every previously
published backtest number is superseded by the honest numbers below.**

### Defects found

1. **Isotonic calibration was dead code.** `IsotonicCalibrator` was
   constructed but never fit, persisted, loaded, or applied — in the
   live path or the backtest. Live picks shipped raw model
   probabilities, which is why all 4 first-slate picks claimed
   implausible 22-25pp edges vs DraftKings.
2. **Backtest leakage.** `backtest.py` computed season K%, BF stats,
   zone%, batter K%, and rookie counts over the FULL test window —
   every prediction saw its own game and future games. Stage A/B were
   also trained on the same window the backtest scored. The published
   "+2% vs naive" was contaminated. With leakage removed and nothing
   else changed, the model showed NO edge over naive (0.1509 vs 0.1505).
3. **Ladder edges overstated.** One-sided milestone edges were computed
   against raw vig-inclusive implied probability (no de-vig) with a
   flat 3% threshold — a materially looser bar than the primary
   market's hold+2%, sharing the same edge column. Ladder rows also
   wrote the MODEL prob into `no_vig_fair_prob`.

### Fixes shipped

- **Vectorized as-of features** (`features/asof.py`):
  `asof_pitcher_game_table` / `asof_batter_game_table` — per-entity
  per-game cumulative stats via sort + cumsum-minus-current; the
  current game can never leak into its own features. Stage A/B
  training preps and the backtest all rebuilt on these.
- **Empirical-Bayes shrinkage** (`features/asof.py::shrink_rate`):
  pitcher K% (70 BF pseudo-count) and batter K% (60 PA) shrunk toward
  league average. This restored the honest edge: thin as-of samples
  are mostly noise without it. Live pipeline batter rates now use the
  same shrinkage as training (was raw with a 30-BF cutoff).
- **Honest backtest** (`backtest.py`): within-2026 time split — train
  ≤ Jul 8, test Jul 9–Aug 3, all features as-of. Result:
  **model Brier 0.1481 vs naive 0.1505 (+2%)**, positive at 5 of 6
  lines. Saves per-game predictions to `data/backtest_predictions.csv`.
- **Isotonic calibration wired live**
  (`tools/fit_calibrator.py`, `models/calibration.py` save/load,
  `strikeout_predictor.py`): fit on out-of-sample predictions with a
  cross-fit honesty check; corrects the model's systematic 2-4pp low
  bias (mid-lines now within ±0.5pp). Applied to per_line and exposed
  as `calibrate_prob()` for milestone tails.
- **Market-anchored shrinkage** (`models/edge.py::MODEL_TRUST_WEIGHT`
  = 0.5): betting probability = 50/50 blend of calibrated model and
  no-vig market fair. Edge = w·(model − fair). First-slate-style picks
  compress from ~22pp claimed edges to ~9-11pp, demoting STRONG → LEAN
  with smaller Kelly stakes. Revisit weight after 100 graded bets.
- **Ladder honesty** (`models/ladder.py`): assumed one-side margin
  de-vig (`ALT_SIDE_MARGIN` = 4%), blended edge, threshold raised to
  `LADDER_EDGE_THRESHOLD` = 10% (2×margin + 2pp). Re-priced 8/4: the
  losing Ginn 6+ rung correctly fails the new bar. True fair prob now
  written to `no_vig_fair_prob`; ladder odds stored with explicit sign.
- **Slate sidecars** (`data/slates/YYYY-MM-DD.json`): pipeline now
  persists every evaluated pitcher — full P(K=k) distribution,
  expected K/BF, and EVERY ladder rung with bet/passed status
  (previously 212 of 213 evaluated rungs were destroyed).
  `tools/reconstruct_slate.py` rebuilt 2026-08-04 from archived odds
  snapshots (26 pitchers, flagged `reconstructed: true`).
- **CLV capture** (`tools/closing_odds.py`, `run.py close`):
  timestamped closing-odds snapshots; grader fills
  `closing_over_odds` / `closing_under_odds` / `clv_pct` (fair prob at
  close minus at open, pick side) as it grades. Three new ledger
  columns appended to tracker FIELDS.
- Production Stage A/B refit on as-of features, full window:
  Stage A season_k_pct coefficient now sensible (+0.064, was −0.11
  degenerate under leaky fit); Stage B pitcher/batter logits ≈ +1.06
  each. Optimizer stabilized (bounded dispersion, clipped log PMF).

### Honest-model caveats (recorded, not hidden)

- With honest features, `n_rookies` (+0.009) and `eastward_tz` (−0.05)
  are marginal; the T2 promotions should be re-gauntleted against the
  honest harness (A-005).
- Statcast cache holds ONLY 2026-06-01..08-04. The documented
  2024-2025 backfill does not exist on disk (A-004); the three-way
  cross-season split is impossible until it runs.

## 2026-08-04 — Phase 6: T2 feature gauntlet and 3 promotions

- Built T2 feature extraction module (`features/t2_candidates.py`):
  - 20 extraction functions across Groups A/B/C/D/E/F/H.
  - T2_REGISTRY metadata: gate1 leakage flag, expected sign/magnitude,
    collinear partners.
  - Master builder `build_t2_features()` for all T2 features.
- Built 5-gate gauntlet runner (`tools/gauntlet.py`):
  - Gate 1: leakage audit from registry metadata.
  - Gate 2: within-2026 three-way OOS (June/July/August splits).
  - Gate 3: coefficient sign and magnitude sanity check.
  - Gate 4: collinearity check against known pairs.
  - Gate 5: Brier improvement confirmation from Gate 2 results.
  - Memory-efficient pitcher-grouped caching (236K pitches, 651 pitchers).
  - Vectorized baseline precomputation across all games and lines.
  - Extended gauntlet for lineup, travel, and game-context features
    derived from Statcast (no external API needed).
  - Atomic JSON merge on save to prevent batch overwrites.
- Ran 16 features through the full gauntlet (10 Statcast + 6 extended):
  - **PROMOTED (3)**: a9_zone_pct, f1_eastward_tz, b14_n_rookies.
  - REJECTED (13): a10_fps_pct, a18_spin_delta, a20_extension,
    c5_tto_decay, c7_prior_pitches, c8_days_rest, c9_season_bf,
    c16_is_debut, f7_month_factor, b12_lineup_recent_k_pct,
    c13_is_doubleheader, f3_days_in_tz, f4_consec_road.
  - 4 features require external data (c14, d6, e8, h4): deferred.
- Wired all 3 promoted features into Stage B production model:
  - Stage B design matrix: 8 features (was 5).
    zone_pct=+0.139, eastward_tz=-0.017, n_rookies=-0.012.
  - Threaded through `strikeout_predictor.py`, `backtest.py`,
    `tools/daily_pipeline.py`.
  - Backtest: Brier 0.1297 vs 0.1321 naive (+2%), beats baseline
    at every line.
- Ran negative controls and noise floor calibration:
  - Calibrated Gate 2 noise floor via 20 random seeds: 95th
    percentile of min(split_A, split_B) = +0.167%.
  - Lunar phase: correctly REJECTED (-0.03% in one split).
  - Per-row random and shuffled K%: both PASSED the noise floor
    (min improvements +0.23%), exposing that the add-one test on
    ~800-game splits has limited power at <0.3% effect sizes.
  - All 3 promoted features are above the noise floor but marginal
    (zone_pct min=+0.17%, eastward_tz min=+0.24%, n_rookies min=+0.21%).
  - Aggregate backtest (+2% Brier over naive on 1777 games) is the
    stronger evidence of signal. Shadow period is the definitive test.
  - Gate 2 now enforces calibrated noise floor (0.167%) as minimum
    improvement threshold in both temporal directions.
- Full gauntlet results logged in `docs/GATES.md`.

## 2026-08-04 — Phase 5: Dashboard redesign (Dark Terminal)

- Complete UI redesign inspired by 21st.dev component patterns.
  Dark editorial sports terminal aesthetic replacing the original
  Newsprint light theme.
- New palette: near-black canvas (#08080A), emerald over (#10B981),
  rose under (#F43F5E), amber accent (#F59E0B).
- Typography: Outfit display font + DM Mono for figures.
- Pick cards: glass-morphism surfaces, gradient left-border accents
  (green=OVER, rose=UNDER), animated edge progress bars with glow,
  LADDER badge in amber pill.
- Hero stats row: 4 KPI cards (record, hit rate, P&L, ROI).
- SVG P&L curve with gradient area fill and endpoint marker.
- Subtle noise texture overlay and radial gradient atmosphere.
- Staggered card entrance animations, live-pulse indicator.
- Brief filming page updated to match dark terminal aesthetic.
- Original Phase 5 entry (Newsprint) below for history.

## 2026-08-04 — Phase 5: Dashboard (original Newsprint)

- Built Newsprint-themed dashboard (`dashboard/index.html`):
  - Mobile-first layout (480px max-width), readable in 30 seconds.
  - Record bar: W-L, hit rate, P&L, ROI — all from canonical source.
  - Today's picks with full detail cards: side, line, odds, stake,
    edge, model P(O), lineup source, result.
  - Ladder picks distinguished with ochre left border + LADDER tag.
  - SVG P&L curve (green gain, crimson loss, dashed zero line).
  - Recent results history section.
  - Newsprint palette: #FBFAF7 background, #211E1A ink, square
    corners, Inter prose, JetBrains Mono figures.
- Built filming brief page (`dashboard/brief.html`):
  - Stripped-down layout for recording video content.
  - Larger typography, centered header, just picks + summary.
  - Same Newsprint palette, same data source.
- Built FlatUnits/CumulativeUnits compile-time guard (`tools/pnl_guard.py`):
  - Validates every P&L field in dashboard JSON has basis tag.
  - Rejects bare floats, missing basis keys, wrong basis labels.
  - Wired into `dashboard_data.py` — runs before every write.
  - CLI: `python tools/pnl_guard.py` to validate standalone.
- Dashboard data API (`tools/dashboard_data.py`): all P&L values
  tagged with `{"value": float, "basis": "flat_100u"}`. Moving-basis
  sums never reach the renderer.

## 2026-08-04 — Phase 4: Ladder betting and production ops

- Built ladder/milestone betting system (`models/ladder.py`):
  - Evaluates P(K >= milestone) at every DK alt line (3+, 4+, ..., 10+).
  - Computes edge vs DK milestone odds independently per rung.
  - Per-rung quarter-Kelly sizing, capped at 2u per rung.
  - Per-pitcher ladder cap of 3u total across primary + all rungs.
  - Allocation is best-edge-first: highest-edge rung gets funded first.
- Integrated ladder into daily pipeline (`tools/daily_pipeline.py`):
  - Fetches 213 milestone lines from DK alt endpoint.
  - Groups by pitcher, evaluates each against the model's K distribution.
  - Ladder picks tracked with `line=N+` and `notes=ladder` in CSV.
  - First ladder run: J.T. Ginn OVER 4.5 (primary, 1u) + 6+ K (ladder, 1u).
- Built auto-grading pipeline (`tools/grader.py`):
  - Fetches actual K counts from MLB Stats API boxscores.
  - Grades: WIN/LOSS for primary and milestone, PUSH on whole-number,
    VOID on scratched starters, POSTPONED on suspended games.
  - Caches boxscores per (game_pk, pitcher_id) to avoid redundant API calls.
  - Locks graded picks via tracker's 3 defensive locks.
- Built production run script (`run.py`):
  - `python run.py` — full cycle: grade yesterday, show P&L, predict today.
  - `python run.py predict` — today's picks only (with `--no-ladder`).
  - `python run.py grade [DATE]` — grade a specific date.
  - `python run.py status` — show record and P&L.
  - `python run.py backfill` — refresh Statcast cache.

## 2026-08-04 — Phase 3: Edge computation and daily pipeline

- Built edge computation module (`models/edge.py`):
  - American odds to implied probability conversion.
  - No-vig fair probability by normalizing both sides.
  - Vig-adjusted edge threshold: hold% + 2% margin, floor 3%.
  - Pick strength classification (STRONG/MEDIUM/LEAN/NO_PLAY).
- Built quarter-Kelly staking engine (`models/staking.py`):
  - Fractional Kelly at 1/4, capped at MAX_STAKE_UNITS (2.0).
  - Portfolio-level daily cap (6u) with 15% correlation haircut
    for same-game picks.
- Built daily prediction pipeline (`tools/daily_pipeline.py`):
  - Fetches MLB schedule via Stats API.
  - Fetches DK strikeout prop odds via `scrape_dk_odds.py`.
  - Matches DK pitcher names to MLB API probables with Unicode
    accent normalization.
  - Computes pitcher/batter features from Statcast cache.
  - Runs compound model, computes edge, sizes bets.
  - Writes qualifying picks to `data/picks_2026.csv` via tracker.
  - Respects pick locking (3 defensive locks from tracker.py).
- First live run: 2026-08-04, 15 games, 29 DK props, 26 analyzed,
  3 picks generated (6u total). All STRONG-rated.
- Updated `ROADMAP.md` with Phase 1–3 completion status.

## 2026-08-04 — Phase 2: Feature engineering and model fitting

- Built all 5 feature builders:
  - `features/pitcher.py` (Group A: 11 T1 features)
  - `features/lineup.py` (Group B: 13 T1 features)
  - `features/workload.py` (Group C: 6 T1 features)
  - `features/umpire_catcher.py` (Group D: 4 T1 features)
  - `features/park_weather.py` (Group E: 7 T1 features)
- Built training data assembler (`models/training_data.py`): game-level
  and batter-level tables with TTO assignment.
- Fitted Stage A (BF model): negative binomial regression, corr = 0.777.
- Fitted Stage B (per-batter K rate): logistic regression with TTO
  decay and matchup structure. TTO 1->3 captures -0.23 logit units.
- Wired end-to-end predictor (`strikeout_predictor.py`): Stage A ->
  Stage B -> Poisson-binomial DP -> P(K >= line).
- Completed isotonic calibration (`models/calibration.py`): PAV with
  fit/predict methods.
- Backtest (`backtest.py`): compound model beats naive baseline at
  every line. Overall Brier 0.1298 vs 0.1321 (+2%).

## 2026-08-04 — Phase 1: Data layer and baselines

- Built `data/backfill_statcast.py` — Statcast pitch-level backfill
  with parquet caching, parallel=False, stale-cache protection.
- Built `features/asof.py` — anti-leakage feature computation from
  pitch-level data. Verified leak-free on real games.
- Built `data/game_context.py` — MLB Stats API + weather forecasts.
- Built `data/id_crosswalk.py` — Chadwick Bureau player ID mapping.
- Rewrote `scrape_dk_odds.py` — Nash endpoint, curl_cffi TLS
  impersonation, Chrome header fingerprint. Pulls O/U and alt lines.
- Computed variance decomposition on real data
  (`tools/variance_decomposition.py`): 57% Bernoulli noise, 48%
  signal, −4% residual. Modeling ceiling ~24–33% of Var(K).
- Computed K%-by-TTO (`tools/tto_analysis.py`): TTO 1→3 decay is
  −3.8 pp (16% decline), talent-adjusted. Largest systematic effect.
- Built distributional naive baseline (`tools/naive_baseline.py`):
  Brier = 0.1507 (coin-flip = 0.25). Bias < 1.2 pp at all lines.
  Every future model must beat these numbers.
- Updated `docs/KB.md` with empirical variance decomposition, TTO
  analysis, and naive baseline scores.

## 2026-08-04 — Phase 0: Skeleton and reconnaissance

- Created repo layout with all stub files.
- Wrote `CLAUDE.md` and `AGENTS.md` (scoped import from NRFI Terminal).
- Wrote `PRODUCT.md` with mission, constraints, and scope.
- Wrote `docs/FACTORS.md` — 114 rows across 8 groups, 44 T1 features.
- Wrote `docs/KB.md` — system overview and variance decomposition.
- Wrote `docs/QUARANTINE.md` — T3 factors and why they're parked.
- Wrote `docs/GATES.md` — gate result log (empty, ready for Phase 2).
- Wrote `tools/check_sources.py` — endpoint health checker.
- Surfaced §3.4 licensing decision to the operator.
