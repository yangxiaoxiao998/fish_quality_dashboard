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
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    force_restart: Optional[bool] = False
    # 定时录制（可选）：1~1440 分钟；不传则按旧行为（无限时）。
    duration_minutes: Optional[int] = None
    force_all_complete: Optional[bool] = False
    autofocus_mode: Optional[str] = None
    lens_position: Optional[float] = None
    autofocus_window: Optional[str] = None


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
    # 会话类型（用于区分 idle / recording / preview）
    session_mode: Optional[str] = None
    preview_mode: Optional[bool] = None
    write_video_enabled: Optional[bool] = None
    create_video_task: Optional[bool] = None
    calibration_only: Optional[bool] = None


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


class CameraSettingsRequest(BaseModel):
    stream_url: str = ""
    width: int = 1920
    height: int = 1080
    fps: int = 25
    force_all_complete: bool = False


@router.get("/camera_yzt/settings")
async def get_camera_settings():
    return await run_in_threadpool(camera_service_yzt.get_saved_settings)


@router.put("/camera_yzt/settings")
async def save_camera_settings(request: CameraSettingsRequest):
    if not 320 <= request.width <= 3840:
        return JSONResponse(content={"error": "宽度必须在 320~3840 之间"}, status_code=422)
    if not 240 <= request.height <= 2160:
        return JSONResponse(content={"error": "高度必须在 240~2160 之间"}, status_code=422)
    if not 1 <= request.fps <= 60:
        return JSONResponse(content={"error": "帧率必须在 1~60 之间"}, status_code=422)
    settings = request.model_dump()
    if not settings["stream_url"]:
        settings["stream_url"] = camera_service_yzt._get_camera_push_url()
    saved = await run_in_threadpool(camera_service_yzt.save_settings, settings)
    return {"ok": True, "settings": saved}


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
    config["force_all_complete"] = bool(request.force_all_complete)
    if request.autofocus_mode is not None:
        config["autofocus_mode"] = request.autofocus_mode
    if request.lens_position is not None:
        config["lens_position"] = request.lens_position
    if request.autofocus_window is not None:
        config["autofocus_window"] = request.autofocus_window
    try:
        if request.duration_minutes and request.duration_minutes > 0:
            from datetime import timedelta, timezone
            from app.repositories import camera_record_schedule_repo as sched_repo
            db = _SessionLocal()
            try:
                start_at = datetime.now(timezone.utc)
                conflicts = sched_repo.find_active_overlaps(
                    db,
                    planned_start_at=start_at,
                    planned_end_at=start_at + timedelta(minutes=request.duration_minutes),
                )
            finally:
                db.close()
            if conflicts:
                return StartStreamResponse(
                    status="conflict",
                    message="手动录制时间与已有计划录制冲突，请调整录制时长或等待计划完成。",
                )
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
    if request.autofocus_mode is not None:
        config["autofocus_mode"] = request.autofocus_mode
    if request.lens_position is not None:
        config["lens_position"] = request.lens_position
    if request.autofocus_window is not None:
        config["autofocus_window"] = request.autofocus_window
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
    autofocus_mode: Optional[str] = None
    lens_position: Optional[float] = None
    autofocus_window: Optional[str] = None


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
    if request.autofocus_mode is not None:
        config["autofocus_mode"] = request.autofocus_mode
    if request.lens_position is not None:
        config["lens_position"] = request.lens_position
    if request.autofocus_window is not None:
        config["autofocus_window"] = request.autofocus_window
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


class FocusUpdateRequest(BaseModel):
    autofocus_mode: str
    lens_position: Optional[float] = None
    autofocus_window: Optional[str] = None


class FocusSettingsRequest(FocusUpdateRequest):
    lens_position: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


def _focus_payload(request: FocusUpdateRequest) -> dict:
    payload = {"autofocus_mode": request.autofocus_mode}
    if request.lens_position is not None:
        payload["lens_position"] = request.lens_position
    if request.autofocus_window:
        payload["autofocus_window"] = request.autofocus_window
    return payload


@router.get("/camera_yzt/focus/settings")
async def get_focus_settings():
    return await run_in_threadpool(camera_service_yzt.get_focus_settings)


@router.put("/camera_yzt/focus/settings")
async def save_focus_settings(request: FocusSettingsRequest):
    if request.autofocus_mode not in {"continuous", "auto", "manual"}:
        return JSONResponse(content={"ok": False, "error": "无效的对焦模式"}, status_code=422)
    if request.lens_position is not None and not 0 <= request.lens_position <= 32:
        return JSONResponse(content={"ok": False, "error": "手动镜头位置必须在 0~32 之间"}, status_code=422)
    settings = await run_in_threadpool(camera_service_yzt.save_focus_settings, _focus_payload(request))
    return {"ok": True, "settings": settings}


