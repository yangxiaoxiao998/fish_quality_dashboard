#!/usr/bin/env bash

# ======================================================================
# 鱼品质检测系统——开发环境一键启动脚本
#
# 启动内容：
#   1. PostgreSQL
#   2. Redis
#   3. MediaMTX
#   4. RQ Worker
#   5. FastAPI 后端
#   6. Vite 前端开发服务器
#
# 设计原则：
#   1. 不在脚本中写死 /home/dsl 等用户目录；
#   2. 项目目录根据本脚本所在位置自动确定；
#   3. 不执行 conda activate，直接使用指定 Conda 环境中的程序；
#   4. Python、Node.js、npm 必须来自同一个 Conda 环境；
#   5. 开发环境可以移动目录，也可以更换用户名；
#   6. 后续打包人员不需要处理开发机器的绝对路径。
#
# 默认 Conda 环境：
#   full
#
# 如需使用其他环境：
#   CONDA_ENV_NAME=test ./start_dev.sh
#
# 如果 Conda 环境无法自动识别：
#   CONDA_ENV_DIR=/实际路径/envs/full ./start_dev.sh
# ======================================================================

set -Eeuo pipefail


# ----------------------------------------------------------------------
# 1. 获取项目根目录
#
# 无论从哪个目录执行本脚本，PROJECT_DIR 始终是 start_dev.sh 所在目录。
# 运行过程中得到绝对路径是正常的，源码中没有写死机器路径。
# ----------------------------------------------------------------------

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"


# ----------------------------------------------------------------------
# 2. 基础配置
# ----------------------------------------------------------------------

CONDA_ENV_NAME="${CONDA_ENV_NAME:-full}"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
VITE_API_PROXY_TARGET="${VITE_API_PROXY_TARGET:-http://127.0.0.1:${BACKEND_PORT}}"
UPGRADE_MANAGER_PORT="${UPGRADE_MANAGER_PORT:-8012}"
UPGRADE_MANAGER_ALLOWED_ORIGINS="${UPGRADE_MANAGER_ALLOWED_ORIGINS:-http://127.0.0.1:${FRONTEND_PORT},http://localhost:${FRONTEND_PORT}}"

DATABASE_URL="${DATABASE_URL:-postgresql://fish:fish@127.0.0.1:5432/fishdb}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

# 算法运行模式：real 表示执行真实算法。
FISH_ALGO_MODE="${FISH_ALGO_MODE:-real}"

# 树莓派服务地址。
FISH_PI_BASE_URL="${FISH_PI_BASE_URL:-http://10.42.0.2:9000}"

# 如果留空，后端根据自身配置决定推流地址。
FISH_CAMERA_PUSH_URL="${FISH_CAMERA_PUSH_URL:-}"

# 直连网线的自动共享模式：推流目标始终跟随当前主机网卡地址，
# 忽略旧的 Wi-Fi 地址和保存过的旧 stream_url。
FISH_CAMERA_AUTO_PUSH_URL="${FISH_CAMERA_AUTO_PUSH_URL:-1}"
FISH_CAMERA_NETWORK_INTERFACE="${FISH_CAMERA_NETWORK_INTERFACE:-enp3s0}"

# 本机读取摄像头流时使用的地址。
FISH_CAMERA_READ_URL="${FISH_CAMERA_READ_URL:-rtmp://127.0.0.1:1935/fish_camera}"

# MinIO is optional for local development and enabled by the desktop launcher.
FISH_MINIO_AUTOSTART="${FISH_MINIO_AUTOSTART:-0}"
FISH_MINIO_BIN="${FISH_MINIO_BIN:-$(command -v minio 2>/dev/null || true)}"
FISH_MINIO_DATA_DIR="${FISH_MINIO_DATA_DIR:-$HOME/minio-data}"
FISH_MINIO_API_PORT="${FISH_MINIO_API_PORT:-9000}"
FISH_MINIO_CONSOLE_PORT="${FISH_MINIO_CONSOLE_PORT:-9001}"
FISH_MINIO_ENV_FILE="${FISH_MINIO_ENV_FILE:-$HOME/.config/fish-quality-dashboard/minio.env}"


