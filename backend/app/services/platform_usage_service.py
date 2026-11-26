from __future__ import annotations

from datetime import timedelta
from math import isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import VideoTask
from app.utils.time_utils import now_beijing
from app.services.video_task_metrics_service import (
    processing_duration_seconds,
    video_duration_seconds,
)


_RANGE_DAYS = {
    "7d": 7,
    "30d": 30,
    "all": None,
}


def _range_start(range_value: str):
    days = _RANGE_DAYS.get(range_value)
    if days is None:
        return None
    return now_beijing() - timedelta(days=days)


def get_platform_usage_summary(db: Session, range_value: str = "7d") -> dict:
    start = _range_start(range_value)

    stmt = select(VideoTask)
    if start is not None:
        stmt = stmt.where(VideoTask.created_at >= start)
    tasks = list(db.execute(stmt).scalars().all())

    total = len(tasks)
    camera_count = total
    done_count = len([t for t in tasks if t.status == "done"])
    failed_count = len([t for t in tasks if t.status == "failed"])
    interrupted_count = len([t for t in tasks if t.status == "interrupted"])
    no_fish_count = len([t for t in tasks if t.status == "no_fish_detected"])

    total_fish = sum(int(t.total_fish or 0) for t in tasks)
    avg_fish = round(total_fish / total, 2) if total else 0

    durations: list[float] = []
    fps_values = []
    for t in tasks:
        duration = processing_duration_seconds(t)
        if duration is not None:
            durations.append(duration)
        if t.fps_estimate:
            fps_values.append(float(t.fps_estimate))

    avg_processing = round(sum(durations) / len(durations), 2) if durations else 0
    max_processing = round(max(durations), 2) if durations else 0
    avg_fps = round(sum(fps_values) / len(fps_values), 2) if fps_values else None

    total_duration = 0.0
    for task in tasks:
        value = video_duration_seconds(task)
        if value is not None:
            total_duration += value
    total_video_duration = round(total_duration, 2)

    return {
        "total_video_tasks": total,
        "camera_video_count": camera_count,
        "done_task_count": done_count,
        "failed_task_count": failed_count,
        "interrupted_task_count": interrupted_count,
        "no_fish_task_count": no_fish_count,
        "success_rate": round(done_count / total, 4) if total else 0,
        "failure_rate": round(failed_count / total, 4) if total else 0,
        "total_fish": total_fish,
        "avg_fish_per_video": avg_fish,
        "total_video_duration": total_video_duration,
        "avg_processing_duration": avg_processing,
        "processing_duration_sample_count": len(durations),
        "max_processing_duration": max_processing,
        "avg_fps": avg_fps,
        "camera_start_count": camera_count,
    }


def get_platform_usage_charts(db: Session, range_value: str = "7d") -> dict:
    start = _range_start(range_value)

    stmt = select(VideoTask)
    if start is not None:
        stmt = stmt.where(VideoTask.created_at >= start)
    tasks = list(db.execute(stmt).scalars().all())

    status_map: dict[str, int] = {}
    daily_task: dict[str, int] = {}
    daily_fish: dict[str, int] = {}
    daily_failed: dict[str, int] = {}
    daily_abnormal: dict[str, int] = {}
    daily_duration_sum: dict[str, float] = {}
    daily_duration_count: dict[str, int] = {}

    for t in tasks:
        day = t.created_at.strftime("%Y-%m-%d") if t.created_at else "unknown"
        daily_task[day] = daily_task.get(day, 0) + 1
        daily_fish[day] = daily_fish.get(day, 0) + int(t.total_fish or 0)
        if t.status == "failed":
            daily_failed[day] = daily_failed.get(day, 0) + 1
        if t.status in {"failed", "interrupted"}:
            daily_abnormal[day] = daily_abnormal.get(day, 0) + 1

        duration_seconds = processing_duration_seconds(t)
        if duration_seconds is not None:
            if isfinite(duration_seconds) and duration_seconds >= 0:
                daily_duration_sum[day] = daily_duration_sum.get(day, 0.0) + duration_seconds
                daily_duration_count[day] = daily_duration_count.get(day, 0) + 1

        status_map[t.status or "unknown"] = status_map.get(t.status or "unknown", 0) + 1

    avg_processing_duration_trend = []
    for day in sorted(daily_duration_sum.keys()):
        s = daily_duration_sum[day]
        c = daily_duration_count[day]
        avg_processing_duration_trend.append({
            "date": day,
            "avg_seconds": round(s / c, 2) if c else 0,
        })

    return {
        "daily_video_task_trend": [
            {"date": k, "count": v} for k, v in sorted(daily_task.items())
        ],
        "daily_fish_trend": [
            {"date": k, "count": v} for k, v in sorted(daily_fish.items())
        ],
        "camera_task_distribution": [{"label": "摄像头", "count": len(tasks)}],
        "task_status_distribution": [
            {"label": k, "count": v} for k, v in status_map.items()
        ],
        "daily_failed_task_trend": [
            {"date": k, "count": v} for k, v in sorted(daily_failed.items())
        ],
        "daily_abnormal_task_trend": [
            {"date": day, "count": daily_abnormal.get(day, 0)}
            for day in sorted(daily_task.keys())
        ],
        "avg_processing_duration_trend": avg_processing_duration_trend,
    }


def get_platform_failures(db: Session, range_value: str = "7d") -> dict:
    start = _range_start(range_value)
    stmt = select(VideoTask).where(VideoTask.status == "failed")
    if start is not None:
        stmt = stmt.where(VideoTask.created_at >= start)
    tasks = list(db.execute(stmt).scalars().all())

    return {
        "items": [
            {
                "video_id": t.video_id,
                "stage": t.failed_stage or _extract_failed_stage(t.error_message or t.last_error),
                "reason": _failure_reason(t),
                "created_at": t.finished_at or t.updated_at or t.created_at,
            }
            for t in sorted(
                tasks,
                key=lambda item: item.finished_at or item.updated_at or item.created_at,
                reverse=True,
            )
        ]
    }


def _extract_failed_stage(message: str | None) -> str | None:
    text = str(message or "")
    for line in text.splitlines():
        normalized = line.strip()
        if normalized.startswith("失败阶段："):
            return normalized.split("：", 1)[1].strip() or None
        if normalized.lower().startswith("failed_stage:"):
            return normalized.split(":", 1)[1].strip() or None
    lower = text.lower()
    if "透视矩阵" in text or "calibration" in lower:
        return "calibrate"
    if "video file missing" in lower or "video file empty" in lower:
        return "video_file"
    return None


def _failure_reason(task: VideoTask) -> str:
    message = str(task.error_message or task.last_error or "未知错误").strip()
    stage = task.failed_stage or _extract_failed_stage(message)
    for line in message.splitlines():
        normalized = line.strip()
        if normalized.startswith("错误原因："):
            value = normalized.split("：", 1)[1].strip()
            if value and value not in {"（未提供）", "未提供", "--"}:
                return value
            return f"{stage or '算法处理'} 阶段失败，未提供具体错误，请查看任务日志。"
    return message or "未知错误"
