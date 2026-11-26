# 作用：提供系统健康检查和就绪检查的 API 接口
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text, func, select

from app.repositories.db import (
    get_db,
    engine,
    REQUIRED_TABLES,
    _current_database,
    _mask_url,
)
from app.settings import settings
from app.repositories.models import (
    DetectionReport,
    Fish,
    FrameFishDetection,
    Stats5Min,
    VideoFrame,
    VideoTask,
)
from app.infra.redis_client import get_redis
from app.services.path_service import to_abs_data_path

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    r = get_redis()
    r.ping()
    return {"status": "ready"}


@router.get("/health/database")
def health_database(db: Session = Depends(get_db)) -> dict:
    """
    数据库健康检查。

    返回：
        database: 当前连接的目标数据库名
        connected: 是否能正常查询
        tables: 已存在的表列表
        fish_count: fish 表行数
        video_task_count: video_task 表行数
        stats_5min_count: stats_5min 表行数
        latest_fish: 最近一条 fish 的 fish_id + created_at
        latest_video_task: 最近一条 video_task 的 video_id + created_at
        latest_stats: 最近一条 stats 的 bucket_start + count
    """
    info: dict = {
        "database_url": _mask_url(settings.database_url),
        "database": None,
        "connected": False,
        "tables": [],
        "required_tables": list(REQUIRED_TABLES),
        "required_tables_present": False,
        "fish_count": 0,
        "video_task_count": 0,
        "stats_5min_count": 0,
        "latest_fish": None,
        "latest_video_task": None,
        "latest_stats": None,
    }

    try:
        info["database"] = _current_database()
        info["connected"] = True
    except Exception as e:
        info["connected"] = False
        info["error"] = str(e)
        return info

    try:
        info["fish_count"] = int(
            db.execute(select(func.count()).select_from(Fish)).scalar() or 0
        )
    except Exception as e:
        info["fish_count_error"] = str(e)

    try:
        info["video_task_count"] = int(
            db.execute(select(func.count()).select_from(VideoTask)).scalar() or 0
        )
    except Exception as e:
        info["video_task_count_error"] = str(e)

    try:
        info["stats_5min_count"] = int(
            db.execute(select(func.count()).select_from(Stats5Min)).scalar() or 0
        )
    except Exception as e:
        info["stats_5min_count_error"] = str(e)

    try:
        from sqlalchemy import inspect
        insp = inspect(engine)
        info["tables"] = sorted(insp.get_table_names())
        info["required_tables_present"] = all(t in info["tables"] for t in REQUIRED_TABLES)
    except Exception as e:
        info["tables_error"] = str(e)

    try:
        latest_fish = db.execute(
            select(Fish).order_by(Fish.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_fish is not None:
            info["latest_fish"] = {
                "fish_id": latest_fish.fish_id,
                "video_id": latest_fish.video_id,
                "status": latest_fish.status,
                "created_at": latest_fish.created_at.isoformat()
                if latest_fish.created_at
                else None,
            }
    except Exception as e:
        info["latest_fish_error"] = str(e)

    try:
        latest_vt = db.execute(
            select(VideoTask).order_by(VideoTask.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_vt is not None:
            info["latest_video_task"] = {
                "video_id": latest_vt.video_id,
                "status": latest_vt.status,
                "total_fish": latest_vt.total_fish,
                "created_at": latest_vt.created_at.isoformat()
                if latest_vt.created_at
                else None,
            }
    except Exception as e:
        info["latest_video_task_error"] = str(e)

    try:
        latest_st = db.execute(
            select(Stats5Min).order_by(Stats5Min.bucket_start.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_st is not None:
            info["latest_stats"] = {
                "video_id": latest_st.video_id,
                "bucket_start": latest_st.bucket_start.isoformat()
                if latest_st.bucket_start
                else None,
                "count": latest_st.count,
            }
    except Exception as e:
        info["latest_stats_error"] = str(e)

    # Data integrity diagnostics are deliberately read-only. They make the
    # health endpoint useful after imports, retries, deletes, and upgrades.
    diagnostics: dict = {
        "missing_required_columns": {},
        "orphan_fish_count": None,
        "orphan_frame_count": None,
        "orphan_detection_count": None,
        "orphan_report_count": None,
        "fish_count_mismatch_task_count": None,
        "missing_pipeline_result_file_count": None,
    }
    try:
        from sqlalchemy import inspect

        inspector = inspect(engine)
        required_columns = {
            "video_task": {
                "video_id", "source_path", "status",
                "pipeline_result_path", "result_dir", "complete_fish",
                "incomplete_fish", "failed_fish",
            },
            "fish": {
                "fish_id", "video_id", "status", "fish_uid",
                "body_length_cm", "body_length_no_tail_cm", "body_height_cm",
                "pred_weight_g", "surface_damage_ratio",
            },
        }
        for table, expected in required_columns.items():
            actual = {column["name"] for column in inspector.get_columns(table)}
            missing = sorted(expected - actual)
            if missing:
                diagnostics["missing_required_columns"][table] = missing
    except Exception as exc:
        diagnostics["schema_diagnostics_error"] = str(exc)

    try:
        diagnostics["orphan_fish_count"] = int(
            db.execute(
                select(func.count())
                .select_from(Fish)
                .outerjoin(VideoTask, Fish.video_id == VideoTask.video_id)
                .where(VideoTask.video_id.is_(None))
            ).scalar()
            or 0
        )
        diagnostics["orphan_frame_count"] = int(
            db.execute(
                select(func.count())
                .select_from(VideoFrame)
                .outerjoin(VideoTask, VideoFrame.video_id == VideoTask.video_id)
                .where(VideoTask.video_id.is_(None))
            ).scalar()
            or 0
        )
        diagnostics["orphan_detection_count"] = int(
            db.execute(
                select(func.count())
                .select_from(FrameFishDetection)
                .outerjoin(VideoTask, FrameFishDetection.video_id == VideoTask.video_id)
                .where(VideoTask.video_id.is_(None))
            ).scalar()
            or 0
        )
        diagnostics["orphan_report_count"] = int(
            db.execute(
                select(func.count())
                .select_from(DetectionReport)
                .outerjoin(VideoTask, DetectionReport.video_id == VideoTask.video_id)
                .where(
                    DetectionReport.video_id.is_not(None),
                    VideoTask.video_id.is_(None),
                )
            ).scalar()
            or 0
        )
        diagnostics["fish_count_mismatch_task_count"] = int(
            db.execute(
                select(func.count())
                .select_from(
                    select(
                        VideoTask.video_id,
                        VideoTask.total_fish,
                        func.count(Fish.fish_id).label("actual_fish"),
                    )
                    .outerjoin(Fish, Fish.video_id == VideoTask.video_id)
                    .group_by(VideoTask.video_id, VideoTask.total_fish)
                    .subquery()
                )
                .where(text("total_fish <> actual_fish"))
            ).scalar()
            or 0
        )
    except Exception as exc:
        diagnostics["relational_diagnostics_error"] = str(exc)

    try:
        tasks_with_results = db.execute(
            select(VideoTask.pipeline_result_path)
            .where(VideoTask.pipeline_result_path.is_not(None))
        ).scalars().all()
        missing_results = 0
        for stored_path in tasks_with_results:
            path = to_abs_data_path(stored_path)
            if path is None or not path.is_file():
                missing_results += 1
        diagnostics["missing_pipeline_result_file_count"] = missing_results
    except Exception as exc:
        diagnostics["file_diagnostics_error"] = str(exc)

    info["data_diagnostics"] = diagnostics

    return info


@router.get("/worker/status")
def worker_status() -> dict:
    """
    详细的 worker / queue 状态。

    返回字段：
        queue              队列名
        queued_count       队列中待执行任务数
        started_count      正在执行任务数（worker.started_registry）
        failed_count       失败任务数（worker.failed_job_registry）
        finished_count     已完成任务数（worker.finished_job_registry）
        deferred_count     延迟任务数（worker.deferred_job_registry）
        workers            当前活跃 worker 列表
        queued_jobs        队列中前 N 个任务的详情
        failed_jobs        最近 N 个失败任务
    """
    from app.workers.queue import get_queue

    q = get_queue()

    info: dict = {
        "queue": q.name,
        "queued_count": q.count,
        "queued_job_ids": q.job_ids,
    }

    # Helpers ---------------------------------------------------------------
    def _safe_count(registry_factory) -> int:
        """
        实例化一个 RQ registry 并安全地读取 count。
        任何异常都返回 0，绝不抛错。
        """
        try:
            reg = registry_factory(q)
            return int(reg.count or 0)
        except Exception:
            return 0

    from rq.registry import (
        StartedJobRegistry,
        FailedJobRegistry,
        FinishedJobRegistry,
        DeferredJobRegistry,
    )

    # Workers via Worker.all()
    workers_info: list[dict] = []
    started_count = 0
    failed_count = 0
    finished_count = 0
    deferred_count = 0
    try:
        from rq import Worker
        for w in Worker.all(get_redis()):
            state = w.get_state()
            current_job = w.get_current_job()
            workers_info.append({
                "name": w.name,
                "state": state,
                "current_job_id": current_job.id if current_job else None,
                "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
            })
    except Exception as e:
        info["workers_error"] = str(e)

    # 从 queue 创建 registry；registry 不是 Worker 的属性
    # 一个 queue 同一时刻只会有一个对应的 registry
    started_count = _safe_count(StartedJobRegistry)
    failed_count = _safe_count(FailedJobRegistry)
    finished_count = _safe_count(FinishedJobRegistry)
    deferred_count = _safe_count(DeferredJobRegistry)

    info["workers"] = workers_info
    info["worker_count"] = len(workers_info)
    info["started_count"] = started_count
    info["failed_count"] = failed_count
    info["finished_count"] = finished_count
    info["deferred_count"] = deferred_count

    # 队列任务详情
    queued_jobs: list[dict] = []
    for jid in q.job_ids[:20]:
        try:
            job = q.fetch_job(jid)
            if job is not None:
                queued_jobs.append({
                    "id": job.id,
                    "func_name": job.func_name,
                    "args": [str(a) for a in (job.args or [])][:3],
                    "kwargs": {k: str(v) for k, v in (job.kwargs or {}).items()},
                    "status": job.get_status(),
                    "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                })
        except Exception:
            continue
    info["queued_jobs"] = queued_jobs

    # 失败任务详情
    failed_jobs: list[dict] = []
    try:
        from rq.registry import FailedJobRegistry
        failed_reg = FailedJobRegistry(queue=q)
        for jid in list(failed_reg.get_job_ids())[:20]:
            try:
                job = failed_reg.fetch_job(jid)
                if job is not None:
                    failed_jobs.append({
                        "id": job.id,
                        "func_name": job.func_name,
                        "args": [str(a) for a in (job.args or [])][:3],
                        "failed_at": job.failed_at.isoformat() if job.failed_at else None,
                        "exc_info": (job.exc_info or "")[:500] if job.exc_info else "",
                    })
            except Exception:
                continue
    except Exception as e:
        info["failed_jobs_error"] = str(e)
    info["failed_jobs"] = failed_jobs

    return info
