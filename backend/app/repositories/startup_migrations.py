"""
Startup migrations: ensure fish schema is up-to-date.
Safe to run every startup - skips if already correct.

第一阶段新增：
- run_algorithm_result_tables_migration()
  兼容式为 video_task / fish 补充算法最新字段。
  使用 ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... 幂等执行。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.repositories.db import engine

logger = logging.getLogger("migration")


def run_track_id_migration() -> None:
    """
    Check and alter fish.track_id to BIGINT if needed.
    PostgreSQL-specific: checks pg_catalog for column type.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT atttypid::regtype
                    FROM pg_attribute
                    JOIN pg_class ON pg_class.oid = pg_attribute.attrelid
                    JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                    WHERE pg_class.relname = 'fish'
                      AND pg_namespace.nspname = 'public'
                      AND pg_attribute.attname = 'track_id'
                """)
            )
            row = result.fetchone()
            if not row:
                logger.info("[migration] track_id column not found, skipping")
                return

            current_type = str(row[0]).lower()
            if current_type == "bigint":
                logger.info("[migration] track_id is already BIGINT, skipping")
                return

            logger.warning(
                f"[migration] track_id is {current_type}, altering to BIGINT"
            )
            conn.execute(text("ALTER TABLE fish ALTER COLUMN track_id TYPE BIGINT"))
            conn.commit()
            logger.info("[migration] track_id changed to BIGINT successfully")
    except Exception as e:
        logger.error(f"[migration] Failed to migrate track_id: {e}")


def run_frame_detection_tables_migration() -> None:
    from sqlalchemy import inspect, text
    from app.repositories.db import engine, Base
    from app.repositories import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("fish")}

    with engine.begin() as conn:
        if "mask_image_path" not in columns:
            conn.execute(text("ALTER TABLE fish ADD COLUMN mask_image_path TEXT"))

        if "surface_damage_ratio" not in columns:
            conn.execute(text("ALTER TABLE fish ADD COLUMN surface_damage_ratio DOUBLE PRECISION"))


def run_body_length_without_tail_migration() -> None:
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT 1
                    FROM pg_attribute
                    JOIN pg_class ON pg_class.oid = pg_attribute.attrelid
                    JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                    WHERE pg_class.relname = 'fish'
                      AND pg_namespace.nspname = 'public'
                      AND pg_attribute.attname = 'body_length_without_tail'
                """)
            )
            if result.fetchone() is not None:
                logger.info(
                    "[migration] body_length_without_tail already exists, skipping"
                )
                return

            logger.warning(
                "[migration] body_length_without_tail missing, adding column"
            )
            conn.execute(
                text(
                    "ALTER TABLE fish "
                    "ADD COLUMN body_length_without_tail DOUBLE PRECISION"
                )
            )
            conn.commit()
            logger.info(
                "[migration] body_length_without_tail added successfully"
            )
    except Exception as e:
        logger.error(
            f"[migration] Failed to migrate body_length_without_tail: {e}"
        )


def run_report_tables_migration() -> None:
    try:
        from app.repositories.db import Base
        from app.repositories import models  # noqa: F401

        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS duration_ms INTEGER"))
            conn.execute(text("ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS operator VARCHAR(64)"))
        logger.info("[migration] report tables checked (detection_report, operation_log)")
    except Exception as e:
        logger.error(f"[migration] report tables migration failed: {e}")


def run_detection_report_conclusion_migration() -> None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_attribute
                    JOIN pg_class ON pg_class.oid = pg_attribute.attrelid
                    JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                    WHERE pg_class.relname = 'detection_report'
                      AND pg_namespace.nspname = 'public'
                      AND pg_attribute.attname = 'daily_conclusion'
                    """
                )
            ).fetchone()
            if row is not None:
                logger.info(
                    "[migration] detection_report.daily_conclusion already exists, skipping"
                )
                return

            logger.warning(
                "[migration] detection_report.daily_conclusion missing, adding column"
            )
            conn.execute(
                text(
                    "ALTER TABLE detection_report "
                    "ADD COLUMN daily_conclusion TEXT"
                )
            )
            conn.commit()
            logger.info(
                "[migration] detection_report.daily_conclusion added successfully"
            )
    except Exception as e:
        logger.error(
            f"[migration] Failed to migrate detection_report.daily_conclusion: {e}"
        )


def run_fish_extra_metric_migration() -> None:
    columns = {
        "mask_image_path": "TEXT",
        "wound_mask_path": "TEXT",
        "surface_damage_ratio": "DOUBLE PRECISION",
    }

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                select column_name
                from information_schema.columns
                where table_name = 'fish'
                """
            )
        ).fetchall()

        existing = {str(row[0]) for row in rows}

        for col, ddl_type in columns.items():
            if col in existing:
                print(f"[DB] fish.{col} already exists")
                continue

            conn.execute(
                text(f"alter table fish add column {col} {ddl_type}")
            )
            print(f"[DB] added fish.{col}")


