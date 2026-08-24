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
import errno
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
    # 09:00 at the operator's request — earlier board, more time to act.
    # Note this is BEFORE Statcast reliably publishes yesterday's games
    # (measured 0 pitches at 03:21 ET, 3,530 by 08:59 ET), which is why
    # evidence logging is no longer tied to this job: _log_evidence()
    # runs on EVERY task instead. See A-022.
    ("morning", dtime(9, 0),   360),
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
    except OSError as exc:
        # fork() failing with EAGAIN means the container is out of
        # process slots. Nothing later in this process can spawn
        # either, and leaked slots never come back (A-045) — exit
        # non-zero and let Railway's ON_FAILURE policy start a clean
        # container. _restart_if_leaking() should fire long before
        # this; this is the endgame backstop.
        if exc.errno == errno.EAGAIN:
            log(f"FATAL {label}: cannot fork ({exc}) — process slots "
                f"exhausted; exiting so Railway restarts the container")
            os._exit(1)
        log(f"FAILED {label}: {type(exc).__name__}: {exc}")
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


# A-045: the fork ceiling. The container leaked one process slot per
# publish pass (python ran as PID 1 and reaped no orphans, so each one
# stayed a zombie), crossed the ceiling after ~44 h — first observed
# failure was pass ~530 — and then served a frozen board for two days
# while /health kept answering. 400 leaves room to log and restart
# while forks still work; a healthy container sits under ~30.
PID_PRESSURE_EXIT = 400
THREAD_PRESSURE_EXIT = 200


def _process_pressure() -> dict:
    """How close the container is to its fork ceiling, for /health.

    `/proc` lists every process in the container's namespace INCLUDING
    zombies — which is exactly what an unreaped-orphan leak produces,
    and why this counts processes rather than asking psutil for "real"
    ones. Threads live under /proc/self/task and count against the
    same ceiling, so they are gauged separately. Both stay None
    off-Linux (local dev).
    """
    pids = threads = None
    try:
        pids = sum(1 for d in os.listdir("/proc") if d.isdigit())
        threads = len(os.listdir("/proc/self/task"))
    except OSError:
        pass
    return {"container_pids": pids, "worker_threads": threads,
            "restart_over": PID_PRESSURE_EXIT}


def _restart_if_leaking() -> None:
    """Exit for a clean restart BEFORE forks start failing, not after.

    Restarting is the fix, not a workaround: a leaked slot is
    unrecoverable from inside the process. Scheduler state lives on
    the volume and boot resumes mid-day (_load_state), so the cost of
    a restart is one publish cycle; the cost of NOT restarting was
    measured on A-045 at two days of a frozen board. Logged every
    pass so the climb rate is in the deploy log the next time anyone
    has to ask.
    """
    p = _process_pressure()
    pids, threads = p["container_pids"], p["worker_threads"]
    if pids is None:
        return
    log(f"pressure: {pids} pids, {threads} threads in container")
    if pids > PID_PRESSURE_EXIT or (threads or 0) > THREAD_PRESSURE_EXIT:
        log(f"FATAL process-slot leak: {pids} pids / {threads} threads "
            f"(limits {PID_PRESSURE_EXIT}/{THREAD_PRESSURE_EXIT}) — "
            f"exiting so Railway restarts the container")
        os._exit(1)


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
                # WHAT the cache holds and WHEN it was last topped up, not
                # merely that a directory exists. `cache_months` said
                # "2026-08 is present" all through A-036/A-037 -- true,
                # useless, and answering "is 2026-08-10 actually in there?"
                # took container-log access nobody has in a hurry. Same
                # lesson as invariant 11: report the operation.
                "statcast_cache": _cache_status(),
                "data_json_present": DASHBOARD_JSON.exists(),
                "last_reconcile": LAST_RECONCILE,
                # The board this container SERVES, and when it last pulled
                # CI's work. These froze at 09:51 for a whole afternoon and
                # nothing anywhere said so, because /health only reported
                # that data.json EXISTED -- never how old it was.
                "last_publish": LAST_PUBLISH,
                # Whether the worker is actually RECEIVING CI's work.
                # last_publish above is not that signal and never was: it
                # reported ok=true for 16 hours across A-029 while every
                # pull failed, because publish_pass swallows the result.
                "last_pull": LAST_PULL,
                # Reports the CAPABILITY, not the config. This used to be
                # bool(GITHUB_TOKEN), which answers "is an env var set?"
                # -- a question nothing depends on. It read `true` for 16
                # hours on 2026-08-08 while every git command in the
                # container failed with "not a git repository", because a
                # token was indeed set and that was all it ever checked.
                "can_push_to_git": bool(
                    GIT_STATUS.get("is_repo")
                    and GIT_STATUS.get("remote") == "authenticated"
                ),
                "git": GIT_STATUS,
                # A-045: the zombie leak that killed forks was
                # invisible until exhaustion. container_pids includes
                # zombies; the worker exits (and Railway restarts it)
                # past restart_over.
                "process_pressure": _process_pressure(),
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


