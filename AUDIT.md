# Audit Log

Tracks open items, resolved items, and known risks.

## Open

### A-001: Licensing decision pending
- **Filed:** 2026-08-04
- **Status:** Awaiting operator decision
- **Description:** MLB Stats API, Open-Meteo free tier, and RotoWire
  all carry non-commercial terms. The operator sells picks. Decision
  needed before Phase 1. See `docs/LICENSING.md`.

### A-002: Historical strikeout prop lines not yet sourced
- **Filed:** 2026-08-04
- **Status:** Open
- **Description:** Phase 3 requires backtesting against real DraftKings
  strikeout prop lines. No historical source identified yet. DK's JSON
  serves current lines only; The Odds API historical endpoint is paid.
  Forward-collecting live lines starting now is the fallback.

### A-003: Park factor CSV returns HTML
- **Filed:** 2026-08-04
- **Status:** Open
- **Description:** Baseball Savant's park-factors leaderboard returns
  HTML (JS-rendered table) when `&csv=true` is appended. Need to parse
  embedded JSON or use the venue-specific endpoint.



### A-006: MODEL_TRUST_WEIGHT held at 0.5 pending live CLV evidence
- **Filed:** 2026-08-05 (amended same day)
- **Status:** Open (monitored via CLV)
- **Description:** Backtest evidence is no longer thin — the
  cross-season harness shows +3.2% to +4.8% over naive across 12,653
  out-of-sample starts (see CHANGELOG Phase 9). But backtests can't
  price in what the market knows that our features don't (scratches,
  leashes, weather), so the 50% market blend and 2u cap stay until
  100+ graded live bets with positive average CLV.

### A-007: Bad inputs manufacture edge and get selected INTO the bet list
- **Filed:** 2026-08-06
- **Status:** Guardrail shipped; structural risk remains open
- **Description:** `_compute_pitcher_stats` fell back to
  `bf_mean = 21.1` (league-average STARTER) whenever a pitcher lacked
  3 starter-length games. On 2026-08-05 this priced Drew Anderson — a
  reliever with 40 appearances averaging 7 BF — as a 21.1-BF starter,
  inflating E[K] from ~3.1 to 5.45 and manufacturing a 17pp edge. He
  became the #1 pick and drew the day's largest stake (3.5u). Final
  line: 0 K in 3.2 IP, 13 BF. All three bets lost.
  **The general lesson is bigger than the bug:** an input error that
  inflates a projection is not a random error — the edge filter hunts
  for high projections, so such errors are systematically selected
  into the portfolio and concentrated at max stake. The fallback fired
  on 1 of 28 pitchers but on 1 of 3 bets.
- **Shipped:** role gate in the live pipeline — no league defaults
  ever (workload comes from real history or the pitcher is skipped),
  plus `is_startable` requiring >= 3 appearances and a recent typical
  outing >= 15 BF. Verified on the 8/5 board: skips Anderson only,
  keeps all 27 genuine starters (typical 22-23 BF), and catches him
  with the thin June-onward cache the morning run actually used.
- **Full sweep done 2026-08-06.** Every default in the live pricing
  path was reviewed under one test: *if this fires, does it invent an
  input?* Seven were landmines and now raise instead of substituting:

  | Location | Was | Now |
  |---|---|---|
  | `stage_a_bf.predict_bf_distribution` | `c1_bf_mean` → 21.1 BF | raises |
  | `stage_a_bf.predict_bf_distribution` | `k_pct` → 0.225 | raises |
  | `strikeout_predictor.predict` | `lineup_k_pcts` → `[0.225]*9` | raises |
  | `strikeout_predictor.predict` | `pitcher_k` → 0.225 | raises |
  | `stage_b_rate.predict_single` | unfitted → matchup formula | raises |
  | `edge.american_to_implied/decimal` | odds 0 → 0.5 / 2.0 | raises |
  | `tracker._calc_pnl` | bad odds → −110 | raises |

  The last one mattered doubly: `pl_calc` validates the ledger through
  the same function, so a fabricated −110 would have been confirmed by
  our own drift check.

  Benign (verified): batter K% for unknown batters resolves to the
  league rate through `shrink_rate`, which is the intended
  empirical-Bayes behaviour, not a fabrication. `zone_pct`,
  `eastward_tz`, `n_rookies` defaults are dead code — those features
  were demoted in Phase 10 and are not in the production design matrix.

### A-008: Symmetric input error also manufactures edge (lineup timing)
- **Filed:** 2026-08-06
- **Status:** Open — quantified, needs an operator decision
- **Description:** A-007 concerned a *biased* default. But ANY input
  error creates phantom edge, because the edge filter selects on the
  error: we systematically bet the cases where noise happened to
  flatter us. Measured on the 8/4 board (25 starters), replacing the
  real lineup with league-average rates — which is what the 10:30am
  run actually uses, since lineups aren't posted yet — moves
  P(over 5.5) by **5.1pp on average, up to 10.9pp**, with 12 of 25
  starters moving more than 5pp. Our edge bar is ~7-8pp, so lineup
  uncertainty alone is the same order as the edge being bet.
