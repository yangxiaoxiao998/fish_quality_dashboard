from __future__ import annotations

import json
import os
import platform
import re
import shutil
import string
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO


PROJECT_ROOT = Path(__file__).resolve().parents[5]
UPDATE_DIR = PROJECT_ROOT / "backend" / "app" / "services" / "update"
HOME_DIR = Path.home()
CONFIG_ROOT = Path(os.environ.get("FISH_CONFIG_ROOT") or PROJECT_ROOT / ".config").expanduser()
DATA_ROOT = Path(os.environ.get("FISH_DATA_ROOT") or PROJECT_ROOT / "data").expanduser()
STATE_ROOT = Path(os.environ.get("FISH_STATE_ROOT") or DATA_ROOT / "state").expanduser()
INSTALL_ROOT = Path(os.environ.get("FISH_INSTALL_ROOT") or PROJECT_ROOT).expanduser()

UPDATER_SCRIPT = UPDATE_DIR / "updater.py"
UPDATE_PACKAGE_DIR = DATA_ROOT / "uploads" / "update_packages"
SOFTWARE_UPDATE_LOG_DIR = STATE_ROOT / "software_update"
TASK_STATUS_DIR = SOFTWARE_UPDATE_LOG_DIR / "tasks"
TASK_LOG_DIR = SOFTWARE_UPDATE_LOG_DIR / "runs"
BACKUP_ROOT = PROJECT_ROOT / "backups"
VERSION_FILE = PROJECT_ROOT / "version.json"
CONFIG_META_FILE = CONFIG_ROOT / "config-meta.json"

DEFAULT_VERSION = "1.0.0"
DEFAULT_CONFIG_VERSION = 1
XZ_MAGIC = b"\xfd7zXZ\x00"
PACKAGE_SUFFIXES = (".tar.xz", ".txz")


def ensure_manager_dirs() -> None:
    for path in (
        UPDATE_PACKAGE_DIR,
        SOFTWARE_UPDATE_LOG_DIR,
        TASK_STATUS_DIR,
        TASK_LOG_DIR,
        BACKUP_ROOT,
        CONFIG_ROOT,
        DATA_ROOT,
        STATE_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    value = float(size)
    for unit in ("K", "M", "G", "T"):
        value /= 1024.0
        if value < 1024:
            return f"{value:.2f}{unit}"
    return f"{value / 1024.0:.2f}P"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_update_package_name(name: str) -> bool:
    lower_name = name.lower()
    return any(lower_name.endswith(suffix) for suffix in PACKAGE_SUFFIXES)


def read_current_version() -> str:
    if VERSION_FILE.exists():
        try:
            payload = _read_json(VERSION_FILE)
            return str(payload.get("version") or payload.get("current_version") or DEFAULT_VERSION)
        except Exception:
            return DEFAULT_VERSION
    return DEFAULT_VERSION


def read_config_meta() -> dict[str, Any]:
    if not CONFIG_META_FILE.exists():
        return {"config_version": DEFAULT_CONFIG_VERSION}
    try:
        payload = _read_json(CONFIG_META_FILE)
    except Exception:
        return {"config_version": DEFAULT_CONFIG_VERSION}
    if not isinstance(payload, dict):
        return {"config_version": DEFAULT_CONFIG_VERSION}
    return payload


def read_current_config_version() -> int:
    payload = read_config_meta()
    try:
        return int(payload.get("config_version") or DEFAULT_CONFIG_VERSION)
    except (TypeError, ValueError):
        return DEFAULT_CONFIG_VERSION


def current_link_targets() -> dict[str, str | None]:
    algo_python = os.environ.get("ALGORITHM_PYTHON", "").strip() or None
    return {
        "backend": str(PROJECT_ROOT / "backend"),
        "frontend": str(PROJECT_ROOT / "frontend"),
        "algorithms": str(PROJECT_ROOT / "algorithms"),
        "algo-env": algo_python,
    }


def _stream_uploaded_package(stream: BinaryIO, target: Path) -> int:
    tmp_target = target.with_name(target.name + ".part")
    size_bytes = 0
    try:
        with tmp_target.open("wb") as handle:
            header = stream.read(len(XZ_MAGIC))
            if not header:
                raise ValueError("uploaded file is empty")
            if header != XZ_MAGIC:
                raise ValueError("file is not a valid .tar.xz archive (magic bytes do not match)")
            handle.write(header)
            size_bytes += len(header)

            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                size_bytes += len(chunk)

        tmp_target.replace(target)
        return size_bytes
    except Exception:
        tmp_target.unlink(missing_ok=True)
        raise


def save_uploaded_package_stream(stream: BinaryIO, original_filename: str) -> dict[str, Any]:
    ensure_manager_dirs()
    if not _is_update_package_name(original_filename):
        raise ValueError("only .tar.xz and .txz update packages are accepted")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_name = f"{ts}_{_safe_name(original_filename)}"
    target = UPDATE_PACKAGE_DIR / package_name
    size_bytes = _stream_uploaded_package(stream, target)
    return {
        "ok": True,
        "package_name": package_name,
        "package_path": str(target),
        "name": package_name,
        "path": str(target),
        "size_bytes": size_bytes,
        "size": format_bytes(size_bytes),
    }


def save_uploaded_package(file_bytes: bytes, original_filename: str) -> dict[str, Any]:
    return save_uploaded_package_stream(BytesIO(file_bytes), original_filename)


def _validate_update_package_path(target: Path, *, field_name: str = "package_path") -> Path:
    if not target.is_file():
        raise ValueError(f"update package not found: {target}")
    if not _is_update_package_name(target.name):
        raise ValueError(f"{field_name} must be .tar.xz or .txz: {target}")

    with target.open("rb") as handle:
        header = handle.read(len(XZ_MAGIC))

    if not header:
        raise ValueError(f"update package is empty: {target}")
    if header != XZ_MAGIC:
        raise ValueError(f"update package is not a valid .tar.xz archive: {target}")
    return target


def import_update_package_from_path(source_path: str) -> dict[str, Any]:
    ensure_manager_dirs()
    if not source_path or not str(source_path).strip():
        raise ValueError("source_path is required")

    try:
        source = Path(str(source_path).strip()).expanduser().resolve()
    except Exception as exc:
        raise ValueError(f"invalid source_path: {source_path}") from exc

    target = _validate_update_package_path(source, field_name="source_path")
    size_bytes = target.stat().st_size
    return {
        "ok": True,
        "package_name": target.name,
        "package_path": str(target),
        "name": target.name,
        "path": str(target),
        "size_bytes": size_bytes,
        "size": format_bytes(size_bytes),
        "source_path": str(source),
        "managed_copy": False,
    }


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _windows_drive_roots() -> list[Path]:
    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive)
    return roots


