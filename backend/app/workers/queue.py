from __future__ import annotations

import os
import uuid
import logging
from pathlib import Path
from typing import Any

from rq import Queue
from rq.job import Retry

from app.infra.redis_client import get_redis

logger = logging.getLogger(__name__)


# Source mode keeps the historical ``fish`` queue. Packaged mode sets a
# separate name so a source worker can never consume a packaged job (or vice
# versa) when both installations share the same Redis instance.
DEFAULT_QUEUE_NAME = os.getenv("FISH_RQ_QUEUE", "fish").strip() or "fish"


def get_queue(name: str = DEFAULT_QUEUE_NAME) -> Queue:
    return Queue(
        name=name,
        connection=get_redis(),
        default_timeout=60 * 60 * 2,  # 2 hours
    )


def _task_token(video_id: str) -> str:
    from app.infra.redis_client import get_redis

    token = uuid.uuid4().hex
    get_redis().set(f"fish:algorithm-task:generation:{video_id}", token)
    return token


def cancel_algorithm_jobs(video_id: str) -> int:
    """取消该视频仍在队列中的旧 job；started job 由 worker 的取消标记处理。"""
    q = get_queue()
    cancelled = 0
    jobs = []
    jobs.extend(q.get_jobs())
    for registry_name in ("started", "scheduled", "deferred"):
        try:
            registry = q.started_job_registry if registry_name == "started" else getattr(q, f"{registry_name}_job_registry")
            jobs.extend(registry.get_job_ids())
        except Exception:
            continue
    seen: set[str] = set()
    for item in jobs:
        job_id = item.id if hasattr(item, "id") else item
        if job_id in seen:
            continue
        seen.add(job_id)
        try:
            from rq.job import Job
            job = item if hasattr(item, "get_call_string") else Job.fetch(job_id, connection=q.connection)
            kwargs = job.kwargs or {}
            if kwargs.get("task_video_id") != video_id:
                continue
            if job.get_status() in {"queued", "scheduled", "deferred"}:
                job.cancel()
                cancelled += 1
        except Exception:
            continue
    return cancelled


# ---------------------------------------------------------------------------
# 新主流程：整视频算法 CLI pipeline
# ---------------------------------------------------------------------------


def enqueue_algorithm_video_pipeline(
    *,
    input_video: str,
    camera_id: str = "default",
    device: str = "cuda",
    video_fps: int = 25,
    overwrite: bool = True,
) -> str:
    """
    入队整视频算法 CLI pipeline。

    真实逻辑：调用 algorithms.tools.run_video_pipeline 后导入数据库。
    """
    from app.workers.handlers.algorithm_video_pipeline import (
        run_algorithm_video_pipeline,
    )

    q = get_queue()
    job = q.enqueue(
        run_algorithm_video_pipeline,
        kwargs={
            "input_video": input_video,
            "camera_id": camera_id,
            "device": device,
            "video_fps": video_fps,
            "overwrite": overwrite,
        },
        retry=Retry(max=2, interval=[10, 30]),
        # 长视频需要先完整抽帧；RQ 超时必须覆盖算法 CLI 超时，避免
        # Worker 在 CLI 仍有机会完成时先杀掉任务。
        job_timeout=60 * 60 * 8,
        result_ttl=60 * 60 * 2,
        failure_ttl=60 * 60,
    )
    return job.id


def enqueue_algorithm_video_pipeline_from_video_task(
    *,
    video_id: str,
    input_video: str,
    force_all_complete: bool = False,
    conveyor_direction: str | None = None,
) -> str:
    """
    视频上传 / 摄像头停止时使用的便捷入口。
    video_id 作为算法 CLI 的固定任务 ID，确保 jobs/results/数据库使用同一目录键。
    """
    from app.workers.handlers.algorithm_video_pipeline import (
        run_algorithm_video_pipeline,
    )

    q = get_queue()
    task_token = _task_token(video_id)
    # 让同一视频的旧排队任务失效并从队列中移除；已运行任务由 generation
    # token 在 worker 内部终止，避免新旧任务同时覆盖同一 video_task。
    cancel_algorithm_jobs(video_id)
    job = q.enqueue(
        run_algorithm_video_pipeline,
        kwargs={
            "task_video_id": video_id,
            "input_video": input_video,
            "task_token": task_token,
            "force_all_complete": force_all_complete,
            "conveyor_direction": conveyor_direction,
        },
        retry=Retry(max=2, interval=[10, 30]),
        # 长视频需要先完整抽帧；RQ 超时必须覆盖算法 CLI 超时。
        job_timeout=60 * 60 * 8,
        result_ttl=60 * 60 * 2,
        failure_ttl=60 * 60,
    )
    return job.id


def recover_missing_queued_video_tasks() -> dict[str, Any]:
    """Re-enqueue unfinished DB tasks whose RQ jobs disappeared during restart.

    RQ jobs are durable in Redis, but a Redis restart or a manually interrupted
    worker can leave ``video_task.status='queued'`` without a corresponding
    job. Recovery is deliberately conservative: active RQ jobs are excluded,
    and tasks without an existing source file are left for manual repair.
    """
    from app.repositories.db import SessionLocal
    from app.repositories.models import VideoTask
    from app.services.path_service import to_abs_data_path

    q = get_queue()
    known_video_ids: set[str] = set()
    try:
        for job in q.get_jobs():
            known_video_ids.add(str((job.kwargs or {}).get("task_video_id") or ""))
        for registry_name in ("started", "scheduled", "deferred"):
            registry = (
                q.started_job_registry
                if registry_name == "started"
                else getattr(q, f"{registry_name}_job_registry")
            )
            for job_id in registry.get_job_ids():
                try:
                    from rq.job import Job

                    job = Job.fetch(job_id, connection=q.connection)
                    known_video_ids.add(str((job.kwargs or {}).get("task_video_id") or ""))
                except Exception:
                    continue
    except Exception:
        logger.exception("cannot inspect RQ jobs during startup recovery")

    recovered: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    db = SessionLocal()
    try:
        tasks = (
            db.query(VideoTask)
            .filter(VideoTask.status.in_(("queued", "processing", "interrupted")))
            .order_by(VideoTask.created_at.asc())
            .all()
        )
        for task in tasks:
            video_id = str(task.video_id)
            if video_id in known_video_ids:
                continue
            source = task.source_path or ""
            source_path = Path(source) if Path(source).is_absolute() else to_abs_data_path(source)
            if not source_path or not source_path.is_file():
                from app.services.video_playback_service import resolve_canonical_video_path

                source_path = resolve_canonical_video_path(video_id)
            if not source_path or not source_path.is_file() or source_path.stat().st_size <= 0:
                skipped.append({"video_id": video_id, "reason": "source_file_missing"})
                continue
            try:
                task.status = "queued"
                task.last_error = None
                db.commit()
                job_id = enqueue_algorithm_video_pipeline_from_video_task(
                    video_id=video_id,
                    input_video=str(source_path),
                )
                recovered.append({"video_id": video_id, "job_id": job_id})
                known_video_ids.add(video_id)
            except Exception as exc:
                skipped.append({"video_id": video_id, "reason": f"enqueue_failed: {exc}"})
    finally:
        db.close()

    result = {"recovered": recovered, "skipped": skipped}
    logger.info("startup queue recovery: %s", result)
    return result
