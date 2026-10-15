#!/bin/sh
# Container entrypoint: migrate, backfill any source that has never succeeded
# (in the background, so a restart resumes where it left off), then serve.
set -e
alembic upgrade head
if [ "${AUTO_BACKFILL:-false}" = "true" ]; then
  python -m scripts.backfill --missing-only &
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
