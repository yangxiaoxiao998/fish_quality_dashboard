#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd -P)"

# shellcheck source=update/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$PROJECT_ROOT/update/dist"
SKIP_FRONTEND_BUILD=0
INCLUDE_UPDATE_MANAGER="${FISH_INCLUDE_UPDATE_MANAGER_IN_UPDATE:-0}"
INCLUDE_ALGORITHMS="${FISH_INCLUDE_ALGORITHMS_IN_UPDATE:-1}"
INCLUDE_MEDIAMTX="${FISH_INCLUDE_MEDIAMTX_IN_UPDATE:-0}"
INCLUDE_ALGORITHM_MODEL_ASSETS="${FISH_INCLUDE_ALGORITHM_MODEL_ASSETS_IN_UPDATE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="${2:?missing value for --output-dir}"
      shift 2
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD=1
      shift
      ;;
    --include-update-manager)
      INCLUDE_UPDATE_MANAGER=1
      shift
      ;;
    --exclude-update-manager|--skip-update-manager)
      INCLUDE_UPDATE_MANAGER=0
      shift
      ;;
    --include-algorithms)
      INCLUDE_ALGORITHMS=1
      shift
      ;;
    --exclude-algorithms|--skip-algorithms)
      INCLUDE_ALGORITHMS=0
      shift
      ;;
    --include-algorithm-model-assets)
      INCLUDE_ALGORITHM_MODEL_ASSETS=1
      shift
      ;;
    --exclude-algorithm-model-assets|--skip-algorithm-model-assets)
      INCLUDE_ALGORITHM_MODEL_ASSETS=0
      shift
      ;;
    --include-mediamtx)
      INCLUDE_MEDIAMTX=1
      shift
      ;;
    --exclude-mediamtx|--skip-mediamtx)
      INCLUDE_MEDIAMTX=0
      shift
      ;;
    *)
      fish_die "unknown argument: $1"
      ;;
  esac
done

PYTHON_BIN="$(fish_python_bin)"
fish_require_command bash rsync tar xz sha256sum find mktemp du

VERSION="$("$PYTHON_BIN" - "$PROJECT_ROOT/version.json" <<'PY'
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
GIT_COMMIT="unknown"
if command -v git >/dev/null 2>&1 && git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GIT_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
fi

PACKAGE_NAME="update_package_v${VERSION}.tar.xz"
OUTPUT_DIR="$(fish_realpath "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR"

STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fish-update-build.XXXXXX")"
PACKAGE_ROOT="$STAGE_ROOT/update_package"
COMPONENTS_TSV="$STAGE_ROOT/components.tsv"
trap 'rm -rf "$STAGE_ROOT"' EXIT

mkdir -p "$PACKAGE_ROOT" "$PACKAGE_ROOT/lib"
: > "$COMPONENTS_TSV"

if [[ "$SKIP_FRONTEND_BUILD" == "1" ]]; then
  fish_warn "--skip-frontend-build is a compatibility no-op in source-update mode"
fi

copy_component() {
  local name="$1"
  local source_dir="$2"
  local target_dir="$3"
  shift 3
  local excludes=("$@")

  copy_component_to "$name" "$source_dir" "$target_dir" "${name}/" "$name" "$name" "${excludes[@]}"
}

copy_component_to() {
  local name="$1"
  local source_dir="$2"
  local target_dir="$3"
  local archive_dir="$4"
  local target_subdir="$5"
  local current_link="$6"
  shift 6
  local excludes=("$@")

  if [[ ! -d "$source_dir" ]]; then
    fish_die "required component directory not found: $source_dir"
  fi

  mkdir -p "$target_dir"
  local rsync_args=(-a)
  local pattern=""
  for pattern in "${excludes[@]}"; do
    rsync_args+=(--exclude "$pattern")
  done
  rsync "${rsync_args[@]}" "$source_dir"/ "$target_dir"/
  printf '%s\t%s\t%s\t%s\n' "$name" "$archive_dir" "$target_subdir" "$current_link" >> "$COMPONENTS_TSV"
}

copy_component \
  "backend" \
  "$PROJECT_ROOT/backend" \
  "$PACKAGE_ROOT/backend" \
  ".env" \
  ".env.local" \
  "__pycache__/" \
  "*.pyc" \
  "*.pyo"

copy_component \
  "frontend" \
  "$PROJECT_ROOT/frontend" \
  "$PACKAGE_ROOT/frontend" \
  "node_modules/" \
  ".vite/" \
  "dist/" \
  "docs/" \
  "coverage/" \
  "*.tsbuildinfo" \
  "__pycache__/" \
  "*.pyc" \
  "*.pyo"

if [[ "$INCLUDE_ALGORITHMS" != "0" && -d "$PROJECT_ROOT/algorithms" ]]; then
  copy_component \
    "algorithms" \
    "$PROJECT_ROOT/algorithms" \
    "$PACKAGE_ROOT/algorithms" \
    "__pycache__/" \
    "pth/" \
    "bert-base-uncased/" \
    "*.pyc" \
    "*.pyo"
fi

if [[ "$INCLUDE_ALGORITHM_MODEL_ASSETS" != "0" && -d "$PROJECT_ROOT/algorithms/pth" ]]; then
  copy_component_to \
    "algorithms-pth" \
    "$PROJECT_ROOT/algorithms/pth" \
    "$PACKAGE_ROOT/algorithms/pth" \
    "algorithms/pth/" \
    "algorithms/pth" \
    "algorithms/pth" \
    "__pycache__/" \
    "*.pyc" \
    "*.pyo"
fi

