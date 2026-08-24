# Audit Log

Tracks open items, resolved items, and known risks.

## Open

### A-001: Licensing decision pending
- **Filed:** 2026-08-04 (amended 2026-08-06)
- **Status:** Awaiting operator decision
- **Description:** MLB Stats API, Open-Meteo free tier, and RotoWire
  all carry non-commercial terms. The operator sells picks. Decision
  needed before Phase 1. See `docs/LICENSING.md`.
- **Amended 2026-08-06 — now also covers imagery.** Pick cards render
  MLB player headshots from `midfield.mlbstatic.com`, keyed on the
  MLBAM id already in each slate row. These are MLB-copyrighted
  photographs served from MLB's public CDN, hot-linked rather than
  copied. That is ordinary practice for fan tools, but this product is
  sold, which puts the images under the same commercial-terms question
  as the data itself rather than a separate one.
- **If the answer comes back no:** deleting `PitcherAvatar` from
  `pick-card.tsx` removes every image; nothing else depends on it, and
  the component already degrades to initials.

### A-002: Historical strikeout prop lines not yet sourced
- **Filed:** 2026-08-04
- **Status:** Open
- **Description:** Phase 3 requires backtesting against real DraftKings
  strikeout prop lines. No historical source identified yet. DK's JSON
  serves current lines only; The Odds API historical endpoint is paid.
  Forward-collecting live lines starting now is the fallback.
- **Escalated 2026-08-16 — this is now the binding constraint on
  A-041, not a Phase 3 nicety.** The question "does the model beat the
  market?" cannot be answered on the backtest, because the two do not
  overlap: `backtest_predictions.csv` spans 2026-04-11..2026-08-04 and
  the only strikeout odds inside that window is a single OPENING
  snapshot on 08-04 (`dk_k_2026-08-04.csv`). Closing captures begin
  2026-08-05. So 18,798 backtest rows are unscoreable against a price,
  and the largest honest market sample is 262 starts.
- **The forward-collection fallback is working** — 12 days of two-sided
  closing lines (334 snapshots, 287 joining to a priced slate) plus
  9,598 alt-ladder rows. At ~25 starts/day, reaching 1,000 market-scored
  starts takes about 40 more days. That is the clock on A-041, and
  nothing else shortens it except buying historical lines.

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

