#!/usr/bin/env bash
# backend/start_backend.sh
#
# 第二阶段：算法统一走 algorithms.tools.run_video_pipeline / algorithms.core.adjust_calibration，
# 后端不再写 FISH_ALGO_MODE mock / real 切换。
#
# 此脚本只负责启动 uvicorn。worker 推荐通过根目录的 start_dev.sh / restart_fish_dashboard.sh
# 统一启动，避免重复管理。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_DIR_LOGICAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR_REAL="$(realpath "$SCRIPT_DIR")"
PROJECT_ROOT_REAL="$(cd "$BACKEND_DIR_REAL/.." && pwd)"
ALGO_DIR="$PROJECT_ROOT_REAL/algorithms"
LOG_DIR="$PROJECT_ROOT_REAL/data/logs"

mkdir -p "$LOG_DIR"

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

if [ -f "$BACKEND_DIR_REAL/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$BACKEND_DIR_REAL/.env"
  set +a
fi

export FISH_PROJECT_ROOT="$PROJECT_ROOT_REAL"
export FISH_ALGO_ROOT="$ALGO_DIR"
export FISH_DATA_ROOT="$PROJECT_ROOT_REAL/data"
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1
export FISH_UVICORN_RELOAD="${FISH_UVICORN_RELOAD:-0}"

export FISH_BERT_BASE_UNCASED_PATH="$PROJECT_ROOT_REAL/algorithms/bert-base-uncased"
export FISH_GROUNDING_DINO_CONFIG="$PROJECT_ROOT_REAL/algorithms/fishalgo/measures/crop_images/grounding_dino_swin-t_finetune_8xb4_50e_fish.py"
export FISH_GROUNDING_DINO_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/best_coco_bbox_mAP_epoch_12.pth"
export FISH_SAM_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/sam_vit_h_4b8939.pth"
export FISH_SAM_HQ_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/sam_hq_vit_h.pth"
export FISH_WOUND_MODEL_PATH="$PROJECT_ROOT_REAL/algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth"
export FISH_WOUND_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth"
export FISH_TAIL_CHECKPOINT="$PROJECT_ROOT_REAL/algorithms/pth/maskrcnn_resnet50_fpn_v2-tail.pth"
export FISH_WEIGHT_MODEL_PATH="$PROJECT_ROOT_REAL/algorithms/pth/catboost_full_model.pkl"

export PYTHONPATH="$PROJECT_ROOT_REAL/backend:$PROJECT_ROOT_REAL/algorithms:${PYTHONPATH:-}"

echo "========================================="
echo "[backend/start_backend.sh] uvicorn-only"
echo "========================================="
echo "[env] conda_env = $CONDA_ENV_NAME"
echo "[env] python    = $($PY -c 'import sys; print(sys.executable)')"
echo "[env] DATABASE_URL = ${DATABASE_URL:-<not_set>}"
echo "[env] REDIS_URL    = ${REDIS_URL:-<not_set>}"

UVICORN_ARGS=(app.main:app --host 0.0.0.0 --port 8000)

if [ "${FISH_UVICORN_RELOAD}" = "1" ]; then
  echo "[uvicorn] reload enabled"
  UVICORN_ARGS+=("--reload")
else
  echo "[uvicorn] reload disabled"
fi

cd "$BACKEND_DIR_REAL"
exec "$PY" -m uvicorn "${UVICORN_ARGS[@]}"
