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
import csv
import json
import urllib.request
import os
import shutil
import subprocess
import sys
import tempfile
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
        if self.path.startswith("/live.json"):
            try:
                self._send(200, LIVE_STATE.read_bytes(), "application/json")
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
                "last_reconcile": LAST_RECONCILE,
                "can_push_to_git": bool(os.environ.get("GITHUB_TOKEN")),
                "dispatch_credentials": DISPATCH_CREDS,
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
    """Pull, then merge the pulled ledger into the volume the jobs use.

    No token gate on the pull: the repo is public, so an anonymous pull
    works. The old GITHUB_TOKEN check meant the container silently never
    pulled, which is how it ended up running against whatever ledger
    happened to be baked into the image. A pull failure is logged and
    tolerated -- working from a slightly old checkout beats refusing to
    run at all.

    The reconcile is the load-bearing half. The jobs read and write
    DATA_STATE_DIR (the volume); `git pull` only touches the /app
    checkout. Without a merge those are two independent ledgers: the PC
    writes picks to git, the container grades a volume copy that never
    sees them, and its /data.json -- which the dashboard PREFERS over
    the bundled copy -- silently reports a record missing the picks.
    """
    _run("git-pull", ["git", "pull", "--rebase", "--autostash",
                      "origin", "master"], 180)
    reconcile_ledger()


TERMINAL_GRADES = {"WIN", "LOSS", "VOID", "PUSH", "POSTPONED"}

# Outcome of the most recent reconcile, surfaced on /health so the merge
# can be checked without deploy-log access. "never run" is a distinct
# state from "ran and changed nothing" on purpose.
LAST_RECONCILE: dict = {"ok": None, "at": None, "error": None,
                        "picks": None, "graded": None}

# Natural keys for the mergeable CSVs. picks_2026.csv uses the same key
# daily_pipeline._load_existing_picks does -- verified unique across the
# ledger, ladder rungs included (a rung's `line` is "6+", not "6.5").
_MERGE_KEYS = {
    "picks_2026.csv": ("date", "game_pk", "pitcher_id", "line"),
    "model_log.csv": ("date", "pitcher_id"),
}


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        return list(r), list(r.fieldnames or [])


