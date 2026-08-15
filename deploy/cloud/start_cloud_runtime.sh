#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${AI_QUANT_PYTHON:-${ROOT}/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  printf 'AI_QUANT_PYTHON is not executable: %s\n' "$PYTHON_BIN" >&2
  exit 64
fi

if [[ -z "${POSTGRES_URL:-}" ]]; then
  printf 'POSTGRES_URL must be set to the persistent SQLite URL.\n' >&2
  exit 64
fi

if [[ -z "${LOCAL_SCHEDULER_STATE_PATH:-}" ]]; then
  printf 'LOCAL_SCHEDULER_STATE_PATH must be set to persistent runtime state.\n' >&2
  exit 64
fi

export PAPER_CONSOLE_API_ONLY=true
export RUNTIME_SCHEDULER_AUTOSTART=false

cd "$ROOT"

PREFLIGHT_ARGS=()
if [[ "$ROLE" == "scheduler" && -n "${SCHEDULER_PID_PATH:-}" ]]; then
  PREFLIGHT_ARGS+=(--scheduler-pid-path "$SCHEDULER_PID_PATH")
fi
"$PYTHON_BIN" deploy/cloud/cloud_preflight.py "${PREFLIGHT_ARGS[@]}"

case "$ROLE" in
  api)
    exec "$PYTHON_BIN" -m apps.api.local_server \
      --host "${API_HOST:-127.0.0.1}" \
      --port "${API_PORT:-8016}" \
      --log-level "${API_LOG_LEVEL:-warning}" \
      --local-console
    ;;
  scheduler)
    exec "$PYTHON_BIN" scripts/run-local-paper-scheduler.py \
      --database-url "$POSTGRES_URL" \
      --engine "v2_active"
    ;;
  *)
    printf 'usage: %s {api|scheduler}\n' "$0" >&2
    exit 64
    ;;
esac
