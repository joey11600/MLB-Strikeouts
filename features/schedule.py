"""Group F — Schedule, travel, and circadian features.

No T1 features in this group. All are T2 or lower.

T2: F1 (eastward travel), F3 (days since TZ arrival), F4 (consecutive
    road games), F7 (late-season chase-rate decay).
"""


def build_schedule_features(
    team_id: int,
    game_pk: int,
    game_dt_utc: str,
) -> dict:
    raise NotImplementedError("Phase 6 — T2 features")
