#!/bin/sh
# Container entrypoint: migrate, optionally backfill an empty DB in the
# background, then serve.
set -e
alembic upgrade head
if [ "${AUTO_BACKFILL:-false}" = "true" ]; then
  python -m scripts.backfill --if-empty &
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