@router.post("/camera_yzt/focus/photo")
async def capture_focus_photo():
    """保存当前实时帧；新的原始照片开始时清理上一次焦距工作流照片。"""
    try:
        def capture():
            photo_dir = camera_service_yzt.focus_photo_dir
            photo_dir.mkdir(parents=True, exist_ok=True)
            for old_file in photo_dir.iterdir():
                if old_file.is_file():
                    old_file.unlink()
            return camera_service_yzt.capture_focus_photo("original")
        path = await run_in_threadpool(capture)
        return {"ok": True, "photo_url": to_static_url(path)}
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=409)


@router.post("/camera_yzt/focus/confirm")
async def confirm_focus(request: FocusSettingsRequest):
    """应用当前窗口/镜头位置后重新抓拍右侧照片。"""
    try:
        payload = _focus_payload(request)
        result = await run_in_threadpool(camera_service_yzt.focus_update, **payload)
        if not result.get("ok") and result.get("status_code") != 404:
            return JSONResponse(content={"ok": False, "error": result.get("error") or result.get("text") or "树莓派对焦更新失败"}, status_code=502)
        await run_in_threadpool(camera_service_yzt.save_focus_settings, payload)
        if not result.get("ok"):
            # Older Pi agents do not expose /camera/focus. Their /camera/start
            # endpoint accepts the same focus fields, so restart the preview
            # with the selected parameters instead of failing confirmation.
            await run_in_threadpool(
                camera_service_yzt.stop_stream,
                reason="focus_confirm_compatibility_restart",
                source="focus_confirm",
                queue_algorithm=False,
                update_video_task=False,
                remove_video_task=True,
            )
            await run_in_threadpool(
                camera_service_yzt.start_stream,
                {
                    **payload,
                    "width": request.width or 1920,
                    "height": request.height or 1080,
                },
                calibration_only=True,
                write_video=False,
                create_video_task=False,
            )
            import asyncio
            import time
            deadline = time.time() + 30.0
            while time.time() < deadline:
                status = await run_in_threadpool(camera_service_yzt.get_status)
                if status.get("stream_ready") and camera_service_yzt.get_latest_frame():
                    break
                await asyncio.sleep(0.3)
            else:
                raise RuntimeError("重启摄像头后未获取到实时画面")
        else:
            import asyncio
            deadline = time.time() + 5.0
            while time.time() < deadline and not camera_service_yzt.get_latest_frame():
                await asyncio.sleep(0.2)
        path = await run_in_threadpool(camera_service_yzt.capture_focus_photo, "focused")
        return {
            "ok": True,
            "photo_url": to_static_url(path),
            "settings": payload,
            "restarted_for_compatibility": not result.get("ok"),
        }
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=409)


@router.post("/camera_yzt/focus")
async def update_focus(request: FocusUpdateRequest):
    """在线更新焦距（不重启预览流）。需要树莓派端支持 /camera/focus 端点。"""
    try:
        result = await run_in_threadpool(
            camera_service_yzt.focus_update,
            autofocus_mode=request.autofocus_mode,
            lens_position=request.lens_position,
            autofocus_window=request.autofocus_window,
        )
        return result
    except Exception as e:
        return JSONResponse(
            content={"ok": False, "error": str(e)},
            status_code=500,
        )


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


@router.post("/camera_yzt/sync")
async def sync_stream():
    """重新同步后端与树莓派：复用树莓派已有推流。

    用于“后端状态丢失、树莓派仍在推流”的 desync 场景，不停止 Pi、
    不创建 video_task、不写视频、不入队算法。
    """
    try:
        result = await run_in_threadpool(camera_service_yzt.sync_stream)
        payload = dict(result)
        payload.setdefault("preview_mode", True)
        payload.setdefault("force_restarted", False)
        return StartStreamResponse(**payload)
    except Exception as e:
        return StartStreamResponse(status="error", message=str(e))


class DebugMockRequest(BaseModel):
    pi_running: Optional[bool] = None
    backend_active: Optional[bool] = None
    pi_reachable: Optional[bool] = None


@router.post("/camera_yzt/debug/mock")
async def set_debug_mock(request: DebugMockRequest):
    """调试用：覆盖 desync 判定状态，用于验证“正常流/异常流/逆向流”。"""
    result = await run_in_threadpool(
        camera_service_yzt.set_debug_mock,
        pi_running=request.pi_running,
        backend_active=request.backend_active,
        pi_reachable=request.pi_reachable,
    )
    return result


