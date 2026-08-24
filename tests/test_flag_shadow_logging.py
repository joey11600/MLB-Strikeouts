"""A-046: the two flag-off models must actually accumulate shadow evidence.

USE_HOOK_MIXTURE and USE_PRIOR_SEASON were parked behind OFF flags
"pending a 2-week shadow" with nothing logging their counterfactual
predictions — so the shadow clock could never start. These tests pin the
plumbing that fixes that:

  1. Stage A / predictor accept a per-call hook-mixture override that does
     not touch the module flag.
  2. The prior-season force path bypasses only the flag, never the
     substance bars.
  3. The model-log schema carries the two shadow columns, and the shared
     row builder fills them from a sidecar record.
  4. The sidecar writer persists and merges the shadow_prior_pitchers
     section without ever letting it leak onto the board list.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import models.stage_a_bf as sa
from strikeout_predictor import StrikeoutPredictor
import tools.daily_pipeline as dp
import tools.model_log as ml


FEATURES = {"a3_season_k_pct_shrunk": 0.24, "c1_bf_mean": 22.0,
            "c10_il_return": False, "c11_pitch_limit": None}


def _fitted_stage_a():
    a = sa.StageA()
    a.load()
    return a


def test_stage_a_override_matches_flag_on(monkeypatch):
    """predict_bf_distribution(use_hook_mixture=True) with the flag OFF
    must equal what the flag ON would serve — same code path, no drift."""
    a = _fitted_stage_a()
    assert sa.USE_HOOK_MIXTURE is False  # ships off (A-042)
    via_override = a.predict_bf_distribution(FEATURES, use_hook_mixture=True)
    monkeypatch.setattr(sa, "USE_HOOK_MIXTURE", True)
    via_flag = a.predict_bf_distribution(FEATURES)
    np.testing.assert_allclose(via_override, via_flag)


def test_stage_a_override_none_defers_to_flag():
    a = _fitted_stage_a()
    default = a.predict_bf_distribution(FEATURES)
    explicit_off = a.predict_bf_distribution(FEATURES, use_hook_mixture=False)
    np.testing.assert_allclose(default, explicit_off)


def test_mixture_shadow_prices_the_left_tail():
    """The mixture exists to price disaster starts; the shadow column must
    reflect that: P(BF <= 8) far larger than the plain NB's."""
    a = _fitted_stage_a()
    nb = a.predict_bf_distribution(FEATURES)
    mix = a.predict_bf_distribution(FEATURES, use_hook_mixture=True)
    assert mix[:9].sum() > 5 * nb[:9].sum()
    # and the conditional mean is preserved (A-042's load-bearing rule)
    n = np.arange(41)
    assert abs(float(n @ mix) - float(n @ nb)) < 0.15


def test_predictor_passes_override_through(monkeypatch):
    """predict(use_hook_mixture=True) with the flag off must equal what a
    flag-on predictor would serve. (No directional assertion: the
    mean-preserving re-centering can move P(over) either way at a given
    line — the hook mass cuts the tail, the re-centered normal arm adds
    to it.)"""
    p = StrikeoutPredictor()
    p.load_models()
    base = p.predict(FEATURES, lineup_k_pcts=[0.22] * 9, lines=[5.5])
    mix = p.predict(FEATURES, lineup_k_pcts=[0.22] * 9, lines=[5.5],
                    use_hook_mixture=True)
    assert mix["per_line_raw"][5.5] != base["per_line_raw"][5.5]
    monkeypatch.setattr(sa, "USE_HOOK_MIXTURE", True)
    flag_on = p.predict(FEATURES, lineup_k_pcts=[0.22] * 9, lines=[5.5])
    assert mix["per_line_raw"][5.5] == pytest.approx(
        flag_on["per_line_raw"][5.5], abs=1e-12)


def test_force_prior_bypasses_flag_not_substance():
    assert dp.USE_PRIOR_SEASON is False  # ships off
    good = {"prior_bf": 400, "prior_starts": 20}
    thin = {"prior_bf": 30, "prior_starts": 2}
    assert dp._prior_is_usable(good) is False           # flag off
    assert dp._prior_is_usable(good, force=True) is True
    assert dp._prior_is_usable(thin, force=True) is False  # bars still apply
    assert dp._prior_is_usable(None, force=True) is False


def test_model_log_fields_carry_shadow_columns():
    assert "p_over_hookmix" in ml.FIELDS
    assert "p_over_prior" in ml.FIELDS


def test_row_from_pitcher_fills_shadow_columns():
    p = {"game_pk": 1, "pitcher_id": 2, "pitcher_name": "A", "line": 5.5,
         "expected_bf": 22.0, "expected_k": 5.0, "p_over_raw": 0.5,
         "p_over_hookmix": 0.48, "p_over_prior": 0.51,
         "primary_units_risked": 0}
    row = ml._row_from_pitcher("2026-08-24", p, 24, 7, False, "now")
    assert row["p_over_hookmix"] == 0.48
    assert row["p_over_prior"] == 0.51
    assert row["over_hit"] == 1
    # unparseable line -> unscorable
    assert ml._row_from_pitcher("2026-08-24", {**p, "line": "6+"},
                                24, 7, False, "now") is None


def test_merge_union_refuses_to_shrink(tmp_path):
    path = tmp_path / "log.csv"
    rows = [{f: "" for f in ml.FIELDS} | {
        "date": "2026-08-24", "game_pk": str(i), "pitcher_id": "1",
        "pitcher_name": "X"} for i in range(3)]
    ml._write_atomic(path, rows)
    merged = ml._merge_union(path, [])
    assert len(merged) == 3  # empty fresh set keeps everything


def test_sidecar_shadow_section_persists_and_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "SLATES_DIR", tmp_path)
    pred = {"pitcher_id": 10, "pitcher_name": "Board Guy", "line": 5.5,
            "expected_k": 5.0, "expected_bf": 22.0, "k_dist": [1.0],
            "p_over_hookmix": 0.44, "p_over_prior": 0.46}
    shadow = {"pitcher_id": 99, "pitcher_name": "Recovered Guy",
              "game_pk": 7, "line": 4.5, "p_over_raw": 0.41,
              "fair_over": 0.5, "expected_k": 4.1, "expected_bf": 20.0,
              "recovered_reason": "insufficient data"}
    dp._write_slate_sidecar("2026-08-24", [pred], shadow_prior=[shadow])
    data = json.loads((tmp_path / "2026-08-24.json").read_text())
    assert data["shadow_prior_pitchers"][0]["pitcher_id"] == 99
    assert data["pitchers"][0]["p_over_hookmix"] == 0.44
    assert all(p["pitcher_id"] != 99 for p in data["pitchers"])

    # re-run without the shadow row: it must be carried, not dropped
    dp._write_slate_sidecar("2026-08-24", [pred], shadow_prior=[])
    data = json.loads((tmp_path / "2026-08-24.json").read_text())
    assert [p["pitcher_id"] for p in data["shadow_prior_pitchers"]] == [99]

    # once the pitcher graduates to the board, the shadow entry retires
    dp._write_slate_sidecar(
        "2026-08-24", [pred, {**pred, "pitcher_id": 99,
                              "pitcher_name": "Recovered Guy"}],
        shadow_prior=[])
    data = json.loads((tmp_path / "2026-08-24.json").read_text())
    assert data["shadow_prior_pitchers"] == []


def test_flags_still_off():
    """The shadow plumbing must not have flipped anything (CLAUDE.md:
    promotion is an operator decision after the 2-week window)."""
    assert sa.USE_HOOK_MIXTURE is False
    assert dp.USE_PRIOR_SEASON is False
