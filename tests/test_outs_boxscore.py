"""Boxscore grader: final-only, starter-only, union semantics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import outs_boxscore
from tools.outs_boxscore import boxscore_rows, grade_recent_finals


def _board_row(gpk, pid, name, line):
    return {"date": "2026-08-24", "game_pk": gpk, "pitcher_id": pid,
            "pitcher_name": name, "pitcher_team": "AAA",
            "opponent_team": "BBB", "is_home": True, "line": line,
            "over_odds": "-110", "under_odds": "-110",
            "odds_source": "live", "expected_outs": 15.0,
            "p_over_raw": 0.5, "p_over_cal": 0.5, "fair_over": 0.5,
            "hold_pct": 0.045}


BOARD = [
    _board_row(1001, 11, "Final Starter", 15.5),   # final, starter, 17 outs
    _board_row(1002, 22, "Live Starter", 15.5),    # game still live
    _board_row(1003, 33, "Scratched Guy", 15.5),   # final, did not start
]


def _fetch(url):
    if "schedule" in url:
        return {"dates": [{"games": [
            {"gamePk": 1001, "status": {"abstractGameState": "Final"}},
            {"gamePk": 1002, "status": {"abstractGameState": "Live"}},
            {"gamePk": 1003, "status": {"abstractGameState": "Final"}},
        ]}]}
    if url.endswith("1001/boxscore"):
        return {"teams": {"home": {"pitchers": [11], "players": {
            "ID11": {"stats": {"pitching": {"inningsPitched": "5.2"}}}}},
            "away": {"pitchers": [99], "players": {}}}}
    if url.endswith("1003/boxscore"):
        # someone else started; our pitcher never appears first
        return {"teams": {"home": {"pitchers": [44, 33], "players": {
            "ID33": {"stats": {"pitching": {"inningsPitched": "2.0"}}}}},
            "away": {"pitchers": [98], "players": {}}}}
    raise AssertionError(f"unexpected fetch {url}")


def test_final_starter_grades_live_and_scratch_do_not():
    rows = boxscore_rows("2026-08-24", BOARD, fetch=_fetch)
    assert [r["pitcher_name"] for r in rows] == ["Final Starter"]
    assert rows[0]["actual_outs"] == 17          # 5.2 IP
    assert rows[0]["over_hit"] == 1              # 17 > 15.5
    assert rows[0]["is_home"] == 1


def test_exact_line_land_is_not_an_over():
    board = [_board_row(1001, 11, "Final Starter", 17.0)]
    rows = boxscore_rows("2026-08-24", board, fetch=_fetch)
    assert rows[0]["over_hit"] == 0              # 17 is not > 17.0


def test_grade_recent_finals_scopes_and_skips(tmp_path, monkeypatch):
    """Only recent PAST ET dates fetch; fully graded dates cost zero
    API calls; today's slate is never touched."""
    import csv
    from datetime import datetime, timedelta

    today = datetime.now(outs_boxscore.ET).date()
    y = (today - timedelta(days=1)).isoformat()
    board = [dict(_board_row(1001, 11, "Final Starter", 15.5), date=y)]

    log = tmp_path / "outs_model_log.csv"
    monkeypatch.setattr(outs_boxscore, "OUTS_LOG_PATH", log)
    import tools.outs_serve as srv
    monkeypatch.setattr(srv, "OUTS_LOG_PATH", log)
    monkeypatch.setattr(outs_boxscore, "available_dates",
                        lambda: [today.isoformat(), y])
    monkeypatch.setattr(outs_boxscore, "load_slate",
                        lambda d: {"board": board})

    calls = []

    def counting_fetch(url):
        calls.append(url)
        return _fetch(url)

    assert grade_recent_finals(fetch=counting_fetch) == 1
    assert all(today.isoformat() not in u for u in calls)
    with open(log) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["actual_outs"] == "17"

    calls.clear()
    assert grade_recent_finals(fetch=counting_fetch) == 0
    assert calls == []                           # nothing missing, no API
