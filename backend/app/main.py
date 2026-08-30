# 作用：FastAPI 应用的主入口，负责创建 FastAPI 实例并注册路由和中间件
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers.health_router import router as health_router
from app.routers.stream_router import router as stream_router
from app.routers.fish_router import router as fish_router
from app.routers.stats_router import router as stats_router
from app.routers.camera_router import router as camera_router
from app.routers.camera_yzt_router import router as camera_yzt_router
from app.routers.report_router import router as report_router
from app.routers.calibration_router import router as calibration_router
from app.routers.led_router import router as led_router
from app.routers.system_router import router as system_router
from app.routers.algorithm_router import router as algorithm_router
from app.routers.operation_log_router import router as operation_log_router
from app.routers.ops_algorithm_task_router import router as ops_algorithm_task_router
from app.routers.storage_router import router as storage_router
from app.repositories.db import SessionLocal
from app.services.operation_log_service import log_operation


def _operation_module(path: str) -> str:
    if path.startswith("/api/algorithm"):
        return "算法检测"
    if path.startswith("/api/reports"):
        return "数据报表"
    if path.startswith("/api/camera"):
        return "摄像头控制"
    if path.startswith("/api/calibration") or path.startswith("/api/algorithm/calibration"):
        return "视觉矫正"
    if path.startswith("/api/videos"):
        return "视频管理"
    if path.startswith("/api/fish"):
        return "鱼体数据"
    if path.startswith("/api/settings") or path.startswith("/api/system"):
        return "系统设置"
    if path.startswith("/api/upload"):
        return "视频管理"
    return "系统操作"


def _operation_action(method: str, path: str) -> tuple[str, str]:
    if method == "POST" and path.startswith("/api/algorithm/videos"):
        return "start_video_detection", "启动视频检测"
    if method == "POST" and path.startswith("/api/algorithm/calibration/capture"):
        return "capture_calibration", "抓拍标定"
    if method == "POST" and path.startswith("/api/algorithm/calibration"):
        return "run_calibration", "执行视觉矫正"
    if "calibration" in path and "clear" in path:
        return "clear_calibration", "清除标定"
    if method == "POST" and path.startswith("/api/reports") and "generate" in path:
        return "generate_report", "生成报表"
    if method == "DELETE":
        return "delete", "删除"
    if method in {"PUT", "PATCH"}:
        return "update", "修改"
    if method == "POST":
        return "submit", "提交操作"
    return method.lower(), method


