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
- [x] Set GITHUB_TOKEN on Railway so container writes reach git
      (A-013 write path) — confirmed operational 2026-08-24: the worker
      pushes live-grade commits at 5-minute cadence and /health reports
      last_pull.ok=true, can_push_to_git=true
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
- [x] Confirm A-037 in production — confirmed 2026-08-24: `/health`
  showed `last_refresh.at = 2026-08-24T12:33 ET, ok, window
  2026-08-20..08-24` (a scheduled window, not a boot) and
  `recent_bytes["2026-08-23"] = 799,305` — a real size
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
- [x] Confirm A-039 in production — superseded by ten clean days of
  operation and, as of 2026-08-24, a standing watchdog check
  (`no stale polls`): zero settled-total/non-final-poll conflicts
  since the 08-13 fix (the three pre-fix archive rows are documented
  and excluded)
- [x] **Give the worker a way back from a wedged checkout (A-040).** A
  failed `git fetch` was terminal for the life of the container: it was
  recorded and never retried, so the worker served a 27-hour-old board
  until a human redeployed. Now clears abandoned lock files (age-gated
  at 600s) and retries once, recording `last_pull.recovered`
- [x] **Reap orphans; restart before the fork ceiling (A-045).** python
  as PID 1 reaped nothing, so orphaned grandchildren accumulated as
  zombies until fork() failed EAGAIN (~44 h) and the board froze for
  two days behind a green `/health`. Now: `tini` is PID 1, the worker
  gauges `/proc` every publish pass (`pressure:` log line +
  `process_pressure` on `/health`) and exits for a clean Railway
  restart past 400 pids / 200 threads, and `_run` treats fork-EAGAIN
  as fatal rather than a per-command failure
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
  262 starts.
  **2026-08-24: the weekly re-run is now a scheduled task** —
  `scorecard` (Sundays 04:30 ET, worker + CI) appends to
  `data/market_scorecard.csv` and prints the shadow clocks. At 447
  starts the verdict HARDENED: raw z=+2.89 worse than the close, and
  the 50/50 blend crossed into significance (z=+2.01). 553 starts to
  the factor-screen threshold
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
  where the normal arm's mean is floored.
  **2026-08-24 (A-046): the shadow now actually records** — the
  pipeline logs `p_over_hookmix` nightly into `model_log.csv`; read it
  with `python tools/flag_shadow_report.py`. Clock starts with the
  first graded slate after 08-24; decide at 14 dates
- [x] **A-043 — bound-pinning sweep: DONE, 3 findings.** Stage A alpha
  at its optimizer bound (A-042), outs-hazard lambda at the top of
  `LAMBDA_GRID`, and the calibrator's top knot at exactly 1.0. Stage B
  clean (fit unbounded). `tools/audit_param_bounds.py` + 8 tests
- [x] **A-043 — stop the calibrator serving certainty.** 53 ladder rungs
  were served at `model_prob == 1.0000` while the raw model never
  exceeded 0.9959; 5 of the 46 with outcomes LOST. `PROB_EPS = 1e-3`
  clamps `predict()` on the way out; blast radius 61/1001 values, max
  change 0.00100
- [x] **A-043 — outs hazard lambda: measured and closed (2026-08-24).**
  Extended the grid to 1000; the raw argmin moved to the NEW edge —
  but the paired per-start z's show the top of the curve is
  statistically TIED (300 vs 1000: z=+1.75) while the small-lambda end
  is genuinely worse (z~+3). Selection now prefers a tied INTERIOR
  point over a boundary argmin, the shipped pkl carries lambda=300
  with the full selection curve persisted (`meta.lambda_grid`,
  per-entry `z_vs_best`), and `audit_param_bounds` reads that evidence
  instead of alarming on grid position alone. OOS skill unchanged
  (+7.52% on the decision split vs +7.49 at lambda=30)
- [ ] **A-043 — smooth the calibrator's top bin rather than relying on
  the clamp.** The guard stops the impossible assertion; it does not
  make the top bin calibrated. Fold into the A-041 recalibration work
  once the market-scored sample is large enough
