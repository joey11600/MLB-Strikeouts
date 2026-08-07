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

Env:
    LIVE_POLL_S        seconds between polls while games are live (30)
    LIVE_QUIET_S       seconds between polls otherwise (600)
    LIVE_STATE_PATH    output file (defaults under DATA_STATE_DIR)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tracker import DATA_STATE_DIR  # noqa: E402

ET = ZoneInfo("America/New_York")
API = "https://statsapi.mlb.com/api/v1"

POLL_S = int(os.environ.get("LIVE_POLL_S", "30"))
QUIET_S = int(os.environ.get("LIVE_QUIET_S", "600"))
STATE_PATH = Path(os.environ.get("LIVE_STATE_PATH")
                  or (DATA_STATE_DIR / "live_state.json"))
SLATES_DIR = DATA_STATE_DIR / "slates"

TERMINAL = {"Final", "Game Over", "Completed Early"}


def log(msg: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}] live: {msg}", flush=True)


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "strikeouts-live"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


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


def poll_once() -> dict:
    iso = today_et()
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


def write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(STATE_PATH))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    once = "--once" in sys.argv
    log(f"starting (poll {POLL_S}s live / {QUIET_S}s quiet) -> {STATE_PATH}")
    seen_final: set[int] = set()

    while True:
        try:
            state = poll_once()
            write_state(state)
            for r in state["pitchers"]:
                pid = r["pitcher_id"]
                if r.get("final") and pid not in seen_final:
                    seen_final.add(pid)
                    k, line = r.get("strikeouts"), r.get("line")
                    verdict = ""
                    if k is not None and line is not None:
                        verdict = (f" -> OVER {line} "
                                   f"{'hits' if k > line else 'misses'}")
                    log(f"FINAL {r['pitcher_name']}: {k} K in "
                        f"{r.get('batters_faced')} BF{verdict}")
            log(f"{state['n_reported']} tracked, {state['n_final']} final, "
                f"live={state['any_live']}")
            if once:
                return 0
            time.sleep(POLL_S if state["any_live"] else QUIET_S)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log(f"cycle error ({type(exc).__name__}: {exc})")
            if once:
                return 1
            time.sleep(QUIET_S)


if __name__ == "__main__":
    sys.exit(main())
