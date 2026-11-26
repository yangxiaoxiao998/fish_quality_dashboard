from __future__ import annotations

import logging
import json
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Optional

import cv2
import requests

from app.paths import DATA_DIR, VID_DIR
from app.services.camera_yzt.pi_runtime_config import (
    DEFAULT_CAMERA_READ_URL,
    DEFAULT_PI_BASE_URL,
    get_camera_push_url,
    get_camera_read_url,
    get_pi_base_url,
    get_pi_runtime_config,
    is_camera_push_url_automatic,
)
from app.services.pi_http_client import make_pi_session, pi_get, pi_post
from app.services.pi_endpoint_resolver import (
    PiResolutionError,
    get_pi_logical_base_url,
    get_pi_arp_diag,
    get_pi_resolved_base_url,
    get_pi_resolution_diag,
    invalidate_pi_resolution_cache,
)
from app.services.video_id_service import generate_video_id
from app.services.path_service import to_abs_data_path
from app.repositories.db import SessionLocal
from app.repositories import session_repo
from app.services.camera_yzt.segmented_recording_service import segmented_recording_service
from app.services.storage_health_service import ensure_recording_capacity, get_storage_health

CAMERA_ENABLE_MOCK_DETECTION = os.getenv(
    "CAMERA_ENABLE_MOCK_DETECTION",
    "false",
).lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


# 等到 OpenCV 读到第一帧的总超时（秒）。
STREAM_READY_TIMEOUT_SECONDS = _env_float(
    "FISH_CAMERA_STREAM_READY_TIMEOUT", 60.0
)
# A temporary camera power/network loss must not immediately terminate a
# recording. Keep the Pi publisher alive and retry the read side for this
# grace period before marking the task failed.
CAMERA_RECONNECT_GRACE_SECONDS = _env_float(
    "FISH_CAMERA_RECONNECT_GRACE_SECONDS", 180.0
)
CAMERA_RECONNECT_REQUEST_INTERVAL_SECONDS = _env_float(
    "FISH_CAMERA_RECONNECT_REQUEST_INTERVAL_SECONDS", 10.0
)

logger = logging.getLogger("camera_yzt_service")


# 独立 Session：禁用系统代理，避免 Clash / VPN 把 raspberrypi.local 错误地代理走。
PI_HTTP_SESSION = make_pi_session()


class CameraRuntimeState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    ERROR = "error"


