"""
ops_algorithm_task_router.py

系统运维 -> 算法任务监控 后端接口。

路由：
    GET    /ops/algorithm-tasks/summary
    GET    /ops/algorithm-tasks
    GET    /ops/algorithm-tasks/{video_id}
    POST   /ops/algorithm-tasks/{video_id}/mark-failed
    POST   /ops/algorithm-tasks/{video_id}/retry

仅运维人员使用；普通用户页面继续显示普通 "正在处理中，请稍后刷新" 提示。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.repositories.db import SessionLocal
from app.repositories import session_repo
from app.repositories.models import VideoTask
from app.services import ops_algorithm_task_service
from app.services import algorithm_runtime_status_service
from app.services.path_service import to_abs_data_path

logger = logging.getLogger("ops_algorithm_task_router")

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MarkFailedRequest(BaseModel):
    reason: str = Field(..., description="运维手动标记失败原因")
    force: bool = Field(
        default=False,
        description="是否允许把已经 done 的任务也标记为 failed（默认 False）",
    )


class RetryRequest(BaseModel):
    force: bool = Field(
        default=False, description="是否强制重新入队（即使 source_path 缺失）"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ops/algorithm-tasks/summary")
def get_algorithm_tasks_summary() -> dict[str, Any]:
    return ops_algorithm_task_service.get_algorithm_summary()


@router.get("/ops/algorithm-runtime/status")
def get_algorithm_runtime_status() -> dict[str, Any]:
    """
    模型运行状态 + 模型文件检查 + GPU/CUDA 探测。

    仅作只读检查，**不**在 FastAPI 进程内预热模型。
    真正要常驻模型需要改造为常驻算法服务。
    """
    return algorithm_runtime_status_service.get_algorithm_runtime_status()


@router.post("/ops/algorithm-runtime/preload")
def post_algorithm_runtime_preload() -> dict[str, Any]:
    """
    模型预热。

    当前 CLI 子进程模式下：模型无法跨任务常驻显存，返回 PRELOAD_NOT_SUPPORTED_IN_CLI_MODE。
    """
    return algorithm_runtime_status_service.preload_algorithm_runtime()


@router.get("/ops/algorithm-tasks")
def list_algorithm_tasks(
    status: Optional[str] = Query(default=None, description="按 DB status 过滤"),
    date: Optional[str] = Query(
        default=None,
        description="按 created_at 日期过滤，格式 YYYY-MM-DD",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    return ops_algorithm_task_service.list_algorithm_tasks(
        status=status, date=date, limit=limit
    )


@router.get("/ops/algorithm-tasks/{video_id}")
def get_algorithm_task_detail(video_id: str) -> dict[str, Any]:
    return ops_algorithm_task_service.get_algorithm_task_detail(video_id)


@router.post("/ops/algorithm-tasks/{video_id}/mark-failed")
def mark_algorithm_task_failed(
    video_id: str, payload: MarkFailedRequest
) -> dict[str, Any]:
    """
    运维手动把 video_task 标记为 failed。
    - 默认只允许 status in [processing, queued, recording] 的任务被标记为 failed
    - 想要把 done 的任务改成 failed，必须 force=true
    """
    db = SessionLocal()
    try:
        vt = db.get(VideoTask, video_id)
        if vt is None:
            raise HTTPException(
                status_code=404,
                detail=f"VideoTask 不存在：{video_id}",
            )
        cur = (vt.status or "").lower()
        if cur == "done" and not payload.force:
            raise HTTPException(
                status_code=400,
                detail="任务已经 done；如需改为 failed，请带 force=true。",
            )
        if cur not in {"processing", "queued", "recording", "failed", "done"} and not payload.force:
            raise HTTPException(
                status_code=400,
                detail=f"当前 status={vt.status} 不允许手动标记为 failed；如需强改请带 force=true。",
            )
        try:
            session_repo.update_video_task_status(
                db,
                video_id=video_id,
                status="failed",
                last_error=(payload.reason or "运维手动标记失败")[:2000],
            )
        except Exception as exc:
            logger.exception("mark failed update failed: %s", video_id)
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"更新失败：{type(exc).__name__}: {exc}",
            )
        return {
            "ok": True,
            "video_id": video_id,
            "status": "failed",
            "last_error": payload.reason,
        }
    finally:
        db.close()


@router.post("/ops/algorithm-tasks/{video_id}/retry")
def retry_algorithm_task(
    video_id: str, payload: RetryRequest
) -> dict[str, Any]:
    """
    重新入队算法任务。

    1. 找 source_path
    2. 转绝对路径
    3. 调 enqueue_algorithm_video_pipeline_from_video_task
    4. 更新 video_task: status=queued, last_error=null
    """
    db = SessionLocal()
    try:
        vt = db.get(VideoTask, video_id)
        if vt is None:
            raise HTTPException(
                status_code=404, detail=f"VideoTask 不存在：{video_id}"
            )

        src = vt.source_path or vt.input_video or vt.original_input_video
        if not src:
            if not payload.force:
                raise HTTPException(
                    status_code=400,
                    detail="video_task.source_path 为空；如需强制 retry 请带 force=true",
                )
            abs_path = ""
        else:
            abs_path = to_abs_data_path(src)
            abs_path_str = str(abs_path) if abs_path else ""
            if not abs_path_str or not Path(abs_path_str).exists():
                if not payload.force:
                    raise HTTPException(
                        status_code=400,
                        detail=f"source_path 指向的文件不存在：{abs_path_str}",
                    )
                abs_path_str = ""

        if not abs_path_str and not payload.force:
            raise HTTPException(
                status_code=400,
                detail="无法解析 source_path；如需强制 retry 请带 force=true",
            )

        # 清理可能残留的 data/jobs/<video_id>，避免算法 CLI 报"job 目录已存在"
        # （虽然 run_video_pipeline 自身 --overwrite 也会清理，这里多一道保险）
        try:
            from app.paths import JOBS_DIR

            job_root = JOBS_DIR / video_id
            if job_root.exists():
                import shutil

                shutil.rmtree(job_root, ignore_errors=True)
                logger.info("retry: cleared stale job_root=%s", job_root)
        except Exception as exc:
            logger.warning("retry: clear job_root failed: %s", exc)

        # 入队
        from app.workers.queue import enqueue_algorithm_video_pipeline_from_video_task

        job_id = enqueue_algorithm_video_pipeline_from_video_task(
            video_id=video_id, input_video=abs_path_str or ""
        )

        # 更新 video_task
        try:
            session_repo.update_video_task_status(
                db, video_id=video_id, status="queued"
            )
            vt.current_algorithm = "queued"
            vt.last_error = None
            vt.failed_stage = None
            vt.error_message = None
            vt.started_at = None
            vt.finished_at = None
            db.commit()
        except Exception as exc:
            logger.exception("retry: update video_task failed: %s", video_id)
            db.rollback()

        return {
            "ok": True,
            "video_id": video_id,
            "status": "queued",
            "job_id": job_id,
            "input_video": abs_path_str,
        }
    finally:
        db.close()


__all__ = ["router"]