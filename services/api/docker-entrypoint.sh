#!/bin/sh
set -eu

python -m alembic -c /app/services/api/alembic.ini upgrade head
exec python -m uvicorn app.main:app \
  --app-dir /app/services/api \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${CAMPUSVOICE_ASR_WORKER_COUNT:-1}"
