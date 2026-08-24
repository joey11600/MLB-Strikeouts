"""A-048: the gauntlet harness itself was partly broken.

- Gate 4 was invoked with an empty array and empty dict, so its loop
  never ran and it passed every feature ever screened.
- Gate 5 re-read Gate 2's Brier improvements — a strictly weaker copy of
  Gate 2's own test that could never independently fail anything.
- The baseline's season_k_pct / bf_mean / bf_std were computed over the
  whole split window INCLUDING the predicted game (candidate as-of,
  baseline not).

These tests pin the repaired behavior with synthetic data.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.gauntlet as G


def _synthetic_pitches():
    """One pitcher, five 20-BF starts on distinct dates; 5 K per start
    except start 5 (10 K) — so any leak of the target game into the
    baseline is detectable in season_k_pct."""
    rows = []
    for g in range(5):
        for i in range(20):
            rows.append({
                "game_pk": 100 + g,
                "pitcher": 1,
                "batter": 500 + i,
                "events": "strikeout" if i < (10 if g == 4 else 5) else "field_out",
                "game_date": f"2026-06-{g + 1:02d}",
                "at_bat_number": i + 1,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def game_set(monkeypatch):
    monkeypatch.setattr(G, "load_cached", lambda *a, **k: _synthetic_pitches())
    G._GAME_SET_CACHE.clear()
    gs = G._build_game_set(G.date(2026, 6, 1), G.date(2026, 6, 30))
    G._GAME_SET_CACHE.clear()
    return gs


def test_game_set_is_strictly_prior(game_set):
    """Rows exist only once 3 prior starts / 50 prior BF accumulate, and
    each row's stats reflect ONLY earlier games."""
    assert list(game_set["game_pk"]) == [103, 104]

    g4 = game_set[game_set["game_pk"] == 103].iloc[0]
    assert g4["prior_bf"] == 60          # games 1-3
    assert g4["season_k_pct"] == pytest.approx(15 / 60)
    assert g4["bf_mean"] == pytest.approx(20.0)
    assert g4["n_starts"] == 3

    g5 = game_set[game_set["game_pk"] == 104].iloc[0]
    assert g5["prior_bf"] == 80          # games 1-4
    # its own 10-K game must NOT be inside its own baseline
    assert g5["season_k_pct"] == pytest.approx(20 / 80)


def test_game_set_would_have_leaked_before_fix(game_set):
    """The whole-window rate (30 K / 100 BF = 0.30) must appear NOWHERE:
    that number only exists if the target games leak into the stats.
    (The pre-A-048 code assigned every row this pooled value.)"""
    assert not np.isclose(game_set["season_k_pct"], 0.30).any()


# ---------------------------------------------------------------- gate 4
def test_gate4_flags_high_correlation():
    x = np.linspace(0.15, 0.35, 100)
    res = G.gate4_collinearity(
        "a9_zone_pct", x, {"season_k_pct": x * 2 + 0.01})
    assert res["passed"] is False
    assert "season_k_pct" in str(res["reason"])


def test_gate4_passes_uncorrelated():
    rng = np.random.default_rng(7)
    res = G.gate4_collinearity(
        "a9_zone_pct", rng.random(200), {"season_k_pct": rng.random(200)})
    assert res["passed"] is True


def test_gate4_unmeasured_is_none_not_pass():
    """The A-048 regression: an empty check must say UNMEASURED (None),
    never quietly PASS."""
    res = G.gate4_collinearity("a9_zone_pct", np.array([0.0]), {})
    assert res["passed"] is None
    assert "UNMEASURED" in res["reason"]


# ---------------------------------------------------------------- gate 5
def _g2(ece_a_base, ece_a_aug, ece_b_base, ece_b_aug):
    return {"splits": {
        "A": {"ece_base": ece_a_base, "ece_aug": ece_a_aug},
        "B": {"ece_base": ece_b_base, "ece_aug": ece_b_aug},
    }}


def test_gate5_fails_on_calibration_degradation():
    res = G.gate5_calibration("f", _g2(0.020, 0.040, 0.020, 0.021))
    assert res["passed"] is False


def test_gate5_passes_within_tolerance():
    res = G.gate5_calibration("f", _g2(0.020, 0.025, 0.030, 0.028))
    assert res["passed"] is True


def test_gate5_missing_ece_is_unjudged():
    res = G.gate5_calibration("f", _g2(None, None, 0.02, 0.02))
    assert res["passed"] is None


def test_gate5_is_not_a_gate2_echo():
    """A feature can improve Brier (Gate 2's quantity) while degrading
    calibration — Gate 5 must be able to fail it independently. The old
    implementation could not fail anything Gate 2 passed."""
    res = G.gate5_calibration("f", _g2(0.010, 0.045, 0.010, 0.012))
    assert res["passed"] is False


# ------------------------------------------------------------------ ece
def test_ece_orders_calibration_quality():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.1, 0.9, 4000)
    y_good = (rng.random(4000) < p).astype(float)
    y_bad = (rng.random(4000) < (1 - p)).astype(float)
    assert G._ece(p, y_good) < 0.05
    assert G._ece(p, y_bad) > 0.30


# ---------------------------------------------------------------- floor
def test_noise_floor_prefers_persisted_value(tmp_path, monkeypatch):
    stored = tmp_path / "floor.json"
    stored.write_text('{"p95": 0.321, "n_seeds": 20}', encoding="utf-8")
    monkeypatch.setattr(G, "NOISE_FLOOR_PATH", stored)
    monkeypatch.setattr(G, "NOISE_FLOOR_PCT", 0.167)
    G._load_noise_floor()
    assert G.NOISE_FLOOR_PCT == pytest.approx(0.321)
