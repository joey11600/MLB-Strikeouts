# CLAUDE.md — Strikeouts Model

## Mission

Predict MLB starting-pitcher total strikeouts for a single game,
recommend over/under bets against sportsbook lines. Sibling system to
the NRFI Terminal — same operator, same discipline, same money rules.

---

## Talking to the user

The user is **not a developer**. They have explicitly asked to be taught,
not talked at.

- No bare acronyms on first use — define them in plain English.
- Translate every error message into one plain sentence.
- Click-by-click instructions for anything they run themselves.
- Re-explain patiently across sessions. Never say "as I said before."
- Code comments, commit messages, and CHANGELOG entries stay technical.
  Only chat output follows the novice rule.

---

## Data integrity rules

- CSV ledger is **append-mostly**.
- **Atomic writes only** via tempfile + `os.fsync` + `os.replace`.
  Never bypass this pattern.
- **Never delete rows.** Even POSTPONED and VOID rows stay.
- **Locked picks freeze.** Three defensive locks in `tracker.py`:
  1. Graded terminal (WIN / LOSS / VOID / PUSH / POSTPONED).
  2. Slate date > 24 h past.
  3. `created_at` > 12 h stale.
- **Pick changes journaled** to `data/pick_changes.csv`, 90-day
  rolling retention.
- Once `bet_placed=Y`, captured odds are **locked** — no refresh
  overwrites them.

---

## Quoting P&L numbers

Before stating ANY P&L figure anywhere — chat, dashboard, docs — run
`python tools/pl_calc.py` for the date/window and copy the number.
**Never mental-math the column.** The script recomputes every row's
`profit_loss_units` against the canonical calculation and flags DRIFT
on disagreements.

---

## Money rules

- **1 unit = 1% of bankroll. Bankroll is always 100 units.** The
  operator sells picks; a published stake cannot depend on the buyer's
  bank.
- **Never sum units on a moving basis.** Publish at a fixed basis and
  name it. The `FlatUnits` / `CumulativeUnits` compile-time guard
  prevents moving-basis sums from reaching the renderer.
- **Never fabricate odds.** No synthesized "what DK probably had."
  Manual overrides via `data/manual_odds_overrides.csv`.
- **v1 stake cap: 2 units per bet.** Quarter-Kelly sizing. Raise only
  after 100+ graded bets with positive closing-line value and a
  passing calibration curve. The cap is a single named constant:
  `MAX_STAKE_UNITS`.
- **Portfolio-level daily cap** with a correlation haircut — do not
  naively sum independent quarter-Kelly stakes across a correlated
  slate.
- **Edge threshold:** every bet must clear a vig-adjusted edge
  threshold. Compute the no-vig fair probability from both sides
  (props hold 8–12%) and require the model's edge to exceed the hold
  plus a margin. STRONG picks do NOT auto-bet regardless of edge (this
  overrides the NRFI precedent; see PRODUCT.md §1.6).

### Prop-specific grading

- **VOID / no action** if the listed starter does not throw a pitch
  (late scratch). A grader that books this as a loss has a P&L bug.
- **PUSH** on whole-number alternate lines (K > 6 settling at exactly
  6). Push is stake-returned, not a loss.
- **VOID** if the game is called before the pitcher is removed — per
  DraftKings house rules.
- **POSTPONED** rows stay; re-grade if resumed.

---

## Test methodology rules

- Out-of-sample validation is non-negotiable.
- Three-way split: train 2024 → test 2025, train 2025 → test 2024,
  train 2024+2025 → test 2026.
- A feature that helps in only one split direction is rejected.
- The model refuses to train if the test file overlaps the train list.
- No training data from before 2024 (post-pitch-clock, post-humidor).
- Regime-scoped features (ABS-era only) substitute a within-2026
  time-split and are held to a higher bar.

---

## Documentation rules

Every shipped change updates, in the **same commit** as the code:

- `CHANGELOG.md`
- `ROADMAP.md`
- `AUDIT.md` (if it closes an audit item)
- `docs/KB.md` (if architecture changed)

---

## Working with the user

- Don't break working state.
- Don't deploy strikeouts code into the NRFI system. This repo has
  its own branch, its own Vercel project, its own everything.

### Palette — Dark Terminal

Dark editorial sports terminal. Dark is default.

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#08080A` | Near-black canvas |
| `--surface` | `#111113` | Card / panel fill |
| `--text` | `#EDEDEF` | Primary text |
| `--text-muted` | `#55555E` | Labels, captions |
| `--over` | `#10B981` | Emerald — wins, OVER side |
| `--under` | `#F43F5E` | Rose — losses, UNDER side |
| `--accent` | `#F59E0B` | Amber — attention, ladder |

- Rounded corners (`border-radius: 14px` cards, `6px` badges).
- Outfit for display, DM Mono for figures only.
- Gradient left-border on pick cards signals side (green/rose).
- Edge bars use gradient fills with glow.
- Hue never carries meaning alone — signs and words do.
- Subtle noise texture overlay and radial gradient atmosphere.

---

## Deploy rules

**This is NOT the NRFI Terminal.** Do not push to
`claude/mlb-inning-run-predictor-QyazL` or verify against
`nrfi-terminal.vercel.app`. This repo gets:

- Its own GitHub branch / repo.
- Its own Vercel project.
- Its own Railway workers (when applicable).
- Its own Supabase project (when applicable).

---

## Feature gate gauntlet

No factor enters production without passing all five gates:

1. **Gate 1 — Leakage audit.** Could this value have been known before
   first pitch?
2. **Gate 2 — Three-way out-of-sample.** Both temporal directions must
   help.
3. **Gate 3 — Effect size sanity.** Fitted coefficient matches
   published magnitude.
4. **Gate 4 — Collinearity.** Known collinear pairs resolved.
5. **Gate 5 — Calibration, not accuracy.** Brier score and calibration
   curve on `P(K ≥ line)`.

Then: shadow for 2 weeks, compare to production, promote.