# ---------------------------------------------------------------------------
# 第一阶段新增：算法最新字段迁移
# ---------------------------------------------------------------------------

VIDEO_TASK_ALGO_FIELDS: dict[str, str] = {
    "original_input_video": "TEXT",
    "input_video": "TEXT",
    "failed_stage": "TEXT",
    "error_message": "TEXT",
    "result_dir": "TEXT",
    "pipeline_result_path": "TEXT",
    "error_conclusion_path": "TEXT",
    "grounded_video_path": "TEXT",
    "measurement_csv_path": "TEXT",
    "complete_match_dir": "TEXT",
    "incomplete_match_dir": "TEXT",
    "complete_fish": "INTEGER DEFAULT 0",
    "incomplete_fish": "INTEGER DEFAULT 0",
    "failed_fish": "INTEGER DEFAULT 0",
    "damaged_fish": "INTEGER DEFAULT 0",
    "avg_surface_damage_ratio": "DOUBLE PRECISION",
    "max_surface_damage_ratio": "DOUBLE PRECISION",
}

FISH_ALGO_FIELDS: dict[str, str] = {
    "fish_uid": "TEXT",
    "frame_index": "INTEGER",
    "crop_image_path": "TEXT",
    "body_length_cm": "DOUBLE PRECISION",
    "body_length_no_tail_cm": "DOUBLE PRECISION",
    "body_height_cm": "DOUBLE PRECISION",
    "pred_weight_g": "DOUBLE PRECISION",
    "surface_damage_ratio": "DOUBLE PRECISION",
    "error_message": "TEXT",
}

FISH_ALGO_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_fish_video_track ON fish(video_id, track_id)",
    "CREATE INDEX IF NOT EXISTS idx_fish_video_uid ON fish(video_id, fish_uid)",
    "CREATE INDEX IF NOT EXISTS idx_fish_video_status ON fish(video_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_fish_video_damage ON fish(video_id, surface_damage_ratio)",
]

VIDEO_TASK_ALGO_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_video_task_status_created ON video_task(status, created_at)",
]


def run_algorithm_result_tables_migration() -> None:
    """
    算法最新字段 + 索引的幂等迁移。

    - 使用 ALTER TABLE ADD COLUMN IF NOT EXISTS（PostgreSQL 9.6+）。
    - CREATE INDEX IF NOT EXISTS。
    - 字段已存在则跳过，不影响存量数据。
    """
    try:
        with engine.begin() as conn:
            for col, ddl in VIDEO_TASK_ALGO_FIELDS.items():
                sql = f"ALTER TABLE video_task ADD COLUMN IF NOT EXISTS {col} {ddl}"
                conn.execute(text(sql))
                logger.info(
                    f"[migration] video_task.{col} ensured ({ddl})"
                )

            for col, ddl in FISH_ALGO_FIELDS.items():
                sql = f"ALTER TABLE fish ADD COLUMN IF NOT EXISTS {col} {ddl}"
                conn.execute(text(sql))
                logger.info(
                    f"[migration] fish.{col} ensured ({ddl})"
                )

            for sql in FISH_ALGO_INDEXES:
                conn.execute(text(sql))
                logger.info(f"[migration] ensured fish index: {sql}")

            for sql in VIDEO_TASK_ALGO_INDEXES:
                conn.execute(text(sql))
                logger.info(f"[migration] ensured video_task index: {sql}")

        print("[DB] algorithm result tables migration: OK")
    except Exception as e:
        # 不让启动失败；只记录日志
        logger.error(f"[migration] algorithm result tables migration failed: {e}")
        print(f"[DB][WARN] algorithm result tables migration failed: {e}")


