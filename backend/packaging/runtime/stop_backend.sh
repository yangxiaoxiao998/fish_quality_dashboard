#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG_FILE="${FISH_ENV_FILE:-$INSTALL_ROOT/config/backend.env}"
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
fi
DATA_ROOT="${FISH_DATA_ROOT:-$INSTALL_ROOT/data}"
RUN_DIR="$DATA_ROOT/run"

stop_pid_file() {
    local name="$1" pid_file="$2" pid=""
    [[ -f "$pid_file" ]] || return 0
    pid="$(tr -d '[:space:]' <"$pid_file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        echo "[stop] $name PID=$pid"
        kill "$pid" 2>/dev/null || true
        for _ in {1..20}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.25
        done
        if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null || true; fi
    fi
    rm -f -- "$pid_file"
}

stop_pid_file "FastAPI" "$RUN_DIR/api.pid"
stop_pid_file "Upgrade Manager" "$RUN_DIR/upgrade-manager.pid"
stop_pid_file "RQ Worker" "$RUN_DIR/worker.pid"
stop_pid_file "MediaMTX" "$RUN_DIR/mediamtx.pid"
echo "[OK] 后端运行进程已关闭"
