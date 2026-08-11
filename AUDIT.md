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
