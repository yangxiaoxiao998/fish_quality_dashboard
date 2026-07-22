"""
algorithm_calibration_capture_service.py

负责：
1. 从 camera_service_yzt.get_latest_frame() 取当前摄像头帧。
2. 把帧保存到 data/uploads/calibration/YYYY/MM/DD/。
3. 调用 algorithm_cli_service.run_calibration_cli 跑真实标定。
4. 在 data/calibration/sessions/<id>/ 中找 adjusted_preview.jpg / preview 文件。
5. 返回结构化结果给 router。

另外还提供 capture_calibration_frame()：
   - 不依赖 camera_service_yzt（不创建 video_task、不写视频历史）
   - 直接从 fish_camera RTMP 流抓一帧，保存到 data/uploads/calibration/YYYY/MM/DD/
   - 仅供视觉矫正"拍摄一张标定图"使用
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.paths import DATA_DIR, PROJECT_ROOT
from app.services import algorithm_cli_service
from app.services.path_service import to_data_relative, to_static_url

logger = logging.getLogger("algorithm_calibration_capture")


CALIBRATION_UPLOAD_DIR = DATA_DIR / "uploads" / "calibration"
CALIBRATION_SESSIONS_DIR = DATA_DIR / "calibration" / "sessions"
CALIBRATION_CURRENT_JSON = DATA_DIR / "calibration" / "current_calibration.json"

PREVIEW_FILENAMES = (
    "adjusted_preview.jpg",
    "rectified_preview.jpg",
    "preview.jpg",
    "adjusted_preview.png",
    "preview.png",
)


def _to_relative(path: str | Path | None) -> str | None:
    return to_data_relative(path)


def _to_url(path: str | Path | None) -> str | None:
    return to_static_url(path)


def save_calibration_frame(frame_bytes: bytes) -> dict[str, Any]:
    """
    保存当前摄像头帧到 data/uploads/calibration/YYYY/MM/DD/。

    返回：
      {
        "abs_path": 绝对路径,
        "relative_path": "data/uploads/calibration/.../xxx.jpg",
        "static_url": "/static/uploads/calibration/.../xxx.jpg",
        "filename": "calibration_xxx.jpg",
        "sub_dir": "YYYY/MM/DD",
      }
    """
    if not frame_bytes:
        raise ValueError("frame_bytes 为空")

    now = datetime.now()
    sub_dir_name = now.strftime("%Y/%m/%d")
    target_dir = CALIBRATION_UPLOAD_DIR / sub_dir_name
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = now.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    short_uuid = uuid.uuid4().hex[:8]
    filename = f"calibration_{stamp}_{short_uuid}.jpg"
    target_path = target_dir / filename

    with open(target_path, "wb") as f:
        f.write(frame_bytes)

    abs_path = target_path.resolve()
    relative_path = _to_relative(abs_path)
    static_url = _to_url(abs_path)

    return {
        "abs_path": str(abs_path),
        "relative_path": relative_path,
        "static_url": static_url,
        "filename": filename,
        "sub_dir": sub_dir_name,
    }


def find_latest_session_dir() -> Optional[Path]:
    """扫描 data/calibration/sessions/，返回最新修改时间的 session 目录。"""
    if not CALIBRATION_SESSIONS_DIR.exists():
        return None

    candidates: list[Path] = []
    for child in CALIBRATION_SESSIONS_DIR.iterdir():
        if not child.is_dir():
            continue
        candidates.append(child)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_adjusted_preview() -> dict[str, Any]:
    """
    在 data/calibration/sessions/<latest>/ 中寻找矫正预览图。

    返回：
      {
        "relative_path": "data/calibration/sessions/<id>/xxx.jpg",
        "static_url": "/static/calibration/sessions/<id>/xxx.jpg",
        "abs_path": "<绝对路径>",
        "session_dir": "<session 目录名>",
      }

    如果没有找到，返回空 dict。
    """
    session_dir = find_latest_session_dir()
    if session_dir is None:
        return {}

    for name in PREVIEW_FILENAMES:
        candidate = session_dir / name
        if candidate.exists() and candidate.is_file():
            abs_path = candidate.resolve()
            return {
                "abs_path": str(abs_path),
                "relative_path": _to_relative(abs_path),
                "static_url": _to_url(abs_path),
                "session_dir": session_dir.name,
                "filename": candidate.name,
            }

    # 兜底：扫描 session_dir 内所有 jpg/png，取最新修改时间
    candidates: list[Path] = []
    for child in session_dir.iterdir():
        if not child.is_file():
            continue
        if child.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        if "preview" in child.stem.lower() or "rectified" in child.stem.lower() or "adjusted" in child.stem.lower():
            candidates.append(child)
    if not candidates:
        return {}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    abs_path = latest.resolve()
    return {
        "abs_path": str(abs_path),
        "relative_path": _to_relative(abs_path),
        "static_url": _to_url(abs_path),
        "session_dir": session_dir.name,
        "filename": latest.name,
    }


def run_capture_and_calibrate(
    *,
    camera_id: str = "default",
    device: str = "cuda",
    overwrite: bool = True,
    save_only: bool = False,
    stream_url: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: Optional[int] = None,
    stop_after_capture: bool = True,
) -> dict[str, Any]:
    """
    主入口：

    1. 调用 capture_frame_via_camera_yzt(...) 复用"摄像头输入"按钮同款链路：
       - 如果 camera_service_yzt 已经在跑，直接拿 last_frame_bytes 抓拍；
       - 否则以 calibration_only=True 启动树莓派推流 + 拉帧；
       - 拍摄后 stop_stream(queue_algorithm=False, remove_video_task=True)
         自动停止摄像头，且不会入队整视频算法、不污染视频历史。
    2. 保存到 data/uploads/calibration/YYYY/MM/DD/；
    3. save_only=True 时只保存并返回 saved=True, calibrated=False；
    4. 否则调用 algorithm_cli_service.run_calibration_cli(...) 执行 adjust_calibration；
    5. 查找 adjusted_preview；
    6. 返回统一 dict。
    """
    from app.services.camera_yzt.camera_yzt_service import camera_service_yzt

    # 复用"摄像头输入"按钮同款链路（已验证可工作）：
    #   1. 如果 camera_service_yzt 已经有最新帧，直接拍；
    #   2. 否则以 calibration_only=True 启动树莓派推流，等待帧出现，保存图片；
    #   3. 拍摄后调用 stop_stream(queue_algorithm=False, remove_video_task=True)
    #      自动停止摄像头（不入队算法、不污染视频历史）。
    capture_result = capture_frame_via_camera_yzt(
        stream_url=stream_url,
        width=width or 1920,
        height=height or 1080,
        fps=fps or 25,
        timeout_seconds=25.0,
        stop_after_capture=stop_after_capture,
    )

    if not capture_result.get("ok"):
        return {
            "ok": False,
            "saved": False,
            "calibrated": False,
            "error_code": capture_result.get("error_code"),
            "error_message": capture_result.get(
                "error_message",
                "摄像头画面获取失败，请稍后重试。",
            ),
        }

    source_image_path = capture_result.get("source_image_path")
    source_image_url = capture_result.get("source_image_url")

    if save_only:
        return {
            "ok": True,
            "saved": True,
            "calibrated": False,
            "source_image_path": source_image_path,
            "source_image_url": source_image_url,
            "message": "标定帧已保存",
        }

    # save_only=False：执行 adjust_calibration CLI
    algo_result = algorithm_cli_service.run_calibration_cli(
        source_image=source_image_path,
        camera_id=camera_id,
        device=device,
        overwrite=overwrite,
    )

    preview: dict[str, Any] = find_adjusted_preview()
    calibrated = bool(algo_result.get("ok"))

    response: dict[str, Any] = {
        "ok": calibrated,
        "saved": True,
        "calibrated": calibrated,
        "message": algo_result.get("calibration_json") and "标定完成" or None,
        "source_image_path": source_image_path,
        "source_image_url": source_image_url,
        "log_path": algo_result.get("log_path"),
        "returncode": algo_result.get("returncode"),
    }

    if calibrated:
        response["calibration_json"] = algo_result.get("calibration_json")
        response["calibration_url"] = _to_url(
            DATA_DIR / "calibration" / "current_calibration.json"
        )
        if preview:
            response["adjusted_preview_path"] = preview.get("relative_path")
            response["adjusted_preview_url"] = preview.get("static_url")
            response["adjusted_preview_session"] = preview.get("session_dir")
        else:
            response["adjusted_preview_warning"] = (
                "未找到矫正预览图，请检查 data/calibration/sessions/ 或算法日志。"
            )
    else:
        response["error_message"] = (
            algo_result.get("error_message") or "算法标定失败"
        )
        if preview:
            response["adjusted_preview_path"] = preview.get("relative_path")
            response["adjusted_preview_url"] = preview.get("static_url")

    return response


def capture_frame_via_camera_yzt(
    *,
    stream_url: Optional[str] = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 25,
    timeout_seconds: float = 25.0,
    stop_after_capture: bool = True,
) -> dict[str, Any]:
    """
    复用 camera_service_yzt 启停链路完成标定抓拍。

    流程：
      1. 如果 camera_service_yzt 已经在跑（state in STARTING/RUNNING），
         直接读 last_frame_bytes 抓拍，**不**额外启动。
      2. 否则以 calibration_only=True 启动推流：
            - 不创建 video_task
            - 不写视频文件
            - 不入队算法
      3. 循环等待 last_frame_bytes 出现，超时返回。
      4. 拿到帧后立即保存到 data/uploads/calibration/YYYY/MM/DD/。
      5. 如果 stop_after_capture=True 且本次启动是我们自己触发的，
         调用 camera_service_yzt.stop_stream(queue_algorithm=False,
                                                remove_video_task=True)
         自动停止摄像头（不污染视频历史）。

    返回：
        { "ok": True, "saved": True, "source_image_path", "source_image_url" }
      或 { "ok": False, "error_code", "error_message" }
    """
    from app.services.camera_yzt.camera_yzt_service import (
        camera_service_yzt,
        CameraRuntimeState,
    )

    started_by_calibration = False

    # 1. 检查是否已经在运行
    already_running = camera_service_yzt.state in (
        CameraRuntimeState.STARTING,
        CameraRuntimeState.RUNNING,
    )

    # 2. 如果没有运行，启动推流（calibration_only 模式）
    if not already_running:
        stream_cfg: dict[str, Any] = {}
        if stream_url:
            stream_cfg["stream_url"] = stream_url
        else:
            stream_cfg["stream_url"] = camera_service_yzt.stream_url or camera_service_yzt.DEFAULT_STREAM_URL
        stream_cfg["width"] = int(width) if width else 1920
        stream_cfg["height"] = int(height) if height else 1080
        stream_cfg["fps"] = int(fps) if fps else 25

        start_resp = camera_service_yzt.start_stream(
            stream_cfg,
            calibration_only=True,
            write_video=False,
            create_video_task=False,
        )
        # 防回归提示：标定抓拍必须以 calibration_only=True 启动 camera_service_yzt，
        # 否则会创建 video_task 并污染 /api/videos → VideoHistory。
        # 这里每次启动都打印一行，便于排查历史记录里出现的 calibration 任务。
        logger.info(
            "[calibration_capture] start_stream calibration_only=True write_video=False create_video_task=False "
            "(不会写入 VideoTask，不会进入视频历史)"
        )
        status = start_resp.get("status") if isinstance(start_resp, dict) else None
        if status not in ("starting", "running"):
            logger.error(
                "[calibration_capture] camera start_stream returned: %s",
                start_resp,
            )
            return {
                "ok": False,
                "error_code": "CAMERA_DEVICE_START_FAILED",
                "error_message": "摄像头连接失败，请检查设备电源和网络连接。",
            }
        started_by_calibration = True

    # 3. 等待 last_frame_bytes 出现
    try:
        deadline = time.time() + max(2.0, timeout_seconds)
        frame_bytes: Optional[bytes] = None
        while time.time() < deadline:
            frame_bytes = camera_service_yzt.get_latest_frame()
            if frame_bytes:
                break
            time.sleep(0.3)

        if not frame_bytes:
            # 超时 → 关闭（如果是自己启动的）
            if started_by_calibration:
                try:
                    camera_service_yzt.stop_stream(
                        queue_algorithm=False,
                        update_video_task=False,
                        remove_video_task=True,
                        reason="calibration_capture_timeout",
                        join_timeout=3,
                        raspberry_stop_timeout=3,
                    )
                except Exception as e:
                    logger.warning("[calibration_capture] stop_stream on timeout failed: %s", e)
            return {
                "ok": False,
                "error_code": "CAMERA_STREAM_TIMEOUT",
                "error_message": "摄像头画面获取超时，请稍后重试。",
            }

        # 4. 保存标定帧
        try:
            saved = save_calibration_frame(frame_bytes)
        except Exception as exc:
            logger.exception("save calibration frame failed")
            if started_by_calibration:
                try:
                    camera_service_yzt.stop_stream(
                        queue_algorithm=False,
                        update_video_task=False,
                        remove_video_task=True,
                        reason="calibration_capture_save_failed",
                        join_timeout=3,
                        raspberry_stop_timeout=3,
                    )
                except Exception:
                    pass
            return {
                "ok": False,
                "error_code": "CALIBRATION_SAVE_FAILED",
                "error_message": "标定帧保存失败，请稍后重试。",
            }

        rel_path = saved["relative_path"]
        static_url = saved["static_url"]
        abs_path = saved["abs_path"]

    finally:
        # 5. 拍摄后自动停止（仅当本次是我们自己启动的）
        if stop_after_capture and started_by_calibration:
            try:
                # 防回归提示：标定抓拍停止时必须 queue_algorithm=False / update_video_task=False，
                # 让 start_stream 未建 video_task 的会话保持干净。
                logger.info(
                    "[calibration_capture] stop_stream queue_algorithm=False "
                    "update_video_task=False remove_video_task=True"
                )
                camera_service_yzt.stop_stream(
                    queue_algorithm=False,
                    update_video_task=False,
                    remove_video_task=True,
                    reason="calibration_capture_done",
                    join_timeout=3,
                    raspberry_stop_timeout=3,
                )
            except Exception as e:
                logger.warning(
                    "[calibration_capture] stop_stream after capture failed: %s", e
                )

    return {
        "ok": True,
        "saved": True,
        "source_image_path": rel_path,
        "source_image_url": static_url,
        "abs_path": abs_path,
    }


# ---------------------------------------------------------------------------
# 独立抓帧接口：只抓一张标定图，不创建 video_task / 不写视频历史。
# ---------------------------------------------------------------------------

# 默认推流地址，参考 CameraServiceYzt.DEFAULT_STREAM_URL
DEFAULT_CALIBRATION_STREAM_URL = os.getenv(
    "CALIBRATION_STREAM_URL",
    "rtmp://127.0.0.1:1935/fish_camera",
)


def check_fish_camera_stream(stream_url: Optional[str] = None, timeout: float = 4.0) -> dict:
    """
    探测 fish_camera 推流是否可用（ffprobe）。

    返回：
      {"ok": True, "stream_url": "..."} 或
      {"ok": False, "stream_url": "...", "error_message": "..."}
    """
    url = (stream_url or DEFAULT_CALIBRATION_STREAM_URL).strip() or DEFAULT_CALIBRATION_STREAM_URL
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return {
            "ok": False,
            "stream_url": url,
            "error_message": "服务器未安装 ffprobe，无法探测摄像头流是否可用。",
        }
    try:
        proc = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                "-timeout", str(int(timeout * 1_000_000)),
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 2.0,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stream_url": url,
            "error_message": f"探测摄像头流超时（{timeout}s）：{url}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "stream_url": url,
            "error_message": f"探测摄像头流失败：{type(exc).__name__}: {exc}",
        }

    if proc.returncode == 0:
        return {"ok": True, "stream_url": url}

    err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return {
        "ok": False,
        "stream_url": url,
        "error_message": (
            f"未检测到摄像头推流：{url}"
            + (f"（ffprobe: {err[:240]}）" if err else "")
        ),
    }


def capture_calibration_frame(
    stream_url: Optional[str] = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """
    独立抓帧：从 fish_camera 拉一帧 JPG，保存到 data/uploads/calibration/YYYY/MM/DD/。

    行为：
      - 复用 camera_service_yzt 链路（同 run_capture_and_calibrate）
      - 不创建 video_task
      - 不写视频历史
      - 不影响数据大屏的录制状态（如果原来未启动）
      - 探测到无流时，自动通知树莓派启动推流；不再要求用户手工启动
      - 错误信息以用户友好方式返回，不暴露 RTMP / IP / MediaMTX 等技术细节
    """
    return capture_frame_via_camera_yzt(
        stream_url=stream_url,
        width=1920,
        height=1080,
        fps=25,
        timeout_seconds=max(8.0, timeout * 3),
        stop_after_capture=True,
    )
