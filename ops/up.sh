#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_file "$BACKEND_ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is missing. Run ./ops/bootstrap-ubuntu.sh first." >&2
  exit 1
fi

if android_enabled; then
  echo "Syncing Python venv with android extras"
  (cd "$ROOT_DIR/backend" && uv sync --frozen --extra android --extra emulation)

  echo "Starting Android target"
  systemctl start yta-android.target

  echo "Waiting for Appium"
  appium_ready=0
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:4723/status" >/dev/null 2>&1; then
      appium_ready=1
      break
    fi
    sleep 2
  done
  if [[ "$appium_ready" -ne 1 ]]; then
    echo "Appium did not become healthy in time." >&2
    exit 1
  fi
fi

echo "Starting compose stack"
compose up -d --build

echo "Waiting for API through gateway"
for _ in $(seq 1 90); do
    if curl -fsS "$(api_ping_url)" >/dev/null 2>&1; then
      echo "Stack is ready at $(public_url)"
      exit 0
    fi
    sleep 2
done

echo "API did not become healthy in time." >&2
exit 1
