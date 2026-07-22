#!/usr/bin/env bash
# start_dev.sh
#
# 一键后台启动后端 + 前端：
#   * 后端：RQ worker + uvicorn (port 8000)
#   * 前端：Vite dev server (port 1124, strictPort)


set -uo pipefail

# ----------------------------------------------------------------------
# 1) 推导项目根
# ----------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${FISH_PROJECT_ROOT:-$SCRIPT_DIR}"
PROJECT_ROOT_REAL="$(realpath "$PROJECT_ROOT")"
BACKEND_DIR_REAL="$PROJECT_ROOT_REAL/backend"
FRONTEND_DIR_REAL="$PROJECT_ROOT_REAL/frontend"
ALGO_DIR="$PROJECT_ROOT_REAL/algorithms"
LOG_DIR="$PROJECT_ROOT_REAL/data/logs"
PID_DIR="$PROJECT_ROOT_REAL/data/pids"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-1124}"

mkdir -p "$LOG_DIR"
mkdir -p "$PID_DIR"

# ----------------------------------------------------------------------
# 2) conda 环境：默认 test
# ----------------------------------------------------------------------
CONDA_ENV_NAME="${CONDA_ENV_NAME:-test}"
PY="/home/$USER/miniconda3/envs/${CONDA_ENV_NAME}/bin/python"

if [ ! -x "$PY" ]; then
  echo "[ERROR] python not found: $PY"
  echo "[HINT] 当前 CONDA_ENV_NAME=$CONDA_ENV_NAME"
  echo "[HINT] 推荐：conda create -n test python=3.10 && conda activate test"
  echo "[HINT]        pip install psycopg2-binary redis rq fastapi 'sqlalchemy[asyncio]' pydantic-settings"
  exit 1
fi

if [ ! -d "$PROJECT_ROOT_REAL/backend" ] || [ ! -d "$PROJECT_ROOT_REAL/algorithms" ]; then
  echo "[ERROR] project root invalid: $PROJECT_ROOT_REAL"
  exit 1
fi

if [ ! -d "$FRONTEND_DIR_REAL/node_modules" ]; then
  echo "[WARN] frontend/node_modules 不存在；建议先在 frontend 目录执行 npm install"
fi

# ----------------------------------------------------------------------
# 3) 加载 backend/.env + 关键环境变量
# ----------------------------------------------------------------------
export FISH_PROJECT_ROOT="$PROJECT_ROOT_REAL"
export FISH_ALGO_ROOT="$ALGO_DIR"
export FISH_DATA_ROOT="$PROJECT_ROOT_REAL/data"
export FISH_PROJECT_ROOT_REAL="$PROJECT_ROOT_REAL"
export FISH_UVICORN_RELOAD="${FISH_UVICORN_RELOAD:-0}"

