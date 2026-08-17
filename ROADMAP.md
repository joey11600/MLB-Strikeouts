# Roadmap

## Phase 0 — Skeleton and reconnaissance ✅
- [x] Read NRFI reference repo
- [x] Create repo layout with stub files
- [x] Write CLAUDE.md and AGENTS.md
- [x] Write docs/FACTORS.md
- [x] Verify data sources (tools/check_sources.py)
- [x] Surface licensing decision

## Phase 1 — Data layer and the honest baseline ✅
- [ ] Backfill Statcast 2024–2026 — **CORRECTED 2026-08-05: only
  2026-06..08 exists on disk (AUDIT A-004). Multi-season backfill
  still required for cross-season splits.**
- [x] Build features/asof.py (the as-of-date utility)
- [x] Build game-context store (probables, lineups, venue, umpire, weather)
- [x] Build ID crosswalk from Chadwick
- [x] Recompute §1.1 variance decomposition on real data
- [x] Compute K%-by-TTO, league-wide, controlling for batter quality
- [x] Build distributional naive baseline and record Brier score

## Phase 2 — T1 features and two-stage model ✅
- [x] Implement 44 T1 features (Groups A–E)
- [x] matchup.py with f(L,L)==L unit test
- [x] Stage A: P(BF = n) — negative binomial, corr=0.777
- [x] Stage B: per-batter p_i — logistic with TTO decay
- [x] compound.py: Poisson-binomial DP
- [x] Isotonic calibration on P(K >= line)
- [x] Backtest: Brier 0.1298 vs 0.1321 naive (+2%)

## Phase 3 — Edge computation and daily pipeline ✅
- [x] Edge module: no-vig fair probability, vig-adjusted threshold
- [x] Quarter-Kelly staking with 2u cap and 6u daily portfolio cap
- [x] Daily pipeline: schedule → DK odds → features → predict → edge → picks
- [x] Name matching (DK ↔ MLB API, accent normalization)
- [x] Picks written to tracker CSV with atomic writes
- [x] First live picks: 2026-08-04 (3 picks, 6u total)
- [ ] Re-fit matchup formula constant a
- [ ] Run negative controls

## Phase 4 — Ladder betting and production ops ✅
- [x] Ladder/milestone betting: evaluate all DK alt lines (6+, 7+, 8+)
- [x] Per-pitcher ladder cap (3u) with best-edge-first allocation
- [x] Auto-grading pipeline: boxscore K fetch, WIN/LOSS/PUSH/VOID
- [x] Production run script (`run.py`): single entry point for operator
- [x] Atomic ledger with void/push/scratch grading
- [x] Daily cron / scheduled task — 5 Windows scheduled tasks
  (morning picks, 3 closing snapshots, night grading), Phase 11
- [ ] Supabase mirror
- [ ] Telegram alerts
- [ ] Loss-cluster pipeline
- [ ] Kill switch

## Phase 5 — Dashboard ✅
- [x] Newsprint palette (warm paper, dark ink, square corners)
- [x] Mobile-first slate view (`dashboard/index.html`)
- [x] /brief page for filming (`dashboard/brief.html`)
- [x] FlatUnits/CumulativeUnits guard (`tools/pnl_guard.py`)
- [x] Dashboard data API (`tools/dashboard_data.py`)

## Phase 6 — T2 features ✅
- [x] Build T2 feature extractors (`features/t2_candidates.py`): 20 functions
- [x] Build 5-gate gauntlet runner (`tools/gauntlet.py`)
- [x] Run 10 Statcast-computable features through gauntlet
  - PROMOTED: a9_zone_pct (Brier +0.17%/+0.18% both directions)
  - REJECTED (9): a10_fps_pct, a18_spin_delta, a20_extension,
    c5_tto_decay, c7_prior_pitches, c8_days_rest, c9_season_bf,
    c16_is_debut, f7_month_factor
- [x] Run 6 extended features (lineup, travel, game context) through gauntlet
  - PROMOTED: f1_eastward_tz (+0.31%/+0.24%), b14_n_rookies (+0.21%/+0.29%)
  - REJECTED (4): b12_lineup_recent_k_pct, c13_is_doubleheader,
    f3_days_in_tz, f4_consec_road
