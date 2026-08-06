"""Railway worker — runs the daily pipeline on an ET-aware schedule.

Why a long-running worker instead of cron:

  1. **Exact timing.** Closing-odds snapshots are unrecoverable once a
     game starts. GitHub Actions' `schedule` trigger is best-effort and
     routinely fires 1-3 hours late (see the NRFI repo's daily.yml for
     the observed cases). A resident scheduler fires on the minute.
  2. **DST-agnostic by construction.** Times are declared in
     America/New_York and compared against `datetime.now(ET)`, so the
     schedule follows DST automatically — no UTC cron shuffling twice
     a year, no hourly-shotgun workaround.
  3. **Persistent cache.** The Statcast cache lives on a Railway volume
     (STATCAST_CACHE_DIR), so features are computed from a warm ~350MB
     dataset instead of re-downloading on every run.

State (which jobs already ran today) is kept on the volume, so a
restart mid-day resumes rather than re-running or skipping.

Environment:
    STATCAST_CACHE_DIR   volume path for the Statcast cache
    GITHUB_TOKEN         push the ledger back to the repo (required)
    GITHUB_REPO          owner/name, default joey11600/MLB-Strikeouts
    VERCEL_DEPLOY_HOOK   POSTed after a data change to rebuild the site
    WORKER_STATE_DIR     defaults to the volume root
"""
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

ET = ZoneInfo("America/New_York")
PYTHON = sys.executable

CACHE_DIR = Path(os.environ.get("STATCAST_CACHE_DIR", REPO / "data" / "statcast_cache"))
STATE_DIR = Path(os.environ.get("WORKER_STATE_DIR", CACHE_DIR.parent))
STATE_PATH = STATE_DIR / "worker_state.json"

GITHUB_REPO = os.environ.get("GITHUB_REPO", "joey11600/MLB-Strikeouts")
SEASON_START = date(2026, 3, 26)

# (task, ET time, max minutes late it's still worth running).
# A missed close snapshot is worthless once games start, so its window
# is short; a missed grade or slate is still worth producing.
SCHEDULE = [
    ("night",   dtime(3, 0),   360),
    ("morning", dtime(10, 30), 360),
    ("close",   dtime(12, 15), 45),
    ("close",   dtime(15, 0),  45),
    ("lineups", dtime(16, 45), 120),
    ("close",   dtime(18, 15), 45),
]

POLL_SECONDS = 30


