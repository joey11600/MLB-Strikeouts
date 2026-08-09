"""The served-board check: the alarm that must never go quiet (A-029).

`check_served_board_is_current` compares the board the Railway worker
SERVES against the one the repo published. It matters more than the other
checks because dashboard/lib/data-context.tsx prefers the worker's
/data.json whenever it answers -- the worker's copy IS the site, so every
other check can pass green while the operator stares at a stale board.

On 2026-08-08 it grew from two arms to six in order to stop a false
positive, and an adversarial review found that four of the new arms let a
genuinely broken worker report ok. This file pins the behaviour that
review established. Each test names the concrete state it encodes.

Two properties, in tension, and both have already been violated once:

  1. NEVER QUIET on a broken worker. A stale board is not a cosmetic
     problem -- A-025 hid a LEAN with two hours to first pitch.
  2. NEVER NOISY on a healthy one. `lag` is quantised to the gap between
     priced boards, so a worker correctly one version behind reads
     hundreds of minutes late. An alarm that fires on that is one the
     operator learns to ignore, and this is not an alarm to ignore.

Run:  python -m pytest tests/test_watchdog_served_board.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tools.watchdog as W  # noqa: E402

TODAY = "2026-08-08"
# The real stamps from the 2026-08-08 false positive.
REPO_BOARD = "2026-08-08T20:46:20.307515+00:00"
PREV_BOARD = "2026-08-08T13:49:41.061568+00:00"
NOW = datetime.fromisoformat("2026-08-08T20:46:29.380000+00:00")


def _payload(stamp, n=28, b=0):
    slate = {} if stamp is None else {
        TODAY: {"generated_at": stamp, "pitcher_count": n, "bet_count": b}}
    return {"slates": slate}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Drive the check against synthetic worker/repo state."""
    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW

    (tmp_path / "dashboard" / "public").mkdir(parents=True)
    monkeypatch.setattr(W, "ROOT", tmp_path)
    monkeypatch.setattr(W, "datetime", _FrozenDT)
    monkeypatch.setattr(W, "_today", lambda: datetime.fromisoformat(TODAY).date())

    def run(*, repo, worker, health, prev=PREV_BOARD):
        (tmp_path / "dashboard" / "public" / "data.json").write_text(
            json.dumps(repo), encoding="utf-8")
        monkeypatch.setattr(W, "_previous_published_board", lambda _t: prev)

        class _Resp:
            def __init__(self, body): self._b = json.dumps(body).encode()
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _open(url, timeout=None):
            if url.endswith("/health"):
                if isinstance(health, Exception):
                    raise health
                return _Resp(health)
            return _Resp(worker)

        # watchdog does `import urllib.request` INSIDE the function, so
        # there is no module attribute to patch -- patch the real module.
        import urllib.request as _u
        monkeypatch.setattr(_u, "urlopen", _open)

        rep = W.Report()
        W.check_served_board_is_current(rep)
        return rep.rows[0]["status"], rep.rows[0]["detail"]

    return run


PULLING = {"last_pull": {"at": "2026-08-08T16:44:00-04:00", "ok": True}}


# --- property 2: must not be noisy -------------------------------------

def test_the_real_false_positive_is_ok(harness):
    """The state that motivated the change, with its real timestamps.

    Board regenerated 9 seconds before the check; worker is one version
    behind and pulling. lag is 417 min -- ten times the old threshold --
    and the worker had it on the next pass.
    """
    status, detail = harness(
        repo=_payload(REPO_BOARD), worker=_payload(PREV_BOARD), health=PULLING)
    assert status == W.OK, detail


# --- property 1: must not go quiet -------------------------------------

def test_no_slate_at_all_fails_once_the_board_has_been_available(harness):
    """Ten of the eleven A-029 failures came from THIS branch.

    The outage condition is a board that has been sitting available while
    the worker serves nothing — not a board published seconds ago.
    """
    old = "2026-08-08T18:00:00+00:00"          # ~166 min before NOW
    status, detail = harness(
        repo=_payload(old), worker=_payload(None), health=PULLING)
    assert status == W.FAIL, detail
    assert "no slate at all" in detail