- **Options:** (a) place bets only at the 4:45pm lineup-lock run,
  (b) require a materially higher edge when `lineup_source` is
  projected, (c) accept and monitor via CLV.

### A-009: Alt-board margin assumption is far off measured value
- **Filed:** 2026-08-06
- **Status:** Open — needs a decision; do NOT just retune the constant
- **Description:** `ALT_SIDE_MARGIN = 0.04`. Measured across 190 alt
  rungs from two slates (restricted to pitchers who actually threw):
  mean implied 47.1% vs mean realized 37.9% — roughly a **24%**
  overround, consistently positive in all four probability bands.
  **Counterintuitively, raising the constant makes the system MORE
  aggressive**, because `edge = blended − fair` and `blended` is half
  market: lowering `fair` lowers `blended` only half as fast, so edge
  grows. Retuning the parameter is therefore the wrong response.
- **Recommendation:** add a real-EV gate on the actual vigged price
  (`blended × decimal_odds − 1 ≥ margin`), which is un-gameable by
  de-vig assumptions, and re-measure the overround as slates
  accumulate (n=190 over 2 days is thin).

### A-010: Live-vs-backtest Brier comparison is not scale-free
- **Filed/Resolved:** 2026-08-06
- **Status:** Resolved in the same commit that filed it — recording the
  finding because the failure mode generalises.
- **Description:** the first live-calibration build compared raw live
  Brier to the flat backtest Brier. Brier is not scale-free: it depends
  on how separable the sample is. The backtest averages a fixed
  six-line grid including near-certainties (8.5 → 0.0654); the live log
  scores one row per pitcher at the book's actual line, and the book
  hangs its line where the game is closest to a coin flip. On the 8/4
  sample the irreducible floor was 0.2385 against a 0.1491 baseline —
  the yardstick sat below what a flawless model could score. 4,000
  Monte-Carlo trials of a perfectly calibrated model returned "worse
  than backtest" **100%** of the time at n=20/25/40.
- **Resolution:** compare `excess = Brier − floor` where
  `floor = mean p(1−p)`, computed against a backtest reference weighted
  onto the live sample's own line mix. Post-fix Monte-Carlo: 2.0% false
  "worse". Verdict band is 2 SE of the live Brier, so it tightens as
  the log grows rather than needing a retune.
- **Generalises to:** any future "live vs backtest" metric. Ask whether
  the two samples are comparable before differencing them. Detection
  power at n≈26 is low regardless; the leash error and the calibration
  curve move first.

### A-011: Snapshot odds could be laundered into the ledger as live
- **Filed/Resolved:** 2026-08-06
- **Status:** Resolved. Fallback remains off by default and is not
  enabled on Railway.
- **Description:** the DK snapshot fallback (added so the blocked
  container could price a slate) had three paths by which a stale board
  could be priced as a current one. (1) `tools/closing_odds.py` re-dates
  rows to today, re-stamps `captured_at` to now, and drops
  `odds_source` — so a snapshot became a fresh-looking closing price
  that the CLV grader wrote to the ledger and that defeated
  `daily_pipeline`'s `date ==` filter, the only guard against pricing
  an old board. (2) `dk_k_*.csv` had no `captured_at` column, so the
  loader dated it from file mtime — which git checkout and Docker
  `COPY` both reset, meaning the age ceiling could never fire on the
  container. Verified against a real git clone. (3) Staleness used the
  file's newest stamp, so one refreshed pitcher re-validated every
  stale row beside it.
- **Resolution:** `closing_odds.py` pins `allow_snapshot=False`;
  `captured_at` is a real column and an unstamped board is refused
  rather than mtime-dated; staleness is per-row; candidate ordering
  prefers freshness over filename prefix; the slate date is checked in
  the loader; `odds_source` is a ledger column so snapshot-priced bets
  stay identifiable. Self-test covers all of it (11 cases).
- **Related:** the same selection principle as A-007 — an input error
  that inflates a price gets selected INTO the bet list by the edge
  filter, so odds provenance is a money-safety property, not hygiene.

### A-012: DK blocks the container on IP reputation
- **Filed/Resolved:** 2026-08-06
- **Status:** RESOLVED — GitHub Actions' shared runners are not blocked.
  Verified live: `ubuntu-latest` fetched 16 O/U props + 119 alt rungs,
  zero 403s. No VPS, proxy, or paid API needed. The sibling NRFI repo
  reaches DK the same way (Actions, not Railway); its Railway service
  only polls the MLB Stats API. `runs-on` reads `vars.RUNNER_LABEL`, so
  if GitHub's ranges are ever blocked again, pointing at a self-hosted
  VPS runner is a settings change rather than a code change.
- **Original description (kept: the measurement stands)**
- **Description:** DraftKings returns 403 to Railway's datacenter IP and
  200 to a residential IP for byte-identical requests. Measured, not
  assumed: the gate is **User-Agent**, not TLS/JA3 (plain `requests`
  with a browser UA → 200; default `python-requests` UA → 403; seven
  curl_cffi impersonation profiles → all 200). No client-side change
  affects an IP-reputation verdict, so "try another impersonation
  profile" is ruled out rather than untried.