### A-052: the outs model goes live as a shadow product — calibration maps refused, market baseline even
- **Filed:** 2026-08-24 (operator: "lets proceed with making the outs
  model")  **Status:** shadow serving; betting blocked by design
- **What shipped:** the outs market's production layer, built shadow-
  first: `tools/outs_serve.py` (today-rows through the SAME leakage-
  safe builder training uses — placeholder labels proven inert by
  perturbation test), `tools/outs_pipeline.py` (daily board -> own
  sidecar `data/outs_slates/` -> own evidence log
  `data/outs_model_log.csv` -> own payload
  `dashboard/public/outs.json`), worker serving `/outs.json` + outs
  steps on morning/lineups/night/scorecard tasks, and the /outs page
  rendering the live board (disagreements first, settled results
  graded in). NOTHING computes an edge, side, or stake — the payload
  cannot carry a pick by construction.
- **Gate 5, measured to a refusal:** fit Platt, isotonic, AND per-line
  Platt maps on 67,970 pooled cross-season OOS pairs (fit24->25 +
  fit25->24), validated on untouched 2026 via the 2024+2025-trained
  pkl. Raw: pooled ECE 0.0092, mean per-line ECE 0.0205, worst line
  gap 7.4pp. Every map made the PER-LINE quantities worse (per-line
  Platt: mean ECE 0.0247). The ship-gate refused all three — the
  2026 per-line miscalibration is not a stable p->p bias the
  cross-season sample can teach a correction for. A-041/A-044's
  lesson, replicated on the second market and caught by construction
  this time. Serving is raw + PROB_EPS clamp; the worst-line gap
  (7.4pp vs ~3.6pp break-even) is exactly why no bet can price yet.
- **The market baseline the K model never had:** closing outs prices
  were captured BEFORE any model priced anything (A-002's lesson,
  applied in time), so the model could be scored retroactively on
  day one: **373 out-of-sample starts over 16 dates, raw vs closing
  no-vig fair: z = +0.65 — statistically indistinguishable from the
  book** (K model at comparable n: z = +2.10 worse). Recorded to
  `data/outs_scorecard.csv`; re-scored weekly by the scorecard task.
  Indistinguishable is not edge: z must go NEGATIVE on a serious
  sample before the threshold question is even asked.
- **Amended 2026-08-24 (same day, found by the operator asking "will I
  be able to look back at today's results tomorrow?"): the outs
  artifacts had NO persistence or mirror path.** Three defects, all
  latent until the second day: `PERSISTED` omitted `outs_slates`,
  `outs_model_log.csv` and `outs_scorecard.csv`, so every outs board
  and grade would vanish on the next container redeploy;
  `commit_and_push` staged none of them, so the volume was a single
  point of failure and no other host could ever see the evidence; and
  `build_payload` read only the state dir, so a payload rebuilt on a
  worker that had not itself priced yesterday would silently DROP
  yesterday's board — the date stepper would come up empty on exactly
  the first morning it was supposed to work. Fixed: outs entries added
  to `PERSISTED` (the seeding pass is a per-file gap-fill merge, so a
  board committed from another host reaches the volume on the next
  boot and the volume's own copy always wins) and to the
  `commit_and_push` stage list; `slate_dirs()` / `available_dates()` /
  `load_slate()` read the state dir AND the git checkout, so a synced
  board is on the page immediately rather than at the next redeploy.
- **Generalises to:** a new product's artifacts are not shipped until
  they are on the SAME persistence, mirror and merge paths the old
  ones use. Serving them correctly once proves nothing about day two.
- **Generalises to:** capture prices before building the model, and
  the model's first market scorecard is free. And a calibration map
  must always face a holdout gate that can refuse it — both markets
  have now independently proven the refusal path earns its keep.

### A-051: structural round — rate random effect KEEP; TTO-4 and damping measured and rejected
- **Filed:** 2026-08-24 (full-model audit)  **Status:** RE shadowing
- **The defect:** on the full 2026 backtest the served distribution is
  ~10% short of realized K variance (implied 6.03 vs actual 6.66) and
  the actual over-rate exceeds the model's at EVERY line (mean
  -1.2pp). The Poisson-binomial is exactly binomial-dispersed at fixed
  BF; a real pitcher's true rate varies game to game around his
  season estimate, and the compound had no term for it.
- **The fix:** `compound_k_distribution_re` — a per-start latent
  effect p_i(u) = sigmoid(logit(p_i) + delta_i + sigma*u), u~N(0,1),
  5-point Gauss-Hermite, with delta_i solved numerically so the
  conditional mean is EXACTLY preserved (the A-042 load-bearing rule;
  pinned by test to 2e-3). Single-pass prefix-capture DP vectorized
  across nodes.
- **Gate (`tools/gate_rate_re.py`), scored on the distribution's own
  NLL because 6-line Brier barely sees width:** sigma* = 0.15 / 0.15 /
  0.10 across the three splits — interior argmin, identical in both
  cross directions, NLL gain positive everywhere. Brier at the 6-line
  grid: neutral (as expected). Mean per-line bias: relieved modestly
  (-1.86 -> -1.78, -1.16 -> -1.09pp), confirming width was only part
  of the tail story — the left-tail hook (A-042) is the rest.
  **KEEP** -> `RATE_RE_SIGMA = 0.15` ships as the nightly `p_over_re`
  shadow column only; production still serves sigma = 0.
- **Also measured, both REJECTED on the repo's own bar**
  (`tools/measure_design_variants.py`, PA-level, both cross
  directions):
  - TTO-4 split out of tto_3: fitted coefficient sensible (-0.25/-0.23)
    but OOS value is +/-0.01e-4 NLL — TTO-4 is 0.42% of PA and the
    fold costs nothing detectable. Not worth a design change.
  - High-end damping relu(lp_c+lb_c)^2: the sign matchup.py predicted
    (negative, both fits) but OOS value flips direction (-0.12e-4 /
    +0.24e-4) and the magnitude is unstable (-0.28 vs -0.08). The
    live confident-OVER failure (A-041) is evidently the market
    information story, not an in-sample curvature a refit can buy.
- **Deferred with rationale:** BF↔K coupling (a shared latent between
  Stage A's leash and Stage B's rate — "dealing goes deeper / burning
  pitches shortens"). Needs a jointly fitted latent across both
  stages; the measured defect this round was the marginal K variance,
  which sigma addresses. Revisit only if the RE shadow shows residual
  shape error concentrated in long-outing tails.
- **Generalises to:** judge a WIDTH parameter on likelihood, not on a
  handful-of-lines Brier — and preserve the mean numerically, not by
  hope, when adding any mixture or latent to a calibrated point
  estimate.

### A-049: Tier A factor round — first cross-season KEEPs; two features to shadow
- **Filed:** 2026-08-24 (full-model audit)  **Status:** shadowing
- **Context:** the audit located the model's market deficit in the
  low-line / low-history population (line <= 4.5: z=+3.10 worse than
  the closing price) and screened candidate stats against the market's
  own disagreement. The screened candidates were then implemented
  as-of (features/asof.py: asof_swstr_pct, asof_csw_pct, p5_pitches,
  velo_trend, is_home, opp_team + asof_team_zone_contact) with
  perturbation tests, and run through the REPAIRED (A-048) cross-season
  drop-one harness over ~12.6k OOS starts.
- **Verdicts** (bar: drop-delta t>=2 in BOTH cross-season directions):

      p5_pitches    KEEP    t = +3.4 / +7.3 / +3.4  (all three splits)
      is_home       KEEP    t = +2.7 / +2.9 / +0.5
      swstr_pct     DEMOTE  t = +1.8 / +1.3 / +0.0  (helps, under bar)
      velo_trend    DEMOTE  t = -0.8 / +1.7 / +3.0  (direction-unstable)
      opp_zcontact  DEMOTE  t = -0.5 / -0.3 / -1.5  (batter K% owns it)

  The csw-for-swstr swap variant scored no better than full. These are
  the first features EVER to clear the honest bar — the entire Phase 6
  cohort died on it (R-005).
- **What shipped:** `models/stage_b_candidate.pkl` (core + p5_pitches
  + is_home; fit by tools/fit_candidate_stage_b.py; production pickles
  untouched) and a nightly `p_over_candidate` shadow column through
  the A-046 infrastructure. Production Stage B remains core-only.
- **Promotion path:** 14 shadow dates -> tools/flag_shadow_report.py
  -> operator decision, per CLAUDE.md. Do not shortcut on the
  regauntlet t-stats alone: the same harness family promoted three
  features in Phase 6 that died on re-test, and the shadow is the
  guard that catches what a harness cannot.
- **Generalises to:** screen against the MARKET's disagreement to pick
  candidates, but let the repo's own OOS bar and a live shadow decide.
  Strong t-stats earn a shadow, never a flag flip.

### A-048: the promotion harness could not fail features the way it claimed to
- **Filed:** 2026-08-24 (full-model audit)  **Fixed:** 2026-08-24
- **Description:** four defects in `tools/gauntlet.py`, the 5-gate
  tryout every factor must pass:
  1. Gate 4 (collinearity) was invoked with an empty array and empty
     dict — its loop never ran; it has passed every feature ever
     screened. Every recorded Gate 4 PASS is meaningless.
  2. Gate 5 (calibration) re-read Gate 2's Brier improvements — a
     strictly weaker copy of Gate 2's own test. It could never
     independently fail a feature; it measured nothing of its name.
  3. The baseline was not as-of: season K% / BF stats pooled over the
     whole split window INCLUDING the predicted game, while the
     candidate was as-of — a leaked, stronger-than-live opponent.
  4. The augmented model's refit included an intercept+slope
     recalibration the raw baseline never got. Measured: PURE NOISE
     cleared "improves both temporal directions" on 14 of 20 seeds
     (p95 of min-improvement +0.317%). The floor constant in the file
     (0.167%) was calibrated before these repairs and `zone_pct` was
     recorded PROMOTED at +0.166% — below even that.
  Together these explain the promote-then-demote cycle: within-2026
  promotions (a9_zone_pct, f1_eastward_tz, b14_n_rookies) that all
  died on the honest cross-season re-gauntlet (A-005/R-005).
- **Fix:** real Gate 4 (candidate values + production inputs + registry
  partners; UNMEASURED reported as None, never PASS); real Gate 5
  (pooled-line ECE must not degrade past 0.010 either direction);
  as-of expanding baseline loading from season start with production
  gates (50 prior BF / 3 prior starts, ordered by date then game_pk);
  symmetric base-side recalibration. Floor re-derived on the repaired
  harness and PERSISTED (`data/gauntlet_noise_floor.json`): noise now
  clears both directions 0/20, p95 -0.034%. Negative controls
  (lunar, random, shuffled-label) re-run: all REJECTED. The
  first-9-batters lineup proxy is documented as shared optimism.
- **Generalises to:** a gate is only a gate if it can FAIL something.
  Verify each gate rejects a designed-to-fail input at build time —
  the same lesson as the negative controls, applied to the harness
  itself. And a noise floor is a property of the harness that
  produced it; re-derive it whenever the harness changes.

### A-047: the ladder read a key primaries never carry — dead since 2026-08-05
- **Filed:** 2026-08-24 (full-model audit)  **Fixed:** 2026-08-24
- **Description:** `tools/daily_pipeline.py` fed the ladder gate
  `play.get("pick_side")`. Primary plays carry `best_side` — the only
  writers of `pick_side` are ladder plays themselves and the CSV
  serializer. So `primary_side` was always None, `gate_open` in
  `models/ladder.py` (which requires `primary_side == "OVER"`) never
  opened, and every alt-line rung was rejected. Verified three ways:
  the code path, a grep of every `pick_side` write site, and six days
  of slate sidecars showing zero rungs past the gate; the last ladder
  rows in the ledger are 2026-08-04/05, the day the bug shipped in
  35bd8be6 ("Ladder discipline"). Money impact so far: none visible —
  betting has been edge-gate-blocked since 08-14 — but the subsystem
  would have stayed dead when betting resumed.
- **Fix:** lookup extracted into `_primary_for()` (regression-tested
  against the exact production play shape) and read from `best_side`.
  Same commit carries three adjacent money-code fixes: haircut
  re-keyed pitcher-first (same-pitcher corr ~+0.50 vs cross-pitcher
  same-game ~+0.02), Stage A now raises unfitted instead of silently
  substituting a fallback model, and `prob_k_geq` refuses whole-number
  lines instead of folding the push into the over.
- **Generalises to:** a gate whose input key can simply be absent
  fails CLOSED and silent. When a subsystem's output drops to zero,
  that is a signal to check, not a quiet day — the watchdog should
  own "ladder evaluated N rungs, M candidates" as a tracked number.

### A-046: both gated fixes had no shadow path — their 2-week clocks never started
- **Filed:** 2026-08-24 (full-model audit)  **Fixed:** 2026-08-24
- **Description:** `USE_HOOK_MIXTURE` (A-042) and `USE_PRIOR_SEASON`
  both passed their gates and were parked OFF "pending a 2-week
  shadow" — but nothing logged their counterfactual predictions.
  Grep-verified across the pipeline, worker, and tools: no consumer of
  either flag wrote a shadow value anywhere. Ten days after A-042
  shipped, zero shadow evidence existed; the promotion decision the
  flags were waiting on could never arrive. A shadow period that
  nothing records is not a shadow period — it is an indefinite off
  switch that reads like process discipline.
- **Fix:** the pipeline now prices both counterfactuals nightly through
  the production code path (per-call `use_hook_mixture` override;
  `force_prior` that bypasses only the flag, never the substance bars)
  and logs them: `p_over_hookmix` / `p_over_prior` columns in
  `model_log.csv`, plus `data/shadow_prior_log.csv` for pitchers only
  the prior window can price (kept out of every model_log consumer by
  construction). `tools/flag_shadow_report.py` turns the columns into
  the promotion case and refuses a verdict before 14 distinct dates.
- **Generalises to:** "shadow for two weeks, then decide" is only a
  plan if the shadow WRITES something. When gating a feature on future
  evidence, ship the evidence collector in the same commit as the
  flag, or the gate silently becomes permanent.

### A-045: the worker ran out of process slots and served a frozen board for two days
- **Filed/Resolved:** 2026-08-24
- **Description:** Every scheduled CI run from 2026-08-23 20:51 UTC
  onward was red on one watchdog check — "served board is current:
  worker is serving no slate at all" — while all 13 other invariants
  passed. The worker's own log had the cause, twice per pass:

      FAILED git-fetch: exit 255
      error: cannot fork() for remote-https: Resource temporarily unavailable

  fork() returning EAGAIN means the container hit its kernel task
  ceiling: no new process or thread could be created. The image ran
  `python tools/railway_worker.py` as **PID 1**, and PID 1 inherits
  every orphaned process in the container but python reaps none of
  them — each orphan (git's background helpers, children of timed-out
  jobs) became a permanent zombie holding one slot. Deployed
  2026-08-20 17:22 UTC; first casualty 2026-08-22 13:32 UTC
  (~44 h ≈ pass 530, consistent with roughly one leaked slot per
  5-minute publish pass), when `dashboard-data` — whose numpy import
  claims a BLAS thread per host CPU in one shot — began dying at
  import. From 13:37 UTC the worker could no longer regenerate
  data.json, so "nothing to commit": live-grade pushes stopped.
  Fetches (needing only a couple of slots) limped on until the leak
  consumed those too; `last_pull ok=False` from then on. `/health`
  kept answering — everything already running was fine, everything
  needing a new process was dead — so the site quietly served the
  2026-08-22 09:35 ET board while CI went red 20+ times.
- **Why the outage was invisible for a day before CI reddened:** the
  watchdog's staleness check compares the repo's board to the served
  one; the frozen board was *current enough* through 08-22 daytime and
  the 08-23 check times, and pull-liveness alone (correctly, per
  A-029/A-040 lessons) only fails the check once the served slate is
  actually behind.
- **Fixes shipped (all in this commit):**
  1. `tini` is PID 1 (`Dockerfile` ENTRYPOINT) — orphans get reaped;
     the leak mechanism is closed regardless of which child orphans
     them (the specific producer was not identified from logs; with a
     reaper in place it no longer matters).
  2. `_restart_if_leaking()` gauges `/proc` before every publish pass,
     logs `pressure: N pids, M threads`, and exits past 400 pids / 200
     threads. Exit is the fix, not a workaround: leaked slots are
     unrecoverable in-process. Scheduler state is on the volume, so a
     restart costs one publish cycle. `railway.json` now pins
     `restartPolicyType: ON_FAILURE`.
  3. `_run()` treats fork-EAGAIN as FATAL (exit 1 → clean restart)
     instead of logging a failure and carrying on — the A-040 lesson
     ("a failed fetch was terminal for the life of the container")
     applied to the failure mode below it.
  4. `/health` gains `process_pressure` so the climb is visible from
     outside; `OPENBLAS_NUM_THREADS=4` / `OMP_NUM_THREADS=4` cap the
     per-spawn thread burst that made numpy the first casualty.
- **Generalises to:** any resident container that forks. "The process
  is up and /health is green" does not imply "the container can still
  create a process" — gauge the resource, and prefer dying loudly
  while recovery still works.

### A-044: the isotonic calibrator measured WORSE than raw — map switched off
- **Filed/Resolved:** 2026-08-19 (operator asked for it after the
  market-scored comparison; see A-041)
- **Description:** A-043 stopped the calibrator asserting certainty but
  left open whether the map helps at all. Scored against the CLOSING
  LINE — the only benchmark that pays — it does not. 329 starts,
  2026-08-05..08-18, `tools/score_vs_market.py`:

      market fair   Brier 0.2520
      model RAW     Brier 0.2642
      model CAL     Brier 0.2663    <- calibrating made it WORSE

      paired cal - raw: +0.00210 +/- 0.00123 (z=+1.71)
      first half  +0.00263 (n=169)
      second half +0.00154 (n=160)

- **The statistic is borderline and is recorded as borderline.** z=+1.71
  does not clear 1.96. Three things carry the decision anyway: the sign
  is the SAME IN BOTH HALVES; the mechanism is understood (the map
  shifts probabilities UP by +0.018 mean, +0.054 max, against a model
  whose standing error is already an OVER bias); and identity is the
  CONSERVATIVE default — disabling a transform is a return to the
  untransformed number, not a new claim about the world.
- **Effect on the OVER bias:** +0.0447 -> +0.0267 against a 0.4407
  actual over-rate. Roughly halved.
- **Effect on the SERVED probability** (what the board ships, blended at
  `MODEL_TRUST_WEIGHT = 0.5`): paired vs market moves from
  **+0.00524 (z=+2.14, significantly WORSE)** to **+0.00418 (z=+1.72,
  indistinguishable)**. Brier blend 0.2573 -> 0.2562.
- **Effect on staking**, replayed through the production gates
  (`tools/kelly_sweep.py`, new): 18 bets / -9.88u / ROI -27.4% becomes
  20 bets / -7.14u / ROI -17.8%. Same direction at w=0.75 and w=1.00.
- **This REDUCES a loss. It does not create an edge.** The model is
  still not better than the market at the market's own line. **A-041
  stays open** and nothing here licenses a larger stake, a relaxed edge
  threshold, or a raised `MAX_STAKE_UNITS`.
- **Fixed** in `models/calibration.py`: `USE_CALIBRATOR = False`, with
  the measurement recorded at the constant. `strikeout_predictor.
  calibrate_prob()` gates on it.
- **The clamp is NOT part of the decision.** `PROB_EPS` still applies on
  both branches via the new `clamp_prob()` helper — dropping the map
  removes the call that used to clamp, and forgetting it would re-open
  A-043 as a side effect.
- **Not a recalibration promotion.** `tests/test_recalibration_gate.py`
  blocks shipping a REFIT on thin evidence; it does not require keeping
  a map that measures worse. Its own finding — the model is calibrated
  where it agrees with the market and inverted where it does not, and
  "no univariate p -> p map fixes that" — is the same conclusion from
  the other side.
- **Guarded by** `tests/test_param_bounds.py`
  (`test_production_serves_the_unmapped_probability`,
  `test_the_clamp_survives_the_map_being_switched_off`) and the
  rewritten watchdog check, which now asserts the served path MATCHES
  `USE_CALIBRATOR` in either state rather than assuming the map is on.
- **Re-enable only** with a market-scored sample that says the map wins,
  the same bar `tools/recalibrate_live.py` enforces for a refit.


### A-043: three fitted parameters were sitting on their bounds
- **Filed/Resolved (partly):** 2026-08-16 (operator asked for the sweep
  after `alpha = exp(-5)` surfaced in A-042)
- **Amended 2026-08-24 — two of the three closed, audit wired into the
  watchdog.** (1) The outs lambda: extending the grid to 1000 moved the
  raw argmin to the NEW edge, but paired per-start z's show the top of
  the curve statistically TIED (300 vs 1000: z=+1.75) while small
  lambda is genuinely worse (z~+3) — the optimum's neighborhood IS on
  the grid. Selection now prefers a tied interior point over a
  boundary argmin; shipped lambda=300 with the full selection curve
  persisted in the pkl (`meta.lambda_grid`, per-entry `z_vs_best`), and
  `audit_param_bounds` reads that evidence. OOS skill unchanged
  (+7.52% vs +7.49%). (2) The audit now runs on every night job / CI
  pass via the watchdog's `parameter bounds` check: NEW pinnings fail
  red; the two documented A-042 alphas report as tracked WARN until
  the hook-mixture shadow resolves them. Remaining open: the
  calibrator top-bin smoothing (folded into the A-041 recalibration,
  blocked on the market-scored sample; the map is OFF meanwhile).
- **Description:** a parameter at its bound is a fit that FAILED — the
  optimizer wanted a value the search space did not contain and stopped
  at the wall, so the estimate is an artifact of where the wall was put.
  Swept every fitted artifact. Three faces of the same failure:

      OPTIMIZER BOUND  stage_a_fitted.pkl / stage_a_eval.pkl
                       alpha = 0.006737946999085467 = exactly exp(-5),
                       the lower bound of log_alpha (A-042)
      GRID EDGE        outs_hazard.pkl
                       lambda = 30.0 = max(LAMBDA_GRID)
      SATURATION       calibrator.pkl
                       top knot y = 1.0 at raw x = 0.9404

  Stage B is CLEAN: it is fit unbounded, so there is no bound to sit on.
  Recorded as a result rather than skipped.
- **The calibrator finding is the severe one, and it reached the board.**
  PAV assigns each bin its outcome mean, so a top bin whose starts all
  went OVER becomes exactly 1.0. Measured across every 2026 slate: the
  RAW model never exceeded 0.9959, yet **53 ladder rungs were served at
  `model_prob == 1.0000`** — the calibrator manufactured the certainty
  by interpolating toward a saturated knot. Of the 46 with a settled
  outcome, **5 LOST (10.9%)**:

      2026-08-04  Davis Martin    needed K>=2, got 1
      2026-08-05  Drew Anderson   needed K>=1, got 0
      2026-08-05  Drew Anderson   needed K>=2, got 0
      2026-08-05  Casey Mize      needed K>=2, got 1
      2026-08-09  Davis Martin    needed K>=2, got 1

  Every one is a LOW milestone killed by a short outing — precisely the
  early-hook tail Stage A cannot produce (A-042). The workload model
  hides disaster starts, so the calibrator never sees them fail, so it
  prices them as impossible. A-042 and this are one disease seen from
  both ends.
- **Why nothing broke.** p=1.0 makes log-loss infinite and Kelly size
  unbounded. Production survived on two guards that have nothing to do
  with probabilities being well-formed: `MAX_STAKE_UNITS` caps the
  stake, and the 50/50 market blend held the served number to 0.9688.
  Neither was designed for this.
- **Fixed** in `models/calibration.py`: `PROB_EPS = 1e-3` clamps
  `predict()` away from {0, 1}. Applied on the way OUT, not at fit time,
  so the shipped artifact is made safe without a refit. Blast radius
  measured: 61 of 1001 raw values move at all, max change 0.00100, none
  above 0.01, mid-range bit-identical. It is a GUARD, not a
  recalibration — the top bin is still genuinely miscalibrated, which is
  A-041 and open.
- **NOT fixed: the outs-hazard penalty.** `lambda = 30.0` is the largest
  entry in `LAMBDA_GRID = (0.3, 1.0, 3.0, 10.0, 30.0)` and selection is
  `min(grid, key=brier)`, so the curve may still have been falling when
  the grid ran out — the model may be under-regularised. The per-lambda
  scores are computed and printed but never persisted, so the shape of
  that curve cannot be recovered from the artifact. Needs a refit with
  an extended grid; that model is a research artifact (Gate 5 unpassed)
  and touches no bet today, so it is filed rather than rushed.
- **Guarded by** `tools/audit_param_bounds.py` (exit 1 on any finding)
  and `tests/test_param_bounds.py` (8 tests). The audit IMPORTS each
  bound from the module that declares it rather than copying it, so a
  changed bound cannot silently drift the audit green. It also checks
  what the calibrator SERVES, not merely what it stores.
- **Generalises to:** print every fitted parameter next to its bound, or
  assert it landed in the interior. A number on a wall is
  indistinguishable from a number that converged, and both survive code
  review. The same applies to hyper-parameter grids: a selection at
  either end is a grid that was too short, not an answer.

### A-042: the workload model has the wrong SHAPE — built, gated, flag OFF
- **Filed:** 2026-08-16 (the mechanism under A-041, fixed on the
  operator's instruction to attack the early-hook tail)
- **Status:** built and gated; `USE_HOOK_MIXTURE = False` pending the
  2-week shadow CLAUDE.md requires.
- **Description:** batters faced is **left**-skewed and the negative
  binomial is **right**-skewed. Measured on 13,170 starts
  (`data/outs_starts.parquet`, 2024-2026): empirical skew **-1.58**, the
  fitted model's **+0.24**. The consequence, same starts:

      threshold   actual   NB model        ratio
      BF <=  8     3.08%      0.11%   27.6x too rare
      BF <= 10     4.07%      0.58%    7.0x too rare
      BF <= 12     5.03%      2.18%    2.3x too rare
      BF <= 18    14.52%     25.51%    0.6x (too COMMON)

- **Why it is an OVER bias specifically, not noise.** A disaster start
  settles every OVER as a loss and can NEVER settle an UNDER that way.
  Pricing a 1-in-32 event at 1-in-900 therefore inflates P(over) on
  every pitcher, in one direction. That is the mechanism behind A-041's
  measured +5.0pp OVER lean and the inverted top confidence bin.
- **A better-fitted NB cannot fix it, and the evidence was already in
  the pickle.** `alpha` = 0.006737946999085467 = **exactly exp(-5)**,
  the lower bound of `log_alpha` in the fit — the optimizer wanted LESS
  dispersion, not more, and hit the wall. Loosening that bound moves it
  toward Poisson, i.e. worse. No negative binomial is left-skewed at any
  alpha. The process is genuinely two-component: a start is either
  hooked early or it is not, and the model should say so.
- **Fix:** a two-component mixture in `models/stage_a_bf.py` —
  `pi` of starts hooked early (NB about `mu_short`), the rest a normal
  outing. The conditional MEAN is preserved by re-centring the normal
  arm to `(mu - pi*mu_short)/(1 - pi)`; the live mean BF error is +0.00
  over 264 starts, so a change that dragged the mean down would trade a
  tail bias for a mean bias and still look like progress.
- **Gates** (`tools/gate_hook_mixture.py`, reproducible):

      train      test    d(logLik)   tail err        pi    mu_short
      2024       2025     +0.0689   0.0275 -> 0.0141  0.0233   5.96
      2025       2024     +0.0798   0.0292 -> 0.0179  0.0195   6.02
      2024+2025  2026     +0.1234   0.0410 -> 0.0287  0.0213   5.99

  Gate 1 leakage: the as-of mean is `shift(1).expanding()`, strictly
  prior starts, safe by construction. Gate 2: improves in BOTH temporal
  directions AND forward. Gate 3: `pi` 0.0195-0.0233 and `mu_short`
  5.96-6.02 across three DISJOINT fits — that agreement is the evidence
  it is real rather than a curve through noise. Gate 4: N/A, no new
  covariate. **Gate 5: PARTIAL** — left-tail calibration improves every
  split, but Gate 5 asks for P(K >= line), which needs the full
  Stage A -> B -> compound path. That is exactly what the shadow
  measures, and why the flag is off.
- **Two bugs caught in my own analysis before they became results**, both
  by an impossible number rather than by review:
  1. The first mixture fit drove `alpha` to 0, where `1/alpha` in the
     log-pmf returns POSITIVE log-probabilities. It reported a mean
     log-likelihood of **+4.67** (impossible; log p <= 0) and a "tail
     error" of 331, and read as a spectacular improvement. `_check()`
     now refuses any positive log-pmf.
  2. Single-start L-BFGS-B then collapsed two of three splits onto the
     boundary (`pi` -> 1e-4), reporting a dead heat where there was a
     real effect. Mixture likelihoods are multimodal; the fit is
     multi-start and the restarts are load-bearing.
- **Expected size of the correction, stated before the shadow so it can
  be checked against:** moving ~2% of mass to ~6 BF should lower P(over)
  by roughly 1-2 points. A-041's measured lean is 5.0 points, so this is
  a partial correction — it is not expected to close the gap alone, and
  a shadow that shows it closing entirely should be treated as
  suspicious rather than lucky.
- **Generalises to:** check the SHAPE of a fitted distribution against
  the data's, not just its mean and variance. This model's mean was
  unbiased and its variance close, and it was still wrong by 27x where
  the money is. Skew is one line of code and would have caught it in
  April. Also: a parameter sitting exactly on its bound is a fit that
  failed, not a fit that finished.

### A-041: the model is worst exactly where it is most confident — OPEN
- **Filed:** 2026-08-16 (operator: "i think the model itself is wrong")
- **Status:** confirmed, NOT fixed. No bets should be placed until it is.
  The edge gate is already enforcing this by accident — see below.
- **The betting record is NOT the evidence.** 4W-8L on 12 bets is noise:
  going 4-8 or worse happens ~19% of the time on a fair coin. Anyone
  concluding "the model is broken" from that number is reading variance.
- **The calibration curve is the evidence**, on 264 scored starts —
  every evaluated pitcher, not just the bets:

      predicted P(OVER)   actual      n
              0.319       0.333      33
              0.386       0.576      33
              0.439       0.424      33
              0.459       0.394      33
              0.497       0.455      33
              0.546       0.485      33
              0.581       0.485      33
              0.654       0.333      33   <-- 11 of 33

  The top bin is inverted: at a stated 65.4% the model hits 33.3%,
  11/33 against an expected 21.6 (z = -3.9, p < 0.0001). Live Brier
  excess +0.0225 against a backtest excess of -0.0006, band +/-0.0136 —
  roughly 3.4 SE outside, and positive on 9 of 11 days since 2026-08-05.
- **Why that bin is the worst possible one to be wrong in:** the edge
  filter selects from it. This is A-007's shape again — a bad input
  does not merely add noise, it gets chosen. The placed bets show it
  cleanly: **OVER 2W-6L, UNDER 2W-2L**, and every OVER bet came from
  the 0.62–0.94 range.
- **Mechanism is workload, not strikeout skill.** The K point estimate
  is unbiased (mean error +0.02 K). But only 61.4% of starts land
  within 3 batters faced, and 5.3% come in badly short — Davis Martin
  projected 23.1 BF, faced 6.0. An early hook kills an OVER and can
  never kill an UNDER, and that asymmetry is exactly the +5.0pp OVER
  bias (predicted 48.5% vs actual 43.6%).
- **The backtest already said so and it was read past.** Per-line
  `model_excess` was negative (good) at 3.5 and 4.5 but POSITIVE at
  5.5 / 6.5 / 7.5 / 8.5 — the high-strikeout, confident-OVER end.
  Live did not introduce this; it amplified it.
- **Correlation compounds it:** Drew Anderson took 3 of the 8 losses on
  one pitcher in one game (.69 / .94 / .81). The haircut keys on
  repeated `game_pk`, not repeated pitcher — already a roadmap item.
- **Amended 2026-08-16 — the recalibration was measured and the fix
  above was WRONG.** "Refit the calibrator" was the obvious read and it
  does not survive contact with the data:
  - **The calibrator was already fit on 2026.**
    `data/backtest_predictions.csv` covers 2026-04-11..2026-08-04,
    18,798 rows, and `tools/fit_calibrator.py` fits on exactly that.
    Refitting "on 2026 data" was already the status quo.
  - **And on that data it is well calibrated everywhere**, including
    the region bets are placed in — gaps of +0.008 to +0.030 across all
    eleven probability bands, with 2,494 rows between 0.55 and 0.75. At
    0.65-0.70 the backtest actual is **0.705**. The live sample says
    **0.333** in the same band. Both cannot be a property of one p -> p
    map, so the map is not what is broken.
  - **What is broken is conditional on the market**, and it is stark:

        model vs market      n    model pred   actual      gap
        much UNDER          41        0.350     0.488    +0.138
        under               60        0.426     0.367    -0.059
        AGREES              79        0.493     0.506    +0.014
        over                58        0.564     0.431    -0.133
        much OVER           26        0.638     0.308    -0.331

    Where the model agrees with the book it is calibrated to within 1.4
    points. Where it disagrees it is wrong in the direction of the
    disagreement, and the further it disagrees the worse it gets. That
    is adverse selection / the winner's curse, and the edge filter
    SELECTS exactly the rows in the outer columns.
  - **A univariate calibrator cannot express this.** The same model
    probability is well calibrated in one column and inverted in
    another; no p -> p map is right for both.
  - **The model does not beat the market.** Paired over all 264 live
    rows: blended minus market **+0.00531 +/- 0.00276 (z=+1.92)**;
    the standalone calibrated probability minus market **+0.01444 +/-
    0.00562 (z=+2.57, significant)**. Sweeping the market trust weight
    on a within-2026 time split, held-out Brier rises **monotonically**
    with model weight (w=0.0 -> 0.2489, w=0.5 -> 0.2516, w=1.0 ->
    0.2582). Every unit of model weight costs accuracy.
  - **The refit itself:** held-out Brier 0.2516 -> 0.2483, directionally
    better and **not significant (z=-0.89)** on 137 held-out rows.
    Built as `tools/recalibrate_live.py`, which re-derives all of the
    above and refuses to promote; gates in
    `tests/test_recalibration_gate.py`. Not promoted;
    `models/calibrator.pkl` untouched.
- **Root cause of the backtest/live gap: the model has never been
  scored against the MARKET.** `backtest_predictions.csv` carries no
  odds columns at all — only `model_p_over` vs `naive_p_over`. Beating a
  naive baseline on a synthetic 3.5-8.5 line grid says nothing about
  beating a book at the one posted line, and the grid's low-probability
  rows are not where money is staked. This is the same omission already
  written down for the outs model ("Score against the MARKET. Nothing
  here measures that") and it applies here too.
- **Amended 2026-08-16 (second) — scored against the closing line, and
  the answer is no.** Step (1) below could NOT be done on the backtest:
  it spans 2026-04-11..08-04 and closing captures begin 08-05, so the
  overlap is one opening snapshot (see A-002, now escalated). Scored
  instead on the largest window that has prices, via
  `tools/score_vs_market.py`:

      PRIMARY  262 starts, posted line, two-sided so the no-vig fair
               probability is EXACT, one row per start (independent)
        market Brier 0.2526   model raw 0.2646   cal 0.2664   blend 0.2575
        paired vs market:  raw +0.01195 +/- 0.00569 (z=+2.10)  WORSE
                           cal +0.01376 +/- 0.00578 (z=+2.38)  WORSE
                         blend +0.00486 +/- 0.00283 (z=+1.72)  n.s.

      LADDER   1,956 alt milestones over the same 262 starts, clustered
               by start (one-sided, de-vigged by each start's own
               measured two-sided margin — an ASSUMPTION, stated)
        paired vs market:  raw +0.00625 +/- 0.00265 (z=+2.36)  WORSE
                           cal +0.00704 +/- 0.00276 (z=+2.55)  WORSE
                         blend +0.00237 +/- 0.00133 (z=+1.79)  n.s.

  Two samples, one conclusion: **the model is significantly worse than
  the closing line**, and the production calibrator makes it worse still
  in both (raw -> cal widens the deficit in every case). The only
  configuration that is not significantly worse is the 50/50 blend —
  and it is not worse only because it is half market. The blend is never
  BETTER in any sample.
- **The de-vig measured its own bug first, which is why the ladder is
  trustworthy at all.** The first pass normalised each ladder's own
  implied PMF; books post ladders truncated (K>=3 upward), so that sum
  is ~P(K>=3) rather than 1 and it produced a median overround of
  **0.946** — a book with negative vig, which does not exist. It also
  flipped the ladder verdict from WORSE (z=+2.36) to indistinguishable
  (z=+0.03). Replaced with proportional de-vig by the same start's
  two-sided margin (median total_implied 1.060, a 6% hold). Guarded by
  `tests/test_market_scoring.py`, which asserts de-vig can only LOWER a
  probability and that clustered SEs exceed naive ones.
- **What this does and does not establish.** It is 262 starts over 11
  days, not a season. It is enough to refuse a promotion and to stop
  betting; it is NOT enough to conclude the model has no edge in
  general, and the ladder result rests on a stated de-vig assumption.
  The honest position is that the model has never been shown to beat a
  price, and now has been shown to lose to one on every sample that
  exists.
- **Next, in order:** (1) keep collecting closing lines — at ~25
  starts/day, 1,000 market-scored starts is ~40 days out (A-002);
  (2) attack the early-hook tail in the workload model, the mechanism
  behind the OVER bias; (3) one bet per pitcher per slate. Do NOT raise
  `MAX_STAKE_UNITS`, relax the edge threshold, or promote a
  recalibration until a market-scored sample says the model wins.
- **Generalises to:** a model can be unbiased on its point estimate and
  still be dangerous, because bets are placed on the tail of the
  distribution, not the mean. Always calibrate the quantity you BET,
  which is P(clears the line) — never settle for the quantity you
  predict.

### A-040: a wedged git checkout stalled the worker for 27 hours
- **Filed/Resolved:** 2026-08-16 (operator: "theres been so many failed
  runs and issues")
- **Description:** the Railway worker stopped pulling at 2026-08-15
  12:32 ET and served a payload generated 2026-08-15 09:33 ET for the
  next 27 hours. `sync_repo()` fetched, and when the fetch failed it
  recorded the failure and moved on — nothing retried, nothing cleared
  the wedge. So the first failure was terminal for the life of the
  container and only a human redeploy could recover it.
- **CI was healthy throughout.** Worker commits went 285 (08-14) -> 89
  (08-15, stopping 12:32) -> 0 (08-16), while CI kept its 12/day and had
  already published correct boards for both days. The pipeline, the
  model and the ledger were all fine; the worker simply could not
  RECEIVE the work. This is why it presented as "everything is broken".
- **Ruled out by measurement, not assumption:** volume disk 1.21 GB and
  still growing (not full), repo 38 MB / 3.8 MiB pack (not bloated),
  dispatch token valid 23 more days. Container had run 3 days without
  restart.
- **Why it went unseen — the alarm fired and nobody was told.**
  `tools/watchdog.py` caught it exactly right ("worker is serving no
  slate at all for 2026-08-16 ... last_pull ok=False") and exits 1, and
  the CI step has no `continue-on-error` — so **every CI run since
  2026-08-15 was red**. The monitoring was not the gap; surfacing it
  was. Note `_worker_is_pulling()` reads `last_pull` and never
  `last_publish`, which is what made the diagnosis instant once looked
  at (A-029's lesson, still holding).
- **/health lied in a familiar way.** `can_push_to_git: true` all 27
  hours: it is derived from `GIT_STATUS`, which `configure_git()` sets
  once at BOOT and never revisits (`git.checked` read 2026-08-13). The
  same shape the file's own comments warn about — "a success line that
  cannot fail is worse than no line" — reappearing one field over.
- **Fixed** in `tools/railway_worker.py`: a failed pull now clears
  abandoned lock files (`index.lock` and siblings) and retries once
  before giving up, and `last_pull.recovered` records when it did — so a
  container that limps is distinguishable from one that never failed.
  Lock removal is age-gated at `STALE_LOCK_S` (600s) because deleting a
  lock a live git still holds turns a stall into corruption, which is
  strictly worse. Covered by `tests/test_git_lock_recovery.py` (5 tests).
- **Exact wedge unconfirmed.** Railway retains deploy logs only for the
  latest deployment and every deployment since is SKIPPED by design
  (`watchPatterns` excludes `/data/**`), so the running container's log
  was unreachable. The stale-lock hypothesis fits the signature —
  worked, stopped at a precise instant, never self-healed — but is not
  proven. The fix is deliberately broader than that one cause: any
  transient failure now gets a retry.
- **Generalises to:** every long-lived worker needs a path back from its
  own failures, not just a path through them. Recording a failure is not
  handling it, and "the operator will redeploy" is not a recovery
  strategy when nothing tells the operator.

### A-039: the board sat on yesterday, with late games stuck "IN GAME"
- **Filed/Resolved:** 2026-08-13 (operator: "the dashboard still has
  yesterdays games, and some of them still say in game")
- **Description:** three independent causes, each of which produces the
  reported symptom on its own. The feed was never at fault — the worker
  was current the whole time (`generated_at` 10:20 ET, today's slate
  present, `access-control-allow-origin: *`).
  1. **The page loaded once and never again.** `DataProvider` fetched in
     a `useEffect` with `[]` deps and had no interval, no visibility
     handler, nothing. A terminal left open overnight — the normal way
     this is used — showed the date it was opened on indefinitely,
     including any game that was live at load time.
  2. **A ~9-hour window every morning where the newest board IS
     yesterday.** `available_dates` only contains dates with a slate,
     and the slate is written by the 09:00 ET job (measured: 09:19 ET on
     2026-08-13). `page.tsx` defaults to `dates[0]`, so from midnight
     until then it opened onto yesterday with nothing said.
  3. **Late starts frozen mid-game forever.** `poll_once()` computed
     `iso = today_et()` internally and `main()` only ever asked for
     today, so at 00:00 ET the poller moved to the new date and never
     looked back. A starter still on the mound at that moment kept
     `status: in_game` permanently, and the board reads `live.final` to
     decide whether a total can still move.
- **Why it went unseen:** the frozen row still shows the *correct* K
  count — the card takes the number from Statcast and only the badge
  from the live record — so it reads as a live game rather than as
  corrupt data. Nothing errors, nothing is absent, and the two sources
  disagree silently. Same family as A-024a/A-038: the failure output is
  not a failure.
- **Measured before:** every affected row is a late start, and every
  date with an archive has one — 2 days out of 2, not occasionally.
  2026-08-11 Nick Martinez (21:40 ET first pitch); 2026-08-12 Eric Lauer
  and George Klassen (both 22:10 ET). No early game was ever hit, which
  is the signature of a midnight cutoff rather than a bad feed.
- **No money was affected.** All three were no-bet pitchers, and the
  live worker stays read-only with respect to the ledger — Statcast
  remained the graded source of truth throughout. P&L, record and
  grading were never wrong.
- **Fixed** in three places, because the causes fail independently:
  `workers/live_strikeouts.py` takes the date as a parameter and
  finishes yesterday before starting today, bounded both by "every
  starter is final" and by `CARRYOVER_UNTIL_H` so a suspended game
  cannot pin the poller to the past; `archive_state()` is split from
  `write_state()` so a carryover updates yesterday's record without
  overwriting the single-file view of *now*; `tools/dashboard_data.py`
  treats a settled Statcast total as outranking a stopped poll, which is
  what clears the rows already frozen on disk that no future poll will
  revisit; and `dashboard/lib/data-context.tsx` refreshes every 60s and
  on tab focus, keeping the board on screen when a refresh fails.
  `page.tsx` now says so when it has defaulted to a past slate.
- **Second bug found underneath it.** The archive's "only ever grow a
  date's record" guard only caught a *fully* empty payload, but a single
  failed boxscore fetch `continue`s past that one pitcher — so a partial
  cycle could still blank a starter who was already final, reopening
  A-035 through a different door. `_merge_rows` now merges per pitcher.
- **Measured after:** re-polling 2026-08-12 through the carryover path
  returned both stuck starters Final — Lauer 6 K in 27 BF, Klassen 5 K
  in 24 BF — and Lauer's 6 matches the Statcast total his card was
  already showing, so the fix agrees with the graded source rather than
  merely flipping a flag. Covered by `tests/test_live_carryover.py`
  (7 tests); full suite 134 passed.
- **Generalises to:** any poller scoped to "today". The rollover
  boundary is not a quiet moment — it is exactly when the longest-running
  work is still in flight, so a date-scoped worker abandons precisely the
  events most likely to matter. And any display that reads freshness from
  one source and values from another: make the settled source win, or
  the two will disagree in public.

### A-038: the book's disambiguation tag made a pitcher unmatchable
- **Filed/Resolved:** 2026-08-11 (operator noticed the board was short and
  asked where a specific pitcher was)
- **Description:** DraftKings appends the team to a name when two players
  share it — `Ryan Johnson (LAA)`. `_normalize_name` stripped accents and
  Jr./III but not the parenthetical, so the key never matched
  (`ryan johnson (laa)` != `ryan johnson`). The last-name fallback then
  read his surname as `(laa)` and missed too. He was dropped from every
  slate DK listed him on — 2026-08-06 and 2026-08-11 — while carrying a
  live posted line, and never reached the board or the ledger.
- **Why it went unseen:** a dropped pitcher leaves no row anywhere. There
  is no zero, no null, no VOID — the board just looks like a short slate,
  which is indistinguishable from a light day. The single `Unmatched DK
  pitchers:` line went to stdout on a scheduled run nobody reads. Same
  shape as A-024a: a path whose failure output is *absence*.
- **Second bug found underneath it.** `mlb_pitchers` was a dict keyed by
  normalized name, built by assignment. Two probables sharing a name
  would have silently overwritten each other, and the last-name fallback
  took the first candidate it happened to iterate onto. The tag DK
  supplies is exactly the signal needed to disambiguate, and it was being
  thrown away rather than used.
- **Fixed** in `tools/daily_pipeline.py`: the tag is stripped for matching
  and parsed by `_team_tag` for tie-breaking; candidates are collected as
  a list and resolved by `_resolve_probable`, which **refuses** when the
  name is ambiguous and the tag cannot settle it. Attaching a line to the
  wrong arm is worse than dropping the row — it prices one pitcher's
  projection against another's number, and the edge filter selects
  hardest on exactly that mismatch. The tag is also stripped from the
  display name so it never reaches the board, ledger, or grader; no other
  part of the book's spelling is touched, because those joins are
  historical. Covered by `tests/test_dk_name_matching.py` (12 tests).
- **Measured after:** 30 DK props -> 30 matched, 0 unmatched (was 29/1).
  Board went 23 -> 26 pitchers; the other 4 are correct role-gate refusals.
- **Generalises to:** any join on a third party's display string. The
  vendor formats for humans and will decorate a name the moment it is
  ambiguous — and the decoration arrives precisely on the rows where
  guessing is most dangerous.

### A-037: dispatching the task outsourced the cache refresh with it
- **Filed/Resolved:** 2026-08-11 (the cause under A-036, fixed on the
  operator's instruction to fix the lag itself and not just the display)
- **Description:** the worker's Statcast cache was refreshed **once per
  boot** and never on a schedule, so it ran arbitrarily far behind CI.
- **The mechanism that was supposed to prevent this had been disabled by
  success.** `_log_evidence()` calls `refresh_cache()` first, six times a
  day, and its docstring explains at length that this is what stopped
  the 2026-08-07 evidence loss (A-022). But `_log_evidence` runs inside
  the TASK, and the scheduler reads

      if not dispatch_github(name):
          TASKS[name]()

  so once a GitHub token existed and dispatch began succeeding every
  time, the task ran on CI and this container stopped executing that
  path at all. The refresh went with it. Nothing failed; the log said
  the window ran, and it had — elsewhere.
- **Measured:** the container booted 2026-08-10 18:41 ET, mid-games, and
  again 2026-08-11 07:58 ET, before Baseball Savant had published the
  previous day (A-022: 0 pitches at 03:21, 3,530 by 08:59). Nothing
  topped it up between. Result on 2026-08-11, same commit, four minutes
  apart: CI rendered 2026-08-10 at 18/18 actual K totals, the worker at
  1/18 (A-036).
- **Third bug of this exact shape.** A mechanism that worked while the
  fallback path was the normal path, and stopped the day the primary
  path started succeeding: A-025 (Railway became the clock and stopped
  being the publisher), A-036 (the renderer read a host-local cache),
  and this one. The publish_pass docstring already says it outright —
  "The bug only became reachable once the GitHub token was added and
  dispatch began succeeding every time." It was written about a
  different symptom of the same cause and nobody generalised it.
- **Beyond the display.** A-036 made the BOARD correct on a stale cache.
  This is the input side: bullpen fatigue reads yesterday's relief usage,
  so a stale cache silently degrades the leash inputs on any locally-run
  pricing — the fallback path that runs whenever dispatch fails, i.e.
  exactly when the container is already having a bad day.
- **Resolution:** `_run_or_dispatch(name)` — dispatch, and on success
  still run `refresh_cache()` here; on failure run the task locally,
  where `_log_evidence` refreshes as before (no double fetch). Both the
  scheduler loop and the `RUN_TASK_ON_BOOT` hatch go through it.
- **Latent bug removed on the way past.** The boot hatch blanked the task
  name after a successful dispatch and then called `TASKS[""]` anyway,
  raising `KeyError('')` into a handler that logged
  `BOOT TASK ERROR : ''` — on the one path that had actually worked.
  Reproduced in the negative control before deleting it.
- **Made observable, so the next one is not a log excavation.**
  `/health` now carries `statcast_cache`: `latest_date`, `n_days`,
  the byte size of each of the last five days (`null` = absent, 636
  = schema-only, ~450 KB = a real light slate), and `last_refresh`
  with its timestamp, ok flag and window. Answering "is 2026-08-10
  actually in there?" required container-log access during this
  investigation, and the Railway session expired mid-diagnosis --
  which is precisely when a health field earns its keep. Same
  lesson as invariant 11.
- **Locked with tests:** `tests/test_worker_cache_refresh.py`, nine cases
  — refresh on dispatch, no local double-run, no double refresh on the
  local path, a raising task contained, an unknown task contained, plus
  four on the health surface: absent vs empty vs real day, a missing
  cache directory, the refresh timestamp recorded, and a FAILED refresh
  recorded as failed rather than ok. Negative-controlled: the old loop
  body calls `refresh_cache` zero times on a successful dispatch where
  the new one calls it once.
- **Still open:** `backfill_statcast` skips a day once it is >2 days old
  AND >20 KB, so a file written mid-games is large-but-incomplete and
  freezes that way if no refresh lands on the following day. This fix
  makes that far less likely (six refreshes a day rather than one per
  deploy) but does not make it impossible; completeness is not checked
  against the schedule. Same family as A-016.

### A-036: the worker overwrote CI's good board with its own stale one
- **Filed/Resolved:** 2026-08-11 (found by watching for the 09:00 backfill
  that A-035 predicted, and seeing it not arrive)
- **Description:** the board's per-pitcher actual-K totals came from
  `_actual_k_lookup`, which read the Statcast cache **and nothing else**.
  That cache is a ~90 MB per-season tree each host tops up on its own
  schedule, so the same commit produces different boards on different
  machines.
- **Measured, same commit, four minutes apart:**

      chore(ci): 2026-08-11 09:01 ET   2026-08-10 -> 18/18 actual K totals
      worker,    2026-08-11 09:05 ET   2026-08-10 ->  1/18

  CI restores and tops up the cache on every run (the A-014 fix). The
  Railway worker calls `refresh_cache()` at boot and on the 03:00 job —
  both of which land BEFORE Statcast publishes the previous day (A-022
  measured 0 pitches at 03:21, 3,530 by 08:59). So the worker can sit a
  full day behind, and did.
- **Two mechanisms turned a stale cache into a wrong site.**
  `dashboard/lib/data-context.tsx` PREFERS the worker's `/data.json` over
  the committed copy, so the blank board is what the operator sees. And
  the worker commits `dashboard/public/data.json` every five minutes, so
  it overwrote CI's correct 09:01 build with its own within four
  minutes — the good artifact existed, was published, and was destroyed.
- **The numbers were on the worker's own disk the whole time.**
  `data/model_log.csv` had all 18 rows for 2026-08-10 with `actual_k`
  populated, and the worker itself committed them at 09:05. Verified the
  join is sound: 18/18 pitcher_id overlap with the slate and 0 game_pk
  mismatches. Nothing was missing — the renderer was reading the one
  source that happened to be host-local.
- **Resolution:** `_actual_k_lookup` still tries the Statcast cache
  first and still lets it win, then fills any key the cache did not
  supply from `model_log.csv`. The evidence table carries the same
  numbers, is a small CSV that rides the ledger reconcile (so every host
  agrees within one publish pass), and is never-delete-rows by policy
  (A-030). A blank `actual_k` is skipped rather than coerced to 0 — a
  fabricated zero is worse than a blank.
- **Not a substitute for fixing the cache lag**, which still degrades
  the leash inputs `refresh_cache` exists to keep current (bullpen
  fatigue reads yesterday's relief usage). This makes the DISPLAY
  correct on any host; the refresh schedule is a separate item.
- **Locked with tests:** `tests/test_actual_k_fallback.py`, six cases —
  the date filter, the wrong-date leak, the blank-not-zero rule, the
  empty cache, a cache that raises, and Statcast precedence where both
  sources answer. Negative-controlled: the old lookup returns `{}` on
  the worker's exact condition where the new one returns all three.
- **Generalises to:** any artifact rendered on two hosts from a source
  only one of them keeps current. Either make the source shared, or read
  the shared copy — and be suspicious when the same commit produces
  different output in two places.

### A-035: yesterday's results vanished at midnight and came back at 09:00
- **Filed/Resolved:** 2026-08-11 (operator report, twice: "the results
  disappeared… they were all there last night, now they are gone", then
  "the results are still missing from yesterday august 10th")
- **Description:** every night, the previous day's board went blank for
  every starter who was not a graded bet, and refilled mid-morning.
  **Measured 2026-08-11 07:48 ET:** the 2026-08-10 slate served **1 of
  18** pitchers with an actual strikeout total, against 26/26, 28/28,
  20/20, 25/25, 27/27 and 27/28 on every other date in the same payload.
- **Two sources, and a window where neither answers.** A board-wide K
  total can come from Statcast (via `data/model_log.csv`) or from the
  MLB Stats API watcher (A-020). Statcast does not publish yesterday's
  games until ~09:00 ET (A-022 measured 0 pitches at 03:21, 3,530 by
  08:59), so overnight the watcher is the only source — and the watcher
  threw its own data away at midnight:
  - `workers/live_strikeouts.py` wrote ONE `live_state.json`, overwritten
    every 30s with `today_et()`. At midnight it rolled to the new date
    with `pitchers: []`, and yesterday's finals ceased to exist.
  - `dashboard_data._load_live_state()` then discarded the file entirely
    unless its date equalled today's.
  Confirmed live at 07:43 ET: `/live.json` served
  `{"date":"2026-08-11","n_tracked":0,"pitchers":[]}`.
- **`git log` on `data/model_log.csv` dates the refill precisely** — each
  day's actuals first appear in the 09:00 ET run the NEXT morning:
  2026-08-09's at 09:02, 08-08's at 09:05, 08-07's at 09:01. So the hole
  is midnight to ~09:00, nightly, and had been there since the watcher
  shipped on 2026-08-06.
- **Nothing was ever lost from the ledger**, which is why this survived
  three separate looks: bets, P&L, `available_dates` and the graded
  record were correct throughout, in all five copies. Only the
  DISPLAY of un-bet starters' results went missing, and only in a window
  the operator happens to be awake for and the pipeline is not.
- **Diagnostic lesson, recorded because it cost a cycle.** The first
  investigation checked the ledger, found 11/11 rows stable across 200
  commits, and reported nothing missing. The operator's word "results"
  meant the per-pitcher K totals on the board, not the bet ledger. When
  a report says data is missing and the obvious table is intact, find
  the table they are actually looking at before concluding they are
  wrong — the second report was needed to get there.
- **Resolution:** the watcher archives each poll under the date the
  payload is ABOUT (`data/live/<date>.json`, atomic write), and
  `dashboard_data` looks up live rows **per slate date** rather than
  "today". The date guard is kept, not dropped: these rows are keyed by
  `pitcher_id` alone and a starter appears on many dates, so a payload
  applied to the wrong slate would attach one night's strikeouts to
  another night's start — a fabricated result, worse than a blank one.
  An empty poll can never overwrite a date that already has finals, or
  the hole would reopen at the exact moment it matters (the first poll
  after midnight, and every poll on a day with no slate).
- **Provenance unchanged.** Live figures still only fill a gap, never
  overwrite a Statcast or ledger value, and still carry
  `result_source: "live"`. This is a display fix; the graded ledger and
  the evidence table are untouched.
- **Does not retroactively restore 2026-08-10** — that day's live rows
  were overwritten before the archive existed. Statcast filled them at
  09:00 ET as usual. The fix takes effect from 2026-08-11 forward.
- **Locked with tests:** `tests/test_live_results_persist.py`, five
  cases — the rollover survival, the wrong-date leak, the legacy
  single-file fallback, the empty-poll erase, and the filing date.
  Negative-controlled: the old `_load_live_state` returns `{}` on the
  same fixture where the new one returns both pitchers.

### A-034: a lost push race halted a rebase, and the container never recovered
- **Filed/Resolved:** 2026-08-11 (found underneath the operator's report
  that "the results disappeared" — which was a different, benign thing:
  the morning board is built at 09:00 ET and they looked at 07:15)
- **Description:** for four hours the worker committed live grades every
  five minutes onto a **detached HEAD**, pushed none of them, and
  reported `OK git-commit` every time. GitHub's last commit stood at
  03:14 ET while the container went on grading. Nothing alarmed.
- **The trigger is a race, caught exactly in the deploy log.** At
  03:01:23 ET `git-pull` printed `Already up to date.` and returned OK;
  one second later the push was rejected:

      [master 4156452] chore(worker): live grades 2026-08-11 03:01 ET
       ! [rejected]  master -> master (fetch first)
      hint: the remote contains work that you do not have locally. This
      hint: is usually caused by another repository pushing to the same ref.

  CI's `chore(ci): 2026-08-11 03:01 ET automated run` landed inside that
  one-second window. The container now held a commit the remote did not.
- **The rebase is what turned a lost race into a permanent wedge.** The
  next pass ran `git pull --rebase --autostash` and tried to replay the
  container's `dashboard/public/data.json` commit onto CI's commit to
  the same file. They conflict by construction — both processes rewrite
  that file in full — so the rebase halted, left `.git/rebase-merge`
  behind and HEAD detached. Every later pull died on

      fatal: It seems that there is already a rebase-merge directory

  exit 128, ~50 times, 03:16 through 07:22 ET and still going when found.
- **Three failures stacked into one misleading signature.** `git-commit`
  succeeded (onto nothing reachable), `git-push origin master` failed
  non-fast-forward (because `master` is frozen at its pre-rebase position
  while a rebase is in flight), and the log therefore read as a *push*
  problem. The real fault was three steps upstream, and `/health`
  reported only `last_pull: {ok: false}` with no word on which of
  attached / detached / mid-rebase the container was in.
- **Blast radius: nothing lost, and that is by design not by luck.** The
  volume is the source of truth and `reconcile_ledger` never stopped
  succeeding — `11 pick(s), 11 graded` on every wedged pass — so
  `/data.json` served the correct board throughout and the dashboard,
  which prefers the worker's copy, was never wrong. Only the git mirror
  froze. Verified across all five copies of the ledger (local clone,
  `origin/master` walked commit by commit, worker feed, deployed static
  fallback, rendered site): 11 rows and `-7.0532` on every one, never
  shrinking.
- **Resolution: reset, not rebase.** `sync_repo` now aborts any halted
  rebase, reattaches HEAD, `git fetch`es, and `git checkout -B master
  FETCH_HEAD`. `data.json` and the mirrored ledger are DERIVED — both
  are regenerated from the volume later in the same pass — so a conflict
  in them has no correct resolution, only a halt. Taking CI's copy
  wholesale is the correct merge, and it is the same reasoning
  `_bootstrap_repo` already documents for its own `reset --hard`: no
  symlinks reach the volume, nothing tracked is excluded from the image,
  and `reset --hard` leaves untracked caches alone. This also makes the
  race survivable rather than fatal — a lost race now costs one pass's
  commit, which held nothing the volume cannot regenerate.
- **Reattachment happens before the network is touched**, so an egress
  blip while detached cannot extend the wedge past the outage that
  caused it. `commit_and_push` refuses outright while detached or
  mid-rebase rather than logging `OK git-commit` onto an unreachable
  HEAD, and `/health` now publishes `head: {branch, detached,
  rebase_in_progress}`.
- **Locked with tests, negative-controlled:**
  `tests/test_worker_git.py::test_halted_rebase_is_cleared_instead_of_wedging_forever`
  builds a real conflicting rebase and halts it for real, rather than
  faking the symptom; two siblings cover the failed-fetch reattach and
  the detached-commit refusal. Confirmed the harness reproduces a
  genuine wedge and that the OLD command cannot clear it: exit 128,
  `detached: True, rebase_in_progress: True` before and after. (The
  old code's first stderr line differs by path — `unmerged files` here
  versus `already a rebase-merge directory` in production, because
  production runs `git checkout -- data.json` first each pass — same
  exit code, same permanent wedge.)
- **Generalises to:** any long-lived process that git-merges a file it
  regenerates itself. If a conflict in an artifact has no meaningful
  resolution, do not ask a three-way merge to find one; decide which
  side is authoritative and take it whole.

### A-033: the A-023 build-skip half-worked, and every surviving build compiled numpy
- **Filed/Resolved:** 2026-08-10 (found from the operator's Vercel usage
  chart: 91 CPU-hours on `mlb-strikeouts` for Aug 7-10, 96.4% of all
  build CPU across their three projects)
- **Description:** two independent causes multiplying. A-023's skip was
  working — most deployments are CANCELED and no verdict was wrong — but
  its recovery path was not, and each build that did survive spent four
  fifths of its time on Python the site does not use.
- **Cause 1 — the reach-back never worked, and failed silently.** Vercel
  clones ~10 commits deep; the worker pushes every 5 minutes, so the last
  BUILT commit leaves that window in under an hour. The fetch meant to
  retrieve it was `git fetch --depth=250 origin "$BASE"` falling back to
  `git fetch --deepen=250`. Both were redirected to `/dev/null`, so the
  failure left no evidence beyond the fail-safe firing.
- **The first fix guessed the cause wrong, and the logging caught it.**
  The guess was that GitHub refuses to serve a raw SHA. It does not
  matter whether it does: **there is no remote named `origin` in Vercel's
  build container at all.** The 14:58 UTC build, the first to run with
  failures printed rather than discarded, said so three times:
  `fatal: 'origin' does not appear to be a git repository`. The container
  has the objects and refs but no configured remote, so every
  `git fetch ... origin ...` was dead on arrival — the original script's
  and its replacement's alike. Resolution: read `git remote`, and when it
  is empty rebuild the provider URL from `VERCEL_GIT_REPO_OWNER` /
  `VERCEL_GIT_REPO_SLUG`. Both values are printed.
- **Measured, not assumed:** two independent windows of Vercel's
  deployment list — Aug 9 06:14-07:55 ET and Aug 10 12:36-13:52 UTC —
  each show exactly 2 forced builds per ~90 minutes, i.e. ~30 needless
  30-core builds a day, every one of them announcing
  `last built commit ... is not reachable in this clone — building`.
- **Cause 2 — `requirements.txt` triggered a Python install on a static
  site.** Vercel auto-detects it at the repo root. On CPython 3.14.3
  neither numpy 2.2.5 nor pandas 2.2.3 has a wheel, so both compiled from
  source: 60s and 81s, 84s of Python against 23s of npm + `next build`.
  A larger share of CPU than of wall clock, since the compile
  parallelises across 30 cores and `next build` does not.
  `dashboard/package.json` declares no Python dependency and `dashboard/`
  holds no `.py` file.
- **Arithmetic reconciles:** ~30 builds/day x 4 days ~= 120 builds; 91
  CPU-hours / 120 ~= 45 CPU-min per build, matching the ~45 CPU-min/build
  already recorded in the A-023 header comment.
- **Resolution:** deepen along the BRANCH (`git fetch --deepen=500 origin
  $VERCEL_GIT_COMMIT_REF`) with `--unshallow` behind it and fetch-by-SHA
  last; no fixed depth can be right once the skip holds and the gap
  between builds grows without bound. Every failure is now printed.
  `installCommand` in `vercel.json` overrides the auto-detected install.
  CI and Railway still use `requirements.txt` normally.
- **Cause 2 verified in production:** the build this work triggered
  reported `Build Completed in /vercel/output [19s]` against `[2m]`
  immediately before, with no `Using CPython` / `Building numpy` /
  `Building pandas` anywhere in the log.
- **Cause 1 verified against a harness that now reproduces the real
  condition** — depth-10 clone, `git remote remove origin`, 25-commit
  data-only gap — which the first harness did not, because it left
  `origin` in place and a local remote serves any SHA. It now prints
  `remotes configured: []`, `using remote: https://github.com/...`,
  `fetch[deepen]: ok`, and SKIPs. The same harness still BUILDs when a
  code commit is in the gap, and BUILDs with every failure named when no
  remote is reachable. A-023a's fail-toward-BUILD holds in all paths.
- **Generalises harder than the git detail does:** the first diagnosis of
  cause 1 was plausible, argued with confidence, and wrong, and it would
  have shipped as "fixed" had the failure path stayed silent. The
  logging, not the reasoning, produced the answer.
- **Generalises to:** any fallback whose failure path is the expensive
  one. `|| true` with output sent to `/dev/null` converts a broken
  recovery into a silent recurring cost. If a fail-safe is worth having,
  its firing is worth logging with the reason attached.
- **Follow-on, not fixed here:** a code commit pushed during a worker
  cycle builds twice, because `VERCEL_GIT_PREVIOUS_SHA` still points at
  the pre-code build while the first build is in flight. With cause 2
  fixed a duplicate costs ~23s, so it is left alone rather than guessed
  at.

### A-032: two dead inputs, and a lineup fallback with zero variance
- **Filed/Resolved:** 2026-08-10 (found by a 68-factor screen answering
  "which factors are wrong")
- **`has_pitch_limit` = +0.00000, and could never be anything else.**
  `prepare_training_data` hardcodes it False on every training row because
  `data/manual_pitch_limits.csv` has never held a data row. Zero variance,
  zero fitted weight, zero effect on any price. **Removed from the design.**
  The serve-time BF cap in `predict_bf_distribution` is a separate
  mechanism and stays.
- **`bp_heavy` fails Gate 2 in all three directions** on total K
  (dRMSE -0.023 / -0.035 / -0.006; t -0.51 / +1.33 / +0.64) and is null on
  batters faced. Measured for the outs target it flipped sign by season.
  **Removed.**
- **The pre-lineup fallback fabricated the opponent.** `[LEAGUE_K_RATE]*9`
  fired on **31.7%** of the logged board (40 of 126) — a constant with
  **sd exactly 0.0000** standing in for the single largest opponent term.
  Replaced with the opponent team's as-of shrunk K%, which recovers
  **68.5% / 76.8% / 57.0%** of a confirmed lineup out-of-sample. Verified
  live: 30 teams, 0.183 (AZ) to 0.254 (CIN), sd 0.0169 — ~1.6 strikeouts
  of spread at 22 BF that the model could not previously see. With no team
  history the pipeline now **skips rather than substitutes** (A-007's rule:
  an invented input is selected INTO the bet list because it flatters the
  projection).
- **Regression check:** cross-season backtest **+3.9% / +4.9% / +3.2%**
  after (was +3.8% / +4.8% / +3.2%). Both directions positive, decision
  split positive. Stage A is now 4 coefficients; calibrator refit.
- **Does NOT close the edge question.** w* = -0.775 (5/5 slates negative,
  permutation p = 0.040) is not caused by an inverted factor: the model
  prices the same three variables as the line at nearly the same weights,
  so its disagreement is largely estimation noise on shared inputs.
  16-18% of the line's variance is outside everything this repo can
  compute and predicts actual K at t = +2.62 — the largest |t| on the
  board, belonging to the market. The 68-factor screen (0/50 random
  controls, 0/3,200 shuffled-label refits) establishes it is not in the
  pitch-level cache.
- **Correction recorded:** `logit_batter_k` is NOT worthless. A reading
  ported from the outs research was wrong — for outs a strikeout and a
  groundout are both one out, so opponent K% carries nothing there. For
  strikeouts it is the most valuable term (dropping it costs
  +1.85/+2.84/+1.08% OOS RMSE) and the shipped +1.06479 is ~12% light
  against refits of +1.213 to +1.223. Reweighting is open work.
- **Correction to the correction (2026-08-24): the "12% light" claim
  does not survive a like-for-like refit.** The 1.213–1.223 figures
  come from `tools/factor_screen_k.py::deep_stage_b`, which fits on the
  SCREEN's own feature definitions (`a_kpct`, `b_k_pct` — its own
  shrinkage), not on production's w=70/w=60 shrunk rates. A logistic
  coefficient rescales with its input's spread, so coefficients are not
  comparable across different shrinkage configurations. Refit fresh on
  production features (backtest eval fit, train 2024+2025):
  `logit_batter_k = +1.0565` — the shipped +1.06479 is what this
  design fits to, not an underweight. Reweighting is NOT open work;
  the real upgrade path for the opponent term is the screen's own
  swap test (batter whiff-per-swing vs batter K%), which goes through
  the gauntlet like any other factor.

### A-031: the shadow tool declared a money decision ready on the wrong count
- **Filed/Resolved:** 2026-08-09
- **Description:** `tools/shadow.py` computed
  `ready_to_decide = len(rows) >= 100`, counting **observations**
  (evaluated pitchers). A-006's gate is **"100+ graded BETS with positive
  average CLV"**. Measured 2026-08-09: 100 observations printed `READY`
  while the production weight had **2 bets** behind it and an average CLV
  of **-15.95%** — roughly fiftyfold less evidence than the gate asks
  for, and it erred toward RAISING `MODEL_TRUST_WEIGHT`, which increases
  stake exposure.
- **Third instance of one defect this week.** `can_push_to_git` reported
  an env var instead of a capability (A-029); the served-board check
  measured version age instead of staleness (A-029); this reported
  evidence volume instead of evidence of the kind the gate names. Each
  answered an easier question than the one that mattered, and each read
  green while the thing it guarded was not satisfied.
- **Resolution:** `_is_ready()` requires BOTH halves — `n_bets >=
  BET_TARGET` **and** positive average CLV — judged at the **production**
  weight, because only that column's bets are real. The rest of the grid
  is counterfactual and shows direction, never authorisation. Every row
  now prints its own `n/100` shortfall, and a NOT-YET verdict says so in
  words rather than leaving a good-looking CLV column to be read as
  decisive.
- **Locked with tests:** `tests/test_shadow_readiness.py` — observations
  are not bets; volume alone is not enough; both halves must pass
  together; a missing production column fails closed; readiness is not
  read off whichever counterfactual column looks best.
  Mutation-checked: restoring the always-ready form fails four of five.

### A-015: The bet filter is set where nothing can pass (A-006 deadlock)
- **Filed:** 2026-08-06
- **Status:** Evidence collection shipped; the money decision is the
  operator's and needs ~2 weeks of data
- **Description:** `MODEL_TRUST_WEIGHT = 0.5` halves every edge before a
  ~8% threshold, so a bet requires a **~16% raw disagreement with
  DraftKings (26% on a projected lineup)**. Real prop edges are 3-8%.
  On 2026-08-06, 5 of 15 confirmed-lineup pitchers had raw gaps of
  8-12% and none cleared. Each gate (half-trust blend, vig margin, EV
  floor, lineup penalty) was justified individually after the 8/4-8/5
  losses; stacked they multiply into an unreachable bar.
- **The deadlock:** A-006 allows raising trust only after "100+ graded
  bets with positive CLV". At ~0 bets/day that evidence is never
  collected. The gate requires proof that the configuration prevents
  gathering.
- **Shipped:** `tools/shadow.py` scores the counterfactual portfolio for
  a grid of trust weights off `model_log.csv` (~20 rows/night, already
  captured with outcomes), including CLV, with no money at risk. Uses
  the production edge/staking functions via a `trust_weight` override
  rather than a reimplementation. Shadow P&L is tagged
  `shadow_flat_100u` and `pnl_guard` enforces bidirectional separation
  from real units. Runs nightly; rendered on /model.
- **Early read (26 reconstructed rows — diagnostic only):** higher trust
  loses too (0.8 -> -6.04u, 1.0 -> -4.15u). Do NOT act on this; it is
  one reconstructed slate.
- **Decision rule:** watch CLV, not W/L — it converges in weeks rather
  than months. A trust weight is only worth adopting if its CLV stays
  positive as the sample grows toward 100.

### A-016: Empty Statcast day cached permanently — a full slate of evidence lost
- **Filed/Resolved:** 2026-08-06 (found by tools/watchdog.py on its first run)
- **Description:** `backfill()` treated any cached parquet over **500
  bytes** as final. An empty parquet (schema, zero rows) is **636
  bytes**. So a day fetched while its games were still in progress was
  written empty and then skipped forever. `2026-08-05.parquet` sat at
  636 bytes / 0 rows, so `model_log.py` could not score that slate and
  **28 prospective observations were silently lost** — the only kind
  that count as validation, since 8/4 is reconstructed. Nothing raised.
  It would have recurred every single day.
- **Resolution:** a cached day is final only if it is strictly before
  today AND larger than `EMPTY_PARQUET_BYTES` (20 KB; a light real slate
  is ~450 KB). Today and later are always re-fetched, because games may
  still be live. 8/5 recovered: 4,376 pitches, 28 rows logged.
- **Generalises to:** any "is it already cached?" test that keys on a
  proxy (size, existence, mtime) rather than on the content being
  complete. Ask what an EMPTY-but-valid artifact looks like, and make
  sure the threshold sits above it.

### A-017: 87% of correctness bugs fail silently — invariant watchdog
- **Filed:** 2026-08-06
- **Status:** Shipped; runs nightly and in CI
- **Description:** of 16 correctness bugs found on 2026-08-06, **2
  raised an exception and 14 did not**. The rest were successful-looking
  runs producing wrong, empty, or stale output: a calibrator that was
  never fit, a 0-pitcher board published over a good one, two silently
  diverging ledgers, a reconcile gated on row count that dropped every
  incoming grade. Error monitoring would have caught 2 of 16.
- **Shipped:** `tools/watchdog.py` asserts invariants rather than
  watching for exceptions — calibrator actually moves probabilities,
  both stages carry coefficients, ledger never shrinks, stored P&L
  recomputes, today's board is non-empty and priced, model log grew for
  the last graded slate, Statcast covers through yesterday, no bet
  priced from unknown odds, the scheduler actually ran, and the
  published P&L equals the ledger. A check that cannot run reports WARN,
  never a silent pass. Read-only by design: a self-healing watchdog
  hides the signal it exists to surface.
- **Proved on arrival:** first run found A-016 (28 lost observations).
  Negative controls confirm it fails on an empty board, a missing cache,
  and a dashboard that disagrees with the ledger.

### A-018: Both stages are at their pre-game ceiling — accuracy is not the problem
- **Filed:** 2026-08-06
- **Status:** Open — redirects the improvement question entirely
- **Description:** asked to "fix the leash", measured it first. Batters
  faced and strikeouts each split into between-pitcher variance
  (reachable pre-game) and within-pitcher variance (game-level noise no
  pre-game model can touch). Measured on 2,910 starts / 206 pitchers,
  2026 season (`tools/ceiling.py`):

  | stage | perfect pre-game model | ours, same sample | of ceiling |
  |---|---|---|---|
  | leash (BF) | 2.71 BF mean abs err | 2.89 BF | **94%** |
  | strikeouts | 1.63 K mean abs err | 1.77 K | **92%** |

  Total remaining headroom: 0.18 BF and 0.14 K. At ~0.22 K per batter
  faced, the entire leash improvement is worth about 0.04 K — 4% of one
  strikeout, against lines quoted in halves. Immaterial.
  The earlier "live mean |error| 2.60 BF, only 57% within +/-3" reading
  looked alarming but was BETTER than a perfect model would post on 28
  rows; the 62% within-+/-3 figure is the ceiling, not a target.
- **What this means:** prediction accuracy is essentially solved to the
  limit of what is knowable before first pitch. Further feature work on
  either stage cannot move the money.
- **The real gap — we have never scored the model against the market.**
  `data/backtest_predictions.csv` prices a fixed six-line grid
  (3.5-8.5) and contains no market prices at all; the published
  "+3.2% to +4.8%" is edge over a NAIVE season-K% baseline, not over
  DraftKings. Beating season-K% is easy; DraftKings is the actual
  opponent. This is A-002, open since 2026-08-04.
- **Only market-relative evidence so far is negative:** shadow CLV is
  below zero at every trust weight (0.5 -30.5%, 0.65 -7.7%, 0.8 -0.5%,
  1.0 -0.3%) on the first 28 prospective observations. Thin, but it
  points the same way: when we disagree with the book, the book has
  been right.
- **Next:** stop tuning accuracy. Let the shadow portfolio and CLV
  accumulate (~20 rows/night, already automated) and answer the only
  question that pays: is there any subpopulation where we beat the
  closing line? If there is not, no amount of model work fixes it.

### A-019: GitHub's cron fires, but hours late on first activation
- **Filed:** 2026-08-06
- **Status:** RESOLVED — cron works; the Railway dispatch covers the lag
- **Resolution (2026-08-06 21:51 ET):** the first schedule-triggered run
  arrived **8 hours 39 minutes** after the workflow was created, then
  succeeded. So GitHub cron is real but its first activation is very
  slow and, per GitHub's own docs, deprioritised under load thereafter.
  Treat it as a backstop, never as the clock.
- **The grace windows earned their keep.** That 21:51 run found every
  one of the day's six windows already past its grace period and logged
  a SKIP for each with the exact lateness (night 1132 min, morning 682,
  closes 577/412/217, lineups 307), then reported "nothing was due".
  Without those windows it would have fired a *morning slate* job at
  9:52pm and re-priced a slate whose games were over.
- **Net:** two independent schedulers. Railway dispatches on time
  (verified: all six windows hit on 2026-08-06); GitHub cron catches
  anything Railway misses. Neither is a single point of failure.
- **Original description kept below — the measurement stands.**
- **Description:** the workflow was created 2026-08-06 13:12 ET. In the
  following 6.5 hours, twelve-plus cron windows passed and **zero**
  schedule-triggered runs occurred. Every run so far is a manual
  `workflow_dispatch`. Configuration is provably fine: workflow state
  `active`, Actions enabled with `allowed_actions: all`, repo public,
  not a fork, not archived, `daily.yml` present on the default branch
  (`master`), both crons parsed (`0,30 6-23 * * *`, `0,30 0-2 * * *`).
  GitHub's `schedule` trigger is documented as best-effort and is
  deprioritised under load; the sibling NRFI repo's daily.yml carries
  the same observation ("9am ET cron firing at 11:16 AM ET").
- **Why it matters:** if cron never fires, nothing runs. The whole cloud
  path silently does nothing, which is the exact failure mode this
  system keeps producing.
- **Mitigation shipped:** `railway_worker.dispatch_github()`. The
  Railway container is a resident process whose ET scheduler DID fire
  all six windows on 2026-08-06; it now dispatches the Actions workflow
  instead of running the pipeline itself, so Railway is the clock and
  Actions are the hands (Actions can reach DraftKings, Railway cannot).
  Falls back to running locally when no token is present.
- **Blocked on the operator:** dispatch needs `GITHUB_TOKEN` (repo
  scope) on the Railway service — the same credential A-013 needs for
  pushing. One token closes both.
- **Until then:** the cloud path depends on GitHub's cron actually
  firing, which is unverified. `tools/watchdog.py` cannot cover this:
  it runs inside the workflow, so a workflow that never starts produces
  no alarm.

### A-020: Results sat unknown for hours after they were already decided
- **Filed/Resolved:** 2026-08-06
- **Description:** a starter's strikeout total is settled the instant he
  is pulled, but grading ran off Statcast at 03:00. A pick decided at
  7:20pm sat unresolved overnight, and the board showed blanks for
  results that were already final. On 2026-08-06 at 20:12 ET, 13 of 18
  tracked starters were finished and the system knew none of them.
- **Resolution:** `workers/live_strikeouts.py` polls the MLB Stats API
  (free, and unlike DraftKings it does not block datacenter IPs -- the
  one job this container is better placed for than GitHub Actions). It
  reads `battersFaced` / `strikeOuts` straight from the boxscore and
  treats a starter as finished when a later pitcher appears for his
  team -- an already-happened fact from the appearance order, not an
  inference from innings or pitch count. Runs as a thread in the Railway
  worker, served at `/live.json`, and rebuilds the board only when a
  starter newly finishes.
- **Deliberately read-only against the ledger.** Statcast stays the
  graded source of truth; live figures fill a gap in the display and
  never overwrite a Statcast or ledger value, and carry
  `result_source: "live"` when they do. A feed that can revise itself
  mid-inning must not book money -- same provenance discipline as
  A-011's odds snapshots.
- **Also enables:** immediate detection of a scratched starter, which
  CLAUDE.md grades VOID and which previously could only be noticed
  after the fact.

### A-021: Ledger grades as soon as the starter is pulled, Statcast confirms
- **Filed/Resolved:** 2026-08-06
- **Description:** grading waited for the whole GAME to finish, so a
  pick settled at 7:20pm booked at 03:00 the next morning. The gate was
  the only thing forcing the wait -- the grader already read the same
  MLB boxscore the live watcher does.
- **Change:** grade when the game is final OR when this pitcher has
  been relieved. `starter_is_relieved()` lives in
  `workers/live_strikeouts.py` and is imported by `tools/grader.py`, so
  early grading and live display share ONE definition of "finished" and
  cannot drift apart. The live watcher triggers the grader the moment a
  starter newly finishes.
- **Why it is safe to settle early:** being relieved is an
  already-happened fact from the boxscore's appearance order, not an
  inference from innings or pitch count. Three guards:
  1. A pitcher who has NOT appeared is never "settled" — otherwise an
     opener or a delayed first pitch would settle a bet that has not
     begun. Verified: unknown pitcher id and currently-pitching reliever
     both return False.
  2. A no-show only becomes VOID once the GAME is final. Voiding a live
     bet is unrecoverable.
  3. The existing postponed/suspended check still runs first.
- **Confirmation:** `graded_source` records which condition settled each
  row (`starter_relieved` / `game_final`, blank for pre-existing rows),
  and `tools/watchdog.py::check_statcast_confirms_grades` re-derives
  every graded K count from Statcast — a separate pipeline from a
  separate source. Agreement is evidence; disagreement FAILS loudly.
  Rows Statcast has not published yet are reported as unconfirmed, never
  as agreed. First run: 9/9 agree exactly.
- **Verified end-to-end** on a ledger copy during live games: Mikolas
  graded WIN (1 K vs UNDER 3.5), P&L +0.56u, CLV +8.6%,
  `graded_source=starter_relieved`, while his game was still in play.

### A-022: The 03:00 job runs before Statcast publishes — evidence lost nightly
- **Filed/Resolved:** 2026-08-07 (found by the watchdog failing CI 7 times)
- **Description:** every CI run from 04:35 UTC onward went red on
  `model log growing: 2026-08-06 has a board but no model-log rows`. The
  night job HAD run and correctly re-fetched 8/6 (the A-016 fix worked
  — the log shows `re-fetching (only 636 bytes, looks empty)`), but the
  fetch returned nothing: **Baseball Savant had not published 8/6 yet**.
  Measured directly — 0 pitches for 8/6 at 03:21 ET, 3,530 by 08:59 ET.
  So `model_log.py` logged 0 pitchers and the slate's 20 observations
  were lost, exactly as 8/5 had been.
- **Root cause is scheduling, not caching.** A-016 fixed the frozen
  empty file; this is the separate problem that the only attempt to log
  a slate happens hours before its data exists.
- **Resolution:** the 10:30 morning job now re-runs `model_log.py` and
  `shadow.py` as well. Both are idempotent per date, so a second pass
  costs nothing and Statcast has published by then. The night job keeps
  its attempt, which now serves as the catch-up for the day before.
- **Watchdog tuned, not silenced:** a missing yesterday before noon ET
  is a WARN naming the publish lag; after noon it is still a FAIL. The
  old rule painted every run red between 03:00 and 10:30 for a condition
  that resolves itself.
- **Recovered:** 8/6 backfilled and logged (20 rows). Model log 54 -> 74,
  shadow evidence 28 -> 48 observations over 2 slates.
- **Generalises to:** any job that reads a third-party feed on a
  schedule. Ask when the data actually lands, not when the games end.

### A-023: Every automated data commit rebuilt the whole site
- **Filed/Resolved:** 2026-08-07 (found from the operator's Vercel bill)
- **Description:** Vercel builds on every push to master. The CI job
  pushes a `chore(ci)` commit whenever the ledger moved — up to 48 runs
  a day — so the entire Next.js app was rebuilt to ship a changed
  `data/` directory. The strikeouts project reached 78 CPU-hours
  (40.5% of the plan's build allowance) and the sibling NRFI project
  99 CPU-hours (51.6%) in one cycle. Between them they consumed 92% of
  the allowance.
- **Why it was pure waste:** `dashboard/lib/data-context.tsx` fetches
  live from the Railway worker and only falls back to the bundled
  `public/data.json` if that fetch fails. In normal operation the
  committed copy is never read — so the rebuild refreshed a file the
  browser overrides at runtime.
- **Verified before shipping, not assumed:** the worker sends
  `Access-Control-Allow-Origin: *` (`railway_worker.py:197`), and both
  endpoints were fetched live — Railway `generated_at`
  13:51:44Z vs Vercel's bundled 13:51:36Z. The runtime path genuinely
  works, so a stale bundled copy is a fallback-only concern.
- **Resolution:** `scripts/vercel-ignore-build.sh`, wired as
  `ignoreCommand` in `vercel.json`. A commit touching only `data/` and
  `dashboard/public/data.json` exits 0 and Vercel skips the build.
  Anything else exits 1 and builds. Fails toward BUILDING when the diff
  cannot be computed — a needless build costs minutes, a wrongly
  skipped one ships stale code silently.
- **Classifier checked against the last 25 real commits:** all 7
  `chore(ci)` commits classified SKIP, all 18 code commits BUILD, zero
  misclassifications.
- **Honest sizing of the win:** the 8/5–8/6 spike (36 and 26 builds)
  was development churn — real code, real rebuilds, one-off. This fixes
  the permanent leak underneath it: at steady state a slate day is
  ~10–18 automated commits and now zero builds.
- **NRFI is the larger consumer and is NOT fixed** — separate repo
  (`MLB-first-inning`, 49 commits on 8/6 via `auto: predict` /
  `auto: grade` / `auto: daily backup snapshot`), awaiting operator
  go-ahead before touching a sibling production system.

### A-023a: the skip could have shipped code that never went live
- **Filed/Resolved:** 2026-08-07 (found red-teaming A-023 the same day)
- **Description:** the first implementation diffed `HEAD^` against
  `HEAD`, which is correct only when a push carries exactly one commit.
  One push can carry several. If a code commit sits UNDERNEATH a
  data-only commit in the same push, `HEAD^..HEAD` sees only the data
  commit, the build is skipped, and the code silently never reaches
  production. Nothing turns red — Vercel records a CANCELED deployment
  that is indistinguishable from the healthy case.
- **Reachable here, not hypothetical:** `tools/odds_relay.py`
  `_publish_hint()` prints `git push origin master` for the operator to
  run by hand. A bare push ships every unpushed commit, and the odds
  snapshot commit touches only `data/odds`, so it lands on top. Any
  local code commit rides underneath it.
- **Never actually happened:** repo history has zero merge commits and
  every deployment so far advanced by exactly one commit. The exposure
  was to operator habit, not to the CI pipeline (which commits
  server-side on top of already-built code and stages only `data/` plus
  `dashboard/public/data.json`).
- **Resolution:** compare against `VERCEL_GIT_PREVIOUS_SHA` — per Vercel
  docs, "the SHA of the last successful deployment; only available when
  an Ignored Build Step is configured." A skipped build is not a
  deployment, so the value stays pinned to the last commit whose code is
  actually LIVE. Consecutive data commits each compare against real live
  code, and a code commit anywhere in the gap is caught. No baseline,
  unreachable baseline, or manual redeploy all build.
- **Locked with tests:** `tests/test_vercel_ignore_build.py`, 6 cases
  against a real throwaway git repo. The load-bearing one builds the
  `[code, data]` push shape and asserts BUILD — it also asserts that the
  naive `HEAD^` rule *would* have wrongly said data-only, so the test
  fails if anyone reintroduces it.
- **Mechanism confirmed by observation, not inference.** The build log
  for d89633b shows Vercel running `bash scripts/vercel-ignore-build.sh`
  and printing the script's own output plus a correct file list — which
  proves in one block that vercel.json's `ignoreCommand` is read (no
  dashboard override), bash runs it with no CRLF failure, and the parent
  commit IS present in Vercel's clone. A clone too shallow to see
  history was the one plausible silent killer and it is ruled out.
- **Sidebar — why the hours were so large:** builds run on a 30-core
  Turbo machine, so ~1.5 minutes of wall clock bills as ~45 CPU-minutes.
  Machine size is a second, independent lever if ever needed; it matters
  much less now that builds are rare.

### A-024: Stage A is finished — and the "leash bias" was a selected subset
- **Filed/Resolved:** 2026-08-07
- **How it started:** the dashboard's live-model panel showed
  `mean_bf_error = -1.32` and this was reported to the operator as a real
  defect. **It was not.** That panel scores only the 48 non-reconstructed
  rows. `model_log.csv` holds 74 graded starts, and the 26 excluded ones
  ran +1.69 the other way. All 74 pooled: **-0.27 BF, SE 0.48**.
  A subset was compared against nothing and called a bias.
- **Measured properly** — 11,042 out-of-sample starts, all three temporal
  directions: bias **-0.008 BF**, 95% CI [-0.11, +0.09]. Per split
  +0.005 / +0.031 / -0.085. Zero is inside every interval. On the
  narrower population the pipeline actually prices: -0.10 to +0.19.
- **How unusual was the 48?** Block bootstrap over whole slates:
  P(|bias| >= 1.32) at n=48 is 3.3%; empirically 5.3% of real consecutive
  two-day pairs across three seasons hit it. A 1-in-19 stretch found by
  looking at the two most recent days.
- **Accuracy is at ceiling:** MAE 2.82 BF against a perfect-model floor of
  2.66 = **94%**, 0.16 BF of headroom. There is nothing left to win.
- **Four candidates, zero survivors** (full three-way gauntlet):
  1. Aligning `prior_bf_mean` onto the serving definition won on the wide
     backtest in all three directions — then REVERSED on the population
     that becomes bets (the pipeline already refuses relief-worked
     pitchers, which is exactly the group the two definitions disagree
     about). Worse BF accuracy in all three directions there; 97.3%
     likely worse on the money metric. The salvage failed Gate 2.
  2. Pitch-limit divisor — real, but see A-024a below.
  3. Alpha-at-bound warning — no effect on any number a bettor sees.
  4. Replacing the BF distribution family (Poisson / binomial /
     hook-mixture / empirical) — neutral or worse in all three directions.
- **Why the distribution family does not matter, despite being wrong.**
  Real BF is left-skewed and UNDER-dispersed (Var/mu ~ 0.44-0.61); the
  negative binomial is always right-skewed and over-dispersed, so `alpha`
  sits pinned at the optimizer's floor (exp(-5)) in both shipped pickles
  and the likelihood wants to keep going. The model's tails are 2-4x too
  fat (P(BF>=29) 10.5% modelled vs 2.5% actual). **But P(K >= line) is
  near-linear in BF**, so only E[BF] survives the compound integral and
  the shape cancels. Sweeping dispersion across a 4x range moves the 4.5
  and 5.5 lines by under half a batter's worth.
- **Where the real error actually is:** across 18,798 backtest rows the
  model says OVER hits 29.69% and it hits 30.88% — **under-calling OVER
  by 1.19pp** (1.9pp on the 2025 test), and the gap persists when BF is
  exactly right. So it lives in Stage B, the TTO decay, or the
  calibrator. This is also why every leash-shortening idea lost: cutting
  BF pushes OVER down, and OVER was already too low.
- **Do not chase BF error.** Re-open only if 150 graded starts come in
  near +1.3 (a 1-in-1000 event; noise at n=150 spans only -0.66 to +0.74).

### A-024a: the pitch-limit cap has never once executed, and was wrong
- **Filed/Resolved:** 2026-08-07
- **Description:** `predict_bf_distribution()` capped a limited starter at
  `pitch_limit / 4.0`. The 4.0 was chosen by eye. The path has **never
  run in production** — `data/manual_pitch_limits.csv` contains only a
  header row, `backtest.py:182` hardcodes `c11_pitch_limit=None`, and 0
  of 98 priced pitchers across every slate carry a limit. An unexecuted
  path is exactly where a wrong constant survives.
- **Measured, not estimated:** replayed pitches in order on 3,283 real
  2026 starts and counted batters actually faced at the Nth pitch — the
  question a limit really asks. 60 -> 15.83 BF (3.791), 75 -> 19.68
  (3.812), 90 -> 23.16 (3.885), 100 -> 25.04 (3.993). 4.0 is right for a
  ~100-pitch outing, which is not a limit. Across the 60-90 band where
  real limits land it understated BF by 0.7-0.9, worth ~2 points of
  P(over) — always suppressing OVER, on exactly the pitchers an operator
  flags as shortened.
- **Note on provenance:** a subagent proposed 3.58-3.83 and separately
  3.88; both disagreed with direct measurement, so neither was taken. The
  constant comes from the replay above, run here.
- **Resolution:** named constant `PITCHES_PER_BF_UNDER_LIMIT = 3.8` with
  the measurement table in the source. One constant covers 60-90 to
  within 0.09 BF, far inside the 2.71 BF noise floor, so a
  limit-dependent curve would be false precision.
- **Provably a no-op today** — the path cannot fire until someone enters
  a limit. This removes a landmine rather than changing live behaviour.
- **Locked with tests:** `tests/test_stage_a_pitch_limit.py` — 4 cases
  pinning the divisor inside the measured band, asserting it is not 4.0,
  that the cap binds where the data says, that no limit means no cap, and
  that a limit can only ever shorten a start.

### A-025: Railway became the clock and stopped being the publisher
- **Filed/Resolved:** 2026-08-07 (found while verifying an unrelated change)
- **Severity: the site hid a live pick.** At 16:47 ET the lineup lock
  produced a LEAN — Payton Tolle UNDER 6.5 at +110, 2.0u, confirmed
  lineup, two hours to first pitch. The dashboard showed the 09:51
  morning board instead, with 24 projected-lineup pitchers and no play.
- **Root cause** — `tools/railway_worker.py` resident loop:
  ```python
  if not dispatch_github(name):
      TASKS[name]()      # only the FALLBACK rebuilds data.json
  state[key] = today     # dispatch succeeded -> done, result never pulled
  ```
  Railway dispatches the real work to GitHub Actions (it can reach
  DraftKings; the container cannot), marks the task done, and moves on.
  It never pulls the result back, so it kept serving the board from its
  own last LOCAL run. `data-context.tsx` prefers the worker's
  `/data.json` **unconditionally whenever it answers** — no freshness
  comparison — so the worker's stale copy IS the site.
- **Why it appeared only now:** the fallback path (dispatch fails -> run
  locally) does rebuild data.json. The bug became reachable the day the
  GITHUB_TOKEN was added and dispatch began succeeding every time —
  i.e. the day the architecture started working as designed.
- **Same class as this morning's "yesterday's results are missing."**
  That was diagnosed as a stale Statcast cache and fixed there; the
  publisher half of the split was never addressed.
- **Second, compounding mechanism:** `sync_repo()` pulls with
  `--rebase --autostash`. A local `dashboard_data.py` run leaves the
  tracked `dashboard/public/data.json` modified, so autostash stashes
  the stale copy, pulls the fresh one, then re-applies the stash **on
  top** — the stale file wins every pull. Reproduced locally: running
  the rebuild leaves `M dashboard/public/data.json`.
- **Resolution:** `publish_pass()` — drop the derived file, `sync_repo()`,
  rebuild data.json — runs every `PUBLISH_EVERY_SECONDS = 300` at the top
  of the resident loop, before the schedule is even consulted. A rebuild
  measures 0.73s, so the pass is effectively free.
- **Now observable:** `/health` reports `last_publish` (time, ok, error,
  and the `generated_at` actually being served). Previously it reported
  only that data.json *existed* — never how old it was, which is why
  seven hours of staleness passed silently.
- **Invariant added:** `check_served_board_is_current()` in
  `tools/watchdog.py` compares the worker's served `generated_at` against
  the repo's. FAIL above 45 min, WARN above 10, and an unreachable worker
  is a WARN because the dashboard legitimately falls back. Verified
  against the live condition: **FAIL, "worker serving a board 416 min
  older than the repo's."** Every other check in the file inspects the
  repo, so all thirteen were green while the operator looked at a
  seven-hour-old board.
- **The first version of the new check was fooled within the hour.** It
  compared the payload's top-level `generated_at`, which only records
  when data.json was last WRITTEN. The worker rebuilds that wrapper from
  its own volume on every boot, so a restart with no pull emitted
  "21:25" around a slate generated at 13:40 (24 pitchers, 0 bets) while
  the repo held 20:47 (25 pitchers, 1 bet) -- and the check reported the
  worker as NEWER than the repo and passed GREEN. A false all-clear on
  the exact fault it was written for. Now compares the SLATE's own
  stamp, which is content-derived and cannot be forged by a rebuild,
  and also reports pitcher/bet counts so a same-age-different-content
  split is a FAIL rather than silence.
- **The build-skip (A-023) was NOT the cause** and was checked before
  being blamed: `loadData()` only reads the bundled copy when the worker
  is unreachable, so the bundle's freshness never mattered here. The site
  would have shown the same stale board with or without it.

### A-026: the book-line marker vanished because `line` is a string
- **Filed/Resolved:** 2026-08-07 (operator noticed it missing from the card)
- **Description:** `kdist-chart.tsx` computed
  `lineX = PAD_L + (line + 0.5) * slot`. The slate JSON stores `line` as
  the STRING "6.5", so this is `"6.50.5" * slot` -> **NaN**. The render
  guard `lineX < W - PAD_R` is false for NaN, so the amber dashed line
  and its label were skipped **silently** -- no error, no warning, the
  marker simply was not there.
- **Why it survived:** every other consumer of `line` only PRINTS it, and
  "6.5" prints fine. Only the one site doing arithmetic broke.
- **Resolution:** coerce with `Number()`, gate on `Number.isFinite`.
  Fixed in the component, not in Python: the ledger's `line` is
  legitimately a string for ladder rungs ("6+", per `_MERGE_KEYS`), so
  re-typing the emitted value would reach well past this bug.
- **Taken further,** since the operator also could not tell which way the
  model leaned: the winning half of the distribution is tinted in the
  side's colour with a wash band, the losing half stays grey, and a caret
  marks the projection. The caret is a different SHAPE from the line on
  purpose -- they usually sit within a batter of each other.
- **Palette rule honoured:** the band is labelled in words, so hue never
  carries meaning alone.
- **Number discipline:** the band reads "62% OF CURVE" because that is
  the area of the RAW model curve, while the card headline is the
  market-blended 52.9%. Two unlabelled "chance this wins" figures on one
  card is how a board stops being trusted.

### A-027: Railway would rebuild on every ledger commit
- **Filed/Resolved:** 2026-08-07
- **Description:** connecting the repo (A-025) means Railway builds on
  every push, including the 10-18 daily ledger commits. Each rebuild
  restarts the container and interrupts the live starter watcher.
- **Resolution:** `railway.json` -- config-as-code overrides the
  dashboard and must sit at the repo root regardless of Root Directory:
  `["**", "!/data/**", "/data/*.py", "!/dashboard/**"]`
- **Two traps, both settled from Railway's docs rather than guessed:**
  1. The leading `**` is load-bearing. Railway: "negations will only work
     if you include files in a preceding rule." A bare `!/data/**`
     matches nothing and silently does nothing -- a config that looks
     applied and is not.
  2. `/data/*.py` re-includes `backfill_statcast.py`, `game_context.py`
     and `id_crosswalk.py`, worker CODE living in the same directory as
     the ledger. Excluding `data/` wholesale would have stopped real code
     changes from ever deploying.

### A-028: the worker could grade a bet but never book it
- **Filed/Resolved:** 2026-08-07
- **Description:** Railway graded Payton Tolle LOSS / 14 K / -2.0u the
  moment he was pulled and reconciled 10 of 10 picks at 22:57. Git's
  ledger row was still blank, so `tools/pl_calc.py` -- which reads the
  repo and is the ONLY sanctioned source of a P&L figure -- reported the
  pre-game total. Early grading (A-020/A-021) existed and could not reach
  the books.
- **TWO independent breaks; fixing either alone changes nothing:**
  1. `_merge_csv` unions repo -> volume and never writes back, so
     anything this container produces stays on the volume. The live
     watcher grades to the volume; the checkout never hears about it.
  2. `commit_and_push()` is only called from the four task functions,
     and those only run when `dispatch_github()` FAILS. Since the token
     was added, dispatch always succeeds -- so it never ran at all.
- **Same root cause as A-025, opposite direction.** A-025 was the pull
  half: work done on GitHub not reaching the container. This is the push
  half: work done in the container not reaching git. The dispatch split
  broke the loop at both ends and only one end was fixed.
- **Resolution:** `mirror_volume_to_repo()` copies the volume's ledger
  into the checkout, then `publish_pass()` commits and pushes. Ordering
  is load-bearing: reconcile unions the freshly pulled repo rows into the
  volume FIRST, so by the time we copy back the volume is a superset and
  the copy can only add. `commit_and_push` no-ops when nothing changed,
  so running it every 5 minutes is free.
- **model_log.csv added to the staged set** -- the evidence table the
  /model page and shadow portfolio are scored from, produced on the
  volume like everything else.
- **Locked with tests:** `tests/test_volume_mirror.py` -- mirrors a
  volume-only grade, carries slates/odds, no-ops on CI where the checkout
  IS the ledger, and survives a missing volume (the publish pass runs
  every 5 minutes and is not worth an outage).

### A-029: the container was never a git repository
- **Filed/Resolved:** 2026-08-08 (8 consecutive CI failures; operator
  asked why)
- **Severity: the site showed nothing for today's slate, all day.** The
  worker served slates for 08-04 through 08-07 while origin/master held
  `data/slates/2026-08-08.json` with 28 priced pitchers. Because
  `dashboard/lib/data-context.tsx` PREFERS the worker's `/data.json`
  whenever it answers, the worker's frozen copy was the site.
- **Root cause, one line:** `.dockerignore` excluded `.git/`. `COPY . .`
  therefore produced an `/app` that is not a git repository, and every
  git command in the container failed with **exit 128, "not a git
  repository"** -- pull, push, checkout, diff, commit, all of it.
- **A-025 and A-028 were both written against a container that could
  never run either fix.** A-025 (2026-08-07) added the pull half; A-028
  (2026-08-07) added the push half. `.dockerignore` excluded `.git/` on
  2026-08-05, before both. Neither has ever executed successfully. The
  worker has been dispatching to CI and discarding every result since.
- **Why it survived 16 hours:** two mechanisms reported success they had
  not verified.
  1. `configure_git()` ran four `subprocess.run(..., capture_output=True)`
     calls and checked **none** of their return codes, then logged
     `git remote configured for joey11600/MLB-Strikeouts` unconditionally.
     That line is in the 23:06 EDT boot log, immediately after four
     exit-128 failures. A success line that cannot fail is worse than no
     line: it is the first thing you check, and it lies.
  2. `/health.can_push_to_git` was `bool(os.environ.get("GITHUB_TOKEN"))`
     -- it answered "is an env var set?", a question nothing depends on.
     It read `true` throughout.
- **CI was not broken; CI was the only thing that noticed.** The
  `served board is current` watchdog check (added by A-025, hardened by
  A-026's slate-stamp fix) fired correctly on the first run after CI
  published a board the worker lacked, and every run since. The eight
  red runs are the alarm working.
- **CORRECTION — the first fix was wrong about the mechanism.** Removing
  the `.dockerignore` exclusion was deployed as `5309d93`, the deploy
  succeeded, and the container came up still reporting
  `git.is_repo: false`. The exclusion was a real bug and not the
  operative one: **Railway's builder ships a source ARCHIVE, not a
  clone.** Its build log reads `fetching snapshot` -> `unpacking
  archive`, so there is no `.git` in the build context to copy no matter
  what `.dockerignore` says. `/app` has never been a repository and
  never would have been.
  - The new detection is what caught this, within one deploy, instead of
    another 16 hours. That is the whole argument for checking the
    capability: the fix failed and said so immediately.
- **Resolution:**
  1. `_bootstrap_repo()` -- the operative fix. When `/app` is not a
     repository, the worker builds one: `git init` / `remote add` /
     `fetch` / `reset --hard origin/master`, then re-probes.
     `reset --hard` is safe here for two specific reasons, both checked:
     no symlinks are involved (`seed_volume_state` copies image->volume
     precisely so the ledger is a real file on `/data/state`), and
     nothing tracked by git is excluded from the image (every
     `.dockerignore` entry covers gitignored paths only), so the reset
     has no phantom deletions to apply. It resets to **origin/master**
     rather than the build commit because CI commits every few minutes
     and `_merge_dir` reads FILES, not git objects -- a current HEAD over
     a stale working tree would merge yesterday's board into the volume
     and look healthy doing it.
  2. `configure_git()` moved to the TOP of `main()`, before
     `seed_volume_state()` and `reconcile_ledger()`. Both read files out
     of the checkout, so bootstrapping after them would publish a
     boot-time `data.json` that is already behind.
  3. `.git/` removed from `.dockerignore` and kept off: it costs 9.3 MB,
     makes a locally built image behave like the deployed one, and
     re-adding it would break any builder that does provide `.git`.
  2. `configure_git()` probes `git rev-parse --git-dir` first and logs
     FATAL with the remedy if it fails; checks every subsequent exit
     code; handles a shallow builder clone via `fetch --unshallow`, since
     `pull --rebase` against a shallow clone can refuse outright.
  3. `can_push_to_git` now reports the CAPABILITY (repo present AND
     authenticated remote set), and a new `git` block on `/health`
     carries `is_repo` / `shallow` / `remote` / `error`.
- **Watch on next deploy:** the boot log must show `git remote configured`
  only after a successful probe, `/health.git.is_repo` must be `true`,
  and the served board must reach today's date within one 5-minute
  publish pass. **Confirmed 2026-08-08 20:16Z:** `git.is_repo: true`,
  `git.bootstrapped: true`, board within 0 min of the repo, CI green.
- **Aftermath — the check that caught this was itself broken.** At 20:45Z
  `served board is current` failed again, this time a FALSE POSITIVE. It
  measured `lag` as the age gap between two BOARD VERSIONS: the board had
  held at 13:49:41Z for seven hours, CI regenerated it at 20:46:20Z and
  ran the check **9 seconds later**, so lag read 417 min against a 45-min
  threshold while the worker was nine seconds behind and current on the
  next pass. Structurally, that fires every time a board is regenerated
  after a quiet stretch.
- **Correction to this entry's own reasoning:** `lag > 45` did NOT catch
  the outage. Ten of the eleven failures that day came from `if not got:`
  -- *serving no slate at all* -- and the eleventh was the false positive
  above. `lag > 45` has fired exactly once in its history and that firing
  was wrong, so relaxing it costs nothing measured.
- **Fix, after an adversarial review returned DO NOT SHIP on the first
  attempt (four lenses, 26 findings, all reproduced):**
  1. Judge by **version identity**, not minutes. `_previous_published_board()`
     reads the prior published board from git history; grace applies only
     to a worker exactly ONE version behind. The first attempt bounded the
     window in minutes but left `lag` unbounded -- a **3.5-day-stale**
     board reported `ok`, as did a 26-hour-stale board hiding a bet, which
     is A-025's exact harm.
  2. Gate grace on `/health.last_pull`, **never** `last_publish`.
     `publish_pass` wraps the pass in try/except and `_run` returns False
     rather than raising, so a failed pull leaves `ok=True` -- which is
     why /health advertised `last_publish: {ok: true}` for 16 hours here.
     Grace granted on that basis would have silenced the check on this
     very outage.
  3. Fetch `/health` in its OWN try, failing closed to `{}`. Sharing the
     block with `/data.json` turned a live stale-board outage into a green
     `warn` ("worker unreachable"), false in both clauses, exit 0.
  4. Bound both clocks (`0 <= available`, `-2 <= pull_age`); a repo stamp
     90 min in the future held grace open for 102 min.
  5. `except (ValueError, TypeError)` -- the new `now - want_dt` line mixes
     awareness, and an uncaught TypeError is downgraded to a warn, i.e. it
     silently disables the one check that must never go quiet.
  6. Shape mismatches stay FAIL at any lag.
  - **Shipped in two commits on purpose.** `last_pull` had to be deployed
    and confirmed on /health before the watchdog could consume it; merged
    together, the check fails closed against every not-yet-redeployed
    worker. A successful bootstrap also records a pull, or LAST_PULL sits
    `{ok: None}` for up to five minutes after every deploy and reds a CI
    run each time.
  - **Locked with tests:** `tests/test_watchdog_served_board.py` -- the
    real false positive is ok; no-slate still fails; a stale board with
    MATCHING pitcher counts still fails (so version identity, not the
    shape guard, is doing the work -- mutation-checked); shape mismatch in
    the window fails; unreachable /health does not downgrade; missing or
    failed `last_pull` gets no grace; negative clocks get no grace.

### A-030: the model log rebuilt dates it could not re-derive, and deleted the difference
- **Filed/Resolved:** 2026-08-08 (a red regression test on master; found
  while clearing it)
- **Description:** `tools/model_log.py::log_dates()` did
  `existing = [r for r in csv.DictReader(f) if r["date"] not in dates]`,
  where `dates` is **every date with a slate file**. So every stored row
  for those dates was dropped, then rows were regenerated only for
  pitchers `_actuals_for()` could derive from the Statcast cache *at that
  moment*. Those are not the same set. A date whose pitches are not in the
  cache regenerates ZERO rows — and the delete stood.
- **Measured, against the real 99-row log:** one run on a machine whose
  cache stopped at 2026-08-06 destroyed **all 25 graded rows for
  2026-08-07** — real `actual_k` / `actual_bf` outcomes, unrecoverable.
  The run logged `2026-08-07: logged 0 pitchers` and reported success.
- **Direct violation of CLAUDE.md's "Never delete rows"**, on the evidence
  table `/model`, the live-calibration block and the shadow portfolio are
  all scored from. A silent truncation there corrupts published
  model-quality figures, not just a cache.
- **Never fired in production.** `git log` over `data/model_log.csv` shows
  26 -> 54 -> 74 -> 99 rows across 25 commits, monotonically increasing.
  CI restores and tops up the Statcast cache before the pipeline runs, so
  the derivation always had its data. Latent, not realised — but an
  incomplete cache is an ordinary transient state (refresh lag, partial
  restore, fresh checkout) and this runs on every close task, so it was
  one unlucky ordering away at all times. The watchdog's "Statcast cache
  fresh" check would have reported the cause, but only after the write.
- **Second time in this area.** `Fix nightly evidence loss: 03:00 runs...`
  (2026-08-07, 54 -> 74 rows) was the same family: a rebuild that assumed
  what it could derive equalled what it had stored.
- **Resolution:** union by `(date, game_pk, pitcher_id)`. A freshly
  derived row supersedes the stored one — that is what lets the backfill
  correct a row — but a stored row is never dropped merely because this
  run could not re-derive it. Same union-only, never-downgrade rule the
  ledger reconcile follows (KB invariant 9). A shrink guard raises
  **before** `_write_atomic`, not after, or it would document the loss
  instead of preventing it.
- **Locked with tests:** `tests/test_evidence_pipeline.py::test_model_log_never_drops_a_date_it_cannot_rederive`
  starves `_actuals_for` of the newest date and asserts no row disappears.
  Mutation-checked: restoring the old filter line fails both it and
  `test_model_log_backfills_a_missing_date`.

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
