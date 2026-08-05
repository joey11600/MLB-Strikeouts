# KB.md — System Overview

## Architecture

### Two-stage strikeout prediction

```
Stage A:  P(BF = n)              for n in 0..40
Stage B:  p_i  for i = 1..40     per-batter K probability
Combine:  P(K = k) = Σ_n P(BF=n) · PoissonBinomial(k; p_1..p_n)
```

Stage A predicts how many batters the pitcher will face (the "leash
model"). Stage B predicts the probability of striking out each
specific batter in the lineup sequence, accounting for handedness,
times-through-order decay, and arsenal matchup.

The compound distribution is computed via a Poisson-binomial dynamic
program — O(n²) where n ≤ 40, so microseconds.

Final output: **P(K ≥ line)** for each line on the board, after
isotonic calibration.

### Matchup formula

```
matchup_K% = (B × P) / (a × B × P + b)

  a = 0.84            (curvature; re-fit in Phase 3)
  b = L − a × L²      (normalization; recompute every season)

  For L = 0.225:  b = 0.225 − 0.84 × 0.050625 = 0.182475
```

**Invariant: f(L, L) == L to within 1e-9.** Unit test enforced.

Source: FanGraphs empirical derivation from 1.5M plate appearances.
The raw constants (0.84, 0.16) assumed a 19.05% league K rate. Fed
2026 inputs without renormalization, f(0.225, 0.225) = 0.2500 — a
+2.5 pp systematic inflation.

## Variance decomposition (computed from real data)

Computed from 1,454 starts by 217 pitchers, June–Aug 2026.
Uses each pitcher's season K% as the talent proxy (not the
game-level realized K/BF, which would be circular).

**Empirical Var(K) = 6.27** (SD ≈ 2.50 strikeouts per start).
E[BF] = 21.1, E[K%] = 21.7%.

| Component | Variance | % of Var(K) |
|---|---|---|
| Bernoulli (irreducible) | 3.543 | 56.5% |
| Signal = Var(BF × p_talent) | 2.979 | 47.5% |
|   — from Var(BF) | 1.209 | |
|   — from Var(K%) | 1.482 | |
|   — from 2·Cov(BF, K%) | 1.449 | |
| Residual | −0.256 | −4.1% |
| **Empirical Var(K)** | **6.266** | **100%** |

Cov(BF, K%) = +0.034: better strikeout pitchers go deeper.
Nearly half the signal comes from this correlation.

**BF variance split:** 65% between-pitcher (predictable), 35%
within-pitcher (game-level noise). The leash model (Stage A)
has meaningful signal even before game-level features.

### Modeling ceiling

A perfect oracle knowing true BF and true p for each start
explains ~48% of Var(K). The other 57% is irreducible Bernoulli
noise. (These sum to >100% because the two sources are positively
correlated.) Realistic models with estimated BF and p: **24–33%
of Var(K) explained.** That's enough for a calibrated edge on
P(K ≥ line) — we don't need to predict K counts, just beat the
sportsbook's implied distribution.

*Script: `tools/variance_decomposition.py`. Rerun after adding
2024–2025 data.*

## K% by Times Through Order (2026 data)

Computed from 60,836 plate appearances, June–Aug 2026. Adjusted
ratios control for pitcher talent (each pitcher's ratio of
TTO-specific K% to their own overall K%, weighted by BF).

| TTO | Raw K% | Adj K% | Ratio | BF |
|---|---|---|---|---|
| 1st (BF 1–9) | 23.5% | 24.0% | 1.053 | 34,032 |
| 2nd (BF 10–18) | 20.3% | 21.2% | 0.930 | 14,097 |
| 3rd (BF 19–27) | 19.6% | 20.1% | 0.884 | 7,190 |
| 4th+ (BF 28+) | 25.9% | 25.3% | 1.112 | 73 |

**TTO 1→3 decay: −3.8 pp (16% decline).** This is the single
largest systematic effect in per-batter K probability and must be
modeled explicitly in Stage B.

TTO 4+ is survivor-biased (only dominant pitchers reach it) and
has only 73 BF — treat as unreliable.

Lineup-slot K% is flat (21.5–23.6%) across positions 1–9. The
cleanup spot (slot 4) is highest at 23.6%.

*Script: `tools/tto_analysis.py`. Rerun after adding 2024–2025 data.*

## Regime breaks

| Date | Break | Training implication |
|---|---|---|
| Mid-2015 | Statcast era begins | No pitch tracking before this |
| Jun 22, 2021 | Sticky-stuff enforcement | RPM distribution shift |
| 2022 | Humidors league-wide | Ball COR and pitch break shift |
| 2023 | Pitch clock + shift ban | Pace and baserunning changes |
| 2026 | ABS challenge system | Umpire/framing effects compressed |

