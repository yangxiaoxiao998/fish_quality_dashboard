#!/usr/bin/env bash
# stop_dev.sh
# 安全停止后端 + 前端：worker / uvicorn / Vite。
# 优先按 pid 文件精确停止，再用安全 pkill 兜底。
# 严禁使用 pkill -f "uvicorn" / "vite" / "node" / "worker" 等宽匹配（会匹配到当前 shell 自身命令行）。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT_REAL="$(realpath "$SCRIPT_DIR")"
RUNTIME_DIR="$PROJECT_ROOT_REAL/.runtime"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
MINIO_API_PORT="${FISH_MINIO_API_PORT:-9000}"

MEDIAMTX_BIN="$PROJECT_ROOT_REAL/mediamtx/mediamtx"
MEDIAMTX_CFG="$PROJECT_ROOT_REAL/mediamtx/mediamtx.yml"

stop_pid_file() {
  local name="$1"
  local file="$2"
  if [ ! -f "$file" ]; then
    return 0
  fi
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    rm -f "$file"
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[stop] $name pid=$pid already exited; removing stale pidfile"
    rm -f "$file"
    return 0
  fi
  echo "[stop] stopping $name pid=$pid"
  kill "$pid" 2>/dev/null || true
  for i in 1 2 3 4 5; do
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "[stop] force killing $name pid=$pid"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

echo "[stop_dev] stopping by pid files..."
stop_pid_file "worker"  "$RUNTIME_DIR/rq-worker.pid"
stop_pid_file "minio"   "$RUNTIME_DIR/minio.pid"
stop_pid_file "uvicorn" "$RUNTIME_DIR/backend.pid"
stop_pid_file "vite"    "$RUNTIME_DIR/frontend.pid"
stop_pid_file "upgrade-manager" "$RUNTIME_DIR/upgrade-manager.pid"

# 只停止本项目配置启动的 mediamtx，不要误杀系统 mediamtx
echo "[stop_dev] stopping project mediamtx..."
stop_pid_file "mediamtx" "$RUNTIME_DIR/mediamtx.pid"
pkill -f "$MEDIAMTX_BIN $MEDIAMTX_CFG" 2>/dev/null || true

echo "[stop_dev] killing remaining backend (uvicorn / worker)..."
# 安全 pkill：使用 [xxx] 防自杀
pkill -f '[u]vicorn.*app.main:app' 2>/dev/null || true
pkill -f '[p]ython -m uvicorn app.main:app' 2>/dev/null || true
pkill -f '[a]pp.workers.worker_main' 2>/dev/null || true
pkill -f '[a]pp.services.update.upgrade_manager' 2>/dev/null || true

echo "[stop_dev] killing remaining frontend (vite / npm / node)..."
pkill -f "[v]ite.*$FRONTEND_PORT" 2>/dev/null || true
pkill -f "[n]pm run dev.*$FRONTEND_PORT" 2>/dev/null || true
pkill -f '[n]ode.*vite' 2>/dev/null || true

sleep 2

echo "[stop_dev] port check:"
ss_check="$(ss -tlnp 2>/dev/null | grep -E ":$BACKEND_PORT|:$FRONTEND_PORT|:$MINIO_API_PORT|:8012|:1935|:8554" || true)"
if [ -z "$ss_check" ]; then
  echo "  [OK] ports $BACKEND_PORT / $FRONTEND_PORT / 8012 / 1935 / 8554 已释放"
else
  echo "$ss_check"
fi

echo "[stop_dev] process check:"
remaining="$(ps -ef | grep -E "[u]vicorn.*app.main:app|[a]pp.workers.worker_main|[v]ite.*$FRONTEND_PORT|[n]pm run dev.*$FRONTEND_PORT|[n]ode.*vite" || true)"
if [ -z "$remaining" ]; then
  echo "  [OK] no backend / worker / vite process running"
else
  echo "$remaining"
  echo "  [WARN] 仍有进程存活，请手工检查"
fi
