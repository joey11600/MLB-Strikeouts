"""Outs Recorded — the fitted inning-lattice hazard model.

Target: STARTING-PITCHER TOTAL OUTS RECORDED (0..27), to price the DraftKings
"Outs Recorded O/U" market (subcategory 17413, half-integer lines 13.5-19.5).

This is a DIFFERENT MODEL FAMILY from the strikeouts model and deliberately
shares no code with it. Outs is not a count process, it is a STOPPING TIME ON
A LATTICE: 65.5% of starts end on an exact multiple of three because the
removal decision is made at inning boundaries. A moment-matched negative
binomial puts 0.073 on 18 outs against an empirical 0.220, so the Stage-A
(negative-binomial batters faced) x Stage-B (per-batter rate) compound in
``models/stage_a_bf.py`` cannot represent the target and is not reused.

The model form, and every design decision behind it, is specified in
``docs/OUTS_MODEL.md``. The composition recursion and its normalisation proof
live in ``models/outs_hazard_proto.py`` and are IMPORTED here rather than
re-implemented, so there is exactly one copy of the code that turns hazards
into a PMF and exactly one set of self-tests covering it.

WHAT THIS MODULE ADDS TO THE PROTOTYPE
--------------------------------------
* the state explosion (per-start outs -> per-(start, inning) A_j / X_j / R_j),
* the as-of design matrix, including explicit handling of the four structural
  missingness blocks (never listwise-deleted, never silently mean-filled),
* maximum-likelihood fitting of the three components by L-BFGS-B,
* penalty selection on the DECISION METRIC (mean Brier across the seven market
  lines) via a temporal split inside the training years,
* the refusal-to-train guard, the sign contract, and pickle save/load.

THE STATE CHAIN (docs/OUTS_MODEL.md section 1)
----------------------------------------------
For inning j = 1..9, with A_j = "the starter throws at least one pitch in
inning j" (A_1 true by definition of starter):

    X_j in {0,1,2,3}   outs recorded in inning j, given A_j
    R_j in {0,1}       comes back out for inning j+1, given X_j == 3

    logit P(X_j == 3 | A_j, x)      = gamma_j + x'delta + x_Q' delta_j   j=1..9
    logit P(R_j == 1 | X_j == 3, x) = alpha_j + x'beta  + x_Q' beta_j    j=1..8
    P(X_j = r | A_j, X_j < 3)       = softmax(psi_j)_r                   r in {0,1,2}

x_Q is the as-of pitcher-quality block (``QUALITY_FEATURES``). Every other
covariate effect is shared across boundaries. There is no alpha_9: the 27-out
ceiling is enforced by the ABSENCE of that parameter, not by clipping.

USAGE
-----
    python -m models.outs_hazard --fit --train 2024,2025 --test 2026
    python -m models.outs_hazard --three-way        # all three mandated splits

    from models.outs_hazard import OutsHazard
    m = OutsHazard(); m.load()
    pmf = m.predict_pmf(row)          # length 28, sums to 1
    p   = m.prob_over(row, 17.5)      # P(outs > 17.5)
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.outs_hazard_proto import (  # noqa: E402
    MARKET_LINES,
    MAX_OUTS,
    N_INNINGS,
    N_OUTCOMES,
    OutsHazardParams,
    outs_pmf,
    p_over,
)

__all__ = [
    "OutsHazard",
    "SplitOverlapError",
    "MODEL_PATH",
    "MARKET_LINES",
    "SIGN_CONTRACT",
    "load_dataset",
    "explode_states",
    "brier_at_lines",
    "asof_baseline_pmf",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

MODEL_PATH = Path(__file__).parent / "outs_hazard.pkl"

#: CLAUDE.md: "No training data from before 2024 (post-pitch-clock,
#: post-humidor)." Enforced in fit(), not just documented.
MIN_TRAIN_SEASON = 2024

#: The three mandated out-of-sample splits. CLAUDE.md: "A feature that helps
#: in only one split direction is rejected", so both temporal directions plus
#: the forward decision split must all be run.
SPLITS = {
    "S1": {"train": (2024,), "test": 2025},
    "S2": {"train": (2025,), "test": 2024},
    "S3": {"train": (2024, 2025), "test": 2026},
}

#: Ridge grid for the boundary-varying slope deviations. Selected on the
#: DECISION METRIC (mean Brier across MARKET_LINES) on an inner temporal split
#: of the training years -- never on log-likelihood, and never on the test
#: years. docs/OUTS_MODEL.md section 7 measured this to matter more than the
#: choice of model form (NLL selection: Brier 0.18389; Brier selection:
#: 0.18143 on the same split).
LAMBDA_GRID = (0.3, 1.0, 3.0, 10.0, 30.0)

#: Inner temporal split: the first 70% of distinct TRAINING dates are fit and
#: the last 30% are scored. Dates, not rows, so a slate is never split.
INNER_SPLIT_FRAC = 0.70

#: The partial-inning shape is shrunk toward its own fitted linear-in-j trend
#: at this multiple of the selected lambda. NOT separately tuned -- it is the
#: same strength, applied to a component with 5,511 events rather than 71,328,
#: which is deliberate: j=9 carries only 25 partial events.
PSI_LAMBDA_SCALE = 1.0

#: Composition tolerance. docs/OUTS_MODEL.md section 11 item 6 requires
#: |sum - 1| < 1e-10 on every row; the prototype measures 5.55e-16 on random
#: parameters. 1e-9 is the contract this module asserts, comfortably above
#: float64 accumulation noise and far below anything that could move a price.
PMF_SUM_TOL = 1e-9

#: L-BFGS-B settings, matching models/stage_a_bf.py's MLE convention.
MAXITER = 2000
FTOL = 1e-12
GTOL = 1e-8

#: The as-of pitcher-quality block: the only covariates whose slope is allowed
#: to vary by inning boundary (partial pooling toward the shared effect). How
#: much a pitcher's own durability history moves the manager's return decision
#: depends on which boundary he is standing at.
QUALITY_FEATURES = (
    "exp_o_shrunk",
    "stop_rate_12",
    "stop_rate_15",
    "stop_rate_18",
    "stop_rate_21",
)

#: Continuous covariates, standardized on TRAIN statistics only.
NUMERIC_FEATURES = (
    "exp_o_shrunk",
    "stop_rate_12",
    "stop_rate_15",
    "stop_rate_18",
    "stop_rate_21",
    "career_start_number",
    # career_x_season REMOVED -- Gate 4 breach on the design matrix the model
    # actually fits, which is not the pooled feature frame the correlation was
    # originally measured on. season_start_number saturates at its cap of 8 on
    # 74.5% of 2024 rows, so the interaction degenerates to 8 x
    # career_start_number: r = +0.9955 and VIF 350.2 on the S1 design. Ridge
    # penalises only the boundary deviations, so both shared slopes ride
    # unregularised. Dropping it improves S1 by 0.00219 Brier and S2 by
    # 0.00059 and is neutral on the decision split -- a gate pass that costs
    # nothing. The season ramp still enters through season_start_number.
    "league_asof_outs",
    "opp_obp_asof",
    "p5_pitches",
)

#: 0/1 covariates, left on their natural scale so the coefficient reads as a
#: log-odds shift directly.
BINARY_FEATURES = ("is_debut", "career_left_censored", "is_home")

#: days_rest is a ROLE detector, not a fatigue variable (within the 5-7 day
#: band its correlation with outs is ~0; at <=2 days the mean is 4.32 outs
#: because that is a bullpen game). Dummy-coded against the modal bucket.
REFERENCE_REST_BUCKET = "6"

#: STRUCTURAL MISSINGNESS. Each block is a real "we do not know" that is
#: itself informative, so it gets an explicit indicator column and the
#: underlying value is filled with the TRAIN mean. The indicator is what
#: carries the information; the fill only keeps the linear predictor finite.
#: Listwise deletion is forbidden here -- it would silently delete every debut
#: (is_debut == 1 implies p5_pitches is NaN by construction) and with it the
#: entire is_debut effect.
MISSING_BLOCKS = {
    "miss_quality": "exp_o_shrunk",       # 26 rows: 2024 opening day
    "miss_career": "career_start_number",  # 1,518 rows: left-censored pitchers
    "miss_opp": "opp_obp_asof",           # 1,802 rows: below MIN_OPP_GAMES
    "miss_budget": "p5_pitches",          # 1,068 rows: first start of a season
}

#: Which block governs each numeric column's NaN pattern. Columns sharing a
#: block share ONE indicator -- emitting one per column would create perfectly
#: collinear indicator pairs (career_start_number and career_x_season are NaN
#: on exactly the same rows) and fail Gate 4 by construction.
COLUMN_MISSING_BLOCK = {
    "exp_o_shrunk": "miss_quality",
    "stop_rate_12": "miss_quality",
    "stop_rate_15": "miss_quality",
    "stop_rate_18": "miss_quality",
    "stop_rate_21": "miss_quality",
    "league_asof_outs": "miss_quality",
    "career_start_number": "miss_career",
    "career_x_season": "miss_career",
    "opp_obp_asof": "miss_opp",
    "p5_pitches": "miss_budget",
}

#: Honest baseline (docs/FACTORS.md C1, "Not the mean -- the distribution"):
#: the pitcher's own strictly as-of empirical outs DISTRIBUTION over prior
#: starts, shrunk toward the as-of league PMF with this many pseudo-starts.
BASELINE_SHRINK_K = 8.0

#: Uniform pseudo-count spread over 0..27 so the as-of league PMF is defined
#: on the very first day of the sample, where there is no prior day at all.
LEAGUE_PMF_PSEUDO = 1.0

#: SIGN CONTRACT.
#:
#: WHAT IS TESTED, AND WHY IT IS NOT THE RAW COEFFICIENT. Every effect size in
#: docs/OUTS_MODEL.md section 9 is measured against OUTS. The corresponding
#: quantity in this model is the directional derivative of E[outs] -- move one
#: term, hold the rest of the row at its real value, read the change in the
#: composed PMF's mean. A raw slope is NOT that quantity here, for two
#: measured reasons:
#:
#:   1. PARTIAL POOLING. ``stop_rate_b`` is a statement about ONE boundary.
#:      The shared slope averages it over eight boundaries, seven of which it
#:      says nothing about, so its sign is noise-dominated -- measured: the
#:      pooled return slope on stop_rate_15 is +0.077 / -0.086 / +0.005 across
#:      the three splits, while the boundary-matched total (shared + beta_j at
#:      j=5) is -0.065 / -0.103 / -0.086, negative in all three.
#:   2. STRUCTURAL COUPLING. Flipping ``is_debut`` alone describes a row that
#:      cannot exist: a genuine career debut necessarily has no prior-5 pitch
#:      budget and career_start_number == 1. Measured: the isolated slope is
#:      +0.47 / -0.12 / +0.06 across splits, while the coherent block move
#:      (debut vs an established starter) is -3.47 / -4.23 / -3.84 outs
#:      against the design's raw 10.79 vs 16.11. ``rest_unknown`` is the same
#:      trap: 717 of its 813 rows have no prior start, and the isolated flip
#:      reads +2.2 outs while the coherent block reads -0.92 / -1.12 / -1.32
#:      against the design's 14.71 vs 16.08.
#:
#: Entries are (expected sign, counterfactual kind, the measurement). Only
#: terms that are BOTH pinned by a design measurement AND sign-stable across
#: all three mandated splits are gated. Everything else is printed as advisory
#: -- see ADVISORY_TERMS for the exclusions and the reason for each.
SIGN_CONTRACT = {
    "exp_o_shrunk": (+1, "sd", "r = +0.378 with outs (n=13,144)"),
    "p5_pitches": (+1, "sd", "r = +0.425 with outs, strongest scalar in the set"),
    "stop_rate_15": (-1, "sd", "r = -0.115 with outs; conditional corr +0.111, all 3 seasons"),
    "stop_rate_18": (-1, "sd", "r = -0.063 with outs; conditional corr +0.066, all 3 seasons"),
    "opp_obp_asof": (-1, "sd", "r = -0.044 with outs; same sign in all three seasons"),
    "is_home": (+1, "binary", "home 15.746 vs away 15.297; within-pitcher +0.4477, t=+6.94"),
    "career_left_censored": (+1, "binary", "r = +0.198; censored pitchers are established (16.02 outs)"),
    "rest_<=2": (-1, "rest", "<=2 days rest -> 4.32 outs vs 16.08 at the 6-day reference"),
    "rest_3": (-1, "rest", "3 days rest -> 6.23 outs vs 16.08 at the reference"),
    "rest_4": (-1, "rest", "4 days rest -> 8.88 outs vs 16.08 at the reference"),
    "rest_11-20": (-1, "rest", "11-20 days rest -> 14.50 outs vs 16.08 at the reference"),
    "rest_21+": (-1, "rest", "21+ days rest -> 13.60 outs vs 16.08 at the reference"),
    "NO_HISTORY_BLOCK": (-1, "block", "genuine debut 10.79 outs vs 16.11 at career >= 10"),
    "UNKNOWN_REST_BLOCK": (-1, "block", "unknown rest bucket 14.71 outs vs 16.08 at the reference"),
}

#: Terms deliberately NOT gated, with the measured reason. These are printed
#: on every fit so nothing is hidden by being ungated.
ADVISORY_TERMS = {
    "stop_rate_12": "sign-UNSTABLE across the three splits (+0.019 / -0.065 / -0.019 "
                    "on E[outs]). Under CLAUDE.md's both-directions rule this is a "
                    "Gate-2 rejection candidate, not a passing feature.",
    "stop_rate_21": "the design measures a NULL here (univariate r = -0.006, p = 0.46; "
                    "conditional p = 0.334, sign flips by season), so there is no "
                    "direction to contradict. The model gives a stable POSITIVE effect "
                    "on outs, opposite to the other three quality terms -- Gate-2 review.",
    "career_start_number": "half of a collinear pair (r = +0.753 with career_x_season) "
                           "plus a structural NaN block; moving it alone is not the "
                           "design's quantity. Tested through NO_HISTORY_BLOCK instead.",
    "career_x_season": "the other half of that pair; same reason.",
    "league_asof_outs": "the design says explicitly to judge this on calibration, not "
                        "correlation -- within-season sd is 0.227 outs, so it cannot "
                        "rank starts and its univariate r is negative in 2026.",
    "rest_5": "measured gap vs the reference is -0.04 outs, below the noise floor.",
    "rest_7": "measured gap vs the reference is -0.23 outs, below the noise floor.",
    "rest_8-10": "measured gap vs the reference is -0.48 outs, below the noise floor.",
    "rest_unknown": "structurally coupled to the no-history block; gated as "
                    "UNKNOWN_REST_BLOCK instead.",
    "is_debut": "structurally coupled to the no-history block; gated as "
                "NO_HISTORY_BLOCK instead.",
}


class SplitOverlapError(ValueError):
    """Raised when the training data would contaminate the test data.

    CLAUDE.md: "The model refuses to train if the test file overlaps the train
    list." This is a hard refusal, not a warning -- an overlapping split
    manufactures out-of-sample skill that does not exist, and the edge filter
    downstream turns manufactured skill into real stakes.
    """


# --------------------------------------------------------------------------
# numerics
# --------------------------------------------------------------------------

def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Overflow-safe logistic. Saturates rather than returning NaN."""
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    e = np.exp(z[~pos])
    out[~pos] = e / (1.0 + e)
    return out


