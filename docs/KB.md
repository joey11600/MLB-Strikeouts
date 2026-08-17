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

### Outs Recorded — a SECOND, separate model family

The DraftKings "Outs Recorded O/U" market (subcategory 17413,
half-integer lines 13.5–19.5) does **not** reuse the Stage A × Stage B
compound above, and must not. Outs is a stopping time on a lattice,
not a count: 65.5% of starts end on an exact multiple of 3 because the
removal decision is made at inning boundaries. A moment-matched
negative binomial puts 0.073 on 18 outs against an empirical 0.220.

```
for inning j = 1..9:
  X_j ∈ {0,1,2,3}   outs recorded in inning j        logit P(X_j=3) = γ_j + x'δ + x_Q'δ_j
  R_j ∈ {0,1}       comes back out for inning j+1    logit P(R_j=1) = α_j + x'β + x_Q'β_j
  X_j < 3           removed mid-inning               P(X_j=r | X_j<3) = softmax(ψ_j)
Compose → P(outs = k) for k in 0..27
```

The 27-out ceiling is enforced by the **absence** of a parameter — `α`
has 8 entries for 9 innings, so no `R_9` exists and no code path
advances past inning 9. Nothing is ever clipped; the recursion
normalizes by construction (measured worst |Σ−1| = 5.6e-16 over all
13,170 starts).

| Piece | File |
|---|---|
| Spec and every design decision | `docs/OUTS_MODEL.md` |
| Composition recursion + self-tests | `models/outs_hazard_proto.py` |
| Fitted model, CLI, save/load | `models/outs_hazard.py` |
| Per-start label table | `tools/build_outs_dataset.py` |
| External validator vs MLB boxscores | `tools/validate_outs_vs_mlb.py` |
| As-of features | `features/outs_asof.py` |

Fit and evaluate:

```
python -m models.outs_hazard --fit --train 2024,2025 --test 2026
python -m models.outs_hazard --three-way
```

Out-of-sample Brier skill vs the honest as-of baseline: +4.48% / +4.68%
/ +7.48% across the three mandated splits. **Not yet bettable** —
calibration shows ECE 0.017–0.026, so the output must route through
`models/calibration.py` first, and Gates 1–5 have not been run on the
individual features against this target.

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
- **DraftKings**: odds via unofficial JSON API (`curl_cffi`). Category
  1031 (Pitcher Props) holds exactly three subcategories — 15221
  Strikeouts Thrown O/U, 17323 Strikeouts Thrown (milestones), and
  17413 Outs Recorded O/U. Every response carries a `subcategories`
  array; enumerate from there rather than guessing IDs.
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

### Edge threshold — two independent gates

A bet must clear BOTH:

1. **Edge vs fair** — `max(hold% + 2%, 3%)`, plus
   `PROJECTED_LINEUP_EDGE_PENALTY` (5pp) when the lineup isn't
   confirmed. Asks: *do we disagree with the market?* The penalty is
   sized to measured uncertainty — league-average lineups move
   P(over) 5.1pp on average, 10.9pp worst case (AUDIT A-008).
2. **Real EV** — `blended_prob × decimal_odds − 1 ≥ MIN_EV` (4%),
   computed against the ACTUAL vigged price. Asks: *is the
   disagreement worth backing at the price offered?* Break-even is the
   vigged implied probability, not the de-vigged fair one, so this is
   the gate that speaks in money and it is immune to the
   `ALT_SIDE_MARGIN` assumption (AUDIT A-009).

Note `ALT_SIDE_MARGIN` is deliberately NOT set to the measured ~24%
alt-board overround: since `edge = blended − fair` and `blended` is
half market, raising the margin makes the system *more* aggressive.
The EV gate is the correct guard.

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

**Data flow — no push credential required.** The mutable state
(`picks_2026.csv`, `pick_changes.csv`, `slates/`, `odds/`) is
symlinked onto the volume by `bind_state_to_volume()` on boot, seeded
once from the image. Without that, every redeploy would silently reset
the ledger to whatever was last committed. Code under `data/` stays in
the image so updates still ship normally.

The worker exposes a small HTTP server:

- `GET /data.json` — the dashboard payload, straight off the volume
  (CORS-open, `no-store`)
- `GET /health` — ET clock, which jobs ran today, cached months,
  payload presence

`dashboard/lib/data-context.tsx` fetches that endpoint first and falls
back to the snapshot bundled in the Vercel build if the worker is
unreachable. Picks therefore appear the instant the pipeline writes
them — no rebuild latency — and nothing needs a token.

