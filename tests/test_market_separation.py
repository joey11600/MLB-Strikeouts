"""The outs market is a SEPARATE PRODUCT (operator directive
2026-08-24): its rows must never blend into a strikeouts aggregate,
page, or published number, and a pick's identity includes its market.

These tests simulate the first outs row landing in the ledger and
assert every strikeouts-facing surface ignores it."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracker import FIELDS, market_of


def _row(**kw):
    base = {f: "" for f in FIELDS}
    base.update({"date": "2026-08-24", "game_pk": "1", "pitcher_id": "7",
                 "pitcher_name": "Arm", "line": "5.5", "pick_side": "OVER",
                 "units_risked": "1.0", "bet_placed": "Y",
                 "graded_result": "WIN", "market_over_odds": "-110"})
    base.update(kw)
    # keep the stored P&L consistent with the row so the drift guard
    # (which is not under test here) stays quiet
    if base["graded_result"] in ("WIN", "LOSS") and not base["profit_loss_units"]:
        from tracker import _calc_pnl
        base["profit_loss_units"] = f"{_calc_pnl(base):.2f}"
    return base


def test_market_of_defaults_blank_to_strikeouts():
    assert market_of({"market": ""}) == "K"
    assert market_of({}) == "K"
    assert market_of({"market": "outs"}) == "OUTS"
    assert market_of({"market": " K "}) == "K"


def test_market_column_in_schema():
    assert "market" in FIELDS


def _write_ledger(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_dashboard_load_picks_filters_outs(tmp_path, monkeypatch):
    ledger = tmp_path / "picks.csv"
    _write_ledger(ledger, [
        _row(),                                   # legacy blank -> K
        _row(market="K", line="6.5"),
        _row(market="OUTS", line="17.5"),
    ])
    import tools.dashboard_data as dd
    monkeypatch.setattr(dd, "PICKS_PATH", ledger)
    picks = dd._load_picks()
    assert len(picks) == 2
    assert all(market_of(r) == "K" for r in picks)


def test_pl_calc_never_prints_a_combined_number(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "picks.csv"
    _write_ledger(ledger, [
        _row(market="K", graded_result="WIN", units_risked="1.0",
             market_over_odds="+100"),            # +1.00u
        _row(market="OUTS", line="17.5", graded_result="WIN",
             units_risked="1.0", market_over_odds="+100"),  # +1.00u
    ])
    import tools.pl_calc as plc
    monkeypatch.setattr(plc, "PICKS_PATH", ledger)
    plc.main()
    out = capsys.readouterr().out
    assert "STRIKEOUTS" in out and "OUTS RECORDED" in out
    assert "never combine" in out
    # each market's P&L appears alone; the blended +2.00 must not exist
    assert "+1.00 units" in out
    assert "+2.00" not in out


def test_pl_calc_single_market_output_unchanged(tmp_path, monkeypatch, capsys):
    """With only strikeout rows (today's reality) the output format the
    operator knows stays byte-compatible — no surprise header."""
    ledger = tmp_path / "picks.csv"
    _write_ledger(ledger, [_row(market="K", market_over_odds="+100")])
    import tools.pl_calc as plc
    monkeypatch.setattr(plc, "PICKS_PATH", ledger)
    plc.main()
    out = capsys.readouterr().out
    assert "STRIKEOUTS" not in out          # no per-market header needed
    assert "Record: 1W-0L" in out


def test_existing_pick_keys_separate_by_market(tmp_path, monkeypatch):
    """A K 5.5 and an OUTS 5.5 on the same arm are different bets: the
    strikeouts pipeline must neither lock nor overwrite the outs row."""
    ledger = tmp_path / "picks.csv"
    _write_ledger(ledger, [
        _row(market="K"),
        _row(market="OUTS"),                  # same game/pitcher/line
    ])
    import tools.daily_pipeline as dp
    monkeypatch.setattr(dp, "PICKS_PATH", ledger)
    existing = dp._load_existing_picks("2026-08-24")
    assert ("1", "7", "K", "5.5") in existing
    assert ("1", "7", "OUTS", "5.5") in existing
    assert len(existing) == 2