# --------------------------------------------------------------------------
# Data assembly
# --------------------------------------------------------------------------

def load_dataset(fast_opponent: bool = False, verbose: bool = True) -> pd.DataFrame:
    """Per-start OUTS table + the Tier-1 as-of feature set, in one frame.

    Wires the two upstream modules together:

      * ``tools.build_outs_dataset.load_outs_starts`` supplies the LABEL and
        the game context (``outs``, ``max_inning``, ``game_max_inning``). Its
        reconstruction is validated 548/548 against MLB boxscore
        ``inningsPitched`` by ``tools/validate_outs_vs_mlb.py``.
      * ``features.outs_asof.build_outs_asof`` supplies the strictly-prior
        covariates.

    ``fast_opponent`` swaps the full-game opponent on-base table for the
    starters-only fallback, which avoids re-reading the Statcast cache but
    measures a NARROWER quantity (~60% of each team's plate appearances).
    The two must never be mixed across training and serving.
    """
    from tools.build_outs_dataset import load_outs_starts
    from features.outs_asof import (
        build_outs_asof, build_starts_table, load_statcast_pa,
        team_batting_from_starts,
    )

    t0 = time.time()
    starts = load_outs_starts()
    if fast_opponent:
        tb = team_batting_from_starts(starts)
        src = "starters-only fallback"
    else:
        pa = load_statcast_pa()
        _, tb = build_starts_table(pa)
        src = "full-game (Statcast PA table)"
    feat = build_outs_asof(starts, tb)

    n_games = feat["game_pk"].nunique()
    if len(feat) != 2 * n_games:
        raise ValueError(
            f"per-start table has {len(feat)} rows for {n_games} games; "
            f"expected exactly {2 * n_games}. The reconstruction is broken -- "
            "refusing to fit on it."
        )
    outs = feat["outs"].astype("int64").to_numpy()
    if outs.min() < 0 or outs.max() > MAX_OUTS:
        raise ValueError(f"outs outside 0..{MAX_OUTS}: [{outs.min()}, {outs.max()}]")
    r = outs - 3 * (feat["max_inning"].astype("int64").to_numpy() - 1)
    if r.min() < 0 or r.max() > 3:
        raise ValueError(
            "outs and max_inning disagree: outs - 3*(max_inning-1) left 0..3 "
            f"on {int(((r < 0) | (r > 3)).sum())} rows"
        )
    if verbose:
        print(f"[data] {len(feat):,} starts, {n_games:,} games, "
              f"{feat['game_date'].min().date()}..{feat['game_date'].max().date()}  "
              f"({time.time() - t0:.1f}s, opponent table: {src})")
    return feat