class CameraServiceYzt:
    # 兼容老代码：访问 ``CameraServiceYzt.DEFAULT_STREAM_URL`` 时返回当前
    # 配置的真实推流地址（不再硬编码 192.168.31.254）。
    @property
    def DEFAULT_STREAM_URL(self) -> str:
        return get_camera_push_url()

    # 兼容老代码：访问 ``CameraServiceYzt.RASPBERRY_PI_URL`` 时返回当前
    # 配置的树莓派 API 逻辑地址（永远使用 mDNS 主机名，不含 IP）。
    @property
    def RASPBERRY_PI_URL(self) -> str:
        return get_pi_base_url()

    FPS = 25
    DETECT_INTERVAL = 1
    VIDEO_EXT = ".mp4"
    CAMERA_START_DELAY = 10
    EVENT_HISTORY_MAX = 100

    def __init__(self) -> None:
        self.state: CameraRuntimeState = CameraRuntimeState.IDLE
        self.is_active: bool = False
        self.last_frame_bytes: Optional[bytes] = None
        # 推流地址（传给树莓派，让 Pi 把视频推到主机）。
        # 来自 FISH_CAMERA_PUSH_URL 或自动推断主机局域网 IP。
        self.camera_push_url: str = get_camera_push_url()
        # 后端 OpenCV 读取地址（主机本机读 MediaMTX）。
        # 来自 FISH_CAMERA_READ_URL，默认 rtmp://127.0.0.1:1935/fish_camera。
        self.camera_read_url: str = get_camera_read_url()
        # 兼容老字段：stream_url 等价于 camera_push_url（推流地址）；
        # capture_url 等价于 camera_read_url（后端读流地址）。
        self.stream_url: str = self.camera_push_url
        self.capture_url: str = self.camera_read_url
        self.width: int = 960
        self.height: int = 540
        self.fps: int = self.FPS
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        # 定时录制 watchdog：与 capture_loop 的 _stop_event 分离，避免互相干扰。
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop_event = threading.Event()
        self._watchdog_video_id: Optional[str] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._writer_width: int = 0
        self._writer_height: int = 0
        self._video_id: Optional[str] = None
        self._video_path: Optional[str] = None
        self._started_at: Optional[datetime] = None
        self._recording_started_at: Optional[datetime] = None
        # 定时录制字段（运行态；与 video_task 持久化字段保持一致）。
        self._record_duration_seconds: Optional[int] = None
        self._scheduled_stop_at: Optional[datetime] = None
        self._auto_stop_triggered: bool = False
        # 延迟武装定时器：
        #   start_stream 收到 duration_minutes 时立刻记录 record_duration_seconds，
        #   但 scheduled_stop_at 留空；等真正收到第一帧（_capture_loop）后再"武装"
        #   scheduled_stop_at = (第一帧时间) + record_duration_seconds 并启动 watchdog。
        # 这样可以避免把"树莓派建连 / MediaMTX 推流等待 / OpenCV 取帧"等启动时间
        # 算进用户的"1 分钟录制"。
        self._timer_pending: bool = False
        self._timer_armed_at: Optional[datetime] = None
        self._frames_captured: int = 0
        self._frames_written: int = 0
        self._capture_started_monotonic: Optional[float] = None
        self._last_error: Optional[str] = None
        self._reconnect_started_at: Optional[datetime] = None
        self._reconnect_timeout_finalization_started: bool = False
        self._storage_stop_started: bool = False
        self._read_fail_count: int = 0
        self.READ_FAIL_THRESHOLD: int = 5
        self._reconnect_grace_seconds = max(0.0, CAMERA_RECONNECT_GRACE_SECONDS)
        self._reconnect_request_interval_seconds = max(
            1.0, CAMERA_RECONNECT_REQUEST_INTERVAL_SECONDS
        )
        # 未来录制计划 ID：start_stream 收到 record_schedule_id 时记录；
        # 正常停止 / 异常停止都会写回计划表。
        self._record_schedule_id: Optional[str] = None
        self._force_all_complete = False
        # 启动 / 运行阶段状态（供前端精细展示）。
        self._stream_ready: bool = False
        self._startup_phase: str = "idle"
        self._startup_error: Optional[str] = None
        # 事件日志（最近 N 条），便于事后追查谁停了推流。
        self._event_history: Deque[dict] = deque(maxlen=self.EVENT_HISTORY_MAX)
        # 标记本次 stop_stream 是用户主动调用还是启动失败被动触发。
        # 仅在 stop_stream 内被使用。
        self._stop_origin: str = ""
        # 标定模式 / 写视频开关 / video_task 创建开关（每次 start_stream 都会重置）
        self._write_video_enabled: bool = True
        self._calibration_only: bool = False
        self._session_create_video_task: bool = True
        # 调试用 desync 模拟开关：None = 使用真实状态。
        # 用于前端“正常流 / 异常流 / 逆向流”三种场景的验证。
        self._debug_pi_running: Optional[bool] = None
        self._debug_backend_active: Optional[bool] = None
        self._debug_pi_reachable: Optional[bool] = None
        # 对焦参数（会话级；每次 start_stream 重置）
        self._autofocus_mode: Optional[str] = None
        self._lens_position: Optional[float] = None
        self._autofocus_window: Optional[str] = None

    @property
    def settings_path(self) -> Path:
        return DATA_DIR / "config" / "camera_settings.json"

    def get_saved_settings(self) -> dict:
        defaults = {
            "stream_url": self._get_camera_push_url(),
            "width": 1980,
            "height": 1020,
            "fps": self.FPS,
        }
        try:
            with self.settings_path.open("r", encoding="utf-8") as file:
                saved = json.load(file)
            if isinstance(saved, dict):
                defaults.update({key: saved[key] for key in defaults if key in saved})
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        # In automatic mode, an old Wi-Fi address must never override the
        # current shared-Ethernet address. Keep the setting editable as an
        # empty value while exposing the effective runtime target to callers.
        if is_camera_push_url_automatic():
            defaults["stream_url"] = ""
        return defaults

    def save_settings(self, settings: dict) -> dict:
        current = self.get_saved_settings()
        current.update(settings)
        if is_camera_push_url_automatic():
            current["stream_url"] = ""
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as file:
            json.dump(current, file, ensure_ascii=False, indent=2)
        return current

    @property
    def focus_config_path(self) -> Path:
        return DATA_DIR / "config" / "focus_settings.json"

    @property
    def focus_photo_dir(self) -> Path:
        # Focus photos are displayed by the frontend after capture. Keep them
        # under the public calibration tree so /static can serve the returned
        # photo_url in both Vite and packaged Electron modes.
        return DATA_DIR / "calibration" / "focus"

    def get_focus_settings(self) -> dict:
        defaults = {
            "autofocus_mode": "continuous",
            "lens_position": 2.0,
            "autofocus_window": "0.35,0.30,0.30,0.40",
        }
        try:
            with self.focus_config_path.open("r", encoding="utf-8") as file:
                saved = json.load(file)
            if isinstance(saved, dict):
                defaults.update({key: saved[key] for key in defaults if key in saved})
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        return defaults

    def save_focus_settings(self, settings: dict) -> dict:
        current = self.get_focus_settings()
        current.update(settings)
        self.focus_config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.focus_config_path.open("w", encoding="utf-8") as file:
            json.dump(current, file, ensure_ascii=False, indent=2)
        return current

    def capture_focus_photo(self, name: str) -> Path:
        frame_bytes = self.get_latest_frame()
        if not frame_bytes:
            raise RuntimeError("摄像头尚未获取到实时画面")
        self.focus_photo_dir.mkdir(parents=True, exist_ok=True)
        target = self.focus_photo_dir / f"{name}.jpg"
        target.write_bytes(frame_bytes)
        return target

    def _release_runtime_resources(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
            self._writer = None

    # ----------------- 运行时配置 / 事件日志 -----------------

    def _get_pi_base_url(self) -> str:
        """统一获取树莓派 API 逻辑地址。"""
        return get_pi_base_url()

    def _get_default_stream_url(self) -> str:
        """兼容老字段名，等价于推流地址。"""
        return get_camera_push_url()

    def _get_camera_push_url(self) -> str:
        """推流地址（传给树莓派的目标）。"""
        return get_camera_push_url()

    def _get_camera_read_url(self) -> str:
        """后端 OpenCV 读取地址（主机本机读 MediaMTX）。"""
        return get_camera_read_url()

    def _record_event(self, event: str, **kwargs: Any) -> None:
        """记录事件到内存 ring buffer（同时打印到日志）。"""
        item: dict = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **kwargs,
        }
        self._event_history.append(item)
        print(f"[camera_yzt_event] {item}")

    def _begin_reconnect_wait(self, reason: str) -> None:
        """Enter reconnecting without ending the current recording session."""
        with self._lock:
            already_waiting = self.state == CameraRuntimeState.RECONNECTING
            if self.state in (
                CameraRuntimeState.STARTING,
                CameraRuntimeState.RUNNING,
                CameraRuntimeState.RECONNECTING,
            ):
                self.state = CameraRuntimeState.RECONNECTING
                self.is_active = False
                self._stream_ready = False
                self._startup_phase = "reconnecting"
                if self._reconnect_started_at is None:
                    self._reconnect_started_at = datetime.now(timezone.utc)
                self._last_error = (
                    f"视频流暂时不可读，将等待 {self._reconnect_grace_seconds:.0f} 秒后再判定失败"
                )
        if not already_waiting:
            self._record_event(
                "stream_reconnect_wait_begin",
                reason=reason,
                grace_seconds=self._reconnect_grace_seconds,
                video_id=self._video_id,
            )

    def _mark_stream_recovered(self) -> None:
        with self._lock:
            was_reconnecting = self.state == CameraRuntimeState.RECONNECTING
            if self.state in (
                CameraRuntimeState.STARTING,
                CameraRuntimeState.RECONNECTING,
            ):
                self.state = CameraRuntimeState.RUNNING
            self.is_active = True
            self._stream_ready = True
            self._startup_phase = "running"
            self._last_error = None
            self._reconnect_started_at = None
            self._reconnect_timeout_finalization_started = False
            self._storage_stop_started = False
        if was_reconnecting:
            self._record_event("stream_recovered", video_id=self._video_id)

    # ----------------- 定时录制辅助 -----------------

    TIMED_RECORDING_MIN_MINUTES = 1
    TIMED_RECORDING_MAX_MINUTES = 24 * 60  # 1440

    @classmethod
    def _parse_duration_minutes(
        cls, stream_config: Optional[dict]
    ) -> Optional[int]:
        """解析并校验 ``duration_minutes``。

        - ``None`` 或缺省 → 返回 ``None``（不限时，兼容旧接口）。
        - 必须可转为正整数。
        - 范围：1~1440 分钟。

        校验失败抛 ``ValueError``，由上层 router 转为 status="error"。
        """
        if not stream_config:
            return None
        raw = stream_config.get("duration_minutes")
        if raw is None or raw == "":
            return None
        try:
            value = int(raw)
        except Exception:
            raise ValueError("录制时长必须是整数分钟")
        if value <= 0:
            raise ValueError("录制时长必须大于 0 分钟")
        if value > cls.TIMED_RECORDING_MAX_MINUTES:
            raise ValueError("录制时长不能超过 24 小时")
        if value < cls.TIMED_RECORDING_MIN_MINUTES:
            raise ValueError("录制时长必须至少 1 分钟")
        return value

    def _compute_remaining_seconds(self) -> Optional[int]:
        """计算 ``scheduled_stop_at - now`` 的剩余秒数。

        - 无计划停止时间 → ``None``（未设置定时录制）。
        - 已过期 → ``0``（由 watchdog 后续处理；本阶段不触发自动停止）。
        """
        if not self._scheduled_stop_at:
            return None
        from app.utils.time_utils import now_beijing

        try:
            delta = (self._scheduled_stop_at - now_beijing()).total_seconds()
        except Exception:
            return None
        return max(0, int(delta))

    def _arm_timed_recording_if_ready(self, reason: str = "first_frame") -> None:
        """延迟武装：在摄像头真正收到第一帧（_capture_loop 主循环）后才设置
        ``scheduled_stop_at``、写入 DB、启动 watchdog。

        关键约束（全部满足才武装）：
            * ``_timer_pending is True``（start_stream 时已经预设）
            * ``_record_duration_seconds`` 有效
            * ``_state`` 是 STARTING 或 RUNNING
            * ``_session_create_video_task``（主录制会话，不是 preview/标定）
            * ``_write_video_enabled``（实际写视频会话）
            * ``not _calibration_only``（标定模式永远不进入定时录制）
            * ``self._video_id`` 已分配

        已经被武装过（``_scheduled_stop_at`` 非 None 且 timer_pending=False）就直接 no-op。
        """
        with self._lock:
            if not self._timer_pending:
                return
            if not self._record_duration_seconds:
                self._timer_pending = False
                return
            if self._scheduled_stop_at is not None:
                # 已经被武装过，避免重复
                self._timer_pending = False
                return
            if self.state not in (
                CameraRuntimeState.STARTING,
                CameraRuntimeState.RUNNING,
            ):
                return
            if (
                not self._session_create_video_task
                or not self._write_video_enabled
                or self._calibration_only
            ):
                # preview / 标定 / 不写视频 → 不武装
                self._timer_pending = False
                return
            if not self._video_id:
                # 主录制会话应该有 video_id；没拿到就 defer 给下一帧
                return

            # 计算 scheduled_stop_at：以"收到第一帧"作为有效录制起点，
            # 这样用户选 1 分钟 = 实际拍 1 分钟有效画面，不再扣掉 ~12 秒启动等待。
            now_utc = datetime.now(timezone.utc)
            # DB 存北京时间（项目统一约定）；in-memory 用 timezone-aware UTC 便于比较。
            self._timer_armed_at = now_utc
            self._scheduled_stop_at = now_utc + timedelta(
                seconds=int(self._record_duration_seconds)
            )
            self._timer_pending = False
            self._auto_stop_triggered = False
            video_id = self._video_id

        # 写 DB（armed 时间用北京时间落盘）
        from app.utils.time_utils import now_beijing

        try:
            db = SessionLocal()
            try:
                armed_bj = now_beijing() + timedelta(
                    seconds=int(self._record_duration_seconds)
                )
                session_repo.update_video_task_recording_schedule(
                    db,
                    video_id=video_id,
                    record_duration_seconds=int(self._record_duration_seconds),
                    scheduled_stop_at=armed_bj,
                    auto_stop_triggered=False,
                )
                if self._record_schedule_id:
                    from app.repositories import camera_record_schedule_repo as schedule_repo
                    schedule_repo.mark_schedule_recording(
                        db,
                        schedule_id=self._record_schedule_id,
                        actual_start_at=now_beijing(),
                    )
            finally:
                db.close()
        except Exception as db_err:
            print(
                f"[camera_yzt] WARN: arm timed recording 写 DB 失败: {db_err!r}"
            )

        # 启动 watchdog：_start_timed_recording_watchdog 内部已经处理
        # scheduled_stop_at is None 的 short-circuit；这里 scheduled_stop_at
        # 已设置，所以这次调用会真正拉起线程。
        self._start_timed_recording_watchdog()

        self._record_event(
            "timed_recording_timer_armed",
            video_id=video_id,
            record_duration_seconds=int(self._record_duration_seconds or 0),
            scheduled_stop_at=(
                self._scheduled_stop_at.isoformat()
                if self._scheduled_stop_at
                else None
            ),
            reason=reason,
        )
        print(
            f"[camera_yzt] timed_recording armed: duration={self._record_duration_seconds}s, "
            f"video_id={video_id}, reason={reason}"
        )

    # ----------------- 定时录制 watchdog -----------------

    WATCHDOG_TICK_SECONDS = 1.0

    def _start_timed_recording_watchdog(self) -> None:
        """启动 watchdog 后台线程，监控 ``scheduled_stop_at``。

        启动条件（全部满足）：
            * 已设置 ``scheduled_stop_at``；
            * 已设置 ``self._video_id``；
            * 当前会话是 video_task 会话（``_session_create_video_task=True``）。

        不满足条件时静默返回（不会启动线程）。
        """
        # 先尝试取消旧 watchdog（force_restart / 重复 start 场景）
        self._stop_timed_recording_watchdog(reason="restart_watchdog")

        if not self._scheduled_stop_at or not self._video_id:
            return
        if not self._session_create_video_task:
            return

        self._watchdog_stop_event.clear()
        self._watchdog_video_id = self._video_id

        thread = threading.Thread(
            target=self._timed_recording_watchdog_loop,
            name=f"camera-timed-watchdog-{self._video_id}",
            daemon=True,
        )
        self._watchdog_thread = thread
        thread.start()

        self._record_event(
            "timed_recording_watchdog_started",
            video_id=self._video_id,
            scheduled_stop_at=(
                self._scheduled_stop_at.isoformat()
                if self._scheduled_stop_at
                else None
            ),
            record_duration_seconds=self._record_duration_seconds,
        )

    def _stop_timed_recording_watchdog(self, reason: str = "cancel") -> None:
        """停止 watchdog 线程。

        - 始终 ``set`` 停止事件，使主循环退出。
        - 仅当 watchdog 线程仍在运行，且当前线程不是 watchdog 自身时，才 ``join``。
        - ``auto_stop_triggered`` 标志由 watchdog 自身在到点时设置，本方法不重置；
          状态重置由 ``stop_stream`` 统一在 ``finally`` 中完成。
        """
        self._watchdog_stop_event.set()

        thread = self._watchdog_thread
        current = threading.current_thread()
        if thread and thread.is_alive() and thread is not current:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

        self._watchdog_thread = None
        self._watchdog_video_id = None

        self._record_event(
            "timed_recording_watchdog_stopped", reason=reason
        )

    def _timed_recording_watchdog_loop(self) -> None:
        """watchdog 主循环：每 1 秒检查一次是否到达计划停止时间。

        设计要点：
            * 锁内仅做快照读取，**绝不在持锁时调用 ``stop_stream``**。
            * 到点后先在锁内把 ``_auto_stop_triggered=True`` 抢占，
              然后释放锁再调用 ``stop_stream``，避免和 capture_loop / 用户
              手动 stop 的 lock 顺序死锁。
            * watchdog 视频 ID 与当前 ``self._video_id`` 不一致时退出（防 force_restart 残留）。
            * 只有真正离开本次录制会话时退出；RECONNECTING 仍属于本次
              录制，会继续计时，避免断网恢复后定时停止失效。
        """
        from app.utils.time_utils import now_beijing

        exit_reason = "init"
        try:
            while not self._watchdog_stop_event.is_set():
                should_stop = False
                video_id: Optional[str] = None
                scheduled_stop_at: Optional[datetime] = None
                state: Optional[CameraRuntimeState] = None
                is_active = False

                with self._lock:
                    video_id = self._video_id
                    watchdog_video_id = self._watchdog_video_id
                    scheduled_stop_at = self._scheduled_stop_at
                    state = self.state
                    is_active = state in (
                        CameraRuntimeState.STARTING,
                        CameraRuntimeState.RUNNING,
                        CameraRuntimeState.RECONNECTING,
                    )

                    if (
                        not scheduled_stop_at
                        or not video_id
                        or not self._session_create_video_task
                    ):
                        exit_reason = "no_schedule_or_video"
                        return

                    if (
                        watchdog_video_id
                        and video_id != watchdog_video_id
                    ):
                        exit_reason = "video_changed"
                        return

                    if not is_active:
                        exit_reason = (
                            f"state_not_active:"
                            f"{state.value if state is not None else 'none'}"
                        )
                        return

                    if self._auto_stop_triggered:
                        exit_reason = "already_triggered"
                        return

                    now = now_beijing()
                    if now >= scheduled_stop_at:
                        # 抢占：锁内设置标志并准备触发，锁外实际调用 stop_stream
                        self._auto_stop_triggered = True
                        should_stop = True
                        exit_reason = "timed_stop_triggered"

                if should_stop:
                    self._record_event(
                        "timed_stop_triggered",
                        video_id=video_id,
                        scheduled_stop_at=(
                            scheduled_stop_at.isoformat()
                            if scheduled_stop_at
                            else None
                        ),
                    )
                    try:
                        self.stop_stream(
                            reason="timed_recording_expired",
                            source="camera_timer",
                            auto_stop_triggered=True,
                        )
                    except Exception as exc:
                        self._record_event(
                            "timed_stop_failed",
                            video_id=video_id,
                            error=repr(exc),
                        )
                        print(
                            f"[camera_yzt] timed stop failed: {exc!r}"
                        )
                    return

                # 1 秒内可被 stop_event 提前唤醒
                if self._watchdog_stop_event.wait(self.WATCHDOG_TICK_SECONDS):
                    exit_reason = "watchdog_stop_event"
                    return
        finally:
            self._record_event(
                "timed_recording_watchdog_exit", reason=exit_reason
            )

    # ----------------- Pi 端辅助方法 -----------------

    def _request_pi_status(self, timeout: float = 5.0) -> dict:
        """查询树莓派 /camera/status。

        Pi 端当前实现只返回 {"running": true}，没有 stream_url。
        因此不能因为 stream_url 缺失而崩溃。

        成功返回示例::

            {
                "reachable": True,
                "ok": True,
                "status_code": 200,
                "running": True,
                "stream_url": None,
                "raw": {...}
            }

        失败返回示例::

            {
                "reachable": False,
                "ok": False,
                "error": "...",
                "hostname": "raspberrypi.local",
                "resolved_ip": None,
                "running": False,
                "stream_url": None,
                "raw": None
            }

        注意：mDNS 解析失败不会立即判定为不可达，会继续用逻辑地址
        ``raspberrypi.local:9000`` 直接发 HTTP 请求。HTTP 真正成功
        才算 ``reachable=True``。
        """
        result: dict = {
            "reachable": False,
            "ok": False,
            "status_code": None,
            "error": None,
            "hostname": get_pi_logical_base_url(),
            "resolved_ip": None,
            "running": False,
            "stream_url": None,
            "raw": None,
        }

        # 1. 尝试解析 mDNS 作为辅助信息（解析失败不视为不可达）
        try:
            resolved_base = get_pi_resolved_base_url()
            parsed_ip = resolved_base.split("//", 1)[1].split(":", 1)[0]
        except PiResolutionError:
            parsed_ip = None
            resolved_base = None

        target_url = f"{resolved_base or get_pi_logical_base_url()}/camera/status"

        try:
            resp = pi_get(target_url, timeout=timeout)
            result["resolved_ip"] = parsed_ip or target_url.split("//", 1)[1].split(":", 1)[0]
            result["status_code"] = resp.status_code
            if resp.status_code >= 400:
                result["error"] = f"HTTP {resp.status_code}: {resp.text}"
                # /camera/status 失败时再尝试 /ping，给前端一个直观的连接信号
                try:
                    ping_ok = self._ping_pi(timeout=min(3.0, timeout))
                except Exception:
                    ping_ok = False
                result["ping_ok"] = ping_ok
                return result
            try:
                raw = resp.json()
            except Exception:
                raw = None
            result["raw"] = raw
            if isinstance(raw, dict):
                running_val = raw.get("running")
                if isinstance(running_val, bool):
                    result["running"] = running_val
                elif isinstance(running_val, str):
                    result["running"] = running_val.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    result["running"] = bool(running_val)
                stream_url = (
                    raw.get("stream_url")
                    or raw.get("url")
                    or raw.get("rtmp_url")
                    or raw.get("rtmp")
                )
                if isinstance(stream_url, str) and stream_url.strip():
                    result["stream_url"] = stream_url.strip()
            result["ok"] = True
            result["reachable"] = True
        except requests.exceptions.RequestException as e:
            invalidate_pi_resolution_cache()
            result["error"] = str(e)
            # HTTP 失败时再尝试一次 /ping
            try:
                ping_ok = self._ping_pi(timeout=min(3.0, timeout))
                result["ping_ok"] = ping_ok
                if ping_ok:
                    # /ping 成功说明树莓派可达
                    result["reachable"] = True
                    result["error"] = None
                    result["ok"] = True
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            result["error"] = f"unexpected: {e!r}"
        return result

    def _ping_pi(self, timeout: float = 3.0) -> bool:
        """通过 ``/ping`` 探测树莓派是否可达。

        不依赖 mDNS 解析结果；HTTP 层做真实 DNS。
        """
        url = f"{get_pi_logical_base_url()}/ping"
        try:
            resp = pi_get(url, timeout=timeout)
            return resp.status_code < 500
        except Exception:
            return False

    def _build_pi_url(self, path: str) -> str:
        """构造树莓派 API URL：优先用解析后 IP，失败回退到逻辑地址。"""
        if not path.startswith("/"):
            path = "/" + path
        try:
            base = get_pi_resolved_base_url()
            return f"{base}{path}"
        except PiResolutionError:
            return f"{get_pi_logical_base_url()}{path}"

    def _post_pi_start(self, payload: dict, timeout: float = 10.0) -> dict:
        """通知树莓派 /camera/start，并结构化返回。

        返回示例（成功）::

            {
                "ok": True,
                "status_code": 200,
                "text": "...",
                "json": {...},
                "already_running": False,
                "error": None,
            }

        返回示例（HTTP 400 + stream already running）::

            {
                "ok": False,
                "status_code": 400,
                "text": '{"detail":"stream already running"}',
                "json": {"detail": "stream already running"},
                "already_running": True,
                "error": None,
            }
        """
        result: dict = {
            "ok": False,
            "status_code": None,
            "text": "",
            "json": None,
            "already_running": False,
            "error": None,
        }
        target_url = self._build_pi_url("/camera/start")
        try:
            resp = pi_post(target_url, json=payload, timeout=timeout)
            result["status_code"] = resp.status_code
            result["text"] = (resp.text or "")[:500]
            try:
                result["json"] = resp.json()
            except Exception:
                result["json"] = None
            text_lower = (resp.text or "").lower()
            result["already_running"] = (
                "stream already running" in text_lower
                or "already_running" in text_lower
            )
            result["ok"] = resp.status_code < 400
        except requests.exceptions.RequestException as e:
            invalidate_pi_resolution_cache()
            result["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"unexpected: {e!r}"
        return result

    def _post_pi_stop(self, timeout: float = 10.0) -> dict:
        """通知树莓派 /camera/stop。

        200 / 400 都返回结构化结果，不抛异常。"""
        result: dict = {
            "ok": False,
            "status_code": None,
            "text": "",
            "error": None,
        }
        target_url = self._build_pi_url("/camera/stop")
        try:
            resp = pi_post(target_url, timeout=timeout)
            result["status_code"] = resp.status_code
            result["text"] = (resp.text or "")[:500]
            result["ok"] = resp.status_code < 400
        except requests.exceptions.RequestException as e:
            invalidate_pi_resolution_cache()
            result["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"unexpected: {e!r}"
        return result

    def _post_pi_focus(self, payload: dict, timeout: float = 5.0) -> dict:
        """通知树莓派 /camera/focus 动态更新对焦参数。"""
        result: dict = {
            "ok": False,
            "status_code": None,
            "text": "",
            "error": None,
        }
        target_url = self._build_pi_url("/camera/focus")
        try:
            resp = pi_post(target_url, json=payload, timeout=timeout)
            result["status_code"] = resp.status_code
            result["text"] = (resp.text or "")[:500]
            result["ok"] = resp.status_code < 400
        except requests.exceptions.RequestException as e:
            invalidate_pi_resolution_cache()
            result["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"unexpected: {e!r}"
        return result

    def _probe_expected_stream(self, stream_url: str, attempts: int = 3, delay: float = 1.0) -> bool:
        """短探测 expected stream_url 是否真的可达。

        仅用于判断是否能复用 Pi 端已有推流，不占用 self._cap。
        """
        if not stream_url:
            return False
        for i in range(1, max(1, attempts) + 1):
            cap = None
            try:
                cap = cv2.VideoCapture(stream_url)
                if cap.isOpened():
                    print(
                        f"[camera_yzt] probe expected stream OK "
                        f"({i}/{attempts}): {stream_url}"
                    )
                    return True
            except Exception as e:
                print(f"[camera_yzt] probe expected stream error ({i}/{attempts}): {e}")
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
            if i < attempts:
                time.sleep(max(0.1, delay))
        print(
            f"[camera_yzt] probe expected stream FAILED after {attempts} attempts: {stream_url}"
        )
        return False

    def start_stream(
        self,
        stream_config: Optional[dict] = None,
        *,
        calibration_only: bool = False,
        write_video: Optional[bool] = None,
        create_video_task: Optional[bool] = None,
        record_schedule_id: Optional[str] = None,
    ) -> dict:
        """
        启动摄像头推流 + 拉帧 + 可选写视频。

        URL 语义：
          * ``camera_push_url`` = 传给树莓派的推流目标（必须能由树莓派访问）。
            来自 ``FISH_CAMERA_PUSH_URL`` 或自动推断主机局域网 IP；
            可被前端 ``stream_config.stream_url`` 显式覆盖。
          * ``camera_read_url`` = 后端 OpenCV 在主机本机读取 MediaMTX 的地址。
            来自 ``FISH_CAMERA_READ_URL``，默认 ``rtmp://127.0.0.1:1935/fish_camera``；
            不接受前端覆盖。
        """
        # 参数归一化。页面保存的设置作为默认值，单次请求参数优先。
        saved_settings = self.get_saved_settings()
        stream_config = {**saved_settings, **(stream_config or {})}
        self._force_all_complete = bool(stream_config.get("force_all_complete", False))
        if write_video is None:
            write_video = not calibration_only  # 标定模式默认不写视频
        if create_video_task is None:
            create_video_task = not calibration_only  # 标定模式默认不建 video_task

        # 校验定时录制参数；非法直接抛 ValueError 由 router 转 status=error
        duration_minutes = self._parse_duration_minutes(stream_config)
        if write_video and create_video_task:
            storage = ensure_recording_capacity(
                duration_minutes * 60 if duration_minutes else None
            )
            if not storage.get("recording_allowed"):
                raise ValueError(storage.get("message") or "磁盘空间不足，无法开始录像")

        # 记录本次会话关联的 future 计划 ID。
        # preview 不得携带计划；只有正式录制 + write_video + create_video_task 才保存。
        if (
            record_schedule_id
            and not calibration_only
            and write_video
            and create_video_task
        ):
            self._record_schedule_id = record_schedule_id
        else:
            self._record_schedule_id = None

        runtime = get_pi_runtime_config()

        with self._lock:
            if self.state in (
                CameraRuntimeState.STARTING,
                CameraRuntimeState.RUNNING,
                CameraRuntimeState.RECONNECTING,
                CameraRuntimeState.STOPPING,
            ):
                return {
                    "status": self.state.value,
                    "message": f"camera is {self.state.value}",
                    "video_id": self._video_id,
                    "stream_url": self.stream_url,
                    "camera_push_url": self.camera_push_url,
                    "camera_read_url": self.camera_read_url,
                    "record_duration_seconds": self._record_duration_seconds,
                    "scheduled_stop_at": (
                        self._scheduled_stop_at.isoformat()
                        if self._scheduled_stop_at
                        else None
                    ),
                    "remaining_seconds": self._compute_remaining_seconds(),
                    "timed_recording": self._scheduled_stop_at is not None,
                    "auto_stop_triggered": self._auto_stop_triggered,
                }

            self.state = CameraRuntimeState.STARTING
            self.is_active = False
            self._stop_event.clear()

            self._release_runtime_resources()

            # ---- 拆分 push / read ----
            if (
                stream_config
                and stream_config.get("stream_url")
                and not is_camera_push_url_automatic()
            ):
                # 前端显式传入时只能覆盖 push_url（用于树莓派推流目标）；
                # 不允许前端覆盖 capture_url（后端读流地址）。
                self.camera_push_url = str(stream_config["stream_url"]).strip() or (
                    self._get_camera_push_url()
                )
            else:
                self.camera_push_url = self._get_camera_push_url()
            self.camera_read_url = self._get_camera_read_url()

            self.stream_url = self.camera_push_url  # 兼容老字段
            self.capture_url = self.camera_read_url

            self.width = (
                int(stream_config["width"]) if stream_config and stream_config.get("width") else 960
            )
            self.height = (
                int(stream_config["height"])
                if stream_config and stream_config.get("height")
                else 540
            )
            self.fps = (
                int(stream_config["fps"]) if stream_config and stream_config.get("fps") else self.FPS
            )

            # 对焦参数（会话级，供 _capture_loop 拼装 rasp_payload 使用）
            self._autofocus_mode = (
                str(stream_config["autofocus_mode"])
                if stream_config and stream_config.get("autofocus_mode")
                else None
            )
            self._lens_position = (
                float(stream_config["lens_position"])
                if stream_config and stream_config.get("lens_position") is not None
                else None
            )
            self._autofocus_window = (
                str(stream_config["autofocus_window"])
                if stream_config and stream_config.get("autofocus_window")
                else None
            )

            self._frames_captured = 0
            self._frames_written = 0
            self._capture_started_monotonic = None
            self._recording_started_at = None
            self._last_error = None
            self._reconnect_started_at = None
            self._reconnect_timeout_finalization_started = False
            self._startup_error = None
            self._stream_ready = False
            self._startup_phase = "init"
            self.last_frame_bytes = None
            self._writer_width = 0
            self._writer_height = 0
            self._read_fail_count = 0
            self._stop_origin = "user_start"
            # 定时录制：默认 None（未设置）
            self._record_duration_seconds = None
            self._scheduled_stop_at = None
            self._auto_stop_triggered = False

            self._record_event(
                "start_stream_called",
                calibration_only=bool(calibration_only),
                write_video=bool(write_video),
                create_video_task=bool(create_video_task),
                camera_push_url=self.camera_push_url,
                camera_read_url=self.camera_read_url,
                pi_base_url=self._get_pi_base_url(),
            )
            self._record_event(
                "camera_push_url_selected", value=self.camera_push_url
            )
            self._record_event(
                "camera_read_url_selected", value=self.camera_read_url
            )

            # 标定模式：跳过 video_task 与 _video_path；用模式标记让
            # _capture_loop 不创建 cv2.VideoWriter；stop_stream 不入队算法。
            if create_video_task:
                db = SessionLocal()
                try:
                    self._video_id = generate_video_id(db)
                finally:
                    db.close()

                VID_DIR.mkdir(parents=True, exist_ok=True)
                self._video_path = str(VID_DIR / f"{self._video_id}{self.VIDEO_EXT}")

                from app.services.path_service import to_data_relative

                db_source_path = to_data_relative(self._video_path)

                db = SessionLocal()
                try:
                    session_repo.create_video_task(
                        db,
                        video_id=self._video_id,
                        source_path=db_source_path,
                        rtsp_url=self.camera_push_url,
                    )
                    session_repo.update_video_task_status(
                        db,
                        video_id=self._video_id,
                        status="recording",
                    )
                    # 第一次写入定时录制字段（scheduled_stop_at 暂时为 None，
                    # 完整 scheduled_stop_at 将在下面 started_at 计算完后再写一次）。
                    record_duration_seconds = (
                        duration_minutes * 60 if duration_minutes else None
                    )
                    session_repo.update_video_task_recording_schedule(
                        db,
                        video_id=self._video_id,
                        record_duration_seconds=record_duration_seconds,
                        scheduled_stop_at=None,
                        auto_stop_triggered=False,
                    )
                except Exception as e:
                    db.rollback()
                    self.state = CameraRuntimeState.ERROR
                    self._last_error = str(e)
                    self._startup_error = str(e)
                    self._startup_phase = "failed"
                    raise
                finally:
                    db.close()
            else:
                # 标定模式：不创建 video_task，_video_id/_video_path 留空
                self._video_id = None
                self._video_path = None

            # 标定模式额外标志：写视频开关（被 _capture_loop 检查）
            # 用一个实例属性保存本次会话的"写视频"标志
            self._write_video_enabled = bool(write_video)
            self._calibration_only = bool(calibration_only)
            self._session_create_video_task = bool(create_video_task)
            # 标定模式只等 2 秒就尝试开流；普通录制保留 10 秒让推流稳定
            self._camera_start_delay = 2 if calibration_only else self.CAMERA_START_DELAY

            from app.utils.time_utils import now_beijing

            self._started_at = now_beijing()
            self.is_active = True

            # 定时录制（关键：延迟武装）
            # - start_stream 收到 duration_minutes 时立刻记录 record_duration_seconds 和 DB 字段；
            # - 但 scheduled_stop_at 留空（前端看到 remaining_seconds=null / timer_pending=true）；
            # - watchdog 不在这里启动；
            # - 等 _capture_loop 真正收到第一帧 / 写入视频，再调用 _arm_timed_recording_if_ready()
            #   来"武装" scheduled_stop_at 和 watchdog，从而避免把启动等待时间算进用户录制时长。
            if duration_minutes:
                self._record_duration_seconds = duration_minutes * 60
                # scheduled_stop_at 留空，等首帧后 arm
                self._scheduled_stop_at = None
                self._timer_pending = True
                self._timer_armed_at = None
                self._auto_stop_triggered = False
                # DB 字段：先记录 record_duration_seconds；scheduled_stop_at 仍为 None
                if self._video_id and create_video_task:
                    db2 = SessionLocal()
                    try:
                        session_repo.update_video_task_recording_schedule(
                            db2,
                            video_id=self._video_id,
                            record_duration_seconds=self._record_duration_seconds,
                            scheduled_stop_at=None,
                            auto_stop_triggered=False,
                        )
                    except Exception as e:
                        print(
                            f"[camera_yzt] WARN: 写入定时录制字段失败: {e!r}"
                        )
                    finally:
                        db2.close()
                self._record_event(
                    "timed_recording_pending",
                    duration_minutes=duration_minutes,
                    record_duration_seconds=self._record_duration_seconds,
                    note="scheduled_stop_at/watchdog 在收到首帧后再武装",
                )
            else:
                # 没有 duration_minutes：保持 None；不会启动 timer
                self._record_duration_seconds = None
                self._scheduled_stop_at = None
                self._timer_pending = False
                self._timer_armed_at = None

            print(
                f"[camera_yzt] start_stream: camera_push_url={self.camera_push_url}"
            )
            print(
                f"[camera_yzt] start_stream: camera_read_url={self.camera_read_url}"
            )
            if self._record_duration_seconds:
                print(
                    f"[camera_yzt] start_stream: timed recording pending "
                    f"(duration={self._record_duration_seconds}s, timer_pending=True, "
                    f"scheduled_stop_at 等首帧后设置)"
                )

            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="camera-yzt-capture",
            )
            self._capture_thread.start()

            # 注意：定时录制 watchdog 不在 start_stream 末尾直接启动；
            # 必须等第一帧成功（_capture_loop）后通过
            # _arm_timed_recording_if_ready() 触发，再调用 _start_timed_recording_watchdog()。

            return {
                "status": "starting",
                "video_id": self._video_id,
                "stream_url": self.stream_url,
                "camera_push_url": self.camera_push_url,
                "camera_read_url": self.camera_read_url,
                "video_path": self._video_path,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "calibration_only": bool(calibration_only),
                # 定时录制字段
                "record_duration_seconds": self._record_duration_seconds,
                "scheduled_stop_at": (
                    self._scheduled_stop_at.isoformat()
                    if self._scheduled_stop_at
                    else None
                ),
                "remaining_seconds": self._compute_remaining_seconds(),
                "timed_recording": self._scheduled_stop_at is not None,
                "timer_pending": self._timer_pending,
                "timer_armed_at": (
                    self._timer_armed_at.isoformat()
                    if self._timer_armed_at
                    else None
                ),
                "auto_stop_triggered": False,
                "storage": get_storage_health(),
            }

    def _capture_loop(self) -> None:
        """后台线程：通知 Pi 推流 → 等 OpenCV 读到第一帧 → 主循环读帧 + 写视频。

        关键点：
            * 推流目标（camera_push_url）只用于通知树莓派。
            * 后端 OpenCV 永远读 camera_read_url（默认 127.0.0.1）。
            * 启动失败必须进入 ERROR，绝不静默回 IDLE。
            * frames_written=0 时不入队算法（由 stop_stream 控制）。
        """
        read_url = self.camera_read_url
        push_url = self.camera_push_url
        video_path = self._video_path

        print(
            f"[camera_yzt] capture_loop: notify pi start stream_url={push_url}"
        )
        print(
            f"[camera_yzt] capture_loop: opencv read url={read_url}"
        )

        rasp_payload = {
            "stream_url": push_url,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
        }
        if getattr(self, "_autofocus_mode", None):
            rasp_payload["autofocus_mode"] = self._autofocus_mode
        if getattr(self, "_lens_position", None) is not None:
            rasp_payload["lens_position"] = self._lens_position
        if getattr(self, "_autofocus_window", None):
            rasp_payload["autofocus_window"] = self._autofocus_window

        def request_pi_reconnect() -> None:
            """Ask the publisher to resume without sending a stop command."""
            try:
                reconnect_result = self._post_pi_start(rasp_payload, timeout=10.0)
                self._record_event(
                    "pi_reconnect_requested",
                    ok=bool(reconnect_result.get("ok")),
                    status_code=reconnect_result.get("status_code"),
                    already_running=bool(reconnect_result.get("already_running")),
                    error=reconnect_result.get("error"),
                )
            except Exception as exc:  # noqa: BLE001
                self._record_event("pi_reconnect_request_failed", error=repr(exc))

        # ===== 1) 先查 Pi 端状态，决定是否复用已有推流 =====
        reused_existing = False
        reuse_reason = ""
        desync_error: Optional[str] = None
        pi_status_for_log = {"reachable": False}

        self._startup_phase = "query_pi_status"
        print("[camera_yzt] 查询树莓派状态...")
        pi_status = self._request_pi_status(timeout=5.0)
        pi_status_for_log = pi_status
        self._record_event("pi_status_before_start", reachable=bool(pi_status.get("ok")))

        if pi_status.get("ok") and pi_status.get("running"):
            pi_stream_url = pi_status.get("stream_url")
            if pi_stream_url and pi_stream_url == push_url:
                reused_existing = True
                reuse_reason = "Pi 已按相同 stream_url 推流，复用现有推流"
                print(f"[camera_yzt] {reuse_reason}")
                self._last_error = None
            else:
                # Pi 只返回 running=true 的常见情况：用 read_url 探测
                print(
                    "[camera_yzt] Pi 已 running，但未返回 stream_url；"
                    f"探测 expected read_url: {read_url}"
                )
                if self._probe_expected_stream(read_url, attempts=3, delay=1.0):
                    reused_existing = True
                    reuse_reason = (
                        "Pi 已 running，且 expected read_url 可打开，复用现有推流"
                    )
                    print(f"[camera_yzt] {reuse_reason}")
                    self._last_error = None
                else:
                    desync_error = (
                        "树莓派显示已有推流，但服务器无法打开期望视频流："
                        f"{read_url}。请检查 MediaMTX/FFmpeg/网络，或点击强制重启摄像头。"
                    )
                    print(f"[camera_yzt] desync detected: {desync_error}")

        # ===== 2) 必要时调用 POST /camera/start =====
        if not reused_existing and desync_error is None:
            print(
                f"[camera_yzt] 通知树莓派开始推流: "
                f"{self._get_pi_base_url()}/camera/start"
            )
            self._startup_phase = "notify_pi_start"
            pi_start = self._post_pi_start(rasp_payload, timeout=10.0)
            self._record_event(
                "pi_start_posted",
                ok=bool(pi_start.get("ok")),
                status_code=pi_start.get("status_code"),
                already_running=bool(pi_start.get("already_running")),
                error=pi_start.get("error"),
            )
            print(
                f"[camera_yzt] POST /camera/start -> status={pi_start.get('status_code')}, "
                f"ok={pi_start.get('ok')}, already_running={pi_start.get('already_running')}"
            )

            if pi_start.get("ok"):
                self._last_error = None
            elif pi_start.get("already_running"):
                recheck = self._request_pi_status(timeout=5.0)
                if recheck.get("ok") and recheck.get("running"):
                    if self._probe_expected_stream(read_url, attempts=3, delay=1.0):
                        reused_existing = True
                        reuse_reason = (
                            "Pi 已 running（POST 报 already_running）"
                            "且 expected read_url 可打开，复用现有推流"
                        )
                        print(f"[camera_yzt] {reuse_reason}")
                        self._last_error = None
                    else:
                        desync_error = (
                            "树莓派返回 stream already running，但服务器无法打开期望视频流："
                            f"{read_url}。请强制重启摄像头。"
                        )
                else:
                    desync_error = (
                        "树莓派返回 stream already running，但重新查询已不运行，"
                        "状态异常，请强制重启摄像头。"
                    )
            else:
                err_text = pi_start.get("error") or (
                    f"HTTP {pi_start.get('status_code')}: {pi_start.get('text')}"
                )
                self._last_error = f"树莓派摄像头启动失败: {err_text}"
                print(f"[camera_yzt] {self._last_error}")

        # ===== 3) desync 时不继续录制空视频，进入 ERROR 并退出 =====
        if desync_error is not None and not reused_existing:
            self._startup_error = desync_error
            self._startup_phase = "failed"
            self._last_error = desync_error
            self._fail_current_video_task(reason=desync_error)
            self._record_event("capture_error", reason=desync_error)
            with self._lock:
                self.state = CameraRuntimeState.ERROR
                self.is_active = False
            self._stop_event.set()
            print(f"[camera_yzt] capture_loop 因 desync 提前结束: {desync_error}")
            return

        # ===== 4) 等推流稳定 =====
        delay = getattr(self, "_camera_start_delay", self.CAMERA_START_DELAY)
        self._startup_phase = "wait_stream_stable"
        print(
            f"[camera_yzt] wait stream readable: read_url={read_url}, delay={delay}s"
        )
        if self._stop_event.wait(delay):
            print("[camera_yzt] capture_loop 在等待推流时被 stop_event 终止")
            return

        # ===== 5) 等 OpenCV 真正读到第一帧 =====
        self._startup_phase = "wait_first_frame"
        self._record_event("stream_wait_begin", read_url=read_url)
        if not self._wait_until_stream_readable(
            read_url,
            timeout_seconds=max(
                STREAM_READY_TIMEOUT_SECONDS,
                self._reconnect_grace_seconds,
            ),
            on_reconnect=request_pi_reconnect,
        ):
            if self._stop_event.is_set():
                return
            self._handle_stream_read_failure(read_url, push_url)
            return

        # ===== 6) 主循环 =====
        self._startup_phase = "running"
        with self._lock:
            if self.state == CameraRuntimeState.STARTING:
                self.state = CameraRuntimeState.RUNNING
                self.is_active = True
        self._mark_stream_recovered()
        self._record_event("stream_readable", read_url=read_url)

        reconnect_delay = 1.0
        max_reconnect_delay = 30.0
        loop_error: Optional[Exception] = None
        stream_lost_at: Optional[float] = None
        last_reconnect_request_at: Optional[float] = None

        try:
            while not self._stop_event.is_set():
                if self._cap is None or not self._cap.isOpened():
                    if self._cap is not None:
                        try:
                            self._cap.release()
                        except Exception:
                            pass
                        self._cap = None

                    open_ok = False
                    for attempt in range(1, 11):
                        print(
                            f"[camera_yzt] 尝试连接流 {attempt}/10: read_url={read_url}"
                        )
                        cap = cv2.VideoCapture(read_url)
                        if cap.isOpened():
                            ret_probe, frame_probe = cap.read()
                            if ret_probe and frame_probe is not None:
                                self._cap = cap
                                open_ok = True
                                if stream_lost_at is not None:
                                    self._stream_ready = True
                                    self._startup_phase = "running"
                                    self._last_error = None
                                    self.state = CameraRuntimeState.RUNNING
                                    self.is_active = True
                                    self._reconnect_started_at = None
                                    self._record_event("stream_recovered")
                                    stream_lost_at = None
                                print(
                                    f"[camera_yzt] stream reopened: read_url={read_url}"
                                )
                                break
                        cap.release()
                        time.sleep(1)

                    if not open_ok:
                        now_monotonic = time.monotonic()
                        if stream_lost_at is None:
                            stream_lost_at = now_monotonic
                            self._begin_reconnect_wait("stream_reopen_failed")
                        if (
                            last_reconnect_request_at is None
                            or now_monotonic - last_reconnect_request_at
                            >= self._reconnect_request_interval_seconds
                        ):
                            request_pi_reconnect()
                            last_reconnect_request_at = now_monotonic
                        if (
                            now_monotonic - stream_lost_at
                            < self._reconnect_grace_seconds
                        ):
                            if self._stop_event.wait(reconnect_delay):
                                break
                            reconnect_delay = min(
                                reconnect_delay * 2, max_reconnect_delay
                            )
                            continue
                        msg = (
                            f"OpenCV 无法从 {read_url} 读取视频帧；"
                            "MediaMTX 可能已收到树莓派推流，但后端无法读取，"
                            "请检查 FISH_CAMERA_READ_URL、OpenCV FFmpeg/RTMP 支持，"
                            "并用 ffmpeg/ffprobe 验证该地址是否可读。"
                        )
                        self._record_event("capture_error", reason=msg)
                        self._last_error = msg
                        self._startup_error = msg
                        self._startup_phase = "failed"
                        self._stream_ready = False
                        self._finalize_stream_after_reconnect_timeout(reason=msg)
                        return

                    reconnect_delay = 1.0

                ret, frame = self._cap.read()
                if not ret:
                    self._read_fail_count += 1
                    self._last_error = "读取帧失败"
                    self._record_event(
                        "read_frame_failed", count=self._read_fail_count
                    )
                    print(
                        f"[camera_yzt] {self._last_error} "
                        f"(连续 {self._read_fail_count} 次)..."
                    )
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    self._cap = None

                    if self._read_fail_count >= self.READ_FAIL_THRESHOLD:
                        now_monotonic = time.monotonic()
                        if stream_lost_at is None:
                            stream_lost_at = now_monotonic
                            self._begin_reconnect_wait("read_frame_failed")
                        if (
                            last_reconnect_request_at is None
                            or now_monotonic - last_reconnect_request_at
                            >= self._reconnect_request_interval_seconds
                        ):
                            request_pi_reconnect()
                            last_reconnect_request_at = now_monotonic
                        if (
                            now_monotonic - stream_lost_at
                            >= self._reconnect_grace_seconds
                        ):
                            msg = (
                                f"OpenCV 连续 {self._read_fail_count} 次从 {read_url} "
                                f"读取帧失败，超过 {self._reconnect_grace_seconds:.0f} 秒恢复等待窗口。"
                            )
                            self._record_event("capture_error", reason=msg)
                            self._last_error = msg
                            self._startup_error = msg
                            self._startup_phase = "failed"
                            self._stream_ready = False
                            self._finalize_stream_after_reconnect_timeout(reason=msg)
                            break

                    if self._stop_event.wait(reconnect_delay):
                        break
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    continue

                self._read_fail_count = 0
                reconnect_delay = 1.0
                self._frames_captured += 1
                if self._capture_started_monotonic is None:
                    self._capture_started_monotonic = time.monotonic()
                    from app.utils.time_utils import now_beijing

                    self._recording_started_at = now_beijing()
                    if self._video_id and self._session_create_video_task:
                        first_frame_db = SessionLocal()
                        try:
                            session_repo.mark_video_task_recording_started(
                                first_frame_db,
                                video_id=self._video_id,
                                recording_started_at=self._recording_started_at,
                            )
                        except Exception as first_frame_error:
                            first_frame_db.rollback()
                            print(
                                "[camera_yzt] WARN: 写入录像首帧时间失败: "
                                f"{first_frame_error!r}"
                            )
                        finally:
                            first_frame_db.close()
                # 延迟武装：收到第一帧后，立即设置 scheduled_stop_at 和启动 watchdog。
                # 条件内部已判断（preview / calibration 不参与）。
                try:
                    self._arm_timed_recording_if_ready(reason="first_frame_captured")
                except Exception as arm_err:
                    print(f"[camera_yzt] WARN: 武装定时录制失败: {arm_err!r}")

                _, jpeg = cv2.imencode('.jpg', frame)
                self.last_frame_bytes = jpeg.tobytes()
                if (
                    self._video_id
                    and self._video_path
                    and getattr(self, "_write_video_enabled", True)
                ):
                    recorder_status = segmented_recording_service.status()
                    if not recorder_status.get("running"):
                        try:
                            recorder_status = segmented_recording_service.start(
                                self._video_id, read_url
                            )
                            self._record_event(
                                "segmented_recording_started",
                                video_id=self._video_id,
                                pid=recorder_status.get("pid"),
                            )
                        except Exception as recorder_error:
                            raise RuntimeError(
                                f"启动断电保护分段录制失败：{recorder_error}"
                            ) from recorder_error
                    # 保持旧 API 的 frames_written 非零语义；真实落盘状态见 recording_segments。
                    self._frames_written = max(self._frames_written, self._frames_captured)

                if self._frames_captured % 100 == 1:
                    print(f"[camera_yzt] 已捕获帧: {self._frames_captured}, 已写入: {self._frames_written}")
                if (
                    self._frames_captured % max(1, int(self.fps * 20)) == 1
                    and getattr(self, "_write_video_enabled", True)
                ):
                    storage = get_storage_health()
                    if storage.get("level") == "block" and not self._storage_stop_started:
                        self._storage_stop_started = True
                        self._record_event(
                            "storage_emergency_stop",
                            free_bytes=storage.get("free_bytes"),
                            message=storage.get("message"),
                        )
                        threading.Thread(
                            target=self.stop_stream,
                            kwargs={
                                "reason": "storage_space_exhausted",
                                "source": "storage_health_monitor",
                                "queue_algorithm": True,
                            },
                            name="camera-storage-emergency-stop",
                            daemon=True,
                        ).start()
        except Exception as e:
            loop_error = e
            print(f"[camera_yzt] 捕获循环异常: {e}")
            self._record_event("capture_error", reason=str(loop_error))
        finally:
            self._release_runtime_resources()
            # 无论是用户停止、断流超时还是捕获线程异常，都要结束独立
            # ffmpeg 进程，避免只剩后台录制进程继续占用摄像头流。
            segmented_recording_service.stop(timeout=15.0)

            measured_duration_seconds = None
            if video_path and Path(video_path).is_file():
                try:
                    from app.services.video_playback_service import probe_media_duration_seconds

                    measured_duration_seconds = probe_media_duration_seconds(video_path)
                except Exception as exc:
                    print(f"[camera_yzt] probe original duration failed: {exc!r}")
            with self._lock:
                if self.state == CameraRuntimeState.STOPPING:
                    pass
                elif self.state == CameraRuntimeState.ERROR:
                    pass
                elif loop_error is not None:
                    self.state = CameraRuntimeState.ERROR
                    self._last_error = str(loop_error)
                    self._startup_error = str(loop_error)
                    self._startup_phase = "failed"
                    self._fail_current_video_task(reason=str(loop_error))
                elif (
                    self._stop_origin
                    and self._stop_origin not in ("user_start", "")
                ):
                    # stop_stream 被显式调用（用户主动 / 失败后清理）：
                    # 不在这里改 state，由 stop_stream 自己管理。
                    pass
                else:
                    # 没有任何 stop 原因 → 视为异常退出，进入 ERROR 而不是 IDLE
                    self.state = CameraRuntimeState.ERROR
                    self._last_error = "摄像头捕获循环异常退出"
                    self._startup_error = self._last_error
                    self._startup_phase = "failed"
                self.is_active = False

        print("[camera_yzt] 捕获循环已停止")

    def _wait_until_stream_readable(
        self,
        read_url: str,
        timeout_seconds: float = STREAM_READY_TIMEOUT_SECONDS,
        on_reconnect: Optional[Callable[[], None]] = None,
    ) -> bool:
        """等到 OpenCV 真正读到第一帧。

        返回 True 表示成功读到第一帧，并把 ``self._cap`` 保留给主循环使用。
        返回 False 表示超时，期间不会修改 ``self._cap``。
        """
        deadline = time.time() + max(2.0, timeout_seconds)
        attempt = 0
        last_reconnect_request_at: Optional[float] = None

        while time.time() < deadline and not self._stop_event.is_set():
            attempt += 1
            print(
                f"[camera_yzt] wait stream readable attempt={attempt}, read_url={read_url}"
            )
            cap: Optional[cv2.VideoCapture] = None
            try:
                cap = cv2.VideoCapture(read_url)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        self._cap = cap
                        cap = None  # 已转交所有权
                        self._mark_stream_recovered()
                        print(
                            f"[camera_yzt] stream readable: read_url={read_url}, "
                            f"frame_shape={frame.shape}"
                        )
                        return True
            except Exception as exc:
                print(f"[camera_yzt] wait stream readable exception: {exc!r}")
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass

            self._begin_reconnect_wait("initial_stream_unreadable")
            now_monotonic = time.monotonic()
            if (
                on_reconnect is not None
                and (
                    last_reconnect_request_at is None
                    or now_monotonic - last_reconnect_request_at
                    >= self._reconnect_request_interval_seconds
                )
            ):
                try:
                    on_reconnect()
                finally:
                    last_reconnect_request_at = now_monotonic

            # 1s 内响应 stop_event
            if self._stop_event.wait(1.0):
                return False

        return False

    def _handle_stream_read_failure(self, read_url: str, push_url: str) -> None:
        """首帧恢复窗口耗尽后封存任务；有有效视频时继续入队处理。"""
        msg = (
            f"MediaMTX 可能已收到树莓派推流（push_url={push_url}），"
            f"但后端无法从 {read_url} 读取视频帧。"
            "请检查：\n"
            "  1. FISH_CAMERA_READ_URL 是否正确（默认 rtmp://127.0.0.1:1935/fish_camera）；\n"
            "  2. OpenCV 是否具备 FFmpeg/RTMP 支持（可先 `ffmpeg -i <read_url> -f null -` 验证）；\n"
            "  3. MediaMTX 路径 / 协议 / 编码是否正常（看 data/logs/mediamtx.log）。"
        )
        self._record_event("stream_wait_timeout", read_url=read_url, push_url=push_url)
        self._last_error = msg
        self._startup_error = msg
        self._startup_phase = "failed"
        self._stream_ready = False
        # Do not stop the Pi here. A missing stream can be a temporary camera
        # power/network loss; the user or the reconnect loop must be allowed to
        # bring the publisher back without destroying the camera session.
        self._record_event("startup_stream_wait_expired_without_pi_stop")

        self._finalize_stream_after_reconnect_timeout(reason=msg)

    def _fail_current_video_task(self, *, reason: str) -> None:
        vid = self._video_id
        if not vid:
            return
        try:
            db = SessionLocal()
            try:
                session_repo.update_video_task_status(
                    db,
                    video_id=vid,
                    status="failed",
                    last_error=reason,
                )
            finally:
                db.close()
            self._record_event("video_task_failed", video_id=vid, reason=reason)
        except Exception as e:
            print(f"[camera_yzt] mark video_task failed failed: {e!r}")

    def _finalize_stream_after_reconnect_timeout(self, *, reason: str) -> None:
        """Close a timed-out recording and process it when a usable file exists."""
        with self._lock:
            if self._reconnect_timeout_finalization_started:
                return
            self._reconnect_timeout_finalization_started = True
            video_id = self._video_id
            frames_written = self._frames_written
            self.state = CameraRuntimeState.ERROR
            self.is_active = False
            self._stream_ready = False
            self._startup_phase = "failed"
            self._last_error = reason
            self._startup_error = reason
            self._stop_event.set()

        self._record_event(
            "stream_reconnect_timeout",
            video_id=video_id,
            frames_written=frames_written,
            reason=reason,
            partial_recording=bool(frames_written > 0),
        )

        # Keep the timeout reason while stop_stream finalizes the file. A valid
        # partial recording is then queued under the original video_id; a
        # missing/empty file is marked failed by stop_stream itself.
        if video_id:
            try:
                db = SessionLocal()
                try:
                    session_repo.update_video_task_status(
                        db,
                        video_id=video_id,
                        status="recording",
                        last_error=reason,
                    )
                finally:
                    db.close()
            except Exception as exc:  # noqa: BLE001
                self._record_event(
                    "stream_reconnect_timeout_reason_persist_failed",
                    video_id=video_id,
                    error=repr(exc),
                )

        def finalize() -> None:
            try:
                result = self.stop_stream(
                    queue_algorithm=True,
                    update_video_task=True,
                    reason="stream_reconnect_timeout",
                    source="camera_reconnect",
                )
                self._record_event(
                    "stream_reconnect_timeout_finalized",
                    video_id=video_id,
                    queued=bool(result.get("queued")),
                    job_id=result.get("job_id"),
                    frames_written=frames_written,
                    queue_error=result.get("queue_error"),
                )
            except Exception as exc:  # noqa: BLE001
                self._record_event(
                    "stream_reconnect_timeout_finalize_failed",
                    video_id=video_id,
                    error=repr(exc),
                )

        threading.Thread(
            target=finalize,
            name=f"camera-reconnect-timeout-finalizer-{video_id or 'none'}",
            daemon=True,
        ).start()

    def stop_stream(
        self,
        *,
        queue_algorithm: bool = True,
        update_video_task: bool = True,
        remove_video_task: bool = False,
        reason: str = "normal",
        source: str = "",
        auto_stop_triggered: bool = False,
        join_timeout: float = 10.0,
        raspberry_stop_timeout: float = 10.0,
    ) -> dict:
        print(
            f"[camera_yzt] stop_stream called: reason={reason!r}, source={source!r}, "
            f"auto_stop_triggered={auto_stop_triggered}"
        )

        # 局部快照：在重置 state 前保存本次会话关联的 plan / video_id，
        # 供 stop_stream 末尾（锁外）异步更新计划状态使用。
        # 不再使用 self._stopped_* 实例级字段，避免与并发 stop_stream 调用互相覆盖。
        stopped_record_schedule_id = self._record_schedule_id
        stopped_video_id = self._video_id
        stopped_recording_started_at = self._recording_started_at
        stopped_actual_stop_at = __import__('datetime').datetime.now(
            __import__('datetime').timezone.utc
        )
        # 本次 stop_stream 是否正常完成；异常路径置 False，_flush_schedule_after_stop 据此区分 completed / failed。
        stop_completed = False
        stop_exception: Optional[BaseException] = None
        # 取消 watchdog：内部会判断是否自身线程，不会 join 自己。
        self._stop_timed_recording_watchdog(
            reason=f"stop_stream:{reason or 'unknown'}"
        )
        self._record_event(
            "stop_stream_called",
            reason=reason,
            source=source,
            auto_stop_triggered=bool(auto_stop_triggered),
            queue_algorithm=bool(queue_algorithm),
            update_video_task=bool(update_video_task),
        )
        self._stop_origin = reason or "user_stop"
        with self._lock:
            if self.state == CameraRuntimeState.IDLE:
                # 后端 idle，但 Pi 端可能仍残留推流 → 主动调用 /camera/stop 清理
                pi_stop = self._post_pi_stop(timeout=raspberry_stop_timeout)
                pi_status_after = self._request_pi_status(timeout=5.0)
                self._record_event(
                    "pi_stop_posted",
                    when="idle_cleanup",
                    ok=bool(pi_stop.get("ok")),
                    status_code=pi_stop.get("status_code"),
                )
                print(
                    f"[camera_yzt] idle stop: pi_stop_status={pi_stop.get('status_code')}, "
                    f"pi_stop_error={pi_stop.get('error')}, "
                    f"pi_running_after={pi_status_after.get('running')}"
                )
                return {
                    "status": "not_running",
                    "video_id": None,
                    "video_path": None,
                    "original_video_path": None,
                    "frames_captured": 0,
                    "frames_written": 0,
                    "fish_detected": 0,
                    "raspberry_stop_status": pi_stop.get("status_code") or "error",
                    "raspberry_stop_error": pi_stop.get("error"),
                    "transcoded_status": "not_needed",
                    "queued": False,
                    "job_id": None,
                    "queue_error": None,
                    "queue_message": (
                        "backend idle, raspberry stop called to clear stale stream"
                    ),
                    "db_update_error": None,
                    "note": "后端未运行，但已尝试清理树莓派残留推流",
                    "pi_stop_called": True,
                    "pi_status_after": pi_status_after,
                    # 定时录制字段（idle 状态下运行态已重置，保持 None 即可）
                    "record_duration_seconds": None,
                    "scheduled_stop_at": None,
                    "auto_stop_triggered": False,
                }

            if self.state == CameraRuntimeState.STOPPING:
                return {
                    "status": "stopping",
                    "video_id": self._video_id,
                    "video_path": self._video_path,
                    "original_video_path": None,
                    "frames_captured": self._frames_captured,
                    "frames_written": self._frames_written,
                    "fish_detected": 0,
                    "raspberry_stop_status": "not_sent",
                    "raspberry_stop_error": None,
                    "transcoded_status": "not_needed",
                    "queued": False,
                    "job_id": None,
                    "queue_error": None,
                    "queue_message": "camera is stopping",
                    "db_update_error": None,
                    "note": "camera is already stopping",
                    "pi_stop_called": False,
                    "pi_status_after": None,
                }

            self.state = CameraRuntimeState.STOPPING
            self.is_active = False
            self._stop_event.set()

            video_id = self._video_id
            video_path = self._video_path
            stopped_frames_captured = self._frames_captured
            stopped_frames_written = self._frames_written
            # 定时录制相关字段：保存快照以便 finally 重置后仍能返回给调用方
            stopped_record_duration_seconds = self._record_duration_seconds
            stopped_scheduled_stop_at = self._scheduled_stop_at
            stopped_auto_stop_triggered = (
                bool(auto_stop_triggered) or bool(self._auto_stop_triggered)
            )
            # 如果是 watchdog 触发的，确保实例标志为 True（便于状态查询）
            self._auto_stop_triggered = stopped_auto_stop_triggered

        transcoded_path = video_path
        transcoded_status = "not_needed"
        measured_duration_seconds: Optional[float] = None
        rasp_stop_status: int | str = "not_sent"
        rasp_stop_error = None
        db_update_error: Optional[str] = None
        # 提前初始化：异常分支的 return 也会引用这两个变量，
        # 必须在 try 块之前给默认值，避免 finally 之前的异常导致 UnboundLocalError。
        pi_stop_called_norm: Optional[bool] = False
        pi_status_after_norm: Optional[dict] = None

        try:
            if self._capture_thread and self._capture_thread.is_alive():
                self._capture_thread.join(timeout=join_timeout)

            self._release_runtime_resources()
            segment_stop = segmented_recording_service.stop(timeout=max(5.0, join_timeout))
            if video_id and video_path:
                finalize_result = segmented_recording_service.finalize(
                    video_id, video_path, cleanup=True
                )
                if finalize_result.get("ok"):
                    stopped_frames_written = max(stopped_frames_written, 1)
                    transcoded_status = "segmented_finalized"
                    self._record_event(
                        "segmented_recording_finalized",
                        video_id=video_id,
                        segment_count=finalize_result.get("segment_count"),
                        bytes=finalize_result.get("bytes"),
                    )
                    try:
                        from app.services.video_playback_service import probe_media_duration_seconds
                        measured_duration_seconds = probe_media_duration_seconds(video_path)
                    except Exception as exc:
                        print(f"[camera_yzt] probe finalized duration failed: {exc!r}")
                elif stopped_frames_written > 0:
                    raise RuntimeError(
                        finalize_result.get("error") or "分段视频合并失败"
                    )

            if video_path and stopped_frames_written > 0 and video_path.endswith(self.VIDEO_EXT):
                # 通过 video_playback_service 生成 H.264 playable 副本
                # 副本存放在 data/uploads/videos/playable/<video_id>_h264.mp4
                # 原始 mp4v 文件保留，不删除。
                try:
                    from app.services.video_playback_service import (
                        ensure_browser_playable_video,
                    )
                    video_id_for_transcode = Path(video_path).stem
                    pb_result = ensure_browser_playable_video(
                        video_id_for_transcode,
                        force_reconvert=False,
                    )
                    if pb_result.get("ok") and pb_result.get("playable_path"):
                        pb_abs = to_abs_data_path(pb_result["playable_path"])
                        if pb_abs and pb_abs.exists() and pb_abs.stat().st_size > 0:
                            transcoded_path = str(pb_abs)
                            if pb_result.get("converted"):
                                transcoded_status = "success"
                                print(
                                    f"[camera_yzt] H.264 playable 已生成: {transcoded_path}"
                                )
                            else:
                                # 已存在副本 / 源已经是 H.264
                                transcoded_status = "success_cached"
                                print(
                                    f"[camera_yzt] 复用 H.264 playable: {transcoded_path}"
                                )
                        else:
                            transcoded_status = "failed: playable file missing"
                            print(
                                f"[camera_yzt] playable file missing: {pb_result.get('playable_path')}"
                            )
                        if transcoded_path and Path(transcoded_path).is_file():
                            try:
                                from app.services.video_playback_service import probe_media_duration_seconds

                                measured_duration_seconds = probe_media_duration_seconds(transcoded_path) or measured_duration_seconds
                            except Exception as exc:
                                print(f"[camera_yzt] probe playable duration failed: {exc!r}")
                    else:
                        err = pb_result.get("error_message") or "unknown"
                        transcoded_status = f"failed: {err[:200]}"
                        print(f"[camera_yzt] H.264 playable 生成失败: {err[:200]}")
                except subprocess.TimeoutExpired:  # noqa: F821
                    transcoded_status = "timeout"
                    print("[camera_yzt] H.264 转码超时")
                except FileNotFoundError:
                    transcoded_status = "ffmpeg_not_found"
                    print("[camera_yzt] ffmpeg 未找到，跳过转码")

            try:
                pi_stop_resp = self._post_pi_stop(timeout=raspberry_stop_timeout)
                rasp_stop_status = pi_stop_resp.get("status_code") or "error"
                rasp_stop_error = pi_stop_resp.get("error")
                self._record_event(
                    "pi_stop_posted",
                    when="user_stop",
                    ok=bool(pi_stop_resp.get("ok")),
                    status_code=pi_stop_resp.get("status_code"),
                )
                print(
                    f"[camera_yzt] A端树莓派stop -> {rasp_stop_status}, "
                    f"{pi_stop_resp.get('text')!r}"
                )
                pi_status_after_norm = self._request_pi_status(timeout=5.0)
                pi_stop_called_norm = True
            except Exception as e:  # noqa: BLE001
                pi_stop_resp = {"ok": False, "status_code": None, "text": "", "error": str(e)}
                rasp_stop_status = "error"
                rasp_stop_error = str(e)
                pi_status_after_norm = {"reachable": False, "ok": False, "running": False}
                pi_stop_called_norm = True
                print(f"[camera_yzt] A端树莓派stop失败: {e}")

            db = SessionLocal()
            try:
                if remove_video_task and video_id:
                    # 标定拍摄场景：start_stream 没创建 video_task，
                    # 但如果会话跨过 start_stream 旧路径残留了 video_id，也直接删除。
                    try:
                        session_repo.delete_video_task(db, video_id=video_id)
                        print(f"[camera_yzt] remove_video_task: deleted {video_id} (reason={reason})")
                    except Exception as e:
                        db_update_error = f"delete_video_task failed: {e}"
                        print(f"[camera_yzt] remove_video_task failed: {e}")
                elif update_video_task:
                    if transcoded_path and Path(transcoded_path).exists() and Path(transcoded_path).stat().st_size > 0:
                        session_repo.update_video_task_status(
                            db, video_id=video_id, status="done"
                        )
                    else:
                        err_msg = (
                            f"video file missing or empty: "
                            f"transcoded_path={transcoded_path!r} "
                            f"original_path={video_path!r}"
                        )
                        session_repo.update_video_task_status(
                            db,
                            video_id=video_id,
                            status="failed",
                            last_error=err_msg,
                        )
                        db_update_error = err_msg

                    # 写入实际停止时间 / 原因 / 来源（定时录制基础数据）。
                    # 手动 stop → auto_stop_triggered=False；
                    # watchdog 触发的 stop → auto_stop_triggered=True。
                    if video_id:
                        try:
                            session_repo.update_video_task_stop_info(
                                db,
                                video_id=video_id,
                                actual_stop_at=stopped_actual_stop_at,
                                stop_reason=reason or "user",
                                stop_source=source or "",
                                auto_stop_triggered=stopped_auto_stop_triggered,
                            )
                        except Exception as e:
                            print(
                                f"[camera_yzt] WARN: 写入 stop info 失败: {e!r}"
                            )

                    if transcoded_path != video_path and video_id:
                        from app.services.path_service import to_data_relative

                        rel_transcoded_path = to_data_relative(transcoded_path)
                        if rel_transcoded_path is not None:
                            session_repo.update_video_task_source_path(
                                db, video_id=video_id, source_path=rel_transcoded_path
                            )
                    if video_id and measured_duration_seconds:
                        session_repo.update_video_task_duration(
                            db,
                            video_id=video_id,
                            video_duration_seconds=measured_duration_seconds,
                        )
            except Exception as e:
                db_update_error = str(e)
                print(f"[camera_yzt] 更新视频状态失败: {e}")
                db.rollback()
            finally:
                db.close()

            queued: bool = False
            queue_error: Optional[str] = None
            job_id: Optional[str] = None
            queue_message: Optional[str] = None
            if not queue_algorithm:
                # 标定拍摄场景：不入队整视频算法。
                queue_message = f"queue_algorithm=False (reason={reason})"
                print(f"[camera_yzt] skip algorithm queue (reason={reason})")
            else:
                try:
                    if (
                        transcoded_path
                        and Path(transcoded_path).exists()
                        and Path(transcoded_path).stat().st_size > 0
                        and stopped_frames_written > 0
                        and video_id
                    ):
                        from app.workers.queue import (
                            enqueue_algorithm_video_pipeline_from_video_task,
                        )

                        print(
                            f"[camera_yzt] video saved: video_id={video_id} "
                            f"path={transcoded_path!r} -> algorithm pipeline queued"
                        )
                        job_id = enqueue_algorithm_video_pipeline_from_video_task(
                            video_id=video_id,
                            input_video=transcoded_path,
                            force_all_complete=self._force_all_complete,
                        )
                        queued = True
                        queue_message = (
                            "已切到整视频算法 CLI pipeline，"
                            "录制 video_id 会沿用到 worker、结果目录、数据库和报表。"
                        )
                        self._record_event(
                            "algorithm_queued", video_id=video_id, job_id=job_id
                        )
                        print(
                            f"[camera_yzt] algorithm pipeline queued: "
                            f"task_video_id={video_id} job_id={job_id}"
                        )
                    elif stopped_frames_written == 0:
                        queue_message = (
                            "视频录制为空（frames_written=0），未入队算法。"
                        )
                        print(f"[camera_yzt] {queue_message}")
                    else:
                        queue_message = "video file missing or empty; pipeline not queued"
                except Exception as e:
                    queue_error = str(e)
                    print(
                        f"[camera_yzt] algorithm pipeline queue failed: video_id={video_id} "
                        f"err={e!r}"
                    )
                    try:
                        db2 = SessionLocal()
                        try:
                            session_repo.update_video_task_status(
                                db2,
                                video_id=video_id,
                                status="failed",
                                last_error=f"algorithm pipeline queue failed: {queue_error}",
                            )
                        finally:
                            db2.close()
                    except Exception:
                        pass

            # 正常完成即将返回：标记 stop_completed=True，
            # _flush_schedule_after_stop 据此把计划状态置为 completed。
            stop_completed = bool(
                video_id
                and stopped_frames_written > 0
                and transcoded_path
                and Path(transcoded_path).is_file()
                and Path(transcoded_path).stat().st_size > 0
            )
            return {
                "status": "stopped",
                "video_id": video_id,
                "video_path": transcoded_path,
                "original_video_path": video_path if transcoded_path != video_path else None,
                "frames_captured": stopped_frames_captured,
                "frames_written": stopped_frames_written,
                "fish_detected": 0,
                "queued": queued,
                "job_id": job_id,
                "queue_error": queue_error,
                "queue_message": queue_message,
                "db_update_error": db_update_error,
                "raspberry_stop_status": rasp_stop_status,
                "raspberry_stop_error": rasp_stop_error,
                "transcoded_status": transcoded_status,
                "note": "已切到 algorithm_video_pipeline，摄像头 video_id 会沿用到算法结果。",
                "pi_stop_called": pi_stop_called_norm,
                "pi_status_after": pi_status_after_norm,
                # 定时录制字段（停止时的快照，finally 重置后仍可读）
                "record_duration_seconds": stopped_record_duration_seconds,
                "scheduled_stop_at": (
                    stopped_scheduled_stop_at.isoformat()
                    if stopped_scheduled_stop_at
                    else None
                ),
                "auto_stop_triggered": stopped_auto_stop_triggered,
                "recording_started_at": (
                    stopped_recording_started_at.isoformat()
                    if stopped_recording_started_at
                    else None
                ),
                "recording_finished_at": stopped_actual_stop_at.isoformat(),
            }
        except Exception as e:
            print(f"[camera_yzt] stop_stream 异常: {e}")
            self._last_error = str(e)
            # 异常路径：保留真实异常信息，_flush_schedule_after_stop 据此置为 failed。
            stop_completed = False
            stop_exception = e
            return {
                "status": "stopped",
                "video_id": video_id,
                "video_path": transcoded_path,
                "original_video_path": video_path if transcoded_path != video_path else None,
                "frames_captured": stopped_frames_captured,
                "frames_written": stopped_frames_written,
                "fish_detected": 0,
                "queued": False,
                "queue_error": f"stop_stream 异常: {e}",
                "queue_message": "stop_stream 异常，未尝试入队",
                "raspberry_stop_status": rasp_stop_status,
                "raspberry_stop_error": rasp_stop_error,
                "transcoded_status": transcoded_status,
                "pi_stop_called": pi_stop_called_norm,
                "pi_status_after": pi_status_after_norm,
                "note": "stop_stream 异常分支",
                "record_duration_seconds": stopped_record_duration_seconds,
                "scheduled_stop_at": (
                    stopped_scheduled_stop_at.isoformat()
                    if stopped_scheduled_stop_at
                    else None
                ),
                "auto_stop_triggered": stopped_auto_stop_triggered,
            }
        finally:
            # 锁内：清理摄像头运行态（包括清空 _record_schedule_id）
            with self._lock:
                self.state = CameraRuntimeState.IDLE
                self.is_active = False
                self.last_frame_bytes = None
                self._started_at = None
                self._recording_started_at = None
                self._last_error = None
                self._video_id = None
                self._video_path = None
                self._frames_captured = 0
                self._frames_written = 0
                self._writer_width = 0
                self._writer_height = 0
                self._capture_thread = None
                self._read_fail_count = 0
                self._reconnect_timeout_finalization_started = False
                self._storage_stop_started = False
                # 标定/写视频/video_task 标志恢复默认
                self._write_video_enabled = True
                self._calibration_only = False
                self._session_create_video_task = True
                # 录制计划 ID 清理：本次会话已结束，不再持有计划关联
                self._record_schedule_id = None
                # 定时录制运行态字段重置（数据库字段由上面 update 持久化）
                self._record_duration_seconds = None
                self._scheduled_stop_at = None
                self._auto_stop_triggered = False
                self._timer_pending = False
                self._timer_armed_at = None
            # 锁外：执行计划状态写回，避免 DB 慢/异常占用摄像头运行态锁
            self._flush_schedule_after_stop(
                stopped_record_schedule_id=stopped_record_schedule_id,
                video_id=stopped_video_id,
                actual_stop_at=stopped_actual_stop_at,
                reason=reason,
                source=source,
                completed=stop_completed,
                error_message=(
                    repr(stop_exception) if stop_exception is not None else None
                ),
            )

    def get_latest_frame(self) -> Optional[bytes]:
        return self.last_frame_bytes

    def force_restart_stream(self, stream_config: Optional[dict] = None) -> dict:
        """强制重启：先 stop（含 idle 分支清理 Pi 残留）→ reset → start。

        用于：Pi 端残留推流、B 端 idle、stream already running 等 desync 场景。
        """
        print("[camera_yzt] force_restart_stream: stop (queue_algorithm=False) ...")
        stop_result = self.stop_stream(
            queue_algorithm=False,
            update_video_task=False,
            reason="force_restart",
            join_timeout=5.0,
        )

        # 给 Pi 端 / B 端一点时间释放资源
        time.sleep(1.0)

        print("[camera_yzt] force_restart_stream: reset() ...")
        try:
            reset_result = self.reset()
        except Exception as e:  # noqa: BLE001
            print(f"[camera_yzt] force_restart_stream reset failed: {e}")
            reset_result = {"status": "reset_error", "error": str(e)}

        print("[camera_yzt] force_restart_stream: start_stream ...")
        start_result = self.start_stream(stream_config)

        return {
            "force_restarted": True,
            "status": "force_restarted",
            "stop_result": stop_result,
            "reset_result": reset_result,
            "start_result": start_result,
            "video_id": start_result.get("video_id"),
            "stream_url": start_result.get("stream_url"),
        }

    def _flush_schedule_after_stop(
        self,
        *,
        stopped_record_schedule_id: Optional[str],
        video_id: Optional[str],
        actual_stop_at: datetime,
        reason: str,
        source: str,
        completed: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """stop_stream 完成后调用：根据本次 stop 是否正常完成更新 future 计划。

        任何异常都不应影响 stop_stream 自身的返回值。

        - ``completed=True`` → 计划状态置为 completed；
          stop_reason 只保存为 ``stop_reason``（不做中英文猜测）。
        - ``completed=False`` → 计划状态置为 failed；
          真实异常信息（error_message）优先，reason 仅作为辅助 stop_reason。
        """
        if not video_id:
            return
        try:
            from app.repositories import (
                camera_record_schedule_repo as _repo,
            )
            from app.repositories.db import SessionLocal as _SL
            db = _SL()
            try:
                existing = _repo.get_schedule(db, stopped_record_schedule_id) if stopped_record_schedule_id else None
                if existing is None:
                    from sqlalchemy import select
                    from app.repositories.models import CameraRecordSchedule
                    existing = db.execute(
                        select(CameraRecordSchedule)
                        .where(CameraRecordSchedule.video_id == video_id)
                        .order_by(CameraRecordSchedule.created_at.desc())
                    ).scalars().first()
                if existing is None:
                    return
                if existing.video_id and (
                    existing.video_id != video_id
                ):
                    return
                if existing.status in (
                    _repo.SCHEDULE_COMPLETED,
                    _repo.SCHEDULE_FAILED,
                    _repo.SCHEDULE_CANCELLED,
                    _repo.SCHEDULE_SKIPPED,
                ):
                    return
                if completed:
                    _repo.mark_schedule_completed(
                        db,
                        schedule_id=existing.schedule_id,
                        actual_stop_at=actual_stop_at,
                        stop_reason=reason or None,
                        stop_source=source or None,
                    )
                else:
                    _repo.mark_schedule_failed(
                        db,
                        schedule_id=existing.schedule_id,
                        error_message=(
                            error_message
                            or f"stop_stream failure: {reason or 'unknown'}"
                        ),
                        stop_reason=reason or None,
                        stop_source=source or None,
                    )
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            print(
                f"[camera_yzt] _flush_schedule_after_stop failed: {exc!r}"
            )

    def reset(self) -> dict:
        # 先停止 watchdog：reset 路径不应保留旧 watchdog
        self._stop_timed_recording_watchdog(
            reason="reset",
        )

        with self._lock:
            self._stop_event.set()
            self.state = CameraRuntimeState.IDLE
            self.is_active = False

        try:
            if (
                self._capture_thread
                and self._capture_thread.is_alive()
            ):
                self._capture_thread.join(timeout=5)
        except Exception:
            pass

        self._release_runtime_resources()

        with self._lock:
            self.last_frame_bytes = None
            self._started_at = None
            self._recording_started_at = None
            self._last_error = None
            self._reconnect_started_at = None
            self._video_id = None
            self._video_path = None
            self._frames_captured = 0
            self._frames_written = 0
            self._capture_thread = None
            self._read_fail_count = 0
            self._reconnect_started_at = None

            self._write_video_enabled = True
            self._record_schedule_id = None
            self._calibration_only = False
            self._session_create_video_task = True

            self._record_duration_seconds = None
            self._scheduled_stop_at = None
            self._auto_stop_triggered = False
            self._timer_pending = False
            self._timer_armed_at = None

        print("[camera_yzt] reset done")
        return {"status": "reset"}

    def set_debug_mock(
        self,
        *,
        pi_running: Optional[bool] = None,
        backend_active: Optional[bool] = None,
        pi_reachable: Optional[bool] = None,
    ) -> dict:
        """调试用：强制覆盖 desync 判定所用状态，用于验证“正常/异常/逆向流”。

        每个参数传 None 表示不覆盖该项、使用真实状态。
        """
        self._debug_pi_running = pi_running
        self._debug_backend_active = backend_active
        self._debug_pi_reachable = pi_reachable
        return {
            "status": "ok",
            "debug_mock": {
                "pi_running": self._debug_pi_running,
                "backend_active": self._debug_backend_active,
                "pi_reachable": self._debug_pi_reachable,
            },
        }

    def clear_debug_mock(self) -> dict:
        """清除调试模拟，恢复真实状态。"""
        self._debug_pi_running = None
        self._debug_backend_active = None
        self._debug_pi_reachable = None
        return {"status": "ok", "debug_mock": None}

    def sync_stream(self) -> dict:
        """重新同步后端与树莓派：复用树莓派已有推流。

        用于“后端状态丢失、树莓派仍在推流”的 desync 场景：
        - 不停止树莓派推流；
        - 不创建 video_task、不写视频、不入队算法；
        - 只是让后端重新连接并读取现有流。
        """
        return self.start_stream(
            {},
            calibration_only=True,
            write_video=False,
            create_video_task=False,
        )

    def focus_update(
        self,
        autofocus_mode: str,
        lens_position: Optional[float] = None,
        autofocus_window: Optional[str] = None,
    ) -> dict:
        """在线更新对焦参数，调用树莓派 /camera/focus。

        要求树莓派端支持该端点，实现动态对焦调整（不重启推流）。
        """
        payload: dict = {"autofocus_mode": autofocus_mode}
        if lens_position is not None:
            payload["lens_position"] = lens_position
        if autofocus_window:
            payload["autofocus_window"] = autofocus_window
        result = self._post_pi_focus(payload)
        return {
            "ok": result.get("ok", False),
            "status_code": result.get("status_code"),
            "error": result.get("error"),
            "text": result.get("text"),
        }

    def get_status(self) -> dict:
        from app.utils.time_utils import now_beijing

        with self._lock:
            current_state = self.state
            is_active = (
                current_state == CameraRuntimeState.RUNNING
                and self._stream_ready
            )
            show_current = current_state in (
                CameraRuntimeState.RUNNING,
                CameraRuntimeState.STARTING,
                CameraRuntimeState.RECONNECTING,
                CameraRuntimeState.STOPPING,
                CameraRuntimeState.ERROR,
            )
            video_id = self._video_id if show_current else None
            stream_url = self.stream_url
            camera_push_url = self.camera_push_url
            camera_read_url = self.camera_read_url
            frames_captured = self._frames_captured if show_current else 0
            frames_written = self._frames_written if show_current else 0
            last_error = self._last_error
            started_at = self._started_at if show_current else None
            recording_started_at = (
                self._recording_started_at if show_current else None
            )
            stream_ready = bool(self._stream_ready)
            startup_phase = self._startup_phase
            startup_error = self._startup_error
            write_video_enabled = bool(self._write_video_enabled)
            calibration_only = bool(self._calibration_only)
            session_create_video_task = bool(self._session_create_video_task)
            record_duration_seconds = (
                self._record_duration_seconds if show_current else None
            )
            scheduled_stop_at = (
                self._scheduled_stop_at if show_current else None
            )
            auto_stop_triggered = (
                self._auto_stop_triggered if show_current else False
            )
            timer_pending = (
                self._timer_pending if show_current else False
            )
            timer_armed_at = self._timer_armed_at if show_current else None
            reconnect_started_at = self._reconnect_started_at if show_current else None

        # 会话类型：idle / recording / preview
        if current_state not in (
            CameraRuntimeState.STARTING,
            CameraRuntimeState.RUNNING,
            CameraRuntimeState.RECONNECTING,
            CameraRuntimeState.STOPPING,
            CameraRuntimeState.ERROR,
        ):
            session_mode = 'idle'
        elif calibration_only or not write_video_enabled or not session_create_video_task:
            session_mode = 'preview'
        else:
            session_mode = 'recording'
        preview_mode = session_mode == 'preview'

        runtime = get_pi_runtime_config()
        effective_push_url = camera_push_url or runtime.camera_push_url
        effective_read_url = camera_read_url or runtime.camera_read_url
        pi_resolution = get_pi_resolution_diag()
        pi_arp = get_pi_arp_diag(runtime.pi_resolved_ip)
        previous_pi_mac = getattr(self, "_last_pi_mac", None)
        pi_mac_changed = bool(
            previous_pi_mac
            and pi_arp.get("mac")
            and previous_pi_mac != pi_arp.get("mac")
        )
        if pi_mac_changed:
            self._record_event(
                "pi_arp_mac_changed",
                previous_mac=previous_pi_mac,
                current_mac=pi_arp.get("mac"),
                resolved_ip=runtime.pi_resolved_ip,
            )
        if pi_arp.get("mac"):
            self._last_pi_mac = pi_arp.get("mac")
        previous_resolved_ip = getattr(self, "_last_pi_resolved_ip", None)
        pi_address_changed = bool(
            previous_resolved_ip
            and runtime.pi_resolved_ip
            and previous_resolved_ip != runtime.pi_resolved_ip
        )
        if pi_address_changed:
            self._record_event(
                "pi_resolved_ip_changed",
                previous_ip=previous_resolved_ip,
                current_ip=runtime.pi_resolved_ip,
            )
        if runtime.pi_resolved_ip:
            self._last_pi_resolved_ip = runtime.pi_resolved_ip

        elapsed: Optional[float] = None
        fps: Optional[float] = None

        if is_active and started_at:
            elapsed = (now_beijing() - started_at).total_seconds()
            if elapsed and elapsed > 0:
                fps = frames_captured / elapsed

        reconnect_elapsed_seconds: Optional[int] = None
        reconnect_remaining_seconds: Optional[int] = None
        if current_state == CameraRuntimeState.RECONNECTING and reconnect_started_at:
            reconnect_elapsed_seconds = max(
                0, int((datetime.now(timezone.utc) - reconnect_started_at).total_seconds())
            )
            reconnect_remaining_seconds = max(
                0, int(self._reconnect_grace_seconds - reconnect_elapsed_seconds)
            )

        # ---- 查询 Pi 端状态（失败不影响主流程） ----
        try:
            pi_status = self._request_pi_status(timeout=3.0)
        except Exception as e:  # noqa: BLE001
            pi_status = {
                "reachable": False,
                "ok": False,
                "error": f"get_status 异常: {e!r}",
                "running": False,
                "stream_url": None,
                "raw": None,
            }

        # ---- 计算 desync ----
        desync: Optional[bool] = None
        desync_reason: Optional[str] = None
        pi_running = bool(pi_status.get("running"))
        pi_reachable = bool(pi_status.get("reachable") and pi_status.get("ok"))
        backend_active = current_state in (
            CameraRuntimeState.RUNNING,
            CameraRuntimeState.STARTING,
            CameraRuntimeState.RECONNECTING,
        )

        # ---- 调试模拟：允许前端强制覆盖状态以验证 desync 判定 ----
        if self._debug_pi_reachable is not None:
            pi_reachable = bool(self._debug_pi_reachable)
        if self._debug_pi_running is not None:
            pi_running = bool(self._debug_pi_running)
        if self._debug_backend_active is not None:
            backend_active = bool(self._debug_backend_active)

        # 同步覆盖返回给前端的 raspberry_pi 块，保证模拟场景前后端展示一致。
        if self._debug_pi_running is not None:
            pi_status["running"] = bool(self._debug_pi_running)
        if self._debug_pi_reachable is not None:
            pi_status["reachable"] = bool(self._debug_pi_reachable)
            pi_status["ok"] = bool(self._debug_pi_reachable)

        if not pi_reachable:
            desync = None
            desync_reason = "树莓派状态不可达"
        elif backend_active and not pi_running:
            desync = True
            desync_reason = "后端认为正在运行，但树莓派未推流"
        elif (not backend_active) and pi_running:
            desync = True
            desync_reason = "后端未运行，但树莓派显示正在推流"
        else:
            desync = False
            desync_reason = "后端与树莓派状态一致"

        backend_block = {
            "state": current_state.value,
            "is_active": is_active,
            "stream_url": effective_push_url,
            "default_stream_url": runtime.camera_push_url,
            "camera_push_url": effective_push_url,
            "camera_read_url": effective_read_url,
            "pi_base_url": runtime.pi_base_url,
            "pi_resolved_ip": runtime.pi_resolved_ip,
            "host_ip": runtime.host_ip,
            "current_video_id": video_id,
            "video_id": video_id,
            "last_error": last_error,
            "stream_ready": stream_ready,
            "startup_phase": startup_phase,
            "startup_error": startup_error,
            "session_mode": session_mode,
            "preview_mode": preview_mode,
            "write_video_enabled": write_video_enabled,
            "create_video_task": session_create_video_task,
            "calibration_only": calibration_only,
            "record_duration_seconds": record_duration_seconds,
            "scheduled_stop_at": (
                scheduled_stop_at.isoformat() if scheduled_stop_at else None
            ),
            "remaining_seconds": self._compute_remaining_seconds()
            if scheduled_stop_at
            else None,
            "timed_recording": scheduled_stop_at is not None,
            "timer_pending": timer_pending,
            "timer_armed_at": (
                timer_armed_at.isoformat() if timer_armed_at else None
            ),
            "auto_stop_triggered": auto_stop_triggered,
            "reconnect_grace_seconds": self._reconnect_grace_seconds,
            "reconnect_elapsed_seconds": reconnect_elapsed_seconds,
            "reconnect_remaining_seconds": reconnect_remaining_seconds,
            "reconnecting": current_state == CameraRuntimeState.RECONNECTING,
            "pi_resolution": pi_resolution,
            "pi_address_changed": pi_address_changed,
            "pi_arp": pi_arp,
            "pi_mac_changed": pi_mac_changed,
            "storage": get_storage_health(),
            "recording_segments": segmented_recording_service.status(),
            "recording_started_at": (
                recording_started_at.isoformat() if recording_started_at else None
            ),
        }

        record_schedule_id = self._record_schedule_id
        return {
            "state": current_state.value,
            "is_active": is_active,
            "current_video_id": video_id,
            "video_id": video_id,
            "stream_url": effective_push_url,
            "default_stream_url": runtime.camera_push_url,
            "camera_push_url": effective_push_url,
            "camera_read_url": effective_read_url,
            "pi_base_url": runtime.pi_base_url,
            "pi_resolved_ip": runtime.pi_resolved_ip,
            "host_ip": runtime.host_ip,
            "frames_captured": frames_captured,
            "frames_written": frames_written,
            "fps": fps,
            "elapsed_seconds": elapsed,
            "last_error": last_error,
            "stream_ready": stream_ready,
            "startup_phase": startup_phase,
            "startup_error": startup_error,
            "session_mode": session_mode,
            "preview_mode": preview_mode,
            "write_video_enabled": write_video_enabled,
            "create_video_task": session_create_video_task,
            "calibration_only": calibration_only,
            "fish_detected": 0,
            "last_fish_detected": 0,
            "note": "本阶段算法统一走整视频 CLI pipeline，摄像头不再做实时 mock 检测。",
            "backend": backend_block,
            "raspberry_pi": pi_status,
            "desync": desync,
            "desync_reason": desync_reason,
            "event_history": list(self._event_history)[-20:],
            "reconnect_grace_seconds": self._reconnect_grace_seconds,
            "reconnect_elapsed_seconds": reconnect_elapsed_seconds,
            "reconnect_remaining_seconds": reconnect_remaining_seconds,
            "reconnecting": current_state == CameraRuntimeState.RECONNECTING,
            "pi_resolution": pi_resolution,
            "pi_address_changed": pi_address_changed,
            "pi_arp": pi_arp,
            "pi_mac_changed": pi_mac_changed,
            "storage": get_storage_health(),
            "recording_segments": segmented_recording_service.status(),
            "recording_started_at": (
                recording_started_at.isoformat() if recording_started_at else None
            ),
            # 定时录制字段
            "record_duration_seconds": record_duration_seconds,
            "scheduled_stop_at": (
                scheduled_stop_at.isoformat() if scheduled_stop_at else None
            ),
            "remaining_seconds": self._compute_remaining_seconds()
            if scheduled_stop_at
            else None,
            "timed_recording": scheduled_stop_at is not None,
            "timer_pending": timer_pending,
            "timer_armed_at": (
                self._timer_armed_at.isoformat()
                if self._timer_armed_at
                else None
            ),
            "auto_stop_triggered": auto_stop_triggered,
        }

    # ----------------- 第三阶段：启动恢复 -----------------

    @staticmethod
    def _ensure_aware_beijing(value: Optional[datetime]) -> Optional[datetime]:
        """把 datetime 归一为 timezone-aware 北京时间，便于与 now_beijing() 比较。

        - None → None。
        - naive → 视为本地时间（数据库返回的 naive 可能是 UTC），
          这里直接补 BEIJING_TZ，避免比较出错。
        - aware → 转换到北京时间。
        """
        if value is None:
            return None
        from app.utils.time_utils import beijing_timezone

        beijing = beijing_timezone()
        if value.tzinfo is None:
            return value.replace(tzinfo=beijing)
        return value.astimezone(beijing)

    def _cleanup_pi_residual_stream_on_startup(self) -> dict:
        """启动时检查树莓派是否仍在推流，是则调用 /camera/stop 清理。

        - 不依赖内存里的 _video_id；只看当前树莓派状态。
        - 不可达 / 异常均不阻断后端启动。
        """
        try:
            pi_base = self._get_pi_base_url()
        except Exception:
            pi_base = ""

        if not pi_base:
            self._record_event(
                "startup_recovery_pi_cleanup_skipped",
                reason="pi_base_url_empty",
            )
            return {
                "checked": False,
                "reason": "pi_base_url_empty",
            }

        result: dict = {
            "checked": True,
            "reachable": False,
            "running_before": None,
            "stop_posted": False,
            "stop_status_code": None,
            "stop_error": None,
            "running_after": None,
            "error": None,
        }

        try:
            pi_status = self._request_pi_status(timeout=3.0)
        except Exception as exc:
            self._record_event(
                "startup_recovery_pi_status_failed", error=repr(exc)
            )
            result["error"] = repr(exc)
            return result

        result["reachable"] = bool(pi_status.get("ok"))
        result["running_before"] = bool(pi_status.get("running"))
        self._record_event(
            "startup_recovery_pi_status",
            reachable=result["reachable"],
            running=result["running_before"],
        )

        if not result["running_before"]:
            return result

        try:
            pi_stop_resp = self._post_pi_stop(timeout=5.0)
            result["stop_status_code"] = pi_stop_resp.get("status_code")
            result["stop_error"] = pi_stop_resp.get("error")
            result["stop_posted"] = bool(pi_stop_resp.get("ok"))
            self._record_event(
                "startup_recovery_pi_stop_posted",
                ok=result["stop_posted"],
                status_code=result["stop_status_code"],
                error=result["stop_error"],
            )
        except Exception as exc:
            self._record_event(
                "startup_recovery_pi_stop_failed", error=repr(exc)
            )
            result["error"] = repr(exc)
            return result

        # 再查一次确认 running=false
        try:
            after = self._request_pi_status(timeout=3.0)
            result["running_after"] = bool(after.get("running"))
        except Exception as exc:
            result["running_after"] = None
            self._record_event(
                "startup_recovery_pi_status_after_failed",
                error=repr(exc),
            )

        return result

    def _reset_runtime_state_after_startup_recovery(self) -> None:
        """启动恢复后清理内存状态，避免新进程里残留假 running。

        - 不调用 stop_stream（重启时本就没有 capture_loop / 写视频状态）。
        - 不影响事件历史；事件历史属于新会话。
        """
        with self._lock:
            self.state = CameraRuntimeState.IDLE
            self.is_active = False
            self.last_frame_bytes = None
            self._started_at = None
            self._recording_started_at = None
            self._last_error = None
            self._video_id = None
            self._video_path = None
            self._frames_captured = 0
            self._frames_written = 0
            self._writer_width = 0
            self._writer_height = 0
            self._capture_thread = None
            self._read_fail_count = 0
            self._reconnect_timeout_finalization_started = False
            self._reconnect_started_at = None
            self._write_video_enabled = True
            self._calibration_only = False
            self._session_create_video_task = True
            self._record_duration_seconds = None
            self._scheduled_stop_at = None
            self._auto_stop_triggered = False
            self._stream_ready = False
            self._startup_phase = "idle"
            self._startup_error = None
            self._reconnect_started_at = None
            self._reconnect_timeout_finalization_started = False
            self._stop_origin = ""

        # 通知 watchdog 退出（理论上没有运行，但 set 一下更稳）
        self._watchdog_stop_event.set()
        self._watchdog_thread = None
        self._watchdog_video_id = None

    def recover_interrupted_recordings_on_startup(self) -> dict:
        """后端启动恢复：优先合并残留 TS 分段并继续算法任务。

        重要约束：
            * 不调用完整 stop_stream——内存里的 _video_id / _capture_thread
              / _writer / OpenCV VideoCapture / watchdog 全都丢失，
              完整 stop_stream 会产生空 video_task_failed 假事件，
              并可能误把状态从 queued/processing/done/failed 改坏。
            * 有有效分段时合并为 MP4，测量时长并重新入队算法。
            * 没有有效分段或合并失败时才标记 interrupted。
            * 尝试清理树莓派残留推流。
            * 任何异常都吞掉，不能阻断后端启动。
        """
        from app.utils.time_utils import now_beijing

        result: dict = {
            "ok": True,
            "recovered_count": 0,
            "items": [],
            "pi_cleanup": None,
            "error": None,
        }

        self._record_event("startup_recovery_begin")
        print("[camera_yzt] startup recovery: begin")

        try:
            now = now_beijing()
            db = SessionLocal()
            try:
                try:
                    tasks = session_repo.list_unfinished_camera_recording_tasks(
                        db, limit=20
                    )
                except Exception as exc:
                    self._record_event(
                        "startup_recovery_list_failed", error=repr(exc)
                    )
                    print(
                        f"[camera_yzt] startup recovery: list failed: {exc!r}"
                    )
                    tasks = []

                for task in tasks:
                    previous_status = task.status  # 修改前先保存
                    scheduled_raw = task.scheduled_stop_at
                    scheduled_norm = self._ensure_aware_beijing(scheduled_raw)
                    expired = (
                        scheduled_norm is not None
                        and scheduled_norm <= now
                    )

                    if expired:
                        stop_reason = "timed_recording_recovered_after_restart"
                        auto_stop_triggered_flag = True
                        last_error = (
                            "后端重启后发现定时录制任务已超过计划停止时间，"
                            "原 watchdog 线程已丢失，"
                            "已在启动恢复阶段标记为 interrupted。"
                        )
                    else:
                        stop_reason = "backend_restart_interrupted_recording"
                        auto_stop_triggered_flag = False
                        last_error = (
                            "后端重启导致摄像头录制任务中断，"
                            "进程内采集线程和写视频状态已丢失，"
                            "已在启动恢复阶段标记为 interrupted。"
                        )

                    recovered_path = VID_DIR / f"{task.video_id}{self.VIDEO_EXT}"
                    try:
                        recovered = segmented_recording_service.finalize(
                            task.video_id, recovered_path, cleanup=True
                        )
                    except Exception as exc:
                        recovered = {"ok": False, "error": repr(exc)}
                    if recovered.get("ok"):
                        try:
                            from app.services.path_service import to_data_relative
                            from app.services.video_playback_service import probe_media_duration_seconds
                            from app.workers.queue import enqueue_algorithm_video_pipeline_from_video_task

                            duration = probe_media_duration_seconds(str(recovered_path))
                            rel_path = to_data_relative(str(recovered_path))
                            if rel_path:
                                session_repo.update_video_task_source_path(
                                    db, video_id=task.video_id, source_path=rel_path
                                )
                            if duration:
                                session_repo.update_video_task_duration(
                                    db, video_id=task.video_id, video_duration_seconds=duration
                                )
                            session_repo.update_video_task_stop_info(
                                db, video_id=task.video_id, actual_stop_at=now,
                                stop_reason="recording_recovered_after_restart",
                                stop_source="startup_recovery",
                                auto_stop_triggered=auto_stop_triggered_flag,
                            )
                            session_repo.update_video_task_status(
                                db, video_id=task.video_id, status="queued",
                                last_error="检测到异常关机前的视频分段，已自动恢复并重新提交算法。",
                            )
                            job_id = enqueue_algorithm_video_pipeline_from_video_task(
                                video_id=task.video_id, input_video=str(recovered_path)
                            )
                            result["recovered_count"] += 1
                            result["items"].append({
                                "video_id": task.video_id, "previous_status": previous_status,
                                "stop_reason": "recording_recovered_after_restart",
                                "actual_stop_at": now.isoformat(), "queued": True,
                                "job_id": job_id, "segment_count": recovered.get("segment_count"),
                            })
                            self._record_event(
                                "startup_recovery_task_restored", video_id=task.video_id,
                                job_id=job_id, segment_count=recovered.get("segment_count"),
                            )
                            continue
                        except Exception as exc:
                            last_error = f"视频分段已合并，但恢复算法任务失败：{exc!r}"

                    try:
                        updated = session_repo.mark_video_task_interrupted(
                            db,
                            video_id=task.video_id,
                            actual_stop_at=now,
                            stop_reason=stop_reason,
                            stop_source="startup_recovery",
                            auto_stop_triggered=auto_stop_triggered_flag,
                            last_error=last_error,
                        )
                    except Exception as exc:
                        print(
                            f"[camera_yzt] startup recovery: mark failed "
                            f"video_id={task.video_id!r} err={exc!r}"
                        )
                        self._record_event(
                            "startup_recovery_mark_failed",
                            video_id=task.video_id,
                            error=repr(exc),
                        )
                        continue

                    if updated is None:
                        continue

                    result["recovered_count"] += 1
                    result["items"].append(
                        {
                            "video_id": task.video_id,
                            "previous_status": previous_status,
                            "stop_reason": stop_reason,
                            "auto_stop_triggered": auto_stop_triggered_flag,
                            "scheduled_stop_at": (
                                scheduled_raw.isoformat()
                                if scheduled_raw is not None
                                else None
                            ),
                            "actual_stop_at": now.isoformat(),
                        }
                    )

                    self._record_event(
                        "startup_recovery_task_interrupted",
                        video_id=task.video_id,
                        previous_status=previous_status,
                        stop_reason=stop_reason,
                        auto_stop_triggered=auto_stop_triggered_flag,
                        scheduled_stop_at=(
                            scheduled_raw.isoformat()
                            if scheduled_raw is not None
                            else None
                        ),
                    )

                try:
                    db.commit()
                except Exception as exc:
                    print(
                        f"[camera_yzt] startup recovery: commit failed: {exc!r}"
                    )
                    self._record_event(
                        "startup_recovery_commit_failed", error=repr(exc)
                    )
            finally:
                db.close()
        except Exception as exc:
            result["ok"] = False
            result["error"] = repr(exc)
            self._record_event(
                "startup_recovery_failed", error=repr(exc)
            )
            print(f"[camera_yzt] startup recovery failed: {exc!r}")
            # 仍然尝试清理内存 + 树莓派，避免出现假 running
            try:
                self._reset_runtime_state_after_startup_recovery()
            except Exception:
                pass
            try:
                result["pi_cleanup"] = (
                    self._cleanup_pi_residual_stream_on_startup()
                )
            except Exception:
                pass
            return result

        # 树莓派残留推流清理（与恢复 DB 行无关；独立 try/except）
        try:
            result["pi_cleanup"] = self._cleanup_pi_residual_stream_on_startup()
        except Exception as exc:
            result["pi_cleanup"] = {"error": repr(exc)}
            self._record_event(
                "startup_recovery_pi_cleanup_failed", error=repr(exc)
            )

        # 重置内存
        try:
            self._reset_runtime_state_after_startup_recovery()
        except Exception as exc:
            self._record_event(
                "startup_recovery_reset_failed", error=repr(exc)
            )

        self._record_event(
            "startup_recovery_done",
            recovered_count=result["recovered_count"],
            pi_cleanup=result["pi_cleanup"],
        )
        print(
            f"[camera_yzt] startup recovery done: recovered_count="
            f"{result['recovered_count']} pi_cleanup={result['pi_cleanup']}"
        )

        return result


camera_service_yzt = CameraServiceYzt()
