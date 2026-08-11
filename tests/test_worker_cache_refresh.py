"""Dispatching work must not outsource this container's inputs (A-037).

`_log_evidence()` calls `refresh_cache()` first, six times a day, and
A-022's fix depends on that: model_log READS the Statcast cache, it does
not fetch, so retrying the read against a stale cache just re-reads the
same empty file.

But `_log_evidence` runs inside the TASK. Once a GitHub token existed,
`dispatch_github()` began succeeding every time, the task ran on CI, and
this container stopped executing that path at all — leaving its cache
with one refresh per BOOT, at whatever arbitrary moment a deploy landed.

Measured: booted 2026-08-10 18:41 ET (mid-games) and 2026-08-11 07:58 ET
(before Savant published the previous day). Nothing between. On
2026-08-11 the worker rendered 2026-08-10 at 1/18 actual K totals while
CI rendered 18/18 from the same commit (A-036).

Third bug of this shape — a mechanism that worked while the FALLBACK was
the normal path and stopped the day the primary path started succeeding
(A-025 publishing, A-036 rendering, A-037 the cache).

Run:  python -m pytest tests/test_worker_cache_refresh.py -q
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


@pytest.fixture()
def worker(monkeypatch):
    import tools.railway_worker as w

    calls: dict[str, list] = {"refresh": [], "task": [], "dispatch": []}
    monkeypatch.setattr(w, "log", lambda m: None)
    monkeypatch.setattr(w, "refresh_cache", lambda: calls["refresh"].append(1))
    monkeypatch.setattr(w, "TASKS", {"morning": lambda: calls["task"].append("morning")})
    return w, calls


def test_dispatched_task_still_refreshes_this_containers_cache(worker, monkeypatch):
    """The operative fix.

    Without it the container's only cache refresh is at boot, and boots
    happen whenever a deploy happens — which is not a schedule.
    """
    w, calls = worker
    monkeypatch.setattr(w, "dispatch_github",
                        lambda n: calls["dispatch"].append(n) or True)

    outcome = w._run_or_dispatch("morning")

    assert outcome == "dispatched"
    assert calls["dispatch"] == ["morning"]
    assert calls["refresh"] == [1], (
        "dispatched the task and left this container's Statcast cache stale"
    )


def test_dispatched_task_does_not_also_run_locally(worker, monkeypatch):
    """It ran on CI. Running it here too would double every side effect.

    This also covers the RUN_TASK_ON_BOOT path, which used to blank the
    task name after a successful dispatch and then call TASKS[""],
    raising KeyError into a handler that logged `BOOT TASK ERROR : ''`
    on the one path that had actually worked.
    """
    w, calls = worker
    monkeypatch.setattr(w, "dispatch_github", lambda n: True)

    w._run_or_dispatch("morning")

    assert calls["task"] == [], "task ran locally as well as on CI"


def test_local_fallback_does_not_double_refresh(worker, monkeypatch):
    """When we run it ourselves, _log_evidence() already refreshes.

    Refreshing here too would fetch five days of Statcast twice per
    window for no gain.
    """
    w, calls = worker
    monkeypatch.setattr(w, "dispatch_github", lambda n: False)

    outcome = w._run_or_dispatch("morning")

    assert outcome == "local"
    assert calls["task"] == ["morning"]
    assert calls["refresh"] == [], "refreshed twice on the local path"


def test_a_failing_task_is_contained(worker, monkeypatch):
    """A task that raises must not take the scheduler loop down."""
    w, calls = worker

    def boom():
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(w, "dispatch_github", lambda n: False)
    monkeypatch.setattr(w, "TASKS", {"morning": boom})

    assert w._run_or_dispatch("morning") == "error"


def test_unknown_task_is_contained(worker, monkeypatch):
    """An unknown name must be an error, not an unhandled KeyError."""
    w, _ = worker
    monkeypatch.setattr(w, "dispatch_github", lambda n: False)

    assert w._run_or_dispatch("no-such-task") == "error"


# --------------------------------------------------------------------
# /health must answer "is the cache current?" without deploy-log access.
# --------------------------------------------------------------------

def test_cache_status_distinguishes_absent_empty_and_real_days(tmp_path, monkeypatch):
    """`cache_months` said "2026-08 is present" all through A-036/A-037.

    True, and useless: the question is whether a specific DAY is in
    there, and whether it holds pitches or only a schema. A schema-only
    parquet is 636 bytes; a light slate is ~450 KB.
    """
    import datetime as _dt

    import tools.railway_worker as w

    today = _dt.datetime.now(w.ET).date()
    yesterday = today - _dt.timedelta(days=1)
    two_back = today - _dt.timedelta(days=2)

    (tmp_path / f"{yesterday:%Y-%m}").mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{yesterday:%Y-%m}" / f"{yesterday.isoformat()}.parquet"
     ).write_bytes(b"x" * 450_000)
    (tmp_path / f"{two_back:%Y-%m}").mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{two_back:%Y-%m}" / f"{two_back.isoformat()}.parquet"
     ).write_bytes(b"x" * 636)
    monkeypatch.setattr(w, "CACHE_DIR", tmp_path)

    s = w._cache_status()

    assert s["recent_bytes"][today.isoformat()] is None, "absent day must be null"
    assert s["recent_bytes"][yesterday.isoformat()] == 450_000
    assert s["recent_bytes"][two_back.isoformat()] == 636, (
        "an empty day must be visibly different from a real one"
    )
    assert s["latest_date"] == yesterday.isoformat()
    assert s["n_days"] == 2


def test_cache_status_survives_a_missing_cache_dir(tmp_path, monkeypatch):
    import tools.railway_worker as w

    monkeypatch.setattr(w, "CACHE_DIR", tmp_path / "not-created")

    s = w._cache_status()

    assert s["latest_date"] is None and s["n_days"] == 0


def test_refresh_records_when_it_last_ran(tmp_path, monkeypatch):
    """"When did this container last top up its own cache?" was
    unanswerable without deploy-log access — which is exactly how A-037
    hid for as long as it did."""
    import tools.railway_worker as w

    monkeypatch.setattr(w, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(w, "log", lambda m: None)
    monkeypatch.setattr(w, "_run", lambda label, cmd, timeout: True)
    w.LAST_CACHE_REFRESH.update(at=None, ok=None, window=None)

    w.refresh_cache()

    assert w.LAST_CACHE_REFRESH["ok"] is True
    assert w.LAST_CACHE_REFRESH["at"], "no timestamp recorded"
    assert ".." in w.LAST_CACHE_REFRESH["window"]
    assert w._cache_status()["last_refresh"]["ok"] is True


def test_a_failed_refresh_is_recorded_as_failed(tmp_path, monkeypatch):
    """Reporting ok=True on a failed fetch is the A-029 mistake."""
    import tools.railway_worker as w

    monkeypatch.setattr(w, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(w, "log", lambda m: None)
    monkeypatch.setattr(w, "_run", lambda label, cmd, timeout: False)
    w.LAST_CACHE_REFRESH.update(at=None, ok=None, window=None)

    w.refresh_cache()

    assert w.LAST_CACHE_REFRESH["ok"] is False
