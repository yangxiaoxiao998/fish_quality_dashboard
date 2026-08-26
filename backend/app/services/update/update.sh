#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd -P)"
# shellcheck source=update/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
  fish_die "usage: update.sh <apply|rollback> [options]"
fi
shift || true

PACKAGE_PATH=""
BACKUP_DIR_ARG=""
INSTALL_ROOT="${FISH_INSTALL_ROOT:-}"
CONFIG_ROOT=""
DATA_ROOT=""
STATE_ROOT=""
RESULT_FILE=""
FORCE=0
SKIP_MIGRATIONS=0
SKIP_FRONTEND_INSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --package)
      PACKAGE_PATH="${2:?missing value for --package}"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR_ARG="${2:?missing value for --backup-dir}"
      shift 2
      ;;
    --install-root)
      INSTALL_ROOT="${2:?missing value for --install-root}"
      shift 2
      ;;
    --config-root)
      CONFIG_ROOT="${2:?missing value for --config-root}"
      shift 2
      ;;
    --data-root)
      DATA_ROOT="${2:?missing value for --data-root}"
      shift 2
      ;;
    --state-root)
      STATE_ROOT="${2:?missing value for --state-root}"
      shift 2
      ;;
    --result-file)
      RESULT_FILE="${2:?missing value for --result-file}"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --skip-migrations)
      SKIP_MIGRATIONS=1
      shift
      ;;
    --skip-frontend-install)
      SKIP_FRONTEND_INSTALL=1
      shift
      ;;
    *)
      fish_die "unknown argument: $1"
      ;;
  esac
done

if [[ -z "$INSTALL_ROOT" ]]; then
  INSTALL_ROOT="$PROJECT_ROOT"
fi
INSTALL_ROOT="$(fish_realpath "$INSTALL_ROOT")"
CONFIG_ROOT="${CONFIG_ROOT:-$INSTALL_ROOT/.config}"
DATA_ROOT="${DATA_ROOT:-$INSTALL_ROOT/data}"
STATE_ROOT="${STATE_ROOT:-$DATA_ROOT/state}"
CONFIG_ROOT="$(fish_realpath "$CONFIG_ROOT")"
DATA_ROOT="$(fish_realpath "$DATA_ROOT")"
STATE_ROOT="$(fish_realpath "$STATE_ROOT")"

PYTHON_BIN="$(fish_python_bin)"
export FISH_UPDATE_PYTHON_BIN="$PYTHON_BIN"

TMP_ROOT="$INSTALL_ROOT/temp_update"
BACKUP_ROOT="$INSTALL_ROOT/backups"
LOCK_DIR="$STATE_ROOT/software_update/update.lock.d"
RUN_LOG_DIR="$DATA_ROOT/logs/update"
mkdir -p "$TMP_ROOT" "$BACKUP_ROOT" "$RUN_LOG_DIR" "$STATE_ROOT/software_update"

if [[ -z "$RESULT_FILE" ]]; then
  RESULT_FILE="$TMP_ROOT/last_${MODE}_result.json"
fi

LOG_FILE="$RUN_LOG_DIR/${MODE}_$(fish_now_ts).log"
exec > >(tee -a "$LOG_FILE") 2>&1

RUNTIME_HEALTH_CHECK_SH="$TMP_ROOT/runtime_health_check.sh"
RUNTIME_HEALTH_CHECK_LIB_DIR="$TMP_ROOT/lib"
RUNTIME_HEALTH_CHECK_INITIAL_DELAY="${FISH_HEALTH_CHECK_INITIAL_DELAY:-30}"
RUNTIME_HEALTH_CHECK_RETRY_INTERVAL="${FISH_HEALTH_CHECK_RETRY_INTERVAL:-10}"
RUNTIME_HEALTH_CHECK_MAX_ATTEMPTS="${FISH_HEALTH_CHECK_MAX_ATTEMPTS:-10}"
cp -a "$SCRIPT_DIR/health_check.sh" "$RUNTIME_HEALTH_CHECK_SH"
mkdir -p "$RUNTIME_HEALTH_CHECK_LIB_DIR"
cp -a "$SCRIPT_DIR/lib/common.sh" "$RUNTIME_HEALTH_CHECK_LIB_DIR/common.sh"
chmod 0755 "$RUNTIME_HEALTH_CHECK_SH"

PAYLOAD_ROOT=""
PACKAGE_ROOT_NAME=""
PAYLOAD_VERSION=""
CURRENT_VERSION=""
BACKUP_DIR=""
OLD_ALEMBIC_REVISION=""
NEW_ALEMBIC_REVISION=""
COMPONENT_RECORDS_TSV=""
ROOT_RECORDS_TSV=""
APPLY_STATUS="failed"
ROLLBACK_STATUS="failed"
UPDATED_TARGETS=""

