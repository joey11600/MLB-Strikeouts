
# Changelog

## 2026-08-06 — Ledger split-brain between the PC and the container (A-013)

Found while verifying the deploy above. The container's jobs read and
write `DATA_STATE_DIR` (the Railway volume); `git pull` only updates the
`/app` checkout; and `seed_volume_state()` copies image → volume **only
where the volume is missing a file**. After the first boot, nothing
bridged them.

So there were two independent ledgers. The PC writes picks and pushes
to git; the container grades a volume copy that never sees them. And
because the dashboard **prefers the worker's `/data.json`** over the
bundled copy, the site would have shown a record missing the picks —
not an error, just a quietly wrong number. It had been papered over by
manual `FORCE_SEED` deploys, which is why it looked fine.

It would have bitten tonight: PC writes picks at 16:45 ET → git; the
container's 18:15 close and 03:00 grading run against a ledger without
them.

`reconcile_ledger()` now runs after every pull and merges the checkout
into the volume. Union only — rows are added or advanced, never dropped
or downgraded, so the append-mostly rule holds *across machines* and
not just within one. Conflicts resolve by: graded beats ungraded (a
grade is strictly the later state, and reopening one would violate the
locked-picks rule), then later `updated_at`, then the more populated
row. Merge key is `(date, game_pk, pitcher_id, line)` — the pipeline's
own key, verified unique across the ledger with ladder rungs included.
Slate sidecars and odds files compare timestamps read from *inside* the
file (`generated_at` / `captured_at`), never mtime, which git checkout
resets on every deploy.

Two bugs caught by the tests written for it, both of which would have
silently lost data:

- The write was gated on the row **count** changing, so any repo-side
  update that did not also add a row was discarded — i.e. exactly the
  overnight-grading case, where the repo carries a grade for a row the
  volume already has. Now compares content.
- On an `updated_at` tie the incumbent won unconditionally, dropping a
  column the other side had populated (e.g. `odds_source`). Now the
  more complete row wins.

Verified: new PC picks merge in; container grades survive a newer
*ungraded* repo row; grades flow in the other direction too; identical
repo and volume is a byte-level no-op; running it twice changes
nothing.

Still open: the container has no `GITHUB_TOKEN`, so its writes reach
the dashboard (via `/data.json`) but never reach git. That is a
single-point-of-failure on the volume, and the fix needs a credential
only the operator can create (AUDIT A-013).

## 2026-08-06 — Live calibration on /model + snapshot odds hardening

Two independent pieces, both of which failed adversarial review as
first built and were rebuilt around what the review found.

**Live calibration (A-010).** New `live_model` block in
`tools/dashboard_data.py` and a LIVE MODEL section on `/model`,
scoring `data/model_log.csv` against the backtest.

The first cut compared raw live Brier to the flat backtest Brier
(0.1491). That comparison is rigged. The backtest averages a fixed
six-line grid including near-certainties (8.5 → 0.0654); the live log
scores one row per pitcher at whatever number the book hung, and the
book hangs its line where the game is closest to a coin flip. The
irreducible Brier floor on the 8/4 sample is 0.2385 — the baseline sat
0.0894 *below* what a flawless model could have scored. Monte-Carlo
over 4,000 trials of a perfectly calibrated model: **"worse than
backtest" 100% of the time**, at every sample size. A permanent red
alarm is an alarm the operator learns to ignore.

Fixed by comparing **calibration error** (Brier − floor, where floor =
mean p(1−p)) instead of raw Brier. That quantity is comparable across
line mixes: it isolates the part of the score attributable to model
error from the part attributable to the difficulty of the board.
`per_line` in the backtest block now carries `model_floor` and
`model_excess` so the reference is computed on the live sample's own
line mix. Same Monte-Carlo after the fix: **2.0% false "worse"**, and
the 8/4 slate reads *in line with backtest* (+0.0213 live vs −0.0004
backtest, band ±0.0435).

The verdict band is 2 standard errors of the live Brier, not a fixed
0.005. A 1-SE trigger fires on ~32% of healthy slates. The band
tightens on its own as the log grows, so real drift becomes detectable
without retuning a constant. Detection power at n=26 is genuinely low
— the page says so, and points at the leash numbers and the
calibration curve as the faster reads.

