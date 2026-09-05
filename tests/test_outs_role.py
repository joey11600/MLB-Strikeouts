"""A-054: the relief-role facts on a sidecar row, and the paper policy
that reads them.

The 2026-09-04 board is the fixture. Kade Morris (one June start, four
relief outings since, DK line 9.5, model P(over) 0.906) and Logan Allen
(a reliever with two starts all year) are the two relief-role rows;
gates as written staked both OVER at 2u, and Allen threw 7 outs. The
shadow skips them, and the daily cap that freed lets Blake Snell UNDER
in. Odds and model numbers are the served values.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.backfill_statcast as BS
import tools.outs_serve as OS
from tools import outs_paper
from tools.outs_paper import _policy_bets, board_paper_columns, relief_role


def _role(d, pitches, was_start, relief_since, days_since_start):
    return {"prev_app_date": d, "prev_app_pitches": pitches,
            "prev_app_was_start": was_start,
            "relief_apps_since_last_start": relief_since,
            "days_since_prev_start": days_since_start}


# fair_over passes the presence gate only; _policy_bets re-derives the
# no-vig fair from the odds.
BOARD_0904 = [
    {"pitcher_name": "Kade Morris", "game_pk": 823093, "pitcher_id": 695034,
     "line": 9.5, "over_odds": "+119", "under_odds": "-159",
     "p_over_cal": 0.9058, "fair_over": 0.4265,
     "role": _role("2026-08-30", 35, False, 4, 90.0)},
    {"pitcher_name": "Logan Allen", "game_pk": 824424, "pitcher_id": 671106,
     "line": 11.5, "over_odds": "-120", "under_odds": "-110",
     "p_over_cal": 0.8332, "fair_over": 0.5101,
     "role": _role("2026-08-29", 26, False, 4, 48.0)},
    {"pitcher_name": "Andre Pallante", "game_pk": 824311, "pitcher_id": 669467,
     "line": 11.5, "over_odds": "-172", "under_odds": "+129",
     "p_over_cal": 0.8938, "fair_over": 0.5915,
     "role": _role("2026-08-17", 88, True, 0, 18.0)},
    {"pitcher_name": "Max Fried", "game_pk": 823256, "pitcher_id": 608331,
     "line": 12.5, "over_odds": "-124", "under_odds": "-107",
     "p_over_cal": 0.8104, "fair_over": 0.5171,
     "role": _role("2026-08-29", 63, True, 0, 6.0)},
    {"pitcher_name": "Matt Wilkinson", "game_pk": 823579, "pitcher_id": 683363,
     "line": 15.5, "over_odds": "+141", "under_odds": "-189",
     "p_over_cal": 0.18, "fair_over": 0.3882,
     "role": _role("2026-08-29", 90, True, 0, 6.0)},
    {"pitcher_name": "Blake Snell", "game_pk": 823905, "pitcher_id": 605483,
     "line": 17.5, "over_odds": "-162", "under_odds": "+122",
     "p_over_cal": 0.384, "fair_over": 0.5785,
     "role": _role("2026-08-29", 99, True, 0, 6.0)},
]


def _names(policy, board):
    return [b["pitcher_name"] for b in _policy_bets(policy, board)]


# ------------------------------------------------------------ the rule
def test_relief_role_reads_the_block_and_names_the_unknown():
    assert relief_role({}) is None                       # predates the block
    assert relief_role({"role": None}) is None
    assert relief_role({"role": {"prev_app_pitches": 3}}) is None
    assert relief_role({"role": _role(None, None, None, None, None)}) is False
    assert relief_role({"role": _role("2026-08-30", 35, False, 4, 90.0)}) is True
    assert relief_role({"role": _role("2026-08-29", 63, True, 0, 6.0)}) is False


# --------------------------------------------------------- the policy
def test_gates_role_skips_relief_rows_and_the_freed_cap_flows_down():
    assert _names("gates", BOARD_0904) == [
        "Kade Morris", "Logan Allen", "Andre Pallante", "Max Fried",
        "Matt Wilkinson"]                                # 5 x 2u = the daily cap
    shadow = _policy_bets("gates_role", BOARD_0904)
    assert [b["pitcher_name"] for b in shadow] == [
        "Andre Pallante", "Max Fried", "Matt Wilkinson", "Blake Snell"]
    assert all(b["units_risked"] == 2.0 for b in shadow)
    assert sum(b["units_risked"] for b in shadow) == 8.0
    snell = shadow[-1]
    assert snell["side"] == "UNDER" and snell["clears_threshold"]
    # Snell was a gates-clearing row all along; only the cap kept him out
    assert snell["best_edge"] > snell["gate_threshold"]


def test_gates_role_sizes_exactly_like_gates_on_the_rows_it_keeps():
    gates = {b["pitcher_name"]: b for b in _policy_bets("gates", BOARD_0904)}
    for b in _policy_bets("gates_role", BOARD_0904):
        if b["pitcher_name"] in gates:
            g = gates[b["pitcher_name"]]
            assert (b["side"], b["units_risked"], b["odds"]) == \
                (g["side"], g["units_risked"], g["odds"])
            assert b["best_edge"] == pytest.approx(g["best_edge"])


def test_gates_role_refuses_a_slate_that_lacks_the_block():
    """An older slate must yield NO shadow bets, not quietly become
    `gates` — that would pollute the very comparison the policy is for."""
    stripped = [{k: v for k, v in r.items() if k != "role"} for r in BOARD_0904]
    assert _policy_bets("gates_role", stripped) == []
    assert len(_policy_bets("gates", stripped)) == 5   # gates unaffected
    one_missing = [dict(r) for r in BOARD_0904]
    del one_missing[3]["role"]
    assert _policy_bets("gates_role", one_missing) == []


def test_policies_tuple_carries_the_shadow():
    assert outs_paper.POLICIES == ("gates", "gates_role", "gold_capped",
                                   "gold_uncapped")


# --------------------------------------------------------- the board
def test_board_columns_carry_the_role_verdicts():
    cols = board_paper_columns(BOARD_0904)
    morris, allen = cols["695034"], cols["671106"]
    for c in (morris, allen):
        assert c["clears_gates"] is True                 # gates stakes it
        assert c["relief_role"] is True
        assert c["role_skip"] is True                    # ...and the shadow refuses
        assert c["gates_role_units"] == 0.0
    pallante = cols["669467"]
    assert pallante["relief_role"] is False
    assert pallante["role_skip"] is False
    assert pallante["gates_role_units"] == 2.0
    # Snell: no other policy staked him, so he only exists on the board
    # because the shadow does — stake 0 from gold, 2u from the shadow.
    snell = cols["605483"]
    assert snell["side"] == "UNDER"
    assert snell["stake_units"] == 0.0
    assert snell["clears_gates"] is False
    assert snell["role_skip"] is False
    assert snell["gates_role_units"] == 2.0
    assert snell["gate_edge"] > snell["gate_threshold"]


def test_board_columns_without_the_block_change_nothing_but_the_flags():
    stripped = [{k: v for k, v in r.items() if k != "role"} for r in BOARD_0904]
    cols = board_paper_columns(stripped)
    assert "605483" not in cols                          # no shadow row appears
    assert all(c["relief_role"] is None for c in cols.values())
    assert all(c["role_skip"] is False for c in cols.values())
    assert all(c["gates_role_units"] == 0.0 for c in cols.values())


def test_paper_summary_reports_the_shadow_policy(tmp_path, monkeypatch):
    p = tmp_path / "paper.csv"
    p.write_text(
        "date,policy,game_pk,pitcher_id,pitcher_name,side,line,odds,"
        "stake_units,result,pl_units,logged_at\n"
        "2026-09-05,gates_role,1,1,X,UNDER,17.5,122,2.0,WIN,2.44,t\n"
        "2026-09-05,gates,1,2,Y,OVER,9.5,119,2.0,LOSS,-2.0,t\n",
        encoding="utf-8")
    monkeypatch.setattr(outs_paper, "PAPER_PATH", p)
    s = outs_paper.paper_summary()
    assert s["policies"]["gates_role"]["wins"] == 1
    assert s["policies"]["gates_role"]["pl"] == pytest.approx(2.44)
    assert s["policies"]["gates_role"]["dates"] == 1
    assert s["policies"]["gates"]["losses"] == 1


# ------------------------------------------------------- the lookup
def _pitch_rows(game_pk, game_date, home, away, top, bot):
    """Synthetic pitch-level rows. `top` pitch for HOME (Top half),
    `bot` for AWAY; each is an ordered list of (pitcher, n_pitches)."""
    rows, ab = [], 0
    for half, plist in (("Top", top), ("Bot", bot)):
        for pid, n in plist:
            for k in range(n):
                if k % 5 == 0:
                    ab += 1
                rows.append({"pitcher": pid, "game_date": game_date,
                             "game_pk": game_pk, "at_bat_number": ab,
                             "pitch_number": k % 5 + 1,
                             "inning_topbot": half,
                             "home_team": home, "away_team": away})
    return rows


def _season():
    rows = []
    # 06-01: pitcher 1 starts for NYY (40 pitches); 5 relieves for BOS
    rows += _pitch_rows(1, "2026-06-01", "NYY", "BOS",
                        top=[(1, 40)], bot=[(2, 45), (5, 20)])
    # 06-05: pitcher 3 starts for NYY, pitcher 1 RELIEVES (30 pitches);
    #        5 relieves again for TB
    rows += _pitch_rows(2, "2026-06-05", "NYY", "TB",
                        top=[(3, 30), (1, 30)], bot=[(4, 50), (5, 15)])
    return pd.DataFrame(rows)


def test_appearance_lookup_reads_role_facts_from_pitch_rows(monkeypatch):
    monkeypatch.setattr(BS, "load_cached", lambda a, b: _season())
    apps = OS._appearance_lookup("2026-06-10", [1, 3, 5, 99])
    one = apps[1]
    assert one["prev_app_date"] == "2026-06-05"
    assert one["prev_app_was_start"] is False            # he relieved last time
    assert one["prev_app_pitches"] == 30
    assert one["relief_apps_since_last_start"] == 1
    assert one["days_since_prev_app"] == 5.0
    assert one["days_since_prev_start"] == 9.0           # the 06-01 start
    three = apps[3]
    assert three["prev_app_was_start"] is True
    assert three["prev_app_pitches"] == 30
    assert three["relief_apps_since_last_start"] == 0
    assert three["days_since_prev_start"] == 5.0
    five = apps[5]                                       # never started
    assert five["prev_app_was_start"] is False
    assert five["relief_apps_since_last_start"] == 2
    assert five["days_since_prev_start"] is None
    assert 99 not in apps                                # no appearance at all
    # the one-number view is unchanged in meaning: ANY appearance
    assert OS._drest_lookup("2026-06-10", [1, 3, 5]) == {1: 5.0, 3: 5.0, 5: 5.0}


def test_appearance_lookup_degrades_to_days_rest_without_role_columns(monkeypatch):
    slim = _season()[["pitcher", "game_date", "game_pk"]]
    monkeypatch.setattr(BS, "load_cached", lambda a, b: slim)
    apps = OS._appearance_lookup("2026-06-10", [1, 3])
    assert apps[1]["days_since_prev_app"] == 5.0
    assert apps[1]["prev_app_was_start"] is None         # unknown, not False
    assert relief_role({"role": OS.role_block(apps, 1)}) is False


def test_role_block_is_always_complete():
    empty = OS.role_block({}, 42)
    assert set(empty) == set(OS.ROLE_FIELDS)
    assert all(v is None for v in empty.values())
    monkey = {695034: {"days_since_prev_app": 5.0, "prev_app_date": "2026-08-30",
                       "prev_app_pitches": 35, "prev_app_was_start": False,
                       "relief_apps_since_last_start": 4,
                       "days_since_prev_start": 90.0}}
    blk = OS.role_block(monkey, 695034)
    assert "days_since_prev_app" not in blk               # not a role fact
    assert blk["prev_app_was_start"] is False and blk["prev_app_pitches"] == 35


def test_today_rows_take_days_rest_from_the_shared_lookup(monkeypatch):
    seen = {}

    def fake(iso, ids):
        seen["ids"] = list(ids)
        return {1: {"days_since_prev_app": 5.0}, 3: {"days_since_prev_app": 11.0}}

    monkeypatch.setattr(OS, "_appearance_lookup", fake)
    matched = [{"game_pk": 7, "pitcher_id": 1, "is_home": True, "opponent_team": "BOS"},
               {"game_pk": 8, "pitcher_id": 3, "is_home": False, "opponent_team": "TB"},
               {"game_pk": 9, "pitcher_id": 9, "is_home": False, "opponent_team": "SEA"}]
    rows = OS._today_rows("2026-06-10", matched)
    assert seen["ids"] == [1, 3, 9]
    got = rows.set_index("pitcher")["days_since_prev_game"]
    assert got[1] == 5.0 and got[3] == 11.0
    assert pd.isna(got[9])                               # no history: blank, not 0
    # and a caller that already holds the lookup can hand it over
    seen.clear()
    rows2 = OS._today_rows("2026-06-10", matched, {1: {"days_since_prev_app": 2.0}})
    assert not seen
    assert rows2.set_index("pitcher")["days_since_prev_game"][1] == 2.0