cleanup() {
  if [[ -d "$LOCK_DIR" ]]; then
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT

acquire_lock() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    fish_die "another update is already running: $LOCK_DIR"
  fi
}

write_result() {
  local json_payload="$1"
  printf '%s' "$json_payload" | fish_write_json "$RESULT_FILE"
}

run_runtime_health_check_with_retries() {
  local attempt=1

  if (( RUNTIME_HEALTH_CHECK_INITIAL_DELAY > 0 )); then
    fish_log "[health] waiting ${RUNTIME_HEALTH_CHECK_INITIAL_DELAY}s before runtime health checks"
    sleep "$RUNTIME_HEALTH_CHECK_INITIAL_DELAY"
  fi

  while (( attempt <= RUNTIME_HEALTH_CHECK_MAX_ATTEMPTS )); do
    fish_log "[health] runtime health check attempt ${attempt}/${RUNTIME_HEALTH_CHECK_MAX_ATTEMPTS}"
    if bash "$RUNTIME_HEALTH_CHECK_SH" --install-root "$INSTALL_ROOT" --python-bin "$PYTHON_BIN"; then
      return 0
    fi

    if (( attempt == RUNTIME_HEALTH_CHECK_MAX_ATTEMPTS )); then
      break
    fi

    fish_warn "runtime health check attempt ${attempt}/${RUNTIME_HEALTH_CHECK_MAX_ATTEMPTS} failed; retry in ${RUNTIME_HEALTH_CHECK_RETRY_INTERVAL}s"
    sleep "$RUNTIME_HEALTH_CHECK_RETRY_INTERVAL"
    attempt=$(( attempt + 1 ))
  done

  fish_die "runtime health check failed after ${RUNTIME_HEALTH_CHECK_MAX_ATTEMPTS} attempts"
}

source_backend_env() {
  if [[ -f "$INSTALL_ROOT/backend/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$INSTALL_ROOT/backend/.env"
    set +a
  fi
  if [[ -f "$INSTALL_ROOT/backend/.env.local" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$INSTALL_ROOT/backend/.env.local"
    set +a
  fi
}

detect_node_and_npm() {
  if [[ -n "${FISH_NODE_BIN:-}" && -x "${FISH_NODE_BIN}" ]]; then
    export PATH
    PATH="$(dirname "${FISH_NODE_BIN}"):$PATH"
  fi
  if [[ -z "${NPM_BIN:-}" ]]; then
    NPM_BIN="${FISH_NPM_BIN:-}"
  fi
  if [[ -z "${NPM_BIN:-}" ]]; then
    NPM_BIN="$(command -v npm 2>/dev/null || true)"
  fi
  if [[ -z "${NPM_BIN:-}" ]]; then
    fish_die "npm not found on PATH; install Node.js/npm or set FISH_NPM_BIN"
  fi
}

read_version_field() {
  local json_file="$1"
  "$PYTHON_BIN" - "$json_file" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    print("")
    raise SystemExit(0)
payload = json.loads(path.read_text(encoding="utf-8"))
value = str(payload.get("version") or payload.get("current_version") or "").strip()
print(value)
PY
}

read_config_version() {
  local json_file="$1"
  "$PYTHON_BIN" - "$json_file" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    print(1)
    raise SystemExit(0)
payload = json.loads(path.read_text(encoding="utf-8"))
print(int(payload.get("config_version", 1) or 1))
PY
}

validate_package_and_extract() {
  local package_path="$1"
  local stage_dir="$2"

  fish_require_command tar xz sha256sum rsync curl find df timeout

  case "$package_path" in
    *.tar.xz|*.txz) ;;
    *)
      fish_die "update package must be .tar.xz or .txz: $package_path"
      ;;
  esac

  if [[ ! -f "$package_path" ]]; then
    fish_die "update package not found: $package_path"
  fi

  local header
  header="$("$PYTHON_BIN" - "$package_path" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
header = path.read_bytes()[:6]
print(header.hex())
PY
)"
  if [[ "$header" != "fd377a585a00" ]]; then
    fish_die "update package magic bytes do not match xz format: $package_path"
  fi

  "$PYTHON_BIN" - "$package_path" <<'PY'
import pathlib
import sys
import tarfile

package_path = pathlib.Path(sys.argv[1])
with tarfile.open(package_path, mode="r:xz") as archive:
    for member in archive.getmembers():
        name = member.name.replace("\\", "/")
        normalized = pathlib.PurePosixPath(name)
        if normalized.is_absolute():
            raise SystemExit(f"archive entry must be relative: {name}")
        if any(part == ".." for part in normalized.parts):
            raise SystemExit(f"archive entry escapes extraction root: {name}")
PY

  mkdir -p "$stage_dir"
  tar -xJf "$package_path" -C "$stage_dir"

  PAYLOAD_ROOT="$("$PYTHON_BIN" - "$stage_dir" <<'PY'
import pathlib
import sys

stage_dir = pathlib.Path(sys.argv[1]).resolve()
if (stage_dir / "manifest.json").is_file() and (stage_dir / "version.json").is_file():
    print(stage_dir)
    raise SystemExit(0)

candidates = []
for version_path in stage_dir.rglob("version.json"):
    if ".git" in version_path.parts:
        continue
    manifest_path = version_path.parent / "manifest.json"
    if manifest_path.is_file():
        candidates.append(version_path.parent.resolve())

if not candidates:
    raise SystemExit("manifest.json/version.json not found in extracted package")

candidates = sorted(set(candidates), key=lambda item: len(item.parts))
if len(candidates) != 1:
    joined = ", ".join(str(item) for item in candidates)
    raise SystemExit(f"multiple payload roots found: {joined}")

print(candidates[0])
PY
)"

  if [[ ! -f "$PAYLOAD_ROOT/checksums.sha256" ]]; then
    fish_die "checksums.sha256 not found in payload root: $PAYLOAD_ROOT"
  fi
  (
    cd "$PAYLOAD_ROOT"
    sha256sum -c checksums.sha256
  )
}

