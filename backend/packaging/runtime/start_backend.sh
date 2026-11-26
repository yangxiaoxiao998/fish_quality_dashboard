#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_BIN="$INSTALL_ROOT/fish-quality-backend"
CONFIG_FILE="${FISH_ENV_FILE:-$INSTALL_ROOT/config/backend.env}"

if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
fi

DATA_ROOT="${FISH_DATA_ROOT:-$INSTALL_ROOT/data}"
RUN_DIR="$DATA_ROOT/run"
LOG_DIR="$DATA_ROOT/logs/runtime"

export FISH_INSTALL_ROOT="$INSTALL_ROOT"
export FISH_PROJECT_ROOT="$INSTALL_ROOT"
export FISH_DATA_ROOT="${FISH_DATA_ROOT:-$DATA_ROOT}"
export FISH_CONFIG_ROOT="${FISH_CONFIG_ROOT:-$INSTALL_ROOT/config}"
export FISH_ALGO_ROOT="${FISH_ALGO_ROOT:-$INSTALL_ROOT/algorithms}"
export DATABASE_URL="${DATABASE_URL:-postgresql://fish:fish@127.0.0.1:5432/fishdb}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
# Keep packaged jobs isolated from a source checkout running on the same host.
export FISH_RQ_QUEUE="${FISH_RQ_QUEUE:-fish-quality-packaged}"
export BACKEND_PORT="${BACKEND_PORT:-8000}"
export UPGRADE_MANAGER_PORT="${UPGRADE_MANAGER_PORT:-8012}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

PTH_DIR="$FISH_ALGO_ROOT/pth"
BERT_DIR="$PTH_DIR/bert-base-uncased"
for required in config.json pytorch_model.bin tokenizer_config.json tokenizer.json vocab.txt; do
    if [[ ! -s "$BERT_DIR/$required" ]]; then
        echo "[ERROR] BERT模型文件不存在或为空：$BERT_DIR/$required" >&2
        exit 1
    fi
done
export FISH_BERT_BASE_UNCASED_PATH="$BERT_DIR"
export GDINO_BERT_PATH="$BERT_DIR"
export FISH_GROUNDING_DINO_CONFIG="${FISH_GROUNDING_DINO_CONFIG:-$FISH_ALGO_ROOT/vendor/GroundingDINO/groundingdino/config/GroundingDINO_SwinT_OGC.py}"
export FISH_GROUNDING_DINO_CHECKPOINT="${FISH_GROUNDING_DINO_CHECKPOINT:-$PTH_DIR/groundingdino_swint_ogc.pth}"
export FISH_SAM_CHECKPOINT="${FISH_SAM_CHECKPOINT:-$PTH_DIR/sam_vit_h_4b8939.pth}"
export FISH_SAM_HQ_CHECKPOINT="${FISH_SAM_HQ_CHECKPOINT:-$PTH_DIR/sam_hq_vit_h.pth}"
export FISH_WOUND_CHECKPOINT="${FISH_WOUND_CHECKPOINT:-$PTH_DIR/maskrcnn_resnet50_fpn_v2-wound.pth}"
export FISH_TAIL_CHECKPOINT="${FISH_TAIL_CHECKPOINT:-$PTH_DIR/maskrcnn_resnet50_fpn_v2-tail.pth}"
export FISH_WEIGHT_MODEL_PATH="${FISH_WEIGHT_MODEL_PATH:-$PTH_DIR/catboost_full_model.pkl}"

mkdir -p "$RUN_DIR" "$LOG_DIR"
[[ -x "$BACKEND_BIN" ]] || { echo "[ERROR] 后端可执行文件不存在：$BACKEND_BIN" >&2; exit 1; }

tcp_listening() {
    local port="$1"
    ss -lnt 2>/dev/null | awk 'NR > 1 {print $4}' | grep -Eq "(^|:)${port}$"
}

require_port() {
    local name="$1" port="$2"
    if ! tcp_listening "$port"; then
        echo "[ERROR] $name 未监听端口 $port；请先安装并启动该系统服务。" >&2
        exit 1
    fi
}

pid_running() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

start_process() {
    local name="$1" pid_file="$2" log_file="$3"
    shift 3
    local old_pid=""
    if [[ -f "$pid_file" ]]; then old_pid="$(tr -d '[:space:]' <"$pid_file")"; fi
    if pid_running "$old_pid"; then
        echo "[INFO] $name 已运行，PID=$old_pid"
        return 0
    fi
    setsid nohup "$@" >>"$log_file" 2>&1 </dev/null &
    local pid=$!
    printf '%s\n' "$pid" >"$pid_file"
    sleep 1
    if ! pid_running "$pid"; then
        echo "[ERROR] $name 启动失败，日志：$log_file" >&2
        tail -n 80 "$log_file" >&2 || true
        exit 1
    fi
    echo "[OK] $name 已启动，PID=$pid"
}

require_port "PostgreSQL" 5432
require_port "Redis" 6379

if ! tcp_listening 1935; then
    MEDIAMTX_BIN="$INSTALL_ROOT/mediamtx/mediamtx"
    MEDIAMTX_CONFIG="$INSTALL_ROOT/mediamtx/mediamtx.yml"
    [[ -x "$MEDIAMTX_BIN" && -f "$MEDIAMTX_CONFIG" ]] || {
        echo "[ERROR] MediaMTX运行文件不完整：$INSTALL_ROOT/mediamtx" >&2
        exit 1
    }
    start_process "MediaMTX" "$RUN_DIR/mediamtx.pid" "$LOG_DIR/mediamtx.log" "$MEDIAMTX_BIN" "$MEDIAMTX_CONFIG"
else
    echo "[INFO] MediaMTX 已监听端口 1935"
fi

start_process "RQ Worker" "$RUN_DIR/worker.pid" "$LOG_DIR/worker.log" "$BACKEND_BIN" worker
start_process "Upgrade Manager" "$RUN_DIR/upgrade-manager.pid" "$LOG_DIR/upgrade-manager.log" "$BACKEND_BIN" upgrade-manager --port "$UPGRADE_MANAGER_PORT"

if tcp_listening "$BACKEND_PORT"; then
    echo "[INFO] 后端端口 $BACKEND_PORT 已被监听，跳过重复启动"
else
    start_process "FastAPI" "$RUN_DIR/api.pid" "$LOG_DIR/api.log" "$BACKEND_BIN" api --port "$BACKEND_PORT"
fi

for _ in {1..90}; do
    if curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$BACKEND_PORT/docs" >/dev/null 2>&1; then
        echo "[OK] 后端健康检查通过：http://127.0.0.1:$BACKEND_PORT/docs"
        exit 0
    fi
    sleep 1
done

echo "[ERROR] 后端健康检查超时，日志：$LOG_DIR/api.log" >&2
tail -n 100 "$LOG_DIR/api.log" >&2 || true
exit 1
