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
    """Reject unsafe realtime increments until an event identity is defined.

    The formal pipeline rebuilds buckets from one authoritative fish row per
    ``video_id`` after import.  A frame-level increment has no idempotency key,
    so accepting it would count retries and repeated frames as new fish.
    """
    raise RuntimeError(
        "realtime stats increments are disabled; rebuild stats_5min after "
        "algorithm import or add an idempotent fish event key first"
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