Also fixed from review: the sample gate and the observations tile now
count *scorable* rows (both a probability and an outcome) rather than
rows merely present — the old code could advertise 22 observations
beside a 4-row Brier with the warning banner suppressed;
`by_date[].reconstructed` uses `any()` not `all()`, so a 24%
reconstructed date no longer renders as LIVE; the reconstructed parser
fails closed (an unrecognised value counts as contaminated); the
per-date column applies the same noise band as the headline; an
unverified "9x the picks ledger" claim was removed (actual 6.5x); and
the empty state no longer tells the operator to run a command they
have already run — a payload generated before this block shipped is
indistinguishable from an empty log, and only the honest message
covers both.

**Snapshot odds hardening.** `scrape_dk_odds.py` grew a snapshot
fallback so the Railway container can price a slate when DraftKings
returns 403 to its datacenter IP. Review found the fallback could
launder stale odds into the ledger as live prices, which the repo's
"never fabricate odds" rule exists to prevent. Four fixes:

- `tools/closing_odds.py` now pins `allow_snapshot=False`. It was the
  laundering path: it re-dates every row to today, re-stamps
  `captured_at` to now, and drops `odds_source` — so a snapshot fed
  through it became a fresh-looking closing price that the CLV grader
  wrote into the ledger, walked through `daily_pipeline`'s `date ==`
  filter (the only guard against pricing an old board), and reset the
  staleness clock permanently. Losing CLV on a blocked day is cheap;
  recording a wrong closing price is not.
- `captured_at` is now a real column on `dk_k_*.csv`, and the loader
  **refuses** a board without it. The old code fell back to file mtime,
  which git checkout and Docker `COPY` both reset to build time — a
  week-old board arrived on the container looking freshly captured, and
  the age ceiling could never fire. Verified against a real git clone.
- Staleness is judged **per row**, not on the file's newest stamp. One
  refreshed pitcher in an append-log used to re-validate every stale
  row beside it.
- Candidate ordering puts freshness ahead of filename prefix, so a
  two-minute-old `closing_*` board beats a five-hour-old `dk_k_*` one.
  The reverse was true before and contradicted the documented intent.
- `odds_source` is now a `tracker.FIELDS` column, so a snapshot-priced
  bet stays identifiable in the ledger after the fact. Historical rows
  carry `""` — read as "predates provenance tracking", not as live.
- The slate date is checked in the loader itself rather than left to a
  downstream filter, and `daily_pipeline` prints a loud warning when
  any row was priced from a snapshot.

Measurement, not guesswork, on the 403 itself: DK gates on
**User-Agent**, not TLS/JA3 fingerprint (plain `requests` with a
browser UA → 200; default `python-requests` UA → 403; seven curl_cffi
impersonation profiles → all 200). The container is blocked on egress
IP reputation, which no client-side knob changes. That rules out the
"try another impersonation profile" theory outright. Also fixed a
latent bug: the urllib fallback tier could never have worked — it
advertised gzip and never decoded it (`UnicodeDecodeError: 0x8b`).

Self-test grew from 6 to 11 cases, including the two production
scenarios that were previously untestable: old content with fresh
mtime, and a board carrying no stamp at all.

The fallback remains **off by default** and is not enabled on Railway.

## 2026-08-06 — Model log: track every pitcher, not just the bets

Each slate produced ~28 model predictions but we durably recorded the
outcome for only the ~3 we bet. P&L and CLV can only ever evaluate bet
selection — a threshold-filtered, biased sample of the model's
opinions — and at 3/night it takes months to say anything.

`tools/model_log.py` joins every evaluated pitcher's prediction with
the actual result from Statcast into an append-only
`data/model_log.csv` (idempotent per date): ~28 observations a night
instead of 3, a ~9x faster feedback loop, measuring the MODEL rather
than the bet filter. Wired into both night jobs and persisted on the
Railway volume. `--report` prints live calibration, Brier vs the
0.1491 backtest baseline, and — most importantly — workload error,
which is the failure mode that has actually cost money.