- [x] **A-043 — audit wired into CI (2026-08-24).** The watchdog's
  `parameter bounds` check runs `tools/audit_param_bounds.py` on every
  night job and CI run: NEW pinned parameters fail red; the two
  documented A-042 alphas report as a tracked WARN until the
  hook-mixture shadow resolves them (a permanent red trains the
  operator to ignore red)
- [x] **A-041 — same-pitcher exposure now haircut (A-047,
  2026-08-24).** Drew Anderson took 3 of 8 losses in one game
  (.69/.94/.81) and the haircut keyed on repeated `game_pk` only.
  `portfolio_daily_cap` now trims on repeated pitcher OR repeated game
  — strictly more conservative. The stricter "one bet per pitcher per
  slate, larger edge wins" rule remains scoped to Phase 10 for the
  K-vs-outs cross-market case
- [x] **A-039 follow-up: stale-poll alert (2026-08-24).** The watchdog's
  `no stale polls` check scans the served payload for
  settled-total/non-final-poll conflicts since the fix date; the three
  documented pre-fix archive rows are excluded so the check stays
  meaningful
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
  measurably harder (Brier 0.190–0.198 vs 0.179–0.182).
  **2026-08-24 (A-046): the shadow now actually records** — priced
  board rows log `p_over_prior`; refused-but-recoverable pitchers are
  priced into `shadow_prior_pitchers` sidecar sections and scored to
  `data/shadow_prior_log.csv`. Read with
  `python tools/flag_shadow_report.py`; decide at 14 dates
- [ ] **Rebuild the prior-season sidecar each offseason.** `python
  tools/build_prior_season.py <year>` once the season closes. Nothing
  schedules this yet, and a missing sidecar degrades silently to
  current-season-only — the pipeline prints a warning and prices on, so
  the board would just quietly get shorter again
- [x] **A-038 follow-up: unmatched props now alert (2026-08-24).** The
  sidecar carries a `skipped` ledger (every unpriced prop with its
  reason, including "no MLB probable matched"), and the watchdog's
  `props all accounted` check reconciles the intraday odds archive
  against pitchers + shadow + skipped daily — a silently dropped name
  is a red check by the next morning. Still open from the original
  note: a regression test of the name join against real captured DK
  names
- [x] **A-016 follow-up: completeness by game count (2026-08-24).**
  `backfill_statcast` now verifies recently settled days hold every
  scheduled final game (distinct `game_pk` vs the MLB schedule) and
  re-fetches shortfalls; schedule-unavailable falls back to the size
  rule rather than blocking. The watchdog's `statcast days complete`
  check independently verifies the last few settled days daily
- [ ] Reconsider whether the worker should commit `dashboard/public/data.json`
  at all — it overwrote CI's better copy every 5 minutes and the site
  prefers the worker's live payload anyway, so the committed artifact is
  a fallback that the worker can only make worse
- [x] Keep yesterday's results on the board overnight (A-035) — the live
  watcher archives per date and the dashboard looks up by slate date, so
  the midnight-to-09:00 blank window is closed. 2026-08-10 served 1/18
  actual K totals when found
- [x] Confirm A-035 — superseded by two weeks of operation; the
  2026-08-24 watchdog reads yesterday's board complete (4,598 pitches
  cached for all 23 pitchers) and the mechanism has run clean since
  the fix
- [ ] Consider surfacing `result_source` on the board so an overnight
  live figure is visibly distinct from a Statcast-confirmed one
- [x] Stop the worker wedging itself on a halted rebase (A-034) —
  `sync_repo` resets to `origin/master` instead of rebasing onto it,
  because the files in conflict are regenerated from the volume in the
  same pass. Four hours of grades had been committed to a detached HEAD
  and never pushed
- [x] Confirm A-034 in production — confirmed 2026-08-24: `/health`
  `last_pull.ok: true` and worker commits landing at 5-minute
  intervals all day (the rebase-to-reset path has run clean since
  08-13)
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
- [x] `market` column in the ledger + identity key
  `(game_pk, pitcher_id, market, line)` (2026-08-24) — blank legacy
  rows read as "K" via `tracker.market_of`; the strikeouts pipeline
  writes and matches only market="K"
