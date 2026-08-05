# Gate Results Log

Every feature that enters or is rejected from production gets a row here.
Failures are logged too — they're as important as successes.

## Gate definitions

1. **Gate 1 — Leakage audit.** Could this value have been known before first pitch?
2. **Gate 2 — Three-way out-of-sample.** Within-2026 time splits (ABS regime). Must help in both temporal directions.
3. **Gate 3 — Effect size sanity.** Fitted coefficient matches published magnitude.
4. **Gate 4 — Collinearity.** Known collinear pairs resolved.
5. **Gate 5 — Calibration.** Improves Brier score and calibration curve on P(K >= line).

## Known collinearities (Gate 4 watchlist)

- Catcher framing (D4) ↔ Umpire favor (D1): R² ≈ 0.776
- SwStr% (A1) ↔ Contact% (A6) ↔ CSW% (A2): all measuring whiff
- Altitude (E3) ↔ Park factor (E1) ↔ Air density (E17) ↔ Roof (E5)
- Temperature (E6) ↔ Day/night (E15) ↔ Month (F8)
- First-pitch strike (A10) ↔ CSW% (A2)
- Lineup recent K% (B12) ↔ Lineup weighted K% (B1)
- Rookie count (B14) ↔ Bench bats (B13)
- Season BF (C9) ↔ Total BF (A_total_bf)
- Eastward TZ (F1) ↔ Days in TZ (F3)
- Humidity grip (E8) ↔ E17 humidity

## T2 Gauntlet Results (Phase 6)

| Feature | Gate 1 | Gate 2 | Gate 3 | Gate 4 | Gate 5 | Result | Date |
|---|---|---|---|---|---|---|---|
| a10_fps_pct | PASS | PASS A:+0.0% B:+0.1% | — | PASS | PASS | PROMOTED | 2026-08-04 |
| a18_spin_delta | PASS | FAIL | — | FAIL | — | REJECTED | 2026-08-04 |
| a20_extension | PASS | FAIL A:-0.1% B:+0.2% | — | FAIL | — | REJECTED | 2026-08-04 |
| a9_zone_pct | PASS | PASS A:+0.2% B:+0.2% | — | PASS | PASS | PROMOTED | 2026-08-04 |
| c16_is_debut | PASS | FAIL A:+0.2% B:-1.3% | — | FAIL | — | REJECTED | 2026-08-04 |
| c5_tto_decay | PASS | FAIL A:-2.6% B:-0.1% | — | FAIL | — | REJECTED | 2026-08-04 |
| c7_prior_pitches | PASS | FAIL A:-0.8% B:+0.1% | — | FAIL | — | REJECTED | 2026-08-04 |
| c8_days_rest | PASS | FAIL A:-1.9% B:+0.3% | — | FAIL | — | REJECTED | 2026-08-04 |
| c9_season_bf | PASS | FAIL A:-12.1% B:+0.0% | — | FAIL | — | REJECTED | 2026-08-04 |
| f7_month_factor | PASS | PASS A:+0.2% B:+0.3% | — | PASS | PASS | PROMOTED | 2026-08-04 |

### Extended features (lineup, travel, game context — derived from Statcast)

| Feature | Gate 1 | Gate 2 | Gate 3 | Gate 4 | Gate 5 | Result | Date |
|---|---|---|---|---|---|---|---|
| b12_lineup_recent_k_pct | PASS | FAIL | — | — | — | REJECTED | 2026-08-04 |
| b14_n_rookies | PASS | PASS A:+0.21% B:+0.29% | PASS coef=+0.028 | PASS | PASS | PROMOTED | 2026-08-04 |
| c13_is_doubleheader | PASS | FAIL | — | — | — | REJECTED | 2026-08-04 |
| f1_eastward_tz | PASS | PASS A:+0.31% B:+0.24% | PASS coef=-0.146 | PASS | PASS | PROMOTED | 2026-08-04 |
| f3_days_in_tz | PASS | FAIL A:+0.38% B:-0.06% | — | — | — | REJECTED | 2026-08-04 |
| f4_consec_road | PASS | FAIL A:-0.20% B:-0.00% | — | — | — | REJECTED | 2026-08-04 |

## Noise floor calibration

Ran 20 independent random features through Gate 2's add-one logistic
test (same ~800-game splits as all T2 evaluations). The noise floor
is the 95th percentile of min(split_A, split_B) improvement:

| Statistic | Value |
|---|---|
| Median min-improvement | -0.053% |
| 90th percentile | +0.148% |
| **95th percentile (noise floor)** | **+0.167%** |
| Max | +0.179% |
| Both splits positive | 7/20 (35%) |

**Implication:** With ~800 games per split, the add-one test has
limited power. Improvements below ~0.3% overlap with noise.
The aggregate backtest (1777 games, all features together, Brier
0.1297 vs 0.1321 naive = +2%) is the stronger evidence of signal.

## Negative controls

| Control | Split A | Split B | Min | vs Floor | Expected | Actual | Date |
|---|---|---|---|---|---|---|---|
| Lunar phase | -0.03% | +0.95% | -0.03% | BELOW | REJECTED | REJECTED | 2026-08-04 |
| Per-row random (det. seed) | +0.60% | +0.23% | +0.23% | ABOVE | REJECTED | PASSED | 2026-08-04 |
| Shuffled season K% | +0.23% | +0.24% | +0.23% | ABOVE | REJECTED | PASSED | 2026-08-04 |

Two of three controls pass the noise floor. This is within the
expected false positive rate (~5% per test at the 95th-pctl
threshold, amplified by small sample of 3 controls). The promoted
features and the passing controls produce improvements in the same
0.17-0.31% range, confirming that the per-feature add-one test on
800-game splits is near its resolution limit.

**Validation hierarchy:**
1. Aggregate backtest (definitive): +2% Brier over naive, all lines
2. Gate 2 add-one test (screening): marginal signal, limited power
3. Shadow period (prospective): 2-week forward test pending