# ----------------------------------------------------------------------
# 3. 项目内部路径
#
# 所有目录都基于 PROJECT_DIR，不写死项目部署位置。
# ----------------------------------------------------------------------

BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
MEDIAMTX_DIR="$PROJECT_DIR/mediamtx"

MEDIAMTX_BIN="$MEDIAMTX_DIR/mediamtx"
MEDIAMTX_CONFIG="$MEDIAMTX_DIR/mediamtx.yml"

RUNTIME_DIR="$PROJECT_DIR/.runtime"
LOG_DIR="$PROJECT_DIR/data/logs/dev"

MEDIAMTX_PID_FILE="$RUNTIME_DIR/mediamtx.pid"
MINIO_PID_FILE="$RUNTIME_DIR/minio.pid"
WORKER_PID_FILE="$RUNTIME_DIR/rq-worker.pid"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
UPGRADE_MANAGER_PID_FILE="$RUNTIME_DIR/upgrade-manager.pid"

MEDIAMTX_LOG="$LOG_DIR/mediamtx.log"
MINIO_LOG="$LOG_DIR/minio.log"
WORKER_LOG="$LOG_DIR/rq-worker.log"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
UPGRADE_MANAGER_LOG="$LOG_DIR/upgrade-manager.log"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"


# ----------------------------------------------------------------------
# 4. 错误处理
# ----------------------------------------------------------------------

on_error() {
    local exit_code=$?
    local line_number="${1:-unknown}"

    echo
    echo "[ERROR] start_dev.sh 执行失败。"
    echo "[ERROR] 退出码：$exit_code"
    echo "[ERROR] 出错行：$line_number"
    echo "[ERROR] 请检查日志目录：$LOG_DIR"

    exit "$exit_code"
}

trap 'on_error $LINENO' ERR


# ----------------------------------------------------------------------
# 5. 动态识别 Conda 环境目录
#
# 查找顺序：
#   1. 外部传入的 CONDA_ENV_DIR；
#   2. 当前已激活且名称匹配的 CONDA_PREFIX；
#   3. 使用 conda env list 查找指定名称的环境。
#
# 这里不执行 conda activate，因此不会因为非交互式 Shell 没有加载
# ~/.bashrc 而出现 conda activate 初始化失败。
# ----------------------------------------------------------------------

find_conda_env_dir() {
    local conda_bin=""
    local detected_env_dir=""

    # 第一优先级：用户明确传入环境目录。
    if [[ -n "${CONDA_ENV_DIR:-}" ]]; then
        printf '%s\n' "$CONDA_ENV_DIR"
        return 0
    fi

    # 第二优先级：当前终端已经激活了目标环境。
    if [[ -n "${CONDA_PREFIX:-}" ]] &&
       [[ "$(basename -- "$CONDA_PREFIX")" == "$CONDA_ENV_NAME" ]]; then
        printf '%s\n' "$CONDA_PREFIX"
        return 0
    fi

    # 第三优先级：查找 conda 命令。
    if [[ -n "${CONDA_EXE:-}" && -x "${CONDA_EXE:-}" ]]; then
        conda_bin="$CONDA_EXE"
    else
        conda_bin="$(command -v conda 2>/dev/null || true)"
    fi

    if [[ -z "$conda_bin" ]]; then
        return 1
    fi

    # conda env list 每行最后一列是环境完整路径。
    detected_env_dir="$(
        "$conda_bin" env list 2>/dev/null |
        awk -v target="$CONDA_ENV_NAME" '
            $1 == target {
                print $NF
                exit
            }
        '
    )"

    if [[ -z "$detected_env_dir" ]]; then
        return 1
    fi

    printf '%s\n' "$detected_env_dir"
}