- [x] Wire all 3 promoted features into Stage B production model
- [x] Retrain Stage B: 8-feature model (zone_pct=+0.139, eastward_tz=-0.017,
  n_rookies=-0.012)
- [x] Backtest: Brier 0.1297 vs 0.1321 naive (+2%), beats baseline at every line
- 4 features deferred (need external data): c14_blowout_risk, d6_umpire_age,
  e8_grip_penalty, h4_shape_divergence
- [x] Run negative controls (lunar phase, random, shuffled-label):
  Noise floor calibrated at +0.167% (20 random seeds, 95th pctl).
  Lunar phase correctly rejected. Random and shuffled controls pass
  the floor (0.23% min), exposing low power at 800-game splits.
  Promoted features are above floor but marginal. Backtest (+2%)
  is the real validation.
- [x] Re-gauntlet T2 promotions on the honest cross-season harness
  (A-005): **all three DEMOTED** — none cleared paired drop-delta
  t≥2 in both directions over 12,653 OOS starts. Production Stage B
  is core-only. (Shadow period moot — features removed.)

## Phase 7 — Model truth audit ✅ (2026-08-05)
- [x] Vectorized as-of feature tables (`features/asof.py`) — leakage
  structurally impossible in training/backtest feature computation
- [x] Empirical-Bayes K% shrinkage (70 BF pitcher / 60 PA batter),
  consistent across training, backtest, and live pipeline
- [x] Honest backtest: train ≤ Jul 8 → test Jul 9–Aug 3, all as-of.
  TRUE numbers: model 0.1481 vs naive 0.1505 (+2%), 618 test starts.
  Predictions saved to `data/backtest_predictions.csv`
- [x] Isotonic calibration fit (cross-fit check), persisted, wired
  into live path — corrects 2-4pp systematic low bias
- [x] Market-anchored shrinkage: MODEL_TRUST_WEIGHT=0.5 blend with
  no-vig fair; edges compress to honest single digits
- [x] Ladder honesty: ALT_SIDE_MARGIN de-vig, 10% threshold, true
  fair prob in ledger, signed odds
- [x] Slate sidecars (`data/slates/*.json`): full board persisted —
  every pitcher, distribution, ladder rung with status. 8/4
  reconstructed from odds snapshots
- [x] CLV capture: `run.py close` snapshots; grader writes closing
  odds + clv_pct per pick
- [x] Multi-season Statcast backfill (2024, 2025, Apr–May 2026) —
  1.95M pitches cached (Phase 9)
- [x] Cross-season three-way splits: +3.8% / +4.8% / +3.2% vs naive,
  12,653 OOS starts; production refit on all three seasons (Phase 9)
- [ ] Raise MODEL_TRUST_WEIGHT only after 100+ graded bets with
  positive average CLV (A-006)

## Phase 8 — Dashboard rebuild (Next.js + 21st.dev) ✅ (2026-08-05)
- [x] Next.js App Router app in `dashboard/`, static export, Dark
  Terminal tokens, Tailwind v4 + real 21st.dev components (accordion,
  market-snapshot chart, expandable-card interaction)
- [x] Data layer v2: per-date slates, availableDates, performance
  aggregates, model analytics
- [x] Slate view: date stepper, filters, expandable pick cards with
  full ladder + K-distribution histogram
- [x] Performance view: P&L chart with scrubbing, window tabs,
  splits, ledger with CLV column
- [x] Model view: calibration curve, Brier-by-line, gauntlet results
- [x] /brief filming page
- [x] Deploy to existing Vercel project
- Deferred: PWA/service worker, browser notifications, per-pick brief
  narratives, Supabase realtime

## Phase 9 — Cloud automation and honest self-measurement ✅ (2026-08-06)
- [x] Railway resident worker: ET-aware scheduler (DST-agnostic by
  construction), persistent volume for ledger/slates/odds, `/data.json`
  + `/health` HTTP endpoints
