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
# CPU-minutes.
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

# Vercel clones shallow. After a run of skipped data commits the last
# built commit can fall outside that window, so reach back for it.
if ! git cat-file -e "${BASE}^{commit}" 2>/dev/null; then
  git fetch --depth=250 origin "$BASE" >/dev/null 2>&1 \
    || git fetch --deepen=250 >/dev/null 2>&1 \
    || true
fi

if ! git cat-file -e "${BASE}^{commit}" 2>/dev/null; then
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