if ! CONDA_ENV_DIR="$(find_conda_env_dir)"; then
    echo "[ERROR] 无法找到 Conda 环境：$CONDA_ENV_NAME"
    echo
    echo "请任选一种方式处理："
    echo
    echo "  方式一：先激活环境再启动"
    echo "    conda activate $CONDA_ENV_NAME"
    echo "    ./start_dev.sh"
    echo
    echo "  方式二：明确指定环境目录"
    echo "    CONDA_ENV_DIR=/实际路径/envs/$CONDA_ENV_NAME ./start_dev.sh"
    exit 1
fi

# 将环境目录转换成规范路径。
if [[ ! -d "$CONDA_ENV_DIR" ]]; then
    echo "[ERROR] Conda 环境目录不存在：$CONDA_ENV_DIR"
    exit 1
fi

CONDA_ENV_DIR="$(cd -- "$CONDA_ENV_DIR" && pwd)"

PY="$CONDA_ENV_DIR/bin/python"
NODE="$CONDA_ENV_DIR/bin/node"
NPM="$CONDA_ENV_DIR/bin/npm"


# ----------------------------------------------------------------------
# 6. 设置运行环境
#
# npm、Vite 等脚本通常使用：
#   #!/usr/bin/env node
#
# 所以必须把 Conda 环境的 bin 放在 PATH 最前面，确保 npm 内部调用的
# 也是 full 环境中的 Node.js，而不是 /usr/bin/node。
# ----------------------------------------------------------------------

export PATH="$CONDA_ENV_DIR/bin:$PATH"
export PYTHONNOUSERSITE=1

export DATABASE_URL
export REDIS_URL
export FISH_ALGO_MODE
export FISH_PI_BASE_URL
export FISH_CAMERA_PUSH_URL
export FISH_CAMERA_AUTO_PUSH_URL
export FISH_CAMERA_NETWORK_INTERFACE
export FISH_CAMERA_READ_URL
export FISH_MINIO_AUTOSTART FISH_MINIO_BIN FISH_MINIO_DATA_DIR
export VITE_API_PROXY_TARGET
export UPGRADE_MANAGER_PORT
export UPGRADE_MANAGER_ALLOWED_ORIGINS

hash -r 2>/dev/null || true


# ----------------------------------------------------------------------
# 7. 摄像头网络预检
#
# 自动共享模式必须有主机网线地址；否则继续启动只会让树莓派拿到
# 127.0.0.1 或旧 Wi-Fi 地址，随后 MediaMTX 只能看到 no stream。
# ----------------------------------------------------------------------
if [[ "$FISH_CAMERA_AUTO_PUSH_URL" == "1" ||
      "$FISH_CAMERA_AUTO_PUSH_URL" == "true" ||
      "$FISH_CAMERA_AUTO_PUSH_URL" == "yes" ||
      "$FISH_CAMERA_AUTO_PUSH_URL" == "on" ]]; then
    CAMERA_NETWORK_INTERFACE="${FISH_CAMERA_NETWORK_INTERFACE:-enp3s0}"
    if ! command -v ip >/dev/null 2>&1; then
        echo "[ERROR] 自动推流地址需要 ip 命令来检查网卡。"
        exit 1
    fi
    CAMERA_HOST_IP="$(ip -4 -o addr show dev "$CAMERA_NETWORK_INTERFACE" scope global 2>/dev/null |
        awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
    if [[ -z "$CAMERA_HOST_IP" ]]; then
        for candidate in enp3s0 eno1 enp2s0 wlo1 wlp0s20f3; do
            CAMERA_HOST_IP="$(ip -4 -o addr show dev "$candidate" scope global 2>/dev/null |
                awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
            if [[ -n "$CAMERA_HOST_IP" ]]; then
                CAMERA_NETWORK_INTERFACE="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$CAMERA_HOST_IP" ]]; then
        echo "[ERROR] 网卡 $CAMERA_NETWORK_INTERFACE 没有 IPv4 地址。"
        echo "[ERROR] 请确认 NetworkManager 使用 ipv4.method=shared，或设置 FISH_CAMERA_AUTO_PUSH_URL=0 后提供固定 FISH_CAMERA_PUSH_URL。"
        exit 1
    fi
    export FISH_CAMERA_NETWORK_INTERFACE
    echo "[start_dev] 摄像头网卡：$CAMERA_NETWORK_INTERFACE ($CAMERA_HOST_IP)"
    echo "[start_dev] 自动推流目标：rtmp://${CAMERA_HOST_IP}:1935/fish_camera"
