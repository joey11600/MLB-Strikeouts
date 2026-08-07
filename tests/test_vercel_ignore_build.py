"""Regression tests for the Vercel build-skip decision (AUDIT A-023).

`scripts/vercel-ignore-build.sh` decides whether a push rebuilds the
site. Getting it wrong in one direction wastes a couple of minutes.
Getting it wrong in the OTHER direction ships code that never reaches
production, with no failed build to notice -- Vercel just records a
CANCELED deployment that looks identical to the healthy case.

The load-bearing case is `test_code_underneath_a_data_commit_still_builds`.
The obvious implementation of this script diffs HEAD^ against HEAD, which
is correct only when a push carries exactly one commit. It does not here:
`tools/odds_relay.py` tells the operator to run a bare `git push origin
master`, and the odds commit it creates is data-only, so any unpushed code
commit rides underneath it and would be skipped straight past.

Run:  python -m pytest tests/test_vercel_ignore_build.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "vercel-ignore-build.sh"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or not SCRIPT.exists(),
    reason="needs bash and scripts/vercel-ignore-build.sh",
)

SKIP, BUILD = 0, 1


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    return r.stdout.strip()


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")
    _commit(r, "initial", {
        "dashboard/app/page.tsx": "export default function P() { return null }\n",
        "dashboard/public/data.json": '{"generated_at":"t0"}\n',
        "data/model_log.csv": "date,pitcher_id\n",
    })
    return r


def _decide(repo: Path, base: str | None) -> tuple[int, str]:
    """Run the real script the way Vercel runs it."""
    env = dict(os.environ)
    env.pop("VERCEL_GIT_PREVIOUS_SHA", None)
    if base is not None:
        env["VERCEL_GIT_PREVIOUS_SHA"] = base
    r = subprocess.run([BASH, str(SCRIPT)], cwd=str(repo), env=env,
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def test_data_only_since_last_build_skips(repo: Path):
    """The whole point: an automated ledger commit must not rebuild."""
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "chore(ci): automated run", {
        "dashboard/public/data.json": '{"generated_at":"t1"}\n',
        "data/model_log.csv": "date,pitcher_id\n2026-08-07,1\n",
    })
    code, out = _decide(repo, base)
    assert code == SKIP, out


def test_code_change_builds(repo: Path):
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feat: something real", {
        "dashboard/app/page.tsx": "export default function P() { return <div/> }\n",
    })
    code, out = _decide(repo, base)
    assert code == BUILD, out


def test_code_underneath_a_data_commit_still_builds(repo: Path):
    """THE ONE THAT MATTERS.

    A single push carrying [code, data] with the data commit on top. A
    HEAD^..HEAD comparison sees only the data commit and skips -- and the
    code silently never ships. Comparing against the last BUILT commit
    catches it.
    """
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feat: real code, pushed underneath", {
        "dashboard/app/page.tsx": "export default function P() { return <span/> }\n",
    })
    _commit(repo, "chore(odds): relay snapshot", {
        "data/odds/dk_2026-08-07.csv": "pitcher,line\n",
    })

    # Sanity: the naive rule really would have skipped this.
    naive = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", "HEAD^", "HEAD", "--",
         ":(exclude)data", ":(exclude)dashboard/public/data.json"],
    ).returncode
    assert naive == 0, "expected the naive HEAD^ rule to wrongly see data-only"

    code, out = _decide(repo, base)
    assert code == BUILD, f"code change was skipped past — {out}"


def test_no_baseline_builds(repo: Path):
    """Cannot tell what is live -> build. Never skip on uncertainty."""
    _commit(repo, "chore(ci): automated run", {
        "dashboard/public/data.json": '{"generated_at":"t1"}\n',
    })
    code, out = _decide(repo, None)
    assert code == BUILD, out


def test_unreachable_baseline_builds(repo: Path):
    """A baseline outside the shallow clone is still uncertainty."""
    _commit(repo, "chore(ci): automated run", {
        "dashboard/public/data.json": '{"generated_at":"t1"}\n',
    })
    code, out = _decide(repo, "0" * 40)
    assert code == BUILD, out


def test_redeploy_of_same_commit_builds(repo: Path):
    """Operator clicked Redeploy. Honour it rather than silently no-op."""
    head = _git(repo, "rev-parse", "HEAD")
    code, out = _decide(repo, head)
    assert code == BUILD, out
