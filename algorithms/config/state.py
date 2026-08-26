"""Pipeline 状态与锁文件工具。

- ``atomic_write_json``：原子写 JSON（先写 .tmp 再 rename）。
- ``PipelineState``：维护 ``data/jobs/<video_id>/state/pipeline_state.json`` 的生命周期。
- ``PipelineLock``：维护 ``data/jobs/<video_id>/state/pipeline.lock`` 的互斥锁。
- ``scan_interrupted_jobs``：扫描所有 jobs，找到 stale ``running`` / ``publishing`` 标记
  但 pid 已死 / 文件已损坏的记录。

约定：

1. ``video_id`` 必须已经被分配（用户传入或系统生成）；
2. ``PipelineState.save`` 使用 ``atomic_write_json`` 写到 ``state/pipeline_state.json``；
3. ``PipelineLock`` 写入 ``state/pipeline.lock`` 携带 pid + started_at + command；
4. 进程退出由 main 入口用 ``finally: lock.release()`` 负责；kill -9 等会留下 stale lock，
   下次启动由 ``scan_interrupted_jobs`` 或 main 中 ``_check_lock_state`` 标记为 interrupted。
"""

from __future__ import annotations

import datetime as _dt
import errno
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

THIS_DIR = Path(__file__).resolve().parent
_ALGORITHMS_ROOT = THIS_DIR.parent
if str(_ALGORITHMS_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_ALGORITHMS_ROOT))

from config.paths import (  # noqa: E402
    ensure_dir,
    job_dir,
    video_pipeline_lock_file,
    video_pipeline_state_json,
    video_state_dir,
)


# ---------------------------------------------------------------------------
# 原子写 + 时间戳
# ---------------------------------------------------------------------------


