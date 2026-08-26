#!/usr/bin/env bash
# scripts/start_upgrade_manager.sh
#
# 启动独立的 Upgrade Manager FastAPI 服务（默认 127.0.0.1:8012）。
# 不影响主后端进程；主后端被升级替换时此服务可继续工作。
#
# 用法：
#   bash scripts/start_upgrade_manager.sh
#
# 关键环境变量（可选，覆盖默认）：
#   PY                     - python 解释器（默认 /home/dsl/miniconda3/envs/full/bin/python）
#   UPGRADE_MANAGER_HOST   - 默认 127.0.0.1
#   UPGRADE_MANAGER_PORT   - 默认 8012
#   UPGRADE_MANAGER_ALLOWED_ORIGINS - CORS 白名单（默认 4 个本地端口）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PY="${PY:-${CONDA_ENV_DIR:-/home/dsl/miniconda3/envs/full}/bin/python}"

cd "$ROOT"

export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export UPGRADE_MANAGER_HOST="${UPGRADE_MANAGER_HOST:-127.0.0.1}"
export UPGRADE_MANAGER_PORT="${UPGRADE_MANAGER_PORT:-8012}"
export UPGRADE_MANAGER_ALLOWED_ORIGINS="${UPGRADE_MANAGER_ALLOWED_ORIGINS:-http://127.0.0.1:5173,http://localhost:5173}"

exec "$PY" -m app.services.update.upgrade_manager
