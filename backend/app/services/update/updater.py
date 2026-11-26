#!/usr/bin/env python3
from __future__ import annotations

import argparse
import encodings.idna  # noqa: F401 - preload before a frozen runtime replaces itself
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from app.runtime_paths import INSTALL_ROOT as RUNTIME_INSTALL_ROOT, resource_path

PROJECT_ROOT = RUNTIME_INSTALL_ROOT
DEFAULT_VERSION = "1.0.0"
DEFAULT_CONFIG_VERSION = 1
DEFAULT_HEALTHCHECK_URL = "http://127.0.0.1:8000/api/health"
PACKAGE_EXTENSIONS = (".tar.xz", ".txz")
XZ_MAGIC = b"\xfd7zXZ\x00"


class UpdateError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


@dataclass(frozen=True)
class BundleComponent:
    name: str
    archive: str
    target_subdir: str
    current_link: str


@dataclass(frozen=True)
class BundlePackage:
    package_path: Path
    package_root: str
    version_payload: dict[str, Any]
    manifest_payload: dict[str, Any]
    components: tuple[BundleComponent, ...]
    current_version: str
    new_version: str
    current_config_version: int
    min_config_version: int
    bundle_format_version: int
    healthcheck: str


def _is_update_package(path: Path) -> bool:
    lower_name = path.name.lower()
    return any(lower_name.endswith(ext) for ext in PACKAGE_EXTENSIONS)


def _validate_update_package_file(package_path: Path) -> Path:
    if not package_path.is_file():
        raise UpdateError(f"update package not found: {package_path}")
    if not _is_update_package(package_path):
        raise UpdateError(f"update package must be .tar.xz or .txz: {package_path}")

    with package_path.open("rb") as package_stream:
        header = package_stream.read(len(XZ_MAGIC))
    if header != XZ_MAGIC:
        raise UpdateError(f"update package is not a valid xz archive: {package_path}")

    # Opening an xz-compressed tar only reads enough data to validate its first
    # header.  It does not walk or decompress the complete package.
    try:
        with tarfile.open(package_path, mode="r:xz"):
            pass
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"failed to read tar.xz update package: {exc}") from exc
    return package_path.resolve()


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError(f"json payload must be an object: {path}")
    return payload


def _read_version_file(version_file: Path) -> str:
    """Read a version from source or a packaged release directory.

    Source installs keep ``version.json`` at the repository root.  The first
    packaged releases only put it under ``backend/``; accepting both layouts
    lets an artifact updater upgrade those installations too.
    """

    candidates = [version_file]
    if version_file.name == "version.json":
        candidates.append(version_file.parent / "backend" / "version.json")
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = _read_json_file(candidate)
        except Exception:
            continue
        value = str(payload.get("version") or payload.get("current_version") or "").strip()
        if value:
            return value
    return DEFAULT_VERSION


def _read_current_config_version(config_root: Path) -> int:
    config_meta = config_root / "config-meta.json"
    if not config_meta.exists():
        return DEFAULT_CONFIG_VERSION
    try:
        payload = _read_json_file(config_meta)
        return int(payload.get("config_version") or DEFAULT_CONFIG_VERSION)
    except Exception:
        return DEFAULT_CONFIG_VERSION


def _normalize_member_path(name: str) -> PurePosixPath:
    raw = name.replace("\\", "/")
    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        raise UpdateError(f"archive entry must be relative: {name}")
    if any(part == ".." for part in normalized.parts):
        raise UpdateError(f"archive entry escapes extraction root: {name}")
    return normalized