fi


# ----------------------------------------------------------------------
# 8. 检查必要的可执行文件
# ----------------------------------------------------------------------

check_executable() {
    local executable="$1"
    local description="$2"

    if [[ ! -x "$executable" ]]; then
        echo "[ERROR] 找不到 $description：$executable"
        exit 1
    fi
}

check_executable "$PY" "Python"
check_executable "$NODE" "Node.js"
check_executable "$NPM" "npm"
check_executable "$MEDIAMTX_BIN" "MediaMTX"


# ----------------------------------------------------------------------
# 9. 检查项目目录和必要文件
# ----------------------------------------------------------------------

if [[ ! -d "$BACKEND_DIR/app" ]]; then
    echo "[ERROR] 后端目录不存在：$BACKEND_DIR/app"
    exit 1
fi

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
    echo "[ERROR] 前端 package.json 不存在：$FRONTEND_DIR/package.json"
    exit 1
fi

if [[ ! -f "$MEDIAMTX_CONFIG" ]]; then
    echo "[ERROR] MediaMTX 配置不存在：$MEDIAMTX_CONFIG"
    exit 1
fi

if [[ ! -f "$PROJECT_DIR/scripts/rsync_data_backup.sh" ]]; then
    echo "[ERROR] rsync 备份脚本不存在：$PROJECT_DIR/scripts/rsync_data_backup.sh"
    exit 1
fi

if [[ ! -f "$BACKEND_DIR/app/services/update/upgrade_manager/__main__.py" ]]; then
    echo "[ERROR] Upgrade Manager 入口不存在：$BACKEND_DIR/app/services/update/upgrade_manager/__main__.py"
    exit 1
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "[ERROR] 前端依赖尚未安装：$FRONTEND_DIR/node_modules"
    echo
    echo "请先执行："
    echo "  cd \"$FRONTEND_DIR\""
    echo "  \"$NPM\" install"
    exit 1
fi


# ----------------------------------------------------------------------
# 10. 检查 Node.js 版本
#
# Vite 7 要求 Node.js 至少为 20.19.0。
# ----------------------------------------------------------------------

NODE_VERSION_RAW="$("$NODE" --version)"
NODE_VERSION="${NODE_VERSION_RAW#v}"
MINIMUM_NODE_VERSION="20.19.0"

PATH_NODE="$(command -v node 2>/dev/null || true)"
if [[ "$PATH_NODE" != "$NODE" ]]; then
    echo "[ERROR] PATH 中的 Node.js 不是 Conda 环境版本：${PATH_NODE:-未找到}"
    echo "[ERROR] 预期使用：$NODE"
    exit 1
fi
PATH_NODE_VERSION_RAW="$(node --version)"

