"""
check_database.py - 数据库诊断脚本

执行：

    python tools/check_database.py

输出：
    - DATABASE_URL (masked)
    - current_database
    - 各表记录数
    - 最新 fish / video_task / stats_5min 记录
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, func, inspect, text

from app.repositories.db import (
    engine,
    _current_database,
    _mask_url,
    REQUIRED_TABLES,
    SessionLocal,
)
from app.settings import settings
from app.repositories.models import Fish, VideoTask, Stats5Min


def _hr(title: str) -> None:
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)


def _kv(k: str, v) -> None:
    print(f"  {k}: {v}")


def main() -> int:
    _hr("Database connection")
    _kv("DATABASE_URL", _mask_url(settings.database_url))

    try:
        current_db = _current_database()
    except Exception as e:
        print(f"  current_database: <FAILED: {e}>")
        return 1
    _kv("current_database", current_db)

    insp = inspect(engine)
    try:
        tables = sorted(insp.get_table_names())
    except Exception as e:
        print(f"  tables: <FAILED: {e}>")
        return 1
    _kv("tables", tables)
    _kv("required_tables", list(REQUIRED_TABLES))
    _kv("required_tables_present", all(t in tables for t in REQUIRED_TABLES))

    db = SessionLocal()
    try:
        _hr("Row counts")
        fish_count = int(
            db.execute(select(func.count()).select_from(Fish)).scalar() or 0
        )
        vt_count = int(
            db.execute(select(func.count()).select_from(VideoTask)).scalar() or 0
        )
        st_count = int(
            db.execute(select(func.count()).select_from(Stats5Min)).scalar() or 0
        )
        _kv("fish", fish_count)
        _kv("video_task", vt_count)
        _kv("stats_5min", st_count)

        _hr("Latest fish")
        latest_fish = db.execute(
            select(Fish).order_by(Fish.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_fish is None:
            print("  (empty)")
        else:
            _kv("fish_id", latest_fish.fish_id)
            _kv("video_id", latest_fish.video_id)
            _kv("status", latest_fish.status)
            _kv("track_id", latest_fish.track_id)
            _kv("body_length", latest_fish.body_length)
            _kv("body_height", latest_fish.body_height)
            _kv("body_weight", latest_fish.body_weight)
            _kv("condition_factor", latest_fish.condition_factor)
            _kv("surface_damage", latest_fish.surface_damage)
            _kv("created_at", latest_fish.created_at)
            _kv("updated_at", latest_fish.updated_at)

        _hr("Latest video_task")
        latest_vt = db.execute(
            select(VideoTask).order_by(VideoTask.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_vt is None:
            print("  (empty)")
        else:
            _kv("video_id", latest_vt.video_id)
            _kv("source_type", latest_vt.source_type)
            _kv("source_path", latest_vt.source_path)
            _kv("status", latest_vt.status)
            _kv("total_fish", latest_vt.total_fish)
            _kv("processed_fish", latest_vt.processed_fish)
            _kv("current_fish_id", latest_vt.current_fish_id)
            _kv("current_algorithm", latest_vt.current_algorithm)
            _kv("last_error", latest_vt.last_error)
            _kv("created_at", latest_vt.created_at)
            _kv("started_at", latest_vt.started_at)
            _kv("finished_at", latest_vt.finished_at)

        _hr("Latest stats_5min")
        latest_st = db.execute(
            select(Stats5Min).order_by(Stats5Min.bucket_start.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_st is None:
            print("  (empty)")
        else:
            _kv("id", latest_st.id)
            _kv("video_id", latest_st.video_id)
            _kv("bucket_start", latest_st.bucket_start)
            _kv("bucket_end", latest_st.bucket_end)
            _kv("count", latest_st.count)
            _kv("sum_body_length", latest_st.sum_body_length)
            _kv("sum_body_weight", latest_st.sum_body_weight)
            _kv("abnormal_count", latest_st.abnormal_count)
            _kv("updated_at", latest_st.updated_at)

        _hr("Database ping")
        result = db.execute(text("SELECT version()")).scalar()
        _kv("postgres version", result)
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
