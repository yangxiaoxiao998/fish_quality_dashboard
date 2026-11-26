from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from app.services.update import updater


def _add_file(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(payload))


def _bundle(package_path: Path) -> updater.BundlePackage:
    return updater.BundlePackage(
        package_path=package_path,
        package_root="update_package",
        version_payload={},
        manifest_payload={},
        components=(),
        current_version="1.0.0",
        new_version="1.0.0",
        current_config_version=1,
        min_config_version=1,
        bundle_format_version=1,
        healthcheck="http://127.0.0.1:8000/api/health",
    )


def _artifact_install(tmp_path: Path) -> Path:
    install_root = tmp_path / "install"
    (install_root / "backend").mkdir(parents=True)
    (install_root / "frontend").mkdir()
    (install_root / "backend" / "fish-quality-backend").write_bytes(b"runtime")
    (install_root / "backend" / "start_backend.sh").write_text("#!/bin/sh\n")
    (install_root / "version.json").write_text('{"version": "1.0.0"}\n')
    return install_root


def _write_extracted_metadata(payload_root: Path) -> None:
    payload_root.mkdir(parents=True)
    (payload_root / "backend").mkdir()
    (payload_root / "frontend").mkdir()
    (payload_root / "version.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "package_mode": "runtime_artifact",
                "package_format": "release_artifact_tar_xz_v1",
            }
        )
    )
    (payload_root / "manifest.json").write_text(
        json.dumps(
            {
                "package_mode": "runtime_artifact",
                "components": [
                    {"name": "backend", "archive": "backend"},
                    {"name": "frontend", "archive": "frontend"},
                ],
            }
        )
    )
    (payload_root / "checksums.sha256").write_text("")


def test_artifact_precheck_only_validates_container(tmp_path: Path, monkeypatch) -> None:
    package_path = tmp_path / "developer_update_v1.0.0.tar.xz"
    with tarfile.open(package_path, "w:xz") as archive:
        _add_file(archive, "not-update-metadata.txt", b"accepted by basic precheck")

    def fail_if_full_inspection_runs(*args, **kwargs):
        raise AssertionError("basic precheck must not inspect archive members")

    monkeypatch.setattr(updater, "inspect_update_package", fail_if_full_inspection_runs)
    install_root = _artifact_install(tmp_path)

    result = updater.precheck_update_package(package_path, install_root=install_root)

    assert result["precheck_mode"] == "basic"
    assert result["metadata_verified"] is False
    assert result["current_version"] == "1.0.0"
    assert result["new_version"] == "1.0.0"
    assert result["updated_targets"] == ["backend", "frontend"]


def test_extracted_package_metadata_and_components_are_checked(tmp_path: Path) -> None:
    install_root = _artifact_install(tmp_path)
    extraction_root = tmp_path / "extracted"
    payload_root = extraction_root / "update_package"
    _write_extracted_metadata(payload_root)

    package, detected_root = updater._inspect_extracted_update_package(
        tmp_path / "developer_update_v1.0.0.tar.xz",
        extraction_root,
        install_root=install_root,
        config_root=install_root / "backend" / "config",
    )

    assert detected_root == payload_root
    assert package.new_version == "1.0.0"
    assert [component.name for component in package.components] == ["backend", "frontend"]


def test_missing_component_is_rejected_after_extraction(tmp_path: Path) -> None:
    install_root = _artifact_install(tmp_path)
    extraction_root = tmp_path / "extracted"
    payload_root = extraction_root / "update_package"
    _write_extracted_metadata(payload_root)
    (payload_root / "frontend").rmdir()

    with pytest.raises(updater.UpdateError, match="component payload not found"):
        updater._inspect_extracted_update_package(
            tmp_path / "developer_update_v1.0.0.tar.xz",
            extraction_root,
            install_root=install_root,
            config_root=install_root / "backend" / "config",
        )


def test_invalid_payload_fails_before_runtime_is_stopped(tmp_path: Path, monkeypatch) -> None:
    package_path = tmp_path / "developer_update_v1.0.0.tar.xz"
    with tarfile.open(package_path, "w:xz") as archive:
        _add_file(archive, "update_package/readme.txt", b"metadata is missing")
    install_root = _artifact_install(tmp_path)

    def fail_if_runtime_stop_runs(*args, **kwargs):
        raise AssertionError("runtime must not stop before extracted metadata is validated")

    monkeypatch.setattr(updater, "_stop_artifact_runtime", fail_if_runtime_stop_runs)

    with pytest.raises(updater.UpdateError, match="manifest.json/version.json not found"):
        updater.run_update(package_path, install_root=install_root)


def test_extract_verified_payload_preserves_internal_symlink(tmp_path: Path) -> None:
    package_path = tmp_path / "safe.tar.xz"
    payload = b"packaged runtime library"
    checksum = hashlib.sha256(payload).hexdigest()
    with tarfile.open(package_path, "w:xz") as archive:
        _add_file(archive, "update_package/library.so.1", payload)
        link = tarfile.TarInfo("update_package/library.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "library.so.1"
        archive.addfile(link)
        _add_file(
            archive,
            "update_package/checksums.sha256",
            f"{checksum}  library.so.1\n".encode(),
        )

    payload_root = updater._extract_verified_payload(
        _bundle(package_path), tmp_path / "extract"
    )

    extracted_link = payload_root / "library.so"
    assert extracted_link.is_symlink()
    assert extracted_link.read_bytes() == payload


def test_extract_verified_payload_rejects_escaping_symlink(tmp_path: Path) -> None:
    package_path = tmp_path / "unsafe.tar.xz"
    with tarfile.open(package_path, "w:xz") as archive:
        link = tarfile.TarInfo("update_package/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)
        _add_file(archive, "update_package/checksums.sha256", b"")

    with pytest.raises(updater.UpdateError, match="unsafe archive member"):
        updater._extract_verified_payload(_bundle(package_path), tmp_path / "extract")