def now_str() -> str:
    """返回形如 ``2026-07-04 15:30:12`` 的本地时间字符串。"""

    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def atomic_write_json(path: Path, payload: dict, *, ensure_ascii: bool = False, indent: int = 2) -> None:
    """把 ``payload`` 原子写入 ``path``：先写同名 .tmp，再 ``replace``。

    异常时清理临时文件，避免半成品残留。
    """

    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=ensure_ascii, indent=indent)
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本文件。"""

    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            fp.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 进程是否存活
# ---------------------------------------------------------------------------


def is_pid_alive(pid: int) -> bool:
    """``kill -0`` 检查 pid 是否还存活。

    注：仅检查内核状态，不保证进程没被 frozen / 中断。
    """

    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 没有权限发送信号但 pid 存在，视为存活
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------


# 状态枚举
STATE_CREATED = "created"
STATE_RUNNING = "running"
STATE_STAGE_DONE = "stage_done"
STATE_FAILED = "failed"
STATE_INTERRUPTED = "interrupted"
STATE_PUBLISHING = "publishing"
STATE_DONE = "done"

VALID_STATES: tuple[str, ...] = (
    STATE_CREATED,
    STATE_RUNNING,
    STATE_STAGE_DONE,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PUBLISHING,
    STATE_DONE,
)


class PipelineState:
    """维护 ``data/jobs/<video_id>/state/pipeline_state.json`` 的高层封装。"""

    def __init__(
        self,
        video_id: str,
        *,
        command: Iterable[str] | None = None,
        pid: int | None = None,
        started_at: str | None = None,
        created: bool = True,
    ) -> None:
        self.video_id = video_id
        self.path = video_pipeline_state_json(video_id)
        ensure_dir(video_state_dir(video_id))
        if created or not self.path.exists():
            self.payload: dict[str, Any] = {
                "video_id": video_id,
                "status": STATE_CREATED,
                "pid": pid if pid is not None else os.getpid(),
                "started_at": started_at or now_str(),
                "updated_at": started_at or now_str(),
                "current_stage": "",
                "completed_stages": [],
                "failed_stage": "",
                "error_message": "",
                "command": list(command) if command is not None else [],
                "input_video": "",
                "result_dir": "",
            }
            self.save()
        else:
            with open(self.path, "r", encoding="utf-8") as fp:
                self.payload = json.load(fp)
            # 启动时若 status 不是 done/failed/interrupted，标记为 running 以反映新进程接管
            old_status = self.payload.get("status", "")
            if old_status not in (STATE_DONE, STATE_FAILED, STATE_INTERRUPTED):
                self.payload["status"] = STATE_RUNNING
                self.payload["pid"] = pid if pid is not None else os.getpid()
                if command is not None:
                    self.payload["command"] = list(command)
                self.touch()

    # ----- 状态更新 -----

    def mark_running(self, stage: str = "") -> None:
        self.payload["status"] = STATE_RUNNING
        self.payload["current_stage"] = stage
        self.touch()

    def start_stage(self, stage: str) -> None:
        self.payload["status"] = STATE_RUNNING
        self.payload["current_stage"] = stage
        self.touch()

    def complete_stage(self, stage: str) -> None:
        if stage not in self.payload["completed_stages"]:
            self.payload["completed_stages"].append(stage)
        if self.payload.get("current_stage") == stage:
            self.payload["current_stage"] = ""
        self.payload["status"] = STATE_STAGE_DONE
        self.touch()

    def fail(self, stage: str, error: str) -> None:
        self.payload["status"] = STATE_FAILED
        self.payload["failed_stage"] = stage
        self.payload["current_stage"] = stage
        self.payload["error_message"] = error
        self.touch()

    def interrupted(self, stage: str, reason: str) -> None:
        self.payload["status"] = STATE_INTERRUPTED
        self.payload["failed_stage"] = stage or self.payload.get("failed_stage", "")
        if reason:
            self.payload["error_message"] = reason
        self.touch()

    def publishing(self) -> None:
        self.payload["status"] = STATE_PUBLISHING
        self.touch()

    def done(self) -> None:
        self.payload["status"] = STATE_DONE
        self.payload["current_stage"] = ""
        self.touch()

    def touch(self) -> None:
        self.payload["updated_at"] = now_str()
        self.save()

    def save(self) -> None:
        atomic_write_json(self.path, self.payload)

    # ----- 便捷访问 -----

    @property
    def status(self) -> str:
        return self.payload.get("status", "")

    @property
    def completed_stages(self) -> list[str]:
        return list(self.payload.get("completed_stages", []) or [])


# ---------------------------------------------------------------------------
# PipelineLock
# ---------------------------------------------------------------------------


class PipelineLock:
    """lock 文件位于 ``data/jobs/<video_id>/state/pipeline.lock``。

    约束：

    - ``acquire`` 会校验目录结构合法性；
    - 如果目标 lock 已存在但 pid 已死（stale），由调用方决定是否覆盖；
    - ``release`` 默认**不会**抛出。
    """

    def __init__(self, video_id: str, *, command: Iterable[str] | None = None, pid: int | None = None) -> None:
        self.video_id = video_id
        self.path = video_pipeline_lock_file(video_id)
        ensure_dir(video_state_dir(video_id))
        self.pid = pid if pid is not None else os.getpid()
        self.payload: dict[str, Any] = {
            "video_id": video_id,
            "pid": self.pid,
            "started_at": now_str(),
            "command": list(command) if command is not None else [],
        }

    def acquire(self, *, overwrite_stale: bool = False) -> bool:
        """占用 lock。如果另一个 live pid 已占用，返回 False。

        - 同一进程（``self.pid == existing.pid`` 且都活着）视为可重入，强制覆盖；
        - 不同 live pid 占用 → 返回 False；
        - ``overwrite_stale=True`` 时，stale lock（pid 已死）会被覆盖为新 lock；
        - ``overwrite_stale=False`` 时，stale lock 也视为冲突，返回 False。
        """

        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fp:
                    existing = json.load(fp)
            except Exception:
                existing = {}
            old_pid_raw = existing.get("pid") or 0
            try:
                old_pid = int(old_pid_raw)
            except (TypeError, ValueError):
                old_pid = 0
            same_process = old_pid == self.pid and old_pid > 0
            old_alive = is_pid_alive(old_pid) if old_pid > 0 else False
            if old_alive and not same_process:
                return False
            # 进程已死 (stale) 时，除非显式同意，否则也视为冲突。
            if not old_alive and not overwrite_stale and not same_process:
                return False
        atomic_write_json(self.path, self.payload)
        return True

    def release(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# scan_interrupted_jobs
# ---------------------------------------------------------------------------


def scan_interrupted_jobs(jobs_dir: Path | None = None) -> list[Path]:
    """扫描所有 jobs 中"看起来还在 running / publishing 但 pid 已死"的 state 文件。

    - 输入：``jobs_dir`` 默认为 ``config.paths.JOBS_DIR``。
    - 输出：被标记为 ``interrupted`` 的 state json 路径列表。
    """

    from config.paths import JOBS_DIR  # 局部引用避免循环

    jobs_dir = Path(jobs_dir or JOBS_DIR)
    if not jobs_dir.is_dir():
        return []
    flagged: list[Path] = []
    for job_entry in sorted(jobs_dir.iterdir()):
        if not job_entry.is_dir():
            continue
        state_path = job_entry / "state" / "pipeline_state.json"
        if not state_path.is_file():
            continue
        try:
            with open(state_path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except Exception:
            continue
        status = (payload.get("status") or "").strip()
        if status not in (STATE_RUNNING, STATE_PUBLISHING, STATE_STAGE_DONE):
            continue
        pid_raw = payload.get("pid") or 0
        try:
            pid_int = int(pid_raw)
        except (TypeError, ValueError):
            pid_int = 0
        if pid_int == os.getpid():
            # 本进程自身，不算 interrupted
            continue
        if is_pid_alive(pid_int):
            continue
        # stale
        try:
            payload["status"] = STATE_INTERRUPTED
            payload["failed_stage"] = payload.get("failed_stage") or payload.get("current_stage") or ""
            payload["error_message"] = (
                payload.get("error_message")
                or "进程被终止、断电、机器重启或显卡崩溃等极端中断"
            )
            payload["updated_at"] = now_str()
            atomic_write_json(state_path, payload)
            flagged.append(state_path)
        except Exception:
            continue
    return flagged


__all__ = [
    "atomic_write_json",
    "atomic_write_text",
    "now_str",
    "is_pid_alive",
    "PipelineState",
    "PipelineLock",
    "scan_interrupted_jobs",
    "STATE_CREATED",
    "STATE_RUNNING",
    "STATE_STAGE_DONE",
    "STATE_FAILED",
    "STATE_INTERRUPTED",
    "STATE_PUBLISHING",
    "STATE_DONE",
    "VALID_STATES",
]