def log(msg: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


def _run(label: str, cmd: list[str], timeout: int) -> bool:
    log(f"START {label}")
    try:
        r = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log(f"FAILED {label}: timed out after {timeout}s")
        return False
    tail = "\n".join((r.stdout or "").strip().splitlines()[-15:])
    if tail:
        log(f"{label} output:\n{tail}")
    if r.returncode != 0:
        err = "\n".join((r.stderr or "").strip().splitlines()[-10:])
        log(f"FAILED {label}: exit {r.returncode}\n{err}")
        return False
    log(f"OK {label}")
    return True


def configure_git() -> None:
    """Point the repo at an authenticated remote so the ledger can push."""
    subprocess.run(["git", "config", "user.email", "worker@mlb-strikeouts"],
                   cwd=REPO, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Strikeouts Worker"],
                   cwd=REPO, capture_output=True)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log("WARNING: GITHUB_TOKEN unset — ledger changes will stay on the "
            "volume and will NOT reach GitHub or the dashboard.")
        return
    remote = f"https://x-access-token:{token}@github.com/{GITHUB_REPO}.git"
    subprocess.run(["git", "remote", "set-url", "origin", remote],
                   cwd=REPO, capture_output=True)
    log(f"git remote configured for {GITHUB_REPO}")


def sync_repo() -> None:
    """Pull before work so the container matches the repo's ledger."""
    if not os.environ.get("GITHUB_TOKEN"):
        return
    _run("git-pull", ["git", "pull", "--rebase", "--autostash",
                      "origin", "master"], 180)


def refresh_cache() -> None:
    """Keep the Statcast cache current.

    Seeds the season on an empty volume, then tops up the last few days
    every night. Bullpen fatigue reads YESTERDAY's relief usage, so a
    stale cache silently degrades the leash inputs.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ET).date()
    has_data = any(CACHE_DIR.glob("*/*"))
    start = SEASON_START if not has_data else today - timedelta(days=4)
    if not has_data:
        log(f"cold volume — seeding Statcast cache from {SEASON_START}")
    _run(
        "statcast-backfill",
        [PYTHON, "data/backfill_statcast.py",
         "--start", start.isoformat(), "--end", today.isoformat()],
        timeout=7200 if not has_data else 1800,
    )


def deploy_dashboard() -> None:
    hook = os.environ.get("VERCEL_DEPLOY_HOOK")
    if not hook:
        log("no VERCEL_DEPLOY_HOOK set — relying on the GitHub push to "
            "trigger Vercel's own build")
        return
    try:
        import requests
        r = requests.post(hook, timeout=30)
        log(f"vercel deploy hook -> HTTP {r.status_code}")
    except Exception as exc:
        log(f"FAILED vercel deploy hook: {exc}")


def commit_and_push(context: str) -> None:
    subprocess.run(
        ["git", "add", "data/picks_2026.csv", "data/slates",
         "data/pick_changes.csv", "data/odds", "dashboard/public/data.json"],
        cwd=REPO, capture_output=True,
    )
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if staged.returncode == 0:
        log("git: nothing to commit")
        return
    stamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    if not _run("git-commit",
                ["git", "commit", "-m", f"chore(worker): {context} {stamp} ET"], 60):
        return
    if os.environ.get("GITHUB_TOKEN"):
        if _run("git-push", ["git", "push", "origin", "master"], 240):
            deploy_dashboard()
    else:
        log("committed locally only (no GITHUB_TOKEN)")


def task_morning() -> None:
    sync_repo()
    _run("daily-cycle", [PYTHON, "run.py"], 2400)
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
    commit_and_push("morning slate")


def task_lineups() -> None:
    sync_repo()
    _run("lineup-lock-predict", [PYTHON, "run.py", "predict"], 2400)
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
    commit_and_push("lineup-lock re-run")


def task_close() -> None:
    _run("closing-odds", [PYTHON, "run.py", "close"], 600)
    commit_and_push("closing-odds snapshot")


def task_night() -> None:
    sync_repo()
    refresh_cache()
    _run("grade", [PYTHON, "run.py", "grade"], 1200)
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
    commit_and_push("overnight grading")


TASKS = {
    "morning": task_morning,
    "lineups": task_lineups,
    "close": task_close,
    "night": task_night,
}


def main() -> None:
    log("=== Strikeouts Railway worker starting ===")
    log(f"cache: {CACHE_DIR}  state: {STATE_PATH}")
    configure_git()

    if not any(CACHE_DIR.glob("*/*")):
        refresh_cache()

    log("schedule (ET): " + ", ".join(
        f"{t.strftime('%H:%M')} {name}" for name, t, _ in SCHEDULE))

    while True:
        try:
            now = datetime.now(ET)
            today = now.date().isoformat()
            state = _load_state()

            for name, at, grace in SCHEDULE:
                key = f"{name}@{at.strftime('%H%M')}"
                if state.get(key) == today:
                    continue
                due = datetime.combine(now.date(), at, tzinfo=ET)
                if now < due:
                    continue
                late = (now - due).total_seconds() / 60
                if late > grace:
                    # Window closed (e.g. worker was down) — record it so
                    # we don't fire a useless job, and say so plainly.
                    state[key] = today
                    _save_state(state)
                    log(f"SKIP {key}: {late:.0f} min late (grace {grace})")
                    continue

                log(f"--- running {key} ({late:.0f} min after due) ---")
                try:
                    TASKS[name]()
                except Exception as exc:
                    log(f"TASK ERROR {key}: {exc}")
                state[key] = today
                _save_state(state)
                log(f"--- finished {key} ---")

        except Exception as exc:
            log(f"LOOP ERROR: {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
