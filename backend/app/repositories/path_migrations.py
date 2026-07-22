"""
path_migrations.py - 历史绝对路径 -> 相对路径 数据迁移

启动时调用一次，安全可重复执行：

    遍历 VideoTask.source_path / Fish.original_image_path / Fish.corrected_image_path：

    1. None                 -> 跳过
    2. 已经是相对路径       -> 跳过
    3. DATA_DIR 下绝对路径  -> 转为相对路径
    4. 不属于 DATA_DIR      -> 置 None（并打印 warning）
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.repositories.db import SessionLocal
from app.repositories.models import Fish, VideoTask
from app.services.path_service import to_data_relative

logger = logging.getLogger("path_migration")


def _p_normalized(p: str) -> str:
    return Path(p).as_posix().lstrip("/")


def _migrate_video_tasks(db: Session) -> int:
    updated = 0
    tasks = db.query(VideoTask).all()
    for task in tasks:
        if task.source_path is None:
            continue
        p_str = str(task.source_path)
        if not p_str.startswith("/"):
            continue
        rel = to_data_relative(p_str)
        if rel is None:
            logger.warning(
                f"[path_migration] VideoTask {task.video_id} source_path 不属于 DATA_DIR，置 None: {p_str}"
            )
            task.source_path = None
        else:
            logger.info(
                f"[path_migration] VideoTask {task.video_id} source_path: {p_str} -> {rel}"
            )
            task.source_path = rel
            updated += 1
    if updated:
        db.commit()
    return updated


def _migrate_fish_field(db: Session, field_attr: str) -> int:
    updated = 0
    fishes = db.query(Fish).all()
    for f in fishes:
        val = getattr(f, field_attr)
        if val is None:
            continue
        p_str = str(val)
        if not p_str.startswith("/"):
            continue
        rel = to_data_relative(p_str)
        if rel is None:
            logger.warning(
                f"[path_migration] Fish {f.fish_id} {field_attr} 不属于 DATA_DIR，置 None: {p_str}"
            )
            setattr(f, field_attr, None)
        else:
            logger.info(
                f"[path_migration] Fish {f.fish_id} {field_attr}: {p_str} -> {rel}"
            )
            setattr(f, field_attr, rel)
            updated += 1
    if updated:
        db.commit()
    return updated


def migrate_paths_to_relative() -> None:
    """
    将历史数据库中的绝对路径转换为相对于 DATA_DIR 的相对路径。

    多次运行不会破坏数据：
        - 相对路径   -> 跳过（不以 / 开头）
    """
    db = SessionLocal()
    try:
        v_count = _migrate_video_tasks(db)
        o_count = _migrate_fish_field(db, "original_image_path")
        c_count = _migrate_fish_field(db, "corrected_image_path")
        logger.info(
            f"[path_migration] 完成: VideoTask={v_count}, "
            f"Fish.original={o_count}, Fish.corrected={c_count}"
        )
    except Exception as e:
        logger.error(f"[path_migration] 迁移失败: {e}")
        db.rollback()
    finally:
        db.close()
