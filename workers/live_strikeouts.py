"""Live starter tracker — final K line the moment the starter is pulled.

A starter's strikeout total is settled the instant he leaves the game,
which is often hours before the game ends and many hours before
Statcast publishes. Today's 3am grading pass means a pick decided at
7:20pm sits unresolved overnight.

The MLB Stats API knows immediately and, unlike DraftKings, does not
block datacenter IPs — which is exactly why the sibling NRFI repo runs
its live worker on Railway. Same idea here, aimed at starters:

    schedule -> per-game boxscore -> battersFaced / strikeOuts
    starter is DONE when a later pitcher appears for his team,
    or the game reaches a terminal state

Why "a later pitcher appeared" and not "innings look finished": the
boxscore lists pitchers in appearance order, so the starter being
followed by anyone is an unambiguous, already-happened fact. Inferring
from inning or pitch count would be a guess, and a guess that finalises
a bet is the failure mode this repo keeps paying for.

Deliberately READ-ONLY with respect to the ledger. It publishes state;
it does not grade. Statcast remains the graded source of truth, and
tools/live_reconcile.py compares the two so a disagreement is a loud
finding rather than a silent overwrite. Live numbers are for watching
picks resolve and for knowing hours early — not for booking money on a
feed that can revise itself mid-inning.

Polls TODAY, and finishes YESTERDAY before it starts. A poll asks for
one date; a start that crosses midnight ET is still in progress when
the date rolls, so watching only today abandoned it mid-game and the
board showed a live pulse on a game that ended hours earlier (A-039).

Env:
    LIVE_POLL_S            seconds between polls while games are live (30)
    LIVE_QUIET_S           seconds between polls otherwise (600)
    LIVE_STATE_PATH        output file (defaults under DATA_STATE_DIR)
    LIVE_CARRYOVER_UNTIL_H ET hour to stop chasing yesterday (12)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tracker import DATA_STATE_DIR  # noqa: E402

ET = ZoneInfo("America/New_York")
API = "https://statsapi.mlb.com/api/v1"

POLL_S = int(os.environ.get("LIVE_POLL_S", "30"))
QUIET_S = int(os.environ.get("LIVE_QUIET_S", "600"))
CARRYOVER_UNTIL_H = int(os.environ.get("LIVE_CARRYOVER_UNTIL_H", "12"))
STATE_PATH = Path(os.environ.get("LIVE_STATE_PATH")
                  or (DATA_STATE_DIR / "live_state.json"))
SLATES_DIR = DATA_STATE_DIR / "slates"
# Per-date archive of the same payload. live_state.json is a SINGLE file
# the poller overwrites, so at midnight ET it rolls to the new date and
# yesterday's finals cease to exist -- while Statcast, the only other
# source of a board-wide K total, does not publish them until ~09:00 ET
# (A-022). That left the previous day's board blank for every starter who
# was not a graded bet, from midnight to 09:00, every single night
# (A-035). Keyed by date so the board can ask for the date it is
# rendering instead of hoping it is still "today".
LIVE_DIR = DATA_STATE_DIR / "live"

TERMINAL = {"Final", "Game Over", "Completed Early"}


def log(msg: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}] live: {msg}", flush=True)


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "strikeouts-live"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def yesterday_et(now: datetime | None = None) -> str:
    return ((now or datetime.now(ET)) - timedelta(days=1)).strftime("%Y-%m-%d")


def carryover_date(now: datetime | None = None) -> str | None:
    """Yesterday, while it still holds a starter left mid-game.

    poll_once() asks for ONE date and main() asks for today, so a start
    that crosses midnight ET was simply abandoned: at 00:00 the poller
    moved to the new date and never looked back. The archive kept
    `status: in_game` forever, and because the board reads `live.final`
    to decide whether a total can still move, those rows showed a
    pulsing "IN GAME" next to a K count that had been settled for
    hours. Measured on both days the archive has existed: 2026-08-11
    left Nick Martinez (21:40 first pitch) stuck, 2026-08-12 left Eric
    Lauer and George Klassen (both 22:10). No early game was ever hit,
    which is the signature of a midnight cutoff rather than a bad feed.

    Bounded twice so a game that never reaches a terminal state cannot
    pin the poller to the past: it stops as soon as every tracked
    starter is final, and it stops at CARRYOVER_UNTIL_H ET regardless.
    A suspended game resumed days later is the grader's problem — this
    file only ever reports.
    """
    now = now or datetime.now(ET)
    if now.hour >= CARRYOVER_UNTIL_H:
        return None
    iso = yesterday_et(now)
    rows = read_archived_state(iso).get("pitchers") or []
    return iso if any(not r.get("final") for r in rows) else None


def tracked_pitchers(iso_date: str) -> dict:
    """{pitcher_id: {name, line, side, game_pk}} from today's slate."""
    path = SLATES_DIR / f"{iso_date}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for p in payload.get("pitchers", []):
        pid = p.get("pitcher_id")
        if pid is None:
            continue
        # The sidecar stores `line` as whatever the book gave us, which
        # may be a string ("5.5"). Coerce once here so nothing
        # downstream compares an int to a str.
        try:
            line = float(p.get("line"))
        except (TypeError, ValueError):
            line = None
        out[int(pid)] = {
            "pitcher_name": p.get("pitcher_name"),
            "line": line,
            "best_side": p.get("best_side"),
            "expected_k": p.get("expected_k"),
            "expected_bf": p.get("expected_bf"),
            "game_pk": p.get("game_pk"),
        }
    return out


