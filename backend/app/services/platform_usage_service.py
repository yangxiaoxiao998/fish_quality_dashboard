from __future__ import annotations

from datetime import timedelta
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import VideoTask
from app.utils.time_utils import now_beijing
from app.services.path_service import to_abs_data_path


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


def _probe_duration(path: str | None) -> float | None:
    if not path:
        return None
    resolved = to_abs_data_path(path)
    if resolved is None or not resolved.exists():
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(resolved)],
            capture_output=True, text=True, timeout=20, check=False,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def get_platform_usage_summary(db: Session, range_value: str = "7d") -> dict:
    start = _range_start(range_value)

    stmt = select(VideoTask)
    if start is not None:
        stmt = stmt.where(VideoTask.created_at >= start)
    tasks = list(db.execute(stmt).scalars().all())

    total = len(tasks)
    upload_count = len([t for t in tasks if t.source_type == "upload"])
    camera_count = len([t for t in tasks if t.source_type in ("rtsp", "camera")])
    done_count = len([t for t in tasks if t.status == "done"])
    failed_count = len([t for t in tasks if t.status == "failed"])
    no_fish_count = len([t for t in tasks if t.status == "no_fish_detected"])

    total_fish = sum(int(t.total_fish or 0) for t in tasks)
    avg_fish = round(total_fish / total, 2) if total else 0

    durations = []
    fps_values = []
    for t in tasks:
        if t.processing_duration_seconds is not None:
            durations.append(float(t.processing_duration_seconds))
        elif t.started_at and t.finished_at:
            durations.append((t.finished_at - t.started_at).total_seconds())
        if t.fps_estimate:
            fps_values.append(float(t.fps_estimate))

    avg_processing = round(sum(durations) / len(durations), 2) if durations else 0
    avg_fps = round(sum(fps_values) / len(fps_values), 2) if fps_values else 0

    total_duration = 0.0
    for task in tasks:
        value = task.video_duration_seconds
        if value is None:
            value = _probe_duration(task.source_path)
        if value is not None and value > 0:
            total_duration += float(value)
    total_video_duration = round(total_duration, 2)

    return {
        "total_video_tasks": total,
        "upload_video_count": upload_count,
        "camera_video_count": camera_count,
        "done_task_count": done_count,
        "failed_task_count": failed_count,
        "no_fish_task_count": no_fish_count,
        "success_rate": round(done_count / total, 4) if total else 0,
        "failure_rate": round(failed_count / total, 4) if total else 0,
        "total_fish": total_fish,
        "avg_fish_per_video": avg_fish,
        "total_video_duration": total_video_duration,
        "avg_processing_duration": avg_processing,
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
    source_map: dict[str, int] = {}
    daily_task: dict[str, int] = {}
    daily_fish: dict[str, int] = {}
    daily_failed: dict[str, int] = {}
    daily_duration_sum: dict[str, float] = {}
    daily_duration_count: dict[str, int] = {}

    for t in tasks:
        day = t.created_at.strftime("%Y-%m-%d") if t.created_at else "unknown"
        daily_task[day] = daily_task.get(day, 0) + 1
        daily_fish[day] = daily_fish.get(day, 0) + int(t.total_fish or 0)
        if t.status == "failed":
            daily_failed[day] = daily_failed.get(day, 0) + 1

        duration_seconds = t.processing_duration_seconds
        if duration_seconds is None and t.started_at and t.finished_at:
            duration_seconds = (t.finished_at - t.started_at).total_seconds()
        if duration_seconds is not None:
            if duration_seconds >= 0:
                daily_duration_sum[day] = daily_duration_sum.get(day, 0.0) + duration_seconds
                daily_duration_count[day] = daily_duration_count.get(day, 0) + 1

        status_map[t.status or "unknown"] = status_map.get(t.status or "unknown", 0) + 1
        source_map[t.source_type or "unknown"] = source_map.get(t.source_type or "unknown", 0) + 1

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
        "source_type_distribution": [
            {"label": k, "count": v} for k, v in source_map.items()
        ],
        "task_status_distribution": [
            {"label": k, "count": v} for k, v in status_map.items()
        ],
        "daily_failed_task_trend": [
            {"date": k, "count": v} for k, v in sorted(daily_failed.items())
        ],
        "avg_processing_duration_trend": avg_processing_duration_trend,
    }


def get_platform_failures(db: Session, range_value: str = "7d") -> dict:
    start = _range_start(range_value)
    stmt = select(VideoTask).where(VideoTask.status == "failed")
    if start is not None:
        stmt = stmt.where(VideoTask.created_at >= start)
    tasks = list(db.execute(stmt).scalars().all())

    reason_map: dict[str, int] = {}
    for t in tasks:
        key = (t.last_error or "未知错误")[:120]
        reason_map[key] = reason_map.get(key, 0) + 1

    return {
        "items": [
            {"reason": k, "count": v}
            for k, v in sorted(reason_map.items(), key=lambda x: x[1], reverse=True)
        ]
    }
