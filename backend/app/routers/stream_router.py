# 作用：摄像头任务停止后的重试和旧帧路由兼容。
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.repositories.db import get_db
from app.repositories import session_repo
from app.workers.queue import (
    DEFAULT_QUEUE_NAME,
    enqueue_algorithm_video_pipeline_from_video_task,
)

router = APIRouter()


@router.post("/videos/{video_id}/stop")
async def stop_video(
    video_id: str,
    db: Session = Depends(get_db),
) -> dict:
    task = session_repo.get_video_task(db, video_id=video_id)
    if task is None:
        return {"status": "not_found", "video_id": video_id}
    session_repo.update_video_task_status(db, video_id=video_id, status="stopped")
    return {"status": "stopped", "video_id": video_id}


@router.post("/videos/{video_id}/requeue")
async def requeue_video(
    video_id: str,
    force: bool = False,
    force_all_complete: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """
    重新入队 video_task。

    注意：当前阶段已切到整视频算法 CLI；旧 requeue 行为暂时保留，但内部走新队列函数。
    """
    from app.services.path_service import to_abs_data_path

    task = session_repo.get_video_task(db, video_id=video_id)
    if task is None:
        return {"status": "not_found", "video_id": video_id}

    if task.status in ("queued", "processing") and not force:
        return {
            "status": "conflict",
            "video_id": video_id,
            "current_status": task.status,
            "message": "task already queued/processing; use force=true to requeue anyway",
        }

    from app.services.video_playback_service import (
        ensure_browser_playable_video,
        resolve_canonical_video_path,
    )

    canonical_source = resolve_canonical_video_path(video_id)
    if canonical_source is None:
        playback_result = ensure_browser_playable_video(video_id)
        if playback_result.get("ok"):
            canonical_source = resolve_canonical_video_path(video_id)

    abs_video_path = canonical_source
    if abs_video_path is None and task.source_path:
        abs_video_path = to_abs_data_path(task.source_path)

    if not abs_video_path or not abs_video_path.is_file():
        source_label = task.source_path or f"data/uploads/videos/{video_id}.mp4"
        err = f"video file missing on disk: {source_label}"
        session_repo.update_video_task_status(
            db, video_id=video_id, status="failed", last_error=err
        )
        return {"status": "failed", "video_id": video_id, "error": err}

    if canonical_source is not None:
        from app.services.path_service import to_data_relative

        canonical_rel = to_data_relative(canonical_source)
        if canonical_rel and task.source_path != canonical_rel:
            task.source_path = canonical_rel
            db.commit()

    # 入队时保存当前的传送带方向设置，确保任务使用入队时的配置
    from app.services.calibration_config_service import get_margin_config
    margin_config = get_margin_config()
    saved_conveyor_direction = margin_config.get("conveyor_direction")
    task.conveyor_direction = saved_conveyor_direction
    db.commit()

    job_id = enqueue_algorithm_video_pipeline_from_video_task(
        video_id=video_id,
        input_video=str(abs_video_path),
        force_all_complete=force_all_complete,
        conveyor_direction=saved_conveyor_direction,
    )

    task.status = "queued"
    task.last_error = None
    task.started_at = None
    task.finished_at = None
    task.processed_fish = 0
    db.commit()
    db.refresh(task)

    return {
        "status": "queued",
        "video_id": video_id,
        "job_id": job_id,
        "queue_name": DEFAULT_QUEUE_NAME,
    }


# 兼容旧前端：保留 /videos/{video_id}/frames 接口，但不再做抽帧逻辑。
@router.get("/videos/{video_id}/frames")
def get_video_frames(
    video_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """
    兼容旧路由。当前阶段抽帧由算法 CLI 在 video_pipeline 中完成，
    后端不再独立维护 frames 目录元信息。
    """
    task = session_repo.get_video_task(db, video_id=video_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"video_id not found: {video_id}")

    return {
        "video_id": video_id,
        "frame_dir": "",
        "exists": False,
        "frame_count": 0,
        "sample_files": [],
        "note": (
            "本阶段已切到整视频算法 CLI；frames 中间目录由算法生成在 data/jobs 下，"
            "前端不再需要后端抽帧元信息。"
        ),
    }


# 兜底函数：旧客户端误访问废弃路由时返回明确的迁移提示。
@router.api_route("/videos/{video_id}/legacy-{path:path}", methods=["GET", "POST"], include_in_schema=False)
def _legacy_not_supported(video_id: str, path: str) -> dict:
    raise HTTPException(
        status_code=410,
        detail={
            "ok": False,
            "error": "legacy endpoint removed; camera tasks are created by /api/camera_yzt/start",
            "video_id": video_id,
            "path": path,
        },
    )
