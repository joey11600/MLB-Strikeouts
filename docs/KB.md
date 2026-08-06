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
isotonic calibration (fit on out-of-sample backtest predictions,
`models/calibrator.pkl`, applied in `strikeout_predictor.predict` and
via `calibrate_prob()` for milestone tails). Before 2026-08-05 the
calibrator existed but was never fit or applied — see CHANGELOG
Phase 7.

### Betting probability (market-anchored)

The probability used for edge and staking is NOT the raw model output:

```
p_bet = w * calibrated_model + (1 - w) * market_no_vig_fair
w = MODEL_TRUST_WEIGHT = 0.5          (models/edge.py)
edge = p_bet - fair  = w * (model - fair)
```

One-sided milestone markets are de-vigged with an assumed side margin
(`ALT_SIDE_MARGIN` = 4%) and held to `LADDER_EDGE_THRESHOLD` = 10%.
Raise w only after 100+ graded bets with positive average CLV.

### As-of features and shrinkage

Every rate feature in training and backtesting flows through
`features/asof.py` — either per-game (`load_pitches_before_game`) or
the vectorized tables (`asof_pitcher_game_table`,
`asof_batter_game_table`: sort by date, cumsum-minus-current, so the
predicted game can never contaminate its own features).

K% rates are shrunk toward league average (empirical Bayes):
pitcher 70 BF pseudo-count, batter 60 PA (`shrink_rate`). Without
shrinkage the honest model has NO edge over naive — thin as-of
samples are noise.

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

## Compound model — honest evaluation (Phase 9 cross-season; supersedes all earlier numbers)

**The Phase 2/6 backtest numbers (0.1297 vs 0.1321 on 1,777 starts)
were contaminated by leakage** and are void (CHANGELOG Phase 7). The
Phase 7 within-2026 split (+2% on 618 starts) is superseded by the
full cross-season validation below (Phase 9).

Honest protocol: models fit ONLY on train seasons; every feature
strictly as-of; seasons loaded separately so priors reset at season
boundaries (matching live serving); naive baseline gets the same
as-of inputs.

| Split | Test starts | Naive Brier | Model Brier | Improvement |
|---|---|---|---|---|
| train 2024 → test 2025 | 4,807 | 0.1539 | 0.1480 | +3.8% |
| train 2025 → test 2024 | 4,713 | 0.1572 | 0.1496 | +4.8% |
| train 2024+2025 → test 2026 | 3,133 | 0.1540 | 0.1491 | +3.2% |

Positive in both temporal directions and on the decision split,
positive at every line in every split — 12,653 out-of-sample starts.

**Production models** (refit 2024+2025+2026, 267,257 PA,
`tools/retrain_production.py`):

- **Stage A** — negative binomial BF with leash inputs (Phase 12):
  prior_bf_mean +0.036, season_k_pct ~+0.25, il_return −0.122
  (25+ day layoff → shorter leash), bp_heavy +0.028 (taxed pen →
  starter stretched), alpha 0.0067. Announced pitch limits apply as a
  direct live cap (E[BF] ≤ limit/4), untrained historically.
  Operator enters limits in `data/manual_pitch_limits.csv`.
- **Stage B** — logistic per-batter K, CORE ONLY
  (`PRODUCTION_EXTRA_FEATURES = []`): intercept +1.343,
  logit(pitcher K%) +0.935, logit(batter K%) +1.065, TTO2 −0.142,
  TTO3 −0.211. The Phase 6 T2 promotions (zone_pct, eastward_tz,
  n_rookies) were ALL demoted by the cross-season re-gauntlet
  (`tools/regauntlet.py`, paired drop-one deltas over 12,653 OOS
  starts, none reached t≥2 both directions — AUDIT R-005). New
  features must pass that same bar to enter.

Isotonic calibration refit on the 18,798 decision-split OOS
predictions: mid-line bias −2pp → within ±1pp. Calibration's job is
bias removal (kills phantom edges), not Brier.

Per-game predictions: `data/backtest_predictions.csv`; split metadata:
`data/backtest_meta.json` (feeds the dashboard Model view). Re-run:
`python backtest.py`, then `python tools/fit_calibrator.py`, then
`python tools/retrain_production.py`.

Remaining signal to capture: park/weather, umpire/catcher, workload
features, lineup-lock timing.

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
We use `f*/4`, cap at MAX_STAKE_UNITS = 2.0 per bet, then quantize to
clean denominations ({0.25, 0.5, 1, 1.5, 2}) — published stakes are
always round numbers (operator rule).

Portfolio daily cap: 10.0u total (raised from 6.0 on 2026-08-05 —
the 3.5u ladder trio plus normal primaries regularly exceeded 6u).
Same-game pitchers get a 15% correlation haircut (same umpire,
weather, game environment).
Picks are allocated best-edge-first; a pick that doesn't fit steps
DOWN to the largest denomination that fits or is dropped — no
fractional partial fills.

### Ladder/milestone betting (operator rules, 2026-08-05)

The ladder is a small ADD-ON to a conviction over pick, not a
parallel betting system:

1. **Gap gate** — fires only when the primary is a PLACED OVER bet
   and E[K] ≥ line + `LADDER_GAP_MIN` (1.5). Line 6.5 needs a
   projection of 8.0+.
2. **Next rungs only** — at most `LADDER_RUNG_COUNT` (2) lines above
   the primary: line 6.5 → alt 7.5 and 8.5 (milestones 8+ and 9+).
3. **Descending stakes in clean denominations** — the primary at the
   market line carries the most money (most robust to a short outing);
   each rung up caps at half the rung below: `primary × 0.5^distance`,
   quantized to clean units ({0.25, 0.5, 1, 1.5, 2} via
   `models/staking.py::quantize_stake`). A 2u primary yields the
   **2 / 1 / 0.5** template (3.5u total = `LADDER_MAX_UNITS`).
   Nearest-rung-first allocation, on top of quarter-Kelly and the 2u
   per-bet cap. This is also the line-gap defense: when DK's line sits
   far below the projection, the line placement itself is leash
   information — most money stays on the leash-proof low line, with
   tapering plus-money exposure to the full-start upside.
4. Every rung still clears `LADDER_EDGE_THRESHOLD` (10%).

Every posted rung is evaluated and stored with a pass/bet status so
the dashboard shows the whole board. DK's alt board is over-only
(probed 2026-08-05); the morning automation re-probes candidate
under subcategories daily and flags if one appears.

Original design notes below for history:

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

**Cloud (Railway — target state).** Project `mlb-strikeouts`, service
`worker`, persistent volume at `/data` (Statcast cache +
`worker_state.json`). `tools/railway_worker.py` is a resident
scheduler: times are declared in America/New_York and compared to
`datetime.now(ET)`, so DST is handled by construction — no UTC cron
shuffling and no hourly-shotgun workaround (contrast NRFI's
`daily.yml`, which needs both because GitHub's `schedule` trigger
fires 1–3 hours late). Each job has a lateness grace: a missed closing
snapshot is skipped and logged rather than fired uselessly after first
pitch; grading and slates still run late.

The worker pulls the repo before each job and pushes the ledger back
after, so GitHub stays the durable source of truth and the Vercel
build picks up `dashboard/public/data.json`. Because every task shells
out to a fresh `python run.py`, a `git pull` means code changes take
effect without redeploying the container.

Required Railway variables: `GITHUB_TOKEN` (contents:write on the
repo — without it the worker computes picks but they never leave the
volume), `GITHUB_REPO`, optional `VERCEL_DEPLOY_HOOK`.

**Local (Windows Task Scheduler, `tools/scheduled_run.py`, ET)** —
the pre-migration path; keep it enabled until the cloud worker is
verified pushing, then disable to avoid double-runs:
10:30 AM morning picks + deploy · 4:45 PM lineup-lock re-run
(re-predict with confirmed lineups; placed bets stay frozen, changes
journaled) · 12:15/3:00/6:15 PM closing snapshots · 3:00 AM grade +
CLV + deploy. Logs in `logs/auto_YYYY-MM-DD.log`. Runs only while
logged in — keep the PC on. Manual commands still work any time:

```
python run.py              # full daily cycle
python run.py predict      # today's picks only
python run.py grade        # grade yesterday's picks
python run.py close        # closing-odds snapshot (CLV)
python run.py status       # show record and P&L
python run.py backfill     # refresh Statcast cache
```

## Dashboard (Phase 8 — Next.js rebuild; Phase 5 static page retired)

### Data pipeline

```
data/picks_2026.csv ─┐
data/slates/*.json  ─┼→ tools/dashboard_data.py → pnl_guard → dashboard/public/data.json
data/backtest_predictions.csv ─┤                                     ↓
data/gauntlet_results.json ────┘                    Next.js app (dashboard/), static export
                                                    routes: /  /performance  /model  /brief
```

`dashboard_data.py` merges the picks ledger with slate sidecars
(grades + stakes attached to every board pitcher and ladder rung),
computes all P&L via `tracker._calc_pnl` (canonical source), tags
every P&L value with `{"value": float, "basis": "flat_100u"}`, runs
the FlatUnits guard, then writes `data.json` into the app's public dir.

Operator flow after any data change:
`python tools/dashboard_data.py` → commit → push (Vercel builds), or
`npx vercel --prod --yes` from the repo root.

Stack: Next.js App Router (static export, trailingSlash), Tailwind v4
tokens from the Dark Terminal palette, 21st.dev components
(@originui/accordion, @ssicevs/market-snapshot P&L chart,
@aghasisahakyan1/expandable-card interaction), hand-rolled SVG charts
elsewhere (NRFI geometry idiom), Outfit display / DM Mono figures.

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
