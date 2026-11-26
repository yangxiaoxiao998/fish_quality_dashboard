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


def run_video_source_type_removal_migration() -> None:
    """Remove the obsolete multi-source column from legacy databases."""
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE video_task DROP COLUMN IF EXISTS source_type"))
        logger.info("[migration] obsolete video_task.source_type removed")
    except Exception as exc:
        logger.warning("[migration] source_type removal failed: %r", exc)


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
    "video_duration_seconds": "DOUBLE PRECISION",
    "processing_duration_seconds": "DOUBLE PRECISION",
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
    "hex_color": "VARCHAR(32)",
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


def _columns_exist(table: str, columns: list[str]) -> bool:
    """检查表中是否已存在指定的所有列。"""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = :table
                    AND column_name IN :columns
                """),
                {"table": table, "columns": tuple(columns)}
            )
            count = result.scalar() or 0
            return count == len(columns)
    except Exception:
        return False


def run_algorithm_result_tables_migration() -> None:
    """
    算法最新字段 + 索引的幂等迁移。

    - 使用 ALTER TABLE ADD COLUMN IF NOT EXISTS（PostgreSQL 9.6+）。
    - CREATE INDEX IF NOT EXISTS。
    - 字段已存在则跳过，不影响存量数据。
    - 预检查：所有列已存在时直接跳过，避免不必要的表锁。
    """
    if _columns_exist("video_task", list(VIDEO_TASK_ALGO_FIELDS.keys())) and \
       _columns_exist("fish", list(FISH_ALGO_FIELDS.keys())):
        logger.info("[migration] algorithm result tables already up-to-date, skipping")
        print("[DB] algorithm result tables migration: already up-to-date, skipping")
        return

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
        logger.error(f"[migration] algorithm result tables migration failed: {e}")
        print(f"[DB][WARN] algorithm result tables migration failed: {e}")


def run_fish_measurement_backfill_migration() -> None:
    """Backfill new measurement columns without overwriting new values.

    The old columns remain during the compatibility window.  This migration is
    deliberately one-way and idempotent: a value is copied only when the new
    column is NULL, so a newer algorithm result always wins.
    """
    pairs = (
        ("body_length", "body_length_cm"),
        ("body_length_without_tail", "body_length_no_tail_cm"),
        ("body_height", "body_height_cm"),
        ("body_weight", "pred_weight_g"),
    )
    try:
        with engine.begin() as conn:
            columns = {
                str(row[0])
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'fish'"
                    )
                ).fetchall()
            }
            updates: dict[str, int] = {}
            for old_field, new_field in pairs:
                if old_field not in columns or new_field not in columns:
                    continue
                result = conn.execute(
                    text(
                        f"UPDATE fish SET {new_field} = {old_field} "
                        f"WHERE {new_field} IS NULL AND {old_field} IS NOT NULL"
                    )
                )
                updates[f"{old_field}->{new_field}"] = int(result.rowcount or 0)
        changed = sum(updates.values())
        logger.info(
            "[migration] fish measurement backfill completed: %s (rows=%d)",
            updates,
            changed,
        )
        print(f"[DB] fish measurement backfill: rows={changed}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[migration] fish measurement backfill failed: %r", exc)


# ---------------------------------------------------------------------------
# 第一阶段新增：定时录制字段迁移
# ---------------------------------------------------------------------------

VIDEO_TASK_TIMED_RECORDING_FIELDS: dict[str, str] = {
    "recording_started_at": "TIMESTAMP WITH TIME ZONE",
    "record_duration_seconds": "INTEGER",
    "scheduled_stop_at": "TIMESTAMP WITH TIME ZONE",
    "actual_stop_at": "TIMESTAMP WITH TIME ZONE",
    "stop_reason": "TEXT",
    "stop_source": "TEXT",
    "auto_stop_triggered": "BOOLEAN DEFAULT FALSE",
}

VIDEO_TASK_REPORT_FIELDS: dict[str, str] = {
    "aquaculture_bin": "VARCHAR(255)",
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
    - 预检查：所有列已存在时直接跳过，避免不必要的表锁。
    """
    if _columns_exist("video_task", list(VIDEO_TASK_TIMED_RECORDING_FIELDS.keys())):
        logger.info("[migration] video_task timed recording columns already up-to-date, skipping")
        print("[DB] video_task timed recording migration: already up-to-date, skipping")
        return

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
        logger.error(
            f"[migration] video_task timed recording migration failed: {e}"
        )
        print(f"[DB][WARN] video_task timed recording migration failed: {e}")


def run_video_task_report_migration() -> None:
    """为视频报表元数据增加养鱼仓字段，兼容已有 video_task 数据。"""
    if _columns_exist("video_task", list(VIDEO_TASK_REPORT_FIELDS.keys())):
        logger.info("[migration] video_task report columns already up-to-date, skipping")
        print("[DB] video_task report migration: already up-to-date, skipping")
        return
    try:
        with engine.begin() as conn:
            for col, ddl in VIDEO_TASK_REPORT_FIELDS.items():
                conn.execute(text(
                    f"ALTER TABLE video_task ADD COLUMN IF NOT EXISTS {col} {ddl}"
                ))
        print("[DB] video_task report migration: OK")
    except Exception as e:
        logger.error("[migration] video_task report migration failed: %s", e)
        print(f"[DB][WARN] video_task report migration failed: {e}")


def run_camera_schedule_integrity_migration() -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE camera_record_schedule ADD COLUMN IF NOT EXISTS force_all_complete BOOLEAN NOT NULL DEFAULT FALSE"
            ))
        print("[DB] camera schedule integrity migration: OK")
    except Exception as e:
        logger.error("[migration] camera schedule integrity migration failed: %s", e)


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
    - 预检查：列已存在时直接跳过，避免不必要的表锁。
    """
    if _columns_exist("detection_report", list(DETECTION_REPORT_VIDEO_REPORT_FIELDS.keys())):
        logger.info("[migration] detection_report video_id column already up-to-date, skipping")
        print("[DB] detection_report video report migration: already up-to-date, skipping")
        return

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
        logger.error(
            f"[migration] detection_report video report migration failed: {e}"
        )
        print(
            f"[DB][WARN] detection_report video report migration failed: {e}"
        )


def run_camera_record_schedule_migration() -> None:
    """幂等创建 future 录制计划表 + 索引。

    - CREATE TABLE IF NOT EXISTS：反复运行不会失败
    - CREATE INDEX IF NOT EXISTS：启动多次无副作用
    - 不影响现有 video_task / fish / report 等任何字段
    """
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS camera_record_schedule (
                        schedule_id       VARCHAR(64) PRIMARY KEY,
                        status            VARCHAR(16) NOT NULL DEFAULT 'pending',
                        planned_start_at  TIMESTAMPTZ NOT NULL,
                        planned_end_at    TIMESTAMPTZ NOT NULL,
                        duration_minutes  INTEGER NOT NULL,
                        video_id          VARCHAR(64),
                        actual_start_at   TIMESTAMPTZ,
                        actual_stop_at    TIMESTAMPTZ,
                        claimed_at        TIMESTAMPTZ,
                        claim_token       VARCHAR(64),
                        cancelled_at      TIMESTAMPTZ,
                        error_message     TEXT,
                        stop_reason       TEXT,
                        stop_source       TEXT,
                        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_camera_record_schedule_due
                    ON camera_record_schedule (status, planned_start_at)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_camera_record_schedule_video
                    ON camera_record_schedule (video_id)
                    """
                )
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        # migration 失败不能让后端无法启动；记录并继续
        logger.warning(
            "[camera_schedule_migration] failed: %r",
            exc,
        )
