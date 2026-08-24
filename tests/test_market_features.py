"""A-049 H1/H2: intraday odds archive + line-movement features."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import features.market as M


def _norm(s):
    return s.strip().lower()


@pytest.fixture
def odds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "INTRADAY_DIR", tmp_path)
    return tmp_path


def _prop(name, line, over, under, src="live"):
    return {"pitcher_name": name, "line": line, "over_odds": over,
            "under_odds": under, "odds_source": src}


def test_record_load_movement_roundtrip(odds_dir):
    M.record_intraday_snapshot("2026-08-24", [_prop("Zack Wheeler", "7.5", "-115", "-105")])
    M.record_intraday_snapshot("2026-08-24", [_prop("Zack Wheeler", "6.5", "-125", "+100")])

    day = M.load_intraday("2026-08-24", _norm)
    caps = day["zack wheeler"]
    assert len(caps) == 2

    mv = M.movement_features(caps)
    assert mv["h1_open_line"] == 7.5
    assert mv["h2_line_move"] == pytest.approx(-1.0)
    assert mv["h2_n_captures"] == 2
    # the line DROPPED a full strikeout, so the fair prob of clearing
    # the new lower line is mechanically HIGHER — the documented
    # confound: fair_move is only standalone-interpretable at a fixed
    # line, and here the line move is the signal.
    assert mv["h2_fair_move"] > 0


def test_fair_move_at_fixed_line(odds_dir):
    """With the line unchanged, fair_move isolates pure price drift —
    here the over price worsens (-105 -> -125), so fair_over rises."""
    M.record_intraday_snapshot("2026-08-24", [_prop("A B", "5.5", "-105", "-115")])
    M.record_intraday_snapshot("2026-08-24", [_prop("A B", "5.5", "-125", "+100")])
    mv = M.movement_features(M.load_intraday("2026-08-24", _norm)["a b"])
    assert mv["h2_line_move"] == 0.0
    assert mv["h2_fair_move"] > 0


def test_single_capture_is_zero_move(odds_dir):
    M.record_intraday_snapshot("2026-08-24", [_prop("A B", "5.5", "-110", "-110")])
    mv = M.movement_features(M.load_intraday("2026-08-24", _norm)["a b"])
    assert mv["h1_open_line"] == 5.5
    assert mv["h2_line_move"] == 0.0
    assert mv["h2_n_captures"] == 1


def test_unparseable_prices_yield_none_not_fabrication(odds_dir):
    M.record_intraday_snapshot("2026-08-24", [_prop("A B", "", "", "")])
    mv = M.movement_features(M.load_intraday("2026-08-24", _norm)["a b"])
    assert mv["h1_open_line"] is None
    assert mv["h2_line_move"] is None


def test_empty_series():
    mv = M.movement_features(None)
    assert mv["h2_n_captures"] == 0
    assert mv["h1_open_line"] is None


def test_appends_preserve_earlier_captures(odds_dir):
    M.record_intraday_snapshot("2026-08-24", [_prop("A B", "5.5", "-110", "-110")])
    M.record_intraday_snapshot("2026-08-24", [_prop("C D", "6.5", "-120", "+100")])
    day = M.load_intraday("2026-08-24", _norm)
    assert set(day) == {"a b", "c d"}
