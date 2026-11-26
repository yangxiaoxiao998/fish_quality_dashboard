#!/usr/bin/env bash
set -Eeuo pipefail

# Build an update package from the Linux release artifacts produced by
# scripts/build_linux_release.sh.  The resulting archive contains the
# PyInstaller onedir backend and Electron AppImage, never the source tree.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../../../.." && pwd -P)"

# shellcheck source=update/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

RELEASE_ROOT="${FISH_RELEASE_ROOT:-$PROJECT_ROOT/dist/fish-quality-linux-x86_64}"
OUTPUT_DIR="$PROJECT_ROOT/update/dist"
GIT_COMMIT="unknown"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-root)
      RELEASE_ROOT="${2:?missing value for --release-root}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:?missing value for --output-dir}"
      shift 2
      ;;
    *)
      fish_die "unknown argument: $1"
      ;;
  esac
done

PYTHON_BIN="$(fish_python_bin)"
fish_require_command bash rsync tar xz sha256sum find mktemp du

RELEASE_ROOT="$(fish_realpath "$RELEASE_ROOT")"
OUTPUT_DIR="$(fish_realpath "$OUTPUT_DIR")"
BACKEND_SOURCE="$RELEASE_ROOT/backend"
FRONTEND_SOURCE="$RELEASE_ROOT/frontend"

[[ -d "$BACKEND_SOURCE" && -x "$BACKEND_SOURCE/fish-quality-backend" ]] || {
  fish_die "packaged backend artifact not found: $BACKEND_SOURCE"
}
[[ -d "$FRONTEND_SOURCE" ]] || fish_die "packaged frontend artifact not found: $FRONTEND_SOURCE"

shopt -s nullglob
appimages=("$FRONTEND_SOURCE"/*.AppImage)
shopt -u nullglob
(( ${#appimages[@]} == 1 )) || {
  fish_die "frontend artifact directory must contain exactly one AppImage: $FRONTEND_SOURCE"
}

VERSION_FILE="$RELEASE_ROOT/version.json"
if [[ ! -f "$VERSION_FILE" ]]; then
  VERSION_FILE="$BACKEND_SOURCE/version.json"
fi
[[ -f "$VERSION_FILE" ]] || fish_die "version.json not found in release root or backend"

VERSION="$($PYTHON_BIN - "$VERSION_FILE" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
version = str(payload.get("version") or payload.get("current_version") or "").strip()
if not version:
    raise SystemExit("version.json is missing version/current_version")
print(version)
PY
)"

BUILD_TIME="$(fish_now_iso)"
if command -v git >/dev/null 2>&1 && git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GIT_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
fi

PACKAGE_NAME="update_artifacts_v${VERSION}.tar.xz"
mkdir -p "$OUTPUT_DIR"

STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fish-artifact-update-build.XXXXXX")"
PACKAGE_ROOT="$STAGE_ROOT/update_package"
trap 'rm -rf "$STAGE_ROOT"' EXIT
mkdir -p "$PACKAGE_ROOT/backend" "$PACKAGE_ROOT/frontend"

# Runtime state must never be carried between machines/versions.  It remains
# on the installed target and is explicitly preserved by updater.py.
rsync -a --delete-excluded \
  --exclude='/data/***' \
  --exclude='/backups/***' \
  --exclude='/config/backend.env' \
  --exclude='/config/backend.env.local' \
  --exclude='/.env' \
  --exclude='/.env.local' \
  "$BACKEND_SOURCE/" "$PACKAGE_ROOT/backend/"
rsync -a --delete "$FRONTEND_SOURCE/" "$PACKAGE_ROOT/frontend/"

for root_file in start.sh stop.sh README.md; do
  [[ -f "$RELEASE_ROOT/$root_file" ]] || fish_die "release root file not found: $RELEASE_ROOT/$root_file"
  cp -a "$RELEASE_ROOT/$root_file" "$PACKAGE_ROOT/$root_file"
done

# Keep a top-level version file in the release and update package.  Older
# releases only had backend/version.json, therefore copy both locations.
cp -a "$VERSION_FILE" "$PACKAGE_ROOT/version.json"
cp -a "$VERSION_FILE" "$PACKAGE_ROOT/backend/version.json"

UNPACKED_BYTES="$(du -sb "$PACKAGE_ROOT" | awk '{print $1}')"
export PACKAGE_ROOT VERSION BUILD_TIME GIT_COMMIT UNPACKED_BYTES
"$PYTHON_BIN" - <<'PY'
import json
import os
import pathlib

root = pathlib.Path(os.environ["PACKAGE_ROOT"])
version = os.environ["VERSION"]
build_time = os.environ["BUILD_TIME"]
git_commit = os.environ["GIT_COMMIT"]
unpacked_bytes = int(os.environ["UNPACKED_BYTES"])

for relative in ("version.json", "backend/version.json"):
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(
        {
            "version": version,
            "current_version": version,
            "build_time": build_time,
            "git_commit": git_commit,
            "min_config_version": int(payload.get("min_config_version", 1) or 1),
            "bundle_format_version": int(payload.get("bundle_format_version", 1) or 1),
            "package_format": "release_artifact_tar_xz_v1",
            "package_mode": "runtime_artifact",
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

manifest = {
    "format_version": 1,
    "package_mode": "runtime_artifact",
    "package_format": "release_artifact_tar_xz_v1",
    "components": [
        {
            "name": "backend",
            "archive": "backend",
            "target_subdir": "backend",
            "current_link": "backend",
        },
        {
            "name": "frontend",
            "archive": "frontend",
            "target_subdir": "frontend",
            "current_link": "frontend",
        },
    ],
    "preserve_paths": [
        "backend/data/",
        "backend/backups/",
        "backend/config/backend.env",
        "backend/config/backend.env.local",
        "data/",
        "backups/",
    ],
    "excluded_paths": [
        "backend/data/",
        "backend/backups/",
        "backend/config/backend.env",
        "backend/config/backend.env.local",
    ],
    "unpacked_bytes": unpacked_bytes,
    "healthcheck": {
        "backend": "http://127.0.0.1:8000/api/health",
        "database": "http://127.0.0.1:8000/api/health/database",
    },
    "required_commands": ["bash", "tar", "xz", "sha256sum", "curl"],
    "frontend_runtime_mode": "appimage",
    "backend_runtime_mode": "pyinstaller_onedir",
}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY

(
  cd "$PACKAGE_ROOT"
  find . -type f ! -name 'checksums.sha256' -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > checksums.sha256
)

FINAL_PACKAGE="$OUTPUT_DIR/$PACKAGE_NAME"
tar -C "$STAGE_ROOT" -cJf "$FINAL_PACKAGE" update_package
(
  cd "$OUTPUT_DIR"
  sha256sum "$PACKAGE_NAME" > "${PACKAGE_NAME}.sha256"
)

printf '%s\n' "$FINAL_PACKAGE"
