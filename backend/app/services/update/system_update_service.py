#!/usr/bin/env python
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

from app.paths import DATA_DIR, PROJECT_ROOT


UPDATE_DIR = Path(__file__).resolve().parent
UPDATER_SCRIPT = UPDATE_DIR / "updater.py"
VERSION_FILE = PROJECT_ROOT / "version.json"
UPDATE_PACKAGE_DIR = DATA_DIR / "uploads" / "update_packages"
DEFAULT_VERSION = {"current_version": "1.0.0", "version": "1.0.0"}
DEFAULT_UPGRADE_MANAGER_BASE_URL = "http://127.0.0.1:8012"
PACKAGE_SUFFIXES = (".tar.xz", ".txz")
XZ_MAGIC = b"\xfd7zXZ\x00"


def _ensure_dirs() -> None:
    UPDATE_PACKAGE_DIR.mkdir(parents=True, exist_ok=True)


def _is_update_package_name(name: str) -> bool:
    lower_name = name.lower()
    return any(lower_name.endswith(suffix) for suffix in PACKAGE_SUFFIXES)


def _read_version() -> Dict[str, Any]:
    if not VERSION_FILE.exists():
        try:
            VERSION_FILE.write_text(
                json.dumps(
                    {
                        "version": "1.0.0",
                        "current_version": "1.0.0",
                        "build_time": "",
                        "git_commit": "",
                        "min_config_version": 1,
                        "bundle_format_version": 1,
                        "package_format": "source_tar_xz_v1",
                        "package_mode": "runtime_source",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            return {**DEFAULT_VERSION, "_error_reading": str(exc)}
    try:
        return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {**DEFAULT_VERSION, "_error_reading": str(exc)}


def _list_uploaded_packages() -> list[Dict[str, Any]]:
    uploaded: list[Dict[str, Any]] = []
    for path in sorted(
        [
            item
            for item in UPDATE_PACKAGE_DIR.iterdir()
            if item.is_file() and _is_update_package_name(item.name)
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        stat = path.stat()
        uploaded.append(
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "size": _format_bytes(stat.st_size),
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return uploaded


def get_update_summary() -> Dict[str, Any]:
    _ensure_dirs()
    info = _read_version()
    current_version = str(info.get("version") or info.get("current_version") or "1.0.0")
    return {
        "ok": True,
        "current_version": current_version,
        "version_file": str(VERSION_FILE),
        "version_file_exists": VERSION_FILE.exists(),
        "updater_script": str(UPDATER_SCRIPT),
        "updater_script_exists": UPDATER_SCRIPT.exists(),
    }


def get_update_status() -> Dict[str, Any]:
    summary = get_update_summary()
    try:
        uploaded = _list_uploaded_packages()
    except Exception as exc:
        uploaded = []
        summary["_error_listing"] = str(exc)
    return {
        **summary,
        "uploaded_packages": uploaded,
    }


def get_update_manager_info() -> Dict[str, Any]:
    base_url = os.environ.get(
        "UPGRADE_MANAGER_BASE_URL",
        DEFAULT_UPGRADE_MANAGER_BASE_URL,
    ).strip()
    base_url = base_url.rstrip("/")
    api_base_url = f"{base_url}/api/upgrade" if base_url else ""
    result: Dict[str, Any] = {
        "ok": True,
        "base_url": base_url,
        "api_base_url": api_base_url,
        "available": bool(base_url),
        "healthy": False,
        "error": None,
        "health": None,
    }
    if not base_url:
        result["available"] = False
        result["error"] = "UPGRADE_MANAGER_BASE_URL is not configured"
        return result

    try:
        with urllib.request.urlopen(f"{api_base_url}/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result["health"] = payload
        result["healthy"] = bool(payload.get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        result["healthy"] = False
        result["error"] = str(exc)
    return result


def save_update_package(file_bytes: bytes, original_filename: str) -> Dict[str, Any]:
    _ensure_dirs()

    if not _is_update_package_name(original_filename):
        raise ValueError("only .tar.xz and .txz update packages are allowed")
    if not file_bytes.startswith(XZ_MAGIC):
        raise ValueError("file is not a valid .tar.xz archive")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", original_filename)
    package_name = f"{ts}_{safe_name}"
    target = UPDATE_PACKAGE_DIR / package_name
    target.write_bytes(file_bytes)

    return {
        "ok": True,
        "package_name": package_name,
        "package_path": str(target),
        "size_bytes": len(file_bytes),
        "size": _format_bytes(len(file_bytes)),
    }


def _validate_update_package_path(target: Path) -> Optional[Path]:
    if not target.is_file() or not _is_update_package_name(target.name):
        return None
    try:
        with target.open("rb") as handle:
            header = handle.read(len(XZ_MAGIC))
    except OSError:
        return None
    if not header or header != XZ_MAGIC:
        return None
    return target


def _resolve_package_path(package_path: str) -> Optional[Path]:
    if not package_path:
        return None
    try:
        target = Path(package_path).expanduser().resolve()
    except Exception:
        return None
    return _validate_update_package_path(target)


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    value = float(size)
    for unit in ("K", "M", "G", "T"):
        value /= 1024.0
        if value < 1024:
            return f"{value:.2f}{unit}"
    return f"{value / 1024.0:.2f}P"


def _normalize_archive_member(name: str) -> PurePosixPath:
    raw = name.replace("\\", "/")
    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        raise ValueError(f"archive entry must be relative: {name}")
    if any(part == ".." for part in normalized.parts):
        raise ValueError(f"archive entry escapes extraction root: {name}")
    return normalized


def _find_payload_root(zf: tarfile.TarFile) -> Optional[str]:
    candidates: dict[int, list[str]] = {}
    names = zf.getnames()
    name_set = set(names)
    for name in names:
        normalized = _normalize_archive_member(name)
        if normalized.name != "version.json":
            continue
        parent = normalized.parent.as_posix() or "."
        manifest_member = f"{parent}/manifest.json" if parent != "." else "manifest.json"
        if manifest_member not in name_set:
            continue
        depth = len([part for part in normalized.parts[:-1] if part])
        candidates.setdefault(depth, []).append(parent)

    if not candidates:
        return None

    shallowest = min(candidates)
    selected = sorted(set(candidates[shallowest]))
    if len(selected) != 1:
        return None
    return selected[0]


def _member_name(package_root: str, relative_name: str) -> str:
    return f"{package_root}/{relative_name}" if package_root != "." else relative_name


def _peek_package_version(target: Path) -> str:
    with tarfile.open(target, "r:xz") as zf:
        package_root = _find_payload_root(zf)
        if not package_root:
            raise ValueError("manifest.json/version.json not found or ambiguous")
        version_member = _member_name(package_root, "version.json")
        extracted = zf.extractfile(version_member)
        if extracted is None:
            raise ValueError("unable to read version.json from package")
        payload = json.loads(extracted.read().decode("utf-8"))
    version = str((payload or {}).get("version") or (payload or {}).get("current_version") or "").strip()
    if not version:
        raise ValueError("package version.json is missing version/current_version")
    return version


def _run_subprocess(cmd: list[str], cwd: Path, timeout: int) -> Dict[str, Any]:
    started = time.time()
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
            "duration_seconds": round(time.time() - started, 2),
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"command timed out: {timeout}s",
            "duration_seconds": round(time.time() - started, 2),
            "timeout": True,
        }
    except Exception as exc:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"execution failed: {exc}",
            "duration_seconds": round(time.time() - started, 2),
        }


def run_update(package_path: str, timeout: int = 3600) -> Dict[str, Any]:
    if not UPDATER_SCRIPT.exists():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"update script not found: {UPDATER_SCRIPT}",
            "message": "update/updater.py does not exist",
        }

    target = _resolve_package_path(package_path)
    if target is None:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"package_path is invalid, unreadable, or not a .tar.xz archive: {package_path}",
            "message": "package_path validation failed",
        }

    try:
        new_version = _peek_package_version(target)
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"failed to read version.json from update package: {exc}",
            "message": "version.json is missing or invalid",
        }

    current_version = str(
        _read_version().get("version") or _read_version().get("current_version") or "1.0.0"
    )
    command = [
        sys.executable,
        str(UPDATER_SCRIPT),
        "apply",
        str(target),
        "--install-root",
        str(PROJECT_ROOT),
    ]
    result = _run_subprocess(command, cwd=PROJECT_ROOT, timeout=timeout)
    ok = result["returncode"] == 0

    return {
        "ok": ok,
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "duration_seconds": result["duration_seconds"],
        "current_version": current_version,
        "new_version": new_version,
        "package_path": str(target),
        "message": (
            f"update completed ({current_version} -> {new_version})"
            if ok
            else "update failed; check backups and task logs for rollback details"
        ),
        "restart_required": ok,
    }
