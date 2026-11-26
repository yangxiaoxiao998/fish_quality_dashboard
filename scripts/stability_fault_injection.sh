#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BASE_URL="http://127.0.0.1:${BACKEND_PORT}"
PYTHON="${PYTHON:-python}"

case "${1:-}" in
    preflight)
        exec "$PYTHON" "$PROJECT_ROOT/backend/tools/diagnose_stability.py"
        ;;
    status)
        curl --noproxy '*' --fail --silent "${BASE_URL}/api/camera_yzt/status"
        printf '\n'
        ;;
    restart-diagnostics)
        exec "$PYTHON" "$PROJECT_ROOT/backend/tools/diagnose_backend_start.py"
        ;;
    timed-recording)
        cat <<'EOF'
用新录制的 video_id 验收：开始短时任务，确认 timed_recording=true，
到点后同一 video_id 进入 queued/processing/done 或 failed，并检查播放和报告。
EOF
        ;;
    disconnect-sim)
        cat <<'EOF'
现场断流验收：开始新录制，临时拔电/断网，确认 state=reconnecting、video_id 不变、
reconnect_remaining_seconds 递减；恢复后 state=running 且 frames_written 继续增长。
超时后才允许 failed。检查 pi_resolution、pi_arp、event_history。
EOF
        ;;
    *)
        echo "用法: $0 {preflight|status|restart-diagnostics|timed-recording|disconnect-sim}"
        exit 2
        ;;
esac
