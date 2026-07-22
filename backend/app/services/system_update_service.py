#!/usr/bin/env python
# backend/app/services/system_update_service.py
#
# 软件更新 service。
# - status: 读 / 创建 version.json
# - upload: 保存 zip 包到 data/uploads/update_packages/，文件名加时间戳
# - run: 调 project_root/updater.py <package>，capture stdout/stderr
#
# 注意：
# - updater.py 会保护 data / logs / backup / temp_update / .env / .env.gpu
# - 这里只做路径校验 + 调脚本，**不**直接覆盖任何业务数据
# - 路径穿越防护：package_path 必须在 DATA_DIR/update_packages 下

from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.paths import DATA_DIR, PROJECT_ROOT


UPDATER_SCRIPT = PROJECT_ROOT / "updater.py"
VERSION_FILE = PROJECT_ROOT / "version.json"
UPDATE_PACKAGE_DIR = DATA_DIR / "uploads" / "update_packages"
DEFAULT_VERSION = {"current_version": "1.0.0"}


def _ensure_dirs() -> None:
    UPDATE_PACKAGE_DIR.mkdir(parents=True, exist_ok=True)


def _read_version() -> Dict[str, Any]:
    """读 version.json，不存在则创建。"""
    if not VERSION_FILE.exists():
        try:
            VERSION_FILE.write_text(
                '{\n  "current_version": "1.0.0"\n}\n', encoding="utf-8"
            )
        except Exception as e:
            return {**DEFAULT_VERSION, "_error_reading": str(e)}
    try:
        import json
        return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {**DEFAULT_VERSION, "_error_reading": str(e)}


def get_update_status() -> Dict[str, Any]:
    """GET /api/system/update/status"""
    _ensure_dirs()
    info = _read_version()
    # 列出已上传但未执行的包
    uploaded: list[Dict[str, Any]] = []
    try:
        for f in sorted(UPDATE_PACKAGE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() == ".zip":
                uploaded.append({
                    "name": f.name,
                    "path": str(f),
                    "size_bytes": f.stat().st_size,
                    "size": _format_bytes(f.stat().st_size),
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
    except Exception as e:
        uploaded = []
        info["_error_listing"] = str(e)

    return {
        "ok": True,
        "current_version": info.get("current_version", "1.0.0"),
        "version_file": str(VERSION_FILE),
        "version_file_exists": VERSION_FILE.exists(),
        "updater_script_exists": UPDATER_SCRIPT.exists(),
        "uploaded_packages": uploaded,
    }


def save_update_package(file_bytes: bytes, original_filename: str) -> Dict[str, Any]:
    """保存 zip 包到 data/uploads/update_packages/。"""
    _ensure_dirs()

    if not original_filename.lower().endswith(".zip"):
        raise ValueError("只允许 .zip 更新包")

    # 简单 magic check：zip 文件头是 PK\x03\x04
    if not file_bytes.startswith(b"PK\x03\x04"):
        raise ValueError("文件不是有效的 zip 格式（magic bytes 不匹配）")

    # 文件名加时间戳，避免覆盖
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", original_filename)
    new_name = f"{ts}_{safe_name}"
    target = UPDATE_PACKAGE_DIR / new_name
    target.write_bytes(file_bytes)

    return {
        "ok": True,
        "package_name": new_name,
        "package_path": str(target),
        "size_bytes": len(file_bytes),
        "size": _format_bytes(len(file_bytes)),
    }


def _resolve_package_path(package_path: str) -> Optional[Path]:
    """安全解析：package_path 必须在 UPDATE_PACKAGE_DIR 之内。"""
    if not package_path:
        return None
    try:
        target = Path(package_path).expanduser().resolve()
    except Exception:
        return None
    base = UPDATE_PACKAGE_DIR.resolve()
    try:
        # 防止 ../ 路径穿越
        target.relative_to(base)
    except ValueError:
        return None
    if not target.is_file() or target.suffix.lower() != ".zip":
        return None
    return target


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    for unit in ["K", "M", "G", "T"]:
        n_f = n / 1024
        if n_f < 1024:
            return f"{n_f:.2f}{unit}"
        n = int(n_f)
    return f"{n/1024:.2f}P"


def _run_subprocess(cmd: list[str], cwd: Path, timeout: int) -> Dict[str, Any]:
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


def run_update(package_path: str, timeout: int = 3600) -> Dict[str, Any]:
    """POST /api/system/update/run

    路径校验 → 调 updater.py。
    """
    if not UPDATER_SCRIPT.exists():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"更新脚本不存在: {UPDATER_SCRIPT}",
            "message": "updater.py 不存在",
        }

    target = _resolve_package_path(package_path)
    if target is None:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"package_path 非法或不在允许目录内: {package_path}",
            "message": "package_path 校验失败",
        }

    # 解析 update package 里的 version.json（peeking，不解压）
    pkg_version = None
    try:
        import zipfile
        with zipfile.ZipFile(target, "r") as zf:
            with zf.open("version.json") as f:
                import json as _json
                pkg_version = _json.loads(f.read().decode("utf-8"))
    except Exception as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"无法读取更新包中的 version.json: {e}",
            "message": "version.json 缺失或格式错误",
        }

    # 解析当前版本
    current = _read_version().get("current_version", "1.0.0")
    new_version = (pkg_version or {}).get("current_version", "")

    # 跑 updater.py
    cmd = ["python3", str(UPDATER_SCRIPT), str(target)]
    result = _run_subprocess(cmd, cwd=PROJECT_ROOT, timeout=timeout)

    ok = result["returncode"] == 0
    # 即使 returncode=0，updater.py 内部可能 return 0 之前已经报错；让前端看 stdout/stderr
    return {
        "ok": ok,
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "duration_seconds": result["duration_seconds"],
        "current_version": current,
        "new_version": new_version,
        "package_path": str(target),
        "message": (
            f"更新完成（{current} -> {new_version}），请重启服务"
            if ok
            else "更新失败，可尝试从 backup 目录回滚"
        ),
        "restart_required": ok,
    }
