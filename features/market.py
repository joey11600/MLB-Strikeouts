"""Group H — Market and meta features.

T1: H1 (opening/current line), H2 (line movement), H3 (no-vig fair prob),
    H8 (model-vs-market disagreement), H9 (historical calibration bucket).

H6 (CLV) is NOT here — it's an evaluation metric, not a feature.
It lives in tools/pl_calc.py.
"""


def build_market_features(
    game_pk: int,
    pitcher_id: int,
    dk_odds: dict | None = None,
) -> dict:
    raise NotImplementedError("Phase 2")
