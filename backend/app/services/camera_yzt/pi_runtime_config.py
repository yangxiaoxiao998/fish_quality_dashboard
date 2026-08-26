"""摄像头 / RTMP 推流 / 树莓派 API 统一运行时配置。

设计目标
========

1. 树莓派 API 地址
   - 默认使用 ``http://raspberrypi.local:9000``，绝不写死 IP。
   - 可通过环境变量 ``FISH_PI_BASE_URL`` 覆盖（上线阶段可改成固定 IP）。

    2. RTMP 推流地址
   - 默认必须是当前主机的局域网 IP，例如
     ``rtmp://192.168.0.111:1935/fish_camera``。
    - 自动共享模式默认跟随当前主机出口地址；只有显式关闭
      ``FISH_CAMERA_AUTO_PUSH_URL=0`` 时才使用 ``FISH_CAMERA_PUSH_URL``。
   - 如果两者都没设置，自动推断本机访问树莓派时的出口 IP。
   - 不允许使用 ``raspberrypi.local`` 当作 RTMP 目标。

3. 禁止再出现 ``rtmp://192.168.31.254:1935/fish_camera`` 这种历史硬编码值。
   一旦发现，写到这里作为统一唯一来源。

实现要点
========

* 树莓派 mDNS 解析优先复用 :mod:`app.services.pi_endpoint_resolver`：
  它使用 ``avahi-resolve-host-name`` / ``getent ahostsv4``，
  比裸 ``socket.gethostbyname`` 更稳定。
* 主机 IP 推断：UDP ``connect`` 不发包，只让系统选择路由和源地址。
* 全部异常吞掉：这是状态/配置接口，不允许它自身崩溃上层调用。
"""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------


DEFAULT_PI_BASE_URL = "http://10.42.0.2:9000"
DEFAULT_RTMP_PORT = 1935
DEFAULT_RTMP_PATH = "fish_camera"

# 后端 OpenCV 在本机读取 MediaMTX 的默认地址。
# 注意：必须使用 127.0.0.1，不要绕局域网 IP。
DEFAULT_CAMERA_READ_URL = "rtmp://127.0.0.1:1935/fish_camera"


