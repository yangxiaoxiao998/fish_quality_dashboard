from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.services.camera_yzt.camera_yzt_service import camera_service_yzt
from app.services.path_service import to_abs_data_path, to_static_url
from app.repositories.db import SessionLocal
from app.repositories import session_repo, fish_repo
from app.paths import DATA_DIR
from app.utils.time_utils import to_beijing

router = APIRouter()


class StartStreamRequest(BaseModel):
    stream_url: Optional[str] = None
    width: Optional[int] = 1920
    height: Optional[int] = 1080
    fps: Optional[int] = 25
    force_restart: Optional[bool] = False
    # 定时录制（可选）：1~1440 分钟；不传则按旧行为（无限时）。
    duration_minutes: Optional[int] = None


class StartStreamResponse(BaseModel):
    status: str
    video_id: Optional[str] = None
    stream_url: Optional[str] = None
    camera_push_url: Optional[str] = None
    camera_read_url: Optional[str] = None
    video_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    message: Optional[str] = None
    reused_existing_stream: Optional[bool] = None
    force_restarted: Optional[bool] = None
    pi_status: Optional[dict] = None
    raspberry_pi: Optional[dict] = None
    desync: Optional[bool] = None
    desync_reason: Optional[str] = None
    # 定时录制字段（可选；不设置时为 None）
    record_duration_seconds: Optional[int] = None
    scheduled_stop_at: Optional[str] = None
    remaining_seconds: Optional[int] = None
    timed_recording: Optional[bool] = None
    auto_stop_triggered: Optional[bool] = None
    # preview/start 专用：True = preview-only（不建 video_task、不写视频、不入队算法）
    preview_mode: Optional[bool] = None


class StreamStatusResponse(BaseModel):
    state: str
    is_active: bool
    # 当前任务：仅 running/starting/stopping 时有值；idle 时为 None
    current_video_id: Optional[str] = None
    video_id: Optional[str] = None
    # 历史：上次跑过的任务，idle 时仍可查
    last_video_id: Optional[str] = None
    stream_url: Optional[str] = None
    default_stream_url: Optional[str] = None
    camera_push_url: Optional[str] = None
    camera_read_url: Optional[str] = None
    pi_base_url: Optional[str] = None
    pi_resolved_ip: Optional[str] = None
    host_ip: Optional[str] = None
    stream_ready: Optional[bool] = None
    startup_phase: Optional[str] = None
    startup_error: Optional[str] = None
    event_history: Optional[list] = None
    frames_captured: int
    frames_written: int
    fish_detected: int
    last_fish_detected: int = 0
    fps: Optional[float]
    elapsed_seconds: Optional[float]
    last_error: Optional[str]
    last_error_history: Optional[str] = None
    backend: Optional[dict] = None
    raspberry_pi: Optional[dict] = None
    desync: Optional[bool] = None
    desync_reason: Optional[str] = None
    # 定时录制字段（可选；不设置时为 None）
    record_duration_seconds: Optional[int] = None
    scheduled_stop_at: Optional[str] = None
    remaining_seconds: Optional[int] = None
    timed_recording: Optional[bool] = None
    auto_stop_triggered: Optional[bool] = None


class StopStreamResponse(BaseModel):
    status: str
    video_id: Optional[str] = None
    video_path: Optional[str] = None
    original_video_path: Optional[str] = None
    frames_captured: int
    frames_written: int
    fish_detected: int
    raspberry_stop_status: Optional[int | str] = None
    raspberry_stop_error: Optional[str] = None
    transcoded_status: Optional[str] = None
    # 本阶段新增：把后端入队结果透出给前端
    queued: bool = False
    job_id: Optional[str] = None
    queue_error: Optional[str] = None
    queue_message: Optional[str] = None
    db_update_error: Optional[str] = None
    note: Optional[str] = None
    pi_stop_called: Optional[bool] = None
    pi_status_after: Optional[dict] = None


class CameraHistoryItem(BaseModel):
    video_id: str
    stream_url: Optional[str] = None
    status: str
    total_fish: int
    processed_fish: int
    created_at: str
    finished_at: Optional[str] = None
    source_path: Optional[str] = None


