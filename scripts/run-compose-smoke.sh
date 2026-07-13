#!/usr/bin/env bash
set -Eeuo pipefail

readonly project_name="${CAMPUSVOICE_SMOKE_PROJECT:-campusvoice-smoke}"
readonly compose_file="${CAMPUSVOICE_COMPOSE_FILE:-docker-compose.yml}"
readonly smoke_file="${CAMPUSVOICE_SMOKE_COMPOSE_FILE:-docker-compose.smoke.yml}"
compose=(docker compose --project-name "${project_name}" --file "${compose_file}" --file "${smoke_file}")

cleanup() {
  local exit_code=$?
  trap - EXIT
  if (( exit_code != 0 )); then
    "${compose[@]}" ps || true
    "${compose[@]}" logs --no-color || true
  fi
  "${compose[@]}" down --volumes --remove-orphans || true
  exit "${exit_code}"
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --detach --build --wait --wait-timeout 300

CAMPUSVOICE_SMOKE_WEB_URL="${CAMPUSVOICE_SMOKE_WEB_URL:-http://127.0.0.1:3000}" \
CAMPUSVOICE_SMOKE_API_URL="${CAMPUSVOICE_SMOKE_API_URL:-http://127.0.0.1:8000}" \
  pnpm --filter @campusvoice/web test:e2e:smoke
