"""Quarter-Kelly staking engine.

Sizes bets using Kelly criterion at 1/4 fraction, capped at
MAX_STAKE_UNITS (2.0 per bet in v1). Includes a portfolio-level
daily cap with a correlation haircut for correlated slate entries.

1 unit = 1% of bankroll. Bankroll is always 100 units.
"""
import math

from tracker import MAX_STAKE_UNITS

KELLY_FRACTION = 0.25
# Raised 6.0 -> 10.0 by operator direction (2026-08-05): the 3.5u
# ladder trio plus normal primaries regularly exceeded 6u.
DAILY_MAX_UNITS = 10.0
CORRELATION_HAIRCUT = 0.15

# Published stakes use clean denominations (operator rule, 2026-08-05):
# whole units when >= 0.75, else 0.5, else 0.25, else no bet. Ladder
# rungs derive from the quantized primary, so a 2u primary yields the
# 2 / 1 / 0.5 template.
STAKE_DENOMS = [2.0, 1.5, 1.0, 0.5, 0.25]


def quantize_stake(units: float) -> float:
    """Round a stake to the nearest clean denomination.

    >= 0.75 rounds to the nearest whole unit; 0.375-0.75 -> 0.5;
    0.125-0.375 -> 0.25; below that -> 0 (no bet).
    """
    if units < 0.125:
        return 0.0
    if units < 0.375:
        return 0.25
    if units < 0.75:
        return 0.5
    return float(min(round(units), MAX_STAKE_UNITS))


def quantize_stake_down(units: float) -> float:
    """Largest clean denomination that fits within `units` (cap-safe)."""
    for denom in STAKE_DENOMS:
        if denom <= units + 1e-9:
            return denom
    return 0.0


def kelly_stake(
    model_prob: float,
    decimal_odds: float,
    fraction: float = KELLY_FRACTION,
) -> float:
    """Compute fractional Kelly stake in units.

    Full Kelly: f* = (b*p - q) / b
    where b = decimal_odds - 1, p = model_prob, q = 1-p.
    We use fraction * f* (quarter-Kelly by default).
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0

    p = model_prob
    q = 1.0 - p

    f_star = (b * p - q) / b
    if f_star <= 0:
        return 0.0

    raw_stake = fraction * f_star * 100.0
    return min(raw_stake, MAX_STAKE_UNITS)


def portfolio_daily_cap(
    picks: list[dict],
    daily_max: float = DAILY_MAX_UNITS,
    haircut: float = CORRELATION_HAIRCUT,
) -> list[dict]:
    """Apply portfolio-level daily cap with correlation haircut.

    Pitchers in the same game are correlated (game environment, umpire,
    weather). Reduce the combined allocation for same-game picks.

    picks: list of dicts with at least 'units_risked', 'game_pk', 'best_edge'.
    Returns the same list with units_risked adjusted down if needed.
    """
    if not picks:
        return picks

    picks = sorted(picks, key=lambda p: p.get("best_edge", 0), reverse=True)

    games_seen = set()
    total_allocated = 0.0

    for pick in picks:
        game_pk = pick.get("game_pk", "")
        raw_units = pick.get("units_risked", 0.0)

        if game_pk in games_seen:
            raw_units *= (1.0 - haircut)

        remaining = daily_max - total_allocated
        if remaining <= 0:
            pick["units_risked"] = 0.0
            pick["capped_reason"] = "daily_cap"
            continue

        # Clean denominations only — a partial fill steps DOWN to the
        # largest denom that fits, never to an arbitrary fraction.
        final_units = quantize_stake_down(min(raw_units, remaining))
        if final_units <= 0:
            pick["units_risked"] = 0.0
            pick["capped_reason"] = "daily_cap"
            continue

        pick["units_risked"] = final_units
        total_allocated += final_units
        games_seen.add(game_pk)

    return picks