- [x] Role gate: never price a pitcher whose starter workload can't be
  established from ≥3 recent appearances (A-007)
- [x] Default audit: 7 silent fallbacks now raise instead of
  substituting a league average that manufactures edge
- [x] Lineup-uncertainty penalty + real-EV gate (A-008, A-009)
- [x] Model log: every evaluated pitcher scored against the outcome,
  not just the ~3 we bet
- [x] Live calibration on `/model`, compared on calibration error
  rather than raw Brier (A-010)
- [x] Snapshot odds provenance and staleness hardening (A-011)
- [x] `sync_repo()` pulls without a token — the repo is public, and the
  token gate meant the container silently never pulled
- [ ] Decide the odds path for the blocked container: residential
  relay, paid proxy, or paid odds API (A-012) — operator call
- [x] Reconcile the git checkout into the volume ledger on every pull
      (A-013) — the two were independent ledgers
- [ ] Set GITHUB_TOKEN on Railway so container writes reach git
      (A-013 write path) — operator credential
- [ ] Retire the local Windows scheduled tasks once the cloud path has
  run clean for a week (deliberately still enabled as backup)
- [x] Data-only commits no longer trigger a Vercel rebuild (A-023) —
  `ignoreCommand` skips when the diff touches only `data/` and
  `dashboard/public/data.json`, since the site reads live from the
  worker anyway
- [x] Close the two leaks the skip did not stop (A-033) — the
  shallow-clone reach-back never worked and failed into BUILDING ~30x a
  day, and every surviving build compiled numpy and pandas from source
  because Vercel auto-installs root `requirements.txt`. 91 CPU-hours
  Aug 7-10
- [x] Confirm A-033 cause 2 in production — build wall clock 2m -> 19s,
  no `Building numpy` in the log
- [x] Confirm A-033 cause 1 — the real fault was that Vercel's container
  has NO remote named `origin`, found only because the retry now logs
  its failures. Fixed and reproduced locally with `git remote remove
  origin` + a 25-commit gap
- [x] Stop the worker serving a blank board from a stale Statcast cache
  (A-036) — `_actual_k_lookup` falls back to `model_log.csv`, which every
  host shares. CI built 08-10 at 18/18 and the worker overwrote it with
  1/18 four minutes later
- [x] **Fix the cache lag itself, not just the display (A-037).** The
  real cause was worse than "runs at 03:00": the refresh lives inside
  `_log_evidence`, which lives inside the task, which has run on CI ever
  since dispatch started succeeding — so the worker refreshed once per
  BOOT. `_run_or_dispatch` now refreshes here on the dispatch path.
  Third bug of that shape after A-025 and A-036
- [x] Make cache freshness answerable from `/health` (`statcast_cache`:
  latest_date, per-day bytes for the last 5 days, last_refresh) — the
  A-037 diagnosis needed deploy logs and the Railway session expired
  part-way through
- [x] **Stop losing a pitcher to the book's own disambiguation tag
  (A-038).** DK writes `Ryan Johnson (LAA)` when two players share a
  name; the normalizer stripped accents and Jr./III but not the
  parenthetical, so he matched nothing and was dropped from both slates
  DK ever listed him on. The tag is now stripped for matching AND used to
  break ties, and an ambiguous name is refused rather than guessed —
  candidates were previously a dict keyed by name, so two same-named
  probables would have overwritten each other. 30/30 matched, 0 unmatched
- [ ] Confirm A-037 in production — after the redeploy, `/health`
  `statcast_cache.last_refresh.at` should advance at each scheduled
  window (15:00 / 16:45 / 18:15 ET), not only at boot, and
  `recent_bytes` for yesterday should be a real size rather than null
- [x] **Stop the board sitting on yesterday with late games stuck "IN
  GAME" (A-039).** Three independent causes: the dashboard fetched once
  on mount and never again, so an open tab never moved; a date only
  enters `available_dates` once the 09:00 ET job writes its slate, so
  midnight–09:20 defaults to yesterday with nothing said; and
  `poll_once()` was scoped to `today_et()`, so a start crossing midnight
  was abandoned mid-game and archived `in_game` forever. Now: 60s +
  on-focus refresh that survives a failed poll, an explicit "nothing
  published for <today> yet" note, a bounded carryover that finishes
  yesterday first, and a settled Statcast total that outranks a stopped
  poll. No money affected — all three frozen rows were no-bet pitchers
