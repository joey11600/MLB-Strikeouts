"""Ladder/milestone betting — evaluate and size alt K lines.

When the model predicts a pitcher's K total is well above (or below) the
primary O/U line, there's edge at multiple milestone thresholds. Instead
of betting only Over 6.5, also bet the 6+, 7+, 8+ milestones — each at
its own DK odds. This is called "laddering" props.

The compound model already computes the full P(K = k) distribution, so
P(K >= milestone) for any milestone is a single array slice — free.

Staking rules:
- Each rung is a separate bet sized via quarter-Kelly.
- Per-rung cap: MAX_STAKE_UNITS (2.0u).
- Per-pitcher cap: LADDER_MAX_UNITS (3.0u) across all rungs combined.
  Bets on 6+, 7+, 8+ for the same pitcher are highly correlated
  (nested events), so the combined exposure must be limited.
- Best-edge-first allocation within each pitcher's ladder.
- The primary O/U bet counts toward the per-pitcher cap.
"""
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
from models.staking import kelly_stake, KELLY_FRACTION
from tracker import MAX_STAKE_UNITS

LADDER_MAX_UNITS = 3.0

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
            import math
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

    rungs.sort(key=lambda r: r["edge"], reverse=True)

    remaining = LADDER_MAX_UNITS - primary_units
    for rung in rungs:
        if rung["status"] != "candidate":
            continue
        if remaining <= 0:
            rung["status"] = "passed_pitcher_cap"
            continue

        capped = min(rung["raw_units"], remaining, MAX_STAKE_UNITS)
        rung["units_risked"] = round(capped, 2)
        rung["status"] = "bet"
        remaining -= capped

    return rungs


def format_ladder_pick_label(milestone: int, strength: str) -> str:
    """Format a pick label for a ladder rung."""
    return f"OVER {milestone}+ K ({strength})"
