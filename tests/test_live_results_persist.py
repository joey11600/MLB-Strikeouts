"""Yesterday's results must survive midnight (AUDIT A-035).

A starter's strikeout total is settled the moment he is pulled, and the
MLB Stats API has it immediately -- which is the whole reason
`workers/live_strikeouts.py` exists (A-020). But the poller wrote a
SINGLE `live_state.json` that it overwrote every 30 seconds with
`today_et()`, and `dashboard_data._load_live_state()` then discarded the
file unless its date was today's.

So at midnight ET the file rolled to the new date, yesterday's finals
ceased to exist, and the only other board-wide source of a K total --
Statcast -- does not publish until ~09:00 ET (A-022). Every night, the
previous day's board went blank for every starter who was not a graded
bet, and refilled mid-morning.

Measured on 2026-08-11 at 07:48 ET: the 2026-08-10 slate served 1 of 18
pitchers with an actual strikeout total. Every other date in the payload
was complete (26/26, 28/28, 20/20, 25/25, 27/27, 27/28).

Run:  python -m pytest tests/test_live_results_persist.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")

YESTERDAY = "2026-08-10"
TODAY = "2026-08-11"


def _payload(iso: str, pitchers: list[dict]) -> dict:
    return {
        "date": iso,
        "updated_at": f"{iso}T23:55:00-04:00",
        "source": "mlb_stats_api",
        "n_tracked": len(pitchers),
        "n_reported": len(pitchers),
        "n_final": sum(1 for p in pitchers if p.get("final")),
        "any_live": False,
        "pitchers": pitchers,
    }


FINALS = [
    {"pitcher_id": 669372, "pitcher_name": "J.T. Ginn", "strikeouts": 7,
     "batters_faced": 24, "final": True, "status": "final"},
    {"pitcher_id": 690928, "pitcher_name": "Hunter Dobbins", "strikeouts": 6,
     "batters_faced": 22, "final": True, "status": "final"},
]


def test_yesterdays_finals_are_readable_the_next_morning(tmp_path, monkeypatch):
    """The defect, at the seam that had it.

    With only an archive for YESTERDAY on disk and the clock reading
    TODAY, asking for YESTERDAY must still return its finals. The old
    code returned {} here, which is what emptied the board.
    """
    import tools.dashboard_data as dd

    (tmp_path / "live").mkdir()
    (tmp_path / "live" / f"{YESTERDAY}.json").write_text(
        json.dumps(_payload(YESTERDAY, FINALS)), encoding="utf-8")
    # The poller has already rolled over to an empty new day.
    (tmp_path / "live_state.json").write_text(
        json.dumps(_payload(TODAY, [])), encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_STATE_DIR", tmp_path)

    rows = dd._live_rows_for(YESTERDAY)

    assert set(rows) == {669372, 690928}, (
        f"yesterday's finals did not survive the rollover: {rows}"
    )
    assert rows[690928]["strikeouts"] == 6


def test_live_rows_never_leak_onto_another_date(tmp_path, monkeypatch):
    """The today-guard was load-bearing; per-date lookup must keep it.

    These rows are keyed by pitcher_id alone and a starter appears on
    many dates, so a payload applied to the wrong slate would attach one
    night's strikeouts to another night's start -- a fabricated result,
    which is worse than a blank one.
    """
    import tools.dashboard_data as dd

    (tmp_path / "live").mkdir()
    (tmp_path / "live" / f"{YESTERDAY}.json").write_text(
        json.dumps(_payload(YESTERDAY, FINALS)), encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_STATE_DIR", tmp_path)

    assert dd._live_rows_for("2026-08-04") == {}
    assert dd._live_rows_for(TODAY) == {}


def test_legacy_single_file_still_works_for_its_own_date(tmp_path, monkeypatch):
    """A container running the old poller must not go blank mid-upgrade.

    The archive does not exist until the new poller writes one, so the
    fallback has to cover the gap -- but only for the date the file
    actually describes.
    """
    import tools.dashboard_data as dd

    (tmp_path / "live_state.json").write_text(
        json.dumps(_payload(TODAY, FINALS)), encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_STATE_DIR", tmp_path)

    assert set(dd._live_rows_for(TODAY)) == {669372, 690928}
    assert dd._live_rows_for(YESTERDAY) == {}


def test_rollover_poll_does_not_erase_a_day_that_has_finals(tmp_path, monkeypatch):
    """The first poll after midnight reports the new date with no games.

    On a day with no slate EVERY poll does. If an empty payload could
    overwrite an archived day, the hole this closes would reopen at
    precisely the moment it matters -- so a date's record only grows.
    """
    import workers.live_strikeouts as lw

    monkeypatch.setattr(lw, "STATE_PATH", tmp_path / "live_state.json")
    monkeypatch.setattr(lw, "LIVE_DIR", tmp_path / "live")

    lw.write_state(_payload(YESTERDAY, FINALS))
    assert len(lw.read_archived_state(YESTERDAY)["pitchers"]) == 2

    # Same date, nothing reported — a transient API failure or an
    # early-morning poll before the boxscores answer.
    lw.write_state(_payload(YESTERDAY, []))

    kept = lw.read_archived_state(YESTERDAY)
    assert len(kept["pitchers"]) == 2, "an empty poll erased the day's finals"


def test_archive_is_filed_under_the_payloads_date(tmp_path, monkeypatch):
    """A poll straddling midnight files under the date it is ABOUT."""
    import workers.live_strikeouts as lw

    monkeypatch.setattr(lw, "STATE_PATH", tmp_path / "live_state.json")
    monkeypatch.setattr(lw, "LIVE_DIR", tmp_path / "live")

    lw.write_state(_payload(YESTERDAY, FINALS))

    assert (tmp_path / "live" / f"{YESTERDAY}.json").is_file()
    assert not (tmp_path / "live" / f"{TODAY}.json").exists()
    # The single-file path must keep working — /live.json serves it.
    assert json.loads(
        (tmp_path / "live_state.json").read_text(encoding="utf-8")
    )["date"] == YESTERDAY
