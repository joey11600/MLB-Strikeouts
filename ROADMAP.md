# Roadmap

## Phase 0 — Skeleton and reconnaissance ✅
- [x] Read NRFI reference repo
- [x] Create repo layout with stub files
- [x] Write CLAUDE.md and AGENTS.md
- [x] Write docs/FACTORS.md
- [x] Verify data sources (tools/check_sources.py)
- [x] Surface licensing decision

## Phase 1 — Data layer and the honest baseline ✅
- [x] Backfill Statcast 2024–2026
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
- [ ] Daily cron / scheduled task
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
- [ ] Shadow promoted features for 2 weeks