It re-fetches **every 60s and whenever the tab regains focus**. That is
not cosmetic. The provider used to fetch once on mount and never again,
so a terminal left open overnight — the normal way this is used — showed
the date it was opened on indefinitely, with any game that happened to
be live at load time still pulsing (A-039). A failed *refresh* keeps the
board that is already on screen; only the first load can surface an
error, because replacing a good slate with an error page over one blip
against the worker is strictly worse than showing a slightly old one.

The board also says so when it has defaulted to a past slate. A date
only enters `available_dates` once its slate is written, and that is the
09:00 ET job, so between midnight and ~09:20 the newest board genuinely
IS yesterday and `page.tsx` lands on it.

Optional Railway variables: `GITHUB_TOKEN` (+ `GITHUB_REPO`) turns on
a git mirror of the ledger for offsite backup; `VERCEL_DEPLOY_HOOK`
refreshes the fallback snapshot. Both are pure backup: the volume is
the live source of truth.

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

**What Vercel actually runs, and what it deliberately does not.** Two
overrides in `vercel.json` exist purely to keep the build cheap, and
both have cost real money when absent (A-023, A-033):

- `ignoreCommand` → `scripts/vercel-ignore-build.sh`. A push whose diff
  since the LAST BUILD touches only `data/` and
  `dashboard/public/data.json` exits 0 and Vercel skips. Baseline is
  `VERCEL_GIT_PREVIOUS_SHA`, not `HEAD^` (A-023a), and the script
  deepens the shallow clone to reach it (A-033). It fails toward
  BUILDING: a needless build costs seconds, a wrongly skipped one ships
  stale code with nothing turning red.
  **Vercel's build container has no remote named `origin`** — it has the
  objects and refs and nothing to fetch from. Any `git fetch ... origin`
  added here will fail. The script reads `git remote` and, finding it
  empty, rebuilds the URL from `VERCEL_GIT_REPO_OWNER` /
  `VERCEL_GIT_REPO_SLUG`. This works because the repo is public; if it
  is ever made private the reach-back will fail and every data commit
  will start building again (loudly — the failures are printed).
- `installCommand` → a no-op `echo`. Without it Vercel finds the root
  `requirements.txt`, and on its CPython 3.14 image neither numpy nor
  pandas has a wheel, so both compile from source — 84s of Python
  against 23s for the site that ships. Nothing in `dashboard/` imports
  Python. `requirements.txt` is for CI and Railway and still works
  normally there; only Vercel's install step is suppressed.

Adding a Python function under `api/` would need this revisited — the
install step is off for the whole project, not just the dashboard.

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

## Odds provenance (Phase 9)

DraftKings returns **403 to datacenter IPs** and 200 to residential
ones. Measured: the gate is User-Agent plus egress IP reputation, NOT
TLS/JA3 fingerprint — seven curl_cffi impersonation profiles and plain
`requests` with a browser UA all return 200 locally; only the default
`python-requests` UA is rejected outright. No client-side change fixes
an IP-reputation verdict (AUDIT A-012).

`scrape_dk_odds.py` can therefore fall back to a previously captured
board. That fallback is **off by default** (`DK_ODDS_SNAPSHOT_FALLBACK=1`
to enable) and is fenced by rules that exist because stale odds priced
as live corrupt the ledger the same way a bad feature does — the edge
filter selects inflated prices INTO the bet list (same principle as
A-007):

- **`captured_at` is a real CSV column, and the clock never comes from
  the filesystem.** git checkout and Docker `COPY` both reset mtime to
  build time, so an mtime-dated board arrives on the container looking
  fresh no matter how old it is. A board with no stamp is refused, not
  guessed at.
- **Staleness is per row**, ceiling `DK_ODDS_SNAPSHOT_MAX_AGE_H`
  (default 6h). A file-level max would let one refreshed pitcher
  re-validate every stale row beside it in an append-log.
- **Candidate order is date → freshness → filename prefix.** A
  two-minute-old `closing_*` board beats a five-hour-old `dk_k_*` one.
- **The slate date is checked in the loader**, not left to
  `daily_pipeline`'s downstream `date ==` filter.
- **`tools/closing_odds.py` never accepts a snapshot.** It re-dates
  rows to today and re-stamps `captured_at` to now — feeding it a
  snapshot launders a stale board into the closing price the CLV grader
  writes to the ledger, defeats the date filter, and resets the
  staleness clock permanently. Losing CLV on a blocked day is the cheap
  failure.
- **`odds_source` is a ledger column** (`live` / `snapshot` / `""` for
  rows predating provenance tracking), so "were the bad bets the ones
  priced off stale prices?" is answerable after the fact.

