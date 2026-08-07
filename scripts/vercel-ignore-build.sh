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
# project alone burned 78 CPU-hours in one billing cycle.
#
# Almost all of it was waste, because the dashboard does not READ the
# committed data.json in normal operation. dashboard/lib/data-context.tsx
# fetches live from the Railway worker and only falls back to the static
# copy if that fetch fails. So rebuilding the entire site to refresh a
# fallback that is overridden at runtime bought nothing.
#
# A data-only commit therefore skips the build. Any change to code,
# config, deps or content still builds normally.
#
# Fails toward BUILDING. If the diff cannot be computed — shallow clone
# with no parent, first commit, detached state — we build. A needless
# build costs a few minutes; a wrongly skipped one ships stale code and
# is much harder to notice.
set -u

DATA_ONLY_PATHS=(
  ':(exclude)data'
  ':(exclude)dashboard/public/data.json'
)

# No parent commit to compare against -> cannot tell -> build.
if ! git rev-parse --verify --quiet HEAD^ >/dev/null 2>&1; then
  echo "no parent commit available — building"
  exit 1
fi

if git diff --quiet HEAD^ HEAD -- "${DATA_ONLY_PATHS[@]}" 2>/dev/null; then
  echo "data-only commit — skipping build (the site reads live data from the worker)"
  exit 0
fi

echo "non-data changes present — building"
git diff --name-only HEAD^ HEAD -- "${DATA_ONLY_PATHS[@]}" | head -20
exit 1
