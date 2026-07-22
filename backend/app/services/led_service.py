# backend/app/services/led_service.py
#
# LED 光圈控制 - 后端代理服务
#
# 功能：
#   - 前端只调 /api/led/xxx，不会直接连树莓派
#   - 本服务把请求代理到树莓派：
#       /api/led/ping        -> GET  /ping
#       /api/led/start       -> POST /led/start
#       /api/led/off         -> POST /led/off
#       /api/led/color       -> POST /led/color   body: {r, g, b}
#       /api/led/brightness  -> POST /led/brightness body: {brightness}
#
# 配置：
#   优先级：FISH_PI_HOST/FISH_PI_PORT > FISH_PI_BASE_URL > 默认 raspberrypi.local:9000
#
# 注意：
#   - 不让 requests 异常堆栈直接暴露给前端
#   - 不打印树莓派服务的 raw 响应超过 500 字符
#   - 所有错误统一抛 LedServiceError，由 router 转 502
#   - 通过统一 Session（禁用系统代理）访问树莓派
#   - 通过 pi_endpoint_resolver 动态解析 raspberrypi.local 当前 IP

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

from app.services.camera_yzt.pi_runtime_config import (
    DEFAULT_PI_BASE_URL,
    get_pi_base_url,
)
from app.services.pi_http_client import make_pi_session
from app.services.pi_endpoint_resolver import (
    PiResolutionError,
    get_pi_resolved_base_url,
    invalidate_pi_resolution_cache,
)

logger = logging.getLogger("led_service")


# 兼容老代码：模块顶层 ``DEFAULT_PI_BASE_URL`` 仍可被外部 import。
DEFAULT_PI_BASE_URL = DEFAULT_PI_BASE_URL


# 独立 Session：禁用环境代理，避免 Clash / VPN 把 raspberrypi.local 错误地代理走。
PI_HTTP_SESSION = make_pi_session()


class LedServiceError(RuntimeError):
    """LED 服务调用异常（连接失败 / 4xx / 5xx）。"""


__all__ = [
    "DEFAULT_PI_BASE_URL",
    "get_pi_base_url",
    "LedServiceError",
    "ping",
    "start",
    "off",
    "set_color",
    "set_brightness",
]


def _build_url(path: str, *, force_refresh: bool = False) -> str:
    """通过统一解析器获得当前实际 IP，构造请求 URL。

    优先用 mDNS 解析出来的 IP 构造请求 URL；如果解析失败，
    仍然尝试用 ``raspberrypi.local`` 主机名直连（HTTP 层会做真实握手）。
    """
    if not path.startswith("/"):
        path = "/" + path
    try:
        base = get_pi_resolved_base_url(force_refresh=force_refresh)
        return f"{base}{path}"
    except PiResolutionError:
        # 解析失败：回退到逻辑地址（HTTP 仍可由系统/requests 做 DNS 解析）
        return f"{get_pi_base_url()}{path}"


def _request(
    method: str,
    path: str,
    json_data: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """统一代理请求：捕获异常 + 截断错误文本 + 规范化返回。

    失败一次后清除解析缓存并重试一次。第二次仍失败立即返回错误。
    """
    last_error: Optional[Exception] = None
    for attempt in (1, 2):
        url = _build_url(path, force_refresh=(attempt > 1))

        started = time.monotonic()
        try:
            resp = PI_HTTP_SESSION.request(
                method, url, json=json_data, timeout=timeout
            )
        except requests.RequestException as exc:
            last_error = exc
            invalidate_pi_resolution_cache()
            logger.info(
                "[led_service] %s %s 失败: %s (%.2fs, 第 %d 次)",
                method, url, exc.__class__.__name__,
                time.monotonic() - started, attempt,
            )
            continue

        text = (resp.text or "")[:500]
        if resp.status_code >= 400:
            last_error = LedServiceError(
                f"树莓派服务返回错误 {resp.status_code}: {text}"
            )
            logger.info(
                "[led_service] %s %s HTTP %s (%.2fs)",
                method, url, resp.status_code,
                time.monotonic() - started,
            )
            return {
                "ok": False,
                "status_code": resp.status_code,
                "error": text,
            }

        try:
            data = resp.json()
        except ValueError:
            return {"ok": True, "text": text}

        if isinstance(data, dict):
            return data
        return {"ok": True, "data": data}

    if isinstance(last_error, LedServiceError):
        raise last_error
    raise LedServiceError(
        f"无法连接树莓派服务: {last_error.__class__.__name__ if last_error else 'unknown'}"
    )


def ping() -> Dict[str, Any]:
    """GET /ping - 检查树莓派服务是否在线。"""
    return _request("GET", "/ping")


def start() -> Dict[str, Any]:
    """POST /led/start - 开启光圈（默认亮度 32，白光）。"""
    return _request("POST", "/led/start")


def off() -> Dict[str, Any]:
    """POST /led/off - 关闭光圈。"""
    return _request("POST", "/led/off")


def set_color(r: int, g: int, b: int) -> Dict[str, Any]:
    """POST /led/color - 设置 RGB 颜色（每个通道 0~255）。"""
    return _request(
        "POST",
        "/led/color",
        {"r": int(r), "g": int(g), "b": int(b)},
    )


def set_brightness(brightness: int) -> Dict[str, Any]:
    """POST /led/brightness - 设置亮度（0~255）。"""
    return _request(
        "POST",
        "/led/brightness",
        {"brightness": int(brightness)},
    )