def _read_tar_member_json(
    archive: tarfile.TarFile,
    member_name: str,
) -> dict[str, Any]:
    member = archive.getmember(member_name)
    extracted = archive.extractfile(member)
    if extracted is None:
        raise UpdateError(f"unable to read archive member: {member_name}")
    payload = json.loads(extracted.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError(f"json payload must be an object: {member_name}")
    return payload


def _find_payload_root(archive: tarfile.TarFile) -> str:
    candidates: dict[int, list[str]] = {}
    names = archive.getnames()
    name_set = set(names)
    for raw_name in names:
        normalized = _normalize_member_path(raw_name)
        if normalized.name != "version.json":
            continue
        parent = normalized.parent.as_posix()
        manifest_candidate = f"{parent}/manifest.json" if parent != "." else "manifest.json"
        if manifest_candidate not in name_set:
            continue
        depth = len([part for part in normalized.parts[:-1] if part])
        candidates.setdefault(depth, []).append(parent or ".")

    if not candidates:
        raise UpdateError("manifest.json/version.json not found in update package")

    shallowest = min(candidates)
    roots = sorted(set(candidates[shallowest]))
    if len(roots) != 1:
        joined = ", ".join(roots)
        raise UpdateError(f"multiple package roots found in update package: {joined}")
    return roots[0]


def _payload_member_name(package_root: str, relative_name: str) -> str:
    return f"{package_root}/{relative_name}" if package_root != "." else relative_name


def _tar_contains_prefix(archive: tarfile.TarFile, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/") + "/"
    for member_name in archive.getnames():
        if member_name == prefix.rstrip("/") or member_name.startswith(normalized_prefix):
            return True
    return False


def _is_artifact_package(bundle: BundlePackage) -> bool:
    mode = str(
        bundle.version_payload.get("package_mode")
        or bundle.manifest_payload.get("package_mode")
        or ""
    ).strip().lower()
    package_format = str(bundle.version_payload.get("package_format") or "").strip().lower()
    return mode in {"runtime_artifact", "packaged", "artifact"} or package_format.startswith(
        "release_artifact_"
    )


def _is_artifact_install(install_root: Path) -> bool:
    """Return whether *install_root* looks like a unified packaged release."""

    return (
        (install_root / "backend" / "fish-quality-backend").is_file()
        and (install_root / "frontend").is_dir()
        and ((install_root / "start.sh").is_file() or (install_root / "backend" / "start_backend.sh").is_file())
    )


def _normalize_install_root(install_root: Path) -> Path:
    """Normalize a backend executable root to the unified release root."""

    install_root = install_root.resolve()
    if _is_artifact_install(install_root):
        return install_root
    # Packaged runtime scripts intentionally set FISH_INSTALL_ROOT to the
    # backend directory so application data resolves naturally.  The updater,
    # however, replaces both sibling backend/frontend artifacts and therefore
    # operates one level above it.
    if (
        install_root.name == "backend"
        and (install_root / "fish-quality-backend").is_file()
        and _is_artifact_install(install_root.parent)
    ):
        return install_root.parent
    return install_root


def _build_bundle_from_metadata(
    *,
    package_path: Path,
    package_root: str,
    version_payload: dict[str, Any],
    manifest_payload: dict[str, Any],
    install_root: Path,
    config_root: Path,
) -> BundlePackage:
    new_version = str(
        version_payload.get("version") or version_payload.get("current_version") or ""
    ).strip()
    if not new_version:
        raise UpdateError("version.json is missing version/current_version")

    try:
        min_config_version = int(version_payload.get("min_config_version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise UpdateError("version.json has invalid min_config_version") from exc

    try:
        bundle_format_version = int(version_payload.get("bundle_format_version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise UpdateError("version.json has invalid bundle_format_version") from exc

    raw_components = manifest_payload.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise UpdateError("manifest.json must contain a non-empty components list")

    components: list[BundleComponent] = []
    seen_names: set[str] = set()
    for item in raw_components:
        if not isinstance(item, dict):
            raise UpdateError("manifest.json contains an invalid component entry")
        name = str(item.get("name") or "").strip()
        archive_name = str(item.get("archive") or "").strip()
        target_subdir = str(item.get("target_subdir") or name).strip()
        current_link = str(item.get("current_link") or target_subdir).strip()
        if not name or not archive_name:
            raise UpdateError("manifest.json contains an incomplete component entry")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise UpdateError(f"manifest.json contains an unsafe component name: {name}")
        for field_name, field_value in (
            ("archive", archive_name),
            ("target_subdir", target_subdir),
            ("current_link", current_link),
        ):
            normalized_field = _normalize_member_path(field_value)
            if normalized_field == PurePosixPath("."):
                raise UpdateError(
                    f"manifest.json contains an invalid {field_name} for component {name}"
                )
        if name in seen_names:
            raise UpdateError(f"duplicate component in manifest.json: {name}")
        seen_names.add(name)
        components.append(
            BundleComponent(
                name=name,
                archive=archive_name,
                target_subdir=target_subdir,
                current_link=current_link,
            )
        )

    healthcheck_payload = manifest_payload.get("healthcheck", DEFAULT_HEALTHCHECK_URL)
    if isinstance(healthcheck_payload, dict):
        healthcheck = str(
            healthcheck_payload.get("backend") or DEFAULT_HEALTHCHECK_URL
        ).strip()
    else:
        healthcheck = str(healthcheck_payload or DEFAULT_HEALTHCHECK_URL).strip()
    if not healthcheck:
        healthcheck = DEFAULT_HEALTHCHECK_URL

    current_config_version = _read_current_config_version(config_root)
    if current_config_version < min_config_version:
        raise UpdateError(
            "current config version is too old for this update package: "
            f"{current_config_version} < {min_config_version}"
        )

    return BundlePackage(
        package_path=package_path.resolve(),
        package_root=package_root,
        version_payload=version_payload,
        manifest_payload=manifest_payload,
        components=tuple(components),
        current_version=_read_version_file(install_root / "version.json"),
        new_version=new_version,
        current_config_version=current_config_version,
        min_config_version=min_config_version,
        bundle_format_version=bundle_format_version,
        healthcheck=healthcheck,
    )


def inspect_update_package(
    package_path: Path,
    *,
    install_root: Path,
    config_root: Path,
) -> BundlePackage:
    package_path = _validate_update_package_file(package_path)
    archive = tarfile.open(package_path, mode="r:xz")

    with archive:
        for member_name in archive.getnames():
            _normalize_member_path(member_name)

        package_root = _find_payload_root(archive)
        version_payload = _read_tar_member_json(
            archive,
            _payload_member_name(package_root, "version.json"),
        )
        manifest_payload = _read_tar_member_json(
            archive,
            _payload_member_name(package_root, "manifest.json"),
        )

        checksums_name = _payload_member_name(package_root, "checksums.sha256")
        if checksums_name not in archive.getnames():
            raise UpdateError("checksums.sha256 is missing from update package")
        bundle = _build_bundle_from_metadata(
            package_path=package_path,
            package_root=package_root,
            version_payload=version_payload,
            manifest_payload=manifest_payload,
            install_root=install_root,
            config_root=config_root,
        )
        for component in bundle.components:
            if not _tar_contains_prefix(
                archive, _payload_member_name(package_root, component.archive)
            ):
                raise UpdateError(
                    "component payload not found in update package: "
                    f"{component.archive} (component={component.name})"
                )
    return bundle


def _bundle_precheck_result(bundle: BundlePackage) -> dict[str, object]:
    manifest = bundle.manifest_payload
    artifact_package = _is_artifact_package(bundle)
    return {
        "package_path": str(bundle.package_path),
        "current_version": bundle.current_version,
        "new_version": bundle.new_version,
        "package_root": bundle.package_root,
        "updated_targets": [component.name for component in bundle.components],
        "components": [
            {
                "name": component.name,
                "archive": component.archive,
                "target_subdir": component.target_subdir,
                "current_link": component.current_link,
            }
            for component in bundle.components
        ],
        "includes_algo_env": False,
        "min_config_version": bundle.min_config_version,
        "current_config_version": bundle.current_config_version,
        "bundle_format_version": bundle.bundle_format_version,
        "requires_restart": ["backend", "frontend"] if artifact_package else ["backend"],
        "healthcheck": bundle.healthcheck,
        "package_format": str(
            bundle.version_payload.get(
                "package_format",
                "release_artifact_tar_xz_v1" if artifact_package else "source_tar_xz_v1",
            )
        ),
        "package_mode": str(
            bundle.version_payload.get(
                "package_mode", "runtime_artifact" if artifact_package else "runtime_source"
            )
        ),
        "build_time": bundle.version_payload.get("build_time"),
        "git_commit": bundle.version_payload.get("git_commit"),
        "frontend_runtime_mode": manifest.get("frontend_runtime_mode"),
        "backend_runtime_mode": manifest.get("backend_runtime_mode"),
        "preserve_paths": manifest.get("preserve_paths") or [],
        "excluded_paths": manifest.get("excluded_paths") or [],
    }


def _version_from_package_name(package_path: Path) -> str | None:
    matches = re.findall(
        r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)(?![0-9])",
        package_path.name,
    )
    return matches[-1] if matches else None


def _basic_artifact_precheck_result(
    package_path: Path,
    *,
    install_root: Path,
    config_root: Path,
) -> dict[str, object]:
    """Return task metadata without walking a potentially very large archive."""

    package_path = _validate_update_package_file(package_path)
    current_version = _read_version_file(install_root / "version.json")
    inferred_version = _version_from_package_name(package_path)
    components = [
        {
            "name": name,
            "archive": name,
            "target_subdir": name,
            "current_link": name,
        }
        for name in ("backend", "frontend")
    ]
    return {
        "package_path": str(package_path),
        "current_version": current_version,
        # Package names produced by the build script contain the version.  If
        # a developer renames one, the runner replaces this hint with the
        # authoritative value read after extraction.
        "new_version": inferred_version or current_version,
        "package_root": "update_package",
        "updated_targets": ["backend", "frontend"],
        "components": components,
        "includes_algo_env": False,
        "min_config_version": 1,
        "current_config_version": _read_current_config_version(config_root),
        "bundle_format_version": 1,
        "requires_restart": ["backend", "frontend"],
        "healthcheck": DEFAULT_HEALTHCHECK_URL,
        "package_format": "release_artifact_tar_xz_v1",
        "package_mode": "runtime_artifact",
        "build_time": None,
        "git_commit": None,
        "frontend_runtime_mode": "appimage",
        "backend_runtime_mode": "pyinstaller_onedir",
        "preserve_paths": [],
        "excluded_paths": [],
        "precheck_mode": "basic",
        "metadata_verified": False,
        "version_inferred_from_filename": inferred_version is not None,
    }


def precheck_update_package(
    package_path: Path,
    *,
    install_root: Path = PROJECT_ROOT,
    config_root: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    install_root = _normalize_install_root((project_root or install_root).resolve())
    default_config_root = (
        install_root / "backend" / "config"
        if _is_artifact_install(install_root)
        else install_root / ".config"
    )
    config_root = (config_root or default_config_root).resolve()
    if _is_artifact_install(install_root):
        return _basic_artifact_precheck_result(
            package_path.resolve(),
            install_root=install_root,
            config_root=config_root,
        )
    bundle = inspect_update_package(
        package_path.resolve(),
        install_root=install_root,
        config_root=config_root,
    )
    return _bundle_precheck_result(bundle)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _extract_update_archive(package_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(package_path, mode="r:xz") as archive:
            # The standard data filter permits links that resolve inside the
            # fresh destination while rejecting absolute or escaping links,
            # device files and other unsafe member types.  Filtering as each
            # member is extracted avoids a second full pass over large xz files.
            archive.extractall(destination, filter="data")
    except tarfile.FilterError as exc:
        raise UpdateError(f"unsafe archive member: {exc}") from exc
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"failed to extract update package: {exc}") from exc


def _find_extracted_payload_root(destination: Path) -> tuple[Path, str]:
    preferred = destination / "update_package"
    candidates = [preferred, destination]
    candidates.extend(
        child for child in sorted(destination.iterdir()) if child.is_dir() and child != preferred
    )
    matches = [
        candidate
        for candidate in candidates
        if (candidate / "version.json").is_file()
        and (candidate / "manifest.json").is_file()
    ]
    if not matches:
        raise UpdateError("manifest.json/version.json not found in extracted update package")

    payload_root = matches[0]
    package_root = "." if payload_root == destination else payload_root.relative_to(destination).as_posix()
    return payload_root, package_root


def _verify_extracted_checksums(payload_root: Path) -> None:
    checksums_path = payload_root / "checksums.sha256"
    if not checksums_path.is_file():
        raise UpdateError(f"checksums.sha256 is missing from payload root: {payload_root}")

    for raw_line in checksums_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            digest, relative_name = line.split(None, 1)
        except ValueError as exc:
            raise UpdateError(f"invalid checksum entry: {line}") from exc
        relative_name = relative_name.lstrip("* ")
        relative = _normalize_member_path(relative_name)
        target = payload_root.joinpath(*relative.parts)
        try:
            target.relative_to(payload_root)
        except ValueError as exc:
            raise UpdateError(f"checksum entry escapes payload root: {relative_name}") from exc
        if not target.is_file():
            raise UpdateError(f"checksum target is missing: {relative_name}")
        actual = _sha256_file(target)
        if actual.lower() != digest.lower():
            raise UpdateError(f"checksum mismatch: {relative_name}")


def _inspect_extracted_update_package(
    package_path: Path,
    destination: Path,
    *,
    install_root: Path,
    config_root: Path,
) -> tuple[BundlePackage, Path]:
    payload_root, package_root = _find_extracted_payload_root(destination)
    try:
        version_payload = _read_json_file(payload_root / "version.json")
        manifest_payload = _read_json_file(payload_root / "manifest.json")
    except (json.JSONDecodeError, OSError) as exc:
        raise UpdateError(f"failed to read update package metadata: {exc}") from exc

    package = _build_bundle_from_metadata(
        package_path=package_path,
        package_root=package_root,
        version_payload=version_payload,
        manifest_payload=manifest_payload,
        install_root=install_root,
        config_root=config_root,
    )
    for component in package.components:
        source = payload_root / component.archive
        if not source.is_dir():
            raise UpdateError(
                "component payload not found in extracted update package: "
                f"{component.archive} (component={component.name})"
            )
    return package, payload_root


def _extract_verified_payload(package: BundlePackage, destination: Path) -> Path:
    """Extract an update archive with path filtering and checksum validation."""

    _extract_update_archive(package.package_path, destination)
    payload_root = destination / package.package_root if package.package_root != "." else destination
    _verify_extracted_checksums(payload_root)
    return payload_root


def _artifact_component_excludes(component_name: str) -> set[str]:
    if component_name == "backend":
        return {
            "data",
            "backups",
            "temp_update",
            ".env",
            ".env.local",
        }
    return set()


def _copy_artifact_tree(source: Path, destination: Path, *, component_name: str) -> None:
    if not source.is_dir():
        raise UpdateError(f"artifact component directory not found: {source}")
    excluded = _artifact_component_excludes(component_name)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in excluded}

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)


def _artifact_preserve_paths(component_name: str) -> tuple[str, ...]:
    if component_name == "backend":
        return (
            "data",
            "backups",
            "config/backend.env",
            "config/backend.env.local",
        )
    return ()


def _move_preserved_paths(source: Path, destination: Path, component_name: str) -> None:
    for relative_name in _artifact_preserve_paths(component_name):
        source_path = source / relative_name
        if not source_path.exists():
            continue
        destination_path = destination / relative_name
        if destination_path.exists():
            _remove_path(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))


def _replace_artifact_component(
    source: Path,
    target: Path,
    *,
    component_name: str,
    preserve_current: bool = True,
) -> None:
    if not source.is_dir():
        raise UpdateError(f"artifact component payload not found: {source}")
    temporary = target.parent / f".{target.name}.update-{uuid.uuid4().hex}"
    old_target = target.parent / f".{target.name}.old-{uuid.uuid4().hex}"
    try:
        _copy_artifact_tree(source, temporary, component_name=component_name)
        if preserve_current and target.exists():
            _move_preserved_paths(target, temporary, component_name)
        if target.exists():
            target.rename(old_target)
        temporary.rename(target)
        if old_target.exists():
            _remove_path(old_target)
    except Exception:
        if target.exists() and not old_target.exists():
            # A failed copy may have created a partial target.  Remove it before
            # putting the original tree back in place.
            _remove_path(target)
        if old_target.exists() and not target.exists():
            old_target.rename(target)
        if temporary.exists():
            _remove_path(temporary)
        raise


def _backup_artifact_component(
    target: Path,
    backup: Path,
    *,
    component_name: str,
) -> bool:
    if not target.exists():
        return False
    if not target.is_dir():
        raise UpdateError(f"installed artifact component is not a directory: {target}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    _copy_artifact_tree(target, backup, component_name=component_name)
    return True


def _run_artifact_script(script: Path, *, install_root: Path, timeout: int) -> None:
    if not script.is_file():
        raise UpdateError(f"packaged runtime script not found: {script}")
    env = os.environ.copy()
    env.setdefault("FISH_INSTALL_ROOT", str(install_root / "backend"))
    env.setdefault("FISH_PROJECT_ROOT", str(install_root / "backend"))
    env.setdefault("FISH_DATA_ROOT", str(install_root / "backend" / "data"))
    env.setdefault("FISH_CONFIG_ROOT", str(install_root / "backend" / "config"))
    env.setdefault("FISH_ALGO_ROOT", str(install_root / "backend" / "algorithms"))
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=str(install_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "runtime script failed").strip()
        raise UpdateError(message)


def _stop_artifact_runtime(install_root: Path) -> None:
    root_stop = install_root / "stop.sh"
    if root_stop.is_file():
        # Stop the Electron AppImage as well as backend processes before
        # replacing the frontend artifact.  Linux permits unlinking an open
        # executable, which would otherwise leave the old UI running.
        _run_artifact_script(root_stop, install_root=install_root, timeout=90)
        return
    backend_stop = install_root / "backend" / "stop_backend.sh"
    if backend_stop.is_file():
        _run_artifact_script(backend_stop, install_root=install_root, timeout=90)


def _start_artifact_runtime(install_root: Path) -> None:
    backend_start = install_root / "backend" / "start_backend.sh"
    if backend_start.is_file():
        _run_artifact_script(backend_start, install_root=install_root, timeout=180)
    else:
        root_start = install_root / "start.sh"
        if root_start.is_file():
            # A release root's start.sh also launches the GUI and can block on
            # the AppImage.  Only use it as a last-resort legacy fallback.
            _run_artifact_script(root_start, install_root=install_root, timeout=180)

    # start_backend.sh deliberately does not launch Electron.  Start the
    # freshly installed AppImage independently so the update runner can exit
    # and the Upgrade Manager can report success to the UI.
    frontend_root = install_root / "frontend"
    appimages = sorted(frontend_root.glob("*.AppImage"))
    if len(appimages) == 1 and os.access(appimages[0], os.X_OK):
        env = os.environ.copy()
        env.setdefault("FISH_BACKEND_ORIGIN", "http://127.0.0.1:8000")
        subprocess.Popen(
            [str(appimages[0])],
            cwd=str(frontend_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _wait_for_http_health(url: str, *, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and (payload.get("status") == "ok" or payload.get("ok") is True):
                return payload
            last_error = f"unexpected health payload: {payload!r}"
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise UpdateError(f"packaged runtime health check failed: {last_error or url}")


def _artifact_backup_manifest(
    backup_dir: Path,
    *,
    package_path: Path,
    current_version: str,
    new_version: str,
    components: list[dict[str, Any]],
    root_files: list[dict[str, Any]],
    status: str,
) -> None:
    payload = {
        "format_version": 1,
        "package_mode": "runtime_artifact",
        "status": status,
        "package_path": str(package_path),
        "current_version": current_version,
        "new_version": new_version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "components": components,
        "root_files": root_files,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _restore_artifact_backup_component(
    backup_source: Path,
    target: Path,
    *,
    component_name: str,
) -> None:
    if not backup_source.exists():
        if target.exists():
            _remove_path(target)
        return
    _replace_artifact_component(
        backup_source,
        target,
        component_name=component_name,
        preserve_current=True,
    )


def _rollback_artifact_backup(
    backup_dir: Path,
    *,
    install_root: Path,
) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise UpdateError(f"artifact backup manifest not found: {manifest_path}")
    manifest = _read_json_file(manifest_path)
    _stop_artifact_runtime(install_root)
    for item in manifest.get("components", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        target_subdir = str(item.get("target_subdir") or name).strip()
        backup_rel = str(item.get("backup_rel") or f"components/{name}").strip()
        target = install_root / target_subdir
        source = backup_dir / backup_rel
        if bool(item.get("existed_before")):
            _restore_artifact_backup_component(source, target, component_name=name)
        elif target.exists():
            _remove_path(target)

    for item in manifest.get("root_files", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        target = install_root / name
        if bool(item.get("existed_before")):
            source = backup_dir / str(item.get("backup_rel") or f"root/{name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.exists():
            _remove_path(target)

    _start_artifact_runtime(install_root)
    return {
        "current_version": _read_version_file(install_root / "version.json"),
        "restored_version": str(manifest.get("current_version") or ""),
        "backup_dir": str(backup_dir),
        "rolled_back_targets": [
            str(item.get("name"))
            for item in manifest.get("components", [])
            if isinstance(item, dict) and item.get("name")
        ],
    }


def _run_artifact_update(
    package_path: Path,
    *,
    install_root: Path,
    config_root: Path,
    data_root: Path | None = None,
) -> dict[str, Any]:
    if not _is_artifact_install(install_root):
        raise UpdateError(
            "runtime artifact update requires a packaged install root containing backend/fish-quality-backend"
        )

    runtime_data_root = (data_root or (install_root / "backend" / "data")).resolve()
    lock_dir = runtime_data_root / "state" / "software_update" / "update.lock.d"
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        raise UpdateError(f"another update is already running: {lock_dir}") from exc

    # Keep the extracted payload outside ``backend/``.  Replacing that
    # component moves its persistent ``data/`` directory, so an extraction
    # nested below it would otherwise disappear halfway through the update.
    extraction_root = Path(tempfile.mkdtemp(prefix="fish-artifact-update-"))
    backup_dir: Path | None = None
    component_records: list[dict[str, Any]] = []
    root_records: list[dict[str, Any]] = []
    try:
        _extract_update_archive(package_path, extraction_root)
        package, payload_root = _inspect_extracted_update_package(
            package_path,
            extraction_root,
            install_root=install_root,
            config_root=config_root,
        )
        if not _is_artifact_package(package):
            raise UpdateError(
                "source update packages cannot be applied to a packaged install; rebuild with --packaged"
            )
        _verify_extracted_checksums(payload_root)

        unpacked_bytes = int(package.manifest_payload.get("unpacked_bytes", 0) or 0)
        current_bytes = _tree_bytes(install_root / "backend") + _tree_bytes(install_root / "frontend")
        # Extraction has already consumed its space.  The install filesystem
        # still needs room for a backup and a temporary copy of the payload.
        required_bytes = unpacked_bytes + current_bytes + (512 * 1024 * 1024)
        if shutil.disk_usage(install_root).free < required_bytes:
            raise UpdateError(
                "insufficient disk space for packaged update: "
                f"available={shutil.disk_usage(install_root).free} required={required_bytes}"
            )
        payload_version = package.new_version
        current_version = _read_version_file(install_root / "version.json")
        if not current_version:
            current_version = DEFAULT_VERSION

        current_config_version = _read_current_config_version(config_root)
        if current_config_version < package.min_config_version:
            raise UpdateError(
                f"config version too old: current={current_config_version} required={package.min_config_version}"
            )

        # The running packaged backend historically stored backups below its
        # own install directory.  Keep that location so the existing Upgrade
        # Manager API continues to list and resolve them.
        backup_root = install_root / "backend" / "backups"
        if not backup_root.parent.is_dir():
            backup_root = install_root / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir = backup_root / f"update_backup_{time.strftime('%Y%m%d_%H%M%S')}_{current_version}_to_{payload_version}"
        backup_dir.mkdir(parents=True, exist_ok=False)

        for component in package.components:
            target = install_root / component.target_subdir
            backup_rel = f"components/{component.name}"
            existed_before = _backup_artifact_component(
                target,
                backup_dir / backup_rel,
                component_name=component.name,
            )
            component_records.append(
                {
                    "name": component.name,
                    "target_subdir": component.target_subdir,
                    "backup_rel": backup_rel,
                    "existed_before": existed_before,
                }
            )

        for name in ("start.sh", "stop.sh", "README.md", "version.json"):
            target = install_root / name
            existed_before = target.is_file()
            backup_rel = f"root/{name}"
            if existed_before:
                backup_target = backup_dir / backup_rel
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
            root_records.append(
                {"name": name, "backup_rel": backup_rel, "existed_before": existed_before}
            )

        _artifact_backup_manifest(
            backup_dir,
            package_path=package.package_path,
            current_version=current_version,
            new_version=payload_version,
            components=component_records,
            root_files=root_records,
            status="prepared",
        )

        _stop_artifact_runtime(install_root)
        for component in package.components:
            source = payload_root / component.archive
            _replace_artifact_component(
                source,
                install_root / component.target_subdir,
                component_name=component.name,
                preserve_current=True,
            )

        for name in ("start.sh", "stop.sh", "README.md", "version.json"):
            source = payload_root / name
            if source.is_file():
                shutil.copy2(source, install_root / name)

        _start_artifact_runtime(install_root)
        health = _wait_for_http_health(package.healthcheck, timeout=90)
        _artifact_backup_manifest(
            backup_dir,
            package_path=package.package_path,
            current_version=current_version,
            new_version=payload_version,
            components=component_records,
            root_files=root_records,
            status="completed",
        )
        return {
            "ok": True,
            "current_version": current_version,
            "new_version": payload_version,
            "backup_dir": str(backup_dir),
            "updated_targets": [component.name for component in package.components],
            "components": [
                {
                    "name": component.name,
                    "archive": component.archive,
                    "target_subdir": component.target_subdir,
                    "current_link": component.current_link,
                }
                for component in package.components
            ],
            "health": health,
        }
    except Exception as exc:
        if backup_dir is not None and (backup_dir / "manifest.json").is_file():
            try:
                rollback_result = _rollback_artifact_backup(backup_dir, install_root=install_root)
                raise UpdateError(
                    str(exc),
                    payload={
                        "status": "rolled_back",
                        "backup_dir": str(backup_dir),
                        **rollback_result,
                    },
                ) from exc
            except UpdateError:
                raise
            except Exception as rollback_exc:
                raise UpdateError(
                    f"{exc}; automatic rollback failed: {rollback_exc}",
                    payload={"backup_dir": str(backup_dir)},
                ) from exc
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(str(exc)) from exc
    finally:
        shutil.rmtree(extraction_root, ignore_errors=True)
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def _run_shell_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        check=False,
    )


def _load_result_payload(result_file: Path) -> dict[str, Any]:
    if not result_file.exists():
        return {}
    return _read_json_file(result_file)


def _run_update_script(
    subcommand: str,
    *,
    install_root: Path,
    config_root: Path | None,
    data_root: Path | None,
    package_path: Path | None = None,
    backup_dir: Path | None = None,
    force: bool = False,
    skip_migrations: bool = False,
    skip_frontend_install: bool = False,
) -> dict[str, Any]:
    # A frozen runtime keeps update.sh in PyInstaller's read-only resources.
    # Source installations retain the historical repository location.
    update_script = resource_path("backend", "app", "services", "update", "update.sh")
    if not update_script.exists():
        update_script = install_root / "backend" / "app" / "services" / "update" / "update.sh"
    if not update_script.exists():
        raise UpdateError(f"update shell script not found: {update_script}")

    with tempfile.TemporaryDirectory(prefix="fish-update-result-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        result_file = tmp_dir / f"{subcommand}_result.json"
        command = [
            "bash",
            str(update_script),
            subcommand,
            "--install-root",
            str(install_root),
            "--result-file",
            str(result_file),
        ]
        if config_root is not None:
            command.extend(["--config-root", str(config_root)])
        if data_root is not None:
            command.extend(["--data-root", str(data_root)])
        if package_path is not None:
            command.extend(["--package", str(package_path)])
        if backup_dir is not None:
            command.extend(["--backup-dir", str(backup_dir)])
        if force:
            command.append("--force")
        if skip_migrations:
            command.append("--skip-migrations")
        if skip_frontend_install:
            command.append("--skip-frontend-install")

        env = os.environ.copy()
        env.setdefault("FISH_UPDATE_PYTHON_BIN", sys.executable)

        completed = _run_shell_command(command, cwd=install_root, env=env)
        payload = _load_result_payload(result_file)
        if completed.returncode != 0:
            message = str(payload.get("error") or payload.get("message") or "update.sh failed")
            raise UpdateError(message, payload=payload)
        return payload


def run_update(
    package_path: Path,
    *,
    install_root: Path = PROJECT_ROOT,
    config_root: Path | None = None,
    data_root: Path | None = None,
    project_root: Path | None = None,
    force: bool = False,
    skip_migrations: bool = False,
    skip_frontend_install: bool = False,
) -> dict[str, object]:
    install_root = _normalize_install_root((project_root or install_root).resolve())
    artifact_install = _is_artifact_install(install_root)
    default_config_root = install_root / "backend" / "config" if artifact_install else install_root / ".config"
    default_data_root = install_root / "backend" / "data" if artifact_install else install_root / "data"
    config_root = (config_root or default_config_root).resolve()
    data_root = (data_root or default_data_root).resolve()
    package_path = _validate_update_package_file(package_path.resolve())

    if artifact_install:
        result = _run_artifact_update(
            package_path,
            install_root=install_root,
            config_root=config_root,
            data_root=data_root,
        )
        return {
            "current_version": result.get("current_version"),
            "new_version": result.get("new_version"),
            "backup_dir": result.get("backup_dir"),
            "updated_targets": result.get("updated_targets") or [],
            "includes_algo_env": False,
            "components": result.get("components") or [],
            "health": result.get("health"),
        }

    bundle = inspect_update_package(
        package_path,
        install_root=install_root,
        config_root=config_root,
    )
    precheck = _bundle_precheck_result(bundle)
    # Packaged installations have no target-side Python/npm environment and
    # their updater shell script lives inside PyInstaller's read-only bundle.
    # Apply the release artifact directly from Python.  Source installations
    # continue through the legacy shell implementation below.
    artifact_mode = str(
        bundle.version_payload.get("package_mode")
        or bundle.manifest_payload.get("package_mode")
        or ""
    ).strip().lower()
    if artifact_mode in {"runtime_artifact", "packaged", "artifact"} or str(
        bundle.version_payload.get("package_format") or ""
    ).startswith("release_artifact_"):
        result = _run_artifact_update(
            package_path,
            install_root=install_root,
            config_root=config_root,
            data_root=data_root,
        )
        return {
            "current_version": result.get("current_version") or precheck["current_version"],
            "new_version": result.get("new_version") or precheck["new_version"],
            "backup_dir": result.get("backup_dir"),
            "updated_targets": result.get("updated_targets") or precheck["updated_targets"],
            "includes_algo_env": False,
            "components": precheck["components"],
            "health": result.get("health"),
        }
    if _is_artifact_install(install_root):
        raise UpdateError(
            "source update packages cannot be applied to a packaged install; rebuild with --packaged"
        )
    payload = _run_update_script(
        "apply",
        install_root=install_root,
        config_root=config_root,
        data_root=data_root,
        package_path=package_path,
        force=force,
        skip_migrations=skip_migrations,
        skip_frontend_install=skip_frontend_install,
    )
    return {
        "current_version": precheck["current_version"],
        "new_version": precheck["new_version"],
        "backup_dir": payload.get("backup_dir"),
        "updated_targets": payload.get("updated_targets") or precheck["updated_targets"],
        "includes_algo_env": False,
        "components": precheck["components"],
    }


def rollback_backup(
    backup_dir: Path,
    *,
    install_root: Path = PROJECT_ROOT,
    project_root: Path | None = None,
) -> dict[str, object]:
    install_root = _normalize_install_root((project_root or install_root).resolve())
    backup_dir = backup_dir.resolve()

    manifest_path = backup_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            backup_manifest = _read_json_file(manifest_path)
        except Exception:
            backup_manifest = {}
        if str(backup_manifest.get("package_mode") or "").strip().lower() in {
            "runtime_artifact",
            "packaged",
            "artifact",
        }:
            result = _rollback_artifact_backup(backup_dir, install_root=install_root)
            return {
                "current_version": result.get("current_version"),
                "restored_version": result.get("restored_version"),
                "backup_dir": str(backup_dir),
                "rolled_back_targets": result.get("rolled_back_targets") or [],
            }

    payload = _run_update_script(
        "rollback",
        install_root=install_root,
        config_root=(install_root / ".config"),
        data_root=(install_root / "data"),
        backup_dir=backup_dir,
    )
    return {
        "current_version": payload.get("current_version"),
        "restored_version": payload.get("restored_version"),
        "backup_dir": str(backup_dir),
        "rolled_back_targets": payload.get("rolled_back_targets") or [],
    }


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] in {"apply", "precheck", "rollback"}:
        return argv
    if argv[0].startswith("-"):
        return argv
    return ["apply", *argv]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Precheck or apply a Linux tar.xz source or packaged-artifact update package."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="Apply a .tar.xz update package")
    apply_parser.add_argument("package", help="Path to the .tar.xz/.txz update package")
    apply_parser.add_argument("--install-root", default=str(PROJECT_ROOT))
    apply_parser.add_argument("--config-root", default=None)
    apply_parser.add_argument("--data-root", default=None)
    apply_parser.add_argument("--force", action="store_true")
    apply_parser.add_argument("--skip-migrations", action="store_true")
    apply_parser.add_argument("--skip-frontend-install", action="store_true")

    precheck_parser = subparsers.add_parser(
        "precheck", help="Inspect a .tar.xz update package without applying it"
    )
    precheck_parser.add_argument("package", help="Path to the .tar.xz/.txz update package")
    precheck_parser.add_argument("--install-root", default=str(PROJECT_ROOT))
    precheck_parser.add_argument("--config-root", default=None)

    rollback_parser = subparsers.add_parser("rollback", help="Rollback from a backup dir")
    rollback_parser.add_argument("backup_dir", help="Path to a backup directory")
    rollback_parser.add_argument("--install-root", default=str(PROJECT_ROOT))

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_cli_argv(list(argv or sys.argv[1:]))
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "apply":
            result = run_update(
                Path(args.package),
                install_root=Path(args.install_root),
                config_root=Path(args.config_root).expanduser() if args.config_root else None,
                data_root=Path(args.data_root).expanduser() if args.data_root else None,
                force=bool(args.force),
                skip_migrations=bool(args.skip_migrations),
                skip_frontend_install=bool(args.skip_frontend_install),
            )
        elif args.command == "precheck":
            result = precheck_update_package(
                Path(args.package),
                install_root=Path(args.install_root),
                config_root=Path(args.config_root).expanduser() if args.config_root else None,
            )
        elif args.command == "rollback":
            result = rollback_backup(
                Path(args.backup_dir),
                install_root=Path(args.install_root),
            )
        else:  # pragma: no cover
            raise UpdateError(f"unsupported command: {args.command}")
    except UpdateError as exc:
        print(f"[error] {exc}", file=sys.stderr, flush=True)
        if exc.payload:
            print(json.dumps(exc.payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"[error] unexpected failure: {exc}", file=sys.stderr, flush=True)
        return 1

    if args.command == "precheck":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
