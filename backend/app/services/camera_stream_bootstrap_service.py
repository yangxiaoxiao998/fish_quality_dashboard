"""
camera_stream_bootstrap_service.py

负责"摄像头视角标定"等不需要用户手动开启摄像头推流的场景。

行为：
    1. 先用 ffprobe 探测本机读取地址（FISH_CAMERA_READ_URL）是否已经有流；
    2. 如果已经有流，直接返回 ok，让调用方继续抓帧；
    3. 如果没有流，并且 FISH_PI_API_BASE + FISH_CAMERA_PUSH_URL 都配置好了，
       自动调用树莓派 /camera/start 接口通知设备推流；
    4. 循环探测流是否就绪，直到超时；
    5. 全部失败时返回用户友好提示（不要把 RTMP / IP 等技术信息暴露给前端）。

本服务是 **后端内部服务**，不直接挂 router：
    - 由算法标定抓拍 (algorithm_calibration_capture_service) 调用；
    - 由 camera_yzt_router 按需复用。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

from app.services.camera_yzt.pi_runtime_config import (
    get_camera_push_url as _runtime_get_camera_push_url,
    get_pi_base_url as _runtime_get_pi_base_url,
)

logger = logging.getLogger("camera_stream_bootstrap")

DEFAULT_READ_URL = "rtmp://127.0.0.1:1935/fish_camera"
PROBE_TIMEOUT_SECONDS = 4.0
START_HTTP_TIMEOUT_SECONDS = 8.0
STARTUP_WAIT_LOOP_INTERVAL_SECONDS = 0.8


def get_pi_api_base() -> str:
    """树莓派 / 摄像头代理服务地址（用于主动通知推流）。

    优先使用统一配置中的 ``FISH_PI_BASE_URL``（默认 ``raspberrypi.local``），
    兼容旧变量 ``FISH_CAMERA_PROXY_BASE_URL`` / ``FISH_PI_API_BASE``。
    """
    legacy = (
        os.getenv("FISH_CAMERA_PROXY_BASE_URL")
        or os.getenv("FISH_PI_API_BASE")
    )
    if legacy and legacy.strip():
        return legacy.strip().rstrip("/")
    return _runtime_get_pi_base_url()


def get_pi_push_url() -> str:
    """传给树莓派的推流目标地址。

    必须是主机局域网 IP，不能是 127.0.0.1。
    优先使用统一配置中的 ``FISH_CAMERA_PUSH_URL``；未配置时由
    :mod:`app.services.camera_yzt.pi_runtime_config` 自动推断主机 IP。
    """
    return _runtime_get_camera_push_url()


def get_camera_read_url() -> str:
    """服务器本机读取 MediaMTX 的地址。"""
    return (
        os.getenv("FISH_CAMERA_READ_URL")
        or os.getenv("CALIBRATION_STREAM_URL")
        or DEFAULT_READ_URL
    ).strip() or DEFAULT_READ_URL


def get_camera_size() -> dict[str, int]:
    """传给树莓派的 width/height/fps，默认 1920x1080 / 25fps。"""
    def _int(name: str, default: int) -> int:
        v = os.getenv(name)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    return {
        "width": _int("FISH_CAMERA_WIDTH", 1920),
        "height": _int("FISH_CAMERA_HEIGHT", 1080),
        "fps": _int("FISH_CAMERA_FPS", 25),
    }


def _probe_stream(read_url: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """
    用 ffprobe 探测流是否可用。

    返回 True 表示当前已经有可读的视频流；False 表示流还没起来或不可达。

    备注：
        - ffprobe 返回 0 表示成功解码到 stream 信息；非 0 表示失败；
        - 用户友好：失败信息只写日志，调用方拿到的是 bool。
    """
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        logger.warning("ffprobe not installed, cannot probe stream")
        return False
    try:
        proc = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_type",
                "-of", "json",
                read_url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timeout probing %s", read_url)
        return False
    except Exception as exc:
        logger.warning("ffprobe exception probing %s: %s", read_url, exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "ffprobe no stream at %s (rc=%s, err=%s)",
            read_url,
            proc.returncode,
            (proc.stderr or "").strip()[:200],
        )
        return False
    return "video" in (proc.stdout or "")


def _start_pi_camera(pi_base: str, push_url: str) -> dict[str, Any]:
    """
    通知树莓派 /camera/start 启动推流。

    返回 {"ok": True, "data": ...} 或
          {"ok": False, "debug_message": "..."}.
    """
    from app.services.pi_http_client import pi_post  # local import

    if not pi_base:
        return {"ok": False, "debug_message": "FISH_PI_API_BASE empty"}
    if not push_url:
        return {"ok": False, "debug_message": "FISH_CAMERA_PUSH_URL empty"}

    url = f"{pi_base.rstrip('/')}/camera/start"
    payload = {
        "stream_url": push_url,
        **get_camera_size(),
    }
    try:
        resp = pi_post(url, json=payload, timeout=START_HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return {
            "ok": False,
            "debug_message": f"Pi camera start connect failed: {exc.__class__.__name__}: {exc}",
        }

    text = (resp.text or "")[:500]
    if resp.status_code >= 400:
        return {
            "ok": False,
            "debug_message": f"Pi camera start HTTP {resp.status_code}: {text}",
        }

    try:
        data = resp.json() if text else {}
    except ValueError:
        data = {}

    # 一些树莓派实现是 {"status": "error"}，统一归类为失败；
    # 已运行视为 ok=true。
    if isinstance(data, dict):
        status = str(data.get("status") or "").lower()
        if status in ("error", "failed") and data.get("ok") is False:
            return {
                "ok": False,
                "debug_message": f"Pi camera start returned error status: {data}",
            }
        # 已运行不算错
        if "already" in str(data.get("message") or "").lower():
            return {"ok": True, "data": data, "already_running": True}

    return {"ok": True, "data": data}


def ensure_fish_camera_stream_ready(
    timeout_seconds: float = 10.0,
    *,
    force_start: bool = False,
) -> dict[str, Any]:
    """
    主入口：确保 MediaMTX fish_camera 推流可用。

    流程：
        1. 先 ffprobe 探测本机读取地址是否已经有流；
        2. 如果有流 → ok=True, started=False；
        3. 如果没有流，并且强制或者设备已配置 → 通知树莓派启动推流；
        4. 循环探测，直到流出现或超时；
        5. 全部失败 → 返回 ok=False，并返回 user_message（前端友好）。

    返回：
        {
          "ok": True | False,
          "started": bool,             # 是否本次主动启动了推流
          "already_running": bool,
          "read_url": str,             # 服务器读取地址（给 ffmpeg 用）
          "code": str,                 # 失败时返回机器可读 code
          "user_message": str,         # 失败时返回给前端的中文提示
          "debug_message": str,        # 后端日志使用，不返回前端
        }
    """
    read_url = get_camera_read_url()

    # 1. 已存在流：直接返回
    if _probe_stream(read_url, timeout=PROBE_TIMEOUT_SECONDS):
        return {
            "ok": True,
            "started": False,
            "already_running": True,
            "read_url": read_url,
        }

    # 2. 设备配置缺失 → 不暴露技术细节
    pi_base = get_pi_api_base()
    push_url = get_pi_push_url()
    if not pi_base or not push_url:
        logger.warning(
            "camera device not configured: FISH_PI_API_BASE / FISH_CAMERA_PUSH_URL empty"
        )
        return {
            "ok": False,
            "started": False,
            "already_running": False,
            "read_url": read_url,
            "code": "CAMERA_DEVICE_NOT_CONFIGURED",
            "user_message": "摄像头设备未配置，请联系管理员检查设备配置。",
            "debug_message": "FISH_PI_API_BASE or FISH_CAMERA_PUSH_URL not configured",
        }

    # 3. 不主动 stop 现有录制（保留 camera_yzt 的能力），只在 force_start=True 时显式触发
    if force_start:
        start_resp = _start_pi_camera(pi_base, push_url)
        if not start_resp.get("ok"):
            logger.warning(
                "Pi camera start failed: %s",
                start_resp.get("debug_message"),
            )
            return {
                "ok": False,
                "started": False,
                "already_running": False,
                "read_url": read_url,
                "code": "CAMERA_DEVICE_START_FAILED",
                "user_message": "摄像头连接失败，请检查设备电源和网络连接。",
                "debug_message": start_resp.get("debug_message"),
            }

    # 4. 循环探测流是否就绪
    deadline = time.time() + max(2.0, timeout_seconds)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        # 等待设备启动 → 服务器收到流，需要几秒；
        # 中间睡眠后探测，探测超时短一些避免拖死总时间。
        time.sleep(STARTUP_WAIT_LOOP_INTERVAL_SECONDS)
        if _probe_stream(read_url, timeout=2.0):
            return {
                "ok": True,
                "started": bool(force_start),
                "already_running": False,
                "read_url": read_url,
                "attempts": attempt,
            }

    # 5. 超时
    logger.warning(
        "fish_camera stream not ready after %.1fs (read_url=%s)",
        timeout_seconds,
        read_url,
    )
    return {
        "ok": False,
        "started": False,
        "already_running": False,
        "read_url": read_url,
        "code": "CAMERA_STREAM_TIMEOUT",
        "user_message": "摄像头画面获取超时，请稍后重试。",
        "debug_message": f"fish_camera stream not ready after {timeout_seconds}s",
    }


def bootstrap_fish_camera(timeout_seconds: float = 10.0) -> dict[str, Any]:
    """
    主动启动推流的入口（与 ensure_fish_camera_stream_ready 不同：本函数
    始终会调用树莓派 /camera/start，不依赖 _probe_stream 的"已有流"快路径）。

    主要用于相机长时间没推流时（例如设备断电后恢复）强制重连。
    """
    read_url = get_camera_read_url()
    pi_base = get_pi_api_base()
    push_url = get_pi_push_url()

    if not pi_base or not push_url:
        return {
            "ok": False,
            "code": "CAMERA_DEVICE_NOT_CONFIGURED",
            "user_message": "摄像头设备未配置，请联系管理员检查设备配置。",
            "debug_message": "FISH_PI_API_BASE or FISH_CAMERA_PUSH_URL empty",
        }

    start_resp = _start_pi_camera(pi_base, push_url)
    if not start_resp.get("ok"):
        return {
            "ok": False,
            "code": "CAMERA_DEVICE_START_FAILED",
            "user_message": "摄像头连接失败，请检查设备电源和网络连接。",
            "debug_message": start_resp.get("debug_message"),
        }

    deadline = time.time() + max(2.0, timeout_seconds)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        time.sleep(STARTUP_WAIT_LOOP_INTERVAL_SECONDS)
        if _probe_stream(read_url, timeout=2.0):
            return {
                "ok": True,
                "started": True,
                "already_running": False,
                "read_url": read_url,
                "attempts": attempt,
            }

    return {
        "ok": False,
        "started": False,
        "already_running": False,
        "read_url": read_url,
        "code": "CAMERA_STREAM_TIMEOUT",
        "user_message": "摄像头画面获取超时，请稍后重试。",
        "debug_message": f"fish_camera stream not ready after {timeout_seconds}s",
    }


__all__ = [
    "DEFAULT_READ_URL",
    "get_pi_api_base",
    "get_pi_push_url",
    "get_camera_read_url",
    "get_camera_size",
    "ensure_fish_camera_stream_ready",
    "bootstrap_fish_camera",
]