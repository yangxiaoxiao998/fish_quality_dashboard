"""未来录制计划后台调度器。

复用现有 start_stream / stop_stream：
  - 调度器只负责"到点 → 启动 → 状态更新"
  - 启动参数：calibration_only=False, write_video=True, create_video_task=True
  - duration_minutes = 计划时长
  - 视频写入、算法入队、首帧武装、watchdog 等全部交给现有链路
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.repositories import camera_record_schedule_repo as sched_repo
from app.repositories.db import SessionLocal
from app.services.camera_yzt.camera_yzt_service import camera_service_yzt

logger = logging.getLogger("camera_schedule")

POLL_INTERVAL_SECONDS = 1.0
MISFIRE_GRACE_SECONDS = 300
WAIT_FOR_IDLE_SECONDS = 15.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_start_kwargs(duration_minutes: int) -> dict[str, Any]:
    return {
        "calibration_only": False,
        "write_video": True,
        "create_video_task": True,
        "duration_minutes": int(duration_minutes),
    }


class CameraRecordScheduleService:
    """单线程、daemon、幂等 start/stop。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._wakeup = threading.Event()
        self._stop_flag = threading.Event()
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._stop_flag.clear()
        self._wakeup.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="camera_record_schedule",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[camera_schedule] scheduler_started poll=%.1fs misfire_grace=%ds",
            POLL_INTERVAL_SECONDS,
            MISFIRE_GRACE_SECONDS,
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_flag.set()
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._running = False
        self._thread = None
        logger.info("[camera_schedule] scheduler_stopped")

    def _run_loop(self) -> None:
        """主循环。事件驱动：stop_flag 置位或 1s 超时都会立刻检查 stop。"""
        logger.info("[camera_schedule] scheduler_loop_start")
        try:
            self._recover_after_startup()
            while not self._stop_flag.is_set():
                try:
                    self._process_due_schedules()
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "[camera_schedule] scheduler_loop_iteration_failed: %r",
                        exc,
                    )
                if self._wakeup.wait(POLL_INTERVAL_SECONDS):
                    break
        finally:
            logger.info("[camera_schedule] scheduler_loop_exit")

    def _recover_after_startup(self) -> None:
        """后端启动时由 main.py 调一次，处理陈旧 schedule。"""
        db = SessionLocal()
        try:
            from app.repositories import camera_record_schedule_repo as _repo
            affected = _repo.recover_stale_schedules(
                db,
                now=_utcnow(),
                misfire_grace_seconds=MISFIRE_GRACE_SECONDS,
            )
            if affected:
                logger.info(
                    "[camera_schedule] scheduler_recovered affected=%d",
                    affected,
                )
            else:
                logger.info("[camera_schedule] scheduler_recovered affected=0")
        finally:
            db.close()

    def _process_due_schedules(self) -> None:
        """查找所有到点 pending 计划并按时间顺序处理。"""
        now = _utcnow()
        db = SessionLocal()
        try:
            due_list = sched_repo.find_due_pending(
                db, now=now, limit=32
            )
        finally:
            db.close()

        for r in due_list:
            if self._stop_flag.is_set():
                return
            try:
                self._process_one(r.schedule_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[camera_schedule] schedule_failed id=%s err=%r",
                    r.schedule_id,
                    exc,
                )

    def _process_one(self, schedule_id: str) -> None:
        """处理单条计划：claim → 摄像头状态检查 → start_stream → 状态更新。"""
        token = sched_repo.make_claim_token()
        db = SessionLocal()
        try:
            claimed = sched_repo.claim_next_due_schedule(
                db,
                schedule_id=schedule_id,
                claim_token=token,
            )
        finally:
            db.close()
        if claimed is None:
            # 已经被其他调度循环或用户取消
            return

        # 错过的宽限
        now = _utcnow()
        delay = (now - claimed.planned_start_at).total_seconds()
        if delay > MISFIRE_GRACE_SECONDS:
            logger.warning(
                "[camera_schedule] schedule_skipped id=%s delay=%.1fs reason=missed_misfire",
                schedule_id,
                delay,
            )
            db = SessionLocal()
            try:
                sched_repo.mark_schedule_skipped(
                    db,
                    schedule_id=schedule_id,
                    error_message="missed_misfire",
                )
            finally:
                db.close()
            return

        # 摄像头占用：到点时不能强制停止手动录制
        try:
            st = camera_service_yzt.get_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[camera_schedule] status_query_failed id=%s err=%r",
                schedule_id,
                exc,
            )
            st = {}

        sess_mode = (st or {}).get("session_mode") or "idle"
        sess_vid = (st or {}).get("video_id")

        if sess_mode == "recording" and sess_vid:
            # 已有正式录制在跑；宽限期内再次尝试，否则 skipped
            if delay <= MISFIRE_GRACE_SECONDS:
                # 让它保持 pending，等下次循环
                logger.info(
                    "[camera_schedule] schedule_waiting_camera_busy id=%s video_id=%s",
                    schedule_id,
                    sess_vid,
                )
                db = SessionLocal()
                try:
                    # claim 后恢复 pending，避免计划卡在 claiming。
                    sched_repo.reset_claiming_schedule(db, schedule_id=schedule_id)
                finally:
                    db.close()
                return
            logger.warning(
                "[camera_schedule] schedule_skipped id=%s reason=camera_busy",
                schedule_id,
            )
            db = SessionLocal()
            try:
                sched_repo.mark_schedule_skipped(
                    db,
                    schedule_id=schedule_id,
                    error_message="camera_busy_at_scheduled_start",
                )
            finally:
                db.close()
            return

        if sess_mode == "preview":
            ok = self._try_stop_preview()
            if not ok:
                logger.warning(
                    "[camera_schedule] schedule_failed id=%s reason=preview_stop_timeout",
                    schedule_id,
                )
                db = SessionLocal()
                try:
                    sched_repo.mark_schedule_failed(
                        db,
                        schedule_id=schedule_id,
                        error_message="preview_stop_timeout",
                        stop_reason="preview_stop_timeout",
                        stop_source="schedule_dispatcher",
                    )
                finally:
                    db.close()
                return

        # 启动正式录制
        kw = _build_start_kwargs(claimed.duration_minutes)
        # 完整性设置按每条计划真正开始的时刻读取；创建计划时的旧值不参与执行。
        saved_settings = camera_service_yzt.get_saved_settings()
        kw["force_all_complete"] = bool(saved_settings.get("force_all_complete", False))
        try:
            result = camera_service_yzt.start_stream(
                kw,
                record_schedule_id=claimed.schedule_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[camera_schedule] start_failed id=%s err=%r",
                schedule_id,
                exc,
            )
            db = SessionLocal()
            try:
                sched_repo.mark_schedule_failed(
                    db,
                    schedule_id=schedule_id,
                    error_message=f"start_stream_raised: {exc!r}",
                    stop_reason="start_failed",
                    stop_source="schedule_dispatcher",
                )
            finally:
                db.close()
            return

        new_video_id = (result or {}).get("video_id") or (result or {}).get(
            "current_video_id"
        )
        status_str = (result or {}).get("status", "error")
        if not new_video_id or status_str not in {"starting", "running"}:
            err = (result or {}).get("message") or "start_stream_failed"
            db = SessionLocal()
            try:
                sched_repo.mark_schedule_failed(
                    db,
                    schedule_id=schedule_id,
                    error_message=err,
                    stop_reason="start_failed",
                    stop_source="schedule_dispatcher",
                )
            finally:
                db.close()
            return

        # 成功启动
        db = SessionLocal()
        try:
            sched_repo.mark_schedule_starting(
                db,
                schedule_id=schedule_id,
                video_id=str(new_video_id),
            )
        finally:
            db.close()
        logger.info(
            "[camera_schedule] schedule_starting id=%s video_id=%s duration=%dmin",
            schedule_id,
            new_video_id,
            claimed.duration_minutes,
        )

    def _try_stop_preview(self) -> bool:
        """同步停止 preview，最多等 15 秒。"""
        try:
            camera_service_yzt.stop_stream()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[camera_schedule] preview_stop_raised: %r", exc
            )
            return False
        deadline = time.time() + WAIT_FOR_IDLE_SECONDS
        while time.time() < deadline:
            if self._stop_flag.is_set():
                return False
            try:
                st = camera_service_yzt.get_status()
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
                continue
            mode = (st or {}).get("session_mode")
            if mode in {None, "idle"}:
                return True
            time.sleep(0.5)
        return False

    # 计划生命周期：start_stream 之后由 pollCameraStatus → mark_schedule_recording →
    # 现有 stop_stream → mark_schedule_completed / mark_schedule_failed。
    # 这些钩子放在 camera_yzt_service.py 中调用。


# 模块级单例：与 camera_yzt_service.camera_service_yzt 模式保持一致，
# main.py 通过 `from ... import camera_record_schedule_service` 引用。
camera_record_schedule_service = CameraRecordScheduleService()