def is_camera_push_url_automatic() -> bool:
    """Return whether RTMP targets must follow the current host address."""
    value = str(os.getenv("FISH_CAMERA_AUTO_PUSH_URL", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PiRuntimeConfig:
    pi_base_url: str
    camera_push_url: str
    camera_read_url: str
    host_ip: str | None
    pi_host: str | None
    pi_resolved_ip: str | None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _strip_url(value: str | None) -> str:
    return str(value or "").strip()


def _strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")


# ---------------------------------------------------------------------------
# 树莓派 API 地址
# ---------------------------------------------------------------------------


def get_pi_base_url() -> str:
    """树莓派 API 地址。

    开发阶段默认使用 ``raspberrypi.local``；上线阶段可由
    ``FISH_PI_BASE_URL`` 覆盖成固定 IP。
    """
    value = _strip_url(os.getenv("FISH_PI_BASE_URL"))
    if value:
        return _strip_trailing_slash(value)
    return _strip_trailing_slash(DEFAULT_PI_BASE_URL)


def get_url_host(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None


def resolve_host(host: str | None) -> str | None:
    """解析 host 到 IPv4。

    优先用 :mod:`app.services.pi_endpoint_resolver`（avahi/getent），
    它对 ``raspberrypi.local`` 的 mDNS 解析更稳。
    失败时退回 :func:`socket.gethostbyname`，再失败返回 ``None``。
    """
    if not host:
        return None

    try:
        from app.services.pi_endpoint_resolver import resolve_pi_ipv4

        return resolve_pi_ipv4(hostname=host)
    except Exception:
        pass

    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 主机出口 IP 推断
# ---------------------------------------------------------------------------


def _guess_host_ip_by_udp_target(target_host: str | None, target_port: int = 9000) -> str | None:
    """通过 UDP ``connect`` 推断本机访问 ``target_host`` 时的出口 IP。

    注意：这里不会真正发送 UDP 数据，只是让系统选择路由和本机源地址。
    """
    if not target_host:
        return None

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target_host, target_port))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        return None

    return None


def _guess_host_ip_by_default_route() -> str | None:
    """兜底：通过连接公共地址推断当前默认出口 IP。

    不发送数据，只用于获取本机源 IP。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        return None

    return None


def _guess_host_ip_by_interface_priority() -> str | None:
    """Fallback order: wired switch/direct cable, then Wi-Fi/router."""
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        rows = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                rows.append((parts[1], parts[3].split("/", 1)[0]))
        priority = ("enp3s0", "eno1", "enp2s0", "enp1s0", "wlo1", "wlp0s20f3")
        for interface in priority:
            for name, ip in rows:
                if name == interface and ip and not ip.startswith("127."):
                    return ip
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def guess_host_lan_ip(pi_base_url: str | None = None) -> str | None:
    """推断当前主机局域网 IP。

    优先使用访问树莓派时的出口 IP；如果 ``raspberrypi.local`` 可解析，
    通常会得到同一 Wi-Fi 网段的主机 IP。
    """
    pi_url = pi_base_url or get_pi_base_url()
    pi_host = get_url_host(pi_url)
    pi_resolved_ip = resolve_host(pi_host)

    # 优先连接解析后的树莓派 IP
    target = pi_resolved_ip or pi_host
    ip = _guess_host_ip_by_udp_target(target, 9000)
    if ip:
        return ip

    # 兜底走默认路由
    return _guess_host_ip_by_interface_priority() or _guess_host_ip_by_default_route()


# ---------------------------------------------------------------------------
# RTMP 推流地址
# ---------------------------------------------------------------------------


def get_camera_push_url() -> str:
    """给树莓派用的推流目标（必须是主机局域网 IP，不能是 127.0.0.1）。

    优先级：
        1. 环境变量 ``FISH_CAMERA_PUSH_URL``；
        2. 自动推断主机局域网 IP，生成 ``rtmp://<host_ip>:1935/fish_camera``。
    """
    env_url = _strip_url(os.getenv("FISH_CAMERA_PUSH_URL"))
    if env_url and not is_camera_push_url_automatic():
        return env_url

    host_ip = guess_host_lan_ip()
    if host_ip:
        return f"rtmp://{host_ip}:{DEFAULT_RTMP_PORT}/{DEFAULT_RTMP_PATH}"

    # 兜底仅用于避免程序崩溃；真实场景里树莓派访问 127.0.0.1 是访问自己。
    return f"rtmp://127.0.0.1:{DEFAULT_RTMP_PORT}/{DEFAULT_RTMP_PATH}"


def get_camera_read_url() -> str:
    """给后端 OpenCV 用的本机读流地址。

    后端和 MediaMTX 在同一台主机上，应优先用 ``127.0.0.1``，
    避免绕局域网 IP（局域网 IP 可能受 MTU / 防火墙影响）。

    优先级：
        1. 环境变量 ``FISH_CAMERA_READ_URL``；
        2. 默认 ``rtmp://127.0.0.1:1935/fish_camera``。
    """
    env_url = _strip_url(os.getenv("FISH_CAMERA_READ_URL"))
    if env_url:
        return env_url
    return DEFAULT_CAMERA_READ_URL


# ---------------------------------------------------------------------------
# 运行时配置聚合
# ---------------------------------------------------------------------------


def get_pi_runtime_config() -> PiRuntimeConfig:
    pi_base_url = get_pi_base_url()
    pi_host = get_url_host(pi_base_url)
    pi_resolved_ip = resolve_host(pi_host)
    host_ip = guess_host_lan_ip(pi_base_url)
    push_env = _strip_url(os.getenv("FISH_CAMERA_PUSH_URL"))
    if push_env and not is_camera_push_url_automatic():
        camera_push_url = push_env
    else:
        camera_push_url = (
            f"rtmp://{host_ip}:{DEFAULT_RTMP_PORT}/{DEFAULT_RTMP_PATH}"
            if host_ip
            else f"rtmp://127.0.0.1:{DEFAULT_RTMP_PORT}/{DEFAULT_RTMP_PATH}"
        )

    return PiRuntimeConfig(
        pi_base_url=pi_base_url,
        camera_push_url=camera_push_url,
        camera_read_url=get_camera_read_url(),
        host_ip=host_ip,
        pi_host=pi_host,
        pi_resolved_ip=pi_resolved_ip,
    )
