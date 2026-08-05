# Quarantine — T3 Factors

Factors parked here have some plausibility but insufficient evidence,
missing data, or high confound risk. They never enter production
without passing the full five-gate gauntlet (§5 of FACTORS.md).

## Group A — Pitcher

| # | Factor | Why quarantined |
|---|---|---|
| A6 | Contact% allowed | Nearly equal to SwStr% — collinear. Promote only if it beats A1 head-to-head. |
| A11 | K-BB% | BB-contaminated; BB% needs 170 BF to stabilize. Strictly inferior to A1+A3 for K-only props. |
| A12 | Fastball velocity, absolute | R² only 0.18 with K%, already priced into A1. Use A13 (delta) instead. |
| A17 | Added a new pitch this season | 2,892 pitcher-seasons: no significant difference. Execution captured by A15. |
| A19 | Release point variance | Computable from Statcast but no published effect size. |

## Group B — Lineup

| # | Factor | Why quarantined |
|---|---|---|
| B8 | Historical P-vs-B head-to-head | 5–30 PA samples = noise. Display on brief page, near-zero model weight. |
| B9 | Pitcher K% vs specific team, career | Roster turnover makes it stale. Captured by B1. |
| B11 | Days since lineup last faced this pitcher | Effect ≈ 0 across sources (−1.0 to −1.3 pp vs −0.01 K/9). |

## Group C — Workload

| # | Factor | Why quarantined |
|---|---|---|
| C15 | Standings position / playoff race | Confounded, weak signal. |

## Group D — Umpire/Catcher

| # | Factor | Why quarantined |
|---|---|---|
| D7 | Umpire star/status bias | Absolute effect small, ABS erodes it. |
| D9 | Umpire home-team bias | Confounded with D10. |
| D11 | Within-game umpire fatigue | No published study isolates from count effect. |

## Group E — Park/Weather

| # | Factor | Why quarantined |
|---|---|---|
| E7 | Temperature, main effect | Two mechanisms push opposite ways. E6 (temp × pitch mix) is the version worth building. |
| E11 | Wind speed and direction | No published strikeout effect. Indirect only. |
| E14 | Batter's eye / lighting | Anecdote only, confounded with park factors. |
| E15 | Day vs night | Reversed around 1980. Real variable is temperature (E6). |

## Group F — Schedule

| # | Factor | Why quarantined |
|---|---|---|
| F5 | Day game after night game | No credible MLB study. CBA changed getaway-day timing in 2017. |
| F8 | Month of season | Strictly cruder than F7 (chase-rate decay). Same signal. |
| F10 | Player sleep quality | Real mechanism but covariate unobtainable. F1/F3 are the proxies. |

## Group G — Situational/Psychological

| # | Factor | Why quarantined |
|---|---|---|
| G1 | Facing former team | n=229, no quality control, selection bias. Re-test properly. |
| G2 | Contract year | Untested for pitcher K rate specifically. |
| G5 | Playoff race pressure | Unquantified. |
| G6 | First start after trade | Unquantified. |

## Promotion criteria

To move from T3 to T2 or T1, a factor must:
1. Have a credible, published (or internally computed) effect size.
2. Have a verified, non-leaking data source.
3. Pass all five gates with logged results in `docs/GATES.md`.