- [ ] Confirm A-039 in production — on the morning of 2026-08-14 the
  2026-08-13 board should show **0 in game** with every late start
  Final, and the 22:10 ET starters are the ones to check first. If any
  row is still stuck, the carryover is not running: check the worker log
  for a `carryover 2026-08-13:` line shortly after midnight ET
- [x] **Give the worker a way back from a wedged checkout (A-040).** A
  failed `git fetch` was terminal for the life of the container: it was
  recorded and never retried, so the worker served a 27-hour-old board
  until a human redeployed. Now clears abandoned lock files (age-gated
  at 600s) and retries once, recording `last_pull.recovered`
- [ ] **Surface a red CI run where the operator will see it.** This is
  the real A-040 gap: `tools/watchdog.py` diagnosed the stall correctly
  and exits 1, so every CI run for 27 hours was red and nothing said so.
  Monitoring that only a maintainer reads is not monitoring. Needs an
  operator decision on the channel (email / phone / Slack)
- [ ] **Make `/health` git fields reflect NOW, not boot.**
  `can_push_to_git` read `true` for all 27 hours because `GIT_STATUS` is
  set once in `configure_git()`; `git.checked` still said 2026-08-13.
  Either re-probe on request or publish the age next to the value
- [x] **A-041 — refit the calibrator: BUILT, MEASURED, NOT PROMOTED.**
  `tools/recalibrate_live.py`. The calibrator was already fit on 2026
  (18,798 rows, Apr 11–Aug 4) and is well calibrated there in every
  band including 0.55–0.75. The refit improves held-out Brier
  0.2516 -> 0.2483 but is not significant (z=-0.89, n=137), and it does
  not address the real defect. `models/calibrator.pkl` untouched
- [x] **A-041 — score against the MARKET: DONE, and the model loses.**
  `tools/score_vs_market.py`. Could NOT be run on the backtest — it ends
  2026-08-04 and closing captures begin 08-05 (A-002). On the 262 starts
  that do have two-sided closing prices: market Brier 0.2526 vs model
  raw 0.2646 / calibrated 0.2664 / blended 0.2575; paired, the raw model
  is worse (z=+2.10) and the calibrated model worse still (z=+2.38). The
  alt ladder (1,956 rows, clustered by start) agrees: z=+2.36 / +2.55.
  The 50/50 blend is the only configuration that escapes significance,
  and only because it is half market
- [ ] **BLOCKS BETTING: no market-scored sample shows the model
  winning.** Do not place bets, raise `MAX_STAKE_UNITS`, relax the edge
  threshold, or promote a recalibration until one does. The edge gate
  has been enforcing this on its own since 2026-08-14
- [ ] **Keep collecting closing lines — this is now the critical path
  (A-002).** 12 days banked (334 two-sided snapshots, 9,598 alt rows).
  At ~25 starts/day, 1,000 market-scored starts is ~40 days out. Nothing
  shortens it except paying for historical lines. Re-run
  `tools/score_vs_market.py` weekly; the verdict is only provisional at
  262 starts
- [ ] **A-041 — the defect is adverse selection, not calibration.** The
  model is calibrated to 1.4 points where it AGREES with the book
  (n=79) and off by -33 points where it most disagrees (n=26). No
  univariate p -> p map can express that, so no recalibration fixes it.
  If a lever is needed before the market backtest lands, it is
  `MODEL_TRUST_WEIGHT` (currently 0.5; every measured increment above
  0.0 costs accuracy) — but that is a decision to stop trusting the
  model, not a tuning exercise
