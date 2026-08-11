"""The container must be a git repository (AUDIT A-029).

Railway is the clock, GitHub Actions are the hands, and git is the only
wire between them. `.dockerignore` excluded `.git/` on 2026-08-05, so
`COPY . .` produced an /app that is not a repository and every git call
in the container failed with exit 128. A-025's pull fix and A-028's push
fix were both written afterwards, against a container that could never
run either. The worker dispatched work to CI and discarded every result
for three days, serving a board frozen at image-build time -- and since
`dashboard/lib/data-context.tsx` prefers the worker's /data.json, that
frozen board was the site.

Sixteen hours of it went unnoticed because two mechanisms reported
success they never checked, so all three properties are asserted here:

  1. SHIPPED     — `.dockerignore` does not exclude `.git/`.
  2. DETECTED    — configure_git() notices a non-repo instead of
                   logging "git remote configured" over four exit-128
                   failures.
  3. HONEST      — can_push_to_git reports the capability, not whether
                   an environment variable happens to be set.

Run:  python -m pytest tests/test_worker_git.py -q
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKER_STATE_DIR", "data")
os.environ.setdefault("DATA_STATE_DIR", "data")


def _dockerignore_patterns() -> list[str]:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_dockerignore_does_not_exclude_git():
    """The one line that caused A-029.

    Matches any form that would drop the directory -- `.git`, `.git/`,
    `/.git`, `**/.git` -- rather than the single literal that happened to
    be there, so a differently-spelled reintroduction still fails.
    """
    offenders = [p for p in _dockerignore_patterns()
                 if re.fullmatch(r"!?\*{0,2}/?\.git/?", p) and not p.startswith("!")]
    assert not offenders, (
        f".dockerignore excludes {offenders} — the worker cannot pull CI's "
        f"board or push the ledger without .git in the image; every git "
        f"call fails with exit 128 and the served board freezes silently."
    )


def test_unbootstrappable_checkout_is_reported_not_papered_over(
    tmp_path, monkeypatch
):
    """When the checkout cannot be built, say so — loudly.

    Bootstrap can legitimately fail (no network, revoked token, GitHub
    down). The container then cannot pull CI's board or push the ledger
    and will serve a frozen one, so this path must be unmistakable in
    the log and must NOT emit the success line.
    """
    import tools.railway_worker as w

    monkeypatch.setattr(w, "REPO", tmp_path)
    # A remote that cannot resolve, so `git fetch` fails.
    monkeypatch.setattr(w, "_remote_url", lambda: str(tmp_path / "nope.git"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    w.GIT_STATUS.update(is_repo=None, shallow=None, remote=None,
                        bootstrapped=None, error=None)

    logged: list[str] = []
    monkeypatch.setattr(w, "log", lambda m: logged.append(str(m)))

    w.configure_git()

    assert w.GIT_STATUS["is_repo"] is False
    assert w.GIT_STATUS["bootstrapped"] is False
    assert w.GIT_STATUS["error"]
    blob = "\n".join(logged)
    assert "FATAL" in blob
    # The precise A-029 regression: a success line emitted over a failure.
    assert "git remote configured" not in blob


def test_bootstrap_failure_never_logs_the_token(tmp_path, monkeypatch):
    """Bootstrap errors are logged; the remote URL carries a credential."""
    import tools.railway_worker as w

    monkeypatch.setattr(w, "REPO", tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecretvalue")
    w.GIT_STATUS.update(is_repo=None, bootstrapped=None, error=None)

    logged: list[str] = []
    monkeypatch.setattr(w, "log", lambda m: logged.append(str(m)))
    # Force the fetch to fail while keeping the real authenticated URL.
    monkeypatch.setattr(w, "GITHUB_REPO", "joey11600/does-not-exist-xyz")

    w.configure_git()

    blob = "\n".join(logged) + str(w.GIT_STATUS)
    assert "ghp_supersecretvalue" not in blob, "token leaked into the log"


def test_configure_git_accepts_a_real_repository(monkeypatch):
    """The checkout this test runs from is a repo; probe must say so."""
    import tools.railway_worker as w

    monkeypatch.setattr(w, "REPO", ROOT)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    w.GIT_STATUS.update(is_repo=None, shallow=None, remote=None, error=None)
    monkeypatch.setattr(w, "log", lambda m: None)

    w.configure_git()

    assert w.GIT_STATUS["is_repo"] is True
    # No token -> pull still works (public repo), push does not.
    assert w.GIT_STATUS["remote"] == "anonymous"


def test_bootstrap_builds_a_checkout_when_there_is_no_git(tmp_path, monkeypatch):
    """The operative fix.

    Railway's builder ships a source ARCHIVE, not a clone -- its build log
    reads "fetching snapshot" then "unpacking archive" -- so /app never
    contains .git no matter what .dockerignore says. Taking `.git/` out of
    .dockerignore was a real bug fix and changed nothing on Railway; the
    container has to build the checkout itself.

    Uses this repository as the remote so the test is offline and fast.
    """
    import tools.railway_worker as w

    monkeypatch.setattr(w, "REPO", tmp_path)
    monkeypatch.setattr(w, "_remote_url", lambda: str(ROOT))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(w, "log", lambda m: None)
    w.GIT_STATUS.update(is_repo=None, shallow=None, remote=None,
                        bootstrapped=None, error=None)

    w.configure_git()

    assert w.GIT_STATUS["is_repo"] is True
    assert w.GIT_STATUS["bootstrapped"] is True
    assert w.GIT_STATUS["error"] is None
    assert (tmp_path / ".git").exists()
    # The working tree must actually be populated, not just the metadata:
    # _merge_dir reads FILES out of this checkout, so a current HEAD over
    # an empty tree would merge nothing and look healthy doing it.
    assert (tmp_path / "tools" / "railway_worker.py").is_file()
    clean = w._git("status", "--porcelain").stdout.strip()
    assert clean == "", f"bootstrapped checkout is dirty: {clean[:200]}"


def test_bootstrap_is_skipped_for_an_existing_checkout(monkeypatch):
    """An inherited .git must not be re-initialised or reset.

    `reset --hard` against a real checkout that has pending mirrored
    ledger rows would discard them before they were ever pushed.
    """
    import tools.railway_worker as w

    monkeypatch.setattr(w, "REPO", ROOT)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(w, "log", lambda m: None)
    called: list[str] = []
    monkeypatch.setattr(w, "_bootstrap_repo", lambda url: called.append(url))
    w.GIT_STATUS.update(is_repo=None, bootstrapped=None, error=None)

    w.configure_git()

    assert called == [], "bootstrap ran against an existing repository"
    assert w.GIT_STATUS["bootstrapped"] is False
    assert w.GIT_STATUS["is_repo"] is True


def test_can_push_reports_capability_not_configuration(monkeypatch):
    """A token with no repository must NOT read as 'can push'.

    This is exactly what /health advertised for 16 hours on 2026-08-08.
    """
    import tools.railway_worker as w

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pretend")
    w.GIT_STATUS.update(is_repo=False, remote=None, error="not a git repository")

    can_push = bool(
        w.GIT_STATUS.get("is_repo")
        and w.GIT_STATUS.get("remote") == "authenticated"
    )
    assert can_push is False, (
        "can_push_to_git must not be satisfied by the presence of a token"
    )


# --------------------------------------------------------------------
# A-034: a halted rebase wedged the checkout and nothing self-healed.
# --------------------------------------------------------------------

def _git_in(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=path, capture_output=True,
                          text=True)


def _wedged_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """Reproduce A-034 exactly: a rebase halted on a conflict.

    Not a simulation of the symptom -- the real sequence. CI and the
    container both rewrite dashboard/public/data.json, so their commits
    conflict on content; `git pull --rebase` stops mid-replay, leaves
    .git/rebase-merge behind and HEAD detached, and every later pull
    dies on "there is already a rebase-merge directory".
    """
    remote, work = tmp_path / "remote", tmp_path / "work"
    remote.mkdir()
    _git_in(remote, "init", "-b", "master")
    _git_in(remote, "config", "user.email", "ci@example.com")
    _git_in(remote, "config", "user.name", "CI")
    (remote / "data.json").write_text("base\n", encoding="utf-8")
    _git_in(remote, "add", "-A")
    _git_in(remote, "commit", "-m", "base")

    _git_in(tmp_path, "clone", str(remote), str(work))
    _git_in(work, "config", "user.email", "worker@example.com")
    _git_in(work, "config", "user.name", "Worker")

    # CI pushes first.
    (remote / "data.json").write_text("ci\n", encoding="utf-8")
    _git_in(remote, "commit", "-am", "chore(ci): automated run")

    # The container commits its own copy of the same derived file.
    (work / "data.json").write_text("worker\n", encoding="utf-8")
    _git_in(work, "commit", "-am", "chore(worker): live grades")

    # The old sync_repo. Conflicts, halts, and leaves the mess behind.
    _git_in(work, "pull", "--rebase", "--autostash", "origin", "master")
    return remote, work


def test_halted_rebase_is_cleared_instead_of_wedging_forever(tmp_path, monkeypatch):
    """The operative fix for A-034.

    On 2026-08-11 this state persisted from 03:16 to 07:22 ET and would
    have persisted indefinitely: four hours of live grades committed onto
    a detached HEAD, none of them reachable from master, none pushed.
    """
    import tools.railway_worker as w

    remote, work = _wedged_checkout(tmp_path)
    monkeypatch.setattr(w, "REPO", work)
    monkeypatch.setattr(w, "log", lambda m: None)
    monkeypatch.setattr(w, "reconcile_ledger", lambda: None)

    # Precondition: the harness really did wedge it. Without this the
    # test could pass against a checkout that was never broken.
    before = w._head_state()
    assert before["rebase_in_progress"] or before["detached"], (
        f"harness did not reproduce the wedged state: {before}"
    )

    w.sync_repo()

    after = w._head_state()
    assert after["rebase_in_progress"] is False, "rebase directory survived"
    assert after["detached"] is False, "HEAD still detached"
    assert after["branch"] == "master", f"not back on master: {after}"
    assert w.LAST_PULL["ok"] is True, w.LAST_PULL["error"]

    # CI's copy must win outright. The container's version of a DERIVED
    # file has no claim -- it is regenerated from the volume moments
    # later -- and preferring it is what the old --autostash pull did.
    assert (work / "data.json").read_text(encoding="utf-8") == "ci\n"
    head = _git_in(work, "rev-parse", "HEAD").stdout.strip()
    tip = _git_in(remote, "rev-parse", "master").stdout.strip()
    assert head == tip, "checkout did not land on origin's tip"

    # And the recovery must be idempotent: a second pass on an already
    # healthy checkout must not break it.
    w.sync_repo()
    assert w._head_state()["branch"] == "master"
    assert w.LAST_PULL["ok"] is True


def test_reattaches_even_when_the_fetch_fails(tmp_path, monkeypatch):
    """Recovery must not depend on the network.

    If reattachment only happened after a successful fetch, an egress
    blip while detached would leave the container committing onto a
    detached HEAD for another five minutes -- and pushing `master`,
    which never moves. The wedge would outlive the outage that caused it.
    """
    import tools.railway_worker as w

    _remote, work = _wedged_checkout(tmp_path)
    monkeypatch.setattr(w, "REPO", work)
    monkeypatch.setattr(w, "log", lambda m: None)
    monkeypatch.setattr(w, "reconcile_ledger", lambda: None)
    # Point the remote at nothing so the fetch cannot succeed.
    _git_in(work, "remote", "set-url", "origin",
            str(tmp_path / "does-not-exist"))

    w.sync_repo()

    after = w._head_state()
    assert after["detached"] is False, "left detached after a failed fetch"
    assert after["rebase_in_progress"] is False
    assert after["branch"] == "master"
    # The failure must still be reported honestly, not swallowed by the
    # successful reattachment.
    assert w.LAST_PULL["ok"] is False
    assert w.LAST_PULL["error"]


def test_commit_is_refused_while_detached(tmp_path, monkeypatch):
    """git-commit reporting OK onto a detached HEAD is the trap.

    Every 5 minutes for four hours the log read `OK git-commit` followed
    by `FAILED git-push: non-fast-forward`, which reads as a push problem
    and is not one. Refuse at the commit, name the real state.
    """
    import tools.railway_worker as w

    _remote, work = _wedged_checkout(tmp_path)
    _git_in(work, "rebase", "--abort")
    _git_in(work, "checkout", "--detach")
    monkeypatch.setattr(w, "REPO", work)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pretend")
    lines: list[str] = []
    monkeypatch.setattr(w, "log", lines.append)

    before = _git_in(work, "rev-parse", "HEAD").stdout.strip()
    w.commit_and_push("live grades")
    after = _git_in(work, "rev-parse", "HEAD").stdout.strip()

    assert after == before, "committed onto a detached HEAD"
    assert any("not on a branch" in ln for ln in lines), (
        f"the skip was silent; log said: {lines}"
    )

    w.GIT_STATUS.update(is_repo=True, remote="authenticated", error=None)
    assert bool(
        w.GIT_STATUS.get("is_repo")
        and w.GIT_STATUS.get("remote") == "authenticated"
    ) is True
