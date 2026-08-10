#!/usr/bin/env bash
# Vercel "Ignored Build Step" — decide whether this push needs a rebuild.
#
#   exit 0  -> SKIP the build
#   exit 1  -> BUILD
#
# Why this exists
# ---------------
# The pipeline commits data every time it runs: worker_state.json, the
# slate sidecar, model_log.csv and dashboard/public/data.json. At up to
# 48 runs a day that was 48 full Next.js builds a day, and the strikeouts
# project alone burned 78 CPU-hours in one billing cycle -- each build
# runs on a 30-core machine, so ~1.5 minutes of wall clock bills as ~45
# CPU-minutes. Those figures are pre-A-033: most of that 1.5 minutes was
# Vercel installing the root requirements.txt and compiling numpy and
# pandas from source for a site that runs no Python. `installCommand` in
# vercel.json now suppresses it and the build proper is ~23 s.
#
# Almost all of it was waste, because the dashboard does not READ the
# committed data.json in normal operation. dashboard/lib/data-context.tsx
# fetches live from the Railway worker and only falls back to the static
# copy if that fetch fails. So rebuilding the entire site to refresh a
# fallback that is overridden at runtime bought nothing.
#
# Compare against the last BUILD, not the previous commit
# -------------------------------------------------------
# The obvious implementation -- diff HEAD^ against HEAD -- is wrong, and
# wrong in the expensive direction.
#
# One push can carry several commits. If a code commit sits underneath a
# data commit in the same push, HEAD^..HEAD sees only the data commit,
# the build is skipped, and the code silently never reaches the live
# site. Nothing turns red. There is no failed build to notice -- just a
# CANCELED deployment that looks exactly like the healthy case.
#
# That shape is reachable here, not hypothetical: tools/odds_relay.py
# `_publish_hint()` tells the operator to run a bare `git push origin
# master`, which ships every unpushed commit, and the odds snapshot
# commit it creates touches only data/odds -- so it lands on top.
#
# VERCEL_GIT_PREVIOUS_SHA is "the SHA of the last successful deployment;
# only available when an Ignored Build Step is configured" (Vercel docs),
# which this project now is. A skipped build is not a deployment, so the
# value stays pinned to the last commit whose code is actually LIVE. That
# is exactly the right baseline: consecutive data commits each compare
# against real live code, and a code commit anywhere in the gap is caught.
#
# Fails toward BUILDING. No baseline, an unreachable baseline, a manual
# redeploy -- all build. A needless build costs a couple of minutes; a
# wrongly skipped one ships stale code and nobody finds out.
set -u

DATA_ONLY_PATHS=(
  ':(exclude)data'
  ':(exclude)dashboard/public/data.json'
)

BASE="${VERCEL_GIT_PREVIOUS_SHA:-}"

if [ -z "$BASE" ]; then
  # First run after wiring this up, or Vercel did not supply it. We cannot
  # know what is already live, so we cannot safely skip.
  echo "VERCEL_GIT_PREVIOUS_SHA not set — no way to know what is already live, building"
  exit 1
fi

if [ "$BASE" = "$(git rev-parse HEAD)" ]; then
  # Same commit as the last deploy: a manual redeploy. They asked for it.
  echo "redeploy of the same commit ($BASE) — building"
  exit 1
fi

# Vercel clones shallow (~10 commits). The worker pushes a data commit
# every 5 minutes, so the last BUILT commit leaves that window in under
# an hour. The first version of this reach-back did not work, and it
# failed silently: every fetch went to /dev/null, so the only evidence
# was the "not reachable" line below firing about twice every 90
# minutes -- ~30 needless 30-core builds a day (A-033).
#
# THERE IS NO REMOTE NAMED origin. Measured, not assumed: the build at
# 2026-08-10 14:58 UTC printed, three times,
#
#     fetch[deepen]: failed — fatal: 'origin' does not appear to be a
#     git repository
#
# Vercel's build container has the objects and refs but no configured
# remote, so every `git fetch ... origin ...` was dead on arrival. That
# is the whole reason the original reach-back never worked -- not
# GitHub's SHA-fetch policy, not credentials, which is what the first
# pass at A-033 guessed. The guess only fell over because the failures
# are now printed instead of sent to /dev/null.
#
# So: use whatever remote the clone actually has, and if it has none,
# rebuild the provider URL from the VERCEL_GIT_* variables. The repo is
# public, so no credential is involved; a private repo would fail here
# and fall through to BUILDING, which is the safe direction to be wrong.
#
# The depth is also no longer a fixed number. Now that the skip holds,
# builds are rare and the gap back to the last one grows without bound,
# so any constant would eventually be too small. --unshallow has no
# such ceiling and the whole repo is ~20 MB.
have_base() { git cat-file -e "${BASE}^{commit}" 2>/dev/null; }

try_fetch() {
  label="$1"
  shift
  if out=$("$@" 2>&1); then
    echo "  fetch[$label]: ok"
  else
    # First line, not last: git's closing lines are the generic "make
    # sure you have the correct access rights" boilerplate, while the
    # actual fatal is line 1.
    echo "  fetch[$label]: failed — $(printf '%s' "$out" | head -1)"
  fi
}

if ! have_base; then
  # Print what the clone really has, so the next surprise is one log
  # line away rather than another billing cycle.
  echo "  remotes configured: [$(git remote | tr '\n' ' ' | sed 's/ $//')]"
  REMOTE="$(git remote | head -1)"
  if [ -z "$REMOTE" ] \
     && [ -n "${VERCEL_GIT_REPO_OWNER:-}" ] \
     && [ -n "${VERCEL_GIT_REPO_SLUG:-}" ]; then
    case "${VERCEL_GIT_PROVIDER:-github}" in
      github) REMOTE="https://github.com/${VERCEL_GIT_REPO_OWNER}/${VERCEL_GIT_REPO_SLUG}.git" ;;
      gitlab) REMOTE="https://gitlab.com/${VERCEL_GIT_REPO_OWNER}/${VERCEL_GIT_REPO_SLUG}.git" ;;
      *)      REMOTE="" ;;
    esac
  fi
  echo "  using remote: ${REMOTE:-<none available>}"
fi

if [ -n "${REMOTE:-}" ]; then
  if ! have_base; then
    # Cheap first: extend the shallow window along the branch we are on.
    try_fetch deepen git fetch --deepen=500 "$REMOTE" "${VERCEL_GIT_COMMIT_REF:-master}"
  fi

  if ! have_base; then
    # No ceiling. Errors harmlessly if the clone is already complete.
    try_fetch unshallow git fetch --unshallow "$REMOTE"
  fi

  if ! have_base; then
    # Still missing: BASE was force-pushed away or is off-branch. Ask
    # for it by name. GitHub does serve reachable SHAs this way.
    try_fetch by-sha git fetch --depth=1 "$REMOTE" "$BASE"
  fi
fi

if ! have_base; then
  echo "last built commit $BASE is not reachable in this clone — building"
  exit 1
fi

if git diff --quiet "$BASE" HEAD -- "${DATA_ONLY_PATHS[@]}" 2>/dev/null; then
  echo "only data has changed since the last build ($BASE) — skipping build"
  exit 0
fi

echo "code has changed since the last build ($BASE) — building"
git diff --name-only "$BASE" HEAD -- "${DATA_ONLY_PATHS[@]}" 2>/dev/null | head -20
exit 1