if [[ "$(printf '%s\n%s\n' "$MINIMUM_NODE_VERSION" "$NODE_VERSION" |
        sort -V |
        head -n1)" != "$MINIMUM_NODE_VERSION" ]]; then
    echo "[ERROR] Node.js 版本过低：$NODE_VERSION_RAW"
    echo "[ERROR] 当前前端至少需要 Node.js v$MINIMUM_NODE_VERSION"
    exit 1
fi


# ----------------------------------------------------------------------
# 11. 显示本次实际使用的环境
# ----------------------------------------------------------------------

echo "======================================================================"
echo "[start_dev] 项目目录：  $PROJECT_DIR"
echo "[start_dev] Conda环境： $CONDA_ENV_DIR"
echo "[start_dev] Python：    $PY"
echo "[start_dev] Python版本：$("$PY" --version 2>&1)"
echo "[start_dev] Node.js：   $NODE"
echo "[start_dev] Node版本：  $NODE_VERSION_RAW"
echo "[start_dev] PATH Node：  $PATH_NODE_VERSION_RAW ($PATH_NODE)"
echo "[start_dev] npm：       $NPM"
echo "[start_dev] npm版本：   $("$NPM" --version)"
echo "[start_dev] 后端端口：  $BACKEND_PORT"
echo "[start_dev] 前端端口：  $FRONTEND_PORT"
echo "[start_dev] API代理：   $VITE_API_PROXY_TARGET"
echo "[start_dev] 日志目录：  $LOG_DIR"
echo "======================================================================"


# ----------------------------------------------------------------------
# 11. PID 与进程辅助函数
# ----------------------------------------------------------------------

pid_is_running() {
    local pid="${1:-}"

    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

read_pid_file() {
    local pid_file="$1"

    if [[ -f "$pid_file" ]]; then
        tr -d '[:space:]' <"$pid_file"
    fi
}

remove_stale_pid_file() {
    local pid_file="$1"
    local service_name="$2"
    local pid=""

    pid="$(read_pid_file "$pid_file")"

    if [[ -n "$pid" ]] && pid_is_running "$pid"; then
        echo "[INFO] $service_name 已经运行，PID=$pid"
        return 1
    fi

    if [[ -f "$pid_file" ]]; then
        rm -f -- "$pid_file"
    fi

    return 0
}

start_detached_process() {
    local service_name="$1"
    local work_dir="$2"
    local log_file="$3"
    local pid_file="$4"
    shift 4

    if ! remove_stale_pid_file "$pid_file" "$service_name"; then
        return 0
    fi

    echo "[INFO] 正在启动 $service_name……"

    (
        cd "$work_dir"

        # 使用 exec 让记录的 PID 直接对应目标程序。
        exec setsid nohup "$@" >>"$log_file" 2>&1
    ) &

    local process_pid=$!
    printf '%s\n' "$process_pid" >"$pid_file"

    sleep 1

    if ! pid_is_running "$process_pid"; then
        echo "[ERROR] $service_name 启动失败。"
        echo "[ERROR] 日志：$log_file"
        tail -n 40 "$log_file" 2>/dev/null || true
        exit 1
    fi

    echo "[OK] $service_name 已启动，PID=$process_pid"
    echo "[OK] 日志：$log_file"
}


# ----------------------------------------------------------------------
# 12. 检查 TCP 端口
# ----------------------------------------------------------------------

port_is_listening() {
    local port="$1"

    if command -v ss >/dev/null 2>&1; then
        ss -lnt 2>/dev/null |
            awk 'NR > 1 {print $4}' |
            grep -Eq "(^|:)$port$"
        return
    fi

    return 1
}

start_minio_if_enabled() {
    case "$FISH_MINIO_AUTOSTART" in
        1|true|yes|on) ;;
        *) echo "[INFO] MinIO 自动启动已关闭（FISH_MINIO_AUTOSTART=$FISH_MINIO_AUTOSTART）"; return 0 ;;
    esac
    if [[ -f "$FISH_MINIO_ENV_FILE" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$FISH_MINIO_ENV_FILE"
        set +a
    fi
    export MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-${MINIO_ROOT_USER:-}}"
    export MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-${MINIO_ROOT_PASSWORD:-}}"
    if port_is_listening "$FISH_MINIO_API_PORT"; then
        echo "[INFO] MinIO 已监听端口 $FISH_MINIO_API_PORT，复用现有服务"
        return 0
    fi
    if [[ -z "$FISH_MINIO_BIN" || ! -x "$FISH_MINIO_BIN" ]]; then
        echo "[ERROR] 已启用 MinIO 自动启动，但找不到 MinIO：${FISH_MINIO_BIN:-未找到}"
        exit 1
    fi
    if [[ -z "${MINIO_ROOT_USER:-}" || -z "${MINIO_ROOT_PASSWORD:-}" ]]; then
        echo "[ERROR] MinIO 自动启动需要 MINIO_ROOT_USER 和 MINIO_ROOT_PASSWORD"
        echo "[ERROR] 可设置环境变量，或创建：$FISH_MINIO_ENV_FILE"
        exit 1
    fi
    mkdir -p "$FISH_MINIO_DATA_DIR"
    start_detached_process "MinIO" "$PROJECT_DIR" "$MINIO_LOG" "$MINIO_PID_FILE" \
        "$FISH_MINIO_BIN" server "$FISH_MINIO_DATA_DIR" \
        --address ":$FISH_MINIO_API_PORT" --console-address ":$FISH_MINIO_CONSOLE_PORT"
}

