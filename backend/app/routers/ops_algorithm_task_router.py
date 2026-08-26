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
from app.services.path_service import to_abs_data_path, to_data_relative
from app.infra.redis_client import get_redis

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
    force_all_complete: bool = False


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
        # 先写跨进程取消标记。worker 会轮询该标记并终止 CLI 进程，
        # 同时在入库前再次检查，避免旧任务覆盖人工失败状态。
        redis_client = get_redis()
        generation_key = f"fish:algorithm-task:generation:{video_id}"
        # 更换任务代次会让旧 worker 的 token 立即失效。
        redis_client.set(generation_key, f"cancelled:{datetime.now(timezone.utc).timestamp()}", ex=24 * 3600)
        redis_client.set(
            f"fish:algorithm-task:cancel:{video_id}",
            payload.reason or "运维手动标记失败",
            ex=24 * 3600,
        )

        cancelled_jobs = 0
        try:
            from app.workers.queue import cancel_algorithm_jobs
            cancelled_jobs = cancel_algorithm_jobs(video_id)
        except Exception:
            logger.exception("cancel algorithm jobs failed: %s", video_id)

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
            "cancelled_jobs": cancelled_jobs,
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

        get_redis().delete(f"fish:algorithm-task:cancel:{video_id}")

        from app.services.video_playback_service import (
            ensure_browser_playable_video,
            resolve_canonical_video_path,
        )

        canonical_source = resolve_canonical_video_path(video_id)
        if canonical_source is None:
            playback_result = ensure_browser_playable_video(video_id)
            if playback_result.get("ok"):
                canonical_source = resolve_canonical_video_path(video_id)
        # results/input 是算法运行期副本，任务结束后可能已被清理，不能作为重试源。
        # 只有 canonical 视频不存在时，才允许检查数据库中的旧 source_path。
        src = str(canonical_source) if canonical_source else (vt.source_path or "")
        if not src:
            if not payload.force:
                raise HTTPException(
                    status_code=400,
                    detail="video_task.source_path 为空；如需强制 retry 请带 force=true",
                )
            abs_path = ""
        else:
            abs_path = Path(src) if Path(src).is_absolute() else to_abs_data_path(src)
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

        if canonical_source is not None:
            canonical_rel = to_data_relative(canonical_source)
            if canonical_rel and vt.source_path != canonical_rel:
                vt.source_path = canonical_rel
                db.commit()
                logger.info(
                    "retry: corrected source_path video_id=%s path=%s",
                    video_id,
                    canonical_rel,
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
            video_id=video_id, input_video=abs_path_str or "",
            force_all_complete=payload.force_all_complete,
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
