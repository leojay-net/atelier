#!/bin/sh
# api     migrate the database, then serve the API (ASGI) on :8000
# worker  run queued jobs until stopped (start as many as the machine can use)
# manage  pass the rest to manage.py (create_org, create_api_key, createsuperuser…)
set -eu
cd /app
case "${1:-api}" in
  api)
    uv run --frozen python manage.py migrate --noinput
    exec uv run --frozen uvicorn atelier.api.asgi:application --host 0.0.0.0 --port 8000 \
      --no-server-header --proxy-headers
    ;;
  worker)
    exec uv run --frozen python manage.py run_worker
    ;;
  manage)
    shift
    exec uv run --frozen python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