def _detect_usb_mounts() -> list[dict[str, str]]:
    if platform.system().lower().startswith("win"):
        mounts: list[dict[str, str]] = []
        for drive in _windows_drive_roots():
            try:
                total, used, free = shutil.disk_usage(str(drive))
            except Exception:
                continue
            mounts.append(
                {
                    "path": str(drive),
                    "free": format_bytes(free),
                    "total": format_bytes(total),
                }
            )
        return mounts

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or HOME_DIR.name or "root"
    candidates: list[Path] = []
    for base in (
        Path(f"/media/{user}"),
        Path(f"/run/media/{user}"),
        Path("/mnt"),
    ):
        if not base.exists():
            continue
        try:
            for child in base.iterdir():
                if child.is_dir():
                    candidates.append(child)
        except PermissionError:
            continue

    mounts: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if not os.access(resolved, os.R_OK):
                continue
            total, used, free = shutil.disk_usage(str(resolved))
            mounts.append(
                {
                    "path": str(resolved),
                    "free": format_bytes(free),
                    "total": format_bytes(total),
                }
            )
        except Exception:
            continue
    return mounts


def _build_browse_roots() -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_root(path: Path, label: str, kind: str, **extra: Any) -> None:
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            return
        if not resolved.exists() or not resolved.is_dir():
            return
        key = str(resolved)
        if key in seen:
            return
        item: dict[str, Any] = {
            "label": label,
            "path": key,
            "kind": kind,
        }
        item.update(extra)
        roots.append(item)
        seen.add(key)

    add_root(HOME_DIR, "Home", "home")
    add_root(HOME_DIR / "Downloads", "Downloads", "downloads")
    add_root(HOME_DIR / "Desktop", "Desktop", "desktop")
    add_root(UPDATE_PACKAGE_DIR, "Imported Packages", "managed")

    if platform.system().lower().startswith("win"):
        for drive in _windows_drive_roots():
            label = f"Drive {drive.drive or drive}"
            add_root(drive, label, "drive")
    else:
        for mount in _detect_usb_mounts():
            mount_path = Path(mount["path"])
            label = f"USB {mount_path.name}" if mount_path.name else "USB"
            add_root(
                mount_path,
                label,
                "usb",
                free=mount.get("free"),
                total=mount.get("total"),
            )

    if not roots:
        add_root(Path("/"), "Filesystem", "filesystem")
    return roots


def _ensure_allowed_browse_path(target: Path, roots: list[dict[str, Any]]) -> Path:
    for root_info in roots:
        root_path = Path(str(root_info["path"])).resolve()
        if target == root_path or _path_is_within(target, root_path):
            return target
    raise ValueError(f"browse path is outside allowed roots: {target}")


