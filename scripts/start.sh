#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set."
  echo "On Railway, add a pgvector/Postgres service and set DATABASE_URL on the web service before deploying."
  exit 1
fi

alembic upgrade head
exec uvicorn src.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
