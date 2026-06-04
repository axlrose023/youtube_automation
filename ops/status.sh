#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_file "$BACKEND_ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

echo "== Docker Compose =="
compose ps || true

echo
echo "== API Health =="
if curl -fsS "$(api_ping_url)" >/dev/null 2>&1; then
  echo "OK $(api_ping_url)"
else
  echo "FAILED $(api_ping_url)"
fi

echo
echo "== Public URL =="
echo "$(public_url)"

echo
echo "== Android =="
if android_enabled; then
  systemctl --no-pager --full status yta-appium.service || true
  systemctl --no-pager --full status yta-android-worker.service || true
  echo
  echo "== Android Manual UI =="
  "$ROOT_DIR/ops/android-ui.sh" status || true
  if command -v adb >/dev/null 2>&1; then
    adb devices || true
  else
    echo "adb is not installed"
  fi
  if [[ -e /dev/kvm ]]; then
    echo "/dev/kvm present"
  else
    echo "/dev/kvm missing"
  fi
else
  echo "Android runtime is disabled or deploy/android.env is missing."
fi