# ---------------------------------------------------------------------------
# 第一阶段新增：定时录制字段迁移
# ---------------------------------------------------------------------------

VIDEO_TASK_TIMED_RECORDING_FIELDS: dict[str, str] = {
    "record_duration_seconds": "INTEGER",
    "scheduled_stop_at": "TIMESTAMP WITH TIME ZONE",
    "actual_stop_at": "TIMESTAMP WITH TIME ZONE",
    "stop_reason": "TEXT",
    "stop_source": "TEXT",
    "auto_stop_triggered": "BOOLEAN DEFAULT FALSE",
}

VIDEO_TASK_TIMED_RECORDING_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_video_task_scheduled_stop "
    "ON video_task(status, scheduled_stop_at)",
]


def run_video_task_timed_recording_migration() -> None:
    """
    定时录制字段 + 索引的幂等迁移。

    - 使用 ALTER TABLE ADD COLUMN IF NOT EXISTS（PostgreSQL 9.6+）。
    - 字段已存在则跳过，不影响存量数据。
    - 启动失败不会因为该迁移异常而中断后端启动。
    """
    try:
        with engine.begin() as conn:
            for col, ddl in VIDEO_TASK_TIMED_RECORDING_FIELDS.items():
                sql = f"ALTER TABLE video_task ADD COLUMN IF NOT EXISTS {col} {ddl}"
                conn.execute(text(sql))
                logger.info(
                    f"[migration] video_task.{col} ensured ({ddl})"
                )

            for sql in VIDEO_TASK_TIMED_RECORDING_INDEXES:
                conn.execute(text(sql))
                logger.info(
                    f"[migration] ensured timed recording index: {sql}"
                )

        print("[DB] video_task timed recording migration: OK")
    except Exception as e:
        # 不让启动失败；只记录日志
        logger.error(
            f"[migration] video_task timed recording migration failed: {e}"
        )
        print(f"[DB][WARN] video_task timed recording migration failed: {e}")


# ---------------------------------------------------------------------------
# 第五阶段新增：detection_report.video_id 字段迁移（视频报表支持）
# ---------------------------------------------------------------------------

DETECTION_REPORT_VIDEO_REPORT_FIELDS: dict[str, str] = {
    # video_id=NULL → 日报；video_id=<具体 id> → 视频报表
    "video_id": "VARCHAR(128)",
}

DETECTION_REPORT_VIDEO_REPORT_INDEXES: list[str] = [
    # 单列索引：加速按 video_id 查某视频的视频报表
    "CREATE INDEX IF NOT EXISTS idx_detection_report_video_id "
    "ON detection_report(video_id)",
    # 联合索引：覆盖"同 report_type 下的 video_id 唯一性"应用层查询
    "CREATE INDEX IF NOT EXISTS idx_detection_report_type_video "
    "ON detection_report(report_type, video_id)",
]


def run_detection_report_video_report_migration() -> None:
    """
    detection_report.video_id 字段 + 索引的幂等迁移。

    - 支持视频报表 report_type='video_fish_quality' + video_id=<具体 id>。
    - 同一 video_id 重复生成时由应用层覆盖逻辑保证唯一 video_fish_quality 记录。
    - 启动失败不会因为该迁移异常而中断后端启动。
    """
    try:
        with engine.begin() as conn:
            for col, ddl in DETECTION_REPORT_VIDEO_REPORT_FIELDS.items():
                sql = (
                    f"ALTER TABLE detection_report "
                    f"ADD COLUMN IF NOT EXISTS {col} {ddl}"
                )
                conn.execute(text(sql))
                logger.info(
                    f"[migration] detection_report.{col} ensured ({ddl})"
                )

            for sql in DETECTION_REPORT_VIDEO_REPORT_INDEXES:
                conn.execute(text(sql))
                logger.info(
                    f"[migration] ensured detection_report index: {sql}"
                )

        print("[DB] detection_report video report migration: OK")
    except Exception as e:
        # 不让启动失败；只记录日志
        logger.error(
            f"[migration] detection_report video report migration failed: {e}"
        )
        print(
            f"[DB][WARN] detection_report video report migration failed: {e}"
        )
