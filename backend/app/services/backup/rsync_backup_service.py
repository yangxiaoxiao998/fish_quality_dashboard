from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger("rsync_backup")
CONFIG_PATH = DATA_DIR / "config" / "rsync_backup.json"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "rsync_data_backup.sh"
DEFAULT_INTERVAL_SECONDS = 3600


def _default_target_dir() -> Path:
    user = os.getenv("USER") or os.getenv("LOGNAME") or "root"
    for base in (Path(f"/media/{user}"), Path(f"/run/media/{user}"), Path("/mnt")):
        if not base.is_dir():
            continue
        try:
            for mount in base.iterdir():
                if mount.is_dir() and os.access(mount, os.W_OK):
                    return mount / "fish_quality_backups" / "rsync"
        except OSError:
            continue
    return PROJECT_ROOT / "backups" / "rsync"


def _default_config() -> dict[str, Any]:
    return {
        "source_dir": str(DATA_DIR),
        "target_dir": str(_default_target_dir()),
        "interval_seconds": DEFAULT_INTERVAL_SECONDS,
        "enabled": False,
    }


def _load_config() -> dict[str, Any]:
    config = _default_config()
    try:
        if CONFIG_PATH.is_file():
            value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                config.update(value)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("load rsync backup config failed: %s", exc)
    return config


def _path(value: str, name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} 不能为空")
    return Path(raw).expanduser().resolve()


def _validate(source_value: str, target_value: str) -> tuple[Path, Path]:
    source = _path(source_value, "备份数据路径")
    target = _path(target_value, "移动硬盘目标路径")
    if not source.is_dir():
        raise ValueError(f"备份数据路径不存在或不是目录：{source}")
    if source == target:
        raise ValueError("备份数据路径和目标路径不能相同")
    return source, target


def _save_config(config: dict[str, Any]) -> dict[str, Any]:
    source, target = _validate(config["source_dir"], config["target_dir"])
    interval = int(config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))
    if interval != DEFAULT_INTERVAL_SECONDS:
        raise ValueError("自动备份间隔固定为 60 分钟")
    normalized = {
        "source_dir": str(source),
        "target_dir": str(target),
        "interval_seconds": interval,
        "enabled": bool(config.get("enabled", False)),
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CONFIG_PATH)
    return normalized


