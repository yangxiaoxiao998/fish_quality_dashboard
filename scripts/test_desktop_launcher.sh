#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
LAUNCHER="$PROJECT_ROOT/desktop/launch_fish_quality.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass=0
fail=0

run_case() {
  local name="$1"
  shift
  if "$@"; then
    printf '[PASS] %s\n' "$name"
    pass=$((pass + 1))
  else
    printf '[FAIL] %s\n' "$name" >&2
    fail=$((fail + 1))
  fi
}

make_fake_runtime() {
  local case_dir="$1"
  mkdir -p "$case_dir/bin" "$case_dir/project/desktop" "$case_dir/project/frontend"
  touch "$case_dir/project/desktop/package.json"
  touch "$case_dir/project/desktop/main.cjs"
  touch "$case_dir/npm-cli.js"

  cat >"$case_dir/bin/node" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "${FAKE_NODE_LOG:?}"
if [[ "${1:-}" == *npm-cli.js && "${2:-}" == "install" ]]; then
  mkdir -p "${FAKE_DESKTOP_DIR:?}/node_modules/.bin"
  touch "$FAKE_DESKTOP_DIR/node_modules/.bin/electron"
  chmod +x "$FAKE_DESKTOP_DIR/node_modules/.bin/electron"
fi
if [[ "${1:-}" == *npm-cli.js && "${2:-}" == "exec" ]]; then
  exit "${FAKE_ELECTRON_EXIT:-0}"
fi
EOF
  chmod +x "$case_dir/bin/node"

  cat >"$case_dir/project/start_dev.sh" <<'EOF'
#!/usr/bin/env bash
touch "${FAKE_START_MARKER:?}"
EOF
  chmod +x "$case_dir/project/start_dev.sh"
}

case_already_running() {
  local d="$TMP_DIR/already"
  make_fake_runtime "$d"
  cat >"$d/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat >"$d/bin/pkill" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_PKILL_LOG:?}"
EOF
  chmod +x "$d/bin/curl"
  chmod +x "$d/bin/pkill"
  FAKE_NODE_LOG="$d/node.log" FAKE_PKILL_LOG="$d/pkill.log" FAKE_DESKTOP_DIR="$d/project/desktop" FAKE_START_MARKER="$d/start.marker" \
    PATH="$d/bin:$PATH" NODE_BIN="$d/bin/node" NPM_CLI="$d/npm-cli.js" \
    FISH_QUALITY_PROJECT_ROOT="$d/project" FISH_QUALITY_START_LOG="$d/start.log" \
    bash "$LAUNCHER" </dev/null
  [[ ! -e "$d/start.marker" ]]
  [[ "$(wc -l < "$d/node.log")" -ge 2 ]]
  grep -q 'npm-cli.js exec -- electron .' "$d/node.log"
  grep -q 'electron.*fish-quality-desktop' "$d/pkill.log"
}

case_start_missing() {
  local d="$TMP_DIR/missing"
  make_fake_runtime "$d"
  cat >"$d/bin/curl" <<'EOF'
#!/usr/bin/env bash
if [[ -e "${FAKE_START_MARKER:?}" ]]; then exit 0; fi
exit 1
EOF
  chmod +x "$d/bin/curl"
  FAKE_NODE_LOG="$d/node.log" FAKE_DESKTOP_DIR="$d/project/desktop" FAKE_START_MARKER="$d/start.marker" \
    PATH="$d/bin:$PATH" NODE_BIN="$d/bin/node" NPM_CLI="$d/npm-cli.js" \
    FISH_QUALITY_PROJECT_ROOT="$d/project" FISH_QUALITY_START_LOG="$d/start.log" \
    bash "$LAUNCHER" </dev/null
  [[ -e "$d/start.marker" ]]
  grep -q 'npm-cli.js exec -- electron .' "$d/node.log"
}

case_timeout() {
  local d="$TMP_DIR/timeout"
  make_fake_runtime "$d"
  cat >"$d/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "$d/bin/curl"
  if timeout 3s env FAKE_NODE_LOG="$d/node.log" FAKE_DESKTOP_DIR="$d/project/desktop" FAKE_START_MARKER="$d/start.marker" \
    PATH="$d/bin:$PATH" NODE_BIN="$d/bin/node" NPM_CLI="$d/npm-cli.js" \
    FISH_QUALITY_PROJECT_ROOT="$d/project" FISH_QUALITY_START_LOG="$d/start.log" \
    bash "$LAUNCHER" </dev/null; then
    return 1
  fi
}

case_node_path() {
  local d="$TMP_DIR/node-path"
  make_fake_runtime "$d"
  cat >"$d/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$d/bin/curl"
  FAKE_NODE_LOG="$d/node.log" FAKE_DESKTOP_DIR="$d/project/desktop" FAKE_START_MARKER="$d/start.marker" \
    PATH="/usr/bin:$PATH" NODE_BIN="$d/bin/node" NPM_CLI="$d/npm-cli.js" \
    FISH_QUALITY_PROJECT_ROOT="$d/project" FISH_QUALITY_START_LOG="$d/start.log" \
    bash "$LAUNCHER" </dev/null
  grep -q 'npm-cli.js exec -- electron .' "$d/node.log"
}

case_launch_lock() {
  local d="$TMP_DIR/lock"
  make_fake_runtime "$d"
  cat >"$d/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat >"$d/bin/flock" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-n" ]]; then exit "${FAKE_FLOCK_EXIT:-0}"; fi
exit 0
EOF
  chmod +x "$d/bin/curl" "$d/bin/flock"
  FAKE_FLOCK_EXIT=1 FAKE_NODE_LOG="$d/node.log" FAKE_DESKTOP_DIR="$d/project/desktop" \
    PATH="$d/bin:$PATH" NODE_BIN="$d/bin/node" NPM_CLI="$d/npm-cli.js" \
    FISH_QUALITY_PROJECT_ROOT="$d/project" FISH_QUALITY_START_LOG="$d/start.log" \
    bash "$LAUNCHER" </dev/null
  [[ ! -e "$d/node.log" ]]
}

run_case '服务已启动时不重复调用 start_dev.sh' case_already_running
run_case '服务未启动时调用 start_dev.sh 并启动 Electron' case_start_missing
run_case '服务持续不可用时超时失败' case_timeout
run_case 'Node 20 路径优先于系统 Node' case_node_path
run_case '重复双击时只允许一个启动流程' case_launch_lock

printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
