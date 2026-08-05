"""Scheduled-task entry point for the daily automation.

Windows Task Scheduler calls this with one argument:

    python tools/scheduled_run.py morning   # 10:30 AM ET
        grade yesterday -> predict today -> dashboard data ->
        commit ledger -> push -> deploy dashboard
    python tools/scheduled_run.py close     # 12:15 / 3:00 / 6:15 PM ET
        closing-odds snapshot (append-only; grader uses the last
        capture before each game's own start time)
    python tools/scheduled_run.py night     # 3:00 AM ET
        grade the finished slate -> dashboard data -> commit -> push ->
        deploy dashboard

Every step logs to logs/auto_YYYY-MM-DD.log. A failed step is logged
and the remaining steps still run (a dead DK endpoint must not stop
grading, and vice versa).
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.parent
LOGS = REPO / "logs"
ET = ZoneInfo("America/New_York")

PYTHON = sys.executable
NPX = shutil.which("npx") or r"C:\Program Files\nodejs\npx.cmd"
if NPX.lower().endswith(".ps1"):
    NPX = str(Path(NPX).with_suffix(".cmd"))


def _log_path() -> Path:
    LOGS.mkdir(exist_ok=True)
    return LOGS / f"auto_{datetime.now(ET):%Y-%m-%d}.log"


def _log(msg: str):
    line = f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run(label: str, cmd: list[str], timeout: int) -> bool:
    _log(f"START {label}: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log(f"FAILED {label}: timed out after {timeout}s")
        return False
    except Exception as exc:
        _log(f"FAILED {label}: {exc}")
        return False

    tail = "\n".join((result.stdout or "").strip().splitlines()[-12:])
    if tail:
        _log(f"  {label} output:\n{tail}")
    if result.returncode != 0:
        err_tail = "\n".join((result.stderr or "").strip().splitlines()[-8:])
        _log(f"FAILED {label}: exit {result.returncode}\n{err_tail}")
        return False
    _log(f"OK {label}")
    return True


def _commit_and_push(context: str) -> bool:
    """Commit ledger/slate/dashboard-data changes, if any, and push."""
    _run("git-add", ["git", "add", "data/picks_2026.csv", "data/slates",
                     "data/pick_changes.csv", "dashboard/public/data.json"], 60)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if staged.returncode == 0:
        _log("git: nothing to commit")
        return True
    ok = _run("git-commit", [
        "git", "commit", "-m",
        f"chore(auto): {context} {datetime.now(ET):%Y-%m-%d %H:%M} ET",
    ], 60)
    if ok:
        ok = _run("git-push", ["git", "push", "origin", "master"], 180)
    return ok


def _deploy_dashboard() -> bool:
    return _run("vercel-deploy", [NPX, "vercel", "--prod", "--yes"], 900)


def task_morning():
    _run("daily-cycle", [PYTHON, "run.py"], 1800)
    _run("probe-alt-unders", [PYTHON, "scrape_dk_odds.py", "--probe-unders"], 180)
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 600)
    _commit_and_push("morning slate")
    _deploy_dashboard()


def task_close():
    _run("closing-odds", [PYTHON, "run.py", "close"], 300)


def task_night():
    _run("grade", [PYTHON, "run.py", "grade"], 900)
    _run("dashboard-data", [PYTHON, "tools/dashboard_data.py"], 600)
    _commit_and_push("overnight grading")
    _deploy_dashboard()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("morning", "close", "night"):
        print("usage: python tools/scheduled_run.py {morning|close|night}")
        sys.exit(2)

    task = sys.argv[1]
    _log(f"===== scheduled task '{task}' begin =====")
    {"morning": task_morning, "close": task_close, "night": task_night}[task]()
    _log(f"===== scheduled task '{task}' end =====")


if __name__ == "__main__":
    main()
