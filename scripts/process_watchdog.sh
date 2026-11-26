#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
CHECK_INTERVAL_SECONDS="${WATCHDOG_INTERVAL_SECONDS:-10}"
LOG_FILE="${WATCHDOG_LOG_FILE:-$PROJECT_ROOT/data/logs/dev/process-watchdog.log}"

mkdir -p "$(dirname -- "$LOG_FILE")"

log() {
    printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "$LOG_FILE"
}

check_url() {
    local path="$1"
    curl --noproxy '*' --silent --show-error --fail --max-time 5 "http://127.0.0.1:${BACKEND_PORT}${path}" >/dev/null
}

log "process watchdog started; backend_port=${BACKEND_PORT}; interval=${CHECK_INTERVAL_SECONDS}s"
log "scope: detects/restarts user-space services only; it cannot recover a kernel hang"

while :; do
    if ! check_url /api/ready; then
        log "backend readiness failed"
        if command -v systemctl >/dev/null 2>&1 && systemctl --user restart fish-quality-backend.service 2>>"$LOG_FILE"; then
            log "requested restart: fish-quality-backend.service"
        else
            log "backend restart unavailable; inspect service logs"
        fi
    fi

    if command -v systemctl >/dev/null 2>&1; then
        for service in fish-quality-mediamtx.service fish-quality-worker.service; do
            if ! systemctl --user is-active --quiet "$service"; then
                log "service inactive: $service"
                systemctl --user restart "$service" >>"$LOG_FILE" 2>&1 || log "restart failed: $service"
            fi
        done
    fi

    sleep "$CHECK_INTERVAL_SECONDS"
