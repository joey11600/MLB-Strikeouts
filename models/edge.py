"""Edge computation — no-vig fair probability and model edge.

Converts DraftKings American odds to implied probabilities, strips the
vig to get the no-vig fair probability, and computes the model's edge
against the market.

Strikeout props hold 8-12% vig (e.g. -125/-115 on both sides).
Every bet must clear a vig-adjusted edge threshold before it's worth
taking. The threshold is: hold% + EDGE_MARGIN.
"""
import math

EDGE_MARGIN = 0.02
MIN_EDGE_PCT = 0.03


def american_to_implied(odds: int | str) -> float:
    """Convert American odds to implied probability.

    +150 -> 100/250 = 0.400
    -150 -> 150/250 = 0.600
    """
    odds = int(odds)
    if odds > 0:
        return 100.0 / (100.0 + odds)
    elif odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    else:
        return 0.5


def american_to_decimal(odds: int | str) -> float:
    """Convert American odds to decimal odds (payout per unit risked).

    +150 -> 2.50 (risk 1, win 1.50, get back 2.50)
    -150 -> 1.667 (risk 1.50, win 1.00, get back 2.50... or risk 1, win 0.667)
    """
    odds = int(odds)
    if odds > 0:
        return 1.0 + odds / 100.0
    elif odds < 0:
        return 1.0 + 100.0 / abs(odds)
    else:
        return 2.0


def no_vig_fair_prob(over_odds: int | str, under_odds: int | str) -> dict:
    """Strip the vig from both sides to get the no-vig fair probability.

    Returns dict with: over_implied, under_implied, total_implied,
    hold_pct, fair_over, fair_under.
    """
    over_imp = american_to_implied(over_odds)
    under_imp = american_to_implied(under_odds)
    total = over_imp + under_imp
    hold = total - 1.0

    fair_over = over_imp / total
    fair_under = under_imp / total

    return {
        "over_implied": over_imp,
        "under_implied": under_imp,
        "total_implied": total,
        "hold_pct": hold,
        "fair_over": fair_over,
        "fair_under": fair_under,
    }


def compute_edge(
    model_prob_over: float,
    over_odds: int | str,
    under_odds: int | str,
) -> dict:
    """Compute model edge for both sides of a strikeout prop.

    Returns dict with: fair_over, fair_under, hold_pct,
    edge_over, edge_under, best_side, best_edge, best_odds,
    model_prob_best, clears_threshold.
    """
    nv = no_vig_fair_prob(over_odds, under_odds)
    model_prob_under = 1.0 - model_prob_over

    edge_over = model_prob_over - nv["fair_over"]
    edge_under = model_prob_under - nv["fair_under"]

    if edge_over >= edge_under:
        best_side = "OVER"
        best_edge = edge_over
        best_odds = int(over_odds)
        model_prob_best = model_prob_over
    else:
        best_side = "UNDER"
        best_edge = edge_under
        best_odds = int(under_odds)
        model_prob_best = model_prob_under

    threshold = max(nv["hold_pct"] + EDGE_MARGIN, MIN_EDGE_PCT)
    clears = best_edge >= threshold

    return {
        "fair_over": nv["fair_over"],
        "fair_under": nv["fair_under"],
        "hold_pct": nv["hold_pct"],
        "edge_over": edge_over,
        "edge_under": edge_under,
        "best_side": best_side,
        "best_edge": best_edge,
        "best_odds": best_odds,
        "model_prob_best": model_prob_best,
        "threshold": threshold,
        "clears_threshold": clears,
    }


def pick_strength(edge: float, threshold: float) -> str:
    """Classify pick strength based on edge magnitude above threshold."""
    excess = edge - threshold
    if excess >= 0.06:
        return "STRONG"
    elif excess >= 0.03:
        return "MEDIUM"
    elif excess >= 0.0:
        return "LEAN"
    return "NO_PLAY"
