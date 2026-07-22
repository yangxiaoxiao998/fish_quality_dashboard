#!/usr/bin/env python
# backend/app/services/system_backup_service.py
#
# 数据备份 / 系统备份 service。
# - status: 探测 U 盘 / 已有备份 / 默认备份目录
# - preview: 不真打 tar，只计算大小、目标路径、新备份名
# - run: 调 project_root/backup.sh，capture stdout/stderr 返回
#
# 注意：
# - 所有 IO 不破坏业务数据（只读 data/，写入 backups/ 或 U 盘）
# - subprocess.run cwd = project_root，让 backup.sh 找到 data/ 和 backend/data/

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import PROJECT_ROOT


# ---- 备份目录探测 ----
def _detect_usb_mounts() -> List[Dict[str, str]]:
    """探测可写的 U 盘挂载点。

    扫描路径：
      - /media/$USER/*
      - /run/media/$USER/*
      - /mnt/*

    过滤：
      - 必须存在
      - 必须可写
      - 不能是项目根目录
    """
    user = os.getenv("USER") or os.getenv("LOGNAME") or "root"
    candidates: List[Path] = []
    for base in [
        Path(f"/media/{user}"),
        Path(f"/run/media/{user}"),
        Path("/mnt"),
    ]:
        if not base.exists():
            continue
        try:
            for child in base.iterdir():
                if child.is_dir():
                    candidates.append(child)
        except PermissionError:
            continue

    result: List[Dict[str, str]] = []
    for cand in candidates:
        try:
            if not os.access(cand, os.W_OK):
                continue
            # 不要把项目根目录当作 U 盘
            if cand.resolve() == PROJECT_ROOT.resolve():
                continue
            total, used, free = shutil.disk_usage(str(cand))
            result.append({
                "path": str(cand),
                "free": _format_bytes(free),
                "total": _format_bytes(total),
            })
        except Exception:
            continue
    return result


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    for unit in ["K", "M", "G", "T"]:
        n_f = n / 1024
        if n_f < 1024:
            return f"{n_f:.2f}{unit}"
        n = int(n_f)
    return f"{n/1024:.2f}P"


def _data_dir_size(base: Path) -> int:
    """递归计算目录总大小（字节）。"""
    if not base.exists():
        return 0
    total = 0
    try:
        for root, _dirs, files in os.walk(base):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
    except Exception:
        pass
    return total


def _list_existing_backups(target_dir: Path) -> List[str]:
    """列出 target_dir 下所有 fish_quality_data_*.tar.gz。"""
    if not target_dir.exists():
        return []
    out: List[str] = []
    try:
        for f in target_dir.iterdir():
            if f.is_file() and f.name.startswith("fish_quality_data_") and f.name.endswith(".tar.gz"):
                out.append(f.name)
    except Exception:
        pass
    return sorted(out)


# ---- 业务路径 ----
BACKUP_SCRIPT = PROJECT_ROOT / "backup.sh"
DEFAULT_BACKUP_BASE = PROJECT_ROOT / "backups"
CANDIDATE_DATA_DIRS = [
    PROJECT_ROOT / "data",
    PROJECT_ROOT / "backend" / "data",
]


def _resolve_target_dir(target_dir: Optional[str]) -> Path:
    """决定备份目标目录。
    1) 显式传入 → 用它（保证可写）
    2) 否则优先第一个 U 盘下的 fish_quality_backups
    3) 都没有 → 项目根 backups/
    """
    if target_dir:
        p = Path(target_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    usb = _detect_usb_mounts()
    if usb:
        cand = Path(usb[0]["path"]) / "fish_quality_backups"
        cand.mkdir(parents=True, exist_ok=True)
        return cand
    DEFAULT_BACKUP_BASE.mkdir(parents=True, exist_ok=True)
    return DEFAULT_BACKUP_BASE


def get_backup_status() -> Dict[str, Any]:
    """GET /api/system/backup/status"""
    usb = _detect_usb_mounts()
    default_dir = DEFAULT_BACKUP_BASE
    existing: List[str] = []
    if default_dir.exists():
        existing = _list_existing_backups(default_dir)

    return {
        "ok": True,
        "usb_inserted": len(usb) > 0,
        "usb_mounts": usb,
        "default_backup_dir": str(default_dir),
        "latest_backup": existing[-1] if existing else None,
        "all_backups": existing,
        "project_root": str(PROJECT_ROOT),
        "backup_script_exists": BACKUP_SCRIPT.exists(),
    }


def preview_backup(target_dir: Optional[str]) -> Dict[str, Any]:
    """POST /api/system/backup/preview

    不执行 tar，只计算：
      - target_dir
      - estimated_size_bytes（累加 CANDIDATE_DATA_DIRS 中存在的目录）
      - already_backed_up（target_dir 下已有 fish_quality_data_*.tar.gz）
      - new_backup_name（按时间戳）
    """
    target = _resolve_target_dir(target_dir)
    total_bytes = 0
    existing_dirs: List[str] = []
    for d in CANDIDATE_DATA_DIRS:
        if d.exists():
            total_bytes += _data_dir_size(d)
            existing_dirs.append(str(d))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"fish_quality_data_{ts}.tar.gz"

    return {
        "ok": True,
        "target_dir": str(target),
        "already_backed_up": _list_existing_backups(target),
        "new_backup_name": new_name,
        "new_backup_path": str(target / new_name),
        "estimated_size_bytes": total_bytes,
        "estimated_size": _format_bytes(total_bytes),
        "existing_data_dirs": existing_dirs,
        "missing_data_dirs": [
            str(d) for d in CANDIDATE_DATA_DIRS if not d.exists()
        ],
    }


def _run_subprocess(cmd: List[str], cwd: Path, timeout: int) -> Dict[str, Any]:
    """跑外部命令，capture stdout/stderr。"""
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "duration_seconds": round(time.time() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"命令超时（>{timeout}s）",
            "duration_seconds": round(time.time() - t0, 2),
            "timeout": True,
        }
    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"执行失败: {e}",
            "duration_seconds": round(time.time() - t0, 2),
        }


def run_backup(target_dir: Optional[str], timeout: int = 3600) -> Dict[str, Any]:
    """POST /api/system/backup/run

    调 backup.sh <target_dir>。
    """
    if not BACKUP_SCRIPT.exists():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"备份脚本不存在: {BACKUP_SCRIPT}",
            "message": "backup.sh 不存在，请确认项目根目录部署完整",
        }

    # 解析最终 target_dir：先调 preview 里的 resolve 逻辑，保证一致
    target = _resolve_target_dir(target_dir)
    cmd = ["bash", str(BACKUP_SCRIPT), str(target)]
    result = _run_subprocess(cmd, cwd=PROJECT_ROOT, timeout=timeout)

    new_backup_path = target / f"fish_quality_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    ok = result["returncode"] == 0

    # 列出备份目录里最新的文件作为结果
    after_backups = _list_existing_backups(target)
    new_backup = after_backups[-1] if after_backups else None
    actual_path = str(target / new_backup) if new_backup else None

    return {
        "ok": ok,
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "duration_seconds": result["duration_seconds"],
        "target_dir": str(target),
        "new_backup_path": actual_path,
        "all_backups": after_backups,
        "message": "备份完成" if ok else "备份失败",
    }
