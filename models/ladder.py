"""Ladder/milestone betting — evaluate and size alt K lines.

The ladder is a small ADD-ON to a conviction over pick, not a parallel
betting system (operator rule, 2026-08-05):

  1. GAP GATE — the ladder fires only when the primary bet is an OVER
     that was actually placed AND the model projects at least
     LADDER_GAP_MIN strikeouts above the line (line 6.5 needs
     E[K] >= 8.0). No gate, no rungs.
  2. NEXT RUNGS ONLY — at most the next LADDER_RUNG_COUNT lines above
     the primary (line 6.5 -> alt 7.5 and alt 8.5, i.e. milestones
     8+ and 9+). Nothing below the line, nothing further out.
  3. DESCENDING STAKES — the primary at the market line carries the
     most money (it is the most leash-proof bet); each rung up caps at
     LADDER_RUNG_STAKE_FRACTION of the rung below it:
     rung cap = primary_units * FRACTION^(distance from the line).
     Line 2.5 with 1.70u primary -> 4+ K <= 0.85u, 5+ K <= 0.43u.
     Allocation is nearest-rung-first, on top of quarter-Kelly,
     MAX_STAKE_UNITS, and the LADDER_MAX_UNITS per-pitcher cap.
  4. Every rung still must clear LADDER_EDGE_THRESHOLD (money rule).

Every posted rung is still EVALUATED and returned with a status so the
slate sidecar can show the whole board and why each rung passed.

The compound model already computes the full P(K = k) distribution, so
P(K >= milestone) for any milestone is a single array slice — free.
"""
import math

import numpy as np

from models.edge import (
    ALT_SIDE_MARGIN,
    EDGE_MARGIN,
    MIN_EDGE_PCT,
    american_to_decimal,
    american_to_implied,
    blend_with_market,
    pick_strength,
)
from models.staking import kelly_stake, quantize_stake_down, KELLY_FRACTION
from tracker import MAX_STAKE_UNITS

# 3.5 fits the clean-denomination template: 2u primary + 1u + 0.5u.
LADDER_MAX_UNITS = 3.5

# Operator ladder rules (confirmed 2026-08-05): fire only on a big
# model-vs-line gap, take only the next rungs up, size small.
LADDER_GAP_MIN = 1.5
LADDER_RUNG_COUNT = 2
LADDER_RUNG_STAKE_FRACTION = 0.5

# One-sided markets carry the book's margin on the only quoted side, and
# nothing to strip it against. Treat the two-sided-equivalent hold as
# 2 * ALT_SIDE_MARGIN and demand the same discipline as the primary
# market: hold + margin. This is intentionally STRICTER than the old
# flat 3% bar, which let ladder picks through on a looser standard than
# primaries while sharing the same edge column.
LADDER_EDGE_THRESHOLD = max(2 * ALT_SIDE_MARGIN + EDGE_MARGIN, MIN_EDGE_PCT)


