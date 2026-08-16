"""A wedged git checkout must not be terminal for the container (A-040).

`sync_repo()` fetched, and if the fetch failed it recorded the failure
and moved on. Nothing retried and nothing cleared the wedge, so every
later pass failed identically: the worker kept serving the board it
already had and only a human redeploy recovered it.

Measured 2026-08-15/16: the Railway worker stopped pulling at 12:32 ET
and served a payload generated 2026-08-15 09:33 ET for the next 27
hours. CI was healthy throughout and had already published the correct
board for both days -- the worker simply could not receive it. /health
reported `can_push_to_git: true` the whole time, because that field is
computed once at boot and never revisited.

The lock file is the specific wedge this targets: a git killed
mid-operation (timeout, OOM, container signal) leaves `.git/index.lock`
or a sibling behind, and every subsequent git command in that checkout
fails on it forever.

Run:  python -m pytest tests/test_git_lock_recovery.py -q
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")


def _mk(git_dir: Path, name: str, age_s: float) -> Path:
    """A lock file whose mtime is `age_s` seconds in the past."""
    p = git_dir / name
    p.write_text("", encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(p, (stamp, stamp))
    return p


def test_abandoned_lock_is_cleared(tmp_path, monkeypatch):
    """The defect, at the seam that had it."""
    import tools.railway_worker as rw

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = _mk(git_dir, "index.lock", rw.STALE_LOCK_S + 60)
    monkeypatch.setattr(rw, "REPO", tmp_path)

    cleared = rw._clear_stale_git_locks()

    assert cleared == ["index.lock"], f"lock not cleared: {cleared}"
    assert not lock.exists()


def test_a_fresh_lock_is_left_alone(tmp_path, monkeypatch):
    """Age gate. A young lock may belong to a RUNNING git.

    Deleting one out from under a live command turns a wedged checkout
    into a corrupted one, which is strictly worse than the stall this
    recovers from.
    """
    import tools.railway_worker as rw

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = _mk(git_dir, "index.lock", 5)
    monkeypatch.setattr(rw, "REPO", tmp_path)

    assert rw._clear_stale_git_locks() == []
    assert lock.exists(), "a lock a live git may hold was deleted"


def test_every_known_lock_name_is_covered(tmp_path, monkeypatch):
    """index.lock is the common one, not the only one.

    A fetch killed partway leaves shallow.lock or FETCH_HEAD.lock, and
    those wedge the same commands just as permanently.
    """
    import tools.railway_worker as rw

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    for name in rw.GIT_LOCKS:
        _mk(git_dir, name, rw.STALE_LOCK_S + 60)
    monkeypatch.setattr(rw, "REPO", tmp_path)

    assert sorted(rw._clear_stale_git_locks()) == sorted(rw.GIT_LOCKS)
    assert not any((git_dir / n).exists() for n in rw.GIT_LOCKS)


def test_no_locks_and_no_git_dir_are_both_quiet(tmp_path, monkeypatch):
    """Runs on EVERY failed pull, so the common path must be silent.

    A missing .git is the boot-time bootstrap's problem, not this
    function's -- it must not raise on the way past.
    """
    import tools.railway_worker as rw

    monkeypatch.setattr(rw, "REPO", tmp_path)
    assert rw._clear_stale_git_locks() == []

    (tmp_path / ".git").mkdir()
    assert rw._clear_stale_git_locks() == []


def test_last_pull_carries_the_recovered_field(tmp_path, monkeypatch):
    """/health has to distinguish healthy from limping.

    A container that fails and self-heals on every pass is not the same
    as one that never failed, and the difference must be visible or the
    next silent stall looks exactly like the last one.
    """
    import tools.railway_worker as rw

    assert "recovered" in rw.LAST_PULL, (
        "last_pull has no `recovered` key — a self-healing worker would "
        "be indistinguishable from a healthy one on /health"
    )
