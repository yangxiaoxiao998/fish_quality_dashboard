#!/usr/bin/env bash
set -Eeuo pipefail

RELEASE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_STOP="$RELEASE_ROOT/backend/stop_backend.sh"
if [[ -x "$BACKEND_STOP" ]]; then "$BACKEND_STOP"; fi
pkill -TERM -f '[/]fish-quality-desktop' 2>/dev/null || true
for _ in {1..30}; do
    pgrep -x 'fish-quality-de' >/dev/null 2>&1 || break
    sleep 0.1
done
pkill -KILL -x 'fish-quality-de' 2>/dev/null || true
echo "[OK] 鱼品质智能检测系统已停止。"
