"""The shadow portfolio must not declare a money decision ready early.

`tools/shadow.py` exists to break the A-015 deadlock: MODEL_TRUST_WEIGHT
cannot be raised without CLV evidence, and evidence cannot be gathered at
a weight that blocks nearly every bet. Its verdict therefore feeds a
decision that changes stake exposure.

It used to compute `ready_to_decide = len(rows) >= 100`, counting
OBSERVATIONS -- evaluated pitchers. A-006's gate is "100+ graded BETS
with positive average CLV". Measured 2026-08-09: 100 observations printed
READY while the production weight had TWO bets behind it and an average
CLV of -15.95%. Roughly fiftyfold less evidence than the gate asks for,
erring toward RAISING the weight.

Same defect as a health check reporting configuration instead of
capability: a readiness signal that answers an easier question than the
one that matters.

Run:  python -m pytest tests/test_shadow_readiness.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import tools.shadow as S  # noqa: E402


def _col(n_bets: int, clv: float | None, production: bool = True) -> dict:
    return {"n_bets": n_bets, "avg_clv_pct": clv, "is_production": production,
            "trust_weight": 0.5}


def test_observations_are_not_bets():
    """The exact regression: plenty of evidence, almost no bets."""
    assert S._is_ready(_col(n_bets=2, clv=-15.95)) is False
    assert S._is_ready(_col(n_bets=2, clv=+5.0)) is False, (
        "two bets is not evidence however good the CLV looks"
    )


def test_volume_alone_is_not_enough():
    """A losing edge does not improve with repetition."""
    assert S._is_ready(_col(n_bets=250, clv=-0.01)) is False
    assert S._is_ready(_col(n_bets=250, clv=None)) is False


def test_both_halves_of_the_gate_pass_together():
    assert S._is_ready(_col(n_bets=S.BET_TARGET, clv=+0.01)) is True
    assert S._is_ready(_col(n_bets=S.BET_TARGET - 1, clv=+5.0)) is False


def test_missing_production_column_is_not_ready():
    """Fail closed: no live column means nothing to judge."""
    assert S._is_ready(None) is False


def test_readiness_is_judged_at_the_production_weight(monkeypatch):
    """Not at whichever counterfactual column happens to look best.

    Only the live weight's bets are real; the rest of the grid shows
    direction. Reading readiness off the best column would let a
    hypothetical portfolio authorise a change to the real one.
    """
    grid = [
        _col(n_bets=2, clv=-15.95, production=True),     # the live weight
        _col(n_bets=500, clv=+9.0, production=False),    # a flattering ghost
    ]
    monkeypatch.setattr(S, "load_rows", lambda *a, **k: [])
    monkeypatch.setattr(S, "_closing_odds_index", lambda: {})
    monkeypatch.setattr(S, "evaluate", lambda rows, w, closing: grid.pop(0))
    monkeypatch.setattr(S, "TRUST_GRID", [0.5, 1.0])

    data = S.build()
    assert data["ready_to_decide"] is False
    assert data["target_graded_bets"] == S.BET_TARGET