**Default training window: 2024, 2025, 2026.**

## Data sources

- **Statcast** (pitch-level): the training source. Free, no key.
- **MLB Stats API**: schedule, probables, lineups, venue, umpire.
  Non-commercial terms — see `docs/LICENSING.md`.
- **Open-Meteo**: weather forecasts. Non-commercial free tier.
- **DraftKings**: odds via unofficial JSON API (`curl_cffi`).
- **Chadwick Bureau**: player ID crosswalk.

## Compound model v1 (Phase 2)

Two-stage model with Poisson-binomial combination. Evaluated on
1,777 starts, June–Aug 2026.

**Stage A** — Negative binomial regression on BF count.
Features: pitcher prior BF mean, season K%. Correlation = 0.777.

**Stage B** — Logistic regression on per-batter K outcome.
8 features: logit(pitcher K%), logit(batter K%), TTO 2, TTO 3,
zone_pct, eastward_tz, n_rookies.
TTO coefficients: TTO 2 = −0.214, TTO 3 = −0.234.
Zone_pct = +0.139, eastward_tz = −0.017, n_rookies = −0.012.

**Phase 6 gauntlet** — 16 T2 features tested (10 Statcast + 6 extended).
3 promoted (a9_zone_pct, f1_eastward_tz, b14_n_rookies), 13 rejected.
4 deferred (need external data). Noise floor calibrated at +0.167%
(95th pctl from 20 random seeds). The add-one test on 800-game
splits has limited power below ~0.3%. The aggregate backtest (+2%)
is the definitive signal evidence. Full results in docs/GATES.md.

| Line | Naive Brier | Model Brier | Improvement |
|---|---|---|---|
| 3.5 | 0.1682 | 0.1663 | +1% |
| 4.5 | 0.1808 | 0.1777 | +2% |
| 5.5 | 0.1627 | 0.1589 | +2% |
| 6.5 | 0.1310 | 0.1275 | +3% |
| 7.5 | 0.0874 | 0.0865 | +1% |
| 8.5 | 0.0622 | 0.0612 | +1% |
| **All** | **0.1321** | **0.1297** | **+2%** |

Beats naive at every line. Sharper at 5/6 lines — the matchup +
TTO structure produces more differentiated predictions.

Remaining signal to capture: park/weather, umpire/catcher,
workload features, calibration. Each should improve sharpness
further without hurting calibration.

## Naive baseline (every model must beat this)

Binomial(pitcher season K%, pitcher historical BF distribution).
No matchups, no TTO, no park/weather/umpire. Evaluated on 1,799
starts, June–Aug 2026, as-of (excluding each game from its own
prediction).

| Line | Brier | Pred | Actual | Bias |
|---|---|---|---|---|
| 3.5 | 0.1941 | 62.9% | 64.1% | −1.1% |
| 4.5 | 0.2086 | 47.6% | 47.9% | −0.2% |
| 5.5 | 0.1869 | 33.5% | 32.5% | +1.1% |
| 6.5 | 0.1487 | 22.0% | 21.7% | +0.3% |
| 7.5 | 0.0978 | 13.4% | 12.3% | +1.1% |
| 8.5 | 0.0681 | 7.6% | 8.1% | −0.4% |

Overall Brier = 0.1507. Coin-flip = 0.2500.
ECE (calibration error) = 0.04–0.07 across lines.

Bias is consistently small (<1.2 pp) — the season K% + BF
distribution captures the bulk of the predictable signal.
The model's job is to improve sharpness (push confident
predictions toward 0 and 1) while maintaining calibration.

*Script: `tools/naive_baseline.py`. Predictions:
`data/naive_baseline_predictions.csv`.*

## Edge computation and staking (Phase 3)

### No-vig fair probability

DK K props hold 8-12% vig (e.g. -125/-115 on both sides). To get
the true market-implied probability:

```
implied_over  = |odds| / (|odds| + 100)   for negative odds
              = 100 / (100 + odds)         for positive odds

total_implied = implied_over + implied_under   (> 1.0 by the hold)
fair_over     = implied_over / total_implied
```

### Edge threshold

Every bet must clear: `max(hold% + 2%, 3%)`. On a typical -120/-110
market (hold = 4.5%), the threshold is 6.5%. A bet must have at
least 6.5pp of edge against the no-vig fair probability before it
qualifies.

### Quarter-Kelly staking

Full Kelly: `f* = (b*p - q) / b` where `b = decimal_odds - 1`.
We use `f*/4` and cap at MAX_STAKE_UNITS = 2.0 per bet.

