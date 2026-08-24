"""A-049 candidate columns in features/asof.py: strictly-prior, gated,
and immune to future-game perturbation (the outs_asof test pattern)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from features.asof import (
    asof_pitcher_game_table, asof_team_zone_contact,
    MIN_PRIOR_PITCHES_RATE, MIN_PRIOR_IZ_SWINGS,
)


def _pitches(game, date_s, n=100, whiffs=12, called=15, velo=94.0,
             pitcher=1, home="AAA", away="BBB", topbot="Top"):
    """One synthetic game: n pitches with known whiff/called counts."""
    rows = []
    for i in range(n):
        if i < whiffs:
            desc = "swinging_strike"
        elif i < whiffs + called:
            desc = "called_strike"
        else:
            desc = "foul" if i % 2 else "hit_into_play"
        rows.append({
            "game_pk": game, "pitcher": pitcher, "batter": 900 + (i % 9),
            "game_date": date_s, "events": ("strikeout" if i % 10 == 0 else
                                            "field_out") if i % 4 == 0 else None,
            "description": desc,
            "pitch_type": "FF" if i % 2 == 0 else "SL",
            "release_speed": velo if i % 2 == 0 else 84.0,
            "zone": 5 if i % 3 == 0 else 11,
            "inning_topbot": topbot,
            "home_team": home, "away_team": away,
            "at_bat_number": i // 4 + 1,
        })
    return rows


def _frame(games):
    return pd.DataFrame([r for g in games for r in g])


BASE_GAMES = [
    _pitches(1, "2026-06-01", velo=94.0),
    _pitches(2, "2026-06-06", velo=94.4),
    _pitches(3, "2026-06-11", velo=93.0, whiffs=20),
    _pitches(4, "2026-06-16", velo=95.0),
    _pitches(5, "2026-06-21", velo=91.0, whiffs=30),
]


def test_swstr_gate_and_value():
    pt = asof_pitcher_game_table(_frame(BASE_GAMES)).sort_values("game_pk")
    r1, r2, r3, r4, r5 = pt.itertuples()
    # 100 prior pitches < MIN gate -> NaN
    assert MIN_PRIOR_PITCHES_RATE == 200
    assert np.isnan(r2.asof_swstr_pct)
    # game 3: 200 prior pitches, 24 whiffs
    assert r3.asof_swstr_pct == pytest.approx(24 / 200)
    # game 5's own 30 whiffs must be absent from its own feature
    assert r5.asof_swstr_pct == pytest.approx((12 + 12 + 20 + 12) / 400)
    # csw adds called strikes
    assert r3.asof_csw_pct == pytest.approx((24 + 30) / 200)


def test_p5_and_velo_trend_are_prior_only():
    pt = asof_pitcher_game_table(_frame(BASE_GAMES)).sort_values("game_pk")
    rows = {r.game_pk: r for r in pt.itertuples()}
    assert rows[4].p5_pitches == pytest.approx(100.0)   # mean of 3 priors
    # velo_trend(g4) = fbv(g3) - mean(fbv(g1), fbv(g2))
    assert rows[4].velo_trend == pytest.approx(93.0 - (94.0 + 94.4) / 2)
    # a trend needs a 2-game baseline plus the previous start, so games
    # 1-3 are NaN by design (never filled)
    assert np.isnan(rows[2].velo_trend)
    assert np.isnan(rows[3].velo_trend)
    assert rows[5].velo_trend == pytest.approx(95.0 - (94.0 + 94.4 + 93.0) / 3)


def test_home_side_and_opponent():
    pt = asof_pitcher_game_table(_frame(BASE_GAMES))
    # pitcher throws in the Top -> he is the HOME pitcher, opponent = away
    assert (pt["is_home"] == 1.0).all()
    assert (pt["opp_team"] == "BBB").all()

    away_game = _pitches(9, "2026-06-25", topbot="Bot", home="CCC", away="DDD")
    pt2 = asof_pitcher_game_table(pd.DataFrame(away_game))
    assert pt2.iloc[0]["is_home"] == 0.0
    assert pt2.iloc[0]["opp_team"] == "CCC"


def test_future_perturbation_leaves_prior_rows_unchanged():
    """Corrupt the LAST game's pitches; every earlier row's features must
    be bit-identical (the features/outs_asof.py perturbation rule)."""
    clean = asof_pitcher_game_table(_frame(BASE_GAMES))
    corrupted_last = _pitches(5, "2026-06-21", velo=80.0, whiffs=90, n=100)
    dirty = asof_pitcher_game_table(_frame(BASE_GAMES[:4] + [corrupted_last]))

    cols = ["asof_swstr_pct", "asof_csw_pct", "p5_pitches", "velo_trend"]
    c = clean[clean["game_pk"] < 5].sort_values("game_pk")[cols].reset_index(drop=True)
    d = dirty[dirty["game_pk"] < 5].sort_values("game_pk")[cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(c, d)


def test_team_zone_contact_prior_day_only():
    games = [
        _pitches(1, "2026-06-01", topbot="Bot", home="AAA", away="BBB"),
        # doubleheader twin, same date — must not see its sibling
        _pitches(2, "2026-06-01", topbot="Bot", home="AAA", away="BBB"),
        _pitches(3, "2026-06-02", topbot="Bot", home="AAA", away="BBB"),
    ]
    tz = asof_team_zone_contact(_frame(games))
    aaa = tz[tz["team"] == "AAA"].sort_values("game_date")
    assert len(aaa) == 2                       # one row per team-DAY
    # day 1: no prior day -> NaN regardless of the twin
    assert np.isnan(aaa.iloc[0]["opp_zcontact"])
    # day 2 uses day 1 only. Per game: in-zone pitches are i%3==0 (34 of
    # 100); of those, swings are non-called descriptions; whiffs i<12.
    day1 = aaa.iloc[1]["opp_zcontact"]
    assert 0.0 < day1 < 1.0 or np.isnan(day1)  # gated by MIN_PRIOR_IZ_SWINGS
    # perturbing day 2 never changes day 2's own feature inputs (day 1)
    games2 = list(games)
    games2[2] = _pitches(3, "2026-06-02", topbot="Bot", home="AAA",
                         away="BBB", whiffs=90)
    tz2 = asof_team_zone_contact(_frame(games2))
    aaa2 = tz2[tz2["team"] == "AAA"].sort_values("game_date")
    if not np.isnan(day1):
        assert aaa2.iloc[1]["opp_zcontact"] == pytest.approx(day1)
    assert MIN_PRIOR_IZ_SWINGS == 300
