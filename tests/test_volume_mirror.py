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


# --- the outs market's half of the same loop (2026-08-27) -------------
#
# outs_paper_tracks.csv was in PERSISTED (so it lived on the volume) but
# in neither _MERGE_KEYS nor the mirror. The outs jobs are DISPATCHED to
# GitHub Actions, so every row log_paper_tracks() ever wrote landed in
# the repo, while the container served the volume copy it was seeded
# with on 2026-08-25 and never refreshed -- seed_volume_state() only
# fills gaps. For three days the /outs board showed 08-25 and 08-26
# graded while the paper P&L beside it read "5 bets, 1 date": 15 gold
# plays missing, including 08-26's losing 3-4, so the published total
# was biased toward the one winning day it did count.

OUTS_PAPER_FIELDS = ["date", "policy", "game_pk", "pitcher_id",
                     "pitcher_name", "side", "line", "odds",
                     "stake_units", "result", "pl_units", "logged_at"]


def _paper(date: str, policy: str, pid: str, result: str, pl: str) -> dict:
    return {"date": date, "policy": policy, "game_pk": "1",
            "pitcher_id": pid, "pitcher_name": "P", "side": "UNDER",
            "line": "17.5", "odds": "-110", "stake_units": "2.0",
            "result": result, "pl_units": pl, "logged_at": "2026-08-27T00:00:00+00:00"}


def test_reconcile_carries_ci_paper_tracks_into_the_volume(worker):
    """CI appends the graded slate; the container must serve it."""
    repo_paper = worker.REPO / "data" / "outs_paper_tracks.csv"
    vol_paper = worker.VOLUME_STATE / "outs_paper_tracks.csv"

    _write_csv(vol_paper, [_paper("2026-08-24", "gold_capped", "1", "WIN", "1.85")],
               OUTS_PAPER_FIELDS)
    _write_csv(repo_paper, [
        _paper("2026-08-24", "gold_capped", "1", "WIN", "1.85"),
        _paper("2026-08-25", "gold_capped", "2", "WIN", "1.09"),
        _paper("2026-08-26", "gold_capped", "3", "LOSS", "-2.0"),
    ], OUTS_PAPER_FIELDS)

    worker.reconcile_ledger()

    dates = {r["date"] for r in _read(vol_paper)}
    assert dates == {"2026-08-24", "2026-08-25", "2026-08-26"}, (
        "the served paper P&L froze at its seed date again")


def test_reconcile_never_drops_a_frozen_paper_row(worker):
    """A (date, policy) pair is written once and FROZEN. The union may
    add to the volume; it must never remove what the volume already
    settled, or a published P&L would move after the fact."""
    repo_paper = worker.REPO / "data" / "outs_paper_tracks.csv"
    vol_paper = worker.VOLUME_STATE / "outs_paper_tracks.csv"

    _write_csv(vol_paper, [_paper("2026-08-26", "gold_capped", "9", "LOSS", "-2.0")],
               OUTS_PAPER_FIELDS)
    _write_csv(repo_paper, [_paper("2026-08-24", "gold_capped", "1", "WIN", "1.85")],
               OUTS_PAPER_FIELDS)

    worker.reconcile_ledger()

    rows = _read(vol_paper)
    assert {(r["date"], r["pitcher_id"]) for r in rows} == {
        ("2026-08-26", "9"), ("2026-08-24", "1")}


def test_reconcile_carries_outs_evidence_and_boards(worker):
    outs_log = worker.VOLUME_STATE / "outs_model_log.csv"
    _write_csv(worker.REPO / "data" / "outs_model_log.csv", [
        {"date": "2026-08-26", "pitcher_id": "7", "actual_outs": "18"},
    ], ["date", "pitcher_id", "actual_outs"])
    (worker.REPO / "data" / "outs_slates").mkdir()
    (worker.REPO / "data" / "outs_slates" / "2026-08-26.json").write_text(
        '{"generated_at": "2026-08-26T20:00:00+00:00"}', encoding="utf-8")

    worker.reconcile_ledger()

    assert _read(outs_log)[0]["actual_outs"] == "18"
    assert (worker.VOLUME_STATE / "outs_slates" / "2026-08-26.json").exists()


def test_mirror_carries_outs_grades_back_to_the_checkout(worker):
    """outs-live grades the VOLUME every five minutes. Without this
    direction those grades reach git only when CI re-derives them from
    Savant the next morning -- 2026-08-25's needed a hand-run sweep."""
    _write_csv(worker.VOLUME_STATE / "outs_model_log.csv", [
        {"date": "2026-08-26", "pitcher_id": "7", "actual_outs": "18"},
    ], ["date", "pitcher_id", "actual_outs"])
    (worker.VOLUME_STATE / "outs_slates").mkdir()
    (worker.VOLUME_STATE / "outs_slates" / "2026-08-26.json").write_text(
        "{}", encoding="utf-8")

    worker.mirror_volume_to_repo()

    assert (worker.REPO / "data" / "outs_model_log.csv").exists()
    assert (worker.REPO / "data" / "outs_slates" / "2026-08-26.json").exists()


def test_mirror_refuses_to_shrink_a_ledger(worker):
    """reconcile_ledger() catches its own exceptions and returns, so the
    volume can legitimately be BEHIND the checkout. The mirror copy is
    blind, so without a guard one bad pass overwrites a complete ledger
    with a stale one -- "never delete rows" broken by a backup path."""
    repo_paper = worker.REPO / "data" / "outs_paper_tracks.csv"
    vol_paper = worker.VOLUME_STATE / "outs_paper_tracks.csv"

    _write_csv(repo_paper, [
        _paper("2026-08-24", "gold_capped", "1", "WIN", "1.85"),
        _paper("2026-08-25", "gold_capped", "2", "WIN", "1.09"),
        _paper("2026-08-26", "gold_capped", "3", "LOSS", "-2.0"),
    ], OUTS_PAPER_FIELDS)
    # the frozen volume copy, as it stood on 2026-08-27
    _write_csv(vol_paper, [_paper("2026-08-24", "gold_capped", "1", "WIN", "1.85")],
               OUTS_PAPER_FIELDS)

    worker.mirror_volume_to_repo()

    assert len(_read(repo_paper)) == 3, "the mirror ate the checkout's ledger"


def test_mirror_still_carries_an_equal_or_longer_ledger(worker):
    """The guard must not block the case it exists to protect."""
    repo_log = worker.REPO / "data" / "outs_model_log.csv"
    fields = ["date", "pitcher_id", "actual_outs"]
    _write_csv(repo_log, [{"date": "2026-08-26", "pitcher_id": "7", "actual_outs": ""}],
               fields)
    _write_csv(worker.VOLUME_STATE / "outs_model_log.csv", [
        {"date": "2026-08-26", "pitcher_id": "7", "actual_outs": "18"},
        {"date": "2026-08-26", "pitcher_id": "8", "actual_outs": "15"},
    ], fields)

    worker.mirror_volume_to_repo()

    rows = _read(repo_log)
    assert len(rows) == 2 and rows[0]["actual_outs"] == "18"