@router.post("/camera_yzt/debug/mock/clear")
async def clear_debug_mock():
    """调试用：清除模拟状态，恢复真实状态。"""
    result = await run_in_threadpool(camera_service_yzt.clear_debug_mock)
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


# =============================================================================
# 未来录制计划：持久化 + 批量创建 + 冲突校验 + 取消
# =============================================================================
from fastapi import Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import or_, and_

from app.repositories.db import SessionLocal as _SessionLocal
from app.repositories import camera_record_schedule_repo as sched_repo


class ScheduleTaskItem(BaseModel):
    """单条计划任务（来自前端）。"""

    start_time: str = Field(...)
    end_time: str | None = Field(default=None)
    duration_minutes: int = Field(...)
    force_all_complete: bool = False

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("start_time 不能为空")
        return value.strip()

    @field_validator("duration_minutes")
    @classmethod
    def _validate_duration(cls, value: int) -> int:
        if not isinstance(value, int):
            raise ValueError("duration_minutes 必须是整数")
        if (
            value < sched_repo.MIN_SCHEDULE_DURATION_MINUTES
            or value > sched_repo.MAX_SCHEDULE_DURATION_MINUTES
        ):
            raise ValueError(
                f"duration_minutes 必须在 "
                f"{sched_repo.MIN_SCHEDULE_DURATION_MINUTES}"
                f"~{sched_repo.MAX_SCHEDULE_DURATION_MINUTES} 之间"
            )
        return value


class CreateSchedulesRequest(BaseModel):
    tasks: list[ScheduleTaskItem] = Field(..., min_length=1)


class CreateSchedulesResponse(BaseModel):
    ok: bool
    created_count: int
    items: list[dict]


def _parse_iso_to_aware_dt(value: str) -> datetime:
    """支持 ISO 8601 与 'YYYY-MM-DD HH:MM:SS' 两种格式；无时区按 Asia/Shanghai 解释。"""
    from app.utils.time_utils import to_beijing, now_beijing
    from zoneinfo import ZoneInfo

    text = (value or "").strip()
    if not text:
        raise ValueError("start_time 不能为空")
    try:
        # 兼容 'YYYY-MM-DD HH:MM:SS' 与 'YYYY-MM-DDTHH:MM:SS' 两种
        text_iso = text.replace(" ", "T")
        dt = datetime.fromisoformat(text_iso)
    except ValueError as exc:
        raise ValueError(f"start_time 无法解析: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return dt.astimezone(ZoneInfo("UTC"))


@router.post(
    "/camera_yzt/schedules",
    response_model=CreateSchedulesResponse,
)
def create_camera_yzt_schedules(
    payload: CreateSchedulesRequest,
) -> CreateSchedulesResponse:
    """批量创建未来录制计划。任一非法或冲突 → 整批拒绝。"""
    if len(payload.tasks) > sched_repo.MAX_SCHEDULE_TASKS_PER_REQUEST:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "too_many_tasks",
                "message": (
                    f"单次最多 {sched_repo.MAX_SCHEDULE_TASKS_PER_REQUEST} 条"
                ),
            },
        )

    parsed_tasks: list[dict] = []
    for t in payload.tasks:
        try:
            start_at = _parse_iso_to_aware_dt(t.start_time)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_start_time",
                    "message": str(exc),
                },
            )
        from app.utils.time_utils import now_beijing, to_beijing
        start_beijing = to_beijing(start_at)
        if start_beijing.date() not in {
            now_beijing().date(),
            now_beijing().date() + __import__("datetime").timedelta(days=1),
        }:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "schedule_not_today",
                    "message": "录制计划只能安排在今天或明天，请重新选择日期。",
                },
            )
        end_at = None
        if t.end_time:
            try:
                end_at = _parse_iso_to_aware_dt(t.end_time)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_end_time", "message": str(exc)},
                )
            end_beijing = to_beijing(end_at)
            if end_beijing.date() not in {
                now_beijing().date(),
                now_beijing().date() + __import__("datetime").timedelta(days=1),
            }:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "schedule_end_not_today_or_tomorrow",
                        "message": "结束时间只能选择今天或明天。",
                    },
                )
            if end_at <= start_at:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_end_time", "message": "结束时间必须晚于开始时间。"},
                )
            actual_duration = int((end_at - start_at).total_seconds() // 60)
            if actual_duration != t.duration_minutes:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "duration_mismatch", "message": "结束时间与录制时长不一致。"},
                )
        parsed_tasks.append(
            {
                "planned_start_at": start_at,
                "planned_end_at": end_at,
                    "duration_minutes": t.duration_minutes,
                    "force_all_complete": t.force_all_complete,
            }
        )

    # 同一请求内部冲突
    parsed_tasks.sort(key=lambda x: x["planned_start_at"])
    for i in range(1, len(parsed_tasks)):
        prev = parsed_tasks[i - 1]
        cur = parsed_tasks[i]
        if cur["planned_start_at"] < prev["planned_start_at"] + __import__(
            "datetime"
        ).timedelta(minutes=prev["duration_minutes"]):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "schedule_internal_conflict",
                    "message": "同一请求内录制计划时间冲突",
                },
            )

    db = _SessionLocal()
    try:
        # 与数据库已有计划冲突
        conflicts: list[dict] = []
        for t in parsed_tasks:
            new_start = t["planned_start_at"]
            new_end = new_start + __import__("datetime").timedelta(
                minutes=t["duration_minutes"]
            )
            for r in sched_repo.find_active_overlaps(
                db,
                planned_start_at=new_start,
                planned_end_at=new_end,
            ):
                conflicts.append(
                    {
                        "schedule_id": r.schedule_id,
                        "planned_start_at": r.planned_start_at.isoformat(),
                        "planned_end_at": r.planned_end_at.isoformat(),
                    }
                )
        if conflicts:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "schedule_conflict",
                    "message": "录制计划与已有任务时间冲突",
                    "conflicts": conflicts,
                },
            )

        rows = sched_repo.create_schedules(db, parsed_tasks)
        return CreateSchedulesResponse(
            ok=True,
            created_count=len(rows),
            items=[
                {
                    "schedule_id": r.schedule_id,
                    "status": r.status,
                    "planned_start_at": r.planned_start_at.isoformat(),
                    "planned_end_at": r.planned_end_at.isoformat(),
                    "duration_minutes": r.duration_minutes,
                    "video_id": r.video_id,
                }
                for r in rows
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "internal_error",
                "message": f"创建录制计划失败: {exc!r}",
            },
        )
    finally:
        db.close()


