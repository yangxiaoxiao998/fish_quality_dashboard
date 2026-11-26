#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from app.runtime_paths import INSTALL_ROOT as RUNTIME_INSTALL_ROOT
from app.runtime_paths import resource_path

PROJECT_ROOT = RUNTIME_INSTALL_ROOT
BUILD_SCRIPT = resource_path("backend", "app", "services", "update", "build_update_package.sh")
OUTPUT_ROOT = PROJECT_ROOT / "data" / "state" / "software_update" / "build"


def build_bundle(
    *,
    skip_frontend_build: bool,
    output_root: Path,
    packaged: bool = False,
    release_root: Path | None = None,
) -> Path:
    if not BUILD_SCRIPT.exists():
        raise RuntimeError(f"build script not found: {BUILD_SCRIPT}")

    bash_bin = shutil.which("bash")
    if not bash_bin:
        raise RuntimeError("bash is required to build the Linux tar.xz update package")

    command = [
        bash_bin,
        str(BUILD_SCRIPT),
        "--output-dir",
        str(output_root),
    ]
    if packaged:
        command.insert(2, "--packaged")
        if release_root is not None:
            command.extend(["--release-root", str(release_root)])
    if skip_frontend_build:
        command.append("--skip-frontend-build")

    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "build script failed"
        raise RuntimeError(message)

    final_line = ""
    for line in reversed((completed.stdout or "").splitlines()):
        if line.strip():
            final_line = line.strip()
            break
    if not final_line:
        raise RuntimeError("build script did not print the final package path")
    return Path(final_line).expanduser().resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a source or packaged-artifact Linux update package"
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Compatibility no-op in source update mode",
    )
    parser.add_argument(
        "--packaged",
        "--artifact",
        action="store_true",
        help="Build from a packaged Linux release (PyInstaller + AppImage)",
    )
    parser.add_argument(
        "--release-root",
        default=None,
        help="Packaged release directory (defaults to dist/fish-quality-linux-x86_64)",
    )
    parser.add_argument(
        "--output-root",
        default=str(OUTPUT_ROOT),
        help="Directory where update_package_v<version>.tar.xz will be written",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        final_package = build_bundle(
            skip_frontend_build=bool(args.skip_frontend_build),
            output_root=Path(args.output_root).expanduser(),
            packaged=bool(args.packaged),
            release_root=Path(args.release_root).expanduser() if args.release_root else None,
        )
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr, flush=True)
        return 1
    print(str(final_package), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
