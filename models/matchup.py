"""Matchup formula — normalized empirical K% combination.

The standard log5/odds-ratio form is miscalibrated at the high end,
which is exactly where strikeout props live. This module uses the
FanGraphs empirical replacement, CORRECTLY NORMALIZED for the current
league K rate.

Source: https://blogs.fangraphs.com/bettermatch-up-data-forecasting-strikeout-rate/

The raw constants (0.84, 0.16) assumed a 19.05% league K rate. Without
renormalization, f(0.225, 0.225) = 0.2500, a +2.5 pp systematic
inflation on an average-vs-average matchup. Over 22 BF that is +0.55
strikeouts per start of pure bias.
"""

A_CURVATURE = 0.84

LEAGUE_K_RATE_2026 = 0.225


def _compute_b(a: float, league_rate: float) -> float:
    """b = L - a * L^2, ensuring f(L, L) == L."""
    return league_rate - a * league_rate**2


B_NORMALIZATION = _compute_b(A_CURVATURE, LEAGUE_K_RATE_2026)


def matchup_k_rate(batter_k_pct: float, pitcher_k_pct: float) -> float:
    """Compute matchup strikeout probability.

    Parameters
    ----------
    batter_k_pct : float
        Batter's strikeout rate (0 to 1).
    pitcher_k_pct : float
        Pitcher's strikeout rate (0 to 1).

    Returns
    -------
    float
        Expected strikeout probability for this matchup.
    """
    bp = batter_k_pct * pitcher_k_pct
    return bp / (A_CURVATURE * bp + B_NORMALIZATION)


def _test_normalization():
    """Assert f(L, L) == L to within 1e-9. Run on every build."""
    L = LEAGUE_K_RATE_2026
    result = matchup_k_rate(L, L)
    assert abs(result - L) < 1e-9, (
        f"Matchup formula normalization FAILED: "
        f"f({L}, {L}) = {result}, expected {L}, "
        f"error = {abs(result - L):.2e}"
    )


_test_normalization()
