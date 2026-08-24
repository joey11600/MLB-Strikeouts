"""The 2026-08-24 watchdog additions must be able to FAIL (A-048's
lesson applied to the monitor itself): each check gets a synthetic
red-condition fixture and a green one."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.watchdog as W


@pytest.fixture
def env(tmp_path, monkeypatch):
    slates = tmp_path / "slates"
    odds = tmp_path / "odds"
    slates.mkdir()
    odds.mkdir()
    monkeypatch.setattr(W, "SLATES", slates)
    monkeypatch.setattr(W, "ODDS", odds)
    monkeypatch.setattr(W, "MODEL_LOG", tmp_path / "model_log.csv")
    monkeypatch.setattr(W, "ROOT", tmp_path)
    return tmp_path


def _slate(env, date_s, payload):
    (env / "slates" / f"{date_s}.json").write_text(
        json.dumps({"date": date_s, **payload}), encoding="utf-8")


def _report():
    return W.Report()


TODAY = W._today().isoformat()


# ------------------------------------------------------------- ladder
def _pitcher(name, units, side, ek, line, statuses):
    return {"pitcher_name": name, "primary_units_risked": units,
            "best_side": side, "expected_k": ek, "line": line,
            "ladder": [{"status": s} for s in statuses]}


def test_ladder_fails_on_a047_signature(env):
    _slate(env, TODAY, {"pitchers": [
        _pitcher("A", 2.0, "OVER", 7.5, 5.5,
                 ["passed_gap_gate", "passed_gap_gate", "primary_equivalent"]),
    ]})
    r = _report()
    W.check_ladder_evaluates(r)
    assert r.failures and "A-047" in r.failures[0]["why_it_matters"]


def test_ladder_ok_when_gate_idle(env):
    _slate(env, TODAY, {"pitchers": [
        _pitcher("A", 0.0, "UNDER", 4.0, 5.5, ["passed_gap_gate"] * 3),
    ]})
    r = _report()
    W.check_ladder_evaluates(r)
    assert not r.failures
    assert "gate idle" in r.rows[0]["detail"]


def test_ladder_ok_when_gate_open_and_rungs_pass(env):
    _slate(env, TODAY, {"pitchers": [
        _pitcher("A", 2.0, "OVER", 7.5, 5.5,
                 ["candidate", "passed_no_edge"]),
    ]})
    r = _report()
    W.check_ladder_evaluates(r)
    assert not r.failures


# ------------------------------------------------------ props accounted
def _intraday(env, date_s, names):
    p = env / "odds" / f"intraday_{date_s}.csv"
    lines = ["captured_at,date,pitcher_name,line,over_odds,under_odds,odds_source"]
    lines += [f"t,{date_s},{n},5.5,-110,-110,live" for n in names]
    p.write_text("\n".join(lines), encoding="utf-8")


def test_props_fail_when_a_name_vanishes(env):
    _intraday(env, TODAY, ["Zack Wheeler", "Ghost Pitcher"])
    _slate(env, TODAY, {
        "pitchers": [{"pitcher_name": "Zack Wheeler"}],
        "shadow_prior_pitchers": [], "skipped": []})
    r = _report()
    W.check_props_all_accounted(r)
    assert r.failures and "ghost pitcher" in r.failures[0]["detail"]


def test_props_ok_when_skip_ledger_covers(env):
    _intraday(env, TODAY, ["Zack Wheeler", "Fringe Arm"])
    _slate(env, TODAY, {
        "pitchers": [{"pitcher_name": "Zack Wheeler"}],
        "shadow_prior_pitchers": [],
        "skipped": [{"pitcher_name": "Fringe Arm",
                     "reason": "insufficient data"}]})
    r = _report()
    W.check_props_all_accounted(r)
    assert not r.failures
    assert "all accounted" in r.rows[0]["detail"]


# --------------------------------------------------------- stale polls
def _data_json(env, slates):
    d = env / "dashboard" / "public"
    d.mkdir(parents=True)
    (d / "data.json").write_text(json.dumps({"slates": slates}),
                                 encoding="utf-8")


def test_stale_poll_warns_after_fix_date(env):
    _data_json(env, {"2026-08-20": {"pitchers": [
        {"pitcher_name": "X", "live": {"stale_poll": True}}]}})
    r = _report()
    W.check_no_stale_polls(r)
    assert r.warnings and "2026-08-20:X" in r.warnings[0]["detail"]


def test_stale_poll_ignores_prefix_archive(env):
    _data_json(env, {"2026-08-12": {"pitchers": [
        {"pitcher_name": "X", "live": {"stale_poll": True}}]}})
    r = _report()
    W.check_no_stale_polls(r)
    assert not r.warnings and not r.failures


# ---------------------------------------------------- shadow recording
def _model_log(env, rows):
    cols = ["date", "pitcher_name", "reconstructed", "p_over_hookmix",
            "p_over_candidate", "p_over_re"]
    lines = [",".join(cols)]
    for row in rows:
        lines.append(",".join(str(row.get(c, "")) for c in cols))
    (env / "model_log.csv").write_text("\n".join(lines), encoding="utf-8")


def test_shadow_recording_fails_when_clock_stops(env):
    _model_log(env, [
        {"date": "2026-08-26", "pitcher_name": "A", "reconstructed": 0},
        {"date": "2026-08-26", "pitcher_name": "B", "reconstructed": 0},
    ])
    r = _report()
    W.check_shadow_columns_recording(r)
    assert r.failures and "clocks stopped" in r.failures[0]["why_it_matters"]


def test_shadow_recording_ok_with_values(env):
    _model_log(env, [
        {"date": "2026-08-26", "pitcher_name": "A", "reconstructed": 0,
         "p_over_hookmix": 0.44, "p_over_candidate": 0.45,
         "p_over_re": 0.43},
    ])
    r = _report()
    W.check_shadow_columns_recording(r)
    assert not r.failures


def test_shadow_recording_not_judgeable_before_wired_date(env):
    _model_log(env, [
        {"date": "2026-08-20", "pitcher_name": "A", "reconstructed": 0},
    ])
    r = _report()
    W.check_shadow_columns_recording(r)
    assert not r.failures
    assert "not judgeable" in r.rows[0]["detail"]
