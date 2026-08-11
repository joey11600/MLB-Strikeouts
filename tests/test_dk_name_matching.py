"""DraftKings -> MLB probable-pitcher name matching.

DK disambiguates two players sharing a name by appending the team:
"Ryan Johnson (LAA)". The normalizer stripped accents and Jr./III but not
that tag, so the key never matched ("ryan johnson (laa)" != "ryan johnson")
and the last-name fallback compared "(laa)" against "johnson" and missed
too. He was silently dropped from every slate DK listed him on -- both
2026-08-06 and 2026-08-11 -- while carrying a live posted line.

The failure mode is invisible by construction: a dropped pitcher leaves no
row anywhere, so the board just looks like a short slate. These tests pin
both halves of the fix -- the tag is stripped for matching, and it is used
to break ties rather than thrown away.

Run:  python -m pytest tests/test_dk_name_matching.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.daily_pipeline import (  # noqa: E402
    _match_dk_to_mlb, _normalize_name, _team_tag,
)


def _game(game_pk, away_name, away_id, away_abbr, home_name, home_id, home_abbr):
    return {
        "game_pk": game_pk,
        "away_probable_id": away_id,
        "away_probable_name": away_name,
        "away_team_abbr": away_abbr,
        "home_probable_id": home_id,
        "home_probable_name": home_name,
        "home_team_abbr": home_abbr,
        "venue_name": "Test Park",
        "away_lineup": [],
        "home_lineup": [],
        "lineup_source": "none",
    }


# --- normalizer -----------------------------------------------------

def test_team_tag_is_stripped_from_the_name():
    assert _normalize_name("Ryan Johnson (LAA)") == "ryan johnson"


def test_stripping_the_tag_does_not_glue_words_together():
    # The tag is replaced with a space, not deleted, so an interior
    # parenthetical cannot fuse the names on either side of it.
    assert _normalize_name("Ryan (LAA) Johnson") == "ryan johnson"


def test_existing_normalizer_behaviour_is_unchanged():
    assert _normalize_name("Cristopher Sánchez") == "cristopher sanchez"
    assert _normalize_name("Lucas Erceg Jr.") == "lucas erceg"
    assert _normalize_name("  Paul   Skenes  ") == "paul skenes"


def test_team_tag_extraction():
    assert _team_tag("Ryan Johnson (LAA)") == "LAA"
    assert _team_tag("Ryan Johnson") is None
    # Not a team abbreviation -- must not become a bogus tie-breaker.
    assert _team_tag("Ryan Johnson (prospect)") is None


# --- matching -------------------------------------------------------

def test_tagged_pitcher_is_matched():
    """The regression. Before the fix this returned zero matches."""
    games = [_game(1, "Cody Bradford", 674003, "TEX",
                   "Ryan Johnson", 696270, "LAA")]
    props = [{"pitcher_name": "Ryan Johnson (LAA)", "line": 4.5}]

    matched = _match_dk_to_mlb(props, games)

    assert len(matched) == 1
    assert matched[0]["pitcher_id"] == 696270
    assert matched[0]["pitcher_team"] == "LAA"
    assert matched[0]["line"] == 4.5
    # The tag must not survive onto the board / ledger / grader.
    assert matched[0]["pitcher_name"] == "Ryan Johnson"


def test_untagged_names_keep_the_books_exact_spelling():
    """Only the tag is stripped. The ledger and grader join on this value,
    so no other normalization may leak into it."""
    games = [_game(1, "Cristopher Sánchez", 650911, "PHI",
                   "Andre Pallante", 664299, "STL")]
    props = [{"pitcher_name": "Cristopher Sanchez", "line": 6.5}]

    matched = _match_dk_to_mlb(props, games)

    assert matched[0]["pitcher_name"] == "Cristopher Sanchez"


def test_tag_picks_the_right_one_when_two_pitchers_share_a_name():
    games = [
        _game(1, "Ryan Johnson", 696270, "LAA", "Paul Skenes", 694973, "PIT"),
        _game(2, "Ryan Johnson", 111111, "SEA", "Bryan Woo", 682120, "SEA"),
    ]
    props = [{"pitcher_name": "Ryan Johnson (SEA)", "line": 5.5}]

    matched = _match_dk_to_mlb(props, games)

    assert len(matched) == 1
    assert matched[0]["pitcher_id"] == 111111


def test_untagged_duplicate_name_is_refused_not_guessed():
    """No tag and two candidates -> drop the row.

    Guessing would price one pitcher's projection against the other's
    number, and the edge filter selects hardest on exactly that mismatch.
    """
    games = [
        _game(1, "Ryan Johnson", 696270, "LAA", "Paul Skenes", 694973, "PIT"),
        _game(2, "Ryan Johnson", 111111, "SEA", "Bryan Woo", 682120, "SEA"),
    ]
    props = [{"pitcher_name": "Ryan Johnson", "line": 5.5}]

    assert _match_dk_to_mlb(props, games) == []


def test_shared_surname_on_one_slate_is_refused():
    """Two different pitchers named *Perez* both start -> no last-name guess."""
    games = [
        _game(1, "Eury Perez", 668678, "MIA", "Paul Skenes", 694973, "PIT"),
        _game(2, "Martin Perez", 527048, "ATL", "Nolan McLean", 690997, "NYM"),
    ]
    props = [{"pitcher_name": "E. Perez", "line": 6.5}]

    assert _match_dk_to_mlb(props, games) == []


def test_last_name_fallback_still_works_when_unambiguous():
    games = [_game(1, "Eury Perez", 668678, "MIA", "Paul Skenes", 694973, "PIT")]
    props = [{"pitcher_name": "E. Perez", "line": 6.5}]

    matched = _match_dk_to_mlb(props, games)

    assert len(matched) == 1
    assert matched[0]["pitcher_id"] == 668678


def test_unknown_pitcher_is_dropped():
    games = [_game(1, "Eury Perez", 668678, "MIA", "Paul Skenes", 694973, "PIT")]
    props = [{"pitcher_name": "Nobody Atall", "line": 4.5}]

    assert _match_dk_to_mlb(props, games) == []


def test_full_slate_shape_is_preserved():
    """Every DK row with a clean unique name still matches exactly once."""
    games = [
        _game(1, "Tanner Bibee", 676440, "CLE", "Drew Anderson", 623454, "DET"),
        _game(2, "Cody Bradford", 674003, "TEX", "Ryan Johnson", 696270, "LAA"),
        _game(3, "Michael Wacha", 608379, "KC", "Blake Snell", 605483, "LAD"),
    ]
    props = [
        {"pitcher_name": "Tanner Bibee", "line": 4.5},
        {"pitcher_name": "Drew Anderson", "line": 3.5},
        {"pitcher_name": "Cody Bradford", "line": 4.5},
        {"pitcher_name": "Ryan Johnson (LAA)", "line": 4.5},
        {"pitcher_name": "Michael Wacha", "line": 4.5},
        {"pitcher_name": "Blake Snell", "line": 6.5},
    ]

    matched = _match_dk_to_mlb(props, games)

    assert len(matched) == 6
    assert {m["pitcher_id"] for m in matched} == {
        676440, 623454, 674003, 696270, 608379, 605483,
    }
