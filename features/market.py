"""Group H — Market and meta features.

T1: H1 (opening/current line), H2 (line movement), H3 (no-vig fair prob),
    H8 (model-vs-market disagreement), H9 (historical calibration bucket).

H3 lives in models/edge.py (no_vig_fair_prob); H8 in compute_edge. H6
(CLV) is NOT here — it's an evaluation metric, not a feature; it lives
in tools/pl_calc.py and the grader.

H1/H2 (A-049, wired 2026-08-24). Line movement is the distilled form of
every piece of late information the market gets — injury whispers,
weather, lineups, pitch-count plans — and the audit showed the model
loses even to the MORNING price, so inheriting the market's own drift is
the cheapest information channel the repo has. Two parts:

  1. record_intraday_snapshot(): persist EVERY odds capture the pipeline
     sees to data/odds/intraday_YYYY-MM-DD.csv. Before this, the
     sidecar's newest-wins merge overwrote the morning price at each
     reprice, so the OPEN was not durably archived anywhere — the one
     input that can never be backfilled (the A-002 lesson, again).
  2. movement_features(): open line/fair vs the latest capture, plus
     capture count, for one pitcher.

CAPTURE-FIRST, MODEL-LATER (the Phase 10 pattern): these values ride the
sidecar and model_log as diagnostics and screen inputs. Nothing prices
off them until they pass the gauntlet on a market-scored sample —
which needs exactly this archive to exist first.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.edge import no_vig_fair_prob  # noqa: E402
from tracker import DATA_STATE_DIR  # noqa: E402

INTRADAY_DIR = DATA_STATE_DIR / "odds"
INTRADAY_FIELDS = ["captured_at", "date", "pitcher_name", "line",
                   "over_odds", "under_odds", "odds_source"]


def _intraday_path(iso_date: str) -> Path:
    return INTRADAY_DIR / f"intraday_{iso_date}.csv"


def record_intraday_snapshot(iso_date: str, props: list[dict]) -> int:
    """Append this run's odds capture to the day's intraday archive.

    Append-only time series; the whole file is rewritten atomically
    (tempfile + fsync + replace, repo rule) because bare appends are
    not atomic on this filesystem. Snapshot-sourced rows are recorded
    too — odds_source says so, downstream decides.
    """
    if not props:
        return 0
    path = _intraday_path(iso_date)
    existing: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    now = datetime.now(timezone.utc).isoformat()
    for p in props:
        existing.append({
            "captured_at": now,
            "date": iso_date,
            "pitcher_name": p.get("pitcher_name", ""),
            "line": p.get("line", ""),
            "over_odds": p.get("over_odds", ""),
            "under_odds": p.get("under_odds", ""),
            "odds_source": p.get("odds_source", ""),
        })

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=INTRADAY_FIELDS,
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(existing)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(props)


def load_intraday(iso_date: str, normalize) -> dict[str, list[dict]]:
    """The day's captures grouped by normalized pitcher name, in time
    order. `normalize` is the caller's name normalizer (the pipeline's
    _normalize_name) — passed in rather than imported so features/
    never depends on tools/."""
    path = _intraday_path(iso_date)
    if not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: r.get("captured_at", ""))
    for r in rows:
        key = normalize(r.get("pitcher_name", ""))
        if key:
            out.setdefault(key, []).append(r)
    return out


def movement_features(captures: list[dict] | None) -> dict:
    """H1/H2 for one pitcher from the day's capture series.

    Returns h1_open_line, h1_open_fair_over, h2_line_move (current
    minus open, in strikeouts), h2_fair_move (current no-vig fair over
    minus open's), h2_n_captures. All None when fewer than one capture
    exists or prices are unparseable — never fabricated (A-007).

    READ THE PAIR TOGETHER: each capture's fair prob is priced AT THAT
    CAPTURE'S LINE, so when the line itself moved, h2_fair_move mixes
    two effects (a line dropped a full point mechanically RAISES the
    fair prob of clearing the new, lower line). h2_fair_move is cleanly
    interpretable on its own only when h2_line_move == 0; when the line
    moved, the line move IS the market signal and dominates. A screen
    consuming these must treat (line_move, fair_move) jointly.
    """
    out = {"h1_open_line": None, "h1_open_fair_over": None,
           "h2_line_move": None, "h2_fair_move": None,
           "h2_n_captures": len(captures or [])}
    if not captures:
        return out
    first, last = captures[0], captures[-1]
    try:
        open_line = float(first["line"])
        open_fair = no_vig_fair_prob(first["over_odds"],
                                     first["under_odds"])["fair_over"]
    except (TypeError, ValueError, KeyError):
        return out
    out["h1_open_line"] = open_line
    out["h1_open_fair_over"] = round(open_fair, 4)
    try:
        cur_line = float(last["line"])
        cur_fair = no_vig_fair_prob(last["over_odds"],
                                    last["under_odds"])["fair_over"]
    except (TypeError, ValueError, KeyError):
        return out
    out["h2_line_move"] = round(cur_line - open_line, 2)
    out["h2_fair_move"] = round(cur_fair - open_fair, 4)
    return out


def build_market_features(
    game_pk: int,
    pitcher_id: int,
    dk_odds: dict | None = None,
) -> dict:
    """Legacy Phase-2 signature, kept so old callers fail loudly with
    direction instead of silently: use record_intraday_snapshot /
    load_intraday / movement_features."""
    raise NotImplementedError(
        "use record_intraday_snapshot + load_intraday + movement_features"
    )
