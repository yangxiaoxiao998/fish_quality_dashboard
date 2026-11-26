#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BASE_URL="http://127.0.0.1:${BACKEND_PORT}"
WAIT_SECONDS="${RECOVERY_WAIT_SECONDS:-90}"
PYTHON="${PYTHON:-python}"

PYTHONPATH="$PROJECT_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$PROJECT_ROOT/backend/tools/diagnose_stability.py"
curl --noproxy '*' --fail --silent "${BASE_URL}/api/ready"
printf '\n按现场顺序重启并观察同一新 video_id：\n'
printf '%s\n' \
  '  systemctl --user restart fish-quality-backend.service' \
  '  systemctl --user restart fish-quality-worker.service' \
  '  systemctl --user restart fish-quality-mediamtx.service'
deadline=$((SECONDS + WAIT_SECONDS))
while (( SECONDS < deadline )); do
    if curl --noproxy '*' --fail --silent "${BASE_URL}/api/ready" >/dev/null; then
        curl --noproxy '*' --fail --silent "${BASE_URL}/api/camera_yzt/status"
        printf '\n'
        exit 0
    fi
    sleep 2
echo "[ERROR] backend did not recover within ${WAIT_SECONDS}s" >&2
exit 1