if [ -f "$BACKEND_DIR_REAL/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$BACKEND_DIR_REAL/.env"
  set +a
fi

export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1

export FISH_BERT_BASE_UNCASED_PATH="$PROJECT_ROOT_REAL/algorithms/bert-base-uncased"
export FISH_GROUNDING_DINO_CONFIG="$PROJECT_ROOT_REAL/algorithms/fishalgo/measures/crop_images/grounding_dino_swin-t_finetune_8xb4_50e_fish.py"
export FISH_GROUNDING_DINO_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/groundingdino_swint_ogc.pth"
export FISH_SAM_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/sam_vit_h_4b8939.pth"
export FISH_SAM_HQ_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/sam_hq_vit_h.pth"
export FISH_WOUND_MODEL_PATH="$PROJECT_ROOT_REAL/algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth"
export FISH_WOUND_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth"
export FISH_TAIL_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/maskrcnn_resnet50_fpn_v2-tail.pth"
export FISH_WEIGHT_MODEL_PATH="$PROJECT_ROOT_REAL/algorithms/pth/catboost_full_model.pkl"

# 摄像头视角标定用的树莓派推流配置（用户点击"拍摄标定帧并执行标定"时使用）。
# 这些变量如果未设置，后端会在抓拍时返回用户友好错误，不暴露技术地址。
export FISH_PI_HOST="${FISH_PI_HOST:-raspberrypi.local}"
export FISH_PI_PORT="${FISH_PI_PORT:-9000}"
export FISH_PI_SCHEME="${FISH_PI_SCHEME:-http}"
export FISH_PI_RESOLVE_TIMEOUT_SECONDS="${FISH_PI_RESOLVE_TIMEOUT_SECONDS:-2}"
export FISH_PI_RESOLVE_CACHE_SECONDS="${FISH_PI_RESOLVE_CACHE_SECONDS:-30}"
# 兼容旧变量：默认值必须是 mDNS 主机名，禁止写死 IP。
# 开发阶段：raspberrypi.local:9000；上线阶段：可通过 FISH_PI_BASE_URL 覆盖为固定 IP。
export FISH_PI_BASE_URL="${FISH_PI_BASE_URL:-${FISH_CAMERA_PROXY_BASE_URL:-http://raspberrypi.local:9000}}"
export FISH_PI_API_BASE="${FISH_PI_API_BASE:-$FISH_PI_BASE_URL}"
# 确保 .local 主机名不被系统代理（Clash / VPN）错误地代理出去
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,raspberrypi.local,.local}"
export no_proxy="${no_proxy:-$NO_PROXY}"
# RTMP 推流地址默认不写死；如果用户显式设置 FISH_CAMERA_PUSH_URL（例如
#   FISH_CAMERA_PUSH_URL=rtmp://192.168.0.111:1935/fish_camera bash start_dev.sh
# ）则保留；否则由后端运行时自动推断主机当前局域网 IP。
if [ -n "${FISH_CAMERA_PUSH_URL:-}" ]; then
  export FISH_CAMERA_PUSH_URL
else
  export FISH_CAMERA_PUSH_URL=""
fi
export FISH_CAMERA_READ_URL="${FISH_CAMERA_READ_URL:-rtmp://127.0.0.1:1935/fish_camera}"
export FISH_CAMERA_WIDTH="${FISH_CAMERA_WIDTH:-1920}"
export FISH_CAMERA_HEIGHT="${FISH_CAMERA_HEIGHT:-1080}"
export FISH_CAMERA_FPS="${FISH_CAMERA_FPS:-25}"

export PYTHONPATH="$PROJECT_ROOT_REAL/backend:$PROJECT_ROOT_REAL/algorithms:${PYTHONPATH:-}"

echo "========================================="
echo "[start_dev] project: $PROJECT_ROOT_REAL"
echo "[start_dev] conda_env: $CONDA_ENV_NAME"
echo "[start_dev] python:    $($PY -c 'import sys; print(sys.executable)')"
echo "[start_dev] backend port: $BACKEND_PORT"
echo "[start_dev] frontend port: $FRONTEND_PORT"
echo "[start_dev] DATABASE_URL: ${DATABASE_URL:-<not_set>}"
echo "[start_dev] REDIS_URL:    ${REDIS_URL:-<not_set>}"
# 只打印"是否配置"，不打印完整技术地址（避免日志泄漏）
if [ -n "$FISH_PI_API_BASE" ]; then
  echo "[start_dev] fish pi api configured: yes"
else
  echo "[start_dev] fish pi api configured: no"
fi
if [ -n "$FISH_CAMERA_PUSH_URL" ]; then
  echo "[start_dev] fish camera push url configured: yes"
else
  echo "[start_dev] fish camera push url configured: no"
fi
echo "[start_dev] fish camera read url configured: $([ -n "$FISH_CAMERA_READ_URL" ] && echo yes || echo no)"
echo "========================================="

# ----------------------------------------------------------------------
# 4) 安全停止旧进程（使用 [xxx] 防自杀）
# ----------------------------------------------------------------------
echo "[stop] killing old uvicorn / worker / vite / mediamtx (safe pkill)..."
pkill -f '[u]vicorn.*app.main:app' 2>/dev/null || true
pkill -f '[p]ython -m uvicorn app.main:app' 2>/dev/null || true
pkill -f '[a]pp.workers.worker_main' 2>/dev/null || true
pkill -f '[v]ite.*1124' 2>/dev/null || true
pkill -f '[n]pm run dev.*1124' 2>/dev/null || true
pkill -f '[n]ode.*vite' 2>/dev/null || true
# 清理旧 pid 文件
rm -f "$PID_DIR/backend_worker.pid" "$PID_DIR/backend_uvicorn.pid" "$PID_DIR/frontend_vite.pid" "$PID_DIR/mediamtx.pid"
sleep 2

