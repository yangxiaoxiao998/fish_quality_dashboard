#!/usr/bin/env bash
set -euo pipefail

fish_now_ts() {
  date +"%Y%m%d_%H%M%S"
}

fish_now_iso() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

fish_log() {
  printf '%s\n' "$*"
}

fish_warn() {
  printf '[warn] %s\n' "$*" >&2
}

fish_err() {
  printf '[error] %s\n' "$*" >&2
}

fish_die() {
  local message="${1:-unknown error}"
  local exit_code="${2:-1}"
  fish_err "$message"
  exit "$exit_code"
}

fish_require_command() {
  local cmd=""
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      fish_die "required command not found: $cmd"
    fi
  done
}

fish_python_bin() {
  if [[ -n "${FISH_UPDATE_PYTHON_BIN:-}" && -x "${FISH_UPDATE_PYTHON_BIN}" ]]; then
    printf '%s\n' "${FISH_UPDATE_PYTHON_BIN}"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  fish_die "python3 or python is required"
}

fish_realpath() {
  local target="${1:-}"
  local py
  py="$(fish_python_bin)"
  "$py" - "$target" <<'PY'
import pathlib
import sys

print(pathlib.Path(sys.argv[1]).expanduser().resolve())
PY
}

fish_path_within() {
  local child="${1:-}"
  local root="${2:-}"
  local py
  py="$(fish_python_bin)"
  "$py" - "$child" "$root" <<'PY'
import pathlib
import sys

child = pathlib.Path(sys.argv[1]).expanduser().resolve()
root = pathlib.Path(sys.argv[2]).expanduser().resolve()
try:
    child.relative_to(root)
except ValueError:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

fish_json_get() {
  local json_file="${1:-}"
  local dotted_path="${2:-}"
  local default_value="${3:-}"
  local py
  py="$(fish_python_bin)"
  "$py" - "$json_file" "$dotted_path" "$default_value" <<'PY'
import json
import pathlib
import sys

json_file = pathlib.Path(sys.argv[1])
dotted_path = sys.argv[2]
default_value = sys.argv[3]

payload = json.loads(json_file.read_text(encoding="utf-8"))
value = payload
for part in dotted_path.split("."):
    if not part:
        continue
    if not isinstance(value, dict) or part not in value:
        print(default_value)
        raise SystemExit(0)
    value = value[part]

if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print(default_value)
else:
    print(value)
PY
}

fish_version_compare() {
  local left="${1:-}"
  local right="${2:-}"
  local py
  py="$(fish_python_bin)"
  "$py" - "$left" "$right" <<'PY'
import re
import sys

def normalize(raw: str):
    parts = re.split(r"[._+-]", raw.strip())
    normalized = []
    for item in parts:
        if item.isdigit():
            normalized.append((0, int(item)))
        else:
            normalized.append((1, item))
    return normalized

left = normalize(sys.argv[1])
right = normalize(sys.argv[2])

if left < right:
    print(-1)
elif left > right:
    print(1)
else:
    print(0)
PY
}

fish_write_json() {
  local output_path="${1:-}"
  local py
  local json_payload
  py="$(fish_python_bin)"
  json_payload="$(cat)"
  if [[ -z "$json_payload" ]]; then
    fish_die "fish_write_json received empty JSON payload"
  fi
  FISH_WRITE_JSON_PAYLOAD="$json_payload" "$py" - "$output_path" <<'PY'
import json
import os
import pathlib
import sys

output_path = pathlib.Path(sys.argv[1])
payload = json.loads(os.environ["FISH_WRITE_JSON_PAYLOAD"])
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}
