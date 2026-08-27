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
    _board_row(1002, 22, "Still Pitching", 15.5),  # live, never relieved
    _board_row(1003, 33, "Scratched Guy", 15.5),   # final, did not start
    _board_row(1004, 44, "Pulled Starter", 15.5),  # live, ALREADY relieved
]


def _fetch(url):
    if "schedule" in url:
        return {"dates": [{"games": [
            {"gamePk": 1001, "status": {"abstractGameState": "Final"}},
            {"gamePk": 1002, "status": {"abstractGameState": "Live"}},
            {"gamePk": 1003, "status": {"abstractGameState": "Final"}},
            {"gamePk": 1004, "status": {"abstractGameState": "Live"}},
        ]}]}
    if url.endswith("1001/boxscore"):
        return {"teams": {"home": {"pitchers": [11], "players": {
            "ID11": {"stats": {"pitching": {"inningsPitched": "5.2"}}}}},
            "away": {"pitchers": [99], "players": {}}}}
    if url.endswith("1002/boxscore"):
        # Still the only pitcher his team has used — he can come back
        # out for another inning, so his line is NOT settled. This is
        # the Tanner Gordon shape: 7.0 IP, side batting, no reliever yet.
        return {"teams": {"away": {"pitchers": [22], "players": {
            "ID22": {"stats": {"pitching": {"inningsPitched": "7.0"}}}}},
            "home": {"pitchers": [97], "players": {}}}}
    if url.endswith("1003/boxscore"):
        # someone else started; our pitcher never appears first
        return {"teams": {"home": {"pitchers": [44, 33], "players": {
            "ID33": {"stats": {"pitching": {"inningsPitched": "2.0"}}}}},
            "away": {"pitchers": [98], "players": {}}}}
    if url.endswith("1004/boxscore"):
        # A reliever has pitched after him: already-happened fact, his
        # total can no longer move even though the game is still on.
        return {"teams": {"home": {"pitchers": [44, 55], "players": {
            "ID44": {"stats": {"pitching": {"inningsPitched": "6.0"}}},
            "ID55": {"stats": {"pitching": {"inningsPitched": "1.0"}}}}},
            "away": {"pitchers": [96], "players": {}}}}
    raise AssertionError(f"unexpected fetch {url}")


def test_settled_lines_grade_unsettled_ones_do_not():
    """Final games and RELIEVED starters grade; a starter still in a
    live game does not, and neither does a scratch."""
    rows = boxscore_rows("2026-08-24", BOARD, fetch=_fetch)
    assert sorted(r["pitcher_name"] for r in rows) == [
        "Final Starter", "Pulled Starter"]
    by_name = {r["pitcher_name"]: r for r in rows}
    assert by_name["Final Starter"]["actual_outs"] == 17      # 5.2 IP
    assert by_name["Final Starter"]["over_hit"] == 1          # 17 > 15.5
    assert by_name["Final Starter"]["is_home"] == 1
    assert by_name["Pulled Starter"]["actual_outs"] == 18     # 6.0 IP
    assert by_name["Pulled Starter"]["over_hit"] == 1


def test_relieved_starter_in_a_live_game_grades_on_the_next_pass():
    """The one still pitching settles the moment a reliever appears —
    nothing about the game's own state has to change."""
    board = [_board_row(1002, 22, "Still Pitching", 15.5)]
    assert boxscore_rows("2026-08-24", board, fetch=_fetch) == []

    def _relieved(url):
        if url.endswith("1002/boxscore"):
            return {"teams": {"away": {"pitchers": [22, 23], "players": {
                "ID22": {"stats": {"pitching": {"inningsPitched": "7.0"}}},
                "ID23": {"stats": {"pitching": {"inningsPitched": "0.1"}}}}},
                "home": {"pitchers": [97], "players": {}}}}
        return _fetch(url)

    rows = boxscore_rows("2026-08-24", board, fetch=_relieved)
    assert len(rows) == 1
    assert rows[0]["actual_outs"] == 21          # 7.0 IP, game still live
    assert rows[0]["over_hit"] == 1


def test_exact_line_land_is_not_an_over():
    board = [_board_row(1001, 11, "Final Starter", 17.0)]
    rows = boxscore_rows("2026-08-24", board, fetch=_fetch)
    assert rows[0]["over_hit"] == 0              # 17 is not > 17.0


def test_grade_recent_finals_grades_today_but_only_settled(tmp_path,
                                                           monkeypatch):
    """TODAY is in scope -- a slate settles the same night instead of
    waiting for the ET date to roll -- but a starter still pitching in
    a live game does not. Dates outside the window are never fetched,
    and a date with nothing left outstanding costs zero API calls."""
    import csv
    from datetime import datetime, timedelta

    today = datetime.now(outs_boxscore.ET).date()
    t = today.isoformat()
    y = (today - timedelta(days=1)).isoformat()
    old = (today - timedelta(days=9)).isoformat()
    boards = {
        y: [dict(_board_row(1001, 11, "Final Starter", 15.5), date=y)],
        t: [dict(_board_row(1001, 11, "Final Starter", 15.5), date=t),
            dict(_board_row(1002, 22, "Live Starter", 15.5), date=t)],
        old: [dict(_board_row(1001, 11, "Final Starter", 15.5), date=old)],
    }

    log = tmp_path / "outs_model_log.csv"
    monkeypatch.setattr(outs_boxscore, "OUTS_LOG_PATH", log)
    import tools.outs_serve as srv
    monkeypatch.setattr(srv, "OUTS_LOG_PATH", log)
    monkeypatch.setattr(outs_boxscore, "available_dates",
                        lambda: sorted(boards, reverse=True))
    monkeypatch.setattr(outs_boxscore, "load_slate",
                        lambda d: {"board": boards[d]})

    calls = []

    def counting_fetch(url):
        calls.append(url)
        return _fetch(url)

    # Yesterday's final AND today's final; today's live game does not.
    assert grade_recent_finals(fetch=counting_fetch) == 2
    assert any(t in u for u in calls)              # today IS in scope now
    assert all(old not in u for u in calls)        # outside the window
    with open(log) as f:
        rows = list(csv.DictReader(f))
    assert sorted((r["date"], r["actual_outs"]) for r in rows) == [
        (y, "17"), (t, "17")]

    # Second pass: yesterday is complete and costs nothing; today still
    # has the live starter outstanding, so it re-checks the schedule and
    # still refuses to grade him.
    calls.clear()
    assert grade_recent_finals(fetch=counting_fetch) == 0
    assert all(y not in u for u in calls)
    assert any(t in u for u in calls)
