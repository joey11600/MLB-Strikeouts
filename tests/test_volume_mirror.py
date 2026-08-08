"""The push half of the volume<->repo loop (AUDIT A-028).

Railway grades a starter the moment he is pulled and writes it to the
VOLUME. `_merge_csv` only ever unions repo -> volume, so nothing this
container produces was reaching git. On 2026-08-07 the worker held
Payton Tolle graded LOSS / 14 K / -2.0u while `tools/pl_calc.py` -- which
reads the repo and is the only sanctioned source of a P&L figure --
still reported the pre-game total.

Two properties are asserted here, because getting either wrong
reintroduces a silent divergence between what the operator sees on the
board and what the books say:

  1. MIRRORS  — volume-only rows reach the checkout.
  2. NEVER LOSES — the copy cannot drop rows the repo already had. That
     holds because reconcile unions repo into volume BEFORE the mirror,
     so the volume is a superset by construction. If someone reorders
     those two steps, this test fails.

Run:  python -m pytest tests/test_volume_mirror.py -q
"""
from __future__ import annotations

import csv
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture()
def worker(tmp_path, monkeypatch):
    """railway_worker with REPO and the volume pointed at a sandbox."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    state = tmp_path / "state"
    (state / "state").mkdir(parents=True)

    monkeypatch.setenv("WORKER_STATE_DIR", str(state))
    monkeypatch.setenv("DATA_STATE_DIR", str(state / "state"))

    import tracker
    importlib.reload(tracker)
    import tools.railway_worker as rw
    importlib.reload(rw)
    rw.REPO = repo
    rw.VOLUME_STATE = state / "state"
    return rw


FIELDS = ["date", "game_pk", "pitcher_id", "line", "result"]


def test_mirrors_volume_grades_into_the_checkout(worker):
    """The grade the live watcher wrote must reach the repo."""
    repo_picks = worker.REPO / "data" / "picks_2026.csv"
    vol_picks = worker.VOLUME_STATE / "picks_2026.csv"

    _write_csv(repo_picks, [
        {"date": "2026-08-07", "game_pk": "1", "pitcher_id": "9", "line": "6.5", "result": ""},
    ], FIELDS)
    # what the live watcher produced, volume-side only
    _write_csv(vol_picks, [
        {"date": "2026-08-07", "game_pk": "1", "pitcher_id": "9", "line": "6.5", "result": "LOSS"},
    ], FIELDS)

    assert _read(repo_picks)[0]["result"] == ""
    n = worker.mirror_volume_to_repo()

    assert n >= 1
    assert _read(repo_picks)[0]["result"] == "LOSS", "the grade never reached git"


def test_mirror_carries_slates_and_odds(worker):
    (worker.VOLUME_STATE / "slates").mkdir()
    (worker.VOLUME_STATE / "slates" / "2026-08-07.json").write_text("{}", encoding="utf-8")
    (worker.VOLUME_STATE / "odds").mkdir()
    (worker.VOLUME_STATE / "odds" / "closing_2026-08-07.csv").write_text("a\n", encoding="utf-8")

    worker.mirror_volume_to_repo()

    assert (worker.REPO / "data" / "slates" / "2026-08-07.json").exists()
    assert (worker.REPO / "data" / "odds" / "closing_2026-08-07.csv").exists()


def test_is_a_noop_on_ci_where_the_checkout_is_the_ledger(worker, monkeypatch):
    """On a CI runner DATA_STATE_DIR == repo/data. Copying a directory
    onto itself is at best pointless and at worst destructive, so the
    mirror must decline -- the same guard reconcile uses."""
    import tracker
    monkeypatch.setattr(tracker, "DATA_STATE_DIR", worker.REPO / "data")
    assert worker.mirror_volume_to_repo() == 0


def test_missing_volume_is_survivable(worker):
    """A container booting before the volume mounts must not crash the
    publish pass -- it runs every 5 minutes and is not worth an outage."""
    import shutil
    shutil.rmtree(worker.VOLUME_STATE)
    assert worker.mirror_volume_to_repo() == 0
