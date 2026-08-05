# PRODUCT.md — Strikeouts Model

## Who uses this

One operator who sells MLB betting picks. Not a developer. Needs to
read the dashboard on a phone in 30 seconds and know what to bet, how
much, and why.

## The one-sentence product

Given tonight's slate, tell me which starting pitchers' strikeout props
are mispriced, which side to take, and how much to bet.

## What it predicts

**P(K ≥ line)** for each starting pitcher on the slate — a full
probability distribution over total strikeouts, not a point estimate.

## What it does NOT predict (v1)

- Batter strikeout props (0.5/1.5 Ks for individual hitters)
- Team total strikeouts
- Reliever / bullpen strikeouts
- Live / in-game betting

The feature store is designed so batter-level props can be added later
without a rewrite.

## Key constraints

### The noise floor

~72% of strikeout variance is irreducible Bernoulli noise. The
realistic ceiling is a few percentage points of edge on a
well-calibrated distribution, not accurate point predictions. If a
backtest shows the model nailing K counts, the backtest is broken.

### The vig

Strikeout props hold 8–12% (e.g. −125/−115 on both sides), not the
near-−110 of first-inning markets. Every bet must clear a vig-adjusted
edge threshold.

### The stake

v1 cap is 2 units per bet. No track record yet — earn the right to
raise it.