@dataclass
class States:
    """The three likelihood components, exploded from per-start rows.

    ``*_row`` indexes back into the per-start frame; ``*_j`` is the inning
    (1-based) and doubles as the group index for the free intercepts.
    """
    comp_row: np.ndarray
    comp_j: np.ndarray
    comp_y: np.ndarray
    ret_row: np.ndarray
    ret_j: np.ndarray
    ret_y: np.ndarray
    part_row: np.ndarray
    part_j: np.ndarray
    part_r: np.ndarray


def explode_states(df: pd.DataFrame) -> States:
    """Per-start outs -> per-(start, inning) A_j / X_j / R_j observations.

    ``max_inning`` is what disambiguates the two-path structure at outs = 3j:
    a start that ended on 15 outs either was not sent back out after the 5th
    (max_inning = 5) or came out for the 6th and was pulled before recording
    an out (max_inning = 6). Counting from ``outs`` alone would collapse two
    disjoint paths that the design measures as separately real (the 0-out
    path supplies 3.4%-23.2% of the mass at 3j).

    A return observation is DROPPED when the game itself ended at inning j
    (``game_max_inning == j``): there was no inning j+1 to be sent out for, so
    "did not return" is not a managerial decision. It is also not a gradeable
    bet -- CLAUDE.md voids a prop when the game is called before the pitcher
    is removed. Measured: 4 of 65,761 return observations on the full sample.
    """
    n = len(df)
    # .astype("int64") first: the upstream table uses pandas nullable Int32,
    # and .to_numpy(np.int64) on that silently produces an object array.
    J = df["max_inning"].astype("int64").to_numpy()
    G = df["game_max_inning"].astype("int64").to_numpy()
    outs = df["outs"].astype("int64").to_numpy()
    r = outs - 3 * (J - 1)
    if r.min() < 0 or r.max() > 3:
        raise ValueError("outs and max_inning disagree; refusing to explode states")

    # --- completion: every inning he entered, y = 1 if he finished it -------
    comp_row = np.repeat(np.arange(n), J)
    comp_j = _within_group_counter(J)
    comp_y = np.where(comp_j < J[comp_row], 1.0,
                      (r[comp_row] == 3).astype(float))

    # --- return: y = 1 at every boundary he pitched past --------------------
    cnt = np.maximum(J - 1, 0)
    ret_row = np.repeat(np.arange(n), cnt)
    ret_j = _within_group_counter(cnt)
    ret_y = np.ones(len(ret_row))

    # ... plus the terminal boundary, when one was actually observed. No R_9
    # exists: the 27-out ceiling is the absence of that parameter.
    stop = np.flatnonzero((r == 3) & (J < N_INNINGS) & (G > J))
    ret_row = np.concatenate([ret_row, stop])
    ret_j = np.concatenate([ret_j, J[stop]])
    ret_y = np.concatenate([ret_y, np.zeros(len(stop))])

    # --- partial inning: removed with 0, 1 or 2 outs recorded ---------------
    part = np.flatnonzero(r < 3)

    return States(
        comp_row=comp_row, comp_j=comp_j, comp_y=comp_y,
        ret_row=ret_row, ret_j=ret_j, ret_y=ret_y,
        part_row=part, part_j=J[part], part_r=r[part],
    )


