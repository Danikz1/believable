#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set."
  echo "On Railway, add a pgvector/Postgres service and set DATABASE_URL on the web service before deploying."
  exit 1
fi

alembic upgrade head
python -c "from src.db.session import get_session; from src.db.seed import seed_people; s = get_session(); seed_people(s); s.commit(); print('Seed: people bios updated')"
exec uvicorn src.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
