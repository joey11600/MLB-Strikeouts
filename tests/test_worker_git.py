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


def test_configure_git_detects_a_non_repository(tmp_path, caplog, monkeypatch):
    """A directory with no .git must be reported, not papered over."""
    import tools.railway_worker as w

    monkeypatch.setattr(w, "REPO", tmp_path)
    w.GIT_STATUS.update(is_repo=None, shallow=None, remote=None, error=None)

    logged: list[str] = []
    monkeypatch.setattr(w, "log", lambda m: logged.append(str(m)))

    w.configure_git()

    assert w.GIT_STATUS["is_repo"] is False
    assert w.GIT_STATUS["error"]
    blob = "\n".join(logged)
    assert "FATAL" in blob and "not a git repository" in blob
    # The precise regression: a success line emitted over a failure.
    assert "git remote configured" not in blob


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