load_payload_root_from_self() {
  if [[ -f "$SCRIPT_DIR/manifest.json" && -f "$SCRIPT_DIR/version.json" ]]; then
    PAYLOAD_ROOT="$SCRIPT_DIR"
  else
    fish_die "payload root is not extracted and --package was not provided"
  fi
}

parse_manifest_components() {
  local manifest_path="$1"
  local output_tsv="$2"
  "$PYTHON_BIN" - "$manifest_path" "$output_tsv" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
output_tsv = pathlib.Path(sys.argv[2])
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
components = payload.get("components")
if not isinstance(components, list) or not components:
    raise SystemExit("manifest.json must contain a non-empty components list")

lines = []
for item in components:
    if not isinstance(item, dict):
        raise SystemExit("manifest.json contains an invalid component entry")
    name = str(item.get("name") or "").strip()
    archive = str(item.get("archive") or "").strip()
    target_subdir = str(item.get("target_subdir") or name).strip()
    current_link = str(item.get("current_link") or target_subdir).strip()
    if not name or not archive:
      raise SystemExit("manifest.json contains an incomplete component entry")
    lines.append("\t".join([name, archive, target_subdir, current_link]))

output_tsv.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY
}

component_backup_excludes() {
  local component_name="$1"
  case "$component_name" in
    algorithms)
      printf '%s\n' 'pth/' 'bert-base-uncased/' '__pycache__/' '*.pyc' '*.pyo'
      ;;
    *)
      return 0
      ;;
  esac
}

component_sync_excludes() {
  local component_name="$1"
  case "$component_name" in
    backend)
      printf '%s\n' '.env' '.env.local'
      ;;
    frontend)
      printf '%s\n' 'node_modules/' '.vite/' 'dist/' 'docs/' 'coverage/' '*.tsbuildinfo'
      ;;
    algorithms)
      printf '%s\n' 'pth/' 'bert-base-uncased/'
      ;;
    update)
      printf '%s\n' 'dist/' '__pycache__/' '*.pyc' '*.pyo'
      ;;
    *)
      return 0
      ;;
  esac
}

rsync_with_excludes() {
  local source_dir="$1"
  local dest_dir="$2"
  local delete_mode="$3"
  shift 3
  local patterns=("$@")
  local rsync_args=(-a)
  if [[ "$delete_mode" == "delete" ]]; then
    rsync_args+=(--delete)
  fi
  local pattern=""
  for pattern in "${patterns[@]}"; do
    rsync_args+=(--exclude "$pattern")
  done
  mkdir -p "$dest_dir"
  rsync "${rsync_args[@]}" "$source_dir"/ "$dest_dir"/
}

backup_component() {
  local component_name="$1"
  local target_subdir="$2"
  local target_dir="$INSTALL_ROOT/$target_subdir"
  local backup_dir_component="$BACKUP_DIR/components/$component_name"
  local existed_before="false"
  local -a excludes=()
  local line=""

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    excludes+=("$line")
  done < <(component_backup_excludes "$component_name" || true)

  if [[ -d "$target_dir" ]]; then
    existed_before="true"
    mkdir -p "$backup_dir_component"
    rsync_with_excludes "$target_dir" "$backup_dir_component" "no-delete" "${excludes[@]}"
  fi

  printf '%s\t%s\t%s\t%s\n' \
    "$component_name" \
    "$target_subdir" \
    "components/$component_name" \
    "$existed_before" >> "$COMPONENT_RECORDS_TSV"
}