def _write_csv_atomic(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _better_row(a: dict, b: dict) -> dict:
    """Pick the more advanced of two versions of the same ledger row.

    A graded row always beats an ungraded one -- grading is the strictly
    later state and re-opening it would violate the locked-picks rule.
    Then the later updated_at. Then, on a genuine tie, the row with more
    populated fields: when both sides were written at the same instant,
    the one carrying more information (e.g. an odds_source the other
    lacks) is the one worth keeping. Never returns None: losing a row is
    the one outcome that is never acceptable.
    """
    a_final = (a.get("graded_result") or "").strip().upper() in TERMINAL_GRADES
    b_final = (b.get("graded_result") or "").strip().upper() in TERMINAL_GRADES
    if a_final != b_final:
        return a if a_final else b

    a_at, b_at = (a.get("updated_at") or ""), (b.get("updated_at") or "")
    if a_at != b_at:
        return a if a_at > b_at else b

    a_filled = sum(1 for v in a.values() if (v or "").strip())
    b_filled = sum(1 for v in b.values() if (v or "").strip())
    return a if a_filled >= b_filled else b


def _merge_csv(name: str, key_fields: tuple[str, ...]) -> None:
    """Union the repo's copy of a ledger into the volume's copy.

    Union, never replace. Rows are only ever added or advanced to a more
    complete version of themselves -- the append-mostly rule holds
    across machines, not just within one.
    """
    repo_path = REPO / "data" / name
    vol_path = VOLUME_STATE / name
    repo_rows, repo_fields = _read_csv(repo_path)
    vol_rows, vol_fields = _read_csv(vol_path)

    if not repo_rows:
        return
    if not vol_rows:
        shutil.copy2(repo_path, vol_path)
        log(f"reconcile {name}: volume was empty, took {len(repo_rows)} row(s) from repo")
        return

    # Prefer whichever header is a superset, so a column added on one
    # side (e.g. odds_source) is not dropped by the merge.
    fields = vol_fields if set(vol_fields) >= set(repo_fields) else repo_fields
    for extra in repo_fields + vol_fields:
        if extra not in fields:
            fields.append(extra)

    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for row in vol_rows + repo_rows:
        k = tuple((row.get(f) or "") for f in key_fields)
        if k in merged:
            merged[k] = _better_row(merged[k], row)
        else:
            merged[k] = row
            order.append(k)

    out = [merged[k] for k in order]
    added = len(merged) - len(vol_rows)

    # Compare CONTENT, not row count. Gating the write on "did the row
    # count change" silently dropped every repo-side update that did not
    # also add a row -- i.e. exactly the overnight-grading case, where
    # the repo carries a grade for a row the volume already has.
    def _shape(rows):
        return [tuple((r.get(f) or "") for f in fields) for r in rows]

    if _shape(out) != _shape(vol_rows):
        _write_csv_atomic(vol_path, out, fields)
        changed = sum(1 for x, y in zip(_shape(out), _shape(vol_rows)) if x != y)
        log(f"reconcile {name}: {len(vol_rows)} volume + {len(repo_rows)} repo "
            f"-> {len(merged)} rows ({added:+d} new, {changed} updated)")


def _merge_journal(name: str) -> None:
    """Union an append-only journal by whole-row identity."""
    repo_path = REPO / "data" / name
    vol_path = VOLUME_STATE / name
    repo_rows, repo_fields = _read_csv(repo_path)
    vol_rows, vol_fields = _read_csv(vol_path)
    if not repo_rows:
        return
    if not vol_rows:
        shutil.copy2(repo_path, vol_path)
        return
    fields = vol_fields or repo_fields
    seen = {tuple(sorted(r.items())) for r in vol_rows}
    fresh = [r for r in repo_rows if tuple(sorted(r.items())) not in seen]
    if fresh:
        _write_csv_atomic(vol_path, vol_rows + fresh, fields)
        log(f"reconcile {name}: +{len(fresh)} journal row(s) from repo")


def _stamp_of(path: Path, field: str) -> str:
    """Newest value of a timestamp column in a CSV, or '' if absent."""
    try:
        rows, _ = _read_csv(path)
        return max(((r.get(field) or "") for r in rows), default="")
    except Exception:
        return ""


def _merge_dir(name: str) -> None:
    """Copy repo files the volume lacks, or that are demonstrably newer.

    "Newer" is read from INSIDE the file (slate sidecars carry
    generated_at, odds CSVs carry captured_at) -- never from mtime,
    which git checkout resets to build time on every deploy.
    """
    repo_dir = REPO / "data" / name
    vol_dir = VOLUME_STATE / name
    if not repo_dir.is_dir():
        return
    vol_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in repo_dir.rglob("*"):
        if not src.is_file():
            continue
        dest = vol_dir / src.relative_to(repo_dir)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
            continue
        if src.suffix == ".json":
            try:
                a = json.loads(src.read_text(encoding="utf-8")).get("generated_at") or ""
                b = json.loads(dest.read_text(encoding="utf-8")).get("generated_at") or ""
            except Exception:
                continue
        elif src.suffix == ".csv":
            a, b = _stamp_of(src, "captured_at"), _stamp_of(dest, "captured_at")
        else:
            continue
        if a and a > b:
            shutil.copy2(src, dest)
            copied += 1
    if copied:
        log(f"reconcile {name}: {copied} file(s) refreshed from repo")


def reconcile_ledger() -> None:
    """Merge the git checkout's data into the volume the jobs read.

    Runs after every pull. Idempotent, union-only, and it never deletes
    or downgrades a row -- so running it twice, or against an identical
    repo, is a no-op.

    Always logs a one-line summary, including when nothing changed. The
    bug this function exists to fix was a mechanism that silently did
    nothing, so silence is exactly the wrong success signal: "reconcile
    ok, no changes" and no line at all must not look identical in the
    job log.
    """
    # On a CI runner the checkout IS the ledger (DATA_STATE_DIR=data),
    # so there are not two copies to merge and VOLUME_STATE does not
    # exist. Merging would be a no-op at best; as written it raised
    # FileNotFoundError and logged a scary warning on every run.
    try:
        if VOLUME_STATE.resolve() == (REPO / "data").resolve():
            LAST_RECONCILE.update(
                ok=True, at=datetime.now(ET).isoformat(timespec="seconds"),
                error=None, picks=None, graded=None,
            )
            log("reconcile skipped — checkout is the ledger (no volume to merge)")
            return
    except OSError:
        pass

    try:
        for name, key in _MERGE_KEYS.items():
            _merge_csv(name, key)
        _merge_journal("pick_changes.csv")
        for name in ("slates", "odds"):
            _merge_dir(name)
    except Exception as exc:
        # A reconcile failure must not take the slate down. The job can
        # still run against the volume copy; it is just possibly behind.
        LAST_RECONCILE.update(
            ok=False, at=datetime.now(ET).isoformat(timespec="seconds"),
            error=f"{type(exc).__name__}: {exc}",
        )
        log(f"WARNING reconcile failed ({type(exc).__name__}: {exc}) — "
            f"jobs continue against the volume copy, which may be stale")
        return

    picks = VOLUME_STATE / "picks_2026.csv"
    rows, _ = _read_csv(picks)
    graded = sum(1 for r in rows
                 if (r.get("graded_result") or "").strip().upper() in TERMINAL_GRADES)
    # Also published on /health. The log is not enough on its own: log
    # access can lapse (an expired deploy credential is all it takes),
    # and a caught-and-logged failure would then be indistinguishable
    # from a clean run. A mechanism guarding the money ledger has to be
    # checkable from outside the box.
    LAST_RECONCILE.update(
        ok=True, at=datetime.now(ET).isoformat(timespec="seconds"),
        error=None, picks=len(rows), graded=graded,
    )
    log(f"reconcile ok — volume ledger: {len(rows)} pick(s), {graded} graded")


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
    # Shadow portfolio: what the model WOULD have bet at other trust
    # weights. Printed nightly so the evidence for the A-006 decision
    # accumulates visibly instead of needing someone to remember to
    # look. Read-only over model_log.csv; it writes nothing.
    _run("shadow", [PYTHON, "tools/shadow.py"], 300)
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
    commit_and_push("overnight grading")
    # After the commit, so a failing invariant is loud without costing
    # us the night's ledger.
    _run("watchdog", [PYTHON, "tools/watchdog.py"], 300)


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
    # A redeploy is exactly when the checkout changes, so reconcile here
    # too and not only at task time. Without it, a deploy that carries
    # new picks leaves them invisible to the volume ledger until the
    # next scheduled job -- and the boot rebuild of data.json below
    # would publish the pre-merge numbers in the meantime.
    reconcile_ledger()
    verify_dispatch_credentials()
    start_http_server()
    start_live_watcher()
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
        # Same dispatch-first path the scheduler uses, so this escape
        # hatch also serves as an end-to-end proof that Railway can
        # drive GitHub Actions.
        if dispatch_github(boot_task):
            log(f"--- {boot_task} dispatched to GitHub Actions ---")
            boot_task = ""
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
                # Prefer GitHub Actions: it can reach DraftKings, this
                # container cannot. This loop is the reliable clock --
                # GitHub's own cron is best-effort and had not fired once
                # in the 6.5 hours after the workflow was created, while
                # every one of these six windows hit on time. Falls back
                # to running here when there is no token, which still
                # grades, logs and rebuilds the dashboard.
                if not dispatch_github(name):
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


DISPATCH_CREDS: dict = {"checked": None, "ok": None, "detail": None,
                        "token_days_left": None}


LIVE_STATE = VOLUME_STATE / "live_state.json"


def start_live_watcher() -> None:
    """Poll MLB for starter lines and rebuild the board when one ends.

    A starter's K total is settled the moment he is pulled -- often
    hours before the game ends and many hours before Statcast
    publishes. The 03:00 grading pass meant a pick decided at 7:20pm sat
    unresolved overnight. MLB's API knows immediately and does not block
    datacenter IPs, so this is the one job this container is better
    placed to do than GitHub Actions.

    Rebuilds data.json only when a starter newly finishes, so the served
    board reflects results within a poll interval without spinning on
    every tick. Read-only with respect to the ledger: Statcast stays the
    graded source of truth and tools/watchdog.py compares the two.
    """
    def loop():
        from workers import live_strikeouts as lw
        lw.STATE_PATH = LIVE_STATE
        lw.SLATES_DIR = VOLUME_STATE / "slates"
        seen: set[int] = set()
        while True:
            try:
                state = lw.poll_once()
                lw.write_state(state)
                fresh = {r["pitcher_id"] for r in state["pitchers"]
                         if r.get("final")} - seen
                if fresh:
                    seen |= fresh
                    names = [r["pitcher_name"] for r in state["pitchers"]
                             if r["pitcher_id"] in fresh]
                    log(f"live: {len(fresh)} starter(s) finished "
                        f"({', '.join(n for n in names if n)}) — grading")
                    # Their totals can no longer change, so grade now
                    # rather than at 03:00. run.py grade is idempotent
                    # and honours the locked-pick rules; Statcast
                    # reconciles overnight via tools/watchdog.py.
                    _run("grade-live", [PYTHON, "run.py", "grade",
                                        state["date"]], 900)
                    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
                time.sleep(30 if state.get("any_live") else 600)
            except Exception as exc:
                log(f"live watcher error ({type(exc).__name__}: {exc})")
                time.sleep(600)

    threading.Thread(target=loop, daemon=True, name="live-watcher").start()
    log("live starter watcher started")


def verify_dispatch_credentials() -> None:
    """Prove at boot that the token can actually reach the workflow.

    Without this, a missing scope or an expired token is discovered at
    03:00 when the night job silently fails to dispatch and the day's
    evidence never gets collected. Tokens expire on a date nobody
    remembers; this turns that into a visible state on /health instead
    of a quiet stop.

    Read-only: a GET against the workflow. It confirms the token is
    valid and carries Actions access without triggering a run.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        DISPATCH_CREDS.update(
            checked=datetime.now(ET).isoformat(timespec="seconds"), ok=False,
            detail="GITHUB_TOKEN not set — Railway cannot dispatch; tasks run "
                   "locally here and cannot fetch DraftKings odds")
        log("dispatch credentials: NO TOKEN — GitHub cron is the only scheduler")
        return

    url = (f"https://api.github.com/repos/{GITHUB_REPO}"
           f"/actions/workflows/daily.yml")
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "strikeouts-worker")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
            # GitHub returns the PAT's expiry on every authenticated
            # response. Capturing it turns "the token died and nothing
            # ran" into a countdown you can see coming.
            expires_raw = resp.headers.get(
                "github-authentication-token-expiration") or ""
        state = payload.get("state")
        ok = resp.status == 200 and state == "active"

        days_left = None
        if expires_raw:
            try:
                stamp = expires_raw.strip().replace(" UTC", "+0000")
                exp = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S%z")
                days_left = (exp - datetime.now(exp.tzinfo)).days
            except ValueError:
                pass

        detail = f"workflow state={state}"
        if days_left is not None:
            detail += f", token expires in {days_left}d ({expires_raw.strip()})"
            if days_left <= 7:
                ok = False
                detail += " — RENEW NOW, dispatch stops silently when it lapses"

        DISPATCH_CREDS.update(
            checked=datetime.now(ET).isoformat(timespec="seconds"), ok=ok,
            detail=detail, token_days_left=days_left)
        log(f"dispatch credentials: {'OK' if ok else 'PROBLEM'} ({detail})")
    except Exception as exc:
        DISPATCH_CREDS.update(
            checked=datetime.now(ET).isoformat(timespec="seconds"), ok=False,
            detail=f"{type(exc).__name__}: {exc}")
        log(f"dispatch credentials: FAILED ({type(exc).__name__}: {exc}) — "
            f"token may be expired or missing Actions access")


def dispatch_github(task: str) -> bool:
    """Ask GitHub Actions to run a task, and report whether it accepted.

    Division of labour: Railway is a resident process whose ET scheduler
    fires reliably (all six windows hit on 2026-08-06), but DraftKings
    403s its datacenter IP. GitHub Actions can reach DraftKings but its
    cron is best-effort and had not fired once in the 6.5 hours after
    the workflow was created. Neither is sufficient alone; together
    Railway is the clock and Actions are the hands.

    Needs GITHUB_TOKEN (repo scope). Without it this is a no-op and the
    resident loop runs the task locally as before -- which still grades,
    logs and rebuilds the dashboard, just without fresh odds.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return False
    url = (f"https://api.github.com/repos/{GITHUB_REPO}"
           f"/actions/workflows/daily.yml/dispatches")
    body = json.dumps({"ref": "master", "inputs": {"task": task}}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "strikeouts-worker")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = 200 <= resp.status < 300
        log(f"dispatched '{task}' to GitHub Actions -> {'accepted' if ok else 'rejected'}")
        return ok
    except Exception as exc:
        log(f"GitHub dispatch for '{task}' failed ({type(exc).__name__}: {exc}) "
            f"-- falling back to running it here")
        return False


def run_due_once() -> int:
    """Run whatever task is due right now, then exit. For CI schedulers.

    GitHub Actions cron is UTC-only, so a workflow with ET-anchored cron
    lines silently shifts by an hour at every DST boundary. Instead the
    workflow fires hourly and calls this, which reuses the SAME schedule
    table, grace windows, and once-per-day state as the resident loop —
    so the two schedulers cannot drift apart, and DST is handled by
    construction rather than by remembering to edit cron lines twice a
    year.

    Exits 0 when nothing is due: an idle tick is a success, not a
    failure, and a red X on an empty run trains the operator to ignore
    the whole workflow.
    """
    sync_repo()
    now = datetime.now(ET)
    today = now.date().isoformat()
    state = _load_state()
    ran = []

    for name, at, grace in SCHEDULE:
        key = f"{name}@{at.strftime('%H%M')}"
        if state.get(key) == today:
            continue
        due = datetime.combine(now.date(), at, tzinfo=ET)
        if now < due:
            continue
        late = (now - due).total_seconds() / 60
        if late > grace:
            state[key] = today
            _save_state(state)
            log(f"SKIP {key}: {late:.0f} min late (grace {grace})")
            continue

        log(f"--- running {key} ({late:.0f} min after due) ---")
        try:
            TASKS[name]()
            ran.append(key)
        except Exception as exc:
            log(f"TASK ERROR {key}: {exc}")
        state[key] = today
        _save_state(state)
        log(f"--- finished {key} ---")

    log(f"due-run complete: {ran or 'nothing was due'}")
    return 0


def run_named_task(name: str) -> int:
    """Run one task by name and exit. For manual CI dispatch."""
    if name not in TASKS:
        log(f"unknown task {name!r}; known: {list(TASKS)}")
        return 2
    log(f"--- forced run: {name} ---")
    TASKS[name]()
    log(f"--- finished {name} ---")
    return 0


if __name__ == "__main__":
    if "--due" in sys.argv:
        sys.exit(run_due_once())
    if "--task" in sys.argv:
        sys.exit(run_named_task(sys.argv[sys.argv.index("--task") + 1]))
    main()
