#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONDA_ENV_DIR="${CONDA_ENV_DIR:-/home/$USER/miniconda3/envs/full}"
TEMPLATE_DIR="$PROJECT_ROOT/packaging/systemd-user"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
CONFIG_DIR="$HOME/.config/fish-quality"
ENV_FILE="$CONFIG_DIR/source.env"

[[ -x "$CONDA_ENV_DIR/bin/python" ]] || {
    echo "[ERROR] Python 不存在：$CONDA_ENV_DIR/bin/python" >&2
    exit 1
}
[[ -x "$CONDA_ENV_DIR/bin/npm" ]] || {
    echo "[ERROR] npm 不存在：$CONDA_ENV_DIR/bin/npm" >&2
    exit 1
}

mkdir -p "$SYSTEMD_USER_DIR" "$AUTOSTART_DIR" "$CONFIG_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
    install -m 0600 /dev/null "$ENV_FILE"
    printf '%s\n' \
        'DATABASE_URL=postgresql://fish:fish@127.0.0.1:5432/fishdb' \
        'REDIS_URL=redis://127.0.0.1:6379/0' \
        'FISH_PI_BASE_URL=http://raspberrypi.local:9000' \
        'FISH_CAMERA_AUTO_PUSH_URL=1' \
        'FISH_CAMERA_NETWORK_INTERFACE=enp3s0' \
        'FISH_CAMERA_READ_URL=rtmp://127.0.0.1:1935/fish_camera' \
        'FISH_CAMERA_RECONNECT_GRACE_SECONDS=180' \
        'FISH_CAMERA_RECONNECT_REQUEST_INTERVAL_SECONDS=10' \
        'VITE_API_PROXY_TARGET=http://127.0.0.1:8000' \
        'UPGRADE_MANAGER_PORT=8012' \
        >"$ENV_FILE"
fi

render() {
    local source="$1" target="$2"
    sed \
        -e "s|@PROJECT_ROOT@|$PROJECT_ROOT|g" \
        -e "s|@CONDA_ENV_DIR@|$CONDA_ENV_DIR|g" \
        "$source" >"$target"
}

for name in mediamtx worker upgrade-manager backend frontend watchdog; do
    render \
        "$TEMPLATE_DIR/fish-quality-$name.service.in" \
        "$SYSTEMD_USER_DIR/fish-quality-$name.service"
done
install -m 0644 "$TEMPLATE_DIR/fish-quality.target" "$SYSTEMD_USER_DIR/fish-quality.target"
render \
    "$TEMPLATE_DIR/fish-quality-desktop.desktop.in" \
    "$AUTOSTART_DIR/fish-quality-desktop.desktop"

systemctl --user daemon-reload
systemctl --user enable fish-quality.target
if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$USER"
fi

echo "[OK] 已安装源码模式自动启动。"
echo "[INFO] systemd user 在主机启动后自动启动后台服务，Electron 在桌面登录后自动打开。"
echo "[INFO] 环境配置：$ENV_FILE"