# ----------------------------------------------------------------------
# 4.5) 启动项目内 MediaMTX（显式带项目配置，避免走系统 mediamtx）
# ----------------------------------------------------------------------
MEDIAMTX_BIN="$PROJECT_ROOT_REAL/mediamtx/mediamtx"
MEDIAMTX_CFG="$PROJECT_ROOT_REAL/mediamtx/mediamtx.yml"
MEDIAMTX_LOG="$LOG_DIR/mediamtx.log"
MEDIAMTX_PID_FILE="$PID_DIR/mediamtx.pid"

if [ -x "$MEDIAMTX_BIN" ] && [ -f "$MEDIAMTX_CFG" ]; then
  if grep -q "fish_camera:" "$MEDIAMTX_CFG"; then
    echo "[mediamtx] starting project mediamtx with explicit config"
    echo "[mediamtx] bin: $MEDIAMTX_BIN"
    echo "[mediamtx] cfg: $MEDIAMTX_CFG"
    setsid nohup "$MEDIAMTX_BIN" "$MEDIAMTX_CFG" \
      >"$MEDIAMTX_LOG" 2>&1 </dev/null &
    MEDIAMTX_PID=$!
    disown "$MEDIAMTX_PID" 2>/dev/null || true
    echo "$MEDIAMTX_PID" > "$MEDIAMTX_PID_FILE"
    sleep 1
    if kill -0 "$MEDIAMTX_PID" 2>/dev/null; then
      echo "[mediamtx] started, pid=$MEDIAMTX_PID, log=$MEDIAMTX_LOG"
    else
      echo "[WARN] mediamtx exited immediately; tail of log:"
      tail -40 "$MEDIAMTX_LOG" || true
    fi
  else
    echo "[WARN] mediamtx config missing fish_camera path: $MEDIAMTX_CFG"
  fi
else
  echo "[WARN] project mediamtx not started: bin=$MEDIAMTX_BIN cfg=$MEDIAMTX_CFG"
fi

# ----------------------------------------------------------------------
# 5) 启动 RQ worker（nohup + setsid 守护）
# ----------------------------------------------------------------------
cd "$BACKEND_DIR_REAL"
setsid nohup "$PY" -m app.workers.worker_main \
  >"$LOG_DIR/backend_worker.log" 2>&1 </dev/null &
WORKER_PID=$!
disown "$WORKER_PID" 2>/dev/null || true
echo "$WORKER_PID" > "$PID_DIR/backend_worker.pid"
echo "[worker] started, pid=$WORKER_PID, log=$LOG_DIR/backend_worker.log, pidfile=$PID_DIR/backend_worker.pid"

sleep 3
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "[ERROR] RQ worker exited immediately (pid=$WORKER_PID)"
  echo "[ERROR] backend_worker.log 最后 80 行："
  echo "----------------------------------------"
  tail -80 "$LOG_DIR/backend_worker.log" || true
  echo "----------------------------------------"
  # 清理本脚本已启动的其它进程
  rm -f "$PID_DIR/backend_worker.pid"
  exit 1
fi
echo "[worker] liveness OK, pid=$WORKER_PID still alive after 3s"

# ----------------------------------------------------------------------
# 6) 启动 uvicorn（nohup + setsid 守护）
# ----------------------------------------------------------------------
setsid nohup "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
  >"$LOG_DIR/backend_uvicorn.log" 2>&1 </dev/null &
UVICORN_PID=$!
disown "$UVICORN_PID" 2>/dev/null || true
echo "$UVICORN_PID" > "$PID_DIR/backend_uvicorn.pid"
echo "[uvicorn] started, pid=$UVICORN_PID, port=$BACKEND_PORT, log=$LOG_DIR/backend_uvicorn.log, pidfile=$PID_DIR/backend_uvicorn.pid"

# 等待后端监听
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  sleep 1
  if curl -sS -m 1 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/docs" 2>/dev/null | grep -q "200"; then
    break
  fi
