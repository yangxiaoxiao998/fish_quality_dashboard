# 作用：保留旧上传视频接口，内部改为入队到整视频算法 pipeline。
# backend/app/routers/stream_router.py
from __future__ import annotations

from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from sqlalchemy.orm import Session

from app.paths import VID_DIR, DATA_DIR
from app.services.path_service import to_data_relative, to_static_url
from app.services.video_id_service import generate_video_id

from app.repositories.db import get_db
from app.repositories import session_repo
from app.workers.queue import enqueue_algorithm_video_pipeline_from_video_task

router = APIRouter()


@router.post("/stream/upload")
async def upload_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    return await _upload_video_impl(file, db)


@router.post("/videos")
async def upload_video_v2(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    return await _upload_video_impl(file, db)


async def _upload_video_impl(
    file: UploadFile,
    db: Session,
) -> dict:
    content = await file.read()
    suffix = Path(file.filename).suffix.lower() or ".mp4"

    if not content:
        err = f"empty upload: filename={file.filename!r}"
        print(f"[UPLOAD] FAILED: {err}")
        raise HTTPException(status_code=400, detail=err)

    video_id = generate_video_id(db, source_type="upload")
    video_path = VID_DIR / f"{video_id}{suffix}"
    video_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(video_path, "wb") as f:
        await f.write(content)

    rel_video_path = to_data_relative(video_path)
    session_repo.create_video_task(
        db,
        video_id=video_id,
        source_type="upload",
        source_path=rel_video_path,
        rtsp_url=None,
    )

    queued = False
    job_id: str | None = None
    queue_error: str | None = None

    try:
        job_id = enqueue_algorithm_video_pipeline_from_video_task(
            video_id=video_id,
            input_video=str(video_path),
        )
        queued = True
    except Exception as e:
        queue_error = repr(e)
        print(f"[UPLOAD] FAILED enqueue algorithm pipeline: video_id={video_id!r} err={queue_error}")

    if not queued:
        err = f"enqueue algorithm pipeline failed: {queue_error}"
        try:
            session_repo.update_video_task_status(
                db, video_id=video_id, status="failed", last_error=err
            )
        except Exception:
            db.rollback()
        return {
            "status": "queue_failed",
            "video_id": video_id,
            "queue_error": queue_error,
            "error": err,
        }

    playback_url = to_static_url(rel_video_path)
    task = session_repo.get_video_task(db, video_id=video_id)

    return {
        "status": "queued",
        "video_id": video_id,
        "job_id": job_id,
        "task_id": job_id,
        "created_at": str(task.created_at) if task else "",
        "playback_url": playback_url,
        "queue_name": "fish",
        "enqueued": True,
        "note": (
            "本阶段已切换到整视频算法 CLI pipeline，"
            "video_id 在算法 CLI 内部自动生成；本接口返回的是上传阶段 video_id。"
        ),
    }


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
    db: Session = Depends(get_db),
) -> dict:
    """
    重新入队 video_task。

    注意：当前阶段已切到整视频算法 CLI；旧 requeue 行为暂时保留，但内部走新队列函数。
    """
    import os as _os
    from app.services.path_service import to_abs_data_path

    task = session_repo.get_video_task(db, video_id=video_id)
    if task is None:
        return {"status": "not_found", "video_id": video_id}

    if not task.source_path:
        err = f"video_task has no source_path: {video_id}"
        session_repo.update_video_task_status(
            db, video_id=video_id, status="failed", last_error=err
        )
        return {"status": "failed", "video_id": video_id, "error": err}

    abs_video_path = to_abs_data_path(task.source_path)
    if not abs_video_path or not _os.path.exists(abs_video_path):
        err = f"video file missing on disk: {task.source_path}"
        session_repo.update_video_task_status(
            db, video_id=video_id, status="failed", last_error=err
        )
        return {"status": "failed", "video_id": video_id, "error": err}

    if task.status in ("queued", "processing") and not force:
        return {
            "status": "conflict",
            "video_id": video_id,
            "current_status": task.status,
            "message": "task already queued/processing; use force=true to requeue anyway",
        }

    job_id = enqueue_algorithm_video_pipeline_from_video_task(
        video_id=video_id,
        input_video=str(abs_video_path),
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
        "queue_name": "fish",
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


# 兜底函数：当某些旧路由 import 了不存在的工具时，向上层返回明确错误。
@router.api_route("/videos/{video_id}/legacy-{path:path}", methods=["GET", "POST"], include_in_schema=False)
def _legacy_not_supported(video_id: str, path: str) -> dict:
    raise HTTPException(
        status_code=410,
        detail={
            "ok": False,
            "error": "legacy endpoint removed; use /api/algorithm/videos",
            "video_id": video_id,
            "path": path,
        },
    )
