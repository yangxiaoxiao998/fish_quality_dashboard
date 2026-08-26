# 作用：管理视频任务的仓库操作
# backend/app/repositories/session_repo.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.models import VideoTask
from app.services.path_service import to_data_relative
from app.utils.time_utils import now_beijing, beijing_timezone


def create_video_task(
    db: Session,
    *,
    video_id: str,
    source_type: str,
    source_path: str | None,
    rtsp_url: str | None,
) -> VideoTask:
    print(
        f"[DB] create video task: "
        f"video_id={video_id!r} source_type={source_type!r} "
        f"source_path={source_path!r} rtsp_url={rtsp_url!r}"
    )

    if source_path is not None:
        source_path = to_data_relative(source_path)

    existing_task = db.query(VideoTask).filter(VideoTask.video_id == video_id).first()

    if existing_task:
        # 如果存在，则更新其信息
        existing_task.source_type = source_type
        existing_task.source_path = source_path
        existing_task.rtsp_url = rtsp_url
        existing_task.status = "queued"  # 重置状态为排队
        existing_task.updated_at = now_beijing()  # 更新时间戳

        # 重置进度相关字段
        existing_task.total_fish = 0
        existing_task.processed_fish = 0
        existing_task.fps_estimate = None
        existing_task.started_at = None
        existing_task.finished_at = None
        existing_task.last_error = None

        db.commit()
        db.refresh(existing_task)  # 刷新以获取数据库最新的数据
        print(f"[DB] create video task: updated existing, task_id={video_id}")
        return existing_task
    else:
        # 如果不存在，则创建新记录
        obj = VideoTask(
            video_id=video_id,
            source_type=source_type,
            source_path=source_path,
            rtsp_url=rtsp_url,
            status="queued",
            total_fish=0,
            processed_fish=0,
            fps_estimate=None,
            started_at=None,
            finished_at=None,
            last_error=None,
            created_at=now_beijing(),  # 确保设置了创建时间
            updated_at=now_beijing(),  # 确保设置了更新时间
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        print(f"[DB] create video task: inserted, task_id={video_id}, status={obj.status}")
        return obj


def update_video_task_status(
    db: Session,
    *,
    video_id: str,
    status: str,
    last_error: str | None = None,
) -> None:
    obj = db.get(VideoTask, video_id)
    if obj is None:
        print(f"[DB] update video task status: task not found, video_id={video_id!r}")
        return

    obj.status = status
    if last_error is not None:
        obj.last_error = last_error

    now = now_beijing()
    if status == "processing":
        obj.started_at = obj.started_at or now
    # 终态：done / failed / stopped / no_fish_detected 都意味着任务结束
    if status in ("done", "failed", "stopped", "no_fish_detected"):
        obj.finished_at = now
        if obj.started_at:
            elapsed = (now - obj.started_at).total_seconds()
            if elapsed >= 0:
                obj.processing_duration_seconds = round(elapsed, 2)

    db.commit()
    print(
        f"[DB] update video task status: video_id={video_id!r} status={status!r} "
        f"total_fish={obj.total_fish} processed_fish={obj.processed_fish}"
    )


def update_video_task_source_path(
    db: Session,
    *,
    video_id: str,
    source_path: str,
) -> None:
    rel_path = to_data_relative(source_path)
    if rel_path is None:
        return
    obj = db.get(VideoTask, video_id)
    if obj is None:
        return
    obj.source_path = rel_path
    db.commit()


def update_video_task_duration(
    db: Session,
    *,
    video_id: str,
    video_duration_seconds: float | None,
) -> None:
    """Persist the measured media duration after recording/transcoding."""
    obj = db.get(VideoTask, video_id)
    if obj is None or video_duration_seconds is None or video_duration_seconds <= 0:
        return
    obj.video_duration_seconds = round(float(video_duration_seconds), 3)
    db.commit()


def delete_video_task(
    db: Session,
    *,
    video_id: str,
) -> bool:
    """
    删除一个 video_task（标定拍摄场景专用：标定过程不会在视频历史里留痕）。

    返回 True 表示有行被删除，False 表示没找到。
    """
    obj = db.get(VideoTask, video_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    print(f"[DB] delete video task: video_id={video_id!r}")
    return True


def update_video_task_aquaculture_bin(
    db: Session,
    *,
    video_id: str,
    aquaculture_bin: str | None,
) -> VideoTask | None:
    obj = db.get(VideoTask, video_id)
    if obj is None:
        return None
    value = (aquaculture_bin or "").strip()
    obj.aquaculture_bin = value[:255] or None
    db.commit()
    db.refresh(obj)
    return obj


def update_video_progress(
    db: Session,
    *,
    video_id: str,
    total_fish: int | None = None,
    processed_fish: int | None = None,
    current_fish_id: str | None = None,
    current_algorithm: str | None = None,
    fps_estimate: float | None = None,
) -> None:
    obj = db.get(VideoTask, video_id)
    if obj is None:
        return

    if total_fish is not None:
        obj.total_fish = total_fish
    if processed_fish is not None:
        obj.processed_fish = processed_fish
    if current_fish_id is not None:
        obj.current_fish_id = current_fish_id
    if current_algorithm is not None:
        obj.current_algorithm = current_algorithm
    if fps_estimate is not None:
        obj.fps_estimate = fps_estimate

    db.commit()


def get_video_task(db: Session, *, video_id: str) -> VideoTask | None:
    return db.get(VideoTask, video_id)


def update_video_task_recording_schedule(
    db: Session,
    *,
    video_id: str,
    record_duration_seconds: int | None,
    scheduled_stop_at: "datetime | None",
    auto_stop_triggered: bool = False,
) -> None:
    """写入定时录制字段。

    - ``record_duration_seconds``：用户选择的总时长（秒），NULL 表示未设置。
    - ``scheduled_stop_at``：基于 started_at + duration 推导出的计划停止时间。
    - ``auto_stop_triggered``：是否由 watchdog 触发（手动 stop 时为 False）。

    只更新传入字段，不动 status / started_at / finished_at。
    """
    obj = db.get(VideoTask, video_id)
    if obj is None:
        print(
            f"[DB] update video task recording schedule: task not found, "
            f"video_id={video_id!r}"
        )
        return

    obj.record_duration_seconds = record_duration_seconds
    obj.scheduled_stop_at = scheduled_stop_at
    obj.auto_stop_triggered = bool(auto_stop_triggered)

    db.commit()
    print(
        f"[DB] update video task recording schedule: "
        f"video_id={video_id!r} record_duration_seconds={record_duration_seconds} "
        f"scheduled_stop_at={scheduled_stop_at!r} auto_stop_triggered={auto_stop_triggered}"
    )


def update_video_task_stop_info(
    db: Session,
    *,
    video_id: str,
    actual_stop_at: "datetime | None",
    stop_reason: str | None,
    stop_source: str | None,
    auto_stop_triggered: bool = False,
) -> None:
    """写入实际停止时间 / 原因 / 来源。

    只更新传入字段；不动 status（由 update_video_task_status 单独维护）。
    """
    obj = db.get(VideoTask, video_id)
    if obj is None:
        print(
            f"[DB] update video task stop info: task not found, "
            f"video_id={video_id!r}"
        )
        return

    obj.actual_stop_at = actual_stop_at
    obj.stop_reason = stop_reason
    obj.stop_source = stop_source
    obj.auto_stop_triggered = bool(auto_stop_triggered)

    db.commit()
    print(
        f"[DB] update video task stop info: "
        f"video_id={video_id!r} actual_stop_at={actual_stop_at!r} "
        f"stop_reason={stop_reason!r} stop_source={stop_source!r} "
        f"auto_stop_triggered={auto_stop_triggered}"
    )


# ---------------------------------------------------------------------------
# 第三阶段新增：后端启动恢复 / 异常 recording 任务清理
# ---------------------------------------------------------------------------

UNFINISHED_CAMERA_RECORDING_STATUSES = ("recording", "running")


def list_unfinished_camera_recording_tasks(
    db: Session,
    limit: int = 20,
) -> list[VideoTask]:
    """查询残留的摄像头录制任务（status ∈ {recording, running} 且未写入 actual_stop_at）。

    - 仅做查询，不修改任何字段。
    - 用于后端启动恢复阶段。
    - 默认 limit=20，避免极端情况下扫到太多数据。
    """
    return (
        db.query(VideoTask)
        .filter(VideoTask.status.in_(UNFINISHED_CAMERA_RECORDING_STATUSES))
        .filter(VideoTask.actual_stop_at.is_(None))
        .order_by(VideoTask.created_at.desc())
        .limit(int(limit))
        .all()
    )


def mark_video_task_interrupted(
    db: Session,
    *,
    video_id: str,
    actual_stop_at: datetime,
    stop_reason: str,
    stop_source: str,
    auto_stop_triggered: bool,
    last_error: str | None = None,
) -> VideoTask | None:
    """把残留的 recording/running 任务标记为 interrupted。

    - 仅修改传入的 video_id 对应行；其它行不受影响。
    - 不动 queued / processing / done / failed / stopped 任务。
    - 内部不 commit；调用方按现有 session_repo 风格自行 commit。
    - last_error 长度限制 2000 字，避免异常长字段。
    """
    task = db.get(VideoTask, video_id)
    if task is None:
        print(
            f"[DB] mark video task interrupted: task not found, "
            f"video_id={video_id!r}"
        )
        return None

    if task.status not in UNFINISHED_CAMERA_RECORDING_STATUSES:
        # 已经被其它流程改完，不要覆盖
        print(
            f"[DB] mark video task interrupted: skip, current status="
            f"{task.status!r} video_id={video_id!r}"
        )
        return None

    task.status = "interrupted"
    task.finished_at = actual_stop_at
    task.actual_stop_at = actual_stop_at
    task.stop_reason = stop_reason
    task.stop_source = stop_source
    task.auto_stop_triggered = bool(auto_stop_triggered)
    if last_error is not None:
        task.last_error = (last_error or "")[:2000]
    task.updated_at = actual_stop_at

    print(
        f"[DB] mark video task interrupted: video_id={video_id!r} "
        f"stop_reason={stop_reason!r} stop_source={stop_source!r} "
        f"auto_stop_triggered={auto_stop_triggered}"
    )
    return task
