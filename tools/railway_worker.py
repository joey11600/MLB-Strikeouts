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
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

ET = ZoneInfo("America/New_York")
PYTHON = sys.executable

CACHE_DIR = Path(os.environ.get("STATCAST_CACHE_DIR", REPO / "data" / "statcast_cache"))
STATE_DIR = Path(os.environ.get("WORKER_STATE_DIR", CACHE_DIR.parent))
STATE_PATH = STATE_DIR / "worker_state.json"

# Mutable state that MUST survive redeploys (the image is rebuilt from
# git, so anything written into /app is lost). Each entry is symlinked
# out to the volume on boot, seeded from the image the first time.
PERSISTED = [
    "picks_2026.csv",
    "pick_changes.csv",
    "model_log.csv",
    "slates",
    "odds",
]
VOLUME_STATE = STATE_DIR / "state"

PORT = int(os.environ.get("PORT", "8080"))
DASHBOARD_JSON = REPO / "dashboard" / "public" / "data.json"

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


def seed_volume_state() -> None:
    """Copy any state the image has that the volume is missing.

    The pipeline reads/writes DATA_STATE_DIR (set to the volume), so no
    symlinks are involved — deliberately. The atomic-write pattern
    (tempfile + os.replace) REPLACES the destination path, which
    silently destroys a symlinked file and drops the write onto
    ephemeral container disk. That bug cost a graded ledger once.

    Volume copies always win; the image only fills gaps (a fresh
    volume, or snapshots committed after the volume was created).
    """
    # FORCE_SEED=a,b or "all" overwrites volume copies from the image.
    # Escape hatch for when the volume has drifted from the repo (e.g.
    # writes that never landed) — set it, deploy, verify, then remove.
    raw = os.environ.get("FORCE_SEED", "").strip()
    force = set(PERSISTED) if raw.lower() == "all" else {
        n.strip() for n in raw.split(",") if n.strip()
    }
    if force:
        log(f"FORCE_SEED active — overwriting from image: {sorted(force)}")

    VOLUME_STATE.mkdir(parents=True, exist_ok=True)
    for name in PERSISTED:
        src_path = REPO / "data" / name
        vol_path = VOLUME_STATE / name
        forced = name in force

        if not src_path.exists():
            continue

        if src_path.is_dir():
            vol_path.mkdir(parents=True, exist_ok=True)
            added = 0
            for src in src_path.rglob("*"):
                if not src.is_file():
                    continue
                dest = vol_path / src.relative_to(src_path)
                if forced or not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    added += 1
            if added:
                log(f"seeded {added} file(s) into volume state: {name}")
        elif forced or not vol_path.exists():
            shutil.copy2(src_path, vol_path)
            log(f"seeded volume state: {name}")

    log(f"volume state ready at {VOLUME_STATE}")


class DataHandler(BaseHTTPRequestHandler):
    """Serves the dashboard payload straight off the volume.

    This is what removes the need for any push credential: the site
    fetches live data from here instead of waiting for a rebuild, so
    picks appear the moment the pipeline writes them.
    """

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The dashboard is a public page on another origin.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/data.json"):
            try:
                self._send(200, DASHBOARD_JSON.read_bytes(), "application/json")
            except Exception as exc:
                self._send(503, json.dumps({"error": str(exc)}).encode(),
                           "application/json")
            return
        if self.path.startswith("/health"):
            payload = {
                "ok": True,
                "now_et": datetime.now(ET).isoformat(),
                "jobs_run_today": _load_state(),
                "cache_months": sorted(p.name for p in CACHE_DIR.glob("*")) if CACHE_DIR.exists() else [],
                "data_json_present": DASHBOARD_JSON.exists(),
            }
            self._send(200, json.dumps(payload, indent=1).encode(), "application/json")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def log_message(self, *args):  # keep request noise out of the job log
        return


def start_http_server() -> None:
    def serve():
        HTTPServer(("0.0.0.0", PORT), DataHandler).serve_forever()
    threading.Thread(target=serve, daemon=True).start()
    log(f"serving /data.json and /health on :{PORT}")


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
    """Pull before work so the container matches the repo's ledger.

    No token gate: the repo is public, so an anonymous pull works. The
    old GITHUB_TOKEN check meant the container silently never pulled,
    which is how it ended up running against whatever ledger happened to
    be baked into the image. A pull failure is logged and tolerated --
    working from a slightly old checkout beats refusing to run at all.
    """
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
    """Optional git mirror of the ledger.

    The volume is the live source of truth and the dashboard reads the
    HTTP endpoint, so this is pure backup — it no-ops without a token.
    """
    if not os.environ.get("GITHUB_TOKEN"):
        log("git mirror skipped (no GITHUB_TOKEN) — volume holds the ledger, "
            "dashboard reads it live over HTTP")
        return
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
    # Grade BOTH dates explicitly rather than relying on run.py's
    # "yesterday" default: at 3am ET yesterday is the finished slate,
    # but a forced/late run at 10pm needs today. Grading is idempotent
    # (graded rows are locked, non-final games are skipped), so doing
    # both is always safe and makes the job time-of-day robust.
    today = datetime.now(ET).date()
    yesterday = today - timedelta(days=1)
    _run("grade-yesterday",
         [PYTHON, "run.py", "grade", yesterday.isoformat()], 1200)
    _run("grade-today",
         [PYTHON, "run.py", "grade", today.isoformat()], 1200)
    # Log prediction-vs-outcome for EVERY evaluated pitcher, not just the
    # bets: ~28 observations a night instead of ~3, and it measures the
    # model rather than the threshold-filtered bet sample.
    _run("model-log", [PYTHON, "tools/model_log.py"], 900)
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
    seed_volume_state()
    start_http_server()
    configure_git()

    if not any(CACHE_DIR.glob("*/*")):
        refresh_cache()

    # data.json is a DERIVED artifact and ships inside the image, so a
    # fresh deploy would otherwise serve a snapshot older than the
    # volume's ledger. Always rebuild it from volume state on boot.
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)

    # Operational escape hatch: set RUN_TASK_ON_BOOT=<task> and redeploy
    # to force one run immediately (no waiting for the window, no public
    # trigger endpoint). Remove the variable afterwards or it fires on
    # every restart.
    boot_task = os.environ.get("RUN_TASK_ON_BOOT", "").strip()
    if boot_task in TASKS:
        log(f"--- RUN_TASK_ON_BOOT={boot_task} — running now ---")
        try:
            TASKS[boot_task]()
        except Exception as exc:
            log(f"BOOT TASK ERROR {boot_task}: {exc}")
        log(f"--- boot task {boot_task} finished ---")
    elif boot_task:
        log(f"RUN_TASK_ON_BOOT={boot_task!r} is not a known task {list(TASKS)}")

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