GIT_STATUS: dict = {"is_repo": None, "shallow": None, "remote": None,
                    "bootstrapped": None, "error": None, "checked": None}

# Whether the worker is actually RECEIVING CI's work. Declared up here
# because the boot-time bootstrap sets it before sync_repo ever runs.
LAST_PULL: dict = {"at": None, "ok": None, "error": None, "head": None,
                   "recovered": None}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True)


def _remote_url() -> str:
    """Authenticated when a token exists, public otherwise.

    The repo is public, so an anonymous URL still pulls. Only the push
    half needs the token.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return f"https://x-access-token:{token}@github.com/{GITHUB_REPO}.git"
    return f"https://github.com/{GITHUB_REPO}.git"


def _bootstrap_repo(url: str) -> bool:
    """Make /app a real checkout of origin/master when it is not one.

    Railway's builder ships a source ARCHIVE, never a clone -- the build
    log reads "fetching snapshot" then "unpacking archive". So /app has
    NEVER contained .git, whatever .dockerignore says. Removing the
    `.git/` exclusion (A-029, first attempt) was a genuine bug fix and
    changed nothing here: there is no .git in the build context to copy.
    The container has to create the checkout itself.

    `reset --hard` is safe here, and the reason is worth stating because
    it would not be safe in a lot of repos:

      * No symlinks are involved. seed_volume_state() copies image ->
        volume precisely so the ledger is a REAL file on /data/state;
        the atomic-write pattern destroys symlinked destinations. So
        nothing git touches in /app can reach the volume's ledger.
      * Nothing tracked by git is excluded from the image. Every
        .dockerignore entry (statcast_cache, chadwick_cache, node_modules,
        .next, out, logs, .vercel) covers only gitignored paths, so the
        unpacked archive is a complete checkout of the build commit and
        the reset has no phantom deletions to apply.

    Resetting to origin/master rather than the build commit is
    deliberate: CI commits every few minutes, so by boot the archive is
    usually already behind, and _merge_dir reads FILES out of this
    checkout -- not git objects. A repo whose HEAD is current but whose
    working tree is stale would merge yesterday's board into the volume
    and look perfectly healthy doing it.
    """
    steps = (
        ("git init",   ("init", "-b", "master")),
        ("git remote", ("remote", "add", "origin", url)),
        ("git fetch",  ("fetch", "--no-tags", "origin")),
        ("git reset",  ("reset", "--hard", "origin/master")),
    )
    for label, args in steps:
        res = _git(*args)
        if res.returncode != 0:
            err = (res.stderr or "").strip()
            # Never let a token reach the log.
            GIT_STATUS.update(bootstrapped=False, error=f"{label}: {err[:160]}")
            log(f"FATAL git bootstrap failed at {label}: {err[:200]}")
            return False
    _git("branch", "--set-upstream-to=origin/master", "master")
    head = (_git("rev-parse", "--short", "HEAD").stdout or "").strip()
    GIT_STATUS["bootstrapped"] = True
    # A successful bootstrap fetched and reset to origin/master, so this
    # container HAS received CI's work -- record it as a pull. Without
    # this LAST_PULL stays {ok: None} until the first publish pass up to
    # five minutes later, and anything gating on it (the watchdog's
    # publish-window grace) reports a freshly-booted worker as broken.
    # A red CI run after every deploy is how a real alarm gets ignored.
    LAST_PULL.update(at=datetime.now(ET).isoformat(timespec="seconds"),
                     ok=True, error=None)
    log(f"git: /app had no .git — bootstrapped a checkout at {head}")
    return True


def configure_git() -> None:
    """Point the repo at an authenticated remote so the ledger can push.

    EVERY step here is checked. The previous version ran four git
    commands with capture_output=True and inspected none of them, then
    logged "git remote configured" unconditionally. On 2026-08-08 that
    line appeared in the boot log at 23:06 EDT while all four commands
    were failing with exit 128 -- `.dockerignore` excluded `.git/`, so
    /app was not a repository at all. The worker then ran 16 hours
    dispatching work to CI and never pulling a single result back,
    serving a board frozen at image-build time, while /health reported
    can_push_to_git: true. A success line that cannot fail is worse than
    no line: it is the thing you check first and it lies.
    """
    GIT_STATUS["checked"] = datetime.now(ET).isoformat(timespec="seconds")
    url = _remote_url()

    probe = _git("rev-parse", "--git-dir")
    if probe.returncode != 0:
        # Expected on Railway every boot: the builder unpacks an archive,
        # so there is no .git to inherit. Build one rather than spend the
        # day serving a frozen board.
        if not _bootstrap_repo(url):
            GIT_STATUS.update(
                is_repo=False,
                error=GIT_STATUS.get("error")
                or (probe.stderr or "").strip() or f"exit {probe.returncode}",
            )
            log("FATAL git: {} is not a git repository and could not be "
                "bootstrapped. The worker cannot pull CI's output or push "
                "the ledger; it will serve a frozen board."
                .format(REPO))
            return
        GIT_STATUS["error"] = None
    else:
        GIT_STATUS["bootstrapped"] = False
    GIT_STATUS["is_repo"] = True

    # Railway's builder may hand us a shallow clone; `git pull --rebase`
    # against one can refuse with "refusing to merge unrelated histories".
    shallow = _git("rev-parse", "--is-shallow-repository")
    GIT_STATUS["shallow"] = (shallow.stdout or "").strip() == "true"
    if GIT_STATUS["shallow"]:
        un = _git("fetch", "--unshallow", "origin", "master")
        log("git: shallow clone {}".format(
            "unshallowed" if un.returncode == 0 else
            f"could NOT be unshallowed ({(un.stderr or '').strip()[:120]})"))
        GIT_STATUS["shallow"] = un.returncode != 0

    for key, val in (("user.email", "worker@mlb-strikeouts"),
                     ("user.name", "Strikeouts Worker")):
        res = _git("config", key, val)
        if res.returncode != 0:
            log(f"WARNING git config {key} failed: "
                f"{(res.stderr or '').strip()[:120]}")

    if not os.environ.get("GITHUB_TOKEN"):
        GIT_STATUS["remote"] = "anonymous"
        log("WARNING: GITHUB_TOKEN unset — pull works (public repo) but "
            "ledger changes will stay on the volume and will NOT reach "
            "GitHub or the dashboard.")
        return
    # Idempotent: set-url whether the remote came from the bootstrap or
    # from an inherited checkout, so both paths end authenticated.
    res = _git("remote", "set-url", "origin", url)
    if res.returncode != 0:
        GIT_STATUS.update(remote="failed",
                          error=(res.stderr or "").strip()[:200])
        log(f"ERROR git remote set-url failed: {GIT_STATUS['error']} — "
            f"pushes will not reach {GITHUB_REPO}")
        return
    GIT_STATUS["remote"] = "authenticated"
    log(f"git remote configured for {GITHUB_REPO}")


# Lock files a killed or timed-out git leaves behind. NOTHING clears
# these: every later git command in the container fails identically
# until a human redeploys. That is how the worker spent 27 hours on
# 2026-08-15/16 serving a board from the previous morning while
# /health reported the credential as fine (A-040).
GIT_LOCKS = ("index.lock", "shallow.lock", "FETCH_HEAD.lock",
             "HEAD.lock", "config.lock", "packed-refs.lock")

# A lock younger than this may belong to a git process that is still
# running, and deleting one out from under a live command turns a
# wedged checkout into a corrupted one. Only the publish loop runs git
# in this container and it runs sequentially, so anything older than a
# full publish cycle is abandoned by definition.
STALE_LOCK_S = 600


def _clear_stale_git_locks() -> list[str]:
    """Delete abandoned git lock files; return the names cleared."""
    git_dir = REPO / ".git"
    cleared = []
    now = time.time()
    for name in GIT_LOCKS:
        path = git_dir / name
        try:
            if path.exists() and now - path.stat().st_mtime > STALE_LOCK_S:
                path.unlink()
                cleared.append(name)
        except OSError as exc:
            log(f"git: could not clear {name}: {type(exc).__name__}: {exc}")
    return cleared


def _head_state() -> dict:
    """What HEAD is doing right now — attached, detached, mid-rebase.

    Published on /health. A-034 ran for four hours with every symptom
    visible in the deploy log and nothing on /health saying which of the
    three states the container was in.
    """
    branch = _git("symbolic-ref", "--quiet", "--short", "HEAD")
    rebase_dir = (_git("rev-parse", "--git-path", "rebase-merge").stdout or "").strip()
    apply_dir = (_git("rev-parse", "--git-path", "rebase-apply").stdout or "").strip()
    mid_rebase = any(
        d and (REPO / d).exists() for d in (rebase_dir, apply_dir)
    )
    return {
        "branch": (branch.stdout or "").strip() if branch.returncode == 0 else None,
        "detached": branch.returncode != 0,
        "rebase_in_progress": mid_rebase,
    }


def sync_repo() -> None:
    """Take CI's copy wholesale, then merge it into the volume.

    RESET, not pull. `git pull --rebase` was wrong here in a way that
    took four hours of stranded grades to show (A-034): it tries to
    reconcile two edits to `dashboard/public/data.json`, and there is
    nothing to reconcile. That file is DERIVED -- regenerated from the
    volume a few lines later in every publish pass -- so a conflict in
    it has no correct resolution, only a halted rebase. Which is what
    happened: the rebase stopped, left `.git/rebase-merge` behind and
    HEAD detached, and every later pull died on
    `fatal: It seems that there is already a rebase-merge directory`.
    Nothing self-healed, because nothing was watching for it.

    It is also what makes the pull/push race survivable. CI pushes on
    its own schedule, so between this container's fetch and its push
    the remote can move -- measured at 03:01:23 ET on 2026-08-11, one
    second apart. Rebasing turns that race into wedged state; resetting
    turns it into "next pass picks it up", because the losing pass's
    commit held nothing the volume cannot regenerate.

    Discarding the checkout is safe for exactly the reasons
    _bootstrap_repo already sets out: no symlinks reach the volume,
    nothing tracked is excluded from the image, and `reset --hard`
    leaves untracked and ignored paths (the Statcast cache) alone. The
    volume is the source of truth; /app is scratch.

    The reconcile is the load-bearing half. The jobs read and write
    DATA_STATE_DIR (the volume); git only touches the /app checkout.
    Without a merge those are two independent ledgers: the PC writes
    picks to git, the container grades a volume copy that never sees
    them, and its /data.json -- which the dashboard PREFERS over the
    bundled copy -- silently reports a record missing the picks.
    """
    # Unconditional, and failure-tolerant on purpose: `git rebase
    # --abort` exits non-zero when no rebase is in progress, which is
    # the normal case. Running it every pass is what makes the recovery
    # automatic instead of waiting for a human with a shell.
    before = _head_state()
    if before["rebase_in_progress"] or before["detached"]:
        log(f"git: recovering wedged checkout {before} — aborting any rebase "
            f"and resetting to origin/master")
        _git("rebase", "--abort")
        # Reattach BEFORE the network is involved. If the fetch below
        # fails (Railway egress blip, GitHub 5xx) the container must
        # still end this call on a branch, or the next commit lands on a
        # detached HEAD again and the push keeps pointing at a master
        # that never moves. Anchoring to HEAD keeps this pass's history;
        # the next successful fetch discards it, which costs nothing
        # because every byte of it is regenerated from the volume.
        if _head_state()["detached"]:
            _run("git-reattach", ["git", "checkout", "-B", "master", "HEAD"], 60)
    # Drop working-tree edits from the previous pass's mirror. They are
    # regenerated from the volume immediately after this returns, and
    # leaving them makes `checkout -B` fail on a dirty tree.
    _git("reset", "--hard")

    # Recorded as a FIRST-CLASS signal, not inferred from publish_pass.
    # `_run` returns False on failure and never raises, and publish_pass
    # wraps the whole pass in try/except and sets last_publish ok=True
    # regardless -- so throughout the A-029 outage /health advertised
    # `last_publish: {ok: true}` while every git command was failing with
    # exit 128. Anything downstream that wants to know "is this worker
    # actually receiving CI's work?" must read THIS, not last_publish.
    #
    # No token gate: the repo is public, so an anonymous fetch works. The
    # old GITHUB_TOKEN check meant the container silently never pulled,
    # which is how it ended up running against whatever ledger happened
    # to be baked into the image.
    def _pull(tag: str) -> bool:
        got = _run(f"git-fetch{tag}",
                   ["git", "fetch", "--no-tags", "origin", "master"], 180)
        if got:
            # `checkout -B` does both halves in one shot: moves master to
            # the fetched tip AND reattaches HEAD to it. A bare `reset
            # --hard` would move whatever HEAD currently is -- and when
            # HEAD is detached that leaves master behind, which is
            # precisely the state that made every push fail
            # non-fast-forward while the commits themselves succeeded.
            got = _run(f"git-reset-to-origin{tag}",
                       ["git", "checkout", "-B", "master", "FETCH_HEAD"], 120)
        return got

    ok = _pull("")
    recovered = None
    if not ok:
        # Try ONCE to unwedge, then pull again. Before this a failed
        # fetch was terminal for the life of the container: nothing
        # retried and nothing cleared the wedge, so the worker kept
        # serving whatever board it already had and only a human
        # redeploy brought it back (A-040). The retry is cheap and the
        # failure it targets is the one that actually happened.
        cleared = _clear_stale_git_locks()
        if cleared:
            log(f"git: cleared abandoned lock(s) {cleared} — retrying pull")
        ok = _pull("-retry")
        if ok:
            recovered = f"recovered after clearing {cleared or 'nothing'}"
            log(f"git: pull {recovered}")

    LAST_PULL.update(
        at=datetime.now(ET).isoformat(timespec="seconds"),
        ok=ok,
        error=None if ok else "git fetch/reset failed — see the job log for the exit code",
        head=_head_state(),
        # Surfaced so a container that is limping (failing, then
        # self-healing every pass) is distinguishable on /health from
        # one that is genuinely healthy.
        recovered=recovered,
    )
    reconcile_ledger()


# How often the resident loop pulls back whatever CI published and
# re-serves it. Cheap enough to be generous: the pull is a no-op when
# nothing changed and a data.json rebuild measures 0.73s.
PUBLISH_EVERY_SECONDS = 300

LAST_PUBLISH: dict = {"at": None, "ok": None, "error": None,
                      "served_generated_at": None}


def mirror_volume_to_repo() -> int:
    """Copy the volume's ledger into the git checkout so it can be pushed.

    The missing direction. `_merge_csv` unions repo -> volume and never
    writes back, so anything this container produces stays on the volume:
    the live watcher grades a starter the moment he is pulled (A-021),
    writes it to the volume, rebuilds the served board -- and the ledger
    in git never hears about it.

    Measured 2026-08-07: the worker had Payton Tolle graded LOSS, 14 K,
    -2.0u, and reconciled 10 of 10 picks at 22:57. Git's newest row for
    him was blank, so `tools/pl_calc.py` -- which reads the repo and is
    the ONLY sanctioned source of a P&L figure -- still reported the
    pre-game total. Early grading existed and could not reach the books.

    Safe because reconcile runs FIRST: it unions the freshly pulled repo
    rows into the volume, union-only and never downgrading, so by the
    time we copy back the volume is a superset of the checkout. Nothing
    can be lost by this direction; it can only add.

    Returns the number of files copied, for the log.
    """
    try:
        from tracker import DATA_STATE_DIR as _LEDGER_DIR
        if _LEDGER_DIR.resolve() == (REPO / "data").resolve():
            return 0  # CI: the checkout IS the ledger, nothing to mirror
    except OSError:
        return 0

    if not VOLUME_STATE.exists():
        return 0

    copied = 0
    for name in ("picks_2026.csv", "model_log.csv", "pick_changes.csv"):
        src = VOLUME_STATE / name
        if src.exists():
            shutil.copy2(src, REPO / "data" / name)
            copied += 1
    for sub in ("slates", "odds"):
        src_dir = VOLUME_STATE / sub
        if not src_dir.is_dir():
            continue
        dest_dir = REPO / "data" / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, dest_dir / f.name)
                copied += 1
    return copied


def publish_pass() -> None:
    """Pull whatever GitHub Actions published, and re-serve it.

    Railway is the clock and Actions are the hands -- but only half of
    that was wired. The loop dispatched the real work to GitHub (which
    can reach DraftKings; this container cannot), marked the task done,
    and moved on. It never pulled the RESULT back. So the container kept
    serving the board from its own last LOCAL run, and since
    dashboard/lib/data-context.tsx PREFERS this container's /data.json
    over the bundled copy -- unconditionally, whenever it answers -- the
    whole site froze there.

    Measured 2026-08-07: the site served the 09:51 morning board all
    afternoon while the repo held the 16:47 lineup-locked one, hiding a
    LEAN on Payton Tolle with two hours to first pitch. It stayed
    invisible until now because the FALLBACK path (dispatch failed ->
    run locally) does rebuild data.json. The bug only became reachable
    once the GitHub token was added and dispatch began succeeding every
    time -- i.e. the day the system started working as designed.

    The checkout is deliberate. data.json is DERIVED and never a source
    of truth, so the local copy is dropped before pulling. Without that,
    `git pull --rebase --autostash` stashes the locally-generated file,
    pulls the fresh one, then re-applies the stash straight back on top
    -- and the stale copy wins every single time.
    """
    try:
        _run("drop-derived",
             ["git", "checkout", "--", "dashboard/public/data.json"], 60)
        sync_repo()
        # Push half. sync_repo() has just unioned the pulled rows into the
        # volume, so the volume is now a superset of the checkout -- copy
        # it back and mirror it to git. Without this the live watcher's
        # grades never leave the container and pl_calc, which reads the
        # repo, reports a P&L the operator can see is wrong on the board.
        n = mirror_volume_to_repo()
        if n:
            log(f"mirrored {n} ledger file(s) from the volume into the checkout")
        _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
        # No-ops when nothing changed ("git: nothing to commit"), so this
        # is cheap to run every pass and only speaks when there is news.
        commit_and_push("live grades")
        served = None
        try:
            served = json.loads(
                DASHBOARD_JSON.read_text(encoding="utf-8")).get("generated_at")
        except Exception:
            pass
        LAST_PUBLISH.update(at=datetime.now(ET).isoformat(timespec="seconds"),
                            ok=True, error=None, served_generated_at=served)
    except Exception as exc:
        LAST_PUBLISH.update(at=datetime.now(ET).isoformat(timespec="seconds"),
                            ok=False, error=f"{type(exc).__name__}: {exc}")
        log(f"publish pass FAILED: {type(exc).__name__}: {exc}")


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
    # On a CI runner the checkout IS the ledger, so there are not two
    # copies to merge. Compare DATA_STATE_DIR — what the pipeline
    # actually reads and writes — NOT VOLUME_STATE, which is a level
    # deeper (data/state) and therefore never equal to data. Getting
    # that wrong meant the guard never fired on CI and every run logged
    # "reconcile failed (FileNotFoundError: data/state/picks_2026.csv)",
    # which is exactly the kind of routine scary warning that trains you
    # to ignore a real one.
    try:
        from tracker import DATA_STATE_DIR as _LEDGER_DIR
        if _LEDGER_DIR.resolve() == (REPO / "data").resolve():
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
    ok = _run(
        "statcast-backfill",
        [PYTHON, "data/backfill_statcast.py",
         "--start", start.isoformat(), "--end", today.isoformat()],
        timeout=7200 if not has_data else 1800,
    )
    # Published on /health. "When did this container last top up its own
    # cache?" was unanswerable without deploy-log access, which is how
    # A-037 -- one refresh per BOOT, because dispatch had quietly taken
    # the scheduled ones away -- stayed invisible.
    LAST_CACHE_REFRESH.update(
        at=datetime.now(ET).isoformat(timespec="seconds"),
        ok=ok,
        window=f"{start.isoformat()}..{today.isoformat()}",
    )


LAST_CACHE_REFRESH: dict = {"at": None, "ok": None, "window": None}


def _cache_status() -> dict:
    """What the Statcast cache actually contains right now.

    `recent_bytes` is the load-bearing field: a schema-only parquet is
    636 bytes, so a day that is present-but-empty is visibly different
    from a real one (a light slate is ~450 KB) without needing to open
    the file. `null` means the file is absent entirely.
    """
    if not CACHE_DIR.exists():
        return {"latest_date": None, "n_days": 0, "recent_bytes": {},
                "last_refresh": LAST_CACHE_REFRESH}
    today = datetime.now(ET).date()
    recent = {}
    for i in range(5):
        d = today - timedelta(days=i)
        p = CACHE_DIR / f"{d:%Y-%m}" / f"{d.isoformat()}.parquet"
        try:
            recent[d.isoformat()] = p.stat().st_size if p.exists() else None
        except OSError:
            recent[d.isoformat()] = None
    dates = sorted(p.stem for p in CACHE_DIR.glob("*/*.parquet"))
    return {
        "latest_date": dates[-1] if dates else None,
        "n_days": len(dates),
        "recent_bytes": recent,
        "last_refresh": LAST_CACHE_REFRESH,
    }


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
    # A detached HEAD is not a place to put commits. The push below names
    # the BRANCH (`origin master`), so a commit made while detached is
    # unreachable from master: git-commit reports OK, git-push reports
    # non-fast-forward, and the pair reads as a push problem when the
    # real fault is three steps upstream. That is A-034 exactly. Refuse
    # loudly; sync_repo reattaches on the next pass and nothing is lost
    # because the volume regenerates the content.
    head = _head_state()
    if head["detached"] or head["rebase_in_progress"]:
        log(f"git mirror skipped — checkout is not on a branch ({head}); "
            f"sync_repo resets it next pass, volume still holds the ledger")
        return
    subprocess.run(
        # model_log.csv rides along: it is the evidence table the /model
        # page and the shadow portfolio are scored from, and it is
        # produced on the volume like everything else here.
        ["git", "add", "data/picks_2026.csv", "data/model_log.csv",
         "data/slates", "data/pick_changes.csv", "data/odds",
         "dashboard/public/data.json"],
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


def _log_evidence() -> None:
    """Score every finished slate and refresh the shadow portfolio.

    Called from EVERY task, not just one. Both steps are idempotent per
    date and cost ~1s when there is nothing new, so the cheapest way to
    guarantee a slate is logged is to keep trying all day.

    That matters because the input is a third-party feed on its own
    clock: Baseball Savant publishes yesterday's games mid-morning, and
    tying the only attempt to a fixed time is exactly how 8/5 and 8/6
    were lost (A-022). Six attempts a day means the board time can move
    -- as it just did, 10:30 -> 09:00 -- without putting the evidence at
    risk again.

    refresh_cache() FIRST, and that ordering is the whole point.
    model_log reads the Statcast cache; it does not fetch. Running it
    six times against a cache that only refreshes at 03:00 just re-reads
    the same empty file six times. That is precisely what happened on
    2026-08-07: the retries were added without their input, so Railway
    served a board with zero results for 8/6 all day while CI -- which
    tops up the cache on every run -- had all twenty.
    """
    refresh_cache()
    _run("model-log", [PYTHON, "tools/model_log.py"], 900)
    _run("shadow", [PYTHON, "tools/shadow.py"], 300)


def task_morning() -> None:
    sync_repo()
    _run("daily-cycle", [PYTHON, "run.py"], 2400)
    _log_evidence()
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
    commit_and_push("morning slate")


def task_lineups() -> None:
    sync_repo()
    _run("lineup-lock-predict", [PYTHON, "run.py", "predict"], 2400)
    _log_evidence()
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
    commit_and_push("lineup-lock re-run")


def task_close() -> None:
    _run("closing-odds", [PYTHON, "run.py", "close"], 600)
    _log_evidence()
    commit_and_push("closing-odds snapshot")


def task_night() -> None:
    sync_repo()
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
    _log_evidence()
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


def _run_or_dispatch(name: str) -> str:
    """Hand a task to CI when we can, and keep OUR cache current either way.

    Returns "dispatched", "local" or "error".

    The refresh on the dispatch path is the whole reason this is a
    function (A-037). `_log_evidence()` calls `refresh_cache()` first,
    six times a day, and its docstring explains at length that this is
    what stopped the 2026-08-07 evidence loss. But `_log_evidence` runs
    inside the TASK -- and once a GitHub token existed, dispatch began
    succeeding every time, so the task ran on CI and this container
    stopped executing that code path entirely. Its cache was left with
    one refresh per BOOT, at whatever arbitrary moment a deploy happened.

    Measured: the container booted 2026-08-10 18:41 ET, mid-games, and
    again 2026-08-11 07:58 ET, before Baseball Savant had published the
    previous day (A-022: 0 pitches at 03:21, 3,530 by 08:59). Between
    those two moments nothing topped it up, so on 2026-08-11 the worker
    rendered 2026-08-10 with 1 of 18 actual strikeout totals while CI --
    which restores the cache on every run -- rendered 18 of 18 from the
    same commit (A-036).

    This is the third bug of that exact shape: a mechanism that worked
    while the fallback path was the normal path, and quietly stopped the
    day the primary path started succeeding (A-025 for publishing, A-036
    for rendering, this for the cache). Dispatching work does not
    outsource this container's own inputs.
    """
    if dispatch_github(name):
        refresh_cache()
        return "dispatched"
    try:
        TASKS[name]()
        return "local"
    except Exception as exc:
        log(f"TASK ERROR {name}: {exc}")
        return "error"


def main() -> None:
    log("=== Strikeouts Railway worker starting ===")
    log(f"cache: {CACHE_DIR}  state: {STATE_PATH}")
    # FIRST, before anything reads the checkout. On Railway /app arrives
    # as an unpacked archive with no .git, so this is what turns it into
    # a real checkout and fast-forwards it to origin/master. Both of the
    # next two steps read files out of that checkout: seed_volume_state()
    # fills gaps in the volume from it, and reconcile_ledger() merges its
    # data/ into the volume. Run them against the unrefreshed archive and
    # the boot rebuild of data.json publishes a board that is already
    # behind -- which is precisely the failure A-029 was filed for.
    configure_git()
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

    # Unconditional, not only when cold. A warm-but-STALE cache is the
    # dangerous state: it looks populated, so the old "is it empty?"
    # test passed and left yesterday's games missing. Topping up a warm
    # cache is a fast no-op.
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
        #
        # This used to blank `boot_task` on a successful dispatch and
        # then call TASKS[""] anyway, which raised KeyError into the
        # handler below and logged `BOOT TASK ERROR : ''` on the one
        # path that had actually WORKED. Routing through
        # _run_or_dispatch removes the second call entirely.
        outcome = _run_or_dispatch(boot_task)
        log(f"--- boot task {boot_task} finished ({outcome}) ---")
    elif boot_task:
        log(f"RUN_TASK_ON_BOOT={boot_task!r} is not a known task {list(TASKS)}")

    log("schedule (ET): " + ", ".join(
        f"{t.strftime('%H:%M')} {name}" for name, t, _ in SCHEDULE))

    next_publish = 0.0
    while True:
        try:
            # BEFORE looking at the schedule. Dispatched work lands in git,
            # not in this container, so without this pass the board we
            # serve only ever reflects work this container did itself.
            if time.monotonic() >= next_publish:
                # Gauge first: restarting while forks still work beats
                # diagnosing after they stop (A-045).
                _restart_if_leaking()
                publish_pass()
                next_publish = time.monotonic() + PUBLISH_EVERY_SECONDS

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
                # grades, logs and rebuilds the dashboard. Either way the
                # Statcast cache on THIS volume is topped up -- see
                # _run_or_dispatch (A-037).
                outcome = _run_or_dispatch(name)
                state[key] = today
                _save_state(state)
                log(f"--- finished {key} ({outcome}) ---")

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
        # (date, pitcher_id): the carryover keeps two dates in flight at
        # once and a starter appears on many dates, so a bare id would
        # swallow tonight's finish because last night's already fired.
        seen: set[tuple[str, int]] = set()
        while True:
            try:
                state = lw.poll_once(lw.today_et())
                lw.write_state(state)
                reports = [state]

                # Finish yesterday's late games before starting today.
                # Archive only -- live_state.json means "now", and now is
                # today. Without this a start crossing midnight ET was
                # abandoned in progress and stayed "IN GAME" forever
                # (A-039).
                carry = lw.carryover_date()
                if carry:
                    carry_state = lw.poll_once(carry)
                    lw.archive_state(carry_state)
                    reports.append(carry_state)

                rebuild = False
                for st in reports:
                    fresh = {(st["date"], r["pitcher_id"])
                             for r in st["pitchers"] if r.get("final")} - seen
                    if not fresh:
                        continue
                    seen |= fresh
                    ids = {pid for _, pid in fresh}
                    names = [r["pitcher_name"] for r in st["pitchers"]
                             if r["pitcher_id"] in ids]
                    log(f"live: {len(fresh)} starter(s) finished on "
                        f"{st['date']} ({', '.join(n for n in names if n)})"
                        " — grading")
                    # Their totals can no longer change, so grade now
                    # rather than at 03:00. run.py grade is idempotent
                    # and honours the locked-pick rules; Statcast
                    # reconciles overnight via tools/watchdog.py.
                    # Grade the date the starter actually pitched on, not
                    # today's, or a carryover finish would be filed
                    # against the wrong slate.
                    _run("grade-live", [PYTHON, "run.py", "grade",
                                        st["date"]], 900)
                    rebuild = True
                if rebuild:
                    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 900)
                time.sleep(30 if any(s.get("any_live") for s in reports) else 600)
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