def browse_importable_package_files(path: str | None = None) -> dict[str, Any]:
    ensure_manager_dirs()
    roots = _build_browse_roots()
    if not roots:
        raise ValueError("no browse roots available")

    if path and str(path).strip():
        try:
            current = Path(str(path).strip()).expanduser().resolve()
        except Exception as exc:
            raise ValueError(f"invalid browse path: {path}") from exc
    else:
        preferred = HOME_DIR / "Downloads"
        current = preferred.resolve() if preferred.exists() else Path(str(roots[0]["path"])).resolve()

    if current.is_file():
        if _is_update_package_name(current.name):
            current = current.parent.resolve()
        else:
            raise ValueError(f"browse path must be a directory: {current}")

    if not current.exists():
        raise ValueError(f"browse path not found: {current}")
    if not current.is_dir():
        raise ValueError(f"browse path must be a directory: {current}")

    current = _ensure_allowed_browse_path(current, roots)

    parent_path: str | None = None
    if current.parent != current:
        try:
            parent_candidate = _ensure_allowed_browse_path(current.parent.resolve(), roots)
            if parent_candidate != current:
                parent_path = str(parent_candidate)
        except ValueError:
            parent_path = None

    directories: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    try:
        children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except PermissionError as exc:
        raise ValueError(f"permission denied while browsing: {current}") from exc

    for child in children:
        if child.name.startswith("."):
            continue

        try:
            resolved_child = child.resolve()
        except Exception:
            continue

        if child.is_dir():
            try:
                _ensure_allowed_browse_path(resolved_child, roots)
            except ValueError:
                continue
            directories.append(
                {
                    "name": child.name,
                    "path": str(resolved_child),
                }
            )
            continue

        if not child.is_file() or not _is_update_package_name(child.name):
            continue

        stat = child.stat()
        files.append(
            {
                "name": child.name,
                "path": str(resolved_child),
                "size_bytes": stat.st_size,
                "size": format_bytes(stat.st_size),
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )

    return {
        "ok": True,
        "current_path": str(current),
        "parent_path": parent_path,
        "roots": roots,
        "directories": directories,
        "files": files,
    }


def list_update_packages() -> list[dict[str, Any]]:
    ensure_manager_dirs()
    packages: list[dict[str, Any]] = []
    for path in sorted(
        [item for item in UPDATE_PACKAGE_DIR.iterdir() if item.is_file() and _is_update_package_name(item.name)],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        stat = path.stat()
        packages.append(
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "size": format_bytes(stat.st_size),
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return packages


def resolve_package_path(package_path: str) -> Path:
    if not package_path:
        raise ValueError("package_path cannot be empty")
    try:
        target = Path(package_path).expanduser().resolve()
    except Exception as exc:
        raise ValueError(f"invalid package_path: {package_path}") from exc
    return _validate_update_package_path(target)


def resolve_backup_dir(backup_dir: str) -> Path:
    if not backup_dir:
        raise ValueError("backup_dir cannot be empty")
    target = Path(backup_dir).expanduser().resolve()
    base = BACKUP_ROOT.resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"backup_dir is outside managed directory: {backup_dir}") from exc
    if not target.is_dir():
        raise ValueError(f"backup directory not found: {target}")
    return target


def new_task_id(task_type: str) -> str:
    return f"{task_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def task_state_path(task_id: str) -> Path:
    return TASK_STATUS_DIR / f"{task_id}.json"


def task_log_path(task_id: str) -> Path:
    return TASK_LOG_DIR / f"{task_id}.log"


def write_task_state(task: dict[str, Any]) -> None:
    ensure_manager_dirs()
    task_state_path(str(task["task_id"])).write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_task_state(task_id: str) -> dict[str, Any]:
    path = task_state_path(task_id)
    if not path.is_file():
        raise FileNotFoundError(f"task not found: {task_id}")
    return _read_json(path)


def append_task_log(task_id: str, text: str) -> None:
    ensure_manager_dirs()
    with task_log_path(task_id).open("a", encoding="utf-8") as handle:
        handle.write(text)


def read_task_log(task_id: str, max_bytes: int = 200 * 1024) -> str:
    path = task_log_path(task_id)
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    return raw.decode("utf-8", errors="replace")


def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    ensure_manager_dirs()
    tasks: list[dict[str, Any]] = []
    for path in sorted(
        TASK_STATUS_DIR.glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:limit]:
        try:
            tasks.append(_read_json(path))
        except Exception:
            continue
    return tasks


def read_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"backup manifest not found: {manifest_path}")
    return _read_json(manifest_path)


def list_backups(limit: int = 50) -> list[dict[str, Any]]:
    ensure_manager_dirs()
    entries: list[dict[str, Any]] = []
    for path in sorted(
        BACKUP_ROOT.glob("update_backup_*"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:limit]:
        stat = path.stat()
        item: dict[str, Any] = {
            "name": path.name,
            "path": str(path),
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = read_backup_manifest(path)
                components = manifest.get("components", [])
                item.update(
                    {
                        "status": manifest.get("status"),
                        "current_version": manifest.get("current_version"),
                        "new_version": manifest.get("new_version"),
                        "targets": [
                            component.get("name")
                            for component in components
                            if isinstance(component, dict)
                        ],
                    }
                )
            except Exception as exc:
                item["manifest_error"] = str(exc)
        entries.append(item)
    return entries
