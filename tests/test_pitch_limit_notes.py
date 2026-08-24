"""A-050: the pitch-limit note parser must be narrow.

A false positive puts a phantom cap suggestion in front of the operator
every day; a miss costs nothing (the operator reads notes anyway). So
every announced-limit phrasing should match, and every retrospective
"threw N pitches" phrasing must not.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.daily_pipeline import _match_pitch_limit


def test_announced_limits_match():
    cases = {
        "He will be limited to 75 pitches in his return from the IL.": 75,
        "Club sources say a pitch limit of 60 is planned.": 60,
        "Expected to be capped at 80 pitches as he builds up.": 80,
        "The plan is around 65-70 pitches tonight.": 65,   # range floor
        "Working on a pitch count around 85.": 85,
    }
    for note, want in cases.items():
        got = _match_pitch_limit(note)
        assert got is not None, note
        assert got[0] == want, (note, got)


def test_retrospective_usage_never_matches():
    cases = [
        "Threw 95 pitches in his last start against Boston.",
        "He tossed around 90 pitches last time out.",
        "Has thrown at least 100 pitches in three straight outings.",
        "Went six innings on 88 pitches in his previous outing.",
        "",
    ]
    for note in cases:
        assert _match_pitch_limit(note) is None, note


def test_absurd_numbers_rejected():
    assert _match_pitch_limit("pitch limit of 200 expected") is None
    assert _match_pitch_limit("limited to 20 pitches") is None
