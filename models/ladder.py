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
    american_to_decimal,
    american_to_implied,
    compute_edge,
    pick_strength,
)
from models.staking import kelly_stake, KELLY_FRACTION
from tracker import MAX_STAKE_UNITS

LADDER_MAX_UNITS = 3.0


def evaluate_ladder(
    k_dist: np.ndarray,
    alt_lines: list[dict],
    primary_line: float | None = None,
    primary_units: float = 0.0,
) -> list[dict]:
    """Evaluate all available milestone lines for one pitcher.

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

    Returns
    -------
    list of dicts, each with:
        milestone, odds, model_prob, implied_prob, edge, strength,
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

        if primary_line is not None:
            import math
            if milestone == math.ceil(primary_line):
                continue

        model_prob = float(np.sum(k_dist[milestone:]))
        implied_prob = american_to_implied(odds_int)
        decimal_odds = american_to_decimal(odds_int)

        edge = model_prob - implied_prob
        if edge <= 0:
            continue

        raw_units = kelly_stake(model_prob, decimal_odds, KELLY_FRACTION)
        if raw_units < 0.1:
            continue

        strength = pick_strength(edge, 0.03)
        if strength == "NO_PLAY":
            continue

        rungs.append({
            "milestone": milestone,
            "odds": odds_int,
            "odds_str": odds_str,
            "model_prob": model_prob,
            "implied_prob": implied_prob,
            "edge": edge,
            "strength": strength,
            "raw_units": raw_units,
            "units_risked": raw_units,
            "decimal_odds": decimal_odds,
        })

    rungs.sort(key=lambda r: r["edge"], reverse=True)

    remaining = LADDER_MAX_UNITS - primary_units
    for rung in rungs:
        if remaining <= 0:
            rung["units_risked"] = 0.0
            rung["capped"] = "pitcher_cap"
            continue

        capped = min(rung["raw_units"], remaining, MAX_STAKE_UNITS)
        rung["units_risked"] = round(capped, 2)
        remaining -= capped

    rungs = [r for r in rungs if r["units_risked"] > 0]
    return rungs


def format_ladder_pick_label(milestone: int, strength: str) -> str:
    """Format a pick label for a ladder rung."""
    return f"OVER {milestone}+ K ({strength})"