- [x] Market filter on `/model`, `/performance` and the headline P&L
  **before** any outs pick enters the ledger (2026-08-24) — the filter
  lives at `dashboard_data._load_picks`, the single choke point every
  strikeouts aggregate reads through, and `pl_calc` reports per market
  with no combined figure. Verified byte-identical output on the live
  ledger
- [ ] Re-key the correlation haircut. It fires on repeated `game_pk`
  (cross-pitcher outs corr **+0.02** — nothing) and misses same-pitcher
  K-vs-outs (**+0.50**, joint lift 1.21–1.61). Simplest safe v1: one bet
  per pitcher per slate, larger edge wins.
- [x] `dashboard/app/outs/page.tsx` + nav link shipped (2026-08-24) — a
  static separation page until the model earns a board — and the
  future payload path `dashboard/public/outs.json` pre-registered in
  `DATA_ONLY_PATHS` so the first outs data commit can't regress A-023
- [x] **Same-night outs grading (2026-08-25).** `tools/outs_boxscore.py`
  grades final games from the MLB boxscore (validated 548/548 vs
  Statcast) inside pipeline step 3 on every host; Statcast's morning
  pass re-derives and confirms. Board and paper tracks now fill by
  the 03:00 job instead of 09:00
- [x] **Outs paper tracks (2026-08-25).** Three staking policies
  (gates-as-written / gold-board capped / gold-board uncapped) graded
  on every settled slate through the real edge+staking code into
  append-only `data/outs_paper_tracks.csv`; frozen per (date, policy);
  cumulative flat-basis totals on /outs. The decision data for whether
  the entry gates are too tight — revisit once the tracks hold ~4
  weeks of dates
- [x] **Watchdog watches the outs page (2026-08-25).** Two checks
  against the SERVED /outs.json: today's board current (slate stamp,
  publish-window grace) and yesterday's results present (13:00 ET
  clock). Replayed against the morning's stale payload: both FAIL
- [x] **Outs board grades its own leans (2026-08-25).** "Model lean"
  column on `/outs`: settled rows badge ✓ right / ✗ wrong on whether
  the gap's side matched the settlement, PUSH neutral on exact lands.
  Explicitly not WIN/LOSS — no bet exists behind any row
- [x] **Board reads from the model's side (2026-08-26, operator
  direction).** Side chip + side-oriented Model/Market/Edge columns,
  plus a Units column carrying the capped paper rule's stake per row
  (`outs_paper.board_paper_columns`, the paper ledger's own code
  path; "gates" tag when the production bar also clears). Page breaks
  out of the max-w-5xl shell on xl+. Retires the payload's
  no-pick-for-today construction — stakes stay paper until the
  calibration gate opens
- [x] **CI staged into the outs payload's mirror path (2026-08-25,
  second A-052 amendment).** The daily jobs race between the worker
  and CI; CI won the 08-25 morning run and discarded the rebuilt
  `outs.json` because the workflow's commit step never staged it — the
  live page served the prior day's payload with 0/20 results. One-line
  fix in `.github/workflows/daily.yml`; payload rebuilt same day

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
- [ ] **Gate 5 — NOT passed, now measured to a refusal (2026-08-24,
  A-052).** Fit Platt, isotonic, AND per-line Platt on 67,970 pooled
  cross-season OOS pairs; on the untouched 2026 holdout every map made
  per-line calibration WORSE (raw mean line ECE 0.0205 / worst gap
  7.4pp; per-line Platt 0.0247). The ship-gate in
  `tools/fit_outs_calibrator.py` refused all three — the 2026
  miscalibration is not a stable p→p bias. Serving is raw + clamp;
  the 7.4pp worst-line gap vs the ~3.6pp break-even bar is exactly
  why no bet can price. Re-run the fitter as seasons accrue; the gate
  decides.