- **Options:** (1) residential relay — operator's PC captures and
  commits a snapshot, container pulls it (built, no cost, no
  credentials, only as fresh as the last run); (2) paid residential
  proxy (~$3–15/GB, automated, but a third party sees the traffic and
  it is deliberate circumvention of an access control DK applied on
  purpose); (3) paid odds API (~$30–200/mo, contractual right to the
  data, cleanest long-term, needs a new adapter and coverage
  verification for pitcher-K props). Options 2 and 3 cost money and
  need credentials — operator's call.

### A-013: Container writes never reach git (volume is a single point of failure)
- **Filed:** 2026-08-06
- **Status:** Partially resolved — read path fixed, write path needs an
  operator credential
- **Description:** two independent ledgers. The container's jobs
  read/write `DATA_STATE_DIR` (the Railway volume); `git pull` only
  updates the `/app` checkout; `seed_volume_state()` filled gaps only.
  The PC wrote picks to git and the container graded a volume copy that
  never saw them — and since the dashboard prefers the worker's
  `/data.json`, the site would show a quietly wrong record rather than
  an error. Manual `FORCE_SEED` deploys had been masking it.
- **Resolved (read path):** `reconcile_ledger()` merges the checkout
  into the volume after every pull. Union-only, graded-beats-ungraded,
  keyed on `(date, game_pk, pitcher_id, line)`. Timestamps for slate and
  odds files are read from inside the file, never mtime.
- **Still open (write path):** no `GITHUB_TOKEN` on the service, so the
  container commits locally and never pushes. Its work reaches the
  dashboard live but not git, so a lost volume loses any grade the PC
  did not independently compute. Grading is deterministic from Statcast
  and the PC grades too, which limits the blast radius — but it is a
  real single point of failure.
- **Fix requires the operator:** create a repo-scoped GitHub token and
  set `GITHUB_TOKEN` on the Railway service. Claude cannot create or
  enter credentials.

### A-014: A run that could not compute published an empty board
- **Filed/Resolved:** 2026-08-06
- **Description:** the first full CI pricing run wrote a 0-pitcher slate
  sidecar over a good 20-pitcher one and pushed it, deleting 3,225 lines
  of that day's evidence. The Statcast cache is gitignored, so a fresh
  runner has none and every pitcher failed with `insufficient data
  (0 BF)`. The pipeline treated "nothing priced" as a legitimately empty
  slate.
- **Resolution:** `daily_pipeline` raises when DK supplied pitchers and
  none could be priced -- an environment fault is not an empty slate,
  and publishing one is the same class of lie as an unobserved odds
  figure. Workflow caches `data/statcast_cache` and tops up the current
  season each run (~88 MB; CI needs only the current season).
- **Generalises to:** any derived artifact written from a possibly-empty
  computation. Ask whether "no results" is a real answer or a missing
  input, and refuse to publish when it cannot tell.

## Resolved

### R-005: T2 promotions re-gauntleted — all three demoted (was A-005)
- **Filed/Resolved:** 2026-08-05
- **Description:** a9_zone_pct, f1_eastward_tz, b14_n_rookies re-tested
  on the cross-season harness via paired drop-one deltas over 12,653
  OOS starts (`tools/regauntlet.py`). None cleared t ≥ 2 in both
  temporal directions; core model matches full within ±0.00006 Brier.
  Production Stage B is core-only (PRODUCTION_EXTRA_FEATURES = []).
  Future promotions must pass this same cross-season bar.

### R-004: Statcast cache missing 2024, 2025, and Apr-May 2026 (was A-004)
- **Filed/Resolved:** 2026-08-05
- **Description:** ROADMAP falsely claimed the multi-season backfill
  was complete; only 2026-06..08 existed. Backfilled 2024 + 2025 +
  Apr-May 2026 (1.95M pitches total). Cross-season three-way splits
  now run; results in CHANGELOG Phase 9.

### R-001: Isotonic calibration dead code (was silent)
- **Filed/Resolved:** 2026-08-05
- **Description:** Calibrator constructed but never fit/applied in live
  or backtest paths; docs claimed otherwise. Now fit on out-of-sample
  predictions (`tools/fit_calibrator.py`), persisted, loaded, applied
  to per_line and milestone tails.

### R-002: Backtest leakage (features + train/test overlap)
- **Filed/Resolved:** 2026-08-05
- **Description:** Full-window aggregates leaked each game's own and
  future data into its features; Stage A/B trained on the scored
  window. Rebuilt on `features/asof.py` vectorized as-of tables with a
  train ≤ Jul 8 / test Jul 9+ split. Published numbers corrected.

### R-003: Ladder edge inflation
- **Filed/Resolved:** 2026-08-05
- **Description:** No de-vig on one-sided milestones, loose 3% bar,
  model prob written into no_vig_fair_prob. Fixed with ALT_SIDE_MARGIN
  de-vig, 10% threshold, market blend, true fair prob in the ledger.
