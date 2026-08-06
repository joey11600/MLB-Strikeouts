# Changelog

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
  to the volume so restarts resume mid-day, git pull before / push
  after each job, optional Vercel deploy hook.
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