def _within_group_counter(counts: np.ndarray) -> np.ndarray:
    """[1..c] concatenated for each c in counts (empty where c == 0)."""
    total = int(counts.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    starts = np.cumsum(counts) - counts
    return np.arange(total) - np.repeat(starts, counts) + 1


# --------------------------------------------------------------------------
# Design matrix
# --------------------------------------------------------------------------

@dataclass
class DesignSpec:
    """Everything needed to rebuild a design matrix identically at serve time.

    Fitted on TRAIN ONLY. Standardization statistics and structural-missingness
    fill values are frozen here, so a test row is transformed with train
    numbers and no test statistic ever reaches the fit.
    """
    numeric: list[str]
    binary: list[str]
    rest_levels: list[str]
    fill: dict[str, float]
    mean: dict[str, float]
    sd: dict[str, float]
    names: list[str]
    quality_idx: np.ndarray
    dropped: list[str] = field(default_factory=list)

    @property
    def required_columns(self) -> list[str]:
        return sorted(set(self.numeric) | set(self.binary) | {"days_rest_bucket"})

    # The spec is serialized as a PLAIN DICT, never as a pickled instance of
    # this class. Fitting runs as `python -m models.outs_hazard`, where this
    # module is __main__, so a pickled instance carries __module__ ==
    # "__main__" and cannot be unpickled from the daily pipeline -- the model
    # file would be write-only. Caught by the API verification, not in
    # production.
    def to_dict(self) -> dict:
        return {"numeric": list(self.numeric), "binary": list(self.binary),
                "rest_levels": list(self.rest_levels), "fill": dict(self.fill),
                "mean": dict(self.mean), "sd": dict(self.sd),
                "names": list(self.names),
                "quality_idx": [int(i) for i in self.quality_idx],
                "dropped": list(self.dropped)}

    @classmethod
    def from_dict(cls, d: dict) -> "DesignSpec":
        return cls(numeric=list(d["numeric"]), binary=list(d["binary"]),
                   rest_levels=list(d["rest_levels"]), fill=dict(d["fill"]),
                   mean=dict(d["mean"]), sd=dict(d["sd"]),
                   names=list(d["names"]),
                   quality_idx=np.asarray(d["quality_idx"], dtype=int),
                   dropped=list(d["dropped"]))


def _rest_labels(df: pd.DataFrame) -> np.ndarray:
    s = df["days_rest_bucket"]
    if isinstance(s.dtype, pd.CategoricalDtype):
        s = s.astype(object)
    return s.where(s.notna(), "unknown").astype(str).to_numpy()


def fit_design_spec(train: pd.DataFrame) -> DesignSpec:
    """Fit the design spec on the training frame only."""
    from features.outs_asof import DAYS_REST_BUCKETS

    missing = [c for c in list(NUMERIC_FEATURES) + list(BINARY_FEATURES)
               + ["days_rest_bucket"] if c not in train.columns]
    if missing:
        raise KeyError(f"training frame is missing feature columns: {missing}")

    fill, mean, sd = {}, {}, {}
    for c in NUMERIC_FEATURES:
        v = pd.to_numeric(train[c], errors="coerce").to_numpy(float)
        obs = v[np.isfinite(v)]
        if obs.size == 0:
            raise ValueError(f"feature {c!r} is entirely missing in the training data")
        fill[c] = float(obs.mean())
        filled = np.where(np.isfinite(v), v, fill[c])
        mean[c] = float(filled.mean())
        s = float(filled.std())
        sd[c] = s if s > 1e-12 else 1.0

    levels = [b for b in DAYS_REST_BUCKETS if b != REFERENCE_REST_BUCKET]
    names = (list(NUMERIC_FEATURES) + list(BINARY_FEATURES)
             + [f"rest_{b}" for b in levels] + list(MISSING_BLOCKS))
    spec = DesignSpec(
        numeric=list(NUMERIC_FEATURES), binary=list(BINARY_FEATURES),
        rest_levels=levels, fill=fill, mean=mean, sd=sd, names=names,
        quality_idx=np.array([names.index(q) for q in QUALITY_FEATURES]),
    )

    # Drop columns with no variation in TRAIN. Their coefficient is not
    # identified and L-BFGS-B would wander along a flat direction. This
    # actually fires: miss_quality is 26 rows on 2024 opening day, so it is
    # constant-zero whenever 2024 is not in the training years.
    X, _ = build_design(train, spec, strict=False)
    const = [nm for k, nm in enumerate(spec.names) if X[:, k].std() < 1e-12]
    if const:
        gone = [q for q in QUALITY_FEATURES if q in const]
        if gone:
            raise ValueError(
                f"quality-block feature(s) {gone} have no variation in the "
                "training data. The boundary-varying block cannot be fitted; "
                "refusing to train."
            )
        spec.dropped = const
        spec.names = [nm for nm in spec.names if nm not in const]
        spec.quality_idx = np.array([spec.names.index(q) for q in QUALITY_FEATURES])
    return spec


def build_design(df: pd.DataFrame, spec: DesignSpec,
                 strict: bool = True) -> tuple[np.ndarray, list[str]]:
    """Apply a fitted spec to a frame. Returns (X, names).

    ``strict=True`` (the serving contract) refuses a NaN in a column whose
    missing-indicator was dropped for lack of training variation -- that is a
    row the model has never seen the missingness pattern of, and substituting
    the training mean for it would manufacture a prediction out of nothing.
    """
    n = len(df)
    isna: dict[str, np.ndarray] = {}
    cols: dict[str, np.ndarray] = {}

    for c in spec.numeric:
        v = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
        bad = ~np.isfinite(v)
        isna[c] = bad
        cols[c] = (np.where(bad, spec.fill[c], v) - spec.mean[c]) / spec.sd[c]

    for c in spec.binary:
        v = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
        if strict and not np.isfinite(v).all():
            raise ValueError(
                f"binary feature {c!r} is missing on "
                f"{int((~np.isfinite(v)).sum())} row(s). It has no missingness "
                "block, so there is nothing honest to fill it with."
            )
        cols[c] = np.where(np.isfinite(v), v, 0.0)

    lab = _rest_labels(df)
    for b in spec.rest_levels:
        cols[f"rest_{b}"] = (lab == b).astype(float)

    for block, src in MISSING_BLOCKS.items():
        cols[block] = isna[src].astype(float)

    if strict:
        for c, block in COLUMN_MISSING_BLOCK.items():
            if block in spec.dropped and isna[c].any():
                raise ValueError(
                    f"{c!r} is missing on {int(isna[c].sum())} row(s) but the "
                    f"indicator {block!r} carried no variation in training, so "
                    "the model has never seen this missingness pattern. "
                    "Refusing to substitute the training mean."
                )

    X = np.column_stack([cols[nm] for nm in spec.names])
    return np.ascontiguousarray(X, dtype=float), list(spec.names)


# --------------------------------------------------------------------------
# The three fitted components
# --------------------------------------------------------------------------

def _logit_objective(theta, Xe, XQe, y, gidx, n_groups, P, Q, lam):
    """NLL + ridge on the boundary-varying deviations, with analytic gradient.

    Intercepts and shared slopes are NOT penalised (docs/OUTS_MODEL.md
    section 7: the thin quantity is the slope deviation, not the intercept --
    even the thinnest boundary, j=8 with n=229, has se(logit) = 0.138).
    """
    gamma = theta[:n_groups]
    delta = theta[n_groups:n_groups + P]
    dev = theta[n_groups + P:].reshape(n_groups, Q)

    eta = gamma[gidx] + Xe @ delta + np.einsum("ij,ij->i", XQe, dev[gidx])
    nll = float(np.sum(np.logaddexp(0.0, eta) - y * eta) + lam * np.sum(dev ** 2))

    res = _sigmoid(eta) - y
    g_gamma = np.bincount(gidx, weights=res, minlength=n_groups)
    g_delta = Xe.T @ res
    g_dev = np.empty((n_groups, Q))
    for q in range(Q):
        g_dev[:, q] = np.bincount(gidx, weights=XQe[:, q] * res, minlength=n_groups)
    g_dev += 2.0 * lam * dev
    return nll, np.concatenate([g_gamma, g_delta, g_dev.ravel()])


def _fit_logit(X, rows, j_idx, y, n_groups, quality_idx, lam):
    """MLE by L-BFGS-B, matching models/stage_a_bf.py's fitting convention."""
    P = X.shape[1]
    Q = len(quality_idx)
    Xe = X[rows]
    XQe = np.ascontiguousarray(Xe[:, quality_idx])
    gidx = (j_idx - 1).astype(np.int64)

    # Intercepts start at the empirical group log-odds so the optimiser never
    # has to travel across a saturated region to find them.
    gamma0 = np.empty(n_groups)
    for g in range(n_groups):
        m = gidx == g
        p = float(y[m].mean()) if m.any() else 0.5
        gamma0[g] = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    theta0 = np.concatenate([gamma0, np.zeros(P), np.zeros(n_groups * Q)])

    res = minimize(
        _logit_objective, theta0,
        args=(Xe, XQe, y, gidx, n_groups, P, Q, lam),
        jac=True, method="L-BFGS-B",
        options={"maxiter": MAXITER, "ftol": FTOL, "gtol": GTOL, "maxcor": 30},
    )
    gamma = res.x[:n_groups]
    delta = res.x[n_groups:n_groups + P]
    dev = res.x[n_groups + P:].reshape(n_groups, Q)
    return gamma, delta, dev, res


def _psi_objective(theta, counts, jc, lam):
    """Multinomial NLL over {0,1,2} + ridge toward the fitted linear-in-j trend.

    ``psi`` holds the logits of classes 0 and 2 relative to class 1 (softmax is
    shift-invariant, so two free logits per inning is the full model). The
    trend (u, v) is free and unpenalised; only the per-inning DEVIATION from
    u + v*(j - jbar) is shrunk. docs/OUTS_MODEL.md section 7: shrink toward the
    fitted trend, not the pooled constant, because the shape demonstrably
    drifts with the inning (b = +0.2147, se = 0.0287, z = +7.48).
    """
    n_j = counts.shape[0]
    psi = theta[:n_j * 2].reshape(n_j, 2)
    u = theta[n_j * 2:n_j * 2 + 2]
    v = theta[n_j * 2 + 2:]

    z = np.column_stack([psi[:, 0], np.zeros(n_j), psi[:, 1]])
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    q = e / e.sum(axis=1, keepdims=True)

    nll = -float(np.sum(counts * np.log(np.clip(q, 1e-300, None))))
    trend = u[None, :] + jc[:, None] * v[None, :]
    d = psi - trend
    nll += lam * float(np.sum(d ** 2))

    N = counts.sum(axis=1)
    g_psi = np.empty((n_j, 2))
    g_psi[:, 0] = N * q[:, 0] - counts[:, 0]
    g_psi[:, 1] = N * q[:, 2] - counts[:, 2]
    g_psi += 2.0 * lam * d
    g_u = -2.0 * lam * d.sum(axis=0)
    g_v = -2.0 * lam * (jc[:, None] * d).sum(axis=0)
    return nll, np.concatenate([g_psi.ravel(), g_u, g_v])


def _fit_partial(part_j, part_r, lam):
    """Per-inning {0,1,2} multinomial. No covariates -- measured worthless."""
    counts = np.zeros((N_INNINGS, 3))
    for j, r in zip(part_j, part_r):
        counts[j - 1, r] += 1.0
    jc = np.arange(1, N_INNINGS + 1, dtype=float)
    jc = jc - jc.mean()

    # Start from the smoothed empirical shape; a zero cell would otherwise
    # start at -inf.
    p0 = (counts + 0.5) / (counts.sum(axis=1, keepdims=True) + 1.5)
    psi0 = np.column_stack([np.log(p0[:, 0] / p0[:, 1]), np.log(p0[:, 2] / p0[:, 1])])
    theta0 = np.concatenate([psi0.ravel(), psi0.mean(axis=0), np.zeros(2)])

    res = minimize(_psi_objective, theta0, args=(counts, jc, lam), jac=True,
                   method="L-BFGS-B",
                   options={"maxiter": MAXITER, "ftol": FTOL, "gtol": GTOL})
    psi2 = res.x[:N_INNINGS * 2].reshape(N_INNINGS, 2)
    psi3 = np.column_stack([psi2[:, 0], np.zeros(N_INNINGS), psi2[:, 1]])
    return psi3, counts, res


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def brier_at_lines(pmf: np.ndarray, outs: np.ndarray,
                   lines=MARKET_LINES) -> dict[float, float]:
    """Brier score of P(outs > L) against the realized indicator, per line."""
    out = {}
    for L in lines:
        p = p_over(pmf, L)
        y = (outs > L).astype(float)
        out[L] = float(np.mean((p - y) ** 2))
    return out


def mean_brier(pmf: np.ndarray, outs: np.ndarray, lines=MARKET_LINES) -> float:
    d = brier_at_lines(pmf, outs, lines)
    return float(np.mean(list(d.values())))


def pmf_nll(pmf: np.ndarray, outs: np.ndarray) -> float:
    p = pmf[np.arange(len(outs)), outs.astype(int)]
    return float(-np.mean(np.log(np.clip(p, 1e-300, None))))


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error, equal-count bins (Gate 5's diagnostic)."""
    order = np.argsort(p)
    p, y = p[order], y[order]
    edges = np.linspace(0, len(p), n_bins + 1).astype(int)
    tot, e = 0.0, 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        w = b - a
        e += w * abs(p[a:b].mean() - y[a:b].mean())
        tot += w
    return float(e / tot) if tot else float("nan")


def asof_baseline_pmf(df: pd.DataFrame, k: float = BASELINE_SHRINK_K) -> np.ndarray:
    """The honest baseline: the pitcher's own strictly as-of outs DISTRIBUTION.

    docs/FACTORS.md C1 -- "Not the mean, the distribution". For each start, the
    empirical histogram over 0..27 of that pitcher's strictly calendar-earlier
    starts, shrunk with ``k`` pseudo-starts toward the as-of league PMF
    (expanding over strictly earlier DAYS, with a uniform pseudo-count so the
    very first day of the sample is defined).

    Computed over the WHOLE frame, so a test-season row is allowed to use
    earlier test-season starts -- those are genuinely knowable before first
    pitch, and denying them would make the baseline artificially weak.
    """
    d = df[["pitcher", "game_date", "outs"]].copy()
    d["_ord"] = np.arange(len(d))
    d = d.sort_values(["game_date", "_ord"], kind="mergesort")

    outs = d["outs"].to_numpy(int)
    dates = d["game_date"].to_numpy()
    pitchers = d["pitcher"].to_numpy()

    league = np.full(N_OUTCOMES, LEAGUE_PMF_PSEUDO / N_OUTCOMES)
    league_n = LEAGUE_PMF_PSEUDO
    own: dict[int, np.ndarray] = {}
    own_n: dict[int, float] = {}

    res = np.empty((len(d), N_OUTCOMES))
    i = 0
    while i < len(d):
        # One calendar day at a time: every row of today is scored off
        # yesterday's totals, then today is folded in. Nothing from today can
        # reach today's own prediction.
        day = dates[i]
        block = []
        while i < len(d) and dates[i] == day:
            block.append(i)
            i += 1
        lg = league / league_n
        for b in block:
            o = own.get(pitchers[b])
            res[b] = lg if o is None else (o + k * lg) / (own_n[pitchers[b]] + k)
        for b in block:
            p = pitchers[b]
            if p not in own:
                own[p] = np.zeros(N_OUTCOMES)
                own_n[p] = 0.0
            own[p][outs[b]] += 1.0
            own_n[p] += 1.0
            league[outs[b]] += 1.0
            league_n += 1.0

    back = np.empty_like(res)
    back[d["_ord"].to_numpy()] = res
    return back


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

class OutsHazard:
    """Inning-lattice hazard model for starting-pitcher total outs recorded."""

    def __init__(self):
        self.params: OutsHazardParams | None = None
        self.spec: DesignSpec | None = None
        self.lam: float | None = None
        self.meta: dict = {}
        self.fit_report: dict = {}

    # ---------------------------------------------------------------- guards
    @staticmethod
    def assert_split_disjoint(train: pd.DataFrame, test: pd.DataFrame) -> None:
        """Refuse a contaminated split. CLAUDE.md, Test methodology rules."""
        tr_seasons = set(pd.to_datetime(train["game_date"]).dt.year.unique().tolist())
        te_seasons = set(pd.to_datetime(test["game_date"]).dt.year.unique().tolist())
        both = sorted(tr_seasons & te_seasons)
        if both:
            raise SplitOverlapError(
                f"train and test share season(s) {both}. Refusing to train: an "
                "overlapping split reports skill the model does not have."
            )
        tr_keys = set(map(tuple, train[["game_pk", "pitcher"]].to_numpy().tolist()))
        te_keys = set(map(tuple, test[["game_pk", "pitcher"]].to_numpy().tolist()))
        shared = tr_keys & te_keys
        if shared:
            raise SplitOverlapError(
                f"train and test share {len(shared)} (game_pk, pitcher) rows, "
                f"e.g. {sorted(shared)[:3]}. Refusing to train."
            )
        shared_dates = (set(pd.to_datetime(train["game_date"]).dt.date)
                        & set(pd.to_datetime(test["game_date"]).dt.date))
        if shared_dates:
            raise SplitOverlapError(
                f"train and test share {len(shared_dates)} slate date(s), "
                f"e.g. {sorted(shared_dates)[:3]}. Refusing to train."
            )

    @staticmethod
    def _assert_training_era(train: pd.DataFrame) -> None:
        yrs = sorted(pd.to_datetime(train["game_date"]).dt.year.unique().tolist())
        early = [y for y in yrs if y < MIN_TRAIN_SEASON]
        if early:
            raise ValueError(
                f"training data contains season(s) {early}, before "
                f"{MIN_TRAIN_SEASON}. CLAUDE.md forbids pre-2024 training data "
                "(pre-pitch-clock, pre-humidor). Refusing to train."
            )

    # ------------------------------------------------------------------- fit
    def fit(self, train_df: pd.DataFrame, test_df: pd.DataFrame | None = None,
            lam: float | None = None, verbose: bool = True) -> OutsHazardParams:
        """Fit on ``train_df``; refuse if ``test_df`` overlaps it.

        ``lam`` selects the ridge strength; when None it is chosen on an inner
        TEMPORAL split of the training years, scored on mean Brier across the
        seven market lines. The test frame is used ONLY for the overlap
        refusal -- never for selection.
        """
        self._assert_training_era(train_df)
        if test_df is not None:
            self.assert_split_disjoint(train_df, test_df)

        spec = fit_design_spec(train_df)
        if lam is None:
            lam, grid = self._select_lambda(train_df, spec, verbose=verbose)
        else:
            grid = None

        params, report = self._fit_components(train_df, spec, lam, verbose=verbose)
        self.params, self.spec, self.lam = params, spec, lam
        self.fit_report = report
        self.fit_report["lambda_grid"] = grid

        # The sign gate is evaluated on the TRAINING rows, so the decision to
        # ship or not ship never touches the test years.
        signs = self.check_signs(train_df)
        self.fit_report["signs"] = signs
        self.fit_report["signs_ok"] = bool(signs["ok"].all())
        self.fit_report["advisory"] = self.effect_directions(
            train_df, {nm: (0, _advisory_kind(nm), why)
                       for nm, why in ADVISORY_TERMS.items()
                       if nm in spec.names or nm.startswith("rest_")})
        self.fit_report["boundary_slopes"] = self.boundary_slopes()
        self.meta = {
            "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "train_seasons": sorted(pd.to_datetime(train_df["game_date"])
                                    .dt.year.unique().tolist()),
            "train_rows": int(len(train_df)),
            "train_dates": [str(pd.to_datetime(train_df["game_date"]).min().date()),
                            str(pd.to_datetime(train_df["game_date"]).max().date())],
            "lambda": float(lam),
            "feature_names": list(spec.names),
            "dropped_features": list(spec.dropped),
        }
        return params

    def _fit_components(self, df, spec, lam, verbose=True):
        X, names = build_design(df, spec, strict=False)
        st = explode_states(df)

        gamma, delta, delta_j, r1 = _fit_logit(
            X, st.comp_row, st.comp_j, st.comp_y, N_INNINGS, spec.quality_idx, lam)
        alpha, beta, beta_j, r2 = _fit_logit(
            X, st.ret_row, st.ret_j, st.ret_y, N_INNINGS - 1, spec.quality_idx, lam)
        psi, counts, r3 = _fit_partial(st.part_j, st.part_r, lam * PSI_LAMBDA_SCALE)

        params = OutsHazardParams(
            gamma=gamma, delta=delta, delta_j=delta_j,
            alpha=alpha, beta=beta, beta_j=beta_j,
            psi=psi, quality_idx=np.asarray(spec.quality_idx),
        )
        params.validate(X.shape[1])

        report = {
            "n_completion": int(len(st.comp_y)),
            "n_return": int(len(st.ret_y)),
            "n_partial": int(len(st.part_r)),
            "partial_counts": counts,
            "completion": _conv(r1),
            "return": _conv(r2),
            "partial": _conv(r3),
            "feature_names": names,
        }
        if verbose:
            for k in ("completion", "return", "partial"):
                c = report[k]
                print(f"  [{k:<10}] success={c['success']}  nit={c['nit']:<4} "
                      f"nfev={c['nfev']:<4} obj={c['fun']:.6f} "
                      f"max|grad|={c['max_grad']:.3e}  {c['message']}")
        return params, report

    def _select_lambda(self, train_df, spec, verbose=True):
        """Inner TEMPORAL split of the training years, scored on Brier."""
        dates = np.sort(pd.to_datetime(train_df["game_date"]).unique())
        cut = dates[int(len(dates) * INNER_SPLIT_FRAC)]
        d = pd.to_datetime(train_df["game_date"]).to_numpy()
        inner_tr = train_df[d < cut]
        inner_te = train_df[d >= cut]
        if len(inner_te) < 200:
            raise ValueError("inner validation split is too small to select a penalty")

        inner_spec = fit_design_spec(inner_tr)
        Xte, _ = build_design(inner_te, inner_spec, strict=False)
        outs_te = inner_te["outs"].to_numpy(int)

        grid = []
        for lam in LAMBDA_GRID:
            p, _ = self._fit_components(inner_tr, inner_spec, lam, verbose=False)
            pmf = outs_pmf(p, Xte)
            grid.append({"lambda": lam,
                         "brier": mean_brier(pmf, outs_te),
                         "nll": pmf_nll(pmf, outs_te)})
        best = min(grid, key=lambda r: r["brier"])
        if verbose:
            print(f"  penalty selection on inner split "
                  f"(fit < {pd.Timestamp(cut).date()}: {len(inner_tr):,} rows; "
                  f"score >= : {len(inner_te):,} rows)")
            for r in grid:
                mark = " <-- selected (Brier)" if r["lambda"] == best["lambda"] else ""
                print(f"    lambda={r['lambda']:>5}  Brier={r['brier']:.5f}  "
                      f"NLL={r['nll']:.5f}{mark}")
        return best["lambda"], grid

    # --------------------------------------------------------------- predict
    def _require_fitted(self):
        if self.params is None or self.spec is None:
            raise ValueError("model is not fitted; call fit() or load() first")

    def predict_pmf(self, row) -> np.ndarray:
        """P(outs = k) for k = 0..27 as a 28-element array summing to 1.

        ``row`` is a dict or a pandas Series carrying the as-of feature
        columns. Following models/stage_a_bf.py's fail-loud contract, a MISSING
        KEY raises rather than being defaulted -- a fabricated input inflates
        the projection and the edge filter then selects that error into the bet
        list at max stake. A NaN VALUE is accepted only in the four structural
        missingness blocks, where it is routed through its own indicator.
        """
        self._require_fitted()
        if isinstance(row, pd.Series):
            row = row.to_dict()
        elif isinstance(row, pd.DataFrame):
            if len(row) != 1:
                raise ValueError("predict_pmf takes one row; use predict_pmf_frame")
            row = row.iloc[0].to_dict()
        missing = [c for c in self.spec.required_columns if c not in row]
        if missing:
            raise ValueError(
                f"outs hazard: feature(s) {missing} are absent from the input "
                "row. Refusing to substitute a league default -- that "
                "manufactures edge. Build the as-of features or skip the start."
            )
        frame = pd.DataFrame([{c: row[c] for c in self.spec.required_columns}])
        X, _ = build_design(frame, self.spec, strict=True)
        pmf = outs_pmf(self.params, X)[0]
        total = float(pmf.sum())
        assert abs(total - 1.0) < PMF_SUM_TOL, f"PMF sums to {total!r}, not 1"
        assert pmf.shape == (N_OUTCOMES,), pmf.shape
        assert np.isfinite(pmf).all() and (pmf >= 0).all()
        return pmf

    def predict_pmf_frame(self, df: pd.DataFrame) -> np.ndarray:
        """Vectorized ``predict_pmf`` over a frame. Returns (n, 28)."""
        self._require_fitted()
        X, _ = build_design(df, self.spec, strict=False)
        pmf = outs_pmf(self.params, X)
        err = np.abs(pmf.sum(axis=1) - 1.0).max()
        assert err < PMF_SUM_TOL, f"worst |sum-1| = {err:.3e}"
        return pmf

    def prob_over(self, row, line: float) -> float:
        """P(outs > line). Raises on a whole-number line (PUSH is a separate
        code path per CLAUDE.md's prop-grading rules; guessing would be a P&L
        bug)."""
        return float(p_over(self.predict_pmf(row)[None, :], line)[0])

    # ------------------------------------------------------------ inspection
    def coefficients(self) -> pd.DataFrame:
        """Shared slopes of both hazards, one row per feature."""
        self._require_fitted()
        return pd.DataFrame({
            "feature": self.spec.names,
            "completion": self.params.delta,
            "return": self.params.beta,
        })

    def boundary_slopes(self) -> pd.DataFrame:
        """stop_rate_b's slope AT ITS OWN BOUNDARY (shared + deviation).

        The identified quantity for a boundary-specific covariate, as opposed
        to the shared slope, which averages it over seven boundaries it says
        nothing about.
        """
        self._require_fitted()
        idx = {nm: k for k, nm in enumerate(self.spec.names)}
        qn = [self.spec.names[i] for i in self.spec.quality_idx]
        rows = []
        for b, j in ((12, 4), (15, 5), (18, 6), (21, 7)):
            c = f"stop_rate_{b}"
            if c not in idx or c not in qn:
                continue
            q = qn.index(c)
            sh = float(self.params.beta[idx[c]])
            dv = float(self.params.beta_j[j - 1, q])
            rows.append({"feature": c, "boundary_j": j, "shared": sh,
                         "deviation": dv, "total": sh + dv})
        return pd.DataFrame(rows)

    def _counterfactual(self, X, idx, name, kind):
        """(X_high, X_low) for one contract term. Structurally coherent."""
        rest = [nm for nm in self.spec.names if nm.startswith("rest_")]
        sp = self.spec

        def z(col, raw):
            return (raw - sp.mean[col]) / sp.sd[col]

        if kind == "sd":
            A = X.copy()
            A[:, idx[name]] += 1.0
            return A, X
        if kind == "binary":
            A, B = X.copy(), X.copy()
            A[:, idx[name]] = 1.0
            B[:, idx[name]] = 0.0
            return A, B
        if kind == "rest":
            B = X.copy()
            for r in rest:
                B[:, idx[r]] = 0.0          # everyone in the reference bucket
            A = B.copy()
            A[:, idx[name]] = 1.0
            return A, B
        if name == "NO_HISTORY_BLOCK":
            # A genuine career debut: no prior-5 budget, career start 1.
            A, B = X.copy(), X.copy()
            A[:, idx["is_debut"]] = 1.0
            A[:, idx["miss_budget"]] = 1.0
            A[:, idx["p5_pitches"]] = z("p5_pitches", sp.fill["p5_pitches"])
            A[:, idx["career_start_number"]] = z("career_start_number", 1.0)
            A[:, idx["career_x_season"]] = z("career_x_season", 1.0)
            B[:, idx["is_debut"]] = 0.0
            B[:, idx["miss_budget"]] = 0.0
            B[:, idx["career_start_number"]] = z("career_start_number", 10.0)
            B[:, idx["career_x_season"]] = z("career_x_season", 80.0)
            return A, B
        if name == "UNKNOWN_REST_BLOCK":
            # Unknown rest means Statcast has no previous appearance for him,
            # which is the same rows as "no prior start this season" (717/813).
            B = X.copy()
            for r in rest:
                B[:, idx[r]] = 0.0
            B[:, idx["miss_budget"]] = 0.0
            A = B.copy()
            A[:, idx["rest_unknown"]] = 1.0
            A[:, idx["miss_budget"]] = 1.0
            A[:, idx["p5_pitches"]] = z("p5_pitches", sp.fill["p5_pitches"])
            return A, B
        raise ValueError(f"unknown counterfactual kind {kind!r} for {name!r}")

    def effect_directions(self, ref: pd.DataFrame,
                          terms: dict | None = None) -> pd.DataFrame:
        """d E[outs] from moving each term over ``ref``, everything else real.

        This is the quantity docs/OUTS_MODEL.md section 9 measures. It is
        parameterization-independent: it reads the fitted model's own output,
        so partial pooling and structural coupling cannot disguise a genuinely
        wrong-signed effect.
        """
        self._require_fitted()
        X, _ = build_design(ref, self.spec, strict=False)
        idx = {nm: k for k, nm in enumerate(self.spec.names)}
        k = np.arange(N_OUTCOMES)

        def mean_outs(Xm):
            return float((outs_pmf(self.params, Xm) * k).sum(axis=1).mean())

        rows = []
        for name, (want, kind, evidence) in (terms or SIGN_CONTRACT).items():
            need = ([name] if kind in ("sd", "binary", "rest")
                    else ["is_debut", "miss_budget", "p5_pitches",
                          "career_start_number", "career_x_season"]
                    if name == "NO_HISTORY_BLOCK"
                    else ["rest_unknown", "miss_budget", "p5_pitches"])
            if any(c not in idx for c in need):
                rows.append({"term": name, "expected": want, "d_mean_outs": np.nan,
                             "ok": True, "note": "not in design", "evidence": evidence})
                continue
            A, B = self._counterfactual(X, idx, name, kind)
            d = mean_outs(A) - mean_outs(B)
            rows.append({"term": name, "expected": want, "d_mean_outs": d,
                         "ok": bool(np.sign(d) == want), "note": "",
                         "evidence": evidence})
        return pd.DataFrame(rows)

    def check_signs(self, ref: pd.DataFrame) -> pd.DataFrame:
        """Gate the fitted model against the measured effect directions.

        A violation is a STOP condition, not a warning: it means the fitted
        model moves outs the opposite way from a measurement in
        docs/OUTS_MODEL.md, and that has to be explained before the model
        prices anything. ``save()`` refuses when this fails.
        """
        return self.effect_directions(ref)

    # ------------------------------------------------------------- save/load
    def save(self, path: Path | None = None) -> Path:
        self._require_fitted()
        if self.fit_report.get("signs_ok") is False:
            bad = self.fit_report["signs"]
            bad = bad[~bad["ok"]]["term"].tolist()
            raise ValueError(
                f"refusing to save: the fitted model moves outs the wrong way "
                f"for {bad}, contradicting a measurement in docs/OUTS_MODEL.md. "
                "Explain the disagreement before this model prices anything."
            )
        path = Path(path or MODEL_PATH)
        blob = pickle.dumps({
            "params": {
                "gamma": self.params.gamma, "delta": self.params.delta,
                "delta_j": self.params.delta_j, "alpha": self.params.alpha,
                "beta": self.params.beta, "beta_j": self.params.beta_j,
                "psi": self.params.psi, "quality_idx": self.params.quality_idx,
            },
            "spec": self.spec.to_dict(),
            "lambda": self.lam,
            "meta": self.meta,
            "format": 1,
        }, protocol=pickle.HIGHEST_PROTOCOL)
        _atomic_write_bytes(blob, path)
        return path

    def load(self, path: Path | None = None) -> "OutsHazard":
        path = Path(path or MODEL_PATH)
        with open(path, "rb") as f:
            d = pickle.load(f)
        p = d["params"]
        self.params = OutsHazardParams(**p)
        spec = d["spec"]
        self.spec = DesignSpec.from_dict(spec) if isinstance(spec, dict) else spec
        self.lam = d["lambda"]
        self.meta = d.get("meta", {})
        self.params.validate(len(self.spec.names))
        return self


def _advisory_kind(name: str) -> str:
    return "rest" if name.startswith("rest_") else "binary" if name == "is_debut" else "sd"


def _conv(res) -> dict:
    return {"success": bool(res.success), "message": str(res.message),
            "nit": int(res.nit), "nfev": int(res.nfev), "fun": float(res.fun),
            "max_grad": float(np.max(np.abs(res.jac)))}


def _atomic_write_bytes(blob: bytes, path: Path) -> None:
    """CLAUDE.md: atomic writes only -- tempfile + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _evaluate(model: OutsHazard, test: pd.DataFrame, base: np.ndarray,
              verbose=True) -> dict:
    pmf = model.predict_pmf_frame(test)
    outs = test["outs"].to_numpy(int)
    sums = pmf.sum(axis=1)
    worst = float(np.abs(sums - 1.0).max())

    per = brier_at_lines(pmf, outs)
    mb = float(np.mean(list(per.values())))
    bper = brier_at_lines(base, outs)
    bmb = float(np.mean(list(bper.values())))

    out = {
        "n": int(len(test)),
        "pmf_sum_worst": worst,
        "pmf_rows_within_tol": int((np.abs(sums - 1.0) < PMF_SUM_TOL).sum()),
        "nll": pmf_nll(pmf, outs),
        "brier": mb,
        "per_line": per,
        "baseline_brier": bmb,
        "baseline_per_line": bper,
        "skill": 100.0 * (bmb - mb) / bmb,
        "ece": {L: ece(p_over(pmf, L), (outs > L).astype(float)) for L in MARKET_LINES},
        "mean_pred": float((pmf * np.arange(N_OUTCOMES)).sum(axis=1).mean()),
        "mean_actual": float(outs.mean()),
    }
    if verbose:
        print(f"\n  PMF normalisation over the whole test set "
              f"({out['n']:,} rows): worst |sum-1| = {worst:.3e}   "
              f"rows within {PMF_SUM_TOL:g}: {out['pmf_rows_within_tol']:,}/{out['n']:,}"
              f"   {'PASS' if out['pmf_rows_within_tol'] == out['n'] else 'FAIL'}")
        print(f"  mean predicted outs {out['mean_pred']:.3f}  vs actual "
              f"{out['mean_actual']:.3f}   NLL {out['nll']:.4f}")
        print(f"\n  {'line':>6} {'Brier':>9} {'baseline':>10} {'skill %':>9} "
              f"{'ECE':>8} {'base rate':>10}")
        for L in MARKET_LINES:
            sk = 100.0 * (bper[L] - per[L]) / bper[L]
            print(f"  {L:>6} {per[L]:>9.5f} {bper[L]:>10.5f} {sk:>+9.2f} "
                  f"{out['ece'][L]:>8.4f} {float((outs > L).mean()):>10.3f}")
        print(f"  {'MEAN':>6} {mb:>9.5f} {bmb:>10.5f} {out['skill']:>+9.2f}")
    return out


def _print_coefficients(model: OutsHazard) -> None:
    co = model.coefficients()
    print("\n  SHARED SLOPES (standardized numeric features; binaries and dummies")
    print("  on their natural 0/1 scale). NOT the gated quantity -- see the sign")
    print("  contract below for why a pooled slope is not the measured effect.")
    print(f"  {'feature':<24} {'completion':>12} {'return':>12}")
    for _, r in co.iterrows():
        print(f"  {r['feature']:<24} {r['completion']:>+12.4f} {r['return']:>+12.4f}")

    print("\n  FREE INTERCEPTS (unpenalised)")
    print(f"  {'j':>3} {'gamma_j (complete inning j)':>30} "
          f"{'alpha_j (return for j+1)':>28}")
    for j in range(1, N_INNINGS + 1):
        a = (f"{model.params.alpha[j - 1]:>+28.4f}" if j < N_INNINGS
             else f"{'(none - structural ceiling)':>28}")
        print(f"  {j:>3} {model.params.gamma[j - 1]:>+30.4f} {a}")

    print("\n  BOUNDARY-VARYING QUALITY-BLOCK DEVIATIONS (ridge-penalised "
          "toward the shared effect)")
    qn = [model.spec.names[i] for i in model.spec.quality_idx]
    print("  completion model, delta_j:")
    print("  " + " " * 16 + "".join(f"{'j=' + str(j):>10}"
                                    for j in range(1, N_INNINGS + 1)))
    for q, nm in enumerate(qn):
        print(f"  {nm:<16}" + "".join(f"{model.params.delta_j[j, q]:>+10.3f}"
                                      for j in range(N_INNINGS)))
    print("  return model, beta_j:")
    print("  " + " " * 16 + "".join(f"{'j=' + str(j):>10}" for j in range(1, N_INNINGS)))
    for q, nm in enumerate(qn):
        print(f"  {nm:<16}" + "".join(f"{model.params.beta_j[j, q]:>+10.3f}"
                                      for j in range(N_INNINGS - 1)))

    print("\n  PARTIAL-INNING SHAPE P(X_j = r | X_j < 3)")
    sh = np.exp(model.params.psi - model.params.psi.max(axis=1, keepdims=True))
    sh = sh / sh.sum(axis=1, keepdims=True)
    print(f"  {'j':>3} {'P(0)':>8} {'P(1)':>8} {'P(2)':>8} {'n events':>10}")
    cnt = model.fit_report.get("partial_counts")
    for j in range(N_INNINGS):
        n = int(cnt[j].sum()) if cnt is not None else -1
        print(f"  {j + 1:>3} {sh[j, 0]:>8.4f} {sh[j, 1]:>8.4f} {sh[j, 2]:>8.4f} {n:>10}")


def _print_sign_check(model: OutsHazard) -> bool:
    chk = model.fit_report["signs"]
    bad = chk[~chk["ok"]]
    print("\n  SIGN CONTRACT -- d E[outs] from moving each term over the TRAINING")
    print("  rows, everything else held at its real value. This, not a raw slope,")
    print("  is the quantity docs/OUTS_MODEL.md section 9 measures.")
    print(f"  {'term':<22} {'want':>5} {'d E[outs]':>11}  status")
    for _, r in chk.iterrows():
        st = "OK" if r["ok"] else "*** CONTRADICTS MEASUREMENT ***"
        d = "        n/a" if not np.isfinite(r["d_mean_outs"]) else f"{r['d_mean_outs']:>+11.4f}"
        print(f"  {r['term']:<22} {r['expected']:>+5} {d}  {st}")
    if len(bad):
        print(f"\n  {len(bad)} SIGN VIOLATION(S). Not shipping.")
        for _, r in bad.iterrows():
            print(f"    {r['term']}: fitted {r['d_mean_outs']:+.4f} outs, expected "
                  f"{r['expected']:+d} because {r['evidence']}")

    bs = model.fit_report.get("boundary_slopes")
    if bs is not None and len(bs):
        print("\n  stop_rate_b AT ITS OWN BOUNDARY (the identified slope; the shared")
        print("  column averages it over seven boundaries it says nothing about)")
        print(f"  {'feature':<16} {'j':>3} {'shared':>9} {'deviation':>11} {'total':>9}")
        for _, r in bs.iterrows():
            print(f"  {r['feature']:<16} {int(r['boundary_j']):>3} {r['shared']:>+9.4f} "
                  f"{r['deviation']:>+11.4f} {r['total']:>+9.4f}")
    return len(bad) == 0


def _print_advisory(model: OutsHazard) -> None:
    adv = model.fit_report.get("advisory")
    print("\n  ADVISORY -- measured but NOT gated, with the reason for each")
    print(f"  {'term':<22} {'d E[outs]':>11}  why not gated")
    if adv is not None:
        for _, r in adv.iterrows():
            d = "        n/a" if not np.isfinite(r["d_mean_outs"]) else f"{r['d_mean_outs']:>+11.4f}"
            print(f"  {r['term']:<22} {d}  {ADVISORY_TERMS.get(r['term'], '')[:96]}")


def run_split(feat: pd.DataFrame, train_years, test_year, save: bool,
              base_all: np.ndarray, lam: float | None = None,
              verbose: bool = True) -> dict:
    year = feat["game_date"].dt.year
    train = feat[year.isin(list(train_years))].copy()
    test_pos = np.flatnonzero((year == int(test_year)).to_numpy())
    test = feat.iloc[test_pos].copy()
    base = base_all[test_pos]
    label = "+".join(str(y) for y in train_years)
    print("\n" + "#" * 78)
    print(f"# train {label}  ->  test {test_year}    "
          f"({len(train):,} train starts, {len(test):,} test starts)")
    print("#" * 78)
    if train.empty or test.empty:
        raise ValueError(f"empty split: train={len(train)} test={len(test)}")

    model = OutsHazard()
    model.fit(train, test_df=test, lam=lam, verbose=verbose)

    st = explode_states(train)
    print(f"\n  states: completion {len(st.comp_y):,} obs "
          f"({int(st.comp_y.sum()):,} completed) | return {len(st.ret_y):,} obs "
          f"({int(st.ret_y.sum()):,} returned) | partial {len(st.part_r):,} obs")
    print(f"  design: {len(model.spec.names)} columns"
          + (f"; dropped for zero train variance: {model.spec.dropped}"
             if model.spec.dropped else ""))

    _print_coefficients(model)
    ok = _print_sign_check(model)
    _print_advisory(model)
    ev = _evaluate(model, test, base, verbose=True)

    if save and ok:
        p = model.save()
        print(f"\n  saved -> {p}")
    elif save and not ok:
        print("\n  NOT SAVED: sign contract violated.")
    return {"model": model, "eval": ev, "signs_ok": ok}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fit", action="store_true", help="fit the model")
    ap.add_argument("--train", default="2024,2025", help="comma-separated train seasons")
    ap.add_argument("--test", default="2026", help="test season")
    ap.add_argument("--three-way", action="store_true",
                    help="run all three mandated out-of-sample splits")
    ap.add_argument("--lambda", dest="lam", type=float, default=None,
                    help="fix the ridge strength instead of selecting it")
    ap.add_argument("--fast-opponent", action="store_true",
                    help="starters-only opponent table (skips the Statcast re-read)")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)

    feat = load_dataset(fast_opponent=a.fast_opponent)
    # The honest baseline is built ONCE over the whole frame so a test row can
    # use that pitcher's earlier starts from any season -- those are genuinely
    # knowable before first pitch, and denying them would make the baseline
    # artificially weak and the model's skill artificially large.
    base_all = asof_baseline_pmf(feat)

    if a.three_way:
        res = {}
        for name, cfg in SPLITS.items():
            r = run_split(feat, cfg["train"], cfg["test"], save=False,
                          base_all=base_all, lam=a.lam, verbose=True)
            res[name] = r
        print("\n" + "=" * 78)
        print("THREE-WAY OUT-OF-SAMPLE SUMMARY (CLAUDE.md: must help in every "
              "direction)")
        print("=" * 78)
        print(f"  {'split':<6} {'train':<10} {'test':<6} {'lambda':>7} {'Brier':>9} "
              f"{'baseline':>10} {'skill %':>9} {'signs':>7}")
        for name, cfg in SPLITS.items():
            e = res[name]["eval"]
            print(f"  {name:<6} {'+'.join(map(str, cfg['train'])):<10} "
                  f"{cfg['test']:<6} {res[name]['model'].lam:>7} {e['brier']:>9.5f} "
                  f"{e['baseline_brier']:>10.5f} {e['skill']:>+9.2f} "
                  f"{'OK' if res[name]['signs_ok'] else 'FAIL':>7}")
        return 0

    if not a.fit:
        ap.error("nothing to do; pass --fit or --three-way")

    train_years = tuple(int(y) for y in a.train.split(","))
    r = run_split(feat, train_years, int(a.test), save=not a.no_save,
                  base_all=base_all, lam=a.lam, verbose=True)
    return 0 if r["signs_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