- [x] **A-042 — early-hook tail in the workload model: BUILT, GATED,
  FLAG OFF.** Batters faced is left-skewed (-1.58); the NB is
  right-skewed (+0.24), pricing BF<=8 at 0.11% against an actual 3.08%.
  `alpha` was pinned at exactly `exp(-5)`, its fit bound, so no
  re-fitting of the NB could help. Replaced with a two-component hook
  mixture that preserves the conditional mean. Gate 2 passes in both
  temporal directions and forward; Gate 3 passes on three disjoint fits
  agreeing (pi 0.0195-0.0233, mu_short 5.96-6.02).
  `tools/gate_hook_mixture.py`
- [ ] **A-042 — shadow the hook mixture for 2 weeks, then decide.** Gate
  5 is only PARTIAL: left-tail calibration improves on every split, but
  the gate asks for P(K >= line) and that needs the full Stage A -> B ->
  compound path. Watch specifically: (a) does the OVER lean actually
  drop by the predicted 1-2 points — more than that is suspicious, not
  lucky; (b) the confident-OVER bin from A-041 (stated 65.4%, actual
  33.3%) should move toward its diagonal; (c) pitch-limited starts,
  where the normal arm's mean is floored
- [x] **A-043 — bound-pinning sweep: DONE, 3 findings.** Stage A alpha
  at its optimizer bound (A-042), outs-hazard lambda at the top of
  `LAMBDA_GRID`, and the calibrator's top knot at exactly 1.0. Stage B
  clean (fit unbounded). `tools/audit_param_bounds.py` + 8 tests
- [x] **A-043 — stop the calibrator serving certainty.** 53 ladder rungs
  were served at `model_prob == 1.0000` while the raw model never
  exceeded 0.9959; 5 of the 46 with outcomes LOST. `PROB_EPS = 1e-3`
  clamps `predict()` on the way out; blast radius 61/1001 values, max
  change 0.00100
- [ ] **A-043 — refit the outs hazard with an extended lambda grid.**
  `lambda = 30.0` is `max(LAMBDA_GRID)`, chosen by `min(grid,
  key=brier)`, so the curve may still have been improving — the model
  may be under-regularised. Extend past 30 (100, 300, 1000) and confirm
  the minimum is INTERIOR. Also persist the per-lambda scores into the
  pkl: they are computed and printed today and then thrown away, which
  is why this needs a refit to answer at all
- [ ] **A-043 — smooth the calibrator's top bin rather than relying on
  the clamp.** The guard stops the impossible assertion; it does not
  make the top bin calibrated. Fold into the A-041 recalibration work
  once the market-scored sample is large enough
- [ ] **A-043 — wire the audit into CI.** It exits 1 today and nothing
  runs it. Same gap as A-040: a check nobody receives is not a check
- [ ] **A-041 — one bet per pitcher per slate.** Drew Anderson took 3 of
  8 losses in one game (.69/.94/.81). The haircut keys on repeated
  `game_pk`, not repeated pitcher — same fix already scoped under
  Phase 10
- [ ] **A-039 follow-up: alert when a settled total meets a non-final
  poll.** `stale_poll` is now set on exactly that disagreement, so the
  condition is detectable — but nothing watches it, and this class of
  fault renders as a plausible live game rather than as an error.
  `tools/watchdog.py` is the natural home
- [x] **Widen the pitcher history window to prior seasons — BUILT, flag
  OFF, all five gates passed.** `docs/PRIOR_SEASON_SCOPE.md`,
  `docs/GATES.md`. Recovers 11.5% of starts (409, ~2.9/day) that the
  50-BF gate refuses over history already on disk. Rate blends across
  seasons (W=0.5); workload does not — 3+ starter games uses the current
  season alone, 1–2 blends 50/50 with the pitcher's prior p25, 0 uses
  p25. Gate 2 +0.44% and Gate 5 +4.83% Brier on the untouched holdout
- [ ] **Shadow the prior-season window for two weeks, then decide.** The
  gates are green and that is not sufficient here: with the flag on for
  2026-08-11, Snell showed an 11.3% UNDER edge on his second start back
  from a 94-day layoff and was blocked by four tenths of a point — by the
  A-008 unposted-lineup penalty, which has nothing to do with this
  feature. Once a lineup posts, the same edge books as a LEAN. Watch
  specifically: (a) the 0.8–1.0 prediction band, 9.0 points high on the
  holdout where every other band is within 3; (b) season debuts, a third
  of recovered starts, which have no production baseline at all and are
  measurably harder (Brier 0.190–0.198 vs 0.179–0.182)
