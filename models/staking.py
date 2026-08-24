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
#
# 1.5 is REACHABLE ONLY via quantize_stake_down (cap fills), never via
# quantize_stake — that is the operator rule above ("whole units when
# >= 0.75"), not an oversight. Audited 2026-08-24 (A-047 sweep): the
# only half-point subtlety in quantize_stake is Python's banker's
# rounding at exactly x.5, which is immaterial in the reachable range
# (1.5 -> 2 either way; 2.5 caps at 2 regardless). Pinned by test.
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

    The haircut keys on the PITCHER first and the game second (A-047).
    It used to key on game_pk alone, which is backwards against the
    measured correlations: same-pitcher entries (a primary plus ladder
    rungs, or K-vs-outs once that market lives) settle off the same arm
    and correlate ~+0.50, while cross-pitcher same-game correlation
    measured ~+0.02. A-041's worst slate was exactly this shape — three
    losses on one pitcher in one game, none haircut beyond the first.
    Both keys now apply: repeated pitcher OR repeated game trims the
    stake. That is strictly more conservative than before.

    Keys register only when a pick actually receives units — the haircut
    prices correlation with EXPOSURE, and a zeroed pick is no exposure.

    picks: list of dicts with at least 'units_risked', 'game_pk',
    'pitcher_id', 'best_edge'. Returns the same list with units_risked
    adjusted down if needed.
    """
    if not picks:
        return picks

    picks = sorted(picks, key=lambda p: p.get("best_edge", 0), reverse=True)

    games_seen = set()
    pitchers_seen = set()
    total_allocated = 0.0

    for pick in picks:
        game_pk = pick.get("game_pk", "")
        pitcher_id = pick.get("pitcher_id", "")
        raw_units = pick.get("units_risked", 0.0)

        correlated = (pitcher_id != "" and pitcher_id in pitchers_seen) or (
            game_pk != "" and game_pk in games_seen)
        if correlated:
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
        pitchers_seen.add(pitcher_id)

    return picks