done
echo "[uvicorn] liveness check done"

# ----------------------------------------------------------------------
# 7) 启动前端 Vite（nohup + setsid 守护）
# ----------------------------------------------------------------------
if [ -d "$FRONTEND_DIR_REAL/node_modules" ]; then
  cd "$FRONTEND_DIR_REAL"
  setsid nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" --strictPort \
    >"$LOG_DIR/frontend_vite.log" 2>&1 </dev/null &
  VITE_PID=$!
  disown "$VITE_PID" 2>/dev/null || true
  echo "$VITE_PID" > "$PID_DIR/frontend_vite.pid"
  echo "[vite] started, pid=$VITE_PID, port=$FRONTEND_PORT, log=$LOG_DIR/frontend_vite.log, pidfile=$PID_DIR/frontend_vite.pid"

  VITE_READY=0
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    sleep 1
    if curl -sS -m 1 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$FRONTEND_PORT/" 2>/dev/null | grep -q "200"; then
      VITE_READY=1
      break
    fi
  done

  if [ "$VITE_READY" -ne 1 ]; then
    echo "[ERROR] Vite 启动失败或监听超时"
    echo "[ERROR] frontend_vite.log 最后 80 行："
    echo "----------------------------------------"
    tail -80 "$LOG_DIR/frontend_vite.log" || true
    echo "----------------------------------------"
    echo "[ERROR] 退出前保留 worker / uvicorn，请用 bash stop_dev.sh 清理"
    exit 1
  fi
else
  echo "[WARN] frontend/node_modules 缺失，跳过前端启动"
  VITE_PID=0
fi

# ----------------------------------------------------------------------
# 8) 综合健康检查
# ----------------------------------------------------------------------
echo "========================================="
echo "[health-check] 后端 /api/algorithm/calibration/status"
curl -sS -m 5 "http://127.0.0.1:$BACKEND_PORT/api/algorithm/calibration/status" 2>&1 | head -c 400
echo
echo
echo "[health-check] 后端 /api/videos?page=1&page_size=3"
curl -sS -m 5 "http://127.0.0.1:$BACKEND_PORT/api/videos?page=1&page_size=3" 2>&1 | head -c 400
echo
echo
echo "[health-check] 前端 proxy /api/videos?page=1&page_size=3"
curl -sS -m 5 "http://127.0.0.1:$FRONTEND_PORT/api/videos?page=1&page_size=3" 2>&1 | head -c 400
echo
echo
echo "[health-check] 前端 proxy /static/results/.../grounded_sam.mp4"
curl -I -m 5 "http://127.0.0.1:$FRONTEND_PORT/static/results/video_20260704_143213_6a2acb0e/videos/grounded_sam.mp4" 2>&1 | head -5
echo

# ----------------------------------------------------------------------
# 9) 最终输出
# ----------------------------------------------------------------------
cat <<INFO

============================================================
启动成功
============================================================
后端接口：http://127.0.0.1:$BACKEND_PORT
后端文档：http://127.0.0.1:$BACKEND_PORT/docs
前端页面：http://127.0.0.1:$FRONTEND_PORT
视频任务：http://127.0.0.1:$FRONTEND_PORT/admin/video-history
结果大屏：http://127.0.0.1:$FRONTEND_PORT/?mode=result&video_id=video_20260704_143213_6a2acb0e

worker pid:  $WORKER_PID
uvicorn pid: $UVICORN_PID
vite pid:    ${VITE_PID:-0}
mediamtx pid:${MEDIAMTX_PID:-0}

worker log:  $LOG_DIR/backend_worker.log
uvicorn log: $LOG_DIR/backend_uvicorn.log
vite log:    $LOG_DIR/frontend_vite.log
mediamtx log:$LOG_DIR/mediamtx.log

pid 文件:
  $PID_DIR/backend_worker.pid
  $PID_DIR/backend_uvicorn.pid
  $PID_DIR/frontend_vite.pid
  $PID_DIR/mediamtx.pid

停止命令：bash stop_dev.sh
============================================================
INFO

echo "[start_dev] all services started in background, returning shell"
exit 0
