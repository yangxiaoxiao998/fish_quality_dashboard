"""Repository for CameraRecordSchedule.

封装所有 SQL。所有时间字段都是 timezone-aware datetime。
调度器是唯一的 claim / 状态机写入口。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.repositories.models import CameraRecordSchedule

logger = logging.getLogger("camera_schedule_repo")


# 与 models.py 的 status 枚举保持一致
SCHEDULE_PENDING = "pending"
SCHEDULE_CLAIMING = "claiming"
SCHEDULE_STARTING = "starting"
SCHEDULE_RECORDING = "recording"
SCHEDULE_COMPLETED = "completed"
SCHEDULE_FAILED = "failed"
SCHEDULE_CANCELLED = "cancelled"
SCHEDULE_SKIPPED = "skipped"

ACTIVE_SCHEDULE_STATUSES = (
    SCHEDULE_PENDING,
    SCHEDULE_CLAIMING,
    SCHEDULE_STARTING,
    SCHEDULE_RECORDING,
)

ALL_SCHEDULE_STATUSES = (
    SCHEDULE_PENDING,
    SCHEDULE_CLAIMING,
    SCHEDULE_STARTING,
    SCHEDULE_RECORDING,
    SCHEDULE_COMPLETED,
    SCHEDULE_FAILED,
    SCHEDULE_CANCELLED,
    SCHEDULE_SKIPPED,
)

MIN_SCHEDULE_LEAD_SECONDS = 5
MAX_SCHEDULE_DURATION_MINUTES = 1440
MIN_SCHEDULE_DURATION_MINUTES = 1
MAX_SCHEDULE_TASKS_PER_REQUEST = 20


def make_schedule_id() -> str:
    """与 models / docstring 中约定的 ID 格式。"""
    return f"schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def make_claim_token() -> str:
    """同一计划仅允许一个调度循环领取。"""
    return uuid.uuid4().hex


def list_schedules(
    db: Session,
    *,
    statuses: Optional[Iterable[str]] = None,
    include_terminal: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[CameraRecordSchedule], int]:
    """统一入口：statuses=None 时返回所有；否则按 statuses 过滤。"""
    stmt = select(CameraRecordSchedule)
    if statuses is not None:
        status_list = list(statuses)
        if not status_list:
            return [], 0
        stmt = stmt.where(CameraRecordSchedule.status.in_(status_list))
    elif not include_terminal:
        stmt = stmt.where(
            CameraRecordSchedule.status.in_(ACTIVE_SCHEDULE_STATUSES)
        )

    count_stmt = select(func.count()).select_from(CameraRecordSchedule)
    if stmt.whereclause is not None:
        count_stmt = count_stmt.where(stmt.whereclause)
    total = int(db.execute(count_stmt).scalar() or 0)

    rows = db.execute(
        stmt.order_by(
            CameraRecordSchedule.planned_start_at.asc(),
            CameraRecordSchedule.created_at.asc(),
        ).limit(limit).offset(offset)
    ).scalars().all()
    return list(rows), total


def get_schedule(db: Session, schedule_id: str) -> Optional[CameraRecordSchedule]:
    return db.get(CameraRecordSchedule, schedule_id)


def create_schedules(
    db: Session,
    tasks: list[dict[str, Any]],
) -> list[CameraRecordSchedule]:
    """单事务批量创建。任一非法时整批回滚。"""
    if not tasks:
        raise ValueError("tasks 不能为空")
    if len(tasks) > MAX_SCHEDULE_TASKS_PER_REQUEST:
        raise ValueError(
            f"单次最多 {MAX_SCHEDULE_TASKS_PER_REQUEST} 条，当前 "
            f"{len(tasks)}"
        )

    now = datetime.now(__import__("datetime").timezone.utc)
    rows: list[CameraRecordSchedule] = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise ValueError(f"tasks[{i}] 必须是对象")

        start_at = t.get("planned_start_at")
        if start_at is None:
            start_at = t.get("start_time")
        if not isinstance(start_at, datetime):
            raise ValueError(f"tasks[{i}].start_time 必须是 ISO 时间")
        if start_at.tzinfo is None:
            raise ValueError(f"tasks[{i}].start_time 必须包含时区")
        duration = t.get("duration_minutes")
        if not isinstance(duration, int):
            end_at_raw = t.get("planned_end_at") or t.get("end_time")
            if isinstance(end_at_raw, datetime):
                if end_at_raw.tzinfo is None:
                    raise ValueError(f"tasks[{i}].end_time 必须包含时区")
                duration = int((end_at_raw - start_at).total_seconds() // 60)
            else:
                raise ValueError(f"tasks[{i}].duration_minutes 必须是整数")
        if (
            duration < MIN_SCHEDULE_DURATION_MINUTES
            or duration > MAX_SCHEDULE_DURATION_MINUTES
        ):
            raise ValueError(
                f"tasks[{i}].duration_minutes 必须在 "
                f"{MIN_SCHEDULE_DURATION_MINUTES}~{MAX_SCHEDULE_DURATION_MINUTES} 之间"
            )
        if (start_at - now).total_seconds() < MIN_SCHEDULE_LEAD_SECONDS:
            raise ValueError(
                f"计划开始时间必须晚于当前时间至少 {MIN_SCHEDULE_LEAD_SECONDS} 秒"
            )

        end_at = t.get("planned_end_at") or t.get("end_time")
        if not isinstance(end_at, datetime):
            end_at = start_at + __import__("datetime").timedelta(
                minutes=duration
            )
        rows.append(
            CameraRecordSchedule(
                schedule_id=make_schedule_id(),
                status=SCHEDULE_PENDING,
                planned_start_at=start_at,
                planned_end_at=end_at,
                duration_minutes=duration,
                video_id=None,
            )
        )

    for r in rows:
        db.add(r)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def find_active_overlaps(
    db: Session,
    *,
    planned_start_at: datetime,
    planned_end_at: datetime,
    exclude_schedule_id: Optional[str] = None,
) -> list[CameraRecordSchedule]:
    """查找与给定时间窗冲突的 active 计划。

    重叠：new_start < existing_end and new_end > existing_start
    边界接触（new_start == existing_end 或反之）不冲突。
    """
    stmt = select(CameraRecordSchedule).where(
        CameraRecordSchedule.status.in_(ACTIVE_SCHEDULE_STATUSES),
        CameraRecordSchedule.planned_start_at < planned_end_at,
        CameraRecordSchedule.planned_end_at > planned_start_at,
    )
    if exclude_schedule_id is not None:
        stmt = stmt.where(
            CameraRecordSchedule.schedule_id != exclude_schedule_id
        )
    return list(db.execute(stmt).scalars().all())


def find_due_pending(
    db: Session,
    *,
    now: datetime,
    limit: int = 32,
) -> list[CameraRecordSchedule]:
    return list(
        db.execute(
            select(CameraRecordSchedule)
            .where(
                CameraRecordSchedule.status == SCHEDULE_PENDING,
                CameraRecordSchedule.planned_start_at <= now,
            )
            .order_by(CameraRecordSchedule.planned_start_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def claim_next_due_schedule(
    db: Session,
    *,
    schedule_id: str,
    claim_token: str,
) -> Optional[CameraRecordSchedule]:
    """原子领取：仅当 status=pending 时才能更新为 claiming。"""
    result = db.execute(
        update(CameraRecordSchedule)
        .where(
            CameraRecordSchedule.schedule_id == schedule_id,
            CameraRecordSchedule.status == SCHEDULE_PENDING,
        )
        .values(
            status=SCHEDULE_CLAIMING,
            claimed_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            claim_token=claim_token,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def reset_claiming_schedule(db: Session, *, schedule_id: str) -> Optional[CameraRecordSchedule]:
    result = db.execute(
        update(CameraRecordSchedule)
        .where(
            CameraRecordSchedule.schedule_id == schedule_id,
            CameraRecordSchedule.status == SCHEDULE_CLAIMING,
        )
        .values(status=SCHEDULE_PENDING, claimed_at=None, claim_token=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def mark_schedule_starting(
    db: Session,
    *,
    schedule_id: str,
    video_id: str,
) -> Optional[CameraRecordSchedule]:
    """start_stream 已返回 video_id，更新为 starting。"""
    result = db.execute(
        update(CameraRecordSchedule)
        .where(CameraRecordSchedule.schedule_id == schedule_id)
        .values(
            status=SCHEDULE_STARTING,
            video_id=video_id,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def mark_schedule_recording(
    db: Session,
    *,
    schedule_id: str,
    actual_start_at: Optional[datetime] = None,
) -> Optional[CameraRecordSchedule]:
    """实际首帧写入后更新为 recording。"""
    values: dict[str, Any] = {"status": SCHEDULE_RECORDING}
    if actual_start_at is not None:
        values["actual_start_at"] = actual_start_at
    result = db.execute(
        update(CameraRecordSchedule)
        .where(CameraRecordSchedule.schedule_id == schedule_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def mark_schedule_completed(
    db: Session,
    *,
    schedule_id: str,
    actual_stop_at: Optional[datetime] = None,
    stop_reason: Optional[str] = None,
    stop_source: Optional[str] = None,
) -> Optional[CameraRecordSchedule]:
    """stop_stream 正常完成后调用。"""
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    values: dict[str, Any] = {
        "status": SCHEDULE_COMPLETED,
        "actual_stop_at": actual_stop_at or now,
    }
    if stop_reason is not None:
        values["stop_reason"] = stop_reason
    if stop_source is not None:
        values["stop_source"] = stop_source
    result = db.execute(
        update(CameraRecordSchedule)
        .where(CameraRecordSchedule.schedule_id == schedule_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def mark_schedule_failed(
    db: Session,
    *,
    schedule_id: str,
    error_message: str,
    stop_reason: Optional[str] = None,
    stop_source: Optional[str] = None,
) -> Optional[CameraRecordSchedule]:
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    values: dict[str, Any] = {
        "status": SCHEDULE_FAILED,
        "error_message": error_message,
        "actual_stop_at": now,
    }
    if stop_reason is not None:
        values["stop_reason"] = stop_reason
    if stop_source is not None:
        values["stop_source"] = stop_source
    result = db.execute(
        update(CameraRecordSchedule)
        .where(CameraRecordSchedule.schedule_id == schedule_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def mark_schedule_skipped(
    db: Session,
    *,
    schedule_id: str,
    error_message: str,
) -> Optional[CameraRecordSchedule]:
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    result = db.execute(
        update(CameraRecordSchedule)
        .where(CameraRecordSchedule.schedule_id == schedule_id)
        .values(
            status=SCHEDULE_SKIPPED,
            error_message=error_message,
            actual_stop_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def cancel_pending_schedule(
    db: Session,
    *,
    schedule_id: str,
) -> Optional[CameraRecordSchedule]:
    """仅允许取消 pending 任务。"""
    result = db.execute(
        update(CameraRecordSchedule)
        .where(
            CameraRecordSchedule.schedule_id == schedule_id,
            CameraRecordSchedule.status == SCHEDULE_PENDING,
        )
        .values(
            status=SCHEDULE_CANCELLED,
            cancelled_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_schedule(db, schedule_id)


def recover_stale_schedules(
    db: Session,
    *,
    now: datetime,
    misfire_grace_seconds: int = 300,
) -> int:
    """后端启动时调用。返回被修改的记录数。

    - pending 已过期：
      * 过期 <= misfire_grace_seconds → 保持 pending，可补执行
      * 过期 > misfire_grace_seconds → skipped
    - claiming / starting / recording 残留：恢复为 pending，让调度器重新执行；
      已经过计划结束时间的记录标记 completed，关联视频由队列恢复逻辑继续处理。
    """
    from sqlalchemy import or_

    affected = 0

    # pending 过期
    expired_pending = db.execute(
        select(CameraRecordSchedule).where(
            CameraRecordSchedule.status == SCHEDULE_PENDING,
            CameraRecordSchedule.planned_start_at < now,
        )
    ).scalars().all()
    for r in expired_pending:
        delay = (now - r.planned_start_at).total_seconds()
        if delay <= misfire_grace_seconds:
            continue
        db.execute(
            update(CameraRecordSchedule)
            .where(CameraRecordSchedule.schedule_id == r.schedule_id)
            .values(
                status=SCHEDULE_SKIPPED,
                error_message="missed_while_backend_offline",
                actual_stop_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        affected += 1

    # claiming / starting / recording 残留。重启后不能假设摄像头线程仍存在，
    # 但不能把用户任务永久标记失败；交给调度器重新执行。
    stale = db.execute(
        select(CameraRecordSchedule).where(
            CameraRecordSchedule.status.in_(
                (
                    SCHEDULE_CLAIMING,
                    SCHEDULE_STARTING,
                    SCHEDULE_RECORDING,
                )
            )
        )
    ).scalars().all()
    for r in stale:
        if r.planned_end_at and r.planned_end_at <= now:
            next_status = SCHEDULE_COMPLETED
            error_message = None
        else:
            next_status = SCHEDULE_PENDING
            error_message = "backend_restart_recovered_schedule"
        db.execute(
            update(CameraRecordSchedule)
            .where(CameraRecordSchedule.schedule_id == r.schedule_id)
            .values(
                status=next_status,
                error_message=error_message,
                actual_stop_at=now if next_status == SCHEDULE_COMPLETED else None,
                claim_token=None,
            )
            .execution_options(synchronize_session=False)
        )
        affected += 1

    if affected:
        db.commit()
    return affected
