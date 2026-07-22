# 作用：管理统计信息的仓库操作
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.repositories.models import Stats5Min


def _bucket_range(ts: datetime) -> tuple[datetime, datetime]:
    """
    以 UTC 为准，5分钟分桶
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    minute = (ts.minute // 5) * 5
    bucket_start = ts.replace(minute=minute, second=0, microsecond=0)
    bucket_end = bucket_start + timedelta(minutes=5)
    return bucket_start, bucket_end


def upsert_stats_increment(
    db: Session,
    *,
    video_id: str,
    ts: datetime,
    body_length: float | None,
    body_height: float | None,
    body_weight: float | None,
    condition_factor: float | None,
    is_abnormal: bool,
) -> None:
    bucket_start, bucket_end = _bucket_range(ts)

    stmt = select(Stats5Min).where(
        Stats5Min.video_id == video_id,
        Stats5Min.bucket_start == bucket_start,
    )
    obj = db.execute(stmt).scalar_one_or_none()
    is_new = obj is None

    if obj is None:
        obj = Stats5Min(
            video_id=video_id,
            bucket_start=bucket_start,
            bucket_end=bucket_end,
            count=0,
            sum_body_length=0.0,
            sum_body_height=0.0,
            sum_body_weight=0.0,
            sum_condition_factor=0.0,
            abnormal_count=0,
        )
        db.add(obj)

    obj.count += 1
    obj.sum_body_length += float(body_length or 0.0)
    obj.sum_body_height += float(body_height or 0.0)
    obj.sum_body_weight += float(body_weight or 0.0)
    obj.sum_condition_factor += float(condition_factor or 0.0)
    obj.abnormal_count += 1 if is_abnormal else 0

    db.commit()

    print(
        f"[DB] stats updated: video_id={video_id!r} "
        f"bucket={bucket_start.isoformat()} count={obj.count} "
        f"abnormal_count={obj.abnormal_count} is_new={is_new} committed=True"
    )


def list_stats(
    db: Session,
    *,
    video_id: str,
    limit: int = 288,  # 24h * 12 buckets/h
) -> list[Stats5Min]:
    stmt = (
        select(Stats5Min)
        .where(Stats5Min.video_id == video_id)
        .order_by(Stats5Min.bucket_start.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())