def test_first_board_of_the_day_is_not_an_outage(harness):
    """The daily false positive this branch used to produce.

    Measured 2026-08-09: data/slates/2026-08-09.json was first committed
    at 13:05:40Z and the check ran at 13:05:52Z — twelve seconds later,
    against a 300s publish pass. Two runs failed; the next two passed
    untouched once the worker pulled. Before today's board exists the
    check warns (no local slate), so this fires exactly once a morning,
    on the one branch that has to stay trustworthy.
    """
    status, detail = harness(
        repo=_payload(REPO_BOARD), worker=_payload(None), health=PULLING)
    assert status == W.OK, detail


def test_no_slate_and_not_pulling_fails_even_on_a_fresh_board(harness):
    """A fresh board must not excuse a worker that cannot be shown to be
    pulling — that combination IS the A-029 shape, and during that outage
    /health carried no last_pull at all."""
    status, detail = harness(
        repo=_payload(REPO_BOARD), worker=_payload(None),
        health={"last_publish": {"at": "2026-08-08T16:44:00-04:00", "ok": True}})
    assert status == W.FAIL, detail
    assert "no slate at all" in detail


def test_arbitrarily_stale_board_is_not_excused_by_a_fresh_repo_board(harness):
    """The hole the grace opened: bounded in minutes, unbounded in lag.

    A 3.5-day-old board, worker pulling fine, repo board seconds old.
    `available` is tiny so a minutes-only grace says ok.

    Pitcher/bet counts are kept IDENTICAL to the repo's on purpose, so the
    shape guard cannot be what rejects this. Version identity has to be
    doing the work, or the test passes for the wrong reason.
    """
    status, detail = harness(
        repo=_payload(REPO_BOARD, n=28, b=0),
        worker=_payload("2026-08-05T09:00:00+00:00", n=28, b=0),
        health=PULLING)
    assert status == W.FAIL, detail


def test_shape_mismatch_inside_the_grace_window_fails(harness):
    """Same generated_at lineage but a different board: two boards are in
    circulation and one of them is wrong. Never excused by grace."""
    status, detail = harness(
        repo=_payload(REPO_BOARD, n=28, b=3),
        worker=_payload(PREV_BOARD, n=24, b=0),
        health=PULLING)
    assert status == W.FAIL, detail


def test_health_unreachable_does_not_downgrade_a_stale_board(harness):
    """/health does strictly more work than /data.json, so it fails first
    on a wedged container. Sharing a try block turned a live outage into
    'worker unreachable — site is on the bundled fallback', which was
    false in both clauses and exited 0."""
    status, detail = harness(
        repo=_payload(REPO_BOARD),
        worker=_payload("2026-08-08T10:46:20+00:00"),
        health=TimeoutError("timed out"))
    assert status == W.FAIL, detail
    assert "unreachable" not in detail


def test_worker_without_last_pull_gets_no_grace(harness):
    """A worker predating pull tracking cannot prove it is pulling.

    Fails closed on purpose: during A-029 /health reported
    last_publish ok=true for 16 hours while every git command failed.
    """
    status, detail = harness(
        repo=_payload(REPO_BOARD), worker=_payload(PREV_BOARD),
        health={"last_publish": {"at": "2026-08-08T16:44:00-04:00", "ok": True}})
    assert status == W.FAIL, detail


def test_failed_pull_gets_no_grace(harness):
    status, detail = harness(
        repo=_payload(REPO_BOARD), worker=_payload(PREV_BOARD),
        health={"last_pull": {"at": "2026-08-08T16:44:00-04:00", "ok": False}})
    assert status == W.FAIL, detail


def test_negative_clocks_do_not_open_the_window(harness):
    """A repo stamp in the future, or a worker clock running fast, is a
    broken clock -- not evidence of health."""
    future = "2026-08-08T22:16:20+00:00"          # 90 min ahead of NOW
    status, _ = harness(
        repo=_payload(future),
        worker=_payload("2026-08-08T04:46:20+00:00"),
        health=PULLING, prev="2026-08-08T04:46:20+00:00")
    assert status == W.FAIL

    status, _ = harness(                            # pull_age = -5 min
        repo=_payload(REPO_BOARD),
        worker=_payload("2026-08-08T10:46:20+00:00"),
        health={"last_pull": {"at": "2026-08-08T16:51:29-04:00", "ok": True}},
        prev="2026-08-08T10:46:20+00:00")
    assert status == W.FAIL