if [[ "$INCLUDE_ALGORITHM_MODEL_ASSETS" != "0" && -d "$PROJECT_ROOT/algorithms/bert-base-uncased" ]]; then
  copy_component_to \
    "algorithms-bert-base-uncased" \
    "$PROJECT_ROOT/algorithms/bert-base-uncased" \
    "$PACKAGE_ROOT/algorithms/bert-base-uncased" \
    "algorithms/bert-base-uncased/" \
    "algorithms/bert-base-uncased" \
    "algorithms/bert-base-uncased" \
    "__pycache__/" \
    "*.pyc" \
    "*.pyo"
fi

if [[ "$INCLUDE_MEDIAMTX" != "0" && -d "$PROJECT_ROOT/mediamtx" ]]; then
  copy_component \
    "mediamtx" \
    "$PROJECT_ROOT/mediamtx" \
    "$PACKAGE_ROOT/mediamtx" \
    "__pycache__/" \
    "*.pyc" \
    "*.pyo"
fi

if [[ "$INCLUDE_UPDATE_MANAGER" != "0" && -d "$SCRIPT_DIR" ]]; then
  copy_component \
    "update" \
    "$SCRIPT_DIR" \
    "$PACKAGE_ROOT/update" \
    "dist/" \
    "__pycache__/" \
    "*.pyc" \
    "*.pyo"
fi

cp -a "$PROJECT_ROOT/start_dev.sh" "$PACKAGE_ROOT/start_dev.sh"
cp -a "$PROJECT_ROOT/stop_dev.sh" "$PACKAGE_ROOT/stop_dev.sh"
cp -a "$PROJECT_ROOT/version.json" "$PACKAGE_ROOT/version.json"
cp -a "$SCRIPT_DIR/update.sh" "$PACKAGE_ROOT/update.sh"
cp -a "$SCRIPT_DIR/stop.sh" "$PACKAGE_ROOT/stop.sh"
cp -a "$SCRIPT_DIR/health_check.sh" "$PACKAGE_ROOT/health_check.sh"
cp -a "$SCRIPT_DIR/lib/common.sh" "$PACKAGE_ROOT/lib/common.sh"

(
  cd "$PROJECT_ROOT/backend"
  if ! command -v uv >/dev/null 2>&1; then
    fish_die "uv is required to export backend_requirements.lock.txt from backend/uv.lock"
  fi
  uv export \
    --frozen \
    --format requirements.txt \
    --output-file "$PACKAGE_ROOT/backend_requirements.lock.txt"
)

UNPACKED_BYTES="$(du -sb "$PACKAGE_ROOT" | awk '{print $1}')"
export PACKAGE_ROOT COMPONENTS_TSV BUILD_TIME GIT_COMMIT UNPACKED_BYTES
"$PYTHON_BIN" - <<'PY'
import json
import os
import pathlib

package_root = pathlib.Path(os.environ["PACKAGE_ROOT"])
components_tsv = pathlib.Path(os.environ["COMPONENTS_TSV"])
build_time = os.environ["BUILD_TIME"]
git_commit = os.environ["GIT_COMMIT"]
unpacked_bytes = int(os.environ["UNPACKED_BYTES"])

version_path = package_root / "version.json"
version_payload = json.loads(version_path.read_text(encoding="utf-8"))
version = str(version_payload.get("version") or version_payload.get("current_version") or "").strip()
version_payload["version"] = version
version_payload["current_version"] = version
version_payload["build_time"] = build_time
version_payload["git_commit"] = git_commit
version_payload["min_config_version"] = int(version_payload.get("min_config_version", 1) or 1)
version_payload["bundle_format_version"] = int(version_payload.get("bundle_format_version", 1) or 1)
version_payload["package_format"] = "source_tar_xz_v1"
version_payload["package_mode"] = "runtime_source"
version_path.write_text(json.dumps(version_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

components = []
for raw_line in components_tsv.read_text(encoding="utf-8").splitlines():
    if not raw_line.strip():
        continue
    name, archive, target_subdir, current_link = raw_line.split("\t")
    components.append(
        {
            "name": name,
            "archive": archive,
            "target_subdir": target_subdir,
            "current_link": current_link,
        }
    )

component_names = {item["name"] for item in components}
model_assets_included = {
    "algorithms/pth/": "algorithms-pth" in component_names,
    "algorithms/bert-base-uncased/": "algorithms-bert-base-uncased" in component_names,
}

preserve_paths = [
    "backend/.env",
    "backend/.env.local",
    ".config/",
    "data/",
]
excluded_paths = [
    "data/",
    "backups/",
    ".git/",
    ".github/",
    ".vscode/",
    "temp_update/",
    "update/dist/",
    "frontend/node_modules/",
    "frontend/.vite/",
    "frontend/dist/",
    "frontend/docs/",
]

for path_name, included in model_assets_included.items():
    if not included:
        preserve_paths.append(path_name)
        excluded_paths.append(path_name)

manifest_payload = {
    "format_version": 1,
    "components": components,
    "preserve_paths": preserve_paths,
    "excluded_paths": excluded_paths,
    "unpacked_bytes": unpacked_bytes,
    "healthcheck": {
        "backend": "http://127.0.0.1:8000/api/health",
        "database": "http://127.0.0.1:8000/api/health/database",
        "frontend": "http://127.0.0.1:5173/",
    },
    "required_commands": [
        "bash",
        "python3",
        "tar",
        "xz",
        "rsync",
        "sha256sum",
        "curl",
        "npm",
    ],
    "python_dep_source": "uv.lock",
    "frontend_runtime_mode": "vite_dev",
}

(package_root / "manifest.json").write_text(
    json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
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
