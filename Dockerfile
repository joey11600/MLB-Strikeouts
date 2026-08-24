# Railway worker image: runs the daily pipeline on an ET-aware schedule.
#
# The Statcast cache lives on a Railway volume (STATCAST_CACHE_DIR), so
# it survives deploys and never re-downloads. Ledger changes are pushed
# back to GitHub, which is the durable source of truth for picks.
FROM python:3.13-slim

# git: the worker commits the ledger back to the repo.
# tzdata: zoneinfo needs the Olson database for America/New_York (DST).
# tini: an init that reaps orphans. python ran as PID 1 and reaps
#   nothing, so every orphaned grandchild (git helpers, children of
#   timed-out jobs) stayed a zombie holding a process slot; the
#   container crossed its fork ceiling after ~44 h and every fetch
#   after that failed EAGAIN for two days (A-045).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git tzdata ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Volume mount point (Railway attaches the persistent disk here).
# DATA_STATE_DIR must be a REAL directory on the volume — the ledger's
# atomic writes replace destination paths, so symlinks don't survive.
ENV STATCAST_CACHE_DIR=/data/statcast_cache
ENV DATA_STATE_DIR=/data/state
ENV PYTHONUNBUFFERED=1
# Every spawned python otherwise starts one BLAS thread per host CPU,
# and Railway hosts are large. Those threads count against the same
# task ceiling as processes — numpy imports were the first casualty of
# A-045 precisely because they claim the most slots in one shot.
ENV OPENBLAS_NUM_THREADS=4
ENV OMP_NUM_THREADS=4

# tini is PID 1; the worker is its only direct child (A-045).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "tools/railway_worker.py"]