def _find_mount(target: Path) -> dict[str, str] | None:
    try:
        result = subprocess.run(
            ["findmnt", "-rn", "-o", "TARGET,SOURCE"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    matches = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2:
            continue
        mount = Path(fields[0]).resolve()
        if mount != Path("/") and (target == mount or mount in target.parents):
            matches.append({"target": str(mount), "source": fields[1].strip()})
    return max(matches, key=lambda item: len(Path(item["target"]).parts), default=None)


class _Scheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.running = False
        self.last_run: dict[str, Any] | None = None
        self.current_run: dict[str, Any] | None = None

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="rsync_backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.running = False
        self._thread = None

    def begin(self) -> bool:
        with self._lock:
            if self.current_run is not None:
                return False
            self.current_run = {"started_at": datetime.now(timezone.utc).isoformat(), "message": "同步执行中"}
            return True

    def end(self) -> None:
        with self._lock:
            self.current_run = None

    def set_last(self, result: dict[str, Any]) -> None:
        with self._lock:
            self.last_run = result

    def _loop(self) -> None:
        while not self._stop.wait(int(_load_config().get("interval_seconds", DEFAULT_INTERVAL_SECONDS))):
            try:
                run_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("scheduled rsync backup failed")
                self.set_last({"ok": False, "message": str(exc)})


_scheduler = _Scheduler()


def get_status() -> dict[str, Any]:
    config = _load_config()
    source, target = Path(config["source_dir"]), Path(config["target_dir"])
    return {
        "ok": True,
        "configured": CONFIG_PATH.is_file(),
        "source_dir": str(source),
        "target_dir": str(target),
        "interval_seconds": int(config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)),
        "enabled": bool(config.get("enabled", False)),
        "scheduler_running": _scheduler.running,
        "source_exists": source.is_dir(),
        "target_exists": target.is_dir(),
        "rsync_exists": Path("/usr/bin/rsync").is_file() or shutil.which("rsync") is not None,
        "last_run": _scheduler.last_run,
        "current_run": _scheduler.current_run,
        "syncing": _scheduler.current_run is not None,
        "ejecting": False,
    }


def save_settings(source_dir: str, target_dir: str, interval_seconds: int, enabled: bool) -> dict[str, Any]:
    config = _save_config({"source_dir": source_dir, "target_dir": target_dir, "interval_seconds": interval_seconds, "enabled": enabled})
    _scheduler.stop()
    if config["enabled"]:
        _scheduler.start()
    return get_status()


def _run_once() -> dict[str, Any]:
    config = _load_config()
    source, target = _validate(config["source_dir"], config["target_dir"])
    if not SCRIPT_PATH.is_file():
        raise ValueError(f"备份脚本不存在：{SCRIPT_PATH}")
    if shutil.which("rsync") is None and not Path("/usr/bin/rsync").is_file():
        raise ValueError("系统未找到 rsync 命令")
    log_path = target / "rsync.log"
    started = time.monotonic()
    try:
        process = subprocess.run(["bash", str(SCRIPT_PATH), str(source), str(target), str(log_path)], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=3600, check=False)
        result = {
            "ok": process.returncode == 0, "returncode": process.returncode,
            "source_dir": str(source), "target_dir": str(target), "log_path": str(log_path),
            "stdout": process.stdout or "", "stderr": process.stderr or "",
            "duration_seconds": round(time.monotonic() - started, 2),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "message": "自动同步备份成功" if process.returncode == 0 else "自动同步备份失败",
        }
    except subprocess.TimeoutExpired as exc:
        result = {"ok": False, "returncode": -1, "source_dir": str(source), "target_dir": str(target), "log_path": str(log_path), "stdout": exc.stdout or "", "stderr": "rsync 执行超时（超过 3600 秒）", "message": "自动同步备份失败"}
    _scheduler.set_last(result)
    return result


def run_once() -> dict[str, Any]:
    if not _scheduler.begin():
        return {"ok": False, "accepted": False, "message": "已有同步任务正在执行，请稍候"}
    try:
        return _run_once()
    finally:
        _scheduler.end()


def start_async() -> dict[str, Any]:
    if not _scheduler.begin():
        return {"ok": False, "accepted": False, "message": "已有同步任务正在执行，请稍候"}
    def worker() -> None:
        try:
            _run_once()
        except Exception as exc:  # noqa: BLE001
            _scheduler.set_last({"ok": False, "message": f"自动同步备份失败：{exc}", "finished_at": datetime.now(timezone.utc).isoformat()})
        finally:
            _scheduler.end()
    threading.Thread(target=worker, name="rsync_backup_once", daemon=True).start()
    return {"ok": True, "accepted": True, "message": "同步任务已启动"}


def eject_target() -> dict[str, Any]:
    target = Path(_load_config()["target_dir"]).expanduser().resolve()
    if _scheduler.current_run is not None:
        return {"ok": False, "message": "同步正在执行，完成后才能弹出移动硬盘"}
    if not any(target == root or root in target.parents for root in (Path("/media"), Path("/run/media"), Path("/mnt"))):
        return {"ok": False, "message": "当前目标路径不是可安全弹出的移动硬盘挂载路径", "target_dir": str(target)}
    mount = _find_mount(target)
    if mount is None:
        return {"ok": False, "message": "未找到目标移动硬盘的挂载点", "target_dir": str(target)}
    try:
        process = subprocess.run(["udisksctl", "unmount", "-b", mount["source"]], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": f"弹出移动硬盘失败：{exc}", "target_dir": str(target)}
    return {"ok": process.returncode == 0, "message": "移动硬盘已安全卸载，可以拔出" if process.returncode == 0 else "移动硬盘卸载失败", "target_dir": str(target), "mount_point": mount["target"], "source": mount["source"], "stdout": process.stdout or "", "stderr": process.stderr or "", "returncode": process.returncode}


def start_scheduler() -> None:
    if _load_config().get("enabled"):
        _scheduler.start()


def stop_scheduler() -> None:
    _scheduler.stop()
