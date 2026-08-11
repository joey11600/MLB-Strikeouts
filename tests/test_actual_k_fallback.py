"""The board must not depend on one host's Statcast cache (AUDIT A-036).

`_actual_k_lookup` read the Statcast cache and nothing else. That cache
is a ~90 MB per-season tree each host tops up on its own schedule: CI
restores it every run, while the Railway worker refreshes it at boot and
on the 03:00 job -- both of which land BEFORE Statcast publishes the
previous day (A-022).

Measured 2026-08-11, from the same commit:

    chore(ci): 09:01 ET   2026-08-10 -> 18/18 actual K totals
    worker,    09:05 ET   2026-08-10 ->  1/18

`dashboard/lib/data-context.tsx` PREFERS the worker's payload, so the
site served the blank one, and the worker committed it over CI's good
copy every five minutes.

`model_log.csv` carries the same numbers, is small enough to ride the
ledger reconcile, and is never-delete-rows by policy -- so it cannot be
fresh on one host and stale on another.

Run:  python -m pytest tests/test_actual_k_fallback.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")

DATES = {"2026-08-10"}

HEADER = "date,game_pk,pitcher_id,pitcher_name,line,expected_k,actual_k,actual_bf\n"
ROWS = (
    "2026-08-10,823018,690928,Hunter Dobbins,3.5,4.8,6,22\n"
    "2026-08-10,822780,543243,Sonny Gray,4.5,4.5,4,21\n"
    "2026-08-10,822780,592791,Jameson Taillon,4.5,4.3,3,20\n"
    # A different date must never leak into the lookup.
    "2026-08-09,111111,999999,Someone Else,5.5,5.1,9,25\n"
    # A row with no outcome yet must be skipped, not coerced to 0 --
    # a fabricated zero is worse than a blank.
    "2026-08-10,824999,700001,Ungraded Arm,4.5,4.4,,\n"
)


@pytest.fixture()
def model_log(tmp_path, monkeypatch):
    import tools.dashboard_data as dd

    path = tmp_path / "model_log.csv"
    path.write_text(HEADER + ROWS, encoding="utf-8")
    monkeypatch.setattr(dd, "MODEL_LOG_PATH", path)
    return dd


def test_model_log_supplies_actuals_for_the_requested_date(model_log):
    dd = model_log

    got = dd._actual_k_from_model_log(DATES)

    assert got == {(823018, 690928): 6, (822780, 543243): 4, (822780, 592791): 3}


def test_other_dates_never_leak(model_log):
    """Keyed by (game_pk, pitcher_id), so a date filter is the only guard."""
    dd = model_log

    assert (111111, 999999) not in dd._actual_k_from_model_log(DATES)
    assert dd._actual_k_from_model_log({"2026-08-09"}) == {(111111, 999999): 9}


def test_a_row_with_no_outcome_is_omitted_not_zeroed(model_log):
    """A blank actual_k means 'not known yet', which is not zero K."""
    dd = model_log

    assert (824999, 700001) not in dd._actual_k_from_model_log(DATES)


def test_lookup_falls_back_when_the_cache_is_empty(model_log, monkeypatch):
    """The worker's exact condition: cache has nothing for the date."""
    dd = model_log
    import data.backfill_statcast as bf
    import pandas as pd

    monkeypatch.setattr(bf, "load_cached", lambda lo, hi: pd.DataFrame())

    got = dd._actual_k_lookup(DATES)

    assert got[(823018, 690928)] == 6, "board stayed blank with the data on disk"
    assert len(got) == 3


def test_lookup_survives_a_cache_that_raises(model_log, monkeypatch):
    """A missing or corrupt cache tree must degrade, not blank the board."""
    dd = model_log
    import data.backfill_statcast as bf

    def boom(lo, hi):
        raise RuntimeError("no cache on this host")

    monkeypatch.setattr(bf, "load_cached", boom)

    assert dd._actual_k_lookup(DATES)[(822780, 543243)] == 4


def test_statcast_wins_where_it_answers(model_log, monkeypatch):
    """Precedence matters: Statcast is the graded truth.

    model_log is derived from it, so they agree in the normal case --
    but if they ever disagree the cache must win, exactly as it did
    before this fallback existed.
    """
    dd = model_log
    import data.backfill_statcast as bf
    import pandas as pd

    # One completed PA for Dobbins, a strikeout => cache says 1, log says 6.
    df = pd.DataFrame({
        "game_pk": [823018],
        "pitcher": [690928],
        "events": ["strikeout"],
    })
    monkeypatch.setattr(bf, "load_cached", lambda lo, hi: df)

    got = dd._actual_k_lookup(DATES)

    assert got[(823018, 690928)] == 1, "model_log overwrote the Statcast figure"
    # ...and the keys the cache did NOT have are still filled.
    assert got[(822780, 543243)] == 4