- [ ] **Rebuild the prior-season sidecar each offseason.** `python
  tools/build_prior_season.py <year>` once the season closes. Nothing
  schedules this yet, and a missing sidecar degrades silently to
  current-season-only — the pipeline prints a warning and prices on, so
  the board would just quietly get shorter again
- [ ] **A-038 follow-up: alert on unmatched props rather than printing
  them.** The tag bug survived two slates because the only signal was one
  stdout line on a scheduled run. A DK prop that matches no probable is a
  measurable daily number and belongs on `/health` next to
  `statcast_cache`, with the same treatment for a probable that no prop
  covers. Related: the name join is a third-party display string in both
  directions and has no test against real captured DK names
- [ ] **A-016 follow-up:** `backfill_statcast` skips any day >2 days old
  and >20 KB, so a file written mid-games is large-but-incomplete and
  freezes that way. Check completeness against the MLB schedule
  (expected games vs distinct `game_pk` in the file) rather than size
  alone. Overlaps the off-day re-fetch item
- [ ] Reconsider whether the worker should commit `dashboard/public/data.json`
  at all — it overwrote CI's better copy every 5 minutes and the site
  prefers the worker's live payload anyway, so the committed artifact is
  a fallback that the worker can only make worse
- [x] Keep yesterday's results on the board overnight (A-035) — the live
  watcher archives per date and the dashboard looks up by slate date, so
  the midnight-to-09:00 blank window is closed. 2026-08-10 served 1/18
  actual K totals when found
- [ ] Confirm A-035 on the morning of 2026-08-12 — the 08-11 board should
  read complete before 09:00 ET, not 1-of-N
- [ ] Consider surfacing `result_source` on the board so an overnight
  live figure is visibly distinct from a Statcast-confirmed one
- [x] Stop the worker wedging itself on a halted rebase (A-034) —
  `sync_repo` resets to `origin/master` instead of rebasing onto it,
  because the files in conflict are regenerated from the volume in the
  same pass. Four hours of grades had been committed to a detached HEAD
  and never pushed
- [ ] Confirm A-034 in production — after the redeploy, `/health` should
  show `last_pull.ok: true` with `head.detached: false`, and commits
  should resume at 5-minute intervals
- [ ] Re-read the Vercel usage chart after a full slate day and confirm
  the number actually landed near ~1-2 CPU-hours/day
- [ ] Apply the same build-skip to the NRFI project (99 CPU-hours,
  51.6% of the allowance, same `auto:` commit pattern) — separate
  repo, operator call. Check it for the `requirements.txt` install too;
  it is the same repo shape and likely carries the same A-033 cause 2

## Phase 10 — Total outs model (research complete, capture started)

A second market: starting-pitcher **total outs recorded** (DK subcat
17413, "Outs Recorded O/U"), on its own dashboard page. Sibling to the
strikeouts model, not a variant of it.

- [x] Confirm the market exists and is reachable through the existing
  scraper — 1:1 pitcher coverage with the strikeout board (2026-08-08)
- [x] Capture outs O/U prices to `dk_outs_*` / `closing_outs_*`
  (writer only; nothing prices a bet)
- [ ] **Every day from here: run `python run.py close` (or
  `tools/odds_relay.py watch`).** Closing prices are the one input that
  cannot be backfilled. This is the whole reason Phase 10 starts with a
  writer instead of a model.
- [ ] Answer the threshold question before building (operator call): at
  the measured 6.97% hold and `MODEL_TRUST_WEIGHT=0.5`, a bet needs
  ~17.9pp of model-vs-market disagreement ≈ **3.3 outs ≈ 1.1 innings**.
  Bets will be rare. Decide whether that is acceptable, or whether the
  threshold structure needs rethinking for this market.
- [ ] Source DK house rules for Outs Recorded — whole-number push,
  suspended-game settlement, and the meaning of the `EarlyExit` tag on
  every outs market. Currently assumed from the K market, which is the
  same class of error as fabricating odds.
