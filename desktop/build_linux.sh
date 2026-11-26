#!/usr/bin/env bash
set -Eeuo pipefail

DESKTOP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-full}"
TARGET="${1:-appimage}"

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

CONDA_ENV_DIR="$(find_conda_env)"
NODE_BIN="$CONDA_ENV_DIR/bin/node"
NPM_CLI="$CONDA_ENV_DIR/lib/node_modules/npm/bin/npm-cli.js"
if [[ ! -x "$NODE_BIN" || ! -f "$NPM_CLI" ]]; then
    echo "[ERROR] $CONDA_ENV_NAME 环境中的 Node.js/npm 不完整：$CONDA_ENV_DIR" >&2
    exit 1
fi

export PATH="$CONDA_ENV_DIR/bin:$PATH"
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"
export npm_config_registry="${NPM_REGISTRY:-https://registry.npmmirror.com}"

cd "$DESKTOP_DIR"
"$CONDA_ENV_DIR/bin/python" "$DESKTOP_DIR/scripts/generate_linux_icons.py"
if [[ ! -x node_modules/.bin/electron-builder ]]; then
    "$NODE_BIN" "$NPM_CLI" install --no-audit --no-fund
fi

case "$TARGET" in
    appimage) "$NODE_BIN" "$NPM_CLI" run dist:linux ;;
    dir) "$NODE_BIN" "$NPM_CLI" run pack:linux ;;
    *) echo "用法：$0 [appimage|dir]" >&2; exit 2 ;;
esac

echo "[OK] Electron Linux 产物：$DESKTOP_DIR/dist"
