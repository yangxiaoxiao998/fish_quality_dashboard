#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
FRONTEND_URL="${FISH_QUALITY_URL:-http://127.0.0.1:5173}"
ELECTRON_BIN="$PROJECT_ROOT/desktop/node_modules/.bin/electron"

for _ in $(seq 1 120); do
    if curl --noproxy '*' --silent --fail --max-time 1 "$FRONTEND_URL" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if [[ ! -x "$ELECTRON_BIN" ]]; then
    exit 1
fi

export FISH_QUALITY_URL="$FRONTEND_URL"
exec "$ELECTRON_BIN" "$PROJECT_ROOT/desktop"
