#!/usr/bin/env bash
# Build the backend as a relocatable PyInstaller onedir application.
#
# The script deliberately resolves every path from its own location.  It can
# therefore be invoked from any working directory and does not bake the
# build-machine's absolute project path into the runtime configuration.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
SPEC_FILE="$SCRIPT_DIR/fish_quality_backend.spec"
DIST_ROOT="${BACKEND_DIST_ROOT:-$PROJECT_ROOT/dist/backend}"
WORK_ROOT="${BACKEND_BUILD_ROOT:-$PROJECT_ROOT/build/pyinstaller-backend}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-full}"

find_conda_env() {
    if [[ -n "${CONDA_ENV_DIR:-}" ]]; then
        printf '%s\n' "$CONDA_ENV_DIR"
        return 0
    fi
    if [[ -n "${CONDA_PREFIX:-}" && "$(basename -- "$CONDA_PREFIX")" == "$CONDA_ENV_NAME" ]]; then
        printf '%s\n' "$CONDA_PREFIX"
        return 0
    fi
    local conda_bin="${CONDA_EXE:-$(command -v conda 2>/dev/null || true)}"
    [[ -n "$conda_bin" ]] || return 1
    "$conda_bin" env list 2>/dev/null | awk -v target="$CONDA_ENV_NAME" '$1 == target {print $NF; exit}'
}

CONDA_ENV_DIR="$(find_conda_env || true)"
if [[ -z "$CONDA_ENV_DIR" ]]; then
    echo "[ERROR] 找不到 Conda 环境：$CONDA_ENV_NAME" >&2
    echo "        可设置 CONDA_ENV_DIR=/absolute/path/to/env 后重试。" >&2
    exit 1
fi

PYTHON_BIN="$CONDA_ENV_DIR/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "[ERROR] 找不到 Conda 环境 $CONDA_ENV_NAME 的 Python：$PYTHON_BIN" >&2
    exit 1
fi
if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
    echo "[ERROR] Conda 环境 $CONDA_ENV_NAME 未安装 PyInstaller" >&2
    exit 1
fi

if [[ ! -f "$SPEC_FILE" ]]; then
    echo "[ERROR] PyInstaller spec 不存在：$SPEC_FILE" >&2
    exit 1
fi

mkdir -p "$DIST_ROOT" "$WORK_ROOT"
echo "[INFO] 使用 Python：$PYTHON_BIN"
echo "[INFO] 项目目录：$PROJECT_ROOT"
echo "[INFO] 输出目录：$DIST_ROOT"

"$PYTHON_BIN" -m PyInstaller --noconfirm --clean \
    --distpath "$DIST_ROOT" --workpath "$WORK_ROOT" "$SPEC_FILE"

RUNTIME_ROOT="$DIST_ROOT/fish-quality-backend"
[[ -x "$RUNTIME_ROOT/fish-quality-backend" ]] || {
    echo "[ERROR] PyInstaller 未生成后端可执行文件：$RUNTIME_ROOT/fish-quality-backend" >&2
    exit 1
}

# Runtime scripts and mutable configuration live beside the executable.  This
# is important for an onedir build: PyInstaller's _internal directory may be
# read-only or recreated, while data/ and config/ must remain writable.
install -m 0755 "$SCRIPT_DIR/runtime/start_backend.sh" "$RUNTIME_ROOT/start_backend.sh"
install -m 0755 "$SCRIPT_DIR/runtime/stop_backend.sh" "$RUNTIME_ROOT/stop_backend.sh"
mkdir -p "$RUNTIME_ROOT/config" "$RUNTIME_ROOT/data" "$RUNTIME_ROOT/mediamtx"
install -m 0644 "$SCRIPT_DIR/backend.env.example" "$RUNTIME_ROOT/config/backend.env.example"
install -m 0644 "$SCRIPT_DIR/README.md" "$RUNTIME_ROOT/README.md"
install -m 0644 "$PROJECT_ROOT/version.json" "$RUNTIME_ROOT/version.json"
if [[ ! -f "$RUNTIME_ROOT/config/backend.env" ]]; then
    install -m 0644 "$SCRIPT_DIR/backend.env.example" "$RUNTIME_ROOT/config/backend.env"
fi

[[ -x "$PROJECT_ROOT/mediamtx/mediamtx" ]] || {
    echo "[ERROR] MediaMTX 可执行文件不存在：$PROJECT_ROOT/mediamtx/mediamtx" >&2
    exit 1
}
[[ -f "$PROJECT_ROOT/mediamtx/mediamtx.yml" ]] || {
    echo "[ERROR] MediaMTX 配置不存在：$PROJECT_ROOT/mediamtx/mediamtx.yml" >&2
    exit 1
}
install -m 0755 "$PROJECT_ROOT/mediamtx/mediamtx" "$RUNTIME_ROOT/mediamtx/mediamtx"
install -m 0644 "$PROJECT_ROOT/mediamtx/mediamtx.yml" "$RUNTIME_ROOT/mediamtx/mediamtx.yml"

if [[ -f "$PROJECT_ROOT/scripts/rsync_data_backup.sh" ]]; then
    mkdir -p "$RUNTIME_ROOT/scripts"
    install -m 0755 "$PROJECT_ROOT/scripts/rsync_data_backup.sh" "$RUNTIME_ROOT/scripts/rsync_data_backup.sh"
fi

if [[ "${INCLUDE_ALGORITHM_ASSETS:-0}" == "1" ]]; then
    command -v rsync >/dev/null 2>&1 || {
        echo "[ERROR] 复制算法资源需要 rsync，请先安装 rsync。" >&2
        exit 1
    }
    [[ -d "$PROJECT_ROOT/algorithms/pth" ]] || {
        echo "[ERROR] 模型目录不存在：$PROJECT_ROOT/algorithms/pth" >&2
        exit 1
    }
    [[ -d "$PROJECT_ROOT/algorithms/vendor" ]] || {
        echo "[ERROR] 算法 vendor 目录不存在：$PROJECT_ROOT/algorithms/vendor" >&2
        exit 1
    }
    mkdir -p "$RUNTIME_ROOT/algorithms"
    rsync -a --delete --exclude='__pycache__' --exclude='.git' \
        "$PROJECT_ROOT/algorithms/pth/" "$RUNTIME_ROOT/algorithms/pth/"
    rsync -a --delete --exclude='__pycache__' --exclude='.git' \
        "$PROJECT_ROOT/algorithms/vendor/" "$RUNTIME_ROOT/algorithms/vendor/"
    rsync -a --delete --exclude='__pycache__' --exclude='.git' \
        "$PROJECT_ROOT/algorithms/config/" "$RUNTIME_ROOT/algorithms/config/"
    # algorithms/core and algorithms/tools are collected into PyInstaller's
    # PYZ archive through hiddenimports.  The only source file kept as a data
    # resource is arched-back.py, whose hyphenated name requires runpy.run_path
    # at runtime (see the spec and run_video_pipeline).
    echo "[INFO] 正在检查冻结版运行时和算法依赖……"
    if ! "$RUNTIME_ROOT/fish-quality-backend" doctor --deep; then
        echo "[ERROR] 后端深度自检失败；请检查上方依赖/模型错误。" >&2
        exit 1
    fi
else
    echo "[WARN] 未复制算法模型和 vendor 资源；完整交付请设置 INCLUDE_ALGORITHM_ASSETS=1。" >&2
fi

echo "[OK] PyInstaller onedir：$RUNTIME_ROOT"