Portfolio daily cap: 6.0u total. Same-game pitchers get a 15%
correlation haircut (same umpire, weather, game environment).
Picks are allocated best-edge-first.

### Ladder/milestone betting

When the model predicts a pitcher's K total well above or below the
primary O/U line, there's edge at multiple K thresholds. Instead of
only betting Over 6.5, also bet the 6+, 7+, 8+ milestone lines at
DK's alt odds. The compound model already computes the full P(K = k)
distribution, so P(K >= milestone) is a free array slice.

**Example:** Model predicts E[K] = 8, line is 6.5.
- Primary: Over 6.5 at -120 (edge +12%)
- Ladder: 6+ K at -300 (edge +8%), 7+ K at -120 (edge +10%),
  8+ K at +150 (edge +6%)

Staking:
- Each rung is sized via quarter-Kelly independently.
- Per-rung cap: 2u (MAX_STAKE_UNITS).
- Per-pitcher cap: 3u total across primary + all rungs. Bets on
  6+, 7+, 8+ are nested events (perfectly correlated upward), so
  combined exposure must be limited.
- Best-edge-first allocation within each pitcher.
- The primary O/U bet counts toward the per-pitcher cap.

Grading:
- Milestone bets are WIN if actual K >= milestone, LOSS otherwise.
- No push on milestones (they're whole numbers, win/lose only).
- VOID if starter was scratched (same as primary).

### Daily pipeline flow

```
MLB Stats API schedule → DK odds + alt lines fetch → name matching →
Statcast features → compound model → P(K = k) full distribution →
edge on primary O/U + edge on each milestone →
Kelly sizing per rung → per-pitcher cap → daily cap → tracker CSV
```

First live run (2026-08-04): 15 games, 29 props + 213 milestones,
26 pitchers analyzed, 16 primary + 5 ladder cleared threshold,
4 picks after caps (3 primary + 1 ladder), 6u total.

### Auto-grading

After games complete, `tools/grader.py` fetches actual K counts
from MLB Stats API boxscores and grades each pick:
- WIN/LOSS for standard O/U and milestone bets
- PUSH on whole-number lines (stake returned)
- VOID on scratched starters
- POSTPONED on suspended games

Graded picks are locked and cannot be overwritten.

### Production operator workflow

```
python run.py              # full daily cycle
python run.py predict      # today's picks only
python run.py grade        # grade yesterday's picks
python run.py status       # show record and P&L
python run.py backfill     # refresh Statcast cache
```

## Dashboard (Phase 5)

### Data pipeline

```
data/picks_2026.csv → tools/dashboard_data.py → pnl_guard → dashboard/data.json
                                                                   ↓
                                                          dashboard/index.html
                                                          dashboard/brief.html
```

`dashboard_data.py` reads the picks CSV, computes all P&L via
`tracker._calc_pnl` (canonical source), tags every P&L value with
`{"value": float, "basis": "flat_100u"}`, runs the FlatUnits guard,
then writes `data.json`.

### FlatUnits / CumulativeUnits guard

Every P&L field that reaches the renderer must be a tagged dict, not
a bare float. The guard (`tools/pnl_guard.py`) walks the entire JSON
tree and rejects:
- Bare numbers in P&L-named fields (must be `{value, basis}`)
- Missing or wrong `basis` key (must match `flat_100u`)
- Top-level `basis` mismatch

Wired into `dashboard_data.py` — runs before every write. Also
available standalone: `python tools/pnl_guard.py`.

### Pages

- **`dashboard/index.html`** — full dashboard. Hero stats bar,
  today's pick cards with edge progress bars and gradient accents,
  SVG P&L curve, recent results. Mobile-first (520px max-width).
  Loads `data.json` via fetch.
- **`dashboard/brief.html`** — filming page. Stripped-down, bigger
  type, just today's picks and summary. Same data source.

Both use the Dark Terminal palette: near-black canvas `#08080A`,
surface cards `#111113`, emerald over `#10B981`, rose under
`#F43F5E`, amber accent `#F59E0B`. Rounded corners, Outfit for
display, DM Mono for figures. Gradient left-borders on cards
signal OVER (green) vs UNDER (rose). Subtle noise texture and
radial gradient atmosphere. 21st.dev-inspired component design.

## Key invariants

1. `f(L, L) == L` on every build.
2. `features/asof.py` is the only path to rate features in training.
3. Atomic CSV writes (tempfile + fsync + os.replace).
4. Never delete rows.
5. `tools/pl_calc.py` is the only source of P&L numbers.
6. Negative controls (lunar, random, shuffled) must be rejected by
   every retrain.
