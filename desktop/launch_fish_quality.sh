#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
START_SCRIPT="$PROJECT_ROOT/start_dev.sh"
DESKTOP_DIR="$PROJECT_ROOT/desktop"
FRONTEND_URL="${FISH_QUALITY_URL:-http://127.0.0.1:5173}"
# 直连树莓派时始终使用当前自动共享网卡地址，不继承旧 Wi-Fi 推流地址。
export FISH_CAMERA_AUTO_PUSH_URL="${FISH_CAMERA_AUTO_PUSH_URL:-1}"
export FISH_CAMERA_NETWORK_INTERFACE="${FISH_CAMERA_NETWORK_INTERFACE:-enp3s0}"
export FISH_PI_BASE_URL="${FISH_PI_BASE_URL:-http://raspberrypi.local:9000}"
export FISH_CAMERA_READ_URL="${FISH_CAMERA_READ_URL:-rtmp://127.0.0.1:1935/fish_camera}"
unset FISH_CAMERA_PUSH_URL
NODE_BIN="${NODE_BIN:-/home/dsl/miniconda3/envs/full/bin/node}"
NPM_CLI="${NPM_CLI:-/home/dsl/miniconda3/envs/full/lib/node_modules/npm/bin/npm-cli.js}"
START_LOG="${FISH_QUALITY_START_LOG:-/tmp/fish-quality-start.log}"
ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"
LAUNCH_LOCK="${FISH_QUALITY_LAUNCH_LOCK:-/tmp/fish-quality-desktop-launch.lock}"

if [[ -n "${FISH_QUALITY_PROJECT_ROOT:-}" ]]; then
  PROJECT_ROOT="$(cd -- "$FISH_QUALITY_PROJECT_ROOT" && pwd -P)"
  START_SCRIPT="$PROJECT_ROOT/start_dev.sh"
  DESKTOP_DIR="$PROJECT_ROOT/desktop"
fi

if [[ ! -x "$NODE_BIN" || ! -f "$NPM_CLI" ]]; then
  echo "未找到 Node.js/npm：$NODE_BIN / $NPM_CLI" >&2
  exit 1
fi

# npm 的依赖安装脚本通过 /usr/bin/env node 启动，必须让它找到同一套 Node 20。
export PATH="$(dirname -- "$NODE_BIN"):$PATH"
export ELECTRON_MIRROR
# The kiosk window must also work on desktop sessions without a usable
# OpenGL/EGL context. main.cjs sets these switches as a second line of
# defence; keeping the environment explicit also covers Electron's GPU
# process during startup.
export ELECTRON_DISABLE_GPU="${ELECTRON_DISABLE_GPU:-1}"

# Keep repeated desktop double-clicks from starting competing service/window trees.
exec 9>"$LAUNCH_LOCK"
if ! flock -n 9; then
  exit 0
fi

if ! curl -fsS --max-time 2 "$FRONTEND_URL" >/dev/null 2>&1; then
  CONDA_ENV_DIR="${CONDA_ENV_DIR:-/home/dsl/miniconda3/envs/full}" \
    "$START_SCRIPT" >"$START_LOG" 2>&1 9>&- &
fi

for _ in {1..120}; do
  if curl -fsS --max-time 2 "$FRONTEND_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS --max-time 2 "$FRONTEND_URL" >/dev/null 2>&1; then
  echo "前端服务未能在 120 秒内启动，请查看 $START_LOG" >&2
  exit 1
fi

# 清理上一次异常退出后残留的本项目 Electron 窗口，避免旧窗口抢占全屏状态。
pkill -f 'electron.*fish-quality-desktop' 2>/dev/null || true

cd "$DESKTOP_DIR"
if [[ ! -x "$DESKTOP_DIR/node_modules/.bin/electron" ]]; then
  "$NODE_BIN" "$NPM_CLI" install --no-audit --no-fund --registry="${NPM_REGISTRY:-https://registry.npmmirror.com}"
fi
exec "$NODE_BIN" "$NPM_CLI" exec -- electron --disable-gpu --disable-gpu-compositing --in-process-gpu .
