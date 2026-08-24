"""Outs serving core (Phase 10): calibration plumbing, today-row
leakage guarantees, sidecar merge, and log union discipline."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.outs_serve as OS
from features.outs_asof import build_outs_asof, FEATURE_COLS


# ----------------------------------------------------------- calibrate
def test_calibrate_identity_clamp_when_absent():
    p = np.array([0.0, 0.5, 1.0])
    out = OS.calibrate(p, None)
    assert out[0] == pytest.approx(1e-3)
    assert out[1] == pytest.approx(0.5)
    assert out[2] == pytest.approx(1 - 1e-3)


def test_calibrate_platt_kind():
    cal = {"kind": "platt", "a": 0.0, "b": 1.0, "prob_eps": 1e-3}
    p = np.array([0.2, 0.5, 0.8])
    np.testing.assert_allclose(OS.calibrate(p, cal), p, atol=1e-9)
    shifted = OS.calibrate(p, {"kind": "platt", "a": 0.5, "b": 1.0,
                               "prob_eps": 1e-3})
    assert (shifted > p).all()


# ------------------------------------------------- today-row leakage
def _hist_starts():
    rows = []
    for g in range(6):
        rows.append({
            "game_pk": 100 + g, "game_date": pd.Timestamp(f"2026-06-{g+1:02d}"),
            "pitcher": 1, "is_home": g % 2, "opponent_team": "BBB",
            "outs": 15 + g, "pitches": 90.0 + g,
            "days_since_prev_game": 5,
        })
    return pd.DataFrame(rows)


def _today_row(outs_placeholder=0, pitches_placeholder=0.0):
    return pd.DataFrame([{
        "game_pk": 999, "game_date": pd.Timestamp("2026-06-10"),
        "pitcher": 1, "is_home": 1, "opponent_team": "BBB",
        "outs": outs_placeholder, "pitches": pitches_placeholder,
        "days_since_prev_game": 4,
    }])


def test_today_row_placeholder_labels_are_inert():
    """The whole serving design rests on this: a today-row's own label
    placeholders must not move its own features one bit."""
    a = build_outs_asof(pd.concat([_hist_starts(), _today_row(0, 0.0)],
                                  ignore_index=True))
    b = build_outs_asof(pd.concat([_hist_starts(), _today_row(27, 120.0)],
                                  ignore_index=True))
    cols = [c for c in FEATURE_COLS if c in a.columns]
    ta = a[a["game_pk"] == 999][cols].reset_index(drop=True)
    tb = b[b["game_pk"] == 999][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(ta, tb)


def test_appending_today_does_not_move_history():
    hist_only = build_outs_asof(_hist_starts())
    with_today = build_outs_asof(
        pd.concat([_hist_starts(), _today_row()], ignore_index=True))
    cols = [c for c in FEATURE_COLS if c in hist_only.columns]
    h1 = hist_only[cols].reset_index(drop=True)
    h2 = with_today[with_today["game_pk"] != 999][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(h1, h2)


def test_today_row_sees_its_prior_history():
    feat = build_outs_asof(
        pd.concat([_hist_starts(), _today_row()], ignore_index=True))
    t = feat[feat["game_pk"] == 999].iloc[0]
    # expanding mean outs over the 6 prior starts: (15+16+17+18+19+20)/6
    assert t["exp_o"] == pytest.approx(17.5)
    # p5: mean pitch count of last 5 priors (91..95)
    assert t["p5_pitches"] == pytest.approx(np.mean([91, 92, 93, 94, 95]))


# ------------------------------------------------------------- sidecar
def test_write_slate_merges_newest_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(OS, "OUTS_SLATES_DIR", tmp_path)
    OS.write_slate("2026-08-24", [
        {"pitcher_id": 1, "p_over_cal": 0.5},
        {"pitcher_id": 2, "p_over_cal": 0.4},
    ])
    # re-price sees only pitcher 1 (game 2 started) with a new number
    OS.write_slate("2026-08-24", [{"pitcher_id": 1, "p_over_cal": 0.6}])
    slate = json.loads((tmp_path / "2026-08-24.json").read_text())
    by_id = {r["pitcher_id"]: r for r in slate["board"]}
    assert by_id[1]["p_over_cal"] == 0.6      # fresh wins
    assert by_id[2]["p_over_cal"] == 0.4      # carried, not dropped
    assert slate["diagnostic_only"] is True
    assert slate["market"] == "OUTS"


# ------------------------------------------------------------ log rules
def test_log_dates_scores_and_never_shrinks(tmp_path, monkeypatch):
    monkeypatch.setattr(OS, "OUTS_SLATES_DIR", tmp_path / "slates")
    monkeypatch.setattr(OS, "OUTS_LOG_PATH", tmp_path / "log.csv")
    (tmp_path / "slates").mkdir()
    OS.write_slate("2026-08-20", [{
        "date": "2026-08-20", "game_pk": 7, "pitcher_id": 1,
        "pitcher_name": "Arm", "pitcher_team": "AAA",
        "opponent_team": "BBB", "is_home": True, "line": 16.5,
        "over_odds": "-110", "under_odds": "-110", "odds_source": "live",
        "expected_outs": 16.0, "p_over_raw": 0.5, "p_over_cal": 0.5,
        "fair_over": 0.5, "hold_pct": 0.05,
    }])

    import tools.build_outs_dataset as BOD
    fake = pd.DataFrame([{"game_pk": 7, "pitcher": 1, "outs": 18}])
    monkeypatch.setattr(BOD, "load_outs_starts",
                        lambda *a, **k: fake)
    n = OS.log_dates()
    assert n == 1
    rows = list(__import__("csv").DictReader(
        open(tmp_path / "log.csv", encoding="utf-8")))
    assert rows[0]["actual_outs"] == "18"
    assert rows[0]["over_hit"] == "1"     # 18 > 16.5

    # a rerun that can re-derive nothing must not shrink the log
    monkeypatch.setattr(BOD, "load_outs_starts",
                        lambda *a, **k: pd.DataFrame(
                            [{"game_pk": 0, "pitcher": 0, "outs": 0}]))
    OS.log_dates()
    rows2 = list(__import__("csv").DictReader(
        open(tmp_path / "log.csv", encoding="utf-8")))
    assert len(rows2) == 1