- [ ] Inning-hazard distribution replacing the negative binomial.
  65.5% of starts end on an exact multiple of 3; P(18 outs)=0.2201 vs
  0.0730 under a moment-matched NB. AUDIT A-024's "family doesn't
  matter" argument holds for K (shape cancels in the compound integral)
  and does **not** survive the port — the outs line sits directly on the
  lattice.
- [ ] Per-PA on-base stage. Given perfect BF, outs still has residual
  sd 2.30; given perfect BF *and* reached-base count it collapses to
  0.76. That gap is the model.
- [ ] `market` column in the ledger + identity key
  `(game_pk, pitcher_id, market, line)` — the current non-collision
  (K 3.5–8.5 vs outs 13.5–19.5) is an accident, not a guarantee
- [ ] Market filter on `/model`, `/performance` and the headline P&L
  **before** any outs pick enters the ledger — those aggregate the whole
  ledger with no market filter today, so the first outs row silently
  blends two markets into every published number
- [ ] Re-key the correlation haircut. It fires on repeated `game_pk`
  (cross-pitcher outs corr **+0.02** — nothing) and misses same-pitcher
  K-vs-outs (**+0.50**, joint lift 1.21–1.61). Simplest safe v1: one bet
  per pitcher per slate, larger edge wins.
- [ ] `dashboard/app/outs/page.tsx` + its own payload artifact, added to
  `DATA_ONLY_PATHS` in `scripts/vercel-ignore-build.sh` or every outs
  data commit resumes burning full Next builds (the A-023 regression)

### Phase 10a — Inning-hazard model (research artifact, 2026-08-08)
- [x] Per-start outs table, 13,170 regular-season starts 2024–2026
  (`tools/build_outs_dataset.py` → `data/outs_starts.parquet`),
  validated 548/548 against MLB boxscore `inningsPitched`
- [x] Leakage-safe as-of features (`features/outs_asof.py`) + a
  brute-force strictly-prior recomputation test
- [x] Inning-lattice hazard model (`models/outs_hazard.py`): per-inning
  completion, return-for-next-inning, and a partial-inning {0,1,2}
  multinomial, composed into a PMF over 0..27
- [x] **Gate 2 PASSES** — three-way out-of-sample, all 21 split-by-line
  cells positive: **+5.64% / +4.99% / +7.49%** Brier skill vs the honest
  as-of baseline (after dropping `career_x_season`)
- [x] Lattice reproduced — chi² 13.23, df 9, **p = 0.152** (not
  rejected) over k=12..21; the negative-binomial failure mode
  (P(18)=0.073 vs 0.220 empirical) does not occur
- [x] Leakage cleared four ways: shuffle control collapses to
  −0.42%/−0.22%/−0.08% against a constant train-marginal; noise control
  null; brute-force as-of recomputation bit-identical; self-inclusion
  probe shows zero own-row feature movement
- [x] Gate 4 — `career_x_season` dropped (r=+0.9955, VIF 350 on the S1
  design; `season_start_number` saturates its cap on 74.5% of 2024 rows,
  degenerating the interaction to 8×`career_start_number`)
- [ ] **Gate 5 — NOT passed.** ECE 0.017–0.026 with single-bin gaps to
  5.1pp, against a measured break-even requirement of ~3.6pp per side.
  An edge filter would fire on calibration error as often as on edge.
  Route through `models/calibration.py` and refit its own calibrator.
- [ ] Gates 1/3/5 per-feature against the outs target — the effect sizes
  in the spec are reproductions from the strikeouts gauntlet, not gate
  passes on this target
- [ ] `stop_rate_12` (sign-unstable across splits) and `stop_rate_21`
  (design measures a null) are Gate-2 rejection candidates, currently
  advisory-only
- [ ] Score against the MARKET. Nothing here measures that — all skill
  is vs a naive baseline, and exactly one ungraded outs slate exists.
  **Do not let this touch `models/edge.py` until enough graded slates
  are banked to fit and validate a calibrator.**