- [ ] Gates 1/3/5 per-feature against the outs target — the effect sizes
  in the spec are reproductions from the strikeouts gauntlet, not gate
  passes on this target
- [ ] `stop_rate_12` (sign-unstable across splits) and `stop_rate_21`
  (design measures a null) are Gate-2 rejection candidates, currently
  advisory-only
- [x] Score against the MARKET (2026-08-24, A-052) — the banked
  closing captures made a day-one verdict possible:
  `tools/score_outs_vs_market.py`, 373 out-of-sample starts over 16
  dates, raw vs closing no-vig fair **z = +0.65 — indistinguishable
  from the book** (the K model was z=+2.10 WORSE at comparable n).
  Series in `data/outs_scorecard.csv`, re-scored weekly by the
  scorecard task. **The edge.py rule STANDS**: indistinguishable is
  not edge — nothing prices until z goes negative on a serious sample
  AND a calibrator passes its gate.

## Phase 11 — Score against the market, not the naive baseline (2026-08-19)

The backtest's "+3–5% Brier over naive" is real and replicated in both
temporal directions over 12,653 starts. It was never a claim about
beating a book, and A-041 showed it does not: at the market's own line
the model is significantly worse than the closing price. Everything in
this phase is aimed at that gap, not at the naive comparison.

- [x] Score the served probability against the closing line
  (`tools/score_vs_market.py`, A-041) — now reports `raw`, `cal`,
  `served`, `blend`, `fair` so the stage that loses the edge is visible
- [x] **Switch off the isotonic map (A-044).** Measured WORSE than raw
  against the closing line (0.2663 vs 0.2642, n=329, same sign in both
  halves). Blended output moves from significantly worse than the
  market (z=+2.14) to indistinguishable (z=+1.72). Halves the OVER
  bias, +0.0447 → +0.0267
- [x] Sweep model trust, Kelly fraction and leverage against real
  closing prices (`tools/kelly_sweep.py`). Findings: every trust level
  above 0 loses; leverage scales the loss roughly linearly (1x −9.88u,
  2x −19.75u, 3x −29.63u) while max drawdown goes 10.3% → 28.4%; and
  the Kelly fraction is **decorative** — `MAX_STAKE_UNITS` binds before
  any fraction from 0.25 to 1.0 changes a stake
- [x] **Archive the intraday odds series; implement H1/H2 (A-049,
  2026-08-24).** Every pipeline capture now lands in
  `data/odds/intraday_*.csv` (the open was previously overwritten by
  each reprice); every sidecar row and model_log row carries
  h1_open_line / h2_line_move / h2_fair_move. Capture-first: the
  fields price nothing until the market-scored screen can judge them
- [ ] **A-002 is the binding constraint, not the factor count.** Every
  factor ever screened was tested on "does it predict strikeouts better
  than naive", never "does it find prices the market got wrong". Those
  are different questions and the market already prices season K%,
  opponent K% and TTO. Historical prop lines are still unsourced, so
  no factor has ever been screened for EDGE
- [x] Build the market-based factor screen (2026-08-24) —
  `tools/market_factor_screen.py`, runnable at any n with a loud
  PROVISIONAL banner below 1,000 starts; the weekly scorecard
  announces when the threshold is crossed. RUN it for decisions at
  ~1,000 (~late Sept 2026)
- [ ] Attack the early-hook tail (A-042) — the named mechanism behind
  the OVER bias, and the failure Stage A cannot currently produce.
  Candidates already in the repo: bullpen rest, blowout risk, innings
  caps (`data/manual_pitch_limits.csv`).
  **2026-08-24 (A-050): the inputs now flow** — game lines (blowout
  risk) captured morning + close; beat-note pitch-limit suggestions
  written daily to `data/pitch_limit_suggestions.csv` for operator
  confirmation into the manual CSV; weather per venue in the sidecar
  `wx` field; home-plate umpires archived nightly + 2026 backfill.
  All capture-only until gated
- [ ] Do NOT raise `MAX_STAKE_UNITS`, relax the edge threshold, promote
  a recalibration, or add leverage until a market-scored sample says
  the model wins
