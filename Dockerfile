# Railway worker image: runs the daily pipeline on an ET-aware schedule.
#
# The Statcast cache lives on a Railway volume (STATCAST_CACHE_DIR), so
# it survives deploys and never re-downloads. Ledger changes are pushed
# back to GitHub, which is the durable source of truth for picks.
FROM python:3.13-slim

# git: the worker commits the ledger back to the repo.
# tzdata: zoneinfo needs the Olson database for America/New_York (DST).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git tzdata ca-certificates \
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

CMD ["python", "tools/railway_worker.py"]
