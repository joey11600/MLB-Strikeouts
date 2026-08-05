# AGENTS.md — Strikeouts Model

Mirror of CLAUDE.md for multi-agent contexts. All rules in CLAUDE.md
apply here. Key points for any agent working in this repo:

## Do NOT

- Modify or deploy anything in the NRFI repo at
  `C:\Users\Pinellas Liquidation\MLB-first-inning\`.
- Push to the NRFI production branch or Vercel project.
- Use season-to-date leaderboard aggregates for training data (leakage).
- Train on ERA5 observed weather when production serves on forecasts.
- Ship the un-normalized matchup formula (`f(L,L) != L`).
- Delete CSV rows. Ever.
- Mental-math P&L — run `tools/pl_calc.py`.
- Fabricate odds.

## Do

- Use `features/asof.py` for every rate feature in training.
- Atomic CSV writes: tempfile + fsync + os.replace.
- Journal every pick change to `data/pick_changes.csv`.
- Three-layer pick locking before any row update.
- Update CHANGELOG.md, ROADMAP.md, AUDIT.md, docs/KB.md in the same
  commit as the code change.
- Talk to the operator in plain language. No bare acronyms on first use.
- Strip vig before computing edge (props hold 8–12%).

## Architecture

- **Two-stage model:** Stage A predicts P(BF = n), Stage B predicts
  per-batter p_i, compound via Poisson-binomial DP.
- **44 T1 features** enumerated in `docs/FACTORS.md`.
- **Matchup formula:** `(B×P) / (a×B×P + b)` where `b = L − a×L²`.
  The `f(L,L)==L` unit test must be green on every build.
- **v1 stake cap: 2 units.** Named constant `MAX_STAKE_UNITS`.

## Money rules

See CLAUDE.md. Key differences from NRFI:
- 2u cap, not 10u (no track record yet).
- Edge threshold required on every bet (STRONG does not auto-bet).
- VOID on scratched starters (not a loss).
- PUSH on whole-number alternate lines.
