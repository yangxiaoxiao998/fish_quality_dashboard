"""树莓派 mDNS 地址解析模块。

职责：
    * 动态解析 ``raspberrypi.local`` 的当前 IPv4 地址；
    * 限制单次解析耗时，不让系统 DNS 解析无限等待；
    * 短期缓存解析结果（默认 30 秒）；
    * 请求失败后使缓存失效；
    * 生成当前可请求的树莓派 URL；
    * 不读取系统 HTTP/HTTPS 代理；
    * 不使用任何固定 IP 作为回退地址。

设计原则：
    * 逻辑地址始终是 ``raspberrypi.local``，从不写死 IP；
    * 解析过程使用 ``avahi-resolve-host-name`` 优先，回退 ``getent``；
    * 单次解析固定超时，超时立即抛 ``PiResolutionError``；
    * 缓存命中时立即返回缓存，不做二次解析；
    * HTTP 请求失败时由调用方调用 ``invalidate_pi_resolution_cache()`` 清缓存。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("pi_endpoint_resolver")


DEFAULT_HOST = "raspberrypi.local"
DEFAULT_PORT = 9000
DEFAULT_SCHEME = "http"


_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


class PiResolutionError(RuntimeError):
    """无法解析树莓派 mDNS 名称或请求解析工具失败。"""


@dataclass
class _CacheEntry:
    hostname: str
    resolved_ip: str
    resolved_at: float
    expires_at: float


_CACHE: Optional[_CacheEntry] = None
_CACHE_LOCK = threading.Lock()


def _cache_get() -> Optional[_CacheEntry]:
    """线程安全地获取当前缓存。"""
    return _CACHE


def _cache_set(entry: Optional[_CacheEntry]) -> None:
    """线程安全地设置当前缓存。"""
    global _CACHE
    _CACHE = entry


def _get_env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _get_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _get_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


def get_pi_hostname() -> str:
    """返回树莓派逻辑主机名（默认 ``raspberrypi.local``）。"""
    explicit = os.environ.get("FISH_PI_HOST")
    if explicit and explicit.strip():
        return explicit.strip()
    legacy = (
        os.environ.get("FISH_PI_BASE_URL")
        or os.environ.get("FISH_CAMERA_PROXY_BASE_URL")
        or os.environ.get("FISH_PI_API_BASE")
    )
    if legacy:
        host = _extract_host(legacy)
        if host:
            return host
    return DEFAULT_HOST


def get_pi_port() -> int:
    """返回树莓派服务端口。"""
    explicit = os.environ.get("FISH_PI_PORT")
    if explicit and explicit.strip():
        try:
            return int(explicit.strip())
        except (TypeError, ValueError):
            pass
    legacy = (
        os.environ.get("FISH_PI_BASE_URL")
        or os.environ.get("FISH_CAMERA_PROXY_BASE_URL")
        or os.environ.get("FISH_PI_API_BASE")
    )
    if legacy:
        port = _extract_port(legacy)
        if port:
            return port
    return DEFAULT_PORT


def get_pi_scheme() -> str:
    """返回树莓派服务协议，默认 http。"""
    explicit = os.environ.get("FISH_PI_SCHEME")
    if explicit and explicit.strip():
        return explicit.strip().lower()
    legacy = (
        os.environ.get("FISH_PI_BASE_URL")
        or os.environ.get("FISH_CAMERA_PROXY_BASE_URL")
        or os.environ.get("FISH_PI_API_BASE")
    )
    if legacy and "://" in legacy:
        return legacy.split("://", 1)[0].lower() or DEFAULT_SCHEME
    return DEFAULT_SCHEME


def get_pi_logical_base_url() -> str:
    """返回逻辑地址，例如 ``http://raspberrypi.local:9000``。

    永远不写死 IP；只包含 ``raspberrypi.local`` 或用户自定义的 mDNS 主机名。
    """
    return f"{get_pi_scheme()}://{get_pi_hostname()}:{get_pi_port()}"


def get_pi_resolve_timeout() -> float:
    return _get_env_float("FISH_PI_RESOLVE_TIMEOUT_SECONDS", 2.0)


def get_pi_cache_seconds() -> float:
    return _get_env_float("FISH_PI_RESOLVE_CACHE_SECONDS", 30.0)


def _extract_host(url_or_host: str) -> Optional[str]:
    s = url_or_host.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    if "/" in s:
        s = s.split("/", 1)[0]
    if ":" in s:
        s = s.split(":", 1)[0]
    return s or None


def _extract_port(url_or_host: str) -> Optional[int]:
    s = url_or_host.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    if "/" in s:
        s = s.split("/", 1)[0]
    if ":" in s:
        port_str = s.split(":", 1)[1]
        try:
            return int(port_str)
        except (TypeError, ValueError):
            return None
    return None


def _validate_ipv4(ip: str) -> bool:
    if not ip or not _IPV4_RE.match(ip):
        return False
    parts = ip.split(".")
    return all(0 <= int(p) <= 255 for p in parts)


def _parse_avahi_output(text: str) -> Optional[str]:
    """``avahi-resolve-host-name -4 raspberrypi.local`` 输出形如::

        raspberrypi.local    192.168.x.x
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for token in line.split():
            if _validate_ipv4(token):
                return token
    return None