- **Markets never share snapshot files.** The outs board uses
  `dk_outs` / `closing_outs`; the strikeout board uses `dk_k` /
  `closing`. Both carry a `line` column and identical row shapes, so a
  prefix matching across markets would price 17.5 outs as 17.5
  strikeouts silently. `_candidate_snapshots` anchors the date
  immediately after the prefix, which makes the two sets disjoint by
  construction rather than by convention.

`python scrape_dk_odds.py --self-test` covers all of it (12 cases,
including old-content-with-fresh-mtime, which is the production case,
and D7 cross-market snapshot separation).

### Outs Recorded capture (Phase 10, 2026-08-08)

`fetch_dk_outs_props()` writes `dk_outs_*` / `closing_outs_*` and is a
**writer only** — no caller in `daily_pipeline`, no ledger column, no
grader, nothing prices an outs bet. It exists because closing prices
are the one input that cannot be backfilled: a model can be built later
from cached Statcast, a closing line from a given day cannot be
reconstructed at all. Ordered last and wrapped in both producers, so an
outs failure can never cost a strikeout closing line.

Measured on the 2026-08-08 board, both samples:

| | strikeouts | outs |
|---|---|---|
| hold | 5.99% (5.84–6.20) | **6.97%** (6.82–7.19) |
| lines | 3.5–8.5 | 13.5–19.5, all half-integers |
| coverage | 30 pitchers | same 30 pitchers |

Outs is the **more expensive** market by ~1 point of hold, higher on
all 14 pitchers in the head-to-head sample. Do not carry the K market's
edge threshold across. Half-integer lines mean no push in practice, but
that is an observation and not a guarantee — P(exactly 18 outs) is 0.22
league-wide, so an integer line would carry first-order push mass and
must not be priced until a three-outcome path exists.

## Live model measurement (Phase 9)

`data/model_log.csv` scores every evaluated pitcher, not just the bets.
The dashboard's `live_model` block compares it to the backtest — but
**on calibration error, never on raw Brier**.

Brier is not scale-free. It depends on how separable the sample is, and
the book hangs its line where the game is closest to a coin flip, so a
live board's irreducible floor sits far above the backtest's fixed
six-line grid (which includes 8.5 at Brier 0.065). Differencing them
directly made a *perfectly calibrated* model read "worse than backtest"
in 100% of 4,000 Monte-Carlo trials (AUDIT A-010).

The comparable quantity is `excess = Brier − floor`, where
`floor = mean p(1−p)` — the Brier a perfectly calibrated model would
post at exactly the confidence claimed. Zero excess means the
confidence was earned. The backtest reference is re-weighted onto the
live sample's own line mix via `per_line[].model_excess`.

The verdict band is **2 SE of the live Brier**, not a fixed constant: a
1-SE trigger fires on ~32% of healthy slates, and a band that tightens
as the log grows needs no retuning. Detection power at n≈26 is low and
the page says so — workload (leash) error and the calibration curve
move first.

Anything counted as an "observation" must be **scorable** (has both a
probability and a settled outcome). Rows merely present are counted
separately; conflating them once advertised 22 observations beside a
4-row Brier.


## Two ledgers, one truth (Phase 9)

The container's jobs read and write `DATA_STATE_DIR` (the Railway
volume). `git pull` only updates the `/app` checkout. Those are
different paths, so a pull alone does NOT bring the PC's picks into the
ledger the jobs use — that was a live split-brain (AUDIT A-013), and it
showed up as a quietly wrong record on the dashboard rather than an
error, because the site prefers the worker's `/data.json`.

`reconcile_ledger()` runs after every pull and merges checkout into
volume:

- **Union only.** Rows are added or advanced to a more complete version
  of themselves. Never dropped, never downgraded. The append-mostly rule
  holds across machines, not just within one.
- **Conflict order:** graded beats ungraded (a grade is strictly the
  later state; reopening one violates the locked-picks rule), then
  later `updated_at`, then the row with more populated fields.
- **Key:** `(date, game_pk, pitcher_id, line)` — the same key
  `daily_pipeline._load_existing_picks` uses. Unique across the ledger
  including ladder rungs, whose `line` is `6+` rather than `6.5`.
- **Files (slates, odds):** compare `generated_at` / `captured_at` read
  from *inside* the file. Never mtime — git checkout resets it on every
  deploy, which is the same trap as the odds staleness clock.

Idempotent: identical repo and volume is a byte-level no-op.

Not yet closed: the container has no push credential, so its writes
reach the dashboard but not git.

## Key invariants

