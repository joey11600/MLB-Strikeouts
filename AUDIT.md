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

### A-004: Statcast cache missing 2024, 2025, and Apr-May 2026
- **Filed:** 2026-08-05
- **Status:** Open
- **Description:** ROADMAP Phase 1 claims "Backfill Statcast 2024-2026"
  complete, but `data/statcast_cache/` holds only 2026-06 through
  2026-08. The sanctioned cross-season splits (train 2024→test 2025,
  etc.) cannot run until the full backfill exists. Until then the
  honest backtest uses a within-2026 time split (train ≤ Jul 8, test
  Jul 9-Aug 3). Run `python data/backfill_statcast.py --start
  2024-03-28` (multi-hour download) and re-run the backtest.

### A-005: T2 promotions need re-gauntleting on the honest harness
- **Filed:** 2026-08-05
- **Status:** Open
- **Description:** a9_zone_pct, f1_eastward_tz, b14_n_rookies were
  promoted by a gauntlet whose feature aggregates predate the as-of
  rewrite. Refit on honest features, n_rookies collapsed to +0.009
  (sign flip) and eastward_tz moved to -0.05. Re-run the gauntlet
  against the as-of pipeline; demote features that fail.

### A-006: Only ~600-test-start evidence behind the +2% edge
- **Filed:** 2026-08-05
- **Status:** Open (structural, monitored via CLV)
- **Description:** The honest backtest covers 618 test starts. The +2%
  Brier edge is real but thin evidence for betting. Mitigations now in
  place: 50% market blend, calibration, stricter ladder bar, CLV
  tracking from the next slate. Raise MODEL_TRUST_WEIGHT only after
  100+ graded bets with positive average CLV.

## Resolved

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
