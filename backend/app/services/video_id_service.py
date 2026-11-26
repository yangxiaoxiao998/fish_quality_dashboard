"""
video_id_service.py - 视频 ID 生成服务

新 video_id 格式：

    YYYYMMDD_NNNN

    - YYYYMMDD：北京时间日期
    - NNNN：当天递增序号（0001 起）

兼容旧格式 YYYYMMDD_NNNN_xxxxxxxxxxxx；旧格式参与当天序号计算。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import VideoTask
from app.utils.time_utils import now_beijing


NEW_VIDEO_ID_PATTERN = re.compile(r"^(\d{8})_(\d{4})$")
LEGACY_VIDEO_ID_PATTERN = re.compile(r"^(\d{8})_(\d{4})_([0-9a-fA-F]{12})$")


def _is_new_format(video_id: str) -> bool:
    return bool(NEW_VIDEO_ID_PATTERN.fullmatch(video_id or "") or LEGACY_VIDEO_ID_PATTERN.fullmatch(video_id or ""))


def _parse_new_format(video_id: str) -> Optional[tuple[str, int]]:
    match = NEW_VIDEO_ID_PATTERN.fullmatch(video_id or "") or LEGACY_VIDEO_ID_PATTERN.fullmatch(video_id or "")
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _date_prefix(date: datetime) -> str:
    return date.strftime("%Y%m%d")


def generate_video_id(db: Session) -> str:
    today = _date_prefix(now_beijing())

    max_seq = 0
    stmt = select(VideoTask.video_id).where(VideoTask.video_id.like(f"{today}_%"))
    candidates = db.execute(stmt).scalars().all()
    for video_id in candidates:
        parsed = _parse_new_format(video_id or "")
        if parsed is not None and parsed[0] == today:
            max_seq = max(max_seq, parsed[1])

    candidate_seq = max_seq + 1
    for _ in range(10000):
        candidate = f"{today}_{candidate_seq:04d}"
        exists_stmt = select(VideoTask.video_id).where(VideoTask.video_id == candidate)
        if db.execute(exists_stmt).scalar_one_or_none() is None:
            return candidate
        candidate_seq += 1

    raise RuntimeError(f"无法为 {today} 生成未占用的 video_id")
