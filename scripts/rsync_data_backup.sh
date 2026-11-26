#!/usr/bin/env bash

set -Eeuo pipefail

SOURCE_DIR="${1:?source directory is required}"
TARGET_DIR="${2:?target directory is required}"
LOG_FILE="${3:?log file is required}"

mkdir -p "$TARGET_DIR" "$(dirname -- "$LOG_FILE")"

exec /usr/bin/rsync -avPh \
  --exclude='.cache/' \
  --exclude='uploads/videos/segments/' \
  --exclude='rsync.log' \
  -- "$SOURCE_DIR/" "$TARGET_DIR/" >>"$LOG_FILE" 2>&1
