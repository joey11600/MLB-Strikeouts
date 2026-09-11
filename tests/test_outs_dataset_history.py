"""The outs label table is MERGED, never replaced (A-055).

The worker and CI carry the current season's Statcast cache only, so
`build()` there returns 2026 alone — correct for what it can see. Writing
that straight over the cached table deleted 2024+2025 on 2026-08-25, and
nothing failed: the board still rendered and the model still priced, so
the only symptom was that every career-depth feature quietly got shorter
(`career_left_censored` 0.000 on every row) and the market scorer's
rebuild drifted 0.18 away from the board that was actually served.

These tests pin the merge, the collision rule, and the refusal.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import build_outs_dataset as bod

COLS = ["game_pk", "game_date", "game_year", "pitcher", "is_home", "outs"]


def _row(game_pk, pitcher, date, outs=15):
    ts = pd.Timestamp(date)
    return [game_pk, ts, ts.year, pitcher, True, outs]


def _starts(rows):
    return pd.DataFrame(rows, columns=COLS)


HISTORY = _starts([
    _row(1, 100, "2024-05-01", 18),
    _row(2, 101, "2025-05-01", 12),
    _row(3, 102, "2026-04-01", 15),
])
#: what a current-season-only cache rebuilds: 2026 alone, one new start
CURRENT_SEASON_ONLY = _starts([
    _row(3, 102, "2026-04-01", 15),
    _row(4, 103, "2026-09-10", 21),
])


def test_merge_keeps_seasons_the_fresh_build_cannot_see():
    merged = bod.merge_starts(CURRENT_SEASON_ONLY, HISTORY)
    assert bod._seasons(merged) == [2024, 2025, 2026]
    assert len(merged) == 4
    assert set(merged["game_pk"]) == {1, 2, 3, 4}


def test_fresh_row_wins_on_collision():
    """A start cached mid-game is corrected by a later rebuild."""
    partial = _starts([_row(3, 102, "2026-04-01", 6)])     # pulled early?
    merged = bod.merge_starts(partial, HISTORY)
    assert len(merged) == 3
    got = merged.loc[merged["game_pk"] == 3, "outs"].iloc[0]
    assert int(got) == 6


def test_empty_sides_are_identities():
    assert len(bod.merge_starts(CURRENT_SEASON_ONLY, None)) == 2
    assert len(bod.merge_starts(_starts([]), HISTORY)) == 3


def test_save_preserves_history_on_a_current_season_rebuild(tmp_path):
    path = tmp_path / "outs_starts.parquet"
    bod.atomic_write_parquet(HISTORY, path)
    out = bod.save_outs_starts(CURRENT_SEASON_ONLY, path=path)
    assert bod._seasons(out) == [2024, 2025, 2026]
    assert bod._seasons(pd.read_parquet(path)) == [2024, 2025, 2026]
    assert len(out) == 4


def test_save_refuses_a_write_that_would_drop_a_season(tmp_path, monkeypatch):
    """The guard is what catches a future merge bug, so prove it fires.

    Mutation check: this is the exact write the pre-A-055 code performed.
    """
    path = tmp_path / "outs_starts.parquet"
    bod.atomic_write_parquet(HISTORY, path)
    monkeypatch.setattr(bod, "merge_starts", lambda fresh, existing: fresh)

    with pytest.raises(RuntimeError, match="would be lost"):
        bod.save_outs_starts(CURRENT_SEASON_ONLY, path=path)
    # and the table on disk is untouched by the refusal
    assert bod._seasons(pd.read_parquet(path)) == [2024, 2025, 2026]


def test_save_refuses_a_write_that_would_shrink_the_table(tmp_path, monkeypatch):
    """Every season still present, rows gone anyway — the second guard alone.

    The season check fires first when a whole year disappears, so this
    case has to lose rows WITHOUT losing a season or it would prove the
    other guard twice.
    """
    path = tmp_path / "outs_starts.parquet"
    existing = _starts([
        _row(1, 100, "2024-05-01", 18),
        _row(2, 101, "2025-05-01", 12),
        _row(3, 102, "2026-04-01", 15),
        _row(4, 103, "2026-09-10", 21),
    ])
    bod.atomic_write_parquet(existing, path)
    lossy = existing.iloc[:3]                 # 2024, 2025, 2026 all survive
    assert bod._seasons(lossy) == [2024, 2025, 2026]
    monkeypatch.setattr(bod, "merge_starts", lambda fresh, existing: lossy)

    with pytest.raises(RuntimeError, match="rows on disk"):
        bod.save_outs_starts(CURRENT_SEASON_ONLY, path=path)
    assert len(pd.read_parquet(path)) == 4


def test_first_write_needs_no_existing_table(tmp_path):
    path = tmp_path / "outs_starts.parquet"
    out = bod.save_outs_starts(HISTORY, path=path)
    assert len(out) == 3
    assert path.exists()
