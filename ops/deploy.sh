#!/usr/bin/env bash
# Usage: ./ops/deploy.sh [branch]
# Pulls latest code from GitHub, syncs deps, rebuilds Docker stack,
# restarts Android worker. Run on the server as root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_common.sh"

BRANCH="${1:-}"

# ── 1. Git pull ───────────────────────────────────────────────────────────────
echo "==> git pull"
cd "$ROOT_DIR"
git fetch origin
if [[ -n "$BRANCH" ]]; then
  git checkout "$BRANCH"
fi
git pull origin "$(git rev-parse --abbrev-ref HEAD)"
echo "    commit: $(git rev-parse HEAD)"

# ── 2. Python deps ────────────────────────────────────────────────────────────
if android_enabled; then
  echo "==> uv sync (android + emulation)"
  (cd "$ROOT_DIR/backend" && uv sync --frozen --extra android --extra emulation)
else
  echo "==> uv sync"
  (cd "$ROOT_DIR/backend" && uv sync --frozen --extra emulation)
fi

# ── 3. Docker stack ───────────────────────────────────────────────────────────
require_file "$BACKEND_ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

echo "==> docker compose rebuild + restart"
compose up -d --build

# ── 4. Android worker ─────────────────────────────────────────────────────────
if android_enabled && systemctl list-units --type=service | grep -q yta-android-worker; then
  echo "==> restart yta-android-worker"
  systemctl restart yta-android-worker
  sleep 2
  systemctl is-active yta-android-worker && echo "    worker: active" || echo "    worker: FAILED" >&2
fi

# ── 5. Health check ───────────────────────────────────────────────────────────
echo "==> waiting for API..."
for _ in $(seq 1 30); do
  if curl -fsS "$(api_ping_url)" >/dev/null 2>&1; then
    echo "==> deploy done. $(public_url)  [$(git rev-parse --short HEAD)]"
    exit 0
  fi
  sleep 2
done

echo "API did not become healthy in time." >&2
exit 1
