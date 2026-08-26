#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[4]
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


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError(f"json payload must be an object: {path}")
    return payload


def _read_version_file(version_file: Path) -> str:
    if not version_file.exists():
        return DEFAULT_VERSION
    try:
        payload = _read_json_file(version_file)
    except Exception:
        return DEFAULT_VERSION
    return str(payload.get("version") or payload.get("current_version") or DEFAULT_VERSION)


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


def inspect_update_package(
    package_path: Path,
    *,
    install_root: Path,
    config_root: Path,
) -> BundlePackage:
    if not package_path.is_file():
        raise UpdateError(f"update package not found: {package_path}")
    if not _is_update_package(package_path):
        raise UpdateError(f"update package must be .tar.xz or .txz: {package_path}")

    header = package_path.read_bytes()[: len(XZ_MAGIC)]
    if header != XZ_MAGIC:
        raise UpdateError(f"update package is not a valid xz archive: {package_path}")

    try:
        archive = tarfile.open(package_path, mode="r:xz")
    except tarfile.TarError as exc:
        raise UpdateError(f"failed to read tar.xz update package: {exc}") from exc

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
            bundle_format_version = int(
                version_payload.get("bundle_format_version", 1) or 1
            )
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
            if name in seen_names:
                raise UpdateError(f"duplicate component in manifest.json: {name}")
            seen_names.add(name)
            if not _tar_contains_prefix(archive, _payload_member_name(package_root, archive_name)):
                raise UpdateError(
                    f"component payload not found in update package: {archive_name} (component={name})"
                )
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


def precheck_update_package(
    package_path: Path,
    *,
    install_root: Path = PROJECT_ROOT,
    config_root: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    install_root = (project_root or install_root).resolve()
    config_root = (config_root or (install_root / ".config")).resolve()
    bundle = inspect_update_package(
        package_path.resolve(),
        install_root=install_root,
        config_root=config_root,
    )
    manifest = bundle.manifest_payload
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
        "requires_restart": ["backend"],
        "healthcheck": bundle.healthcheck,
        "package_format": str(
            bundle.version_payload.get("package_format") or "source_tar_xz_v1"
        ),
        "build_time": bundle.version_payload.get("build_time"),
        "git_commit": bundle.version_payload.get("git_commit"),
        "frontend_runtime_mode": manifest.get("frontend_runtime_mode"),
        "preserve_paths": manifest.get("preserve_paths") or [],
        "excluded_paths": manifest.get("excluded_paths") or [],
    }


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
    install_root = (project_root or install_root).resolve()
    config_root = (config_root or (install_root / ".config")).resolve()
    data_root = (data_root or (install_root / "data")).resolve()
    package_path = package_path.resolve()

    precheck = precheck_update_package(
        package_path,
        install_root=install_root,
        config_root=config_root,
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
    install_root = (project_root or install_root).resolve()
    backup_dir = backup_dir.resolve()

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
        description="Precheck or apply a Linux tar.xz source update package."
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