def _parse_getent_output(text: str) -> Optional[str]:
    """``getent ahostsv4 raspberrypi.local`` 输出形如::

        192.168.x.x    STREAM raspberrypi.local
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        first = line.split()[0]
        if _validate_ipv4(first):
            return first
    return None


def _resolve_via_avahi(hostname: str, timeout: float) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["avahi-resolve-host-name", "-4", hostname],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.info(
            "[pi_endpoint_resolver] avahi-resolve-host-name 超时 %.2fs", timeout
        )
        return None
    except FileNotFoundError:
        logger.info("[pi_endpoint_resolver] 系统未安装 avahi-resolve-host-name")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info("[pi_endpoint_resolver] avahi 异常: %r", exc)
        return None

    if proc.returncode != 0:
        logger.info(
            "[pi_endpoint_resolver] avahi returncode=%s stderr=%s",
            proc.returncode,
            (proc.stderr or "")[:200],
        )
        return None
    ip = _parse_avahi_output(proc.stdout or "")
    if ip:
        return ip
    return None


def _resolve_via_getent(hostname: str, timeout: float) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["getent", "ahostsv4", hostname],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.info("[pi_endpoint_resolver] getent 超时 %.2fs", timeout)
        return None
    except FileNotFoundError:
        logger.info("[pi_endpoint_resolver] 系统未安装 getent")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info("[pi_endpoint_resolver] getent 异常: %r", exc)
        return None

    if proc.returncode != 0:
        logger.info(
            "[pi_endpoint_resolver] getent returncode=%s stderr=%s",
            proc.returncode,
            (proc.stderr or "")[:200],
        )
        return None
    ip = _parse_getent_output(proc.stdout or "")
    if ip:
        return ip
    return None


def resolve_pi_ipv4(
    *,
    force_refresh: bool = False,
    hostname: Optional[str] = None,
) -> str:
    """解析 ``raspberrypi.local`` 当前 IPv4 地址。

    行为：
        * 有未过期缓存时直接返回缓存（除非 ``force_refresh=True``）；
        * 缓存过期或被失效时执行解析；
        * 优先 ``avahi-resolve-host-name -4``，回退 ``getent ahostsv4``；
        * 任一步骤超时或失败抛出 ``PiResolutionError``；
        * 不读取系统 HTTP/HTTPS 代理；
        * 永不写死任何固定 IP。
    """
    host = hostname or get_pi_hostname()
    if not host:
        raise PiResolutionError("树莓派主机名为空，无法解析")

    cache_ttl = get_pi_cache_seconds()
    now = time.monotonic()

    # 先把模块级 _CACHE 拷到 local，避免与本函数内部 _CACHE = 冲突
    if not force_refresh:
        with _CACHE_LOCK:
            cached = _cache_get()
        if (
            cached is not None
            and cached.hostname == host
            and cached.expires_at > now
        ):
            return cached.resolved_ip

    timeout = get_pi_resolve_timeout()

    ip = _resolve_via_avahi(host, timeout)
    if not ip:
        ip = _resolve_via_getent(host, timeout)

    if not ip or not _validate_ipv4(ip):
        raise PiResolutionError(
            f"无法通过 mDNS 解析 {host}。"
            "请确认主机和树莓派位于同一局域网，"
            "Avahi 服务正常运行，路由器未开启客户端隔离。"
        )

    with _CACHE_LOCK:
        _cache_set(
            _CacheEntry(
                hostname=host,
                resolved_ip=ip,
                resolved_at=time.time(),
                expires_at=now + max(cache_ttl, 1.0),
            )
        )

    logger.info("[pi_endpoint_resolver] %s -> %s (缓存 %.1fs)", host, ip, cache_ttl)
    return ip


def get_pi_resolved_base_url(
    *,
    force_refresh: bool = False,
) -> str:
    """返回 ``http(s)://<current_ipv4>:<port>``。

    用于实际 HTTP 请求；前端只看到逻辑地址，看不到 IP。
    """
    ip = resolve_pi_ipv4(force_refresh=force_refresh)
    return f"{get_pi_scheme()}://{ip}:{get_pi_port()}"


def invalidate_pi_resolution_cache() -> None:
    """清除解析缓存，使下一次解析重新执行 mDNS。"""
    with _CACHE_LOCK:
        _cache_set(None)


def get_pi_resolution_diag() -> dict:
    """返回当前解析器诊断信息（用于状态接口与日志）。"""
    now = time.monotonic()
    entry = _cache_get()
    return {
        "hostname": get_pi_hostname(),
        "logical_base_url": get_pi_logical_base_url(),
        "resolved_ip": entry.resolved_ip if entry else None,
        "resolved_at": entry.resolved_at if entry else None,
        "expires_at": entry.expires_at if entry else None,
        "cache_seconds": get_pi_cache_seconds(),
        "resolve_timeout_seconds": get_pi_resolve_timeout(),
        "now": now,
        "cache_alive": bool(entry and entry.expires_at > now),
    }