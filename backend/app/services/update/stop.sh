#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd -P)"
# shellcheck source=update/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

INSTALL_ROOT="${FISH_INSTALL_ROOT:-}"
QUIET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root)
      INSTALL_ROOT="${2:?missing value for --install-root}"
      shift 2
      ;;
    --quiet)
      QUIET=1
      shift
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
PID_DIR="$INSTALL_ROOT/data/pids"
UPDATE_LOG_DIR="$INSTALL_ROOT/data/logs/update"
mkdir -p "$UPDATE_LOG_DIR"

LOG_FILE="$UPDATE_LOG_DIR/stop_$(fish_now_ts).log"
exec > >(tee -a "$LOG_FILE") 2>&1

if [[ "$QUIET" != "1" ]]; then
  fish_log "[stop] install_root=$INSTALL_ROOT"
  fish_log "[stop] log_file=$LOG_FILE"
fi

process_matches_project() {
  local pid="$1"
  [[ -d "/proc/$pid" ]] || return 1

  local cwd=""
  local cmdline=""
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  if [[ -f "/proc/$pid/cmdline" ]]; then
    cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  fi

  if [[ -n "$cwd" ]] && fish_path_within "$cwd" "$INSTALL_ROOT"; then
    return 0
  fi
  if [[ "$cmdline" == *"$INSTALL_ROOT"* ]]; then
    return 0
  fi
  return 1
}

pgid_for_pid() {
  local pid="$1"
  ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]'
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout_seconds="${2:-15}"
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if (( waited >= timeout_seconds )); then
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 0
}

stop_pid_file() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi

  local pid=""
  pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    fish_log "[stop] $name pid=$pid already exited"
    rm -f "$pid_file"
    return 0
  fi

  if ! process_matches_project "$pid"; then
    fish_warn "[stop] skip $name pid=$pid because it does not belong to $INSTALL_ROOT"
    rm -f "$pid_file"
    return 0
  fi

  local pgid=""
  pgid="$(pgid_for_pid "$pid")"
  if [[ -n "$pgid" ]]; then
    fish_log "[stop] TERM $name pid=$pid pgid=$pgid"
    kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  else
    fish_log "[stop] TERM $name pid=$pid"
    kill -TERM "$pid" 2>/dev/null || true
  fi

  if ! wait_for_pid_exit "$pid" 15; then
    if [[ -n "$pgid" ]]; then
      fish_warn "[stop] KILL $name pid=$pid pgid=$pgid"
      kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    else
      fish_warn "[stop] KILL $name pid=$pid"
      kill -KILL "$pid" 2>/dev/null || true
    fi
    wait_for_pid_exit "$pid" 5 || true
  fi

  rm -f "$pid_file"
}

stop_by_pattern() {
  local name="$1"
  local pattern="$2"
  local line=""

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    local pid=""
    pid="$(printf '%s' "$line" | awk '{print $1}')"
    if [[ -z "$pid" ]] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
      continue
    fi
    if ! process_matches_project "$pid"; then
      continue
    fi
    local pgid=""
    pgid="$(pgid_for_pid "$pid")"
    if [[ -n "$pgid" ]]; then
      fish_log "[stop] fallback TERM $name pid=$pid pgid=$pgid"
      kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    else
      fish_log "[stop] fallback TERM $name pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done < <(ps -eo pid,args | grep -E "$pattern" | grep -v grep || true)
}

mkdir -p "$PID_DIR"
stop_pid_file "worker" "$PID_DIR/backend_worker.pid"
stop_pid_file "uvicorn" "$PID_DIR/backend_uvicorn.pid"
stop_pid_file "vite" "$PID_DIR/frontend_vite.pid"
stop_pid_file "mediamtx" "$PID_DIR/mediamtx.pid"

stop_by_pattern "worker" 'app\.workers\.worker_main'
stop_by_pattern "uvicorn" 'uvicorn .*app\.main:app|python .* -m uvicorn app\.main:app'
stop_by_pattern "vite" 'vite .*--port [0-9]+|npm .*run dev'
stop_by_pattern "mediamtx" 'mediamtx'

sleep 2
fish_log "[stop] done"
