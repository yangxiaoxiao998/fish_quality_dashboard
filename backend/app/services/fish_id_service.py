"""
fish_id_service.py - 鱼唯一 ID 生成服务

对外只暴露一个函数：

    generate_fish_id(db, video_id) -> tuple[str, str]
        返回 (fish_id, fish_display_id)

新格式：
    fish_id          = f"{video_id}_F{seq:06d}"
                       例：20260613_0001_2daac2bc28a8_F000001
    fish_display_id  = f"F{seq:06d}"
                       例：F000001

逻辑：
    1. 查询 fish 表 video_id 列下，已存在的 {video_id}_F{seq:06d} 鱼。
    2. 找到最大 F 序号 N。
    3. 新序号 = N + 1。
    4. 如果插入时仍冲突（并发），重试最多 5 次。
    5. 旧的 "{video_id}_track_{track_id}" 鱼 ID 不参与新序号统计。

设计动机：
    旧的 fish_id 形如 20260613_0001_2daac2bc28a8_track_81365359390032，
    对客户界面太长。本服务只负责生成更短的"展示友好 ID"，
    track_id 仍保留在 Fish.track_id 字段，只供算法内部使用。
"""
from __future__ import annotations

import re
from typing import Tuple

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.repositories.models import Fish

# 匹配新格式 fish_id 的正则
_NEW_FISH_ID_PATTERN = re.compile(r"^(?P<video_id>.+)_F(?P<seq>\d{6})$")
# 匹配旧格式 fish_id（用于兼容 / 兜底）
_OLD_FISH_ID_PATTERN = re.compile(r"_track_\d+$")

# 默认起始序号（当 video_id 还没有任何鱼时使用）
_START_SEQ = 1
_MAX_RETRY = 5


def _parse_seq_from_fish_id(fish_id: str) -> int | None:
    """从新格式 fish_id 中解析出 seq 序号；非新格式返回 None。"""
    m = _NEW_FISH_ID_PATTERN.match(fish_id)
    if not m:
        return None
    try:
        return int(m.group("seq"))
    except (TypeError, ValueError):
        return None


def _current_max_seq(db: Session, video_id: str) -> int:
    """
    查询 video_id 对应的最大新格式 F 序号。

    - 旧 track_xxx 鱼 ID 不参与统计。
    - 没有匹配到任何新 ID 时返回 0。
    """
    pattern = f"{video_id}_F%"
    stmt = select(Fish.fish_id).where(Fish.fish_id.like(pattern))
    rows = db.execute(stmt).scalars().all()
    max_seq = 0
    for fid in rows:
        s = _parse_seq_from_fish_id(fid)
        if s is not None and s > max_seq:
            max_seq = s
    return max_seq


def _fallback_seq(db: Session, video_id: str) -> int:
    """
    当 video_id 还没有新格式鱼时，退化为该 video 下的总鱼数。
    这样既不会重复，也不会被旧 track_ 格式污染。
    """
    count = db.execute(
        select(func.count()).select_from(Fish).where(Fish.video_id == video_id)
    ).scalar() or 0
    return int(count)


def _seq_exists(db: Session, fish_id: str) -> bool:
    return db.get(Fish, fish_id) is not None


def _build_pair(video_id: str, seq: int) -> Tuple[str, str]:
    fish_id = f"{video_id}_F{seq:06d}"
    fish_display_id = f"F{seq:06d}"
    return fish_id, fish_display_id


def generate_fish_id(db: Session, video_id: str) -> Tuple[str, str]:
    """
    生成下一条鱼对应的 (fish_id, fish_display_id)。

    行为：
        - 严格自增：同 video 内 F{seq:06d} 单调递增。
        - 并发安全：发现 seq 已被占用时自动 +1 重试，最多重试 5 次。
        - 旧 track_xxx 鱼 ID 不影响新序号（被正则过滤）。
        - 若 video 完全为空：seq=1。

    返回：
        (fish_id, fish_display_id)
    """
    if not video_id:
        raise ValueError("video_id is required")

    base_seq = _current_max_seq(db, video_id)
    if base_seq == 0:
        base_seq = _fallback_seq(db, video_id)
        if base_seq == 0:
            base_seq = _START_SEQ - 1  # 下面 +1 起步 = START_SEQ

    seq = base_seq + 1
    for _ in range(_MAX_RETRY):
        fish_id, fish_display_id = _build_pair(video_id, seq)
        if not _seq_exists(db, fish_id):
            return fish_id, fish_display_id
        seq += 1

    raise RuntimeError(
        f"generate_fish_id failed after {_MAX_RETRY} retries for video_id={video_id}"
    )


__all__ = [
    "generate_fish_id",
    "derive_fish_display_id",
    "derive_video_display_id",
    "derive_fish_short_id",
    "_NEW_FISH_ID_PATTERN",
    "_OLD_FISH_ID_PATTERN",
]


def derive_fish_display_id(fish_id: str | None) -> str:
    """
    从完整 fish_id 解析出"客户友好"短编号 F{seq:06d}。

    支持：
        - 新格式 20260614_0006_966d42cc3bd2_F000524 -> F000524
        - 旧格式 20260613_xxx_track_182736451        -> track_182736451
        - None / 空字符串                            -> ""
    """
    if not fish_id:
        return ""
    m = _NEW_FISH_ID_PATTERN.match(fish_id)
    if m:
        return f"F{int(m.group('seq')):06d}"
    return fish_id


def derive_video_display_id(video_id: str | None) -> str:
    """
    客户友好的 video_id 短显示。

    新格式 video_id: YYYYMMDD_NNNN_xxxxxxxxxxxx
        20260614_0006_966d42cc3bd2 -> 20260614_0006
    旧格式 / 其他：原样返回。
    """
    if not video_id:
        return ""
    parts = video_id.split("_")
    if len(parts) >= 2 and len(parts[0]) == 8 and parts[0].isdigit() and len(parts[1]) == 4 and parts[1].isdigit():
        return f"{parts[0]}_{parts[1]}"
    return video_id


def derive_fish_short_id(fish_id: str | None) -> str:
    """
    客户友好的"鱼唯一 ID"短显示。

    新格式：20260614_0006_966d42cc3bd2_F000524 -> 20260614_0006_F000524
    旧格式：原样返回。
    """
    if not fish_id:
        return ""
    m = _NEW_FISH_ID_PATTERN.match(fish_id)
    if m:
        video_id = m.group("video_id")
        seq = int(m.group("seq"))
        return f"{derive_video_display_id(video_id)}_F{seq:06d}"
    return fish_id