apply_component() {
  local component_name="$1"
  local archive_dir="$2"
  local target_subdir="$3"
  local source_dir="$PAYLOAD_ROOT/${archive_dir%/}"
  local target_dir="$INSTALL_ROOT/$target_subdir"
  local -a excludes=()
  local line=""

  if [[ ! -d "$source_dir" ]]; then
    echo "[error] payload component not found: $source_dir" >&2
    return 1
  fi

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    excludes+=("$line")
  done < <(component_sync_excludes "$component_name" || true)

  rsync_with_excludes "$source_dir" "$target_dir" "delete" "${excludes[@]}"
  UPDATED_TARGETS="${UPDATED_TARGETS}${UPDATED_TARGETS:+,}$component_name"
}

backup_root_files() {
  local file_name=""
  for file_name in start_dev.sh stop_dev.sh version.json; do
    local source_path="$INSTALL_ROOT/$file_name"
    local existed_before="false"
    if [[ -f "$source_path" ]]; then
      existed_before="true"
      mkdir -p "$BACKUP_DIR/root"
      cp -a "$source_path" "$BACKUP_DIR/root/$file_name"
    fi
    printf '%s\t%s\t%s\n' "$file_name" "root/$file_name" "$existed_before" >> "$ROOT_RECORDS_TSV"
  done
}

restore_root_files() {
  local line=""
  while IFS=$'\t' read -r file_name relative_backup_path existed_before; do
    [[ -n "$file_name" ]] || continue
    local target_path="$INSTALL_ROOT/$file_name"
    if [[ "$existed_before" == "true" ]]; then
      mkdir -p "$(dirname "$target_path")"
      cp -a "$BACKUP_DIR/$relative_backup_path" "$target_path"
    else
      rm -f "$target_path"
    fi
  done < "$ROOT_RECORDS_TSV"
}

restore_component() {
  local component_name="$1"
  local target_subdir="$2"
  local relative_backup_path="$3"
  local existed_before="$4"
  local target_dir="$INSTALL_ROOT/$target_subdir"
  local source_dir="$BACKUP_DIR/$relative_backup_path"
  local -a excludes=()
  local line=""

  if [[ "$existed_before" != "true" ]]; then
    rm -rf "$target_dir"
    return 0
  fi

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    excludes+=("$line")
  done < <(component_backup_excludes "$component_name" || true)

  rsync_with_excludes "$source_dir" "$target_dir" "delete" "${excludes[@]}"
}