@router.get("/camera_yzt/schedules")
def list_camera_yzt_schedules(
    status: Optional[str] = Query(default=None),
    include_terminal: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    statuses: Optional[list[str]] = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    db = _SessionLocal()
    try:
        rows, total = sched_repo.list_schedules(
            db,
            statuses=statuses,
            include_terminal=include_terminal,
            limit=limit,
            offset=offset,
        )
        return {
            "ok": True,
            "total": total,
            "items": [
                {
                    "schedule_id": r.schedule_id,
                    "status": r.status,
                    "planned_start_at": r.planned_start_at.isoformat(),
                    "planned_end_at": r.planned_end_at.isoformat(),
                    "duration_minutes": r.duration_minutes,
                    "video_id": r.video_id,
                    "actual_start_at": r.actual_start_at.isoformat() if r.actual_start_at else None,
                    "actual_stop_at": r.actual_stop_at.isoformat() if r.actual_stop_at else None,
                    "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
                    "error_message": r.error_message,
                    "stop_reason": r.stop_reason,
                    "stop_source": r.stop_source,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.get("/camera_yzt/schedules/{schedule_id}")
def get_camera_yzt_schedule(schedule_id: str) -> dict:
    db = _SessionLocal()
    try:
        r = sched_repo.get_schedule(db, schedule_id)
        if r is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "schedule_not_found",
                    "message": "录制计划不存在",
                },
            )
        return {
            "ok": True,
            "schedule_id": r.schedule_id,
            "status": r.status,
            "planned_start_at": r.planned_start_at.isoformat(),
            "planned_end_at": r.planned_end_at.isoformat(),
            "duration_minutes": r.duration_minutes,
            "video_id": r.video_id,
            "actual_start_at": r.actual_start_at.isoformat() if r.actual_start_at else None,
            "actual_stop_at": r.actual_stop_at.isoformat() if r.actual_stop_at else None,
            "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
            "error_message": r.error_message,
            "stop_reason": r.stop_reason,
            "stop_source": r.stop_source,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
    finally:
        db.close()


@router.post("/camera_yzt/schedules/{schedule_id}/cancel")
def cancel_camera_yzt_schedule(schedule_id: str) -> dict:
    db = _SessionLocal()
    try:
        r = sched_repo.cancel_pending_schedule(
            db, schedule_id=schedule_id
        )
        if r is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "schedule_not_cancellable",
                    "message": "该计划不在 pending 状态，无法取消",
                },
            )
        return {
            "ok": True,
            "schedule_id": r.schedule_id,
            "status": r.status,
            "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
        }
    finally:
        db.close()
