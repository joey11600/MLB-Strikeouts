"""Prior-season history window (docs/PRIOR_SEASON_SCOPE.md).

The 50-BF gate refuses 18.8% of 2026 starts; 11.5% of all starts belong
to pitchers with a full prior season sitting unused in the cache. This
widens the window for the RATE -- which travels across seasons (r=0.68-
0.73, and unbiased on the recovered starts) -- while treating WORKLOAD,
which does not (r=0.40-0.51), far more carefully.

The load-bearing test here is that the flag OFF is a byte-for-byte no-op.
Everything else can be re-litigated; a feature that quietly perturbs
production while "disabled" cannot.

Run:  python -m pytest tests/test_prior_season.py -q
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import daily_pipeline as dp  # noqa: E402


def _statcast(pitcher_id: int, outings: list[int], k_rate: float = 0.25,
              start_day: int = 1) -> pd.DataFrame:
    """Synthetic completed-PA rows: one block per outing."""
    rows = []
    for i, bf in enumerate(outings):
        n_k = round(bf * k_rate)
        for j in range(bf):
            rows.append({
                "pitcher": pitcher_id,
                "game_pk": 1000 + i,
                "game_date": pd.Timestamp(f"2026-05-{start_day + i:02d}"),
                "events": "strikeout" if j < n_k else "field_out",
                "zone": 5,
                "home_team": "LAD",
            })
    return pd.DataFrame(rows)


def _prior(prior_bf=400, prior_starts=16, k_pct=0.29, p25=22.0):
    return {
        "pitcher": 1,
        "prior_bf": prior_bf,
        "prior_ks": prior_bf * k_pct,
        "prior_k_pct": k_pct,
        "prior_starts": prior_starts,
        "prior_bf_mean": p25 + 1.8,
        "prior_bf_p25": p25,
    }


@pytest.fixture
def prior_on(monkeypatch):
    monkeypatch.setattr(dp, "USE_PRIOR_SEASON", True)


# --- the flag must be a true no-op ----------------------------------

def test_flag_off_is_a_no_op_even_when_a_prior_row_is_passed():
    df = _statcast(1, [20, 22, 24])
    without = dp._compute_pitcher_stats(df, 1)
    with_prior = dp._compute_pitcher_stats(df, 1, prior=_prior())
    assert without == with_prior
    assert with_prior["used_prior_season"] is False


def test_flag_off_leaves_eff_bf_equal_to_total_bf():
    """The caller gates on eff_bf, so this is what keeps the 50-BF rule
    identical to what production ran before the feature existed."""
    df = _statcast(1, [20, 22, 24])
    s = dp._compute_pitcher_stats(df, 1)
    assert s["eff_bf"] == float(s["total_bf"])


# --- the prior must be substantial ----------------------------------

@pytest.mark.parametrize("prior,why", [
    (_prior(prior_bf=150), "too few batters faced"),
    (_prior(prior_starts=4), "too few starts -- reliever volume"),
    (None, "no prior row at all"),
])
def test_thin_or_missing_prior_is_ignored(prior_on, prior, why):
    df = _statcast(1, [18])
    s = dp._compute_pitcher_stats(df, 1, prior=prior)
    assert s["used_prior_season"] is False, why
    assert s["eff_bf"] == float(s["total_bf"])


def test_reliever_volume_does_not_launder_into_a_starter(prior_on):
    """250 BF across 60 relief appearances clears the BF bar and must
    still fail on starts. This is the A-007 case."""
    df = _statcast(1, [5, 6, 4, 5, 7, 5])
    s = dp._compute_pitcher_stats(
        df, 1, prior=_prior(prior_bf=250, prior_starts=2))
    assert s["used_prior_season"] is False
    assert s["is_startable"] is False


def test_current_relief_usage_vetoes_a_starter_prior(prior_on):
    """Even with a genuine starter's prior season, relief-length outings
    THIS season veto. Last year must not overturn what this year shows."""
    df = _statcast(1, [5, 6, 4, 5, 7, 5])
    s = dp._compute_pitcher_stats(df, 1, prior=_prior())
    assert s["is_startable"] is False
    assert "relief" in (s["skip_reason"] or "")


# --- what the feature is for ----------------------------------------

def test_thin_current_season_is_priced_when_prior_is_real(prior_on):
    """The Snell case: one outing this season, a full season behind it."""
    df = _statcast(1, [18], k_rate=0.28)
    s = dp._compute_pitcher_stats(df, 1, prior=_prior())

    assert s["used_prior_season"] is True
    assert s["total_bf"] == 18
    assert s["eff_bf"] == pytest.approx(18 + 0.5 * 400)
    assert s["eff_bf"] >= 50           # clears the caller's gate
    assert s["is_startable"] is True


def test_season_debut_uses_prior_p25_for_workload(prior_on):
    """No current outings at all -> pure prior p25, never the prior mean."""
    empty = pd.DataFrame(columns=["pitcher", "game_pk", "game_date",
                                  "events", "zone", "home_team"])
    s = dp._compute_pitcher_stats(empty, 1, prior=_prior(p25=22.0))
    assert s["bf_mean"] == pytest.approx(22.0)


def test_one_outing_blends_current_with_prior_p25(prior_on):
    df = _statcast(1, [18])
    s = dp._compute_pitcher_stats(df, 1, prior=_prior(p25=22.0))
    assert s["bf_mean"] == pytest.approx(0.5 * 18 + 0.5 * 22.0)


def test_prior_never_overrides_an_established_current_workload(prior_on):
    """3+ starter games this season -> current season alone, as before."""
    df = _statcast(1, [24, 26, 25, 27])
    s = dp._compute_pitcher_stats(df, 1, prior=_prior(p25=15.0))
    assert s["bf_mean"] == pytest.approx(pd.Series([24, 26, 25, 27]).mean())


def test_rate_moves_toward_the_prior_season(prior_on):
    """A high-K prior should pull a thin current season upward -- and the
    result must still sit between the two, never outside them."""
    df = _statcast(1, [20], k_rate=0.10)
    without = dp._compute_pitcher_stats(df, 1)
    s = dp._compute_pitcher_stats(df, 1, prior=_prior(k_pct=0.35))
    assert s["season_k_pct"] > without["season_k_pct"]
    assert 0.10 < s["season_k_pct"] < 0.35


def test_shrinkage_still_applies_on_top(prior_on):
    """Prior widens the sample; it does not bypass league shrinkage. A
    0.40 prior must not come through at 0.40."""
    df = _statcast(1, [20], k_rate=0.40)
    s = dp._compute_pitcher_stats(df, 1, prior=_prior(k_pct=0.40))
    assert s["season_k_pct"] < 0.40
    assert s["season_k_pct"] > dp.LEAGUE_K_RATE
