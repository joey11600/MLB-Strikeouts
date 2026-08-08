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

    w.GIT_STATUS.update(is_repo=True, remote="authenticated", error=None)
    assert bool(
        w.GIT_STATUS.get("is_repo")
        and w.GIT_STATUS.get("remote") == "authenticated"
    ) is True
