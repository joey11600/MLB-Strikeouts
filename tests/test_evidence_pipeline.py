"""Regression tests for the evidence pipeline (AUDIT A-016, A-022).

Two slates' worth of observations were silently lost on consecutive
days. Both losses were invisible until the watchdog started failing CI,
and both came from the same shape of mistake: a job that reads a
third-party feed on a schedule the feed does not honour.

These lock in the three layers that now prevent it, because every one
of them is the kind of behaviour a future refactor removes without
noticing:

  1. SELF-HEAL  — any model_log run backfills every missing slate date,
                  so one good run recovers an arbitrary backlog.
  2. IDEMPOTENT — running it repeatedly never duplicates a row, which is
                  what makes running it twice a day free.
  3. ALARM      — the watchdog tolerates the publish lag in the morning
                  and fails in the afternoon. Getting this backwards
                  either cries wolf all morning or never fires at all.

Run:  python -m pytest tests/test_evidence_pipeline.py -q
"""
from __future__ import annotations

import csv
import importlib
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

REAL_SLATES = ROOT / "data" / "slates"
REAL_LOG = ROOT / "data" / "model_log.csv"

pytestmark = pytest.mark.skipif(
    not REAL_LOG.exists() or not REAL_SLATES.is_dir(),
    reason="needs a populated data/ directory",
)


def _isolated(tmp_path: Path, drop_date: str | None):
    """A throwaway DATA_STATE_DIR, optionally missing one date's rows."""
    shutil.copytree(REAL_SLATES, tmp_path / "slates")
    with open(REAL_LOG, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [r for r in reader if r["date"] != drop_date]
    with open(tmp_path / "model_log.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.environ["DATA_STATE_DIR"] = str(tmp_path)
    import tracker
    importlib.reload(tracker)
    return len(rows)


def _log_rows(tmp_path: Path) -> list[dict]:
    with open(tmp_path / "model_log.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_model_log_backfills_a_missing_date(tmp_path):
    """One good run must recover a slate an earlier run failed to log.

    This is the property that makes the 03:00 publish lag survivable: the
    night job can log nothing and a later job still fills it in.
    """
    before = _isolated(tmp_path, drop_date="2026-08-06")
    import tools.model_log as ml
    importlib.reload(ml)

    assert not any(r["date"] == "2026-08-06" for r in _log_rows(tmp_path))
    ml.log_dates()

    after = _log_rows(tmp_path)
    recovered = [r for r in after if r["date"] == "2026-08-06"]
    assert recovered, "a missing slate was not backfilled"
    assert len(after) > before


def test_model_log_is_idempotent(tmp_path):
    """Repeat runs must not duplicate rows — that is what makes running
    it on every task free."""
    _isolated(tmp_path, drop_date=None)
    import tools.model_log as ml
    importlib.reload(ml)

    ml.log_dates()
    first = _log_rows(tmp_path)
    ml.log_dates()
    second = _log_rows(tmp_path)

    assert len(first) == len(second)
    keys = {(r["date"], r["pitcher_id"]) for r in second}
    assert len(keys) == len(second), "duplicate (date, pitcher) rows"


@pytest.mark.parametrize(
    "hour,expected",
    # 12 is a WARN on purpose: the third logging attempt runs at 12:15,
    # so failing at 12:00 would fire fifteen minutes before the thing
    # that fixes it.
    [(3, "WARN"), (6, "WARN"), (11, "WARN"), (12, "WARN"),
     (13, "FAIL"), (15, "FAIL")],
)
def test_watchdog_tolerates_publish_lag_then_fails(tmp_path, hour, expected):
    """Statcast publishes mid-morning, so a gap before early afternoon is
    normal and a gap after 13:00 ET — past three logging attempts — is a
    real loss.

    Backwards in either direction is its own bug: fail-early paints every
    run red all morning until the alarm is ignored, and
    fail-never means the next lost slate goes unnoticed the way 8/5 did.
    """
    _isolated(tmp_path, drop_date="2026-08-06")
    import tools.watchdog as wd
    importlib.reload(wd)
    wd.MODEL_LOG = tmp_path / "model_log.csv"
    wd.SLATES = tmp_path / "slates"
    wd._today = lambda: date(2026, 8, 7)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 7, hour, 0, tzinfo=tz)

    with mock.patch.object(wd, "datetime", FrozenClock):
        report = wd.Report()
        wd.check_model_log_growing(report)

    assert report.rows[0]["status"] == expected


def test_model_log_never_drops_a_date_it_cannot_rederive(tmp_path, monkeypatch):
    """A date whose Statcast pitches are missing must keep its rows.

    The regression this pins (AUDIT A-030): log_dates() dropped every
    stored row whose date had a slate file, then regenerated only what
    Statcast could derive right now. Those are different sets. A date not
    yet in the cache regenerates ZERO rows, so the delete stood.

    Measured against the real log before the fix: one run on a machine
    whose cache stopped at 08-06 destroyed all 25 graded rows for 08-07 --
    real actual_k/actual_bf outcomes, unrecoverable. An incomplete cache
    is an ordinary transient state and this runs on every close task.
    """
    _isolated(tmp_path, drop_date=None)
    import tools.model_log as ml
    importlib.reload(ml)

    before = _log_rows(tmp_path)
    assert before, "fixture produced no rows"
    dates = sorted({r["date"] for r in before})
    starved = dates[-1]

    # Statcast can derive nothing for the newest date -- exactly what a
    # lagging or partially restored cache looks like.
    real_actuals = ml._actuals_for

    def _blind(target_dates):
        keep = {d for d in target_dates if d != starved}
        return real_actuals(keep) if keep else {}

    monkeypatch.setattr(ml, "_actuals_for", _blind)
    ml.log_dates()

    after = _log_rows(tmp_path)
    key = lambda r: (r["date"], r["game_pk"], r["pitcher_id"])  # noqa: E731
    lost = [r for r in before if key(r) not in {key(x) for x in after}]
    assert not lost, (
        f"{len(lost)} row(s) deleted for dates that could not be re-derived, "
        f"e.g. {lost[0]['date']} {lost[0]['pitcher_name']} "
        f"(actual_k={lost[0]['actual_k']})"
    )
    assert len(after) >= len(before)
