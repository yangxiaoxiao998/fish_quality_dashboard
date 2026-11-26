from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR

WARNING_BYTES = int(float(os.getenv("FISH_STORAGE_WARNING_GB", "20")) * 1024**3)
CRITICAL_BYTES = int(float(os.getenv("FISH_STORAGE_CRITICAL_GB", "8")) * 1024**3)
BLOCK_BYTES = int(float(os.getenv("FISH_STORAGE_BLOCK_GB", "2")) * 1024**3)


def _mounted_targets() -> list[dict[str, Any]]:
    user = os.getenv("USER") or os.getenv("LOGNAME") or "root"
    result: list[dict[str, Any]] = []
    for base in (Path(f"/media/{user}"), Path(f"/run/media/{user}"), Path("/mnt")):
        if not base.is_dir():
            continue
        try:
            candidates = list(base.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            if not candidate.is_dir() or not os.access(candidate, os.W_OK):
                continue
            try:
                usage = shutil.disk_usage(candidate)
            except OSError:
                continue
            result.append({"path": str(candidate), "free_bytes": usage.free, "total_bytes": usage.total})
    return result


def get_storage_health() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(DATA_DIR)
    free_ratio = usage.free / usage.total if usage.total else 0.0
    if usage.free < BLOCK_BYTES or free_ratio < 0.02:
        level = "block"
        message = "磁盘空间严重不足，已禁止开始新录像。请立即插入 U 盘并迁移数据。"
    elif usage.free < CRITICAL_BYTES or free_ratio < 0.05:
        level = "critical"
        message = "磁盘空间即将耗尽，请尽快插入 U 盘并迁移数据。"
    elif usage.free < WARNING_BYTES or free_ratio < 0.15:
        level = "warning"
        message = "磁盘剩余空间偏低，建议插入 U 盘迁移历史数据。"
    else:
        level = "ok"
        message = "磁盘空间充足"
    mounts = _mounted_targets()
    return {
        "level": level, "message": message, "path": str(DATA_DIR),
        "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
        "free_percent": round(free_ratio * 100, 1), "recording_allowed": level != "block",
        "usb_recommended": level in {"warning", "critical", "block"},
        "removable_targets": mounts, "removable_available": bool(mounts),
    }


def ensure_recording_capacity(duration_seconds: int | None = None) -> dict[str, Any]:
    health = get_storage_health()
    # Conservative H.264 allowance: 12 Mbit/s plus 2 GiB safety reserve.
    required = 2 * 1024**3 + int(max(0, duration_seconds or 0) * 12_000_000 / 8)
    health["estimated_required_bytes"] = required
    if health["free_bytes"] < required:
        health["recording_allowed"] = False
        health["level"] = "block"
        health["message"] = "当前空间不足以完成本次录像。请插入 U 盘并完成数据迁移后重试。"
    return health