@router.post("/camera_yzt/start", response_model=StartStreamResponse)
async def start_stream(request: StartStreamRequest):
    config = {}
    if request.stream_url:
        config["stream_url"] = request.stream_url
    if request.width is not None:
        config["width"] = request.width
    if request.height is not None:
        config["height"] = request.height
    if request.fps is not None:
        config["fps"] = request.fps
    if request.duration_minutes is not None:
        # 透传给 service 层做校验 / 持久化
        config["duration_minutes"] = request.duration_minutes
    try:
        if request.force_restart:
            result = await run_in_threadpool(
                camera_service_yzt.force_restart_stream, config
            )
            # force_restart_stream 已包含 start_result；补齐顶层字段
            start_inner = result.get("start_result") or {}
            payload = {
                "status": result.get("status", "force_restarted"),
                "video_id": result.get("video_id") or start_inner.get("video_id"),
                "stream_url": result.get("stream_url") or start_inner.get("stream_url"),
                "camera_push_url": start_inner.get("camera_push_url"),
                "camera_read_url": start_inner.get("camera_read_url"),
                "video_path": start_inner.get("video_path"),
                "width": start_inner.get("width"),
                "height": start_inner.get("height"),
                "fps": start_inner.get("fps"),
                "message": "force_restarted",
                "force_restarted": True,
                "record_duration_seconds": start_inner.get("record_duration_seconds"),
                "scheduled_stop_at": start_inner.get("scheduled_stop_at"),
                "remaining_seconds": start_inner.get("remaining_seconds"),
                "timed_recording": start_inner.get("timed_recording"),
                "auto_stop_triggered": start_inner.get("auto_stop_triggered"),
            }
            return StartStreamResponse(**payload)
        result = await run_in_threadpool(camera_service_yzt.start_stream, config)
        # 主路径补齐新字段（前端可读 desync / pi_status）
        try:
            pi_status = await run_in_threadpool(
                camera_service_yzt._request_pi_status, 3.0
            )
        except Exception:
            pi_status = None
        payload = dict(result)
        payload.setdefault("force_restarted", False)
        payload["pi_status"] = pi_status
        payload["raspberry_pi"] = pi_status
        return StartStreamResponse(**payload)
    except ValueError as e:
        # duration_minutes 校验失败：返回标准错误响应
        return StartStreamResponse(status="error", message=str(e))
    except Exception as e:
        return StartStreamResponse(status="error", message=str(e))


@router.post("/camera_yzt/force-restart")
async def force_restart_stream(request: StartStreamRequest = StartStreamRequest()):
    config = {}
    if request.stream_url:
        config["stream_url"] = request.stream_url
    if request.width is not None:
        config["width"] = request.width
    if request.height is not None:
        config["height"] = request.height
    if request.fps is not None:
        config["fps"] = request.fps
    if request.duration_minutes is not None:
        config["duration_minutes"] = request.duration_minutes
    try:
        result = await run_in_threadpool(
            camera_service_yzt.force_restart_stream, config
        )
        return result
    except ValueError as e:
        return JSONResponse(
            content={"ok": False, "status": "error", "error": str(e)},
            status_code=400,
        )


@router.post("/camera_yzt/stop", response_model=StopStreamResponse)
async def stop_stream(request: Optional[dict] = None):
    """停止摄像头。

    可选请求体：
        {"reason": "dashboard_camera_button", "source": "Dashboard.vue"}
    用于记录 stop 来源，方便排查页面切换是否误触发了 stop。
    """
    reason = ""
    source = ""
    if isinstance(request, dict):
        reason = str(request.get("reason") or "").strip()
        source = str(request.get("source") or "").strip()
    result = await run_in_threadpool(
        camera_service_yzt.stop_stream,
        reason=reason or "api",
        source=source,
    )
    return StopStreamResponse(**result)


class PreviewStartRequest(BaseModel):
    """摄像头实时预览（仅用于 Dashboard 光圈设置弹窗的实时画面）。

    与 /camera_yzt/start 的区别：
      - 不创建 video_task（不会进入视频历史记录）
      - 不写视频文件
      - 不入队算法
      - 不支持定时录制（duration_minutes 字段不允许传）
    """
    stream_url: Optional[str] = None
    width: Optional[int] = 1920
    height: Optional[int] = 1080
    fps: Optional[int] = 25


@router.post("/camera_yzt/preview/start", response_model=StartStreamResponse)
async def start_preview_stream(request: PreviewStartRequest):
    """仅用于 Dashboard 光圈设置弹窗的实时预览。

    内部以 calibration_only=True / write_video=False / create_video_task=False
    调用 camera_service_yzt.start_stream，确保：
      - 不创建 video_task
      - 不写视频文件
      - 不入队算法
      - 不影响主录制（如果主录制在跑会拒绝；前端需先停止主录制）
    """
    config = {}
    if request.stream_url:
        config["stream_url"] = request.stream_url
    if request.width is not None:
        config["width"] = request.width
    if request.height is not None:
        config["height"] = request.height
    if request.fps is not None:
        config["fps"] = request.fps
    try:
        result = await run_in_threadpool(
            camera_service_yzt.start_stream,
            config,
            calibration_only=True,
            write_video=False,
            create_video_task=False,
        )
        payload = dict(result)
        payload["preview_mode"] = True
        payload.setdefault("force_restarted", False)
        return StartStreamResponse(**payload)
    except Exception as e:
        return StartStreamResponse(status="error", message=str(e))