Reconstructed slates are flagged and excluded from validation: they
were priced retroactively and their dates may sit inside the training
window. Only live slates count as prospective predictions.

First run (8/4, 26 pairs, reconstructed → diagnostic only) immediately
surfaced what 3 bets never could: mean absolute workload error 3.38 BF
with only 62% of starts within ±3. The worst miss (Yesavage: expected
22.5 BF, faced 7) is correctly NOT a role-gate case — 17 prior
appearances averaging 25 BF, a genuine starter who got knocked out
early. That distinction matters: Anderson was predictable from
history, Yesavage was not.

## 2026-08-06 — Lineup-uncertainty penalty + real-EV gate (A-008, A-009)

Both audit findings implemented as gates rather than parameter tweaks.

**A-008 — unposted lineups now cost edge.**
`PROJECTED_LINEUP_EDGE_PENALTY = 0.05` is added to the threshold
whenever `lineup_source != "confirmed"`, sized to the measured
uncertainty (league-average lineups move P(over) 5.1pp on average).
Applies to primaries and ladder rungs. The morning run mostly sees
unposted lineups, so this deliberately shifts action to the 4:45pm
lineup-lock re-run, where the inputs are real.

**A-009 — a real-EV gate on the actual posted price.**
`MIN_EV = 0.04`: a bet must clear `blended_prob × decimal_odds − 1 ≥
4%`, computed against the vigged price. Break-even is the *vigged*
implied probability, not the de-vigged fair one, so this is the only
threshold that speaks directly in money and it cannot be gamed by the
`ALT_SIDE_MARGIN` assumption. Deliberately did NOT retune that
constant to the measured 24% overround: because `edge = blended −
fair` and `blended` is half market, raising the margin *lowers* fair
faster than blended and makes the system more aggressive — the
opposite of the intent. Both gates must pass; `clears_edge` and
`clears_ev` are reported separately and stored on every rung.

Checked against real graded bets (not fitted to outcomes):

| Bet | Now |
|---|---|
| Henderson UNDER 6.5, projected lineup, lost 2u | **rejected** — edge 7.8% vs 13.1% bar |
| Burke OVER 6.5, confirmed, lost 1u | still bet — fairly priced, simply lost |
| Detmers UNDER 7.5, confirmed, won 1.32u | still bet |
| 97% model view at −2000 | **rejected** — EV −1.6% despite a positive-looking edge |

Burke surviving is the point: the gates filter *uncertainty*, not
losers with hindsight. Board-level effect: 8/4 goes 5 qualifying picks
with confirmed lineups vs 0 with projected; 8/5 goes 3 vs 1.

## 2026-08-06 — Default audit: 7 landmines now fail loudly (A-007/8/9)

Swept every default in the live pricing path under one test: *if this
fires, does it invent an input?* Seven did. All now raise instead of
substituting a league average — a fabricated input inflates a
projection, and the edge filter selects that error into the bet list
at max stake (the Anderson mechanism).

Fixed: Stage A's `c1_bf_mean` → 21.1 BF and `k_pct` → 0.225;
`predict()`'s `lineup_k_pcts` → league lineup and `pitcher_k` → 0.225;
Stage B silently falling back to the matchup formula when unfitted
(a failed model load would have priced a whole slate off the wrong
model); `american_to_implied/decimal` treating odds of 0 as a coin
flip; and `_calc_pnl` booking a WIN at an invented −110. That last one
mattered doubly — `pl_calc` validates the ledger through the same
function, so the fabrication would have been confirmed by our own
drift check.

Verified: all 7 raise, the normal prediction path is unchanged
(E[K] 5.71 on a reference pitcher), and `pl_calc` still reads
2W-4L / −4.01u with no drift.

Two measured findings filed for decision rather than silently patched:

- **A-008** — symmetric error manufactures edge too. Replacing real
  lineups with league averages (what the 10:30am run does, since
  lineups aren't posted) moves P(over 5.5) by 5.1pp on average and up
  to 10.9pp, with 12 of 25 starters over 5pp. Our edge bar is ~7-8pp.
- **A-009** — the alt board's measured overround is ~24% (implied
  47.1% vs realized 37.9%, n=190), not the assumed 4%. Retuning the
  constant would make the system *more* aggressive, not less, because
  of how the market blend enters the edge formula. A real-EV gate on
  the vigged price is the right fix.

