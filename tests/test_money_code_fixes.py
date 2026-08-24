"""A-047: money-code fixes from the 2026-08-24 full-model audit.

1. The ladder gate read `pick_side` off primary plays, which only ever
   carry `best_side` — the side was always None, the gate never opened,
   and zero ladder rungs were possible from 2026-08-05 onward.
2. The correlation haircut keyed on game_pk only; same-pitcher entries
   (~+0.50 correlated) escaped while cross-pitcher same-game (~+0.02)
   was trimmed. Now: pitcher first, game second.
3. Stage A silently substituted an unfitted fallback model when
   coefficients were missing; Stage B has always raised. Now both raise.
4. prob_k_geq folded a whole-number line's PUSH into the over win —
   against its own docstring. Whole lines are now refused outright.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import models.stage_a_bf as sa
from models.compound import prob_k_geq
from models.staking import (
    portfolio_daily_cap, quantize_stake, quantize_stake_down)
from models.ladder import evaluate_ladder
from tools.daily_pipeline import _primary_for


# ---------------------------------------------------------------- ladder
def test_primary_for_reads_best_side():
    """The A-047 one-word bug: primary plays carry best_side, never
    pick_side. The lookup must return the side that exists."""
    pred = {"pitcher_id": 7}
    all_plays = [{
        "pitcher_id": 7, "pick_type": "primary",
        "units_risked": 2.0, "best_side": "OVER",
        # exactly like production: no pick_side key on primaries
    }]
    units, side = _primary_for(pred, all_plays)
    assert units == 2.0
    assert side == "OVER"


def test_primary_for_no_primary():
    assert _primary_for({"pitcher_id": 7}, []) == (0.0, None)


def _flat_kdist():
    """A distribution with real upper-tail mass so rungs price."""
    d = np.zeros(41)
    d[4:12] = 1.0
    return d / d.sum()


def test_ladder_gate_opens_end_to_end():
    """With the side wired correctly, an OVER primary with a wide gap
    must produce rungs past the gap gate (any status except
    passed_gap_gate proves gate_open was True)."""
    alts = [{"milestone": 7, "odds": "+120"}, {"milestone": 8, "odds": "+250"}]
    rungs = evaluate_ladder(
        _flat_kdist(), alts, primary_line=5.5, primary_units=2.0,
        calibrate_fn=None, expected_k=7.5, primary_side="OVER",
        lineup_confirmed=True)
    assert rungs, "no rungs evaluated"
    assert all(r["status"] != "passed_gap_gate" for r in rungs)

    # and the regression shape: side None (the old bug) closes the gate
    rungs_none = evaluate_ladder(
        _flat_kdist(), alts, primary_line=5.5, primary_units=2.0,
        calibrate_fn=None, expected_k=7.5, primary_side=None,
        lineup_confirmed=True)
    assert all(r["status"] == "passed_gap_gate" for r in rungs_none)


# -------------------------------------------------------------- haircut
def _pick(pitcher, game, units, edge):
    return {"pitcher_id": pitcher, "game_pk": game,
            "units_risked": units, "best_edge": edge}


def test_haircut_fires_on_repeated_pitcher():
    """Primary + ladder rung on the SAME pitcher: the second entry gets
    the 15% trim (2.0 -> 1.7 -> quantized down to 1.5). Pre-fix it
    escaped untouched because both entries share a game_pk only when
    the key happened to match."""
    picks = portfolio_daily_cap([
        _pick(7, "g1", 2.0, 0.10),
        _pick(7, "g1", 2.0, 0.08),
    ])
    assert picks[0]["units_risked"] == 2.0
    assert picks[1]["units_risked"] == 1.5  # 2.0 * 0.85 = 1.7 -> down to 1.5


def test_haircut_fires_on_repeated_game_distinct_pitchers():
    picks = portfolio_daily_cap([
        _pick(7, "g1", 2.0, 0.10),
        _pick(8, "g1", 2.0, 0.08),
    ])
    assert picks[1]["units_risked"] == 1.5


def test_no_haircut_across_distinct_games_and_pitchers():
    picks = portfolio_daily_cap([
        _pick(7, "g1", 2.0, 0.10),
        _pick(8, "g2", 2.0, 0.08),
    ])
    assert [p["units_risked"] for p in picks] == [2.0, 2.0]


def test_zeroed_pick_registers_no_exposure():
    """A pick that gets no units is no exposure — the next pick on the
    same pitcher is then the FIRST real position and takes no trim."""
    picks = portfolio_daily_cap([
        _pick(7, "g1", 0.1, 0.10),   # quantizes down to 0 -> no exposure
        _pick(7, "g1", 2.0, 0.08),
    ])
    assert picks[0]["units_risked"] == 0.0
    assert picks[1]["units_risked"] == 2.0


# ------------------------------------------------------------- staking
def test_quantize_stake_pins_operator_rule():
    """Operator rule 2026-08-05: whole units at >= 0.75, else 0.5 /
    0.25 / 0. 1.5 is deliberately unreachable here (down-quantize
    only). Pinned so a 'fix' can't silently change published stakes."""
    assert quantize_stake(0.10) == 0.0
    assert quantize_stake(0.20) == 0.25
    assert quantize_stake(0.40) == 0.5
    assert quantize_stake(0.75) == 1.0
    assert quantize_stake(1.40) == 1.0
    assert quantize_stake(1.50) == 2.0
    assert quantize_stake(2.40) == 2.0   # MAX_STAKE_UNITS cap
    assert quantize_stake_down(1.60) == 1.5  # the only path to 1.5


# ------------------------------------------------------------- stage A
def test_stage_a_unfitted_raises():
    a = sa.StageA()
    assert a.coefficients is None
    with pytest.raises(RuntimeError, match="no fitted coefficients"):
        a.predict_bf_distribution({"a3_season_k_pct_shrunk": 0.22,
                                   "c1_bf_mean": 22.0})


# ------------------------------------------------------------ compound
def test_prob_k_geq_half_lines_unchanged():
    d = _flat_kdist()
    assert prob_k_geq(d, 5.5) == pytest.approx(float(d[6:].sum()))


def test_prob_k_geq_refuses_whole_lines():
    with pytest.raises(ValueError, match="PUSH"):
        prob_k_geq(_flat_kdist(), 6.0)
    with pytest.raises(ValueError, match="PUSH"):
        prob_k_geq(_flat_kdist(), 6)