def starter_is_relieved(box: dict, pid: int) -> bool:
    """True when someone has pitched AFTER this pitcher for his team.

    THE single definition of "this starter's line is final", shared with
    tools/grader.py so early grading and live display can never disagree
    about what finished means.

    It is an already-happened fact, not an inference: the boxscore lists
    pitchers in appearance order, so anyone after him means he is out of
    the game and cannot return. Deriving it from innings or pitch count
    would be a guess, and a guess that settles a bet is the failure mode
    this repo keeps paying for.

    False when he has not appeared at all -- "hasn't pitched yet" and
    "pitched and left" must never collapse into one answer, or an opener
    or a late first pitch would settle a bet that has not started.
    """
    line = _starter_line(box, pid)
    return bool(line and line["relieved"])


def _starter_line(box: dict, pid: int) -> dict | None:
    """Live pitching line for one pitcher, plus whether he is finished."""
    for side in ("away", "home"):
        team = box.get("teams", {}).get(side, {})
        order = list(team.get("pitchers") or [])
        if pid not in order:
            continue
        player = team.get("players", {}).get(f"ID{pid}") or {}
        st = (player.get("stats") or {}).get("pitching") or {}
        idx = order.index(pid)
        return {
            "batters_faced": st.get("battersFaced"),
            "strikeouts": st.get("strikeOuts"),
            "pitches": st.get("pitchesThrown"),
            "innings": st.get("inningsPitched"),
            # Unambiguous: someone pitched after him. Not an inference.
            "relieved": idx < len(order) - 1,
        }
    return None