def evaluate_ladder(
    k_dist: np.ndarray,
    alt_lines: list[dict],
    primary_line: float | None = None,
    primary_units: float = 0.0,
    calibrate_fn=None,
    expected_k: float | None = None,
    primary_side: str | None = None,
) -> list[dict]:
    """Evaluate all available milestone lines for one pitcher.

    Honesty rules (same discipline as the primary market):
      1. Raw tail mass P(K >= m) is calibrated via calibrate_fn.
      2. The one-sided implied probability is de-vigged with an assumed
         side margin (fair = implied / (1 + ALT_SIDE_MARGIN)).
      3. The calibrated model prob is blended with the fair prob
         (market-anchored shrinkage) before edge is measured.
      4. Threshold is LADDER_EDGE_THRESHOLD, not a loose flat 3%.

    Parameters
    ----------
    k_dist : array of shape (41,)
        P(K = k) for k = 0..40 from the compound model.
    alt_lines : list of dicts
        Each has 'milestone' (int or str) and 'odds' (American odds str).
        From fetch_dk_strikeout_alts(), filtered to this pitcher.
    primary_line : float or None
        The primary O/U line (e.g. 6.5). Used to skip milestones that
        duplicate the primary bet.
    primary_units : float
        Units already committed on the primary O/U line for this pitcher.
        Counts toward LADDER_MAX_UNITS.
    calibrate_fn : callable or None
        Maps raw tail probability -> calibrated probability
        (StrikeoutPredictor.calibrate_prob). Identity if None.

    Returns
    -------
    list of dicts, each with:
        milestone, odds, raw_model_prob, model_prob (calibrated),
        implied_prob, fair_prob, blended_prob, edge, strength,
        units_risked, decimal_odds
    Sorted best-edge-first, already capped at LADDER_MAX_UNITS.
    """
    if k_dist is None or len(k_dist) == 0:
        return []

    # Gap gate: ladder only rides on a placed OVER primary whose
    # projection clears the line by LADDER_GAP_MIN.
    gate_open = (
        primary_side == "OVER"
        and primary_units > 0
        and primary_line is not None
        and expected_k is not None
        and (expected_k - primary_line) >= LADDER_GAP_MIN
    )
    eligible: set[int] = set()
    if gate_open and primary_line is not None:
        base = math.ceil(primary_line)
        eligible = {base + i for i in range(1, LADDER_RUNG_COUNT + 1)}

    rungs = []
    for alt in alt_lines:
        try:
            milestone = int(float(alt["milestone"]))
            odds_str = str(alt["odds"]).replace("−", "-")
            odds_int = int(odds_str)
        except (ValueError, KeyError):
            continue

        if milestone < 1 or milestone > 39:
            continue

        is_primary_equivalent = False
        if primary_line is not None:
            is_primary_equivalent = milestone == math.ceil(primary_line)

        raw_model_prob = float(np.sum(k_dist[milestone:]))
        model_prob = calibrate_fn(raw_model_prob) if calibrate_fn else raw_model_prob
        implied_prob = american_to_implied(odds_int)
        fair_prob = implied_prob / (1.0 + ALT_SIDE_MARGIN)
        decimal_odds = american_to_decimal(odds_int)

        blended_prob = blend_with_market(model_prob, fair_prob)
        edge = blended_prob - fair_prob

        raw_units = kelly_stake(blended_prob, decimal_odds, KELLY_FRACTION) if edge > 0 else 0.0
        strength = pick_strength(edge, LADDER_EDGE_THRESHOLD)

        # Every evaluated rung is kept — passed rungs carry a status so
        # the slate sidecar can show the full board, not just the bets.
        # The rung at ceil(primary_line) is the primary bet's own event
        # (its inverse when the primary is an under) — displayed for a
        # complete sequence but never separately bettable.
        if is_primary_equivalent:
            status = "primary_equivalent"
        elif not gate_open:
            status = "passed_gap_gate"
        elif milestone not in eligible:
            status = "passed_not_next_rung"
        elif edge <= 0:
            status = "passed_no_edge"
        elif raw_units < 0.1:
            status = "passed_stake_too_small"
        elif strength == "NO_PLAY":
            status = "passed_below_threshold"
        else:
            status = "candidate"

        rungs.append({
            "milestone": milestone,
            "odds": odds_int,
            "odds_str": f"{odds_int:+d}",
            "raw_model_prob": raw_model_prob,
            "model_prob": model_prob,
            "implied_prob": implied_prob,
            "fair_prob": fair_prob,
            "blended_prob": blended_prob,
            "edge": edge,
            "strength": strength,
            "raw_units": raw_units,
            "units_risked": 0.0,
            "decimal_odds": decimal_odds,
            "status": status,
        })

    # Nearest-rung-first allocation with stakes that DESCEND by distance
    # from the line: the market line holds the most money (most robust
    # to a short outing), each step up gets at most half the step below.
    rungs.sort(key=lambda r: r["milestone"])

    base = math.ceil(primary_line) if primary_line is not None else 0
    remaining = LADDER_MAX_UNITS - primary_units
    for rung in rungs:
        if rung["status"] != "candidate":
            continue
        if remaining <= 0:
            rung["status"] = "passed_pitcher_cap"
            continue

        distance = max(1, rung["milestone"] - base)
        rung_stake_ceiling = primary_units * (LADDER_RUNG_STAKE_FRACTION ** distance)
        capped = quantize_stake_down(min(
            rung["raw_units"], remaining, MAX_STAKE_UNITS, rung_stake_ceiling,
        ))
        if capped <= 0:
            rung["status"] = "passed_stake_too_small"
            continue
        rung["units_risked"] = capped
        rung["status"] = "bet"
        remaining -= capped

    return rungs


def format_ladder_pick_label(milestone: int, strength: str) -> str:
    """Format a pick label for a ladder rung."""
    return f"OVER {milestone}+ K ({strength})"
