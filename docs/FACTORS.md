# FACTORS.md — The Factor Catalog

Source of truth for every factor considered by the Strikeouts Model.
Phase 2 builds exactly the T1 rows. Enumerate them programmatically
from this file rather than counting by hand.

## Tagging system

**Tier** — `T1` build in Phase 2 · `T2` build in Phase 6 · `T3`
quarantine (see QUARANTINE.md) · `T4` rejected, listed so nobody
re-adds it · `—` not a feature, an architecture/operational note.

**Data** — `✅` free and verified · `⚠️` needs work or unverified ·
`❌` no source · `—` not applicable.

**Evidence** — `[E]` established/replicated · `[M]` one or two credible
studies · `[M-null]` credible study finding no effect · `[S]`
speculative · `[X]` refuted · `—` not applicable.

**Rule: tier is set by evidence × data availability, not by how
interesting the idea is.** Exceptions are stated and justified.

---

## Group A — Pitcher skill and form

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| A1 | SwStr% (swinging strike rate) | T1 | ✅ | [E] | R² ≈ 0.68 with same-season K%. Strongest simple predictor. R² > 0.5 with only ~14 days of data. |
| A2 | CSW% (called + swinging strikes / pitches) | T1 | ✅ | [E] | r² ≈ 0.64 with K%; better YoY stability (0.445 vs 0.396). Stabilizes ~10 starts. |
| A3 | Season K%, Bayesian shrinkage to league mean | T1 | ✅ | [E] | Stabilizes at 70 BF (~3 starts). Must be as-of-date. |
| A4 | Rolling K% over last 3/5/10 starts | T1 | ✅ | [E] | Multiple windows, let model weight them. |
| A5 | K% trend (slope of last 5 vs prior 10) | T1 | ✅ | [M] | Form direction, not level. |
| A6 | Contact% allowed | T3 | ✅ | [E] | Nearly equal to SwStr% — collinear. Gate 4 will kill one of A1/A2/A6. |
| A7 | Chase rate / O-Swing% induced | T1 | ✅ | [E] | Only plate-discipline metric that helps K% and BB% simultaneously. |
| A8 | Put-Away Rate (2-strike K conversion) | T1 | ✅ | [M] | r² = 0.85 with K% but much less sticky YoY. High PAR + low SwStr% = expect decline. |
| A9 | Zone% / in-zone rate | T2 | ✅ | [M] | Low standalone K value. Build only as interaction with umpire zone (D1). |
| A10 | First-pitch strike % | T2 | ✅ | [M] | Strongest link to walk suppression; weak on K. Count leverage. |
| A11 | K-BB% | T3 | ✅ | [E] | BB-contaminated; BB% needs 170 BF to stabilize. Inferior to A1+A3. |
| A12 | Fastball velocity, absolute | T3 | ✅ | [E] | R² only 0.18 with K%, priced into A1. Use A13 instead. |
| A13 | Velocity delta vs own 30-day baseline | T1 | ✅ | [E] | The useful velocity feature. −1.5 mph from baseline is real signal. |
| A14 | Pitcher age | T1 | ✅ | [E] | K/9 flat to age 32, then decline. Survivor bias caveat. |
| A15 | Pitch-type whiff% × usage | T1 | ✅ | [E] | Sliders carry highest whiff. Must be as-of-date. |
| A16 | Arsenal breadth (# pitches ≥10% usage) | T1 | ✅ | [E] | Matters mainly as TTO interaction (C4). |
| A17 | Added a new pitch this season | T3 | ✅ | [M-null] | 2,892 pitcher-seasons: no significant difference. |
| A18 | Spin rate + spin-rate delta | T2 | ✅ | [M] | Post-2021 crackdown heterogeneous. Fit on post-June-2021 only. |
| A19 | Release point consistency / variance | T3 | ⚠️ | [S] | Computable from Statcast but no published effect size. |
| A20 | Extension (release distance toward plate) | T2 | ✅ | [M] | Longer extension = higher perceived velocity. |

---

## Group B — Opponent lineup

Timing constraint: B1, B3, B6, B7, B10, B13, B15, B16 require the
posted lineup (~3h pre-game). The model runs twice: early pass on
projected lineups, late pass on confirmed. Backtest must reproduce both.

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| B1 | Lineup-weighted K% (per-hitter, not team avg) | T1 | ✅ | [E] | Sum over posted 9, weighted by expected PA given batting order. Most important opponent feature. |
| B2 | Matchup K% via normalized empirical form | T1 | ✅ | [M] | (B×P)/(a·B·P + b) with b = L − aL². See §1.5. |
| B3 | Batter K% split by pitcher handedness | T1 | ✅ | [E] | From the hitter side. |
| B4 | Pitcher K% vs LHB / RHB | T1 | ✅ | [E] | Sign: positive = benefits from platoon. LHP splits much larger than RHP. |
| B5 | Platoon-split regression by arm angle | T1 | ✅ | [E] | RHP need ~1,670 harmonic PA; LHP only ~570. Arm-angle league priors for RHP. |
| B6 | Batter whiff% by pitch type × pitcher usage | T1 | ✅ | [M] | Real arsenal-vs-lineup matchup. Must be as-of-date. |
| B7 | Batter chase rate, lineup-weighted | T1 | ✅ | [E] | |
| B8 | Historical P-vs-B head-to-head | T3 | ✅ | [S] | 5–30 PA samples = noise. Display only, near-zero model weight. |
| B9 | Pitcher K% vs specific team, career | T3 | ✅ | [S] | Roster changed. Captured by B1. |
| B10 | Lineup handedness composition (# LHB) | T1 | ✅ | [E] | Interacts with B4/B5. |
| B11 | Days since lineup last faced this pitcher | T3 | ✅ | [S] | Effect ≈ 0. Demoted. |
| B12 | Lineup's recent K% (last 15 games) | T2 | ✅ | [M] | Form vs talent. Weight below season rate. |
| B13 | Regulars resting / bench bats in lineup | T1 | ✅ | [M] | Bench bats strike out more. |
| B14 | Rookie / call-up hitters in lineup | T2 | ✅ | [M] | Capture via hitter-level projections, not a September dummy. |
| B15 | Lineup aggregate contact% and zone-contact% | T1 | ✅ | [E] | |
| B16 | Batting-order expected-PA weights | T1 | ✅ | [E] | Leadoff sees ~0.5 more PA. Mechanical — compute, don't fit. Feeds the Poisson-binomial. |
| B17 | DH vs pitcher spot | T4 | — | — | Dead. Universal DH since 2022. |

---

## Group C — Workload, leash, and expected batters faced

Most reducible variance lives here. Give it the most engineering time.

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| C1 | Pitcher's own BF distribution, last 10 starts | T1 | ✅ | [E] | Not the mean — the distribution. Stage A backbone. |
| C2 | Manager leash tendency (team-level) | T1 | ✅ | [M] | Mean and SD of starter pitch count, shrunk to league. Underused. |
| C3 | League BF/IP/pitch baselines | — | ✅ | [E] | Not a feature — a constant (22 BF, 86.2 pitches in 2024). Stage A prior. |
| C4 | TTO × arsenal breadth interaction | T1 | ✅ | [M] | Most actionable TTO feature. FB-heavy lose 47 wOBA pts 3rd trip; low-FB only 18. |
| C5 | TTO penalty, base | T2 | ⚠️ | [E/gap] | ~13 wOBA pts/trip. No published K%-by-TTO table. Compute from Statcast in Phase 1. |
| C6 | TTO is partly self-cancelling for K props | — | — | — | Architecture note. More BF raises K even as per-batter K% dips. Model separately in Stage A and Stage B. |
| C7 | Prior start pitch count | T2 | ✅ | [M-null] | 110 vs 100: ~3–4 runs over a season, K rate NOT significantly affected. Stage A only. |
| C8 | Days of rest | T2 | ✅ | [M-null] | No significant effect found. Low expectations. |
| C9 | Season cumulative innings vs career norm | T2 | ✅ | [S] | Workload fatigue proxy. |
| C10 | First start off the injured list | T1 | ✅ | [S] | Tier exception: [S] evidence but mechanism is a hard pitch limit (mechanical, observable). Stage A only. |
| C11 | Announced pitch limit / opener / bullpen game | T1 | ⚠️ | [E] | Tier exception: beat-writer news, not API. Highest-value Stage A input. Operator-maintained `data/manual_pitch_limits.csv`. |
| C12 | Bullpen usage in last 2–3 days | T1 | ✅ | [M] | Gassed bullpen = longer leash. Almost nobody models this. |
| C13 | Doubleheader / 27th-man context | T2 | ✅ | [M] | Merged with F9. Build once in features/workload.py. |
| C14 | Blowout risk | T2 | ✅ | [M] | Pre-game market-implied only (run line + total). Merged with H7. |
| C15 | Standings position / playoff race | T3 | ✅ | [S] | Confounded and weak. |
| C16 | MLB debut start | T2 | ✅ | [S] | Short leash. Stage A. |

---

## Group D — Umpire and catcher

Collinearity warning: R² ≈ 0.776 between team catcher-framing runs and
total umpire "favor." Include D1 and D4 jointly or orthogonalize.

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| D1 | HP umpire called-strike-rate over expected | T1 | ⚠️ | [M] | Compute from Statcast. 2025 spread: 31%–52% borderline strike rate across 83 umps. ±0.2 to ±0.5 K at extremes. |
| D2 | Umpire effect is far smaller on K than BB | — | — | [E] | Prior-setting note. Zone changes explain 71% of walk reduction but only 3–9% of K increase. |
| D3 | Umpire assignment availability | — | — | — | Operational. Typically known ~1 day ahead. |
| D4 | Starting catcher framing runs | T1 | ✅ | [E] | rv_tot plus zone-region breakout. ~0.2–0.4 K at extremes. Needs confirmed catcher. As-of-date. |
| D5 | ABS challenge K-flip rate | T1 | ✅ | [E] | New for 2026. n_strikeouts_flip per pitcher/catcher. Regime-scoped: exempt from Gate 2 three-way split. |
| D6 | Umpire age / experience | T2 | ✅ | [M] | Runs backwards: older umps have higher error rates. |
| D7 | Umpire star/status bias | T3 | ⚠️ | [M] | ~16% relatively more likely to call ball a strike for All-Stars. ABS erodes it. |
| D8 | Count-dependent zone size | — | ✅ | [E] | Biggest umpire effect but NOT game-level. 0-2 zone is 2.39 sqft; 3-0 is 3.73 (36% larger). Pitch-level only. |
| D9 | Umpire home-team bias | T3 | ⚠️ | [M] | Favors home hitter → small K penalty for road pitchers. Confounded with D10. |
| D10 | Home vs away | T1 | ✅ | [M] | Home starters K'd 10.2% more (117,534 games). Declining over time, confounded. |
| D11 | Within-game umpire fatigue | T3 | ✅ | [S] | No published study isolates from count effect. |
| D12 | Personal catcher / battery pairing | T4 | ✅ | [X] | Reject. "Chemistry" beyond framing is unestablished. Catcher's ERA is noise. |
| D13 | Umpire monitoring regime | — | — | [E] | Architecture note. QuesTec → Zone Eval → Statcast → ABS is a chain of regime shifts. Refit annually. |

---

## Group E — Park, environment, and weather

Collinearity warning: E1, E3, E5, E17 measure overlapping things. Build
E17 as the single physical variable; let E1 carry only the residual.

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| E1 | Park strikeout factor (residual) | T1 | ⚠️ | [M] | Savant park factors include SO, indexed to 100. Residualize against E17 first. |
| E2 | Park fastball-whiff factor | T1 | ✅ | [M] | Spread: Tropicana 24.0% → Coors 16.5%, 7.5 pp range in FB whiff. Convert to K-per-start before ranking. |
| E3 | Altitude / air density | T1 | ✅ | [E] | ~1 inch less movement per 1,000 ft. Coors: 4-seam loses ~2.6" vertical. Feed through E17. |
| E4 | Altitude × arsenal interaction | T1 | ✅ | [M] | Curveball-heavy at Coors is the most identifiable negative park-arsenal interaction. Slider-heavy arms survive best. |
| E5 | Roof status | T1 | ✅ | [M] | Three top FB-whiff parks are roofed. Stable air, no wind, consistent light. |
| E6 | Temperature × pitch mix | T1 | ✅ | [M] | Cold (<60°F): slider whiff −1.3%, splitter −1.0%. Interact temp with arsenal. Train on forecast weather. |
| E7 | Temperature, main effect | T3 | ✅ | [S] | Two mechanisms push opposite ways. No published net K coefficient. E6 is better. |
| E8 | Humidity (as grip) | T2 | ✅ | [M] | 20% RH change ≈ 1/8 inch movement (negligible). But dry air is worst grip condition. |
| E9 | Humidor regime (2022+) | — | ✅ | [E] | Not a feature within 2024–26 — flag is 1 on every row. Documented constant. |
| E10 | Barometric pressure as own feature | T4 | — | [X] | Reject. Enters only through air density. Double-counting. Use E17. |
| E11 | Wind speed and direction | T3 | ✅ | [S] | No published K effect. Indirect only (fewer HR → longer start → more BF). |
| E12 | Sun/shadow geometry at first pitch | T2 | ⚠️ | [S] | Most under-quantified factor with highest plausible upside. Fully computable. Genuinely original work. |
| E13 | Ballpark orientation | — | ✅ | [M] | Input to E12, not standalone. |
| E14 | Batter's eye quality / stadium lighting | T3 | ❌ | [S] | Anecdote only, confounded. |
| E15 | Day vs night | T3 | ✅ | [M] | Day/night gap reversed around 1980. Real variable is temperature (E6). |
| E16 | Attendance / crowd size | T4 | ✅ | [X] | Reject. COVID-2020: no significant MLB home-advantage change. |
| E17 | Air density, computed | T1 | ✅ | [E] | The correct single environmental variable. Combine altitude + temp + humidity + pressure. |

---

## Group F — Schedule, travel, and circadian

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| F1 | Eastward travel, <1 day per TZ crossed | T2 | ✅ | [M] | 46,535 games. Almost entirely eastward. Big enough to erase HFA. But metric was HR, not K. Compute and test. |
| F2 | Westward travel | — | ✅ | [M-null] | Not a feature — a control. Same study: "very limited effects" westward. |
| F3 | Days since arriving in current time zone | T2 | ✅ | [M] | Body clock shifts ~1 hr/day. Mechanism variable behind F1. |
| F4 | Consecutive road games | T2 | ✅ | [S] | |
| F5 | Day game after night game | T3 | ✅ | [S] | No credible study. CBA changed getaway timing in 2017. |
| F6 | Getaway day / day of week | T4 | ✅ | [X] | Reject. Lineup-composition artifact — top hitters sit 1.1% more Sundays. Conditioning on posted lineup should make it vanish. |
| F7 | Late-season chase-rate decay | T2 | ✅ | [M] | 24/30 teams showed monotonic decay April→September. Re-test from Statcast. |
| F8 | Month of season / games elapsed | T3 | ✅ | [M] | Strictly cruder than F7. Same signal. |
| F9 | Doubleheader, game 2 | — | ✅ | [S] | Merged into C13. Build once in features/workload.py. |
| F10 | Player sleep quantity / quality | T3 | ❌ | [M] | Real mechanism but covariate unobtainable. F1/F3 are the proxies. |

---

## Group G — Situational and psychological

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| G1 | Pitcher facing former team | T3 | ✅ | [S] | n=229, no quality control. Re-test properly. |
| G2 | Contract year | T3 | ⚠️ | [M] | Untested for pitcher K rate. |
| G3 | Start after very bad outing | T4 | ✅ | [X] | REJECT — regression to the mean wearing a costume. |
| G4 | Revenge game / rivalry | T4 | ⚠️ | [S] | No credible evidence. |
| G5 | Playoff race pressure | T3 | ✅ | [S] | |
| G6 | First start after being traded | T3 | ✅ | [S] | |
| G7 | National TV game | T4 | ⚠️ | [S] | Pure selection bias. Confounded beyond recovery. |
| G8 | Beanball retaliation in hot weather | T4 | ✅ | [M] | Affects HBP, not K. Rejected on relevance. |
| G9 | Uniform color | T4 | ✅ | [X] | REJECT. 1988 study failed reanalysis. Cautionary tale. |
| G10 | Full moon / lunar phase | T4 | ✅ | [X] | REJECT. Retained as negative control. If promoted, pipeline overfits. |
| G11 | Menstrual cycle phase | T4 | ❌ | [X] | Reject on three grounds: evidence (ES = −0.06), data (private health info), and ethics. Not applicable to MLB. |
| G12 | Pitcher personal news / off-field | T4 | ❌ | [S] | Unobtainable, unmodelable. |

---

## Group H — Market and meta

| # | Factor | Tier | Data | Ev | Notes |
|---|---|---|---|---|---|
| H1 | Opening line and current line | T1 | ✅ | [E] | The market is a strong baseline. Must store it. |
| H2 | Line movement (open → current) | T1 | ✅ | [E] | Sharp money signal. |
| H3 | No-vig fair probability from both sides | T1 | ✅ | [E] | Required — props hold 8–12%. Strip vig before computing edge. |
| H4 | Alternate-line ladder shape | T2 | ✅ | [M] | Full ladder implies book's distribution. Disagreement in tails = edge. |
| H5 | Cross-book line disagreement | T2 | ⚠️ | [E] | Needs a second book. The Odds API caveats apply. |
| H6 | Closing line value (CLV) | — | ✅ | [E] | NOT a feature — evaluation metric. Leakage if in feature matrix. Lives in tools/pl_calc.py. |
| H7 | Game total / run line | — | ✅ | [M] | Merged into C14. Pre-game market-implied blowout risk. |
| H8 | Model-vs-market disagreement size | T1 | ✅ | [E] | The bet trigger. Computed against H3, not H1. |
| H9 | Historical calibration in this bucket | T1 | ✅ | [E] | Port NRFI loss-cluster pipeline. |

---

## Summary

**114 rows across 8 groups** (A:20, B:17, C:16, D:13, E:17, F:10, G:12, H:9).

| Tier | Count | Meaning |
|---|---|---|
| T1 — Phase 2 | 44 | The v1 model |
| T2 — Phase 6 | 22 | After v1 is live with positive CLV |
| T3 — quarantine | 23 | Research backlog (QUARANTINE.md) |
| T4 — rejected | 13 | Documented so nobody re-adds them |
| — notes | 12 | Architecture/operational, not features |
| **Total** | **114** | |

### The 44 T1 features

A1–A5, A7, A8, A13–A16 · B1–B7, B10, B13, B15, B16 · C1, C2, C4,
C10–C12 · D1, D4, D5, D10 · E1–E6, E17 · H1–H3, H8, H9.

### Top 5 most underused (signal × rarity)

1. C11/C10/C12 — pitch limits, IL returns, bullpen fatigue (leash modeling)
2. E4 — altitude × arsenal (curveball at Coors)
3. D5 — ABS challenge K-flips (new 2026, public, unbuilt)
4. E12 — sun/shadow geometry (deterministic, zero published effect sizes)
5. H4 — alternate-line ladder shape (structural advantage in tails)

### Negative controls (build deliberately)

1. G10 — Lunar phase (genuinely meaningless)
2. Per-row random draw (fresh random per game-pitcher row)
3. Shuffled-label A1 (SwStr% permuted across rows)

Do NOT use a per-pitcher fixed random value — that's a pitcher identity
key in disguise.
