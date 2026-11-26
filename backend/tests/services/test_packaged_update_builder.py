from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from app.services.update import updater


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "backend" / "app" / "services" / "update" / "build_update_package.sh"


def _write_executable(path: Path, content: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required by the package builder")
def test_packaged_builder_creates_runtime_artifact(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    backend = release_root / "backend"
    frontend = release_root / "frontend"
    (backend / "data").mkdir(parents=True)
    (backend / "backups").mkdir()
    (backend / "config").mkdir()
    frontend.mkdir(parents=True)

    _write_executable(backend / "fish-quality-backend")
    _write_executable(frontend / "fish-quality-desktop-1.0.0-x86_64.AppImage")
    (backend / "data" / "runtime-state.txt").write_text("must not be packaged\n", encoding="utf-8")
    (backend / "backups" / "previous.txt").write_text("must not be packaged\n", encoding="utf-8")
    (backend / "config" / "backend.env").write_text("SECRET=preserve-me\n", encoding="utf-8")
    (release_root / "version.json").write_text('{"version": "1.0.0"}\n', encoding="utf-8")
    for name in ("start.sh", "stop.sh"):
        _write_executable(release_root / name)
    (release_root / "README.md").write_text("release\n", encoding="utf-8")

    output_dir = tmp_path / "output"
    env = os.environ.copy()
    env["FISH_UPDATE_PYTHON_BIN"] = sys.executable
    completed = subprocess.run(
        [
            "bash",
            str(BUILD_SCRIPT),
            "--packaged",
            "--release-root",
            str(release_root),
            "--output-dir",
            str(output_dir),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    package_path = output_dir / "update_artifacts_v1.0.0.tar.xz"
    assert package_path.is_file()
    assert Path(f"{package_path}.sha256").is_file()

    with tarfile.open(package_path, mode="r:xz") as archive:
        names = set(archive.getnames())
        manifest_member = archive.extractfile("update_package/manifest.json")
        assert manifest_member is not None
        manifest = json.loads(manifest_member.read())

    assert "update_package/backend/fish-quality-backend" in names
    assert "update_package/frontend/fish-quality-desktop-1.0.0-x86_64.AppImage" in names
    assert "update_package/backend/data/runtime-state.txt" not in names
    assert "update_package/backend/backups/previous.txt" not in names
    assert "update_package/backend/config/backend.env" not in names
    assert manifest["package_mode"] == "runtime_artifact"
    assert manifest["backend_runtime_mode"] == "pyinstaller_onedir"

    bundle = updater.inspect_update_package(
        package_path,
        install_root=tmp_path / "source-install",
        config_root=tmp_path / "config",
    )
    assert bundle.version_payload["package_mode"] == "runtime_artifact"
    assert [component.name for component in bundle.components] == ["backend", "frontend"]
