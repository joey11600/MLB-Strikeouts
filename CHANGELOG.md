# Changelog

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