## 2026-08-06 — Role gate: no league defaults, no pricing non-starters

Anderson's 0-K line (3.2 IP, 13 BF, all 3.5u lost) was not variance and
not the market hiding information — it was a bad input. The pipeline
fell back to `bf_mean = 21.1` (league-average starter) because he had
< 3 starter-length games in the loaded cache. He is a reliever: 40
appearances averaging 7 BF. That default inflated E[K] 3.1 -> 5.45,
produced a 17pp phantom edge, made him the #1 pick, and drew the
day's biggest stake. DK's line of 2.5 was correct all along.

- No league default, ever: workload now comes from real history.
- `is_startable` role gate — needs >= `MIN_APPEARANCES_TO_PRICE` (3)
  appearances and a recent typical outing >= `STARTER_TYPICAL_BF`
  (15 BF, vs a real starter's 18-22) over the last
  `ROLE_LOOKBACK_GAMES` (8). Stage A is trained on starter workloads,
  so pricing an opener/reliever with it is out-of-distribution.
- Skips print a reason and appear in the run log.

Verified against all 28 pitchers on the 8/5 board: skips Anderson
only (typical recent outing 5 BF), keeps all 27 real starters
(22-23 BF). Also verified against the thin June-onward cache the
10:30am run actually used — the pick would not have been made and
3.5u would not have been risked.

Filed as AUDIT A-007 with the general lesson recorded: an input error
that inflates a projection is selected INTO the bet list by the edge
filter and concentrated at max stake. It fired on 1 of 28 pitchers but
1 of 3 bets.

## 2026-08-06 — Cloud cutover verified; symlink/atomic-write bug fixed

Forced a live grading run on the Railway worker rather than waiting
for 3am. It graded correctly (Detmers UNDER 7.5 WIN 5K +1.32u, Burke
OVER 6.5 LOSS 4K −1.00u; Anderson's three still in progress) — and
then lost the result on the next deploy, which exposed a real bug.

**Symlinked state cannot survive atomic writes.** `bind_state_to_volume`
symlinked `picks_2026.csv` onto the volume, but the repo's atomic-write
pattern is tempfile + `os.replace`, and `os.replace` REPLACES the
destination path — destroying the symlink and landing the write on
ephemeral container disk. The grade looked successful in the logs and
vanished on redeploy.

Fixed by removing symlinks entirely in favour of a single
`DATA_STATE_DIR` root (env-overridable, defaults to the repo's
`data/`) that `tracker`, `daily_pipeline`, `dashboard_data`,
`closing_odds`, `grader` and `reconstruct_slate` all derive from. The
worker sets it to `/data/state`, a real directory on the volume.

Also fixed in the same pass:
- `data/odds/` was gitignored, so the snapshots CLV is computed from
  never reached the worker image — the cloud graded with no CLV while
  local had it. Now tracked (120KB) and backed up.
- `data.json` is derived but ships in the image, so a fresh deploy
  served a payload older than the volume's ledger. Now rebuilt from
  volume state on every boot.
- `RUN_TASK_ON_BOOT` (force one job immediately) and `FORCE_SEED`
  (resync volume state from the image) added as operational controls.
  Both are removed after use — leaving FORCE_SEED set would overwrite
  the worker's own writes on every deploy.

Verified end to end: worker endpoint and local `pl_calc` agree at
**2W-4L, −4.01u**, first CLV recorded (n=2, avg −0.51%), and
mlb-strikeouts.vercel.app renders it live from the worker.

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
  to the volume so restarts resume mid-day.
- Ledger, journal, slate sidecars and odds snapshots are symlinked
  onto the volume (seeded from the image once) — without this a
  redeploy would reset the ledger to the last commit.
- The worker serves `/data.json` and `/health` over HTTP and the
  dashboard reads the live endpoint (static build is the fallback),
  so the system needs NO push credential and picks appear without
  waiting for a rebuild. Git mirroring is optional backup.
- Live: `https://worker-production-036c.up.railway.app`.
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