def poll_once(iso: str) -> dict:
    """Poll one date's tracked starters. The date is a parameter, not
    today(), so main() can finish yesterday's late games."""
    tracked = tracked_pitchers(iso)
    sched = _get(f"{API}/schedule?sportId=1&date={iso}")
    games = [g for dt in sched.get("dates", []) for g in dt.get("games", [])]

    by_pk = {int(g["gamePk"]): g for g in games}
    want_pks = {int(v["game_pk"]) for v in tracked.values() if v.get("game_pk")}
    want_pks &= set(by_pk)

    rows, any_live = [], False
    for pk in sorted(want_pks):
        g = by_pk[pk]
        state = g["status"]["detailedState"]
        abstract = g["status"]["abstractGameState"]
        if abstract == "Live":
            any_live = True
        if abstract == "Preview":
            continue
        try:
            box = _get(f"{API}/game/{pk}/boxscore")
        except Exception as exc:
            log(f"boxscore {pk} failed: {type(exc).__name__}: {exc}")
            continue

        for pid, meta in tracked.items():
            if int(meta.get("game_pk") or 0) != pk:
                continue
            line = _starter_line(box, pid)
            if line is None:
                # In a started game and never appeared => scratched.
                # CLAUDE.md grades that VOID; surfacing it early is the
                # point, but this file only reports it.
                rows.append({**meta, "pitcher_id": pid, "game_pk": pk,
                             "game_state": state, "status": "did_not_pitch",
                             "batters_faced": None, "strikeouts": None,
                             "final": abstract == "Final"})
                continue
            done = line["relieved"] or state in TERMINAL
            rows.append({
                **meta, "pitcher_id": pid, "game_pk": pk,
                "game_state": state,
                "status": "final" if done else "in_game",
                "final": done,
                **line,
            })

    return {
        "date": iso,
        "updated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "source": "mlb_stats_api",
        "n_tracked": len(tracked),
        "n_reported": len(rows),
        "n_final": sum(1 for r in rows if r.get("final")),
        "any_live": any_live,
        "pitchers": sorted(rows, key=lambda r: (not r.get("final"),
                                                r.get("pitcher_name") or "")),
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _merge_rows(archived: dict, state: dict) -> dict:
    """Fresh poll wins per pitcher; nobody already recorded is dropped.

    Only ever grow a date's record. Two ways a cycle can report less
    than the whole board: the first poll after midnight reports the NEW
    date with zero pitchers (and on a day with no slate, every poll
    does), and a single failed boxscore fetch `continue`s past that one
    pitcher. Writing either straight through would blank a starter who
    was already final -- the exact disappearing-results failure this
    archive exists to prevent (A-035).

    Rows are keyed by pitcher_id WITHIN one date's file, so this cannot
    attach one night's strikeouts to another night's start.
    """
    if archived.get("date") != state.get("date"):
        return state
    prior = {r.get("pitcher_id"): r for r in archived.get("pitchers") or []}
    if not prior:
        return state
    fresh = {r.get("pitcher_id"): r for r in state.get("pitchers") or []}
    rows = sorted({**prior, **fresh}.values(),
                  key=lambda r: (not r.get("final"),
                                 r.get("pitcher_name") or ""))
    return {**state,
            "pitchers": rows,
            "n_reported": len(rows),
            "n_final": sum(1 for r in rows if r.get("final"))}


def archive_state(state: dict) -> None:
    """Update the per-date archive only, leaving live_state.json alone.

    Split out of write_state so a carryover poll can finish yesterday's
    record without overwriting the single-file view of what is happening
    NOW, which is by definition about today.
    """
    # Archive under the date the payload is ABOUT, not the date it was
    # written, so a poll straddling midnight files itself correctly.
    iso = state.get("date")
    if not iso:
        return
    _write_json_atomic(LIVE_DIR / f"{iso}.json",
                       _merge_rows(read_archived_state(iso), state))


def write_state(state: dict) -> None:
    _write_json_atomic(STATE_PATH, state)
    archive_state(state)


def read_archived_state(iso: str) -> dict:
    try:
        return json.loads((LIVE_DIR / f"{iso}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    once = "--once" in sys.argv
    log(f"starting (poll {POLL_S}s live / {QUIET_S}s quiet) -> {STATE_PATH}")
    # Keyed by (date, pitcher_id): a starter appears on many dates, and
    # now that two dates are in flight at once a bare pitcher_id would
    # swallow tonight's FINAL line because last night's already fired.
    seen_final: set[tuple[str, int]] = set()

    while True:
        try:
            state = poll_once(today_et())
            write_state(state)
            reports = [state]

            # Finish yesterday before starting today. Archive only --
            # live_state.json means "now", and now is today.
            carry = carryover_date()
            if carry:
                carry_state = poll_once(carry)
                archive_state(carry_state)
                reports.append(carry_state)
                log(f"carryover {carry}: {carry_state['n_final']}/"
                    f"{carry_state['n_reported']} final, "
                    f"live={carry_state['any_live']}")

            any_live = any(s["any_live"] for s in reports)
            for st in reports:
                for r in st["pitchers"]:
                    key = (st["date"], r["pitcher_id"])
                    if r.get("final") and key not in seen_final:
                        seen_final.add(key)
                        k, line = r.get("strikeouts"), r.get("line")
                        verdict = ""
                        if k is not None and line is not None:
                            verdict = (f" -> OVER {line} "
                                       f"{'hits' if k > line else 'misses'}")
                        log(f"FINAL {r['pitcher_name']}: {k} K in "
                            f"{r.get('batters_faced')} BF{verdict}")
            log(f"{state['n_reported']} tracked, {state['n_final']} final, "
                f"live={any_live}")
            if once:
                return 0
            # Yesterday's late game is live even when today has not
            # started, so the fast interval has to consider both.
            time.sleep(POLL_S if any_live else QUIET_S)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log(f"cycle error ({type(exc).__name__}: {exc})")
            if once:
                return 1
            time.sleep(QUIET_S)


if __name__ == "__main__":
    sys.exit(main())