write_backup_manifest() {
  local status="$1"
  local payload_path="$2"
  export BACKUP_DIR CURRENT_VERSION PAYLOAD_VERSION OLD_ALEMBIC_REVISION NEW_ALEMBIC_REVISION
  export MANIFEST_STATUS="$status"
  export PACKAGE_PAYLOAD_PATH="$payload_path"
  export COMPONENT_RECORDS_TSV ROOT_RECORDS_TSV
  "$PYTHON_BIN" - <<'PY'
import json
import os
import pathlib

backup_dir = pathlib.Path(os.environ["BACKUP_DIR"])
components_tsv = pathlib.Path(os.environ["COMPONENT_RECORDS_TSV"])
root_tsv = pathlib.Path(os.environ["ROOT_RECORDS_TSV"])

components = []
if components_tsv.exists():
    for raw_line in components_tsv.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        name, target_subdir, backup_rel, existed_before = raw_line.split("\t")
        components.append(
            {
                "name": name,
                "target_subdir": target_subdir,
                "backup_rel": backup_rel,
                "existed_before": existed_before == "true",
            }
        )

root_files = []
if root_tsv.exists():
    for raw_line in root_tsv.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        name, backup_rel, existed_before = raw_line.split("\t")
        root_files.append(
            {
                "name": name,
                "backup_rel": backup_rel,
                "existed_before": existed_before == "true",
            }
        )

payload = {
    "status": os.environ["MANIFEST_STATUS"],
    "package_path": os.environ["PACKAGE_PAYLOAD_PATH"],
    "current_version": os.environ["CURRENT_VERSION"],
    "new_version": os.environ["PAYLOAD_VERSION"],
    "created_at": os.environ.get("BACKUP_CREATED_AT") or "",
    "updated_at": os.environ.get("BACKUP_UPDATED_AT") or "",
    "alembic_old_revision": os.environ.get("OLD_ALEMBIC_REVISION") or "",
    "alembic_new_revision": os.environ.get("NEW_ALEMBIC_REVISION") or "",
    "components": components,
    "root_files": root_files,
}

(backup_dir / "manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
}

copy_root_files_from_payload() {
  local file_name=""
  for file_name in start_dev.sh stop_dev.sh version.json; do
    if [[ -f "$PAYLOAD_ROOT/$file_name" ]]; then
      cp -a "$PAYLOAD_ROOT/$file_name" "$INSTALL_ROOT/$file_name"
    fi
  done
}

copy_package_metadata_into_update_dir() {
  mkdir -p "$INSTALL_ROOT/update"
  if [[ -f "$PAYLOAD_ROOT/backend_requirements.lock.txt" ]]; then
    cp -a "$PAYLOAD_ROOT/backend_requirements.lock.txt" "$SCRIPT_DIR/backend_requirements.lock.txt"
  fi
  if [[ -f "$PAYLOAD_ROOT/manifest.json" ]]; then
    cp -a "$PAYLOAD_ROOT/manifest.json" "$SCRIPT_DIR/current_package_manifest.json"
  fi
}

restore_permissions() {
  find "$INSTALL_ROOT" -type f -name "*.sh" -exec chmod 0755 {} +
  chmod 0755 "$INSTALL_ROOT/start_dev.sh" 2>/dev/null || true
  chmod 0755 "$INSTALL_ROOT/stop_dev.sh" 2>/dev/null || true
  chmod 0755 "$INSTALL_ROOT/backend/start_backend.sh" 2>/dev/null || true
  find "$INSTALL_ROOT/update" -maxdepth 2 -type f -name "*.sh" -exec chmod 0755 {} + 2>/dev/null || true
  if [[ -f "$INSTALL_ROOT/mediamtx/mediamtx" ]]; then
    chmod 0755 "$INSTALL_ROOT/mediamtx/mediamtx" 2>/dev/null || true
  fi
}

sync_python_dependencies() {
  local requirements_path="$1"
  if [[ ! -f "$requirements_path" ]]; then
    fish_warn "python requirements lock not found, skip pip install: $requirements_path"
    return 0
  fi
  fish_log "[update] pip install -r $requirements_path"
  "$PYTHON_BIN" -m pip install -r "$requirements_path"
}

sync_frontend_dependencies() {
  if [[ "$SKIP_FRONTEND_INSTALL" == "1" ]]; then
    fish_warn "skip frontend dependency install by option"
    return 0
  fi
  detect_node_and_npm
  local frontend_dir="$INSTALL_ROOT/frontend"
  local install_timeout="${FISH_FRONTEND_INSTALL_TIMEOUT_SECONDS:-1800}"
  local heartbeat_interval="${FISH_FRONTEND_INSTALL_HEARTBEAT_SECONDS:-60}"
  local poll_interval="${FISH_FRONTEND_INSTALL_POLL_INTERVAL_SECONDS:-5}"
  if [[ ! -f "$frontend_dir/package.json" ]]; then
    fish_warn "frontend/package.json not found, skip npm install"
    return 0
  fi
  if [[ -f "$frontend_dir/package-lock.json" ]]; then
    fish_log "[update] npm ci (timeout=${install_timeout}s)"
    (
      cd "$frontend_dir"
      timeout --preserve-status --kill-after=30s "$install_timeout" \
        "$NPM_BIN" ci --no-fund --no-audit --prefer-offline --progress=false
    ) &
    local npm_runner_pid=$!
    local npm_started_at
    npm_started_at="$(date +%s)"
    local npm_heartbeat_elapsed=0
    while kill -0 "$npm_runner_pid" 2>/dev/null; do
      sleep "$poll_interval"
      npm_heartbeat_elapsed=$(( npm_heartbeat_elapsed + poll_interval ))
      if (( npm_heartbeat_elapsed >= heartbeat_interval )) && kill -0 "$npm_runner_pid" 2>/dev/null; then
        local npm_elapsed
        npm_elapsed="$(( $(date +%s) - npm_started_at ))"
        fish_log "[update] npm ci still running (${npm_elapsed}s elapsed, heartbeat every ${heartbeat_interval}s)"
        npm_heartbeat_elapsed=0
      fi
    done
    set +e
    wait "$npm_runner_pid"
    local npm_rc=$?
    set -e
    if [[ "$npm_rc" -eq 124 ]]; then
      fish_die "frontend dependency install timed out after ${install_timeout}s"
    fi
    if [[ "$npm_rc" -ne 0 ]]; then
      fish_die "frontend dependency install failed with exit code ${npm_rc}"
    fi
  else
    fish_log "[update] npm install (timeout=${install_timeout}s)"
    (
      cd "$frontend_dir"
      timeout --preserve-status --kill-after=30s "$install_timeout" \
        "$NPM_BIN" install --no-fund --no-audit --prefer-offline --progress=false
    ) &
    local npm_runner_pid=$!
    local npm_started_at
    npm_started_at="$(date +%s)"
    local npm_heartbeat_elapsed=0
    while kill -0 "$npm_runner_pid" 2>/dev/null; do
      sleep "$poll_interval"
      npm_heartbeat_elapsed=$(( npm_heartbeat_elapsed + poll_interval ))
      if (( npm_heartbeat_elapsed >= heartbeat_interval )) && kill -0 "$npm_runner_pid" 2>/dev/null; then
        local npm_elapsed
        npm_elapsed="$(( $(date +%s) - npm_started_at ))"
        fish_log "[update] npm install still running (${npm_elapsed}s elapsed, heartbeat every ${heartbeat_interval}s)"
        npm_heartbeat_elapsed=0
      fi
    done
    set +e
    wait "$npm_runner_pid"
    local npm_rc=$?
    set -e
    if [[ "$npm_rc" -eq 124 ]]; then
      fish_die "frontend dependency install timed out after ${install_timeout}s"
    fi
    if [[ "$npm_rc" -ne 0 ]]; then
      fish_die "frontend dependency install failed with exit code ${npm_rc}"
    fi
  fi
}

get_alembic_revision() {
  if [[ ! -f "$INSTALL_ROOT/backend/alembic.ini" ]]; then
    return 0
  fi
  (
    cd "$INSTALL_ROOT/backend"
    "$PYTHON_BIN" -m alembic -c "$INSTALL_ROOT/backend/alembic.ini" current 2>/dev/null \
      | tail -n 1 \
      | awk '{print $1}'
  ) || true
}

run_migrations() {
  if [[ "$SKIP_MIGRATIONS" == "1" ]]; then
    fish_warn "skip migrations by option"
    return 0
  fi
  source_backend_env
  fish_log "[update] alembic upgrade head"
  (
    cd "$INSTALL_ROOT/backend"
    "$PYTHON_BIN" -m alembic -c "$INSTALL_ROOT/backend/alembic.ini" upgrade head
  )
}

maybe_downgrade_database() {
  local target_revision="$1"
  if [[ -z "$target_revision" ]]; then
    return 0
  fi
  local current_revision=""
  current_revision="$(get_alembic_revision)"
  if [[ -z "$current_revision" || "$current_revision" == "$target_revision" ]]; then
    return 0
  fi
  source_backend_env
  fish_log "[rollback] alembic downgrade $target_revision"
  (
    cd "$INSTALL_ROOT/backend"
    "$PYTHON_BIN" -m alembic -c "$INSTALL_ROOT/backend/alembic.ini" downgrade "$target_revision"
  )
}

estimate_required_space_bytes() {
  local payload_root="$1"
  local manifest_path="$2"
  "$PYTHON_BIN" - "$payload_root" "$manifest_path" "$INSTALL_ROOT" <<'PY'
import json
import os
import pathlib
import shutil
import sys

payload_root = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])
install_root = pathlib.Path(sys.argv[3])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
unpacked = int(manifest.get("unpacked_bytes", 0) or 0)
current_size = 0
for item in manifest.get("components", []):
    target = install_root / str(item.get("target_subdir") or item.get("name") or "")
    if target.exists():
        current_size += sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