def _should_log_operation(method: str, path: str) -> bool:
    if method == "OPTIONS":
        return False
    if path.startswith("/static") or path.startswith("/api/health") or "stream" in path or "frame" in path:
        return False
    if path.startswith("/api/operation-logs"):
        return method in {"POST", "GET"}
    if method in {"DELETE", "PUT", "PATCH"}:
        return path.startswith("/api/")
    if method == "POST":
        prefixes = (
            "/api/algorithm/videos",
            "/api/algorithm/calibration",
            "/api/reports",
            "/api/camera",
            "/api/calibration",
            "/api/upload",
        )
        return path.startswith(prefixes)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI 启动 / 关闭生命周期。

    启动顺序：
        1. ensure_directories
        2. log_db_connection + init_db_schema
        3. 全部 startup_migrations（10 个）
        4. path_migrations
        5. 摄像头 startup_recovery（容错，失败仅记录）
        6. 启动 camera_record_schedule scheduler（所有前置成功后才启动）

    关闭顺序：
        1. 停止 camera_record_schedule scheduler（幂等）
        2. 任何后续清理如需添加，按顺序追加

    设计原则：
        - 不在 import 阶段执行任何运行期副作用；
        - 迁移失败 / 启动恢复失败不掩盖，按当前容错语义处理；
        - 关闭时只 stop scheduler；不再次迁移、不重复执行 startup。
    """
    # ---- startup ----
    print("=========================================")
    print("[startup] Fish Quality Dashboard API")
    print("=========================================")

    # 1) 目录
    from app.paths import ensure_directories

    ensure_directories()

    # 2) DB 连接 + schema
    from app.repositories.db import log_db_connection, init_db_schema

    log_db_connection()
    init_db_schema()

    # 3) 全部 startup migrations（保持现有调用顺序）
    from app.repositories.startup_migrations import (
        run_track_id_migration,
        run_body_length_without_tail_migration,
        run_report_tables_migration,
        run_frame_detection_tables_migration,
        run_fish_extra_metric_migration,
        run_detection_report_conclusion_migration,
        run_algorithm_result_tables_migration,
        run_video_task_timed_recording_migration,
        run_video_task_report_migration,
        run_camera_schedule_integrity_migration,
        run_detection_report_video_report_migration,
        run_camera_record_schedule_migration,
        run_storage_tables_migration,
    )

    run_track_id_migration()
    run_body_length_without_tail_migration()
    run_report_tables_migration()
    run_frame_detection_tables_migration()
    run_fish_extra_metric_migration()
    run_detection_report_conclusion_migration()
    run_algorithm_result_tables_migration()
    run_video_task_timed_recording_migration()
    run_video_task_report_migration()
    run_camera_schedule_integrity_migration()
    run_detection_report_video_report_migration()
    run_camera_record_schedule_migration()
    run_storage_tables_migration()
    try:
        from app.paths import DATA_DIR
        from app.services.config_revision_service import ensure_file_revision
        config_db = SessionLocal()
        try:
            ensure_file_revision(config_db, "camera_settings", "default", DATA_DIR / "config" / "camera_settings.json")
            ensure_file_revision(config_db, "focus_settings", "default", DATA_DIR / "config" / "focus_settings.json")
            ensure_file_revision(config_db, "calibration", "default", DATA_DIR / "calibration" / "current_calibration.json")
        finally:
            config_db.close()
    except Exception as exc:
        print(f"[config_revision] local bootstrap failed: {exc!r}")
    # Redis/RQ can lose queued jobs while the database keeps the queued state.
    # Restore only missing jobs before declaring the API ready.
    try:
        from app.workers.queue import recover_missing_queued_video_tasks

        recovery = recover_missing_queued_video_tasks()
        print(f"[queue] startup recovery: {recovery}")
    except Exception as exc:  # noqa: BLE001
        print(f"[queue] startup recovery failed: {exc!r}")

    # 4) 路径迁移
    from app.repositories.path_migrations import migrate_paths_to_relative

    migrate_paths_to_relative()

    # 5) 摄像头启动恢复（容错，失败不能阻断启动）
    try:
        from app.services.camera_yzt.camera_yzt_service import (
            camera_service_yzt,
        )

        print("[camera_yzt] startup recovery begin")
        recovery_result = (
            camera_service_yzt.recover_interrupted_recordings_on_startup()
        )
        print(f"[camera_yzt] startup recovery done: {recovery_result}")
    except Exception as exc:  # noqa: BLE001
        print(f"[camera_yzt] startup recovery failed: {exc!r}")

    # 6) 启动 future 录制计划调度器（仅在所有前置成功后）
    try:
        from app.services.camera_yzt.camera_record_schedule_service import (
            camera_record_schedule_service,
        )

        camera_record_schedule_service.start()
        print("[camera_schedule] scheduler started")
    except Exception as exc:  # noqa: BLE001
        print(f"[camera_schedule] scheduler start failed: {exc!r}")

    try:
        from app.services.backup import rsync_backup_service
        rsync_backup_service.start_scheduler()
        print("[rsync_backup] scheduler initialized")
    except Exception as exc:  # noqa: BLE001
        print(f"[rsync_backup] scheduler start failed: {exc!r}")

    print("=========================================")
    print("[startup] ready")
    print("=========================================")

    try:
        yield
    finally:
        # ---- shutdown ----
        try:
            from app.services.camera_yzt.camera_record_schedule_service import (
                camera_record_schedule_service,
            )

            camera_record_schedule_service.stop()
            print("[camera_schedule] scheduler stopped")
        except Exception as exc:  # noqa: BLE001
            print(f"[camera_schedule] scheduler stop failed: {exc!r}")
        try:
            from app.services.backup import rsync_backup_service
            rsync_backup_service.stop_scheduler()
        except Exception as exc:  # noqa: BLE001
            print(f"[rsync_backup] scheduler stop failed: {exc!r}")


def create_app() -> FastAPI:
    """组装 FastAPI 应用结构。

    重要：本函数只做结构组装（middleware、router、lifespan、mount），
    不得直接执行运行期副作用（DB 连接、迁移、startup_recovery、scheduler.start）。
    所有运行期副作用在 lifespan async context 中执行。
    """
    from app.paths import DATA_DIR

    app = FastAPI(
        title="Fish Quality Dashboard API",
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=str(DATA_DIR)), name="static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def operation_logging_middleware(request: Request, call_next):
        started = time.perf_counter()
        response = None
        error = None
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            path = request.url.path
            method = request.method.upper()
            if _should_log_operation(method, path):
                duration_ms = int((time.perf_counter() - started) * 1000)
                status_code = response.status_code if response is not None else 500
                module = _operation_module(path)
                action, label = _operation_action(method, path)
                db = SessionLocal()
                try:
                    log_operation(
                        db,
                        request=request,
                        module=module,
                        action=action,
                        action_label=label,
                        success=status_code < 400 and error is None,
                        status_code=status_code,
                        error_message=str(error) if error else None,
                        duration_ms=duration_ms,
                    )
                finally:
                    db.close()

    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(stream_router, prefix="/api", tags=["stream"])
    app.include_router(fish_router, prefix="/api", tags=["fish"])
    app.include_router(stats_router, prefix="/api", tags=["stats"])
    app.include_router(camera_router, prefix="/api", tags=["camera"])
    app.include_router(camera_yzt_router, prefix="/api", tags=["camera_yzt"])
    app.include_router(report_router, prefix="/api", tags=["reports"])
    app.include_router(operation_log_router, prefix="/api", tags=["operation_logs"])
    # dashboard_frame_router 已废弃，本阶段不再注册
    app.include_router(calibration_router, prefix="/api", tags=["calibration"])
    app.include_router(led_router)
    app.include_router(system_router)
    app.include_router(algorithm_router, prefix="/api", tags=["algorithm"])
    app.include_router(
        ops_algorithm_task_router,
        prefix="/api",
        tags=["ops-algorithm-tasks"],
    )
    app.include_router(storage_router, prefix="/api", tags=["storage"])

    return app


# 模块级 app：import 阶段仅做结构组装，不执行运行期副作用。
app = create_app()
