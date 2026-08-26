"""Paper-track policies pinned to the slate that created them.

The 2026-08-24 gold rows are frozen history (real captured DK odds,
graded actuals), embedded here as fixtures. If a refactor moves any
policy's day total, these fail — the paper record must stay
reproducible or it is not evidence.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import outs_paper
from tools.outs_paper import _policy_bets, _settle

# fair_over passes the presence gate only; _policy_bets re-derives the
# no-vig fair from the odds.
GOLD_0824 = [
    {"pitcher_name": "Jose Urquidy", "game_pk": "824557",
     "pitcher_id": "664353", "line": 14.5, "over_odds": "-147",
     "under_odds": "+111", "p_over_cal": 0.3485, "fair_over": 0.5},
    {"pitcher_name": "Ranger Suarez", "game_pk": "823828",
     "pitcher_id": "624133", "line": 17.5, "over_odds": "-110",
     "under_odds": "-120", "p_over_cal": 0.3291, "fair_over": 0.5},
    {"pitcher_name": "Zack Wheeler", "game_pk": "823097",
     "pitcher_id": "554430", "line": 17.5, "over_odds": "-176",
     "under_odds": "+132", "p_over_cal": 0.4571, "fair_over": 0.5},
    {"pitcher_name": "Cade Cavalli", "game_pk": "822695",
     "pitcher_id": "676917", "line": 17.5, "over_odds": "-200",
     "under_odds": "+149", "p_over_cal": 0.4896, "fair_over": 0.5},
    {"pitcher_name": "Framber Valdez", "game_pk": "824235",
     "pitcher_id": "664285", "line": 18.5, "over_odds": "+131",
     "under_odds": "-175", "p_over_cal": 0.2744, "fair_over": 0.5},
    {"pitcher_name": "Braxton Ashcraft", "game_pk": "823260",
     "pitcher_id": "677952", "line": 17.5, "over_odds": "-142",
     "under_odds": "+107", "p_over_cal": 0.4583, "fair_over": 0.5},
]
ACTUALS_0824 = {"664353": 9, "624133": 15, "554430": 12,
                "676917": 18, "664285": 18, "677952": 18}


def _day_total(policy):
    bets = _policy_bets(policy, GOLD_0824)
    settled = [_settle(b, ACTUALS_0824[str(b["pitcher_id"])]) for b in bets]
    staked = sum(b["units_risked"] for b in bets)
    pl = sum(x for _, x in settled)
    return bets, staked, pl


def test_gates_takes_only_urquidy():
    bets, staked, pl = _day_total("gates")
    assert [b["pitcher_name"] for b in bets] == ["Jose Urquidy"]
    assert bets[0]["side"] == "UNDER"
    assert staked == 2.0
    assert pl == pytest.approx(2.22, abs=0.01)


def test_gold_capped_five_bets_daily_cap_cuts_ashcraft():
    bets, staked, pl = _day_total("gold_capped")
    names = {b["pitcher_name"] for b in bets}
    assert "Braxton Ashcraft" not in names          # 10u daily cap
    assert len(bets) == 5
    assert all(b["units_risked"] == 2.0 for b in bets)
    assert staked == 10.0
    assert pl == pytest.approx(5.67, abs=0.01)


def test_gold_uncapped_all_six_full_kelly():
    bets, staked, pl = _day_total("gold_uncapped")
    assert len(bets) == 6
    assert max(b["units_risked"] for b in bets) > 2.0   # truly uncapped
    assert staked == pytest.approx(33.77, abs=0.05)
    assert pl == pytest.approx(17.73, abs=0.05)


def test_settle_push_and_void():
    bet = {"line": 18.0, "odds": +100, "side": "OVER", "units_risked": 2.0}
    assert _settle(bet, 18) == ("PUSH", 0.0)      # exact land, whole line
    assert _settle(bet, None) == ("VOID", 0.0)    # no actual -> no action
    assert _settle(bet, 19) == ("WIN", 2.0)
    assert _settle(bet, 17) == ("LOSS", -2.0)


def _paper_env(tmp_path, monkeypatch, dates, actual_rows):
    import csv

    log = tmp_path / "outs_model_log.csv"
    with open(log, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "pitcher_id",
                                          "actual_outs"])
        w.writeheader()
        w.writerows(actual_rows)
    monkeypatch.setattr(outs_paper, "OUTS_LOG_PATH", log)
    monkeypatch.setattr(outs_paper, "PAPER_PATH",
                        tmp_path / "outs_paper_tracks.csv")
    monkeypatch.setattr(outs_paper, "available_dates", lambda: dates)
    monkeypatch.setattr(outs_paper, "load_slate",
                        lambda d: {"board": GOLD_0824})


def test_tonight_never_scores_even_after_utc_midnight(tmp_path, monkeypatch):
    """The slate clock is ET. A date equal to today-in-ET must not
    settle, no matter what the UTC calendar says (the 22:07 ET day-one
    bug: UTC had rolled over and tonight scored mid-slate)."""
    from datetime import datetime

    today_et = datetime.now(outs_paper.ET).date().isoformat()
    _paper_env(tmp_path, monkeypatch, [today_et],
               [{"date": today_et, "pitcher_id": pid, "actual_outs": got}
                for pid, got in ACTUALS_0824.items()])
    assert outs_paper.log_paper_tracks() == 0


def test_partial_grades_defer_until_complete(tmp_path, monkeypatch):
    """A recent date with a bet pitcher still ungraded must wait — not
    settle the missing one as VOID (the 03:00-before-Savant window and
    same-night boxscore grading both produce partial logs)."""
    from datetime import datetime, timedelta

    d = (datetime.now(outs_paper.ET).date()
         - timedelta(days=1)).isoformat()
    partial = [{"date": d, "pitcher_id": pid, "actual_outs": got}
               for pid, got in ACTUALS_0824.items()
               if pid != "664353"]           # Urquidy not yet graded
    _paper_env(tmp_path, monkeypatch, [d], partial)
    assert outs_paper.log_paper_tracks() == 0    # every policy bets him

    # grades complete -> the same pairs now settle in full
    _paper_env(tmp_path, monkeypatch, [d],
               [{"date": d, "pitcher_id": pid, "actual_outs": got}
                for pid, got in ACTUALS_0824.items()])
    assert outs_paper.log_paper_tracks() == 12
    assert not any(r["result"] == "VOID"
                   for r in outs_paper._existing()[0])


def test_frozen_pairs_never_rescored(tmp_path, monkeypatch):
    """A (date, policy) pair is written once; reruns add nothing."""
    import csv

    log = tmp_path / "outs_model_log.csv"
    with open(log, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "pitcher_id",
                                          "actual_outs"])
        w.writeheader()
        for pid, got in ACTUALS_0824.items():
            w.writerow({"date": "2026-08-24", "pitcher_id": pid,
                        "actual_outs": got})

    monkeypatch.setattr(outs_paper, "OUTS_LOG_PATH", log)
    monkeypatch.setattr(outs_paper, "PAPER_PATH",
                        tmp_path / "outs_paper_tracks.csv")
    monkeypatch.setattr(outs_paper, "available_dates",
                        lambda: ["2026-08-24"])
    monkeypatch.setattr(outs_paper, "load_slate",
                        lambda d: {"board": GOLD_0824})

    first = outs_paper.log_paper_tracks()
    assert first == 12                     # 1 + 5 + 6 bets across policies
    assert outs_paper.log_paper_tracks() == 0   # frozen
    s = outs_paper.paper_summary()
    assert s["policies"]["gates"]["pl"] == pytest.approx(2.22, abs=0.01)
    assert s["policies"]["gold_capped"]["pl"] == pytest.approx(5.67,
                                                               abs=0.01)
    assert s["policies"]["gold_uncapped"]["pl"] == pytest.approx(17.73,
                                                                 abs=0.05)