for file_name in ("start_dev.sh", "stop_dev.sh", "version.json"):
    target = install_root / file_name
    if target.exists() and target.is_file():
        current_size += target.stat().st_size
print(int(current_size + unpacked + 512 * 1024 * 1024))
PY
}

available_space_bytes() {
  df -PB1 "$INSTALL_ROOT" | awk 'NR==2 {print $4}'
}

perform_rollback_from_backup() {
  fish_log "[rollback] restoring files from $BACKUP_DIR"
  bash "$SCRIPT_DIR/stop.sh" --install-root "$INSTALL_ROOT" || true

  local line=""
  while IFS=$'\t' read -r component_name target_subdir relative_backup_path existed_before; do
    [[ -n "$component_name" ]] || continue
    restore_component "$component_name" "$target_subdir" "$relative_backup_path" "$existed_before"
  done < "$COMPONENT_RECORDS_TSV"

  restore_root_files
  restore_permissions
  maybe_downgrade_database "$OLD_ALEMBIC_REVISION" || fish_warn "database downgrade failed"

  if [[ -f "$SCRIPT_DIR/backend_requirements.lock.txt" ]]; then
    sync_python_dependencies "$SCRIPT_DIR/backend_requirements.lock.txt" || fish_warn "pip reinstall during rollback failed"
  elif [[ -f "$INSTALL_ROOT/requirements.txt" ]]; then
    sync_python_dependencies "$INSTALL_ROOT/requirements.txt" || fish_warn "fallback pip reinstall during rollback failed"
  fi

  if [[ -d "$INSTALL_ROOT/frontend" ]]; then
    sync_frontend_dependencies || fish_warn "frontend dependency sync during rollback failed"
  fi

  bash "$INSTALL_ROOT/start_dev.sh"
  run_runtime_health_check_with_retries
}