start_minio_if_enabled


# ----------------------------------------------------------------------
# 13. 启动 PostgreSQL 和 Redis
#
# 脚本不会写死服务程序路径。
# 如果服务已经运行，则直接继续。
#
# systemctl 启动系统服务通常需要 sudo 权限。脚本会先尝试普通启动，
# 失败后在交互式终端中使用 sudo。
# ----------------------------------------------------------------------

start_system_service_if_needed() {
    local service_name="$1"
    local check_port="$2"

    if port_is_listening "$check_port"; then
        echo "[INFO] $service_name 已监听端口 $check_port"
        return 0
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        echo "[ERROR] 未找到 systemctl，无法启动 $service_name"
        exit 1
    fi

    echo "[INFO] 正在启动系统服务：$service_name"

    if systemctl start "$service_name" 2>/dev/null; then
        :
    elif [[ -t 0 ]] && command -v sudo >/dev/null 2>&1; then
        sudo systemctl start "$service_name"
    else
        echo "[ERROR] 无法启动 $service_name。"
        echo "[ERROR] 请手动执行：sudo systemctl start $service_name"
        exit 1
    fi

    sleep 1

    if ! port_is_listening "$check_port"; then
        echo "[ERROR] $service_name 启动后仍未监听端口 $check_port"
        echo "[ERROR] 请检查：systemctl status $service_name"
        exit 1
    fi

    echo "[OK] $service_name 已启动"
}


start_system_service_if_needed "postgresql" "5432"
start_system_service_if_needed "redis-server" "6379"


# ----------------------------------------------------------------------
# 14. 检查 PostgreSQL 和 Redis 连通性
# ----------------------------------------------------------------------

if command -v pg_isready >/dev/null 2>&1; then
    if ! pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
        echo "[ERROR] PostgreSQL 未通过连接检查。"
        exit 1
    fi

    echo "[OK] PostgreSQL 连接检查通过"
fi

if command -v redis-cli >/dev/null 2>&1; then
    if ! redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null |
         grep -qx "PONG"; then
        echo "[ERROR] Redis 未通过连接检查。"
        exit 1
    fi

    echo "[OK] Redis 连接检查通过"
fi


# ----------------------------------------------------------------------
# 15. 启动 MediaMTX
# ----------------------------------------------------------------------

chmod +x "$MEDIAMTX_BIN"

start_detached_process \
    "MediaMTX" \
    "$MEDIAMTX_DIR" \
    "$MEDIAMTX_LOG" \
    "$MEDIAMTX_PID_FILE" \
    "$MEDIAMTX_BIN" \
    "$MEDIAMTX_CONFIG"


# ----------------------------------------------------------------------
# 16. 启动 RQ Worker
#
# 后端工作目录必须是 backend，确保 Python 可以导入 app。
# ----------------------------------------------------------------------

start_detached_process \
    "RQ Worker" \
    "$BACKEND_DIR" \
    "$WORKER_LOG" \
    "$WORKER_PID_FILE" \
    "$PY" \
    -m app.workers.worker_main

start_detached_process \
    "Upgrade Manager" \
    "$BACKEND_DIR" \
    "$UPGRADE_MANAGER_LOG" \
    "$UPGRADE_MANAGER_PID_FILE" \
    "$PY" \
    -m app.services.update.upgrade_manager


