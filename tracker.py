"""Strikeout picks ledger.

Ported from NRFI Terminal's tracker.py with strikeout-specific grading:
- VOID on scratched starters (not a loss)
- PUSH on whole-number alternate lines
- VOID if game called before pitcher removed

Atomic CSV writes only (tempfile + fsync + os.replace). Never delete rows.
Three defensive locks prevent accidental overwrites of finalized picks.
"""
import csv
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MAX_STAKE_UNITS = 2.0

# Mutable state root. The Railway worker points this at its persistent
# volume (DATA_STATE_DIR=/data/state); everywhere else it's the repo's
# data/ directory. It must be a real directory, NOT a symlink: the
# atomic-write pattern (tempfile + os.replace) replaces the target
# path, which would silently destroy a symlinked file and drop the
# write onto ephemeral container disk.
DATA_STATE_DIR = Path(
    os.environ.get("DATA_STATE_DIR") or (Path(__file__).parent / "data")
)

PICKS_PATH = DATA_STATE_DIR / "picks_2026.csv"
CHANGES_PATH = DATA_STATE_DIR / "pick_changes.csv"

FIELDS = [
    "date",
    "game_pk",
    "pitcher_name",
    "pitcher_id",
    "pitcher_team",
    "opponent_team",
    "is_home",
    "venue",
    "line",
    "pick_side",
    "pick_strength",
    "pick_label",
    "model_prob_over",
    "model_prob_under",
    "market_over_odds",
    "market_under_odds",
    "opened_over_odds",
    "opened_under_odds",
    "closing_over_odds",
    "closing_under_odds",
    "clv_pct",
    "no_vig_fair_prob",
    "edge_pct",
    "units_risked",
    "bet_placed",
    "graded_result",
    "actual_strikeouts",
    "profit_loss_units",
    "created_at",
    "updated_at",
    "lineup_source",
    "notes",
]


def _write_rows(path: Path, rows: list[dict]) -> None:
    """Atomic CSV write: tempfile + fsync + os.replace."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _pick_is_locked(existing: dict, iso_date: str) -> bool:
    """Three defensive locks. A locked pick cannot be updated."""
    now_et = datetime.now(ET)
    now_utc = datetime.now(UTC)

    graded = (existing.get("graded_result") or "").strip().upper()
    if graded in ("WIN", "LOSS", "VOID", "PUSH", "POSTPONED", "SUSPENDED"):
        return True

    try:
        slate_end_et = datetime.fromisoformat(iso_date).replace(
            hour=23, minute=59, tzinfo=ET
        )
        if now_et > slate_end_et + timedelta(hours=24):
            return True
    except Exception:
        pass

    try:
        created_at = (existing.get("created_at") or "").strip()
        if created_at:
            ca_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if (now_utc - ca_dt) > timedelta(hours=12):
                return True
    except Exception:
        pass

    return False


def _journal_change(
    iso_date: str,
    game_pk: int,
    pitcher_name: str,
    old_label: str,
    new_label: str,
) -> None:
    """Append to pick_changes.csv. Never deletes entries (90-day prune TBD)."""
    CHANGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not CHANGES_PATH.exists()
    with open(CHANGES_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "captured_at_utc",
                "date",
                "game_pk",
                "pitcher_name",
                "old_pick_label",
                "new_pick_label",
            ],
        )
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "captured_at_utc": datetime.now(UTC).isoformat(),
                "date": iso_date,
                "game_pk": game_pk,
                "pitcher_name": pitcher_name,
                "old_pick_label": old_label,
                "new_pick_label": new_label,
            }
        )


def _calc_pnl(row: dict) -> float:
    """Canonical P&L calculation. tools/pl_calc.py calls this."""
    graded = (row.get("graded_result") or "").strip().upper()
    bet_placed = (row.get("bet_placed") or "").strip().upper()

    if graded in ("VOID", "PUSH"):
        return 0.0

    if bet_placed != "Y":
        return 0.0

    units = float(row.get("units_risked") or 0)
    if units <= 0:
        return 0.0

    if graded == "LOSS":
        return -units

    if graded == "WIN":
        pick_side = (row.get("pick_side") or "").strip().upper()
        if pick_side == "OVER":
            odds_str = row.get("market_over_odds") or ""
        elif pick_side == "UNDER":
            odds_str = row.get("market_under_odds") or ""
        else:
            odds_str = ""

        try:
            odds = int(odds_str)
        except (ValueError, TypeError):
            odds = -110

        if odds > 0:
            return units * (odds / 100)
        else:
            return units * (100 / abs(odds))

    return 0.0


def grade_pick(row: dict, actual_k: int) -> str:
    """Grade a pick given actual strikeouts.

    Returns one of: WIN, LOSS, PUSH, VOID.

    VOID if the listed starter did not throw a pitch (actual_k is None
    or the pitcher was scratched).
    """
    if actual_k is None:
        return "VOID"

    pick_side = (row.get("pick_side") or "").strip().upper()
    line = float(row.get("line") or 0)

    if actual_k == line and line == int(line):
        return "PUSH"

    if pick_side == "OVER":
        return "WIN" if actual_k > line else "LOSS"
    elif pick_side == "UNDER":
        return "WIN" if actual_k < line else "LOSS"

    return "VOID"
