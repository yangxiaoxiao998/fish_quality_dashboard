#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BACKEND_SOURCE="${BACKEND_SOURCE:-$PROJECT_ROOT/dist/backend/fish-quality-backend}"
FRONTEND_DIST="${FRONTEND_DIST:-$PROJECT_ROOT/desktop/dist}"
TEMPLATE_ROOT="${TEMPLATE_ROOT:-$PROJECT_ROOT/packaging/linux-release}"
RELEASE_ROOT="${RELEASE_ROOT:-$PROJECT_ROOT/dist/fish-quality-linux-x86_64}"

[[ -x "$BACKEND_SOURCE/fish-quality-backend" ]] || {
    echo "[ERROR] 后端产物不存在：$BACKEND_SOURCE" >&2
    exit 1
}

shopt -s nullglob
appimages=("$FRONTEND_DIST"/fish-quality-desktop-*-x86_64.AppImage)
shopt -u nullglob
(( ${#appimages[@]} > 0 )) || {
    echo "[ERROR] 前端 AppImage 不存在：$FRONTEND_DIST" >&2
    exit 1
}
FRONTEND_SOURCE="$(ls -1t "${appimages[@]}" | head -n 1)"

# Do not replace a release directory while one of its own backend processes is
# running.  This avoids corrupting a live installation during a rebuild.
if [[ -d "$RELEASE_ROOT/backend/data/run" ]]; then
    for pid_file in "$RELEASE_ROOT"/backend/data/run/*.pid; do
        [[ -f "$pid_file" ]] || continue
        pid="$(tr -d '[:space:]' <"$pid_file")"
        if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
            echo "[ERROR] 交付目录正在运行（PID=$pid），请先执行 $RELEASE_ROOT/stop.sh" >&2
            exit 1
        fi
    done
fi

command -v rsync >/dev/null 2>&1 || {
    echo "[ERROR] 组装交付目录需要 rsync，请先安装 rsync。" >&2
    exit 1
}
mkdir -p "$RELEASE_ROOT/frontend" "$RELEASE_ROOT/backend"
echo "[INFO] 正在复制后端和模型（保留现有 data、backups、backend.env）……"
rsync -a --delete \
    --exclude='/data/' \
    --exclude='/backups/' \
    --exclude='/config/backend.env' \
    "$BACKEND_SOURCE/" "$RELEASE_ROOT/backend/"
mkdir -p \
    "$RELEASE_ROOT/backend/data/logs/runtime" \
    "$RELEASE_ROOT/backend/data/run" \
    "$RELEASE_ROOT/backend/data/uploads/videos/playable" \
    "$RELEASE_ROOT/backend/data/jobs" \
    "$RELEASE_ROOT/backend/data/results" \
    "$RELEASE_ROOT/backend/backups" \
    "$RELEASE_ROOT/backend/config"
if [[ ! -f "$RELEASE_ROOT/backend/config/backend.env" ]]; then
    install -m 0644 \
        "$RELEASE_ROOT/backend/config/backend.env.example" \
        "$RELEASE_ROOT/backend/config/backend.env"
fi
echo "[INFO] 正在复制 Electron AppImage……"
find "$RELEASE_ROOT/frontend" -maxdepth 1 -type f -name '*.AppImage' -delete
install -m 0755 "$FRONTEND_SOURCE" "$RELEASE_ROOT/frontend/$(basename -- "$FRONTEND_SOURCE")"
install -m 0755 "$TEMPLATE_ROOT/start.sh" "$RELEASE_ROOT/start.sh"
install -m 0755 "$TEMPLATE_ROOT/stop.sh" "$RELEASE_ROOT/stop.sh"
install -m 0644 "$TEMPLATE_ROOT/README.md" "$RELEASE_ROOT/README.md"
install -m 0644 "$PROJECT_ROOT/desktop/build/icons/512x512.png" "$RELEASE_ROOT/fish-quality-desktop.png"
mkdir -p "$RELEASE_ROOT/icons"
rsync -a --delete "$PROJECT_ROOT/desktop/build/icons/" "$RELEASE_ROOT/icons/"
sed "s|@RELEASE_ROOT@|$RELEASE_ROOT|g" \
    "$TEMPLATE_ROOT/fish-quality-packaged.desktop" >"$RELEASE_ROOT/fish-quality-packaged.desktop"
chmod 0644 "$RELEASE_ROOT/fish-quality-packaged.desktop"
install -m 0644 "$PROJECT_ROOT/version.json" "$RELEASE_ROOT/version.json"

install_user_desktop_integration() {
    [[ "${INSTALL_DESKTOP_INTEGRATION:-1}" == "1" ]] || return 0
    [[ -n "${HOME:-}" && -d "$HOME" ]] || return 0

    local applications_dir="$HOME/.local/share/applications"
    local icon_theme_root="$HOME/.local/share/icons/hicolor"
    local desktop_dir="$HOME/Desktop"
    local desktop_shortcut=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || printf '%s' "$desktop_dir")"
    fi

    mkdir -p "$applications_dir"
    install -m 0644 \
        "$RELEASE_ROOT/fish-quality-packaged.desktop" \
        "$applications_dir/fish-quality-desktop.desktop"

    for icon in "$PROJECT_ROOT"/desktop/build/icons/*x*.png; do
        [[ -f "$icon" ]] || continue
        local size
        size="$(basename -- "$icon" .png)"
        mkdir -p "$icon_theme_root/$size/apps"
        install -m 0644 "$icon" "$icon_theme_root/$size/apps/fish-quality-desktop.png"
    done

    if [[ -d "$desktop_dir" ]]; then
        desktop_shortcut="$desktop_dir/鱼品质智能检测系统-打包版.desktop"
        install -m 0755 \
            "$RELEASE_ROOT/fish-quality-packaged.desktop" \
            "$desktop_shortcut"
        if command -v gio >/dev/null 2>&1; then
            gio set -t string "$desktop_shortcut" metadata::trusted true >/dev/null 2>&1 || true
        fi
    fi

    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "$icon_theme_root" >/dev/null 2>&1 || true
}

install_user_desktop_integration

echo "[INFO] 正在运行后端深度自检……"
"$RELEASE_ROOT/backend/fish-quality-backend" doctor --deep
echo "[OK] Linux 统一交付目录：$RELEASE_ROOT"
du -sh "$RELEASE_ROOT"
