"""A start that crosses midnight ET must not stay "IN GAME" (AUDIT A-039).

`poll_once()` asked for ONE date and `main()` asked for today, so at
00:00 ET the poller moved to the new date and never looked at yesterday
again. Any starter still on the mound at that moment was abandoned in
progress: the archive kept `status: in_game` forever, and because the
board reads `live.final` to decide whether a total can still move, those
rows showed a pulsing "IN GAME" beside a K count that had been settled
for hours.

Measured on the served payload 2026-08-13, across both days the archive
has existed -- so this hit 2 days out of 2, not occasionally:

    2026-08-11  Nick Martinez                  21:40 ET first pitch
    2026-08-12  Eric Lauer, George Klassen     22:10 ET first pitch

No early game was ever affected, which is the signature of a midnight
cutoff rather than a bad feed.

Two defences, because they fail independently. The poller now finishes
yesterday before it starts today; and the board treats a settled
Statcast total as outranking a stopped poll, which also clears the rows
already frozen in the archive that no future poll will revisit.

Run:  python -m pytest tests/test_live_carryover.py -q
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")

ET = ZoneInfo("America/New_York")
YESTERDAY = "2026-08-12"
TODAY = "2026-08-13"

# Eric Lauer, 22:10 ET first pitch: still in the sixth when the date
# rolled, so the poller left him there.
STUCK = {"pitcher_id": 641778, "pitcher_name": "Eric Lauer", "strikeouts": 5,
         "batters_faced": 26, "innings": "6.0", "final": False,
         "game_state": "In Progress", "status": "in_game"}
DONE = {"pitcher_id": 605400, "pitcher_name": "Merrill Kelly", "strikeouts": 2,
        "batters_faced": 23, "innings": "5.0", "final": True,
        "game_state": "Final", "status": "final"}


def _payload(iso: str, pitchers: list[dict]) -> dict:
    return {
        "date": iso,
        "updated_at": f"{iso}T23:58:00-04:00",
        "source": "mlb_stats_api",
        "n_tracked": len(pitchers),
        "n_reported": len(pitchers),
        "n_final": sum(1 for p in pitchers if p.get("final")),
        "any_live": any(not p.get("final") for p in pitchers),
        "pitchers": pitchers,
    }


def _archive(lw, tmp_path, monkeypatch, iso, pitchers):
    monkeypatch.setattr(lw, "STATE_PATH", tmp_path / "live_state.json")
    monkeypatch.setattr(lw, "LIVE_DIR", tmp_path / "live")
    lw.archive_state(_payload(iso, pitchers))


def test_carryover_claims_yesterday_while_a_starter_is_unfinished(
        tmp_path, monkeypatch):
    """The defect, at the seam that had it.

    00:30 ET, yesterday's archive holds a starter mid-game: the poller
    has to go back for him. The old code had no concept of this and
    simply never returned.
    """
    import workers.live_strikeouts as lw

    _archive(lw, tmp_path, monkeypatch, YESTERDAY, [DONE, STUCK])
    now = datetime(2026, 8, 13, 0, 30, tzinfo=ET)

    assert lw.carryover_date(now) == YESTERDAY


def test_carryover_stops_once_every_starter_is_final(tmp_path, monkeypatch):
    """It must let go, or it polls the past forever."""
    import workers.live_strikeouts as lw

    _archive(lw, tmp_path, monkeypatch, YESTERDAY, [DONE])
    now = datetime(2026, 8, 13, 0, 30, tzinfo=ET)

    assert lw.carryover_date(now) is None


def test_carryover_is_bounded_by_the_cutoff_hour(tmp_path, monkeypatch):
    """A game that never reaches a terminal state cannot pin the poller.

    Suspended and resumed-days-later games exist. They are the grader's
    problem; the live board must still move on.
    """
    import workers.live_strikeouts as lw

    _archive(lw, tmp_path, monkeypatch, YESTERDAY, [DONE, STUCK])

    assert lw.carryover_date(datetime(2026, 8, 13, 11, 59, tzinfo=ET)) == YESTERDAY
    assert lw.carryover_date(datetime(2026, 8, 13, 12, 0, tzinfo=ET)) is None
    assert lw.carryover_date(datetime(2026, 8, 13, 18, 0, tzinfo=ET)) is None


def test_carryover_archives_without_touching_todays_live_state(
        tmp_path, monkeypatch):
    """live_state.json means NOW, and now is today.

    Finishing yesterday must update yesterday's archive only. Writing it
    through the single-file path would make the /live.json view claim
    the board is on last night's games.
    """
    import workers.live_strikeouts as lw

    monkeypatch.setattr(lw, "STATE_PATH", tmp_path / "live_state.json")
    monkeypatch.setattr(lw, "LIVE_DIR", tmp_path / "live")

    lw.write_state(_payload(TODAY, []))          # today's poll, no games yet
    lw.archive_state(_payload(YESTERDAY, [DONE, STUCK]))  # the carryover

    single = json.loads((tmp_path / "live_state.json").read_text(encoding="utf-8"))
    assert single["date"] == TODAY, "the carryover overwrote 'now'"
    assert len(lw.read_archived_state(YESTERDAY)["pitchers"]) == 2


def test_a_partial_poll_never_drops_an_already_final_starter(
        tmp_path, monkeypatch):
    """One failed boxscore fetch `continue`s past that pitcher.

    So a cycle can report a strict subset of the board. Overwriting the
    archive with it would blank a starter who was already final -- the
    disappearing-results failure of A-035, reintroduced through a
    different door.
    """
    import workers.live_strikeouts as lw

    monkeypatch.setattr(lw, "STATE_PATH", tmp_path / "live_state.json")
    monkeypatch.setattr(lw, "LIVE_DIR", tmp_path / "live")

    lw.archive_state(_payload(YESTERDAY, [DONE, STUCK]))
    # Next cycle: Merrill Kelly's boxscore call failed, so only the
    # carried-over starter comes back -- now finished.
    finished = {**STUCK, "strikeouts": 6, "final": True,
                "game_state": "Final", "status": "final"}
    lw.archive_state(_payload(YESTERDAY, [finished]))

    kept = {r["pitcher_id"]: r for r in lw.read_archived_state(YESTERDAY)["pitchers"]}
    assert set(kept) == {605400, 641778}, "a partial poll dropped a starter"
    assert kept[641778]["final"] is True, "the carryover result was not recorded"
    assert kept[641778]["strikeouts"] == 6


# --- second defence: the board, for rows already frozen on disk -------

MODEL_LOG_HEADER = (
    "date,game_pk,pitcher_id,pitcher_name,line,expected_k,actual_k,actual_bf\n")


def _board(tmp_path, monkeypatch, *, settled: bool):
    """One slate holding one starter the poller left mid-game."""
    import tools.dashboard_data as dd

    slates = tmp_path / "slates"
    slates.mkdir()
    (slates / f"{YESTERDAY}.json").write_text(json.dumps({
        "date": YESTERDAY,
        "pitchers": [{"pitcher_id": 641778, "pitcher_name": "Eric Lauer",
                      # A game_pk Statcast cannot know, so the settled
                      # total comes from the evidence table or nowhere.
                      "game_pk": 999001, "line": 4.5, "ladder": []}],
    }), encoding="utf-8")

    log = tmp_path / "model_log.csv"
    log.write_text(
        MODEL_LOG_HEADER + (
            f"{YESTERDAY},999001,641778,Eric Lauer,4.5,3.3,6,26\n"
            if settled else ""),
        encoding="utf-8")

    (tmp_path / "live").mkdir()
    (tmp_path / "live" / f"{YESTERDAY}.json").write_text(
        json.dumps(_payload(YESTERDAY, [STUCK])), encoding="utf-8")

    monkeypatch.setattr(dd, "SLATES_DIR", slates)
    monkeypatch.setattr(dd, "MODEL_LOG_PATH", log)
    monkeypatch.setattr(dd, "DATA_STATE_DIR", tmp_path)

    built, _dates = dd._build_slates([])
    return built[YESTERDAY]["pitchers"][0]


def test_a_settled_total_overrides_a_stopped_poll(tmp_path, monkeypatch):
    """The rows already frozen on disk, which no future poll revisits.

    The poller fix stops new ones, but 2026-08-11 and 2026-08-12 are
    archived stuck and the carryover window for them closed long ago.
    Statcast is the graded source of truth per CLAUDE.md, so a settled
    total has to win over a poll that stopped mid-game.
    """
    entry = _board(tmp_path, monkeypatch, settled=True)

    assert entry["actual_strikeouts"] == 6, "settled total did not reach the board"
    assert entry["live"]["final"] is True, 'still renders as "IN GAME"'
    assert entry["live"]["status"] == "final"
    assert entry["live"]["stale_poll"] is True, "the override was not flagged"
    # The last in-game observation is kept as observed, not rewritten to
    # match: they legitimately disagree, and hiding that hides the bug.
    assert entry["live"]["strikeouts"] == 5


def test_a_genuinely_live_start_is_left_in_game(tmp_path, monkeypatch):
    """The override must not fire without a settled total to stand on.

    A pitcher in the fourth has no Statcast row yet. Calling him final
    would settle a total that can still move -- the exact failure this
    repo keeps paying for, arrived at from the opposite direction.
    """
    entry = _board(tmp_path, monkeypatch, settled=False)

    assert entry["live"]["final"] is False, "an in-progress start was called final"
    assert entry["live"]["status"] == "in_game"
    assert "stale_poll" not in entry["live"]
    assert entry["result_source"] == "live"