# ----------------------------------------------------------------------
# 17. 启动 FastAPI 后端
# ----------------------------------------------------------------------

if port_is_listening "$BACKEND_PORT"; then
    echo "[INFO] 端口 $BACKEND_PORT 已被占用，跳过后端启动。"
    echo "[INFO] 如果不是现有后端，请先停止占用该端口的进程。"
else
    start_detached_process \
        "FastAPI 后端" \
        "$BACKEND_DIR" \
        "$BACKEND_LOG" \
        "$BACKEND_PID_FILE" \
        "$PY" \
        -m uvicorn \
        app.main:app \
        --host 0.0.0.0 \
        --port "$BACKEND_PORT"
fi


# ----------------------------------------------------------------------
# 18. 等待后端健康检查
# ----------------------------------------------------------------------

wait_for_backend() {
    local health_url="http://127.0.0.1:${BACKEND_PORT}/docs"
    local attempt=""

    if ! command -v curl >/dev/null 2>&1; then
        echo "[WARN] 未安装 curl，跳过后端 HTTP 健康检查。"
        return 0
    fi

    echo "[INFO] 等待后端就绪：$health_url"

    # 后端启动时会执行数据库迁移、队列恢复和摄像头状态恢复，首次启动可能超过 30 秒。
    for attempt in {1..90}; do
        if curl \
            --noproxy '*' \
            --silent \
            --fail \
            --max-time 2 \
            "$health_url" \
            >/dev/null 2>&1; then
            echo "[OK] 后端健康检查通过"
            return 0
        fi

        sleep 1
    done

    echo "[ERROR] 后端在 90 秒内没有通过健康检查。"
    echo "[ERROR] 后端日志：$BACKEND_LOG"
    tail -n 60 "$BACKEND_LOG" 2>/dev/null || true
    exit 1
}

wait_for_backend


# ----------------------------------------------------------------------
# 19. 启动 Vite 前端开发服务器
#
# 必须使用 full 环境中的 npm。
# PATH 也已经将 full/bin 放在最前，因此 npm 内部调用的是 Node.js 20。
# ----------------------------------------------------------------------

if port_is_listening "$FRONTEND_PORT"; then
    echo "[INFO] 端口 $FRONTEND_PORT 已被占用，跳过前端启动。"
    echo "[INFO] 如果不是现有前端，请先停止占用该端口的进程。"
else
    start_detached_process \
        "Vite 前端" \
        "$FRONTEND_DIR" \
        "$FRONTEND_LOG" \
        "$FRONTEND_PID_FILE" \
        "$NPM" \
        run dev -- \
        --host 0.0.0.0 \
        --port "$FRONTEND_PORT" \
        --strictPort
fi


# ----------------------------------------------------------------------
# 20. 输出启动结果
# ----------------------------------------------------------------------

echo
echo "======================================================================"
echo "[OK] 鱼品质检测系统开发环境启动完成"
echo
echo "前端地址："
echo "  http://127.0.0.1:$FRONTEND_PORT"
echo
echo "后端接口文档："
echo "  http://127.0.0.1:$BACKEND_PORT/docs"
echo
echo "MediaMTX："
echo "  RTMP    rtmp://127.0.0.1:1935/fish_camera"
echo "  RTSP    rtsp://127.0.0.1:8554/fish_camera"
echo "  HLS     http://127.0.0.1:8888/fish_camera/index.m3u8"
echo "  WebRTC  http://127.0.0.1:8889/fish_camera"
echo
echo "日志目录："
echo "  $LOG_DIR"
echo
echo "查看后端日志："
echo "  tail -f \"$BACKEND_LOG\""
echo
echo "查看前端日志："
echo "  tail -f \"$FRONTEND_LOG\""
echo
echo "查看 Worker 日志："
echo "  tail -f \"$WORKER_LOG\""
echo
echo "查看 MediaMTX 日志："
echo "  tail -f \"$MEDIAMTX_LOG\""
echo "======================================================================"
