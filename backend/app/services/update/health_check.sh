#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd -P)"
COMMON_SH=""
for candidate in \
  "$SCRIPT_DIR/lib/common.sh" \
  "$PROJECT_ROOT/backend/app/services/update/lib/common.sh"
do
  if [[ -f "$candidate" ]]; then
    COMMON_SH="$candidate"
    break
  fi
done
if [[ -z "$COMMON_SH" ]]; then
  printf '[error] health_check.sh could not find lib/common.sh relative to %s\n' "$SCRIPT_DIR" >&2
  exit 1
fi
# shellcheck source=update/lib/common.sh
source "$COMMON_SH"

INSTALL_ROOT="${FISH_INSTALL_ROOT:-}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PYTHON_BIN="${FISH_UPDATE_PYTHON_BIN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root)
      INSTALL_ROOT="${2:?missing value for --install-root}"
      shift 2
      ;;
    --backend-port)
      BACKEND_PORT="${2:?missing value for --backend-port}"
      shift 2
      ;;
    --frontend-port)
      FRONTEND_PORT="${2:?missing value for --frontend-port}"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="${2:?missing value for --python-bin}"
      shift 2
      ;;
    *)
      fish_die "unknown argument: $1"
      ;;
  esac
done

if [[ -z "$INSTALL_ROOT" ]]; then
  INSTALL_ROOT="$PROJECT_ROOT"
fi
INSTALL_ROOT="$(fish_realpath "$INSTALL_ROOT")"

if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(fish_python_bin)"
fi

if [[ -f "$INSTALL_ROOT/backend/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$INSTALL_ROOT/backend/.env"
  set +a
fi
if [[ -f "$INSTALL_ROOT/backend/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$INSTALL_ROOT/backend/.env.local"
  set +a
fi

BACKEND_HEALTH_URL="http://127.0.0.1:${BACKEND_PORT}/api/health"
DB_HEALTH_URL="http://127.0.0.1:${BACKEND_PORT}/api/health/database"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}/"
WORKER_PID_FILE="$INSTALL_ROOT/data/pids/backend_worker.pid"
MEDIAMTX_PID_FILE="$INSTALL_ROOT/data/pids/mediamtx.pid"

fish_require_command curl

fetch_json_payload() {
  local url="$1"
  local label="$2"
  local payload=""
  if ! payload="$(curl -fsS "$url")"; then
    fish_die "$label is not reachable: $url"
  fi
  printf '%s' "$payload"
}

fish_log "[health] backend=$BACKEND_HEALTH_URL"
backend_payload="$(fetch_json_payload "$BACKEND_HEALTH_URL" "backend health endpoint")"
HEALTHCHECK_JSON_PAYLOAD="$backend_payload" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ.get("HEALTHCHECK_JSON_PAYLOAD") or "{}")
if payload.get("status") != "ok" and payload.get("ok") is not True:
    raise SystemExit("backend /api/health did not return ok")
PY

fish_log "[health] database=$DB_HEALTH_URL"
db_payload="$(fetch_json_payload "$DB_HEALTH_URL" "database health endpoint")"
HEALTHCHECK_JSON_PAYLOAD="$db_payload" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ.get("HEALTHCHECK_JSON_PAYLOAD") or "{}")
if payload.get("connected") is not True:
    raise SystemExit("database health check reports connected=false")
PY

if [[ -n "${REDIS_URL:-}" ]]; then
  fish_log "[health] redis=${REDIS_URL}"
  "$PYTHON_BIN" - <<'PY'
import os

import redis

url = os.environ["REDIS_URL"]
client = redis.Redis.from_url(url)
if client.ping() is not True:
    raise SystemExit("redis ping failed")
PY
else
  fish_warn "REDIS_URL is not set; skip redis ping"
fi

fish_log "[health] frontend=$FRONTEND_URL"
if ! curl -fsS "$FRONTEND_URL" >/dev/null; then
  fish_die "frontend is not reachable: $FRONTEND_URL"
fi

if [[ ! -f "$WORKER_PID_FILE" ]]; then
  fish_die "worker pid file not found: $WORKER_PID_FILE"
fi
worker_pid="$(tr -d '[:space:]' < "$WORKER_PID_FILE" 2>/dev/null || true)"
if [[ -z "$worker_pid" ]] || ! kill -0 "$worker_pid" 2>/dev/null; then
  fish_die "worker process is not running"
fi
if [[ -f "/proc/$worker_pid/cmdline" ]]; then
  worker_cmdline="$(tr '\0' ' ' < "/proc/$worker_pid/cmdline" 2>/dev/null || true)"
  if [[ "$worker_cmdline" != *"app.workers.worker_main"* ]]; then
    fish_die "worker pid file points to an unexpected process: $worker_cmdline"
  fi
fi

if [[ -f "$MEDIAMTX_PID_FILE" ]]; then
  mediamtx_pid="$(tr -d '[:space:]' < "$MEDIAMTX_PID_FILE" 2>/dev/null || true)"
  if [[ -n "$mediamtx_pid" ]]; then
    if ! kill -0 "$mediamtx_pid" 2>/dev/null; then
      fish_die "mediamtx pid file exists but process is not running"
    fi
    if command -v ss >/dev/null 2>&1; then
      if ! ss -ltnp 2>/dev/null | grep -q ':1935'; then
        fish_warn "mediamtx pid exists but port 1935 is not listening"
      fi
    fi
  fi
fi

fish_log "[health] all checks passed"