1. `f(L, L) == L` on every build.
2. `features/asof.py` is the only path to rate features in training.
3. Atomic CSV writes (tempfile + fsync + os.replace).
4. Never delete rows.
5. `tools/pl_calc.py` is the only source of P&L numbers.
6. Negative controls (lunar, random, shuffled) must be rejected by
   every retrain.
7. Odds carry provenance and an in-file capture time. Never date a
   board from file mtime; never let `closing_odds.py` read a snapshot.
8. Never difference two Brier scores from samples with different line
   mixes. Compare excess-over-floor instead.
9. The volume ledger and the git ledger are merged, never replaced.
   Any reconcile is union-only and may not drop or downgrade a row.
10. **The worker image must contain `.git`.** Railway is the clock and
    CI is the hands; git is the only wire between them. `.dockerignore`
    must never exclude `.git/` — without it every git call in the
    container fails with exit 128 ("not a git repository"), the worker
    silently serves a board frozen at image-build time, and because the
    dashboard prefers the worker's `/data.json`, that frozen board IS
    the site (A-029).
11. **A health check reports capability, not configuration.** Probing
    that an env var is set, or that a file exists, passes while the
    thing it guards is broken — `can_push_to_git` read `true` for 16
    hours across a container that could not run `git` at all. Check the
    operation, or check nothing.
12. **A success line must be able to fail.** `configure_git()` logged
    "git remote configured" unconditionally after four unchecked
    `subprocess.run(..., capture_output=True)` calls. Never log success
    from a code path that cannot observe failure (A-029).
13. **The container's checkout is derived; never merge into it.**
    `/app` is scratch — the volume is the ledger, and
    `dashboard/public/data.json` plus the mirrored CSVs are rebuilt
    from it every publish pass. So `sync_repo` takes `origin/master`
    wholesale (`fetch` + `checkout -B master FETCH_HEAD`) and never
    rebases or merges. A three-way merge on a file both sides
    regenerate in full has no correct resolution: on 2026-08-11 one
    halted mid-rebase, detached HEAD, and cost four hours of pushes
    while every `git-commit` reported OK (A-034). Corollary: never
    commit while HEAD is detached — `git push origin master` names the
    branch, so those commits are unreachable and the push fails
    non-fast-forward for a reason that looks nothing like the cause.
14. **A cache keyed to "today" loses yesterday at midnight.** The live
    watcher wrote one `live_state.json` stamped `today_et()` and the
    board discarded it unless the stamp matched today, so the previous
    day's results blanked at midnight and refilled when Statcast landed
    at ~09:00 (A-035). Anything that answers "what happened on date D"
    must be stored and looked up **by D**, not by the current clock. Keep
    the date check when you add the key — rows keyed only by
    `pitcher_id` would otherwise attach one night's result to another
    night's start, and a fabricated result is worse than a blank one.
15. **"Missing data" means the table the operator is looking at.** The
    first pass at A-035 verified the bet ledger across five copies,
    found it perfect, and said nothing was missing. The operator meant
    the per-pitcher K totals on the board, which were genuinely gone.
    When a report of missing data meets an intact table, locate the view
    they are actually reading before concluding the report is wrong.
16. **Two hosts rendering the same artifact must read the same source.**
    `_actual_k_lookup` read the Statcast cache, a ~90 MB tree each host
    tops up on its own schedule, so the same commit produced 18/18 on CI
    and 1/18 on the worker four minutes later — and since the dashboard
    prefers the worker's payload and the worker commits `data.json`
    every 5 minutes, the worse copy won twice (A-036). When output
    differs between hosts from one commit, suspect a host-local input
    before suspecting the code. Prefer the small shared record
    (`model_log.csv`, which rides the ledger reconcile) over the large
    host-local one, and skip a blank rather than coercing it to zero.
17. **Delegating work does not delegate your own inputs.** The Statcast
    refresh lived inside `_log_evidence`, inside the task; the scheduler
    ran the task only when `dispatch_github()` FAILED. So the day
    dispatch started succeeding, the worker stopped refreshing its own
    cache and fell back to once per deploy (A-037). Whenever a primary
    path is added in front of a fallback, ask what the fallback was
    doing for you besides the obvious job — this is the third instance
    of that exact shape (A-025 publishing, A-036 rendering, A-037 the
    cache), and each time the log kept saying the window ran, because it
    had, elsewhere.
