#!/usr/bin/env bash
set -Eeuo pipefail

RELEASE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_ROOT="$RELEASE_ROOT/backend"
FRONTEND_ROOT="$RELEASE_ROOT/frontend"
BACKEND_START="$BACKEND_ROOT/start_backend.sh"
BACKEND_BIN="$BACKEND_ROOT/fish-quality-backend"
MEDIAMTX_BIN="$BACKEND_ROOT/mediamtx/mediamtx"
RUN_ROOT="$BACKEND_ROOT/data/run"

if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "[ERROR] 当前安装包仅支持 x86_64 Linux，当前架构：$(uname -m)" >&2
    exit 1
fi
[[ -x "$BACKEND_START" ]] || { echo "[ERROR] 后端启动脚本不存在：$BACKEND_START" >&2; exit 1; }

shopt -s nullglob
appimages=("$FRONTEND_ROOT"/*.AppImage)
shopt -u nullglob
if (( ${#appimages[@]} != 1 )); then
    echo "[ERROR] frontend 目录中必须有且只有一个 AppImage，当前：${#appimages[@]}" >&2
    exit 1
fi
FRONTEND_APP="${appimages[0]}"
chmod +x "$FRONTEND_APP"

register_desktop_application() {
    local applications_dir="$HOME/.local/share/applications"
    local icon_theme_root="$HOME/.local/share/icons/hicolor"
    local desktop_file="$applications_dir/fish-quality-desktop.desktop"
    local release_icon="$RELEASE_ROOT/fish-quality-desktop.png"

    mkdir -p "$applications_dir" "$icon_theme_root/512x512/apps"
    if [[ -f "$release_icon" ]]; then
        install -m 0644 "$release_icon" "$icon_theme_root/512x512/apps/fish-quality-desktop.png"
    fi
    for icon in "$RELEASE_ROOT"/icons/*x*.png; do
        [[ -f "$icon" ]] || continue
        local size
        size="$(basename -- "$icon" .png)"
        mkdir -p "$icon_theme_root/$size/apps"
        install -m 0644 "$icon" "$icon_theme_root/$size/apps/fish-quality-desktop.png"
    done
    cat >"$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=鱼品质智能检测系统
Name[zh_CN]=鱼品质智能检测系统
GenericName=鱼品质智能检测系统
Comment=鱼品质智能检测系统 Linux 交付版
Exec="$RELEASE_ROOT/start.sh"
Path=$RELEASE_ROOT
Icon=fish-quality-desktop
Terminal=false
Categories=Science;
StartupWMClass=fish-quality-desktop
StartupNotify=true
EOF
    chmod 0644 "$desktop_file"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "$icon_theme_root" >/dev/null 2>&1 || true
}

register_desktop_application

# A developer checkout can be configured as a systemd --user autostart on the
# same workstation. It uses the same fixed ports (8000/8012/1935), so the
# packaged launcher must stop that source stack before starting; otherwise the
# AppImage silently connects to the source DATA_DIR and the packaged backend
# never starts. This operation is a no-op on delivery machines without those
# units.
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet fish-quality.target 2>/dev/null; then
    echo "[INFO] 检测到源码版后台服务，正在停止以切换到打包版……"
    systemctl --user stop fish-quality.target || true
fi
PROJECT_CHECKOUT="$(cd -- "$RELEASE_ROOT/../.." && pwd -P)"
pkill -TERM -f "$PROJECT_CHECKOUT/desktop/node_modules/.bin/electron" 2>/dev/null || true

clean_foreign_pid() {
    local pid_file="$1" expected_executable="$2" pid="" actual=""
    [[ -f "$pid_file" ]] || return 0
    pid="$(tr -d '[:space:]' <"$pid_file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        actual="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
        [[ "$actual" == "$expected_executable" ]] && return 0
    fi
    rm -f -- "$pid_file"
}

mkdir -p "$RUN_ROOT"
clean_foreign_pid "$RUN_ROOT/api.pid" "$BACKEND_BIN"
clean_foreign_pid "$RUN_ROOT/worker.pid" "$BACKEND_BIN"
clean_foreign_pid "$RUN_ROOT/upgrade-manager.pid" "$BACKEND_BIN"
clean_foreign_pid "$RUN_ROOT/mediamtx.pid" "$MEDIAMTX_BIN"

echo "============================================================"
echo " 鱼品质智能检测系统"
echo " 前端：$FRONTEND_APP"
echo " 后端：$BACKEND_ROOT"
echo "============================================================"

"$BACKEND_START"
export FISH_BACKEND_ORIGIN="${FISH_BACKEND_ORIGIN:-http://127.0.0.1:8000}"
echo "[OK] 后端已就绪，正在打开桌面端……"
exec "$FRONTEND_APP"