apply_update() {
  local stage_dir="$TMP_ROOT/apply_stage_$(fish_now_ts)"
  local manifest_components_tsv="$TMP_ROOT/apply_components_$(fish_now_ts).tsv"
  PACKAGE_PATH="$(fish_realpath "$PACKAGE_PATH")"

  validate_package_and_extract "$PACKAGE_PATH" "$stage_dir"
  COMPONENT_RECORDS_TSV="$TMP_ROOT/backup_components_$(fish_now_ts).tsv"
  ROOT_RECORDS_TSV="$TMP_ROOT/backup_root_$(fish_now_ts).tsv"
  : > "$COMPONENT_RECORDS_TSV"
  : > "$ROOT_RECORDS_TSV"

  PAYLOAD_VERSION="$(read_version_field "$PAYLOAD_ROOT/version.json")"
  CURRENT_VERSION="$(read_version_field "$INSTALL_ROOT/version.json")"
  if [[ -z "$CURRENT_VERSION" ]]; then
    CURRENT_VERSION="1.0.0"
  fi

  local compare_result
  compare_result="$(fish_version_compare "$PAYLOAD_VERSION" "$CURRENT_VERSION")"
  if [[ "$compare_result" != "1" ]]; then
    fish_warn "version gate disabled: applying package version $PAYLOAD_VERSION over current version $CURRENT_VERSION"
  fi

  local current_config_version
  current_config_version="$(read_config_version "$CONFIG_ROOT/config-meta.json")"
  local min_config_version
  min_config_version="$(fish_json_get "$PAYLOAD_ROOT/version.json" "min_config_version" "1")"
  if (( current_config_version < min_config_version )); then
    fish_die "config version too old: current=$current_config_version required=$min_config_version"
  fi

  local required_bytes
  local available_bytes
  required_bytes="$(estimate_required_space_bytes "$PAYLOAD_ROOT" "$PAYLOAD_ROOT/manifest.json")"
  available_bytes="$(available_space_bytes)"
  if (( available_bytes < required_bytes )); then
    fish_die "insufficient disk space: available=$available_bytes required=$required_bytes"
  fi

  BACKUP_DIR="$BACKUP_ROOT/update_backup_$(fish_now_ts)_${CURRENT_VERSION}_to_${PAYLOAD_VERSION}"
  mkdir -p "$BACKUP_DIR"
  export BACKUP_CREATED_AT BACKUP_UPDATED_AT
  BACKUP_CREATED_AT="$(fish_now_iso)"
  BACKUP_UPDATED_AT="$BACKUP_CREATED_AT"

  parse_manifest_components "$PAYLOAD_ROOT/manifest.json" "$manifest_components_tsv"
  source_backend_env
  OLD_ALEMBIC_REVISION="$(get_alembic_revision)"

  while IFS=$'\t' read -r component_name archive_dir target_subdir current_link; do
    [[ -n "$component_name" ]] || continue
    backup_component "$component_name" "$target_subdir"
  done < "$manifest_components_tsv"
  backup_root_files
  write_backup_manifest "prepared" "$PACKAGE_PATH"

  bash "$SCRIPT_DIR/stop.sh" --install-root "$INSTALL_ROOT"

  while IFS=$'\t' read -r component_name archive_dir target_subdir current_link; do
    [[ -n "$component_name" ]] || continue
    apply_component "$component_name" "$archive_dir" "$target_subdir"
  done < "$manifest_components_tsv"

  copy_root_files_from_payload
  copy_package_metadata_into_update_dir
  restore_permissions
  sync_python_dependencies "$PAYLOAD_ROOT/backend_requirements.lock.txt"
  sync_frontend_dependencies
  run_migrations
  NEW_ALEMBIC_REVISION="$(get_alembic_revision)"

  bash "$INSTALL_ROOT/start_dev.sh"
  run_runtime_health_check_with_retries

  APPLY_STATUS="success"
  BACKUP_UPDATED_AT="$(fish_now_iso)"
  write_backup_manifest "completed" "$PACKAGE_PATH"
  write_result "$(cat <<JSON
{
  "ok": true,
  "action": "apply",
  "package_path": $(printf '%s' "$PACKAGE_PATH" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "current_version": $(printf '%s' "$CURRENT_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "new_version": $(printf '%s' "$PAYLOAD_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "backup_dir": $(printf '%s' "$BACKUP_DIR" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "updated_targets": $(printf '%s' "$UPDATED_TARGETS" | "$PYTHON_BIN" -c 'import json,sys; values=[item for item in sys.stdin.read().split(",") if item]; print(json.dumps(values))')
}
JSON
)"
}

rollback_command() {
  if [[ -z "$BACKUP_DIR_ARG" ]]; then
    fish_die "--backup-dir is required for rollback"
  fi
  BACKUP_DIR="$(fish_realpath "$BACKUP_DIR_ARG")"
  if [[ ! -d "$BACKUP_DIR" ]]; then
    fish_die "backup directory not found: $BACKUP_DIR"
  fi

  COMPONENT_RECORDS_TSV="$TMP_ROOT/rollback_components_$(fish_now_ts).tsv"
  ROOT_RECORDS_TSV="$TMP_ROOT/rollback_root_$(fish_now_ts).tsv"
  export BACKUP_DIR COMPONENT_RECORDS_TSV ROOT_RECORDS_TSV
  "$PYTHON_BIN" - <<'PY'
import json
import os
import pathlib

backup_dir = pathlib.Path(os.environ["BACKUP_DIR"])
manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
components_path = pathlib.Path(os.environ["COMPONENT_RECORDS_TSV"])
root_path = pathlib.Path(os.environ["ROOT_RECORDS_TSV"])

component_lines = []
for item in manifest.get("components", []):
    component_lines.append(
        "\t".join(
            [
                str(item.get("name") or ""),
                str(item.get("target_subdir") or ""),
                str(item.get("backup_rel") or ""),
                "true" if bool(item.get("existed_before")) else "false",
            ]
        )
    )
components_path.write_text("\n".join(component_lines) + ("\n" if component_lines else ""), encoding="utf-8")

root_lines = []
for item in manifest.get("root_files", []):
    root_lines.append(
        "\t".join(
            [
                str(item.get("name") or ""),
                str(item.get("backup_rel") or ""),
                "true" if bool(item.get("existed_before")) else "false",
            ]
        )
    )
root_path.write_text("\n".join(root_lines) + ("\n" if root_lines else ""), encoding="utf-8")
PY

  CURRENT_VERSION="$(read_version_field "$INSTALL_ROOT/version.json")"
  PAYLOAD_VERSION="$(fish_json_get "$BACKUP_DIR/manifest.json" "current_version" "$CURRENT_VERSION")"
  OLD_ALEMBIC_REVISION="$(fish_json_get "$BACKUP_DIR/manifest.json" "alembic_old_revision" "")"

  perform_rollback_from_backup
  BACKUP_UPDATED_AT="$(fish_now_iso)"
  write_backup_manifest "manually_rolled_back" "$BACKUP_DIR"
  write_result "$(cat <<JSON
{
  "ok": true,
  "action": "rollback",
  "backup_dir": $(printf '%s' "$BACKUP_DIR" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "current_version": $(printf '%s' "$CURRENT_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "restored_version": $(printf '%s' "$PAYLOAD_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "rolled_back_targets": $(cat "$COMPONENT_RECORDS_TSV" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps([line.split("\t")[0] for line in sys.stdin.read().splitlines() if line.strip()]))')
}
JSON
)"
}

handle_apply_failure() {
  local message="$1"
  fish_err "$message"
  if [[ -n "$BACKUP_DIR" && -f "$COMPONENT_RECORDS_TSV" && -f "$ROOT_RECORDS_TSV" ]]; then
    fish_warn "update failed, attempting rollback from $BACKUP_DIR"
    if perform_rollback_from_backup; then
      BACKUP_UPDATED_AT="$(fish_now_iso)"
      write_backup_manifest "rolled_back" "$PACKAGE_PATH"
      write_result "$(cat <<JSON
{
  "ok": false,
  "action": "apply",
  "status": "rolled_back",
  "package_path": $(printf '%s' "$PACKAGE_PATH" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "current_version": $(printf '%s' "$CURRENT_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "new_version": $(printf '%s' "$PAYLOAD_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "backup_dir": $(printf '%s' "$BACKUP_DIR" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "error": $(printf '%s' "$message" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
}
JSON
)"
      exit 1
    fi
  fi

  write_result "$(cat <<JSON
{
  "ok": false,
  "action": "apply",
  "status": "failed",
  "package_path": $(printf '%s' "$PACKAGE_PATH" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "current_version": $(printf '%s' "$CURRENT_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "new_version": $(printf '%s' "$PAYLOAD_VERSION" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "backup_dir": $(printf '%s' "$BACKUP_DIR" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "error": $(printf '%s' "$message" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
}
JSON
)"
  exit 1
}

acquire_lock
fish_log "[update] mode=$MODE"
fish_log "[update] install_root=$INSTALL_ROOT"
fish_log "[update] config_root=$CONFIG_ROOT"
fish_log "[update] data_root=$DATA_ROOT"
fish_log "[update] state_root=$STATE_ROOT"
fish_log "[update] log_file=$LOG_FILE"

case "$MODE" in
  apply)
    if [[ -z "$PACKAGE_PATH" ]]; then
      load_payload_root_from_self
      PACKAGE_PATH="$PAYLOAD_ROOT"
    fi
    if ! apply_update; then
      handle_apply_failure "update failed"
    fi
    ;;
  rollback)
    rollback_command
    ;;
  *)
    fish_die "unsupported mode: $MODE"
    ;;
esac
