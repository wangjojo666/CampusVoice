#!/usr/bin/env bash
set -Eeuo pipefail

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
readonly run_suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-$$}"
readonly project_name="${CAMPUSVOICE_SMOKE_PROJECT:-campusvoice-smoke-${run_suffix}}"
readonly compose_file_input="${CAMPUSVOICE_COMPOSE_FILE:-docker-compose.yml}"
readonly smoke_file_input="${CAMPUSVOICE_SMOKE_COMPOSE_FILE:-docker-compose.smoke.yml}"
readonly extra_compose_file_input="${CAMPUSVOICE_EXTRA_COMPOSE_FILE-docker-compose.multi-worker.yml}"
readonly compose_profile="${CAMPUSVOICE_SMOKE_PROFILE-multi-worker}"
readonly web_url="${CAMPUSVOICE_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
readonly api_url="${CAMPUSVOICE_SMOKE_API_URL:-http://127.0.0.1:8000}"

resolve_repo_path() {
  local path="$1"
  if [[ "${path}" = /* ]]; then
    printf '%s' "${path}"
  else
    printf '%s/%s' "${repo_root}" "${path}"
  fi
}

readonly compose_file="$(resolve_repo_path "${compose_file_input}")"
readonly smoke_file="$(resolve_repo_path "${smoke_file_input}")"
extra_compose_file=""
if [[ -n "${extra_compose_file_input}" ]]; then
  extra_compose_file="$(resolve_repo_path "${extra_compose_file_input}")"
fi
readonly extra_compose_file

# The smoke stack must never inherit production credentials, network bindings, or data services.
while IFS= read -r name; do
  unset "${name}"
done < <(compgen -e | grep -E '^(CAMPUSVOICE_|NEXT_PUBLIC_|COMPOSE_)' || true)
export COMPOSE_DISABLE_ENV_FILE=1
export CAMPUSVOICE_BIND_HOST=127.0.0.1
export CAMPUSVOICE_DATABASE_URL='sqlite+aiosqlite:///./campusvoice.db'
export CAMPUSVOICE_LOG_LEVEL=INFO
export CAMPUSVOICE_ASR_WORKER_COUNT="$([[ -n "${extra_compose_file}" ]] && printf '2' || printf '1')"
export CAMPUSVOICE_ASR_REDIS_URL='redis://redis:6379/0'
export CAMPUSVOICE_ASR_REDIS_KEY_PREFIX="campusvoice:smoke:${project_name}:asr:quota"
export CAMPUSVOICE_SMOKE_WEB_URL="${web_url}"
export CAMPUSVOICE_SMOKE_API_URL="${api_url}"

compose=(
  docker compose
  --project-name "${project_name}"
  --file "${compose_file}"
  --file "${smoke_file}"
)
if [[ -n "${extra_compose_file}" ]]; then
  compose+=(--file "${extra_compose_file}")
fi
if [[ -n "${compose_profile}" ]]; then
  compose+=(--profile "${compose_profile}")
fi

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

cd -- "${repo_root}"
"${compose[@]}" config --quiet
"${compose[@]}" up --detach --build --wait --wait-timeout 300

before_container_id="$("${compose[@]}" ps -q api)"
if [[ -z "${before_container_id}" ]]; then
  echo "Compose smoke could not resolve the initial API container id." >&2
  exit 1
fi
before_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}}|{{.Name}}{{end}}{{end}}' "${before_container_id}")"
if [[ "${before_mount}" != volume\|* ]]; then
  echo "API /data mount is not a Docker volume: ${before_mount:-missing}" >&2
  exit 1
fi
before_volume_name="${before_mount#volume|}"
before_volume_labels="$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.volume"}}' "${before_volume_name}")"
if [[ "${before_volume_labels}" != "${project_name}|campusvoice_smoke_data" ]]; then
  echo "API /data volume is not the Compose-managed smoke volume: ${before_volume_labels}" >&2
  exit 1
fi

sentinel_action_id="$(node "${repo_root}/scripts/check_compose_persistence.mjs" create "${api_url}")"
if [[ -z "${sentinel_action_id}" ]]; then
  echo "Compose persistence sentinel creation returned an empty action id." >&2
  exit 1
fi
"${compose[@]}" exec -T api sh -c \
  "mkdir -p /data/app && printf '%s\n' 'raise RuntimeError(\"untrusted /data/app imported\")' > /data/app/__init__.py && printf '%s\n' 'CAMPUSVOICE_API_PREFIX=/untrusted' > /data/.env"

"${compose[@]}" up --detach --force-recreate --no-deps --wait --wait-timeout 300 api
after_container_id="$("${compose[@]}" ps -q api)"
if [[ -z "${after_container_id}" || "${after_container_id}" == "${before_container_id}" ]]; then
  echo "API container was not replaced during the persistence check." >&2
  exit 1
fi
after_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}}|{{.Name}}{{end}}{{end}}' "${after_container_id}")"
if [[ "${after_mount}" != "${before_mount}" ]]; then
  echo "API /data volume changed during container recreation." >&2
  exit 1
fi

node "${repo_root}/scripts/check_compose_persistence.mjs" verify "${api_url}" "${sentinel_action_id}"
pnpm --filter @campusvoice/web test:e2e:smoke