18. **A poller scoped to "today" abandons whatever is still running at
    the rollover.** This is the companion to invariant 14, and a
    different bug: storing by date fixed *where yesterday's results
    live*, not *who finishes them*. `poll_once()` computed
    `iso = today_et()` internally, so at 00:00 ET the watcher moved to
    the new date and never returned — and midnight is not a quiet
    moment, it is exactly when the longest-running work is still in
    flight. Every affected row was a 21:40-or-later first pitch; no
    early game was ever hit (A-039). Pass the date in, finish the prior
    one before starting the new one, and bound the chase both ways
    (all-done, and a wall-clock cutoff) so a suspended game cannot pin
    the worker to the past.
19. **When freshness and value come from different sources, the settled
    one must win.** The frozen rows still showed the *correct* strikeout
    count — the card reads the number from Statcast and only the badge
    from the live record — so a stale poll rendered as a live game
    rather than as broken data, and nothing errored (A-039). Any view
    that mixes a "can this still change?" flag from one source with the
    value from another needs an explicit rule for disagreement, or the
    two will contradict each other in public and look plausible doing
    it.
20. **Recording a failure is not handling it.** `sync_repo()` noted a
    failed fetch and moved on, so the FIRST failure was terminal for the
    life of the container — 27 hours serving a board from the previous
    morning, recoverable only by a human redeploy (A-040). A long-lived
    worker needs a path back from its own failures, not just a path
    past them. "The operator will redeploy" is not a recovery strategy
    when nothing tells the operator.
21. **An alarm nobody receives is not monitoring.** The watchdog
    diagnosed A-040 exactly right and exits 1, and the CI step has no
    `continue-on-error` — so every run for 27 hours was red while the
    operator watched a dashboard that looked merely stale. Before
    building a new check, ask who is woken by the ones already there.
22. **Calibrate the quantity you BET, not the one you predict.** The
    strikeout point estimate is unbiased (+0.02 K) and the model is
    still dangerous, because money rides on P(clears the line) — a tail
    of the distribution, not its mean. The top confidence bin inverted
    (stated 65.4% OVER, actual 33.3%) while the mean stayed honest, and
    the edge filter selects from precisely that bin (A-041, A-007's
    shape). A mean-unbiased model with a mis-shaped tail is worse than
    an obviously bad one, because it passes every summary statistic on
    the way to the bet list.
23. **Beating a naive baseline is not beating the market.** The
    strikeouts backtest scores `model_p_over` against `naive_p_over` on
    a synthetic 3.5–8.5 line grid and carries no odds columns at all, so
    "+3.2% Brier improvement" was never a claim about the book. Live, at
    the one posted line, the model loses to the market (paired, 264
    rows, z=+1.92 blended / +2.57 standalone) and held-out Brier rises
    monotonically with model trust weight. Any model that will be bet
    must be scored against the price it will be bet into — everything
    else measures a different question (A-041; same omission already
    written down for the outs model).
24. **Calibration can be conditional, and the bet list selects on the
    condition.** The strikeouts model is calibrated to 1.4 points where
    it AGREES with the book and off by -33 points where it most
    disagrees. Pooled calibration hides that completely: the curve looks
    acceptable because the agreeing rows outnumber the others, while the
    edge filter picks exclusively from the rows that are wrong. Before
    trusting any calibration curve, re-cut it by the quantity your
    selector keys on — for a betting model that is always the
    disagreement with the price. A univariate p -> p calibrator cannot
    repair this by construction, so reaching for one is a sign the
    diagnosis has not landed yet (A-041).
25. **You cannot backtest against a price you never recorded.** The
    strikeouts backtest runs 2026-04-11..08-04; closing captures start
    08-05. The overlap is ONE opening snapshot, so 18,798 rows are
    permanently unscoreable against the market and the honest sample is
    262 starts (A-002). Odds are not a derived artifact — they exist
    only if someone captured them that day, and no amount of later work
    recovers them. Start the capture on day one of any market-facing
    model, before the model is good enough to care about, because the
    day you need the history is the day it is too late to collect.
26. **A de-vig that RAISES a probability is a bug, and it will look
    like a result.** Normalising a truncated alt ladder's own implied
    PMF produced a median overround of 0.946 — a book with negative vig
    — and that single sign error flipped the verdict from "model
    significantly worse" (z=+2.36) to "indistinguishable" (z=+0.03).
    Assert the direction: removing vig can only ever lower an implied
    probability. Where only one side is priced, de-vig by a MEASURED
    margin from the same event's two-sided quote and label it an
    assumption; never by a sum over a support the book truncated.
27. **Repeated measurements of one event are one measurement.** Alt
    milestones on a start are the same game seen at several thresholds:
    1,956 ladder rows carry roughly 262 starts' worth of information.
    Unclustered standard errors shrink by about sqrt(rows per start) and
    manufacture significance out of correlation. Cluster on the unit
    that actually varies — here the start, never the row.