@router.post("/camera_yzt/preview/stop", response_model=StopStreamResponse)
async def stop_preview_stream():
    """停止 Dashboard 光圈设置弹窗启动的预览。

    内部以 queue_algorithm=False / update_video_task=False / remove_video_task=True
    调用 camera_service_yzt.stop_stream，确保：
      - 不入队算法
      - 不更新任何主录制的 video_task 状态
      - preview/start 没建 video_task，所以这里 remove_video_task=True 也不会误删主录制

    配套 reason/source 用于日志区分：
      - reason="aperture_preview_closed"
      - source="DashboardAperturePreview"
    """
    result = await run_in_threadpool(
        camera_service_yzt.stop_stream,
        reason="aperture_preview_closed",
        source="DashboardAperturePreview",
        queue_algorithm=False,
        update_video_task=False,
        remove_video_task=True,
    )
    return StopStreamResponse(**result)


@router.get("/camera_yzt/status", response_model=StreamStatusResponse)
async def get_stream_status():
    # get_status 内部会同步访问树莓派，必须放到线程池避免阻塞事件循环
    result = await run_in_threadpool(camera_service_yzt.get_status)
    return StreamStatusResponse(**result)


@router.post("/camera_yzt/reset")
async def reset_stream():
    """
    强制重置摄像头运行时状态。

    用于清理：旧 video_id 残留 / 捕获线程僵死 / 错误历史信息 / 旧鱼数。
    """
    result = await run_in_threadpool(camera_service_yzt.reset)
    return result


@router.get("/camera_yzt/frame")
async def get_latest_frame():
    frame_bytes = camera_service_yzt.get_latest_frame()
    if frame_bytes is None:
        # 204 让前端区分"摄像头还没读到第一帧"与"摄像头真的坏了"
        return Response(status_code=204)
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get("/camera_yzt/history")
async def get_camera_history(limit: int = 50):
    db = SessionLocal()
    try:
        from app.repositories.models import VideoTask
        from sqlalchemy import select, desc

        stmt = (
            select(VideoTask)
            .where(VideoTask.source_type == "rtsp")
            .order_by(desc(VideoTask.created_at))
            .limit(limit)
        )
        results = db.execute(stmt).scalars().all()

        items = []
        for r in results:
            created_at_bj = to_beijing(r.created_at)
            finished_at_bj = to_beijing(r.finished_at)
            items.append(
                CameraHistoryItem(
                    video_id=r.video_id,
                    stream_url=r.rtsp_url,
                    status=r.status,
                    total_fish=r.total_fish or 0,
                    processed_fish=r.processed_fish or 0,
                    created_at=created_at_bj.strftime("%Y-%m-%d %H:%M") if created_at_bj else "",
                    finished_at=finished_at_bj.strftime("%Y-%m-%d %H:%M") if finished_at_bj else None,
                    source_path=r.source_path,
                )
            )
        return {"items": items, "count": len(items)}
    finally:
        db.close()


@router.get("/camera_yzt/video/{video_id}")
async def get_camera_video(video_id: str):
    db = SessionLocal()
    try:
        task = session_repo.get_video_task(db, video_id=video_id)
        if not task:
            return JSONResponse(content={"error": "Video not found"}, status_code=404)

        fishes = fish_repo.list_fish_by_video(db, video_id=video_id, limit=1000)

        playback_url = to_static_url(task.source_path)
        abs_video_path = to_abs_data_path(task.source_path)
        video_exists = abs_video_path.exists() if abs_video_path else False

        created_at_bj = to_beijing(task.created_at)
        finished_at_bj = to_beijing(task.finished_at)

        return {
            "video_id": video_id,
            "status": task.status,
            "stream_url": task.rtsp_url,
            "total_fish": task.total_fish or 0,
            "processed_fish": task.processed_fish or 0,
            "created_at": created_at_bj.isoformat() if created_at_bj else None,
            "finished_at": finished_at_bj.isoformat() if finished_at_bj else None,
            "source_path": task.source_path,
            "playback_url": playback_url,
            "video_exists": video_exists,
            "playable": task.status == "done" and video_exists,
            "play_url": f"/api/camera_yzt/video/{video_id}/play" if task.status == "done" and video_exists else None,
            "fishes": [
                {
                    "fish_id": f.fish_id,
                    "body_length": f.body_length,
                    "body_height": f.body_height,
                    "body_weight": f.body_weight,
                    "condition_factor": f.condition_factor,
                    "surface_damage": f.surface_damage,
                    "status": f.status,
                    "corrected_image_url": to_static_url(f.corrected_image_path),
                }
                for f in fishes
            ],
        }
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    finally:
        db.close()


@router.get("/camera_yzt/video/{video_id}/play")
async def play_camera_video(video_id: str):
    db = SessionLocal()
    try:
        task = session_repo.get_video_task(db, video_id=video_id)
        if not task or not task.source_path:
            return JSONResponse(content={"error": "Video not found"}, status_code=404)

        if task.status != "done":
            return JSONResponse(content={"error": "视频仍在录制中，请停止后播放"}, status_code=400)

        abs_video_path = to_abs_data_path(task.source_path)
        if not abs_video_path or not os.path.exists(abs_video_path):
            return JSONResponse(content={"error": "Video file not found"}, status_code=404)

        return FileResponse(str(abs_video_path), media_type="video/mp4", filename=f"{video_id}.mp4")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    finally:
        db.close()
