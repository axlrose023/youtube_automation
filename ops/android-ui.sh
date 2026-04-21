#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ACTION="${1:-status}"
BOOTSTRAP_SCRIPT="$ROOT_DIR/ops/android-bootstrap-ui.sh"
RESTART_RUNTIME_ON_STOP="${YTA_ANDROID_UI_RESTART_RUNTIME_ON_STOP:-1}"

require_file "$ANDROID_ENV_FILE"
load_env_file "$ANDROID_ENV_FILE"
require_file "$BOOTSTRAP_SCRIPT"

show_runtime_status() {
  echo "Android runtime target:"
  systemctl is-active yta-android.target 2>/dev/null || true
  echo
  "$BOOTSTRAP_SCRIPT" status
}

start_manual_ui() {
  echo "Stopping Android runtime target to free the emulator"
  systemctl stop yta-android.target || true
  echo
  echo "Starting manual Android UI"
  "$BOOTSTRAP_SCRIPT" start
}

stop_manual_ui() {
  echo "Stopping manual Android UI"
  "$BOOTSTRAP_SCRIPT" stop
  if [[ "$RESTART_RUNTIME_ON_STOP" != "0" ]]; then
    echo
    echo "Starting Android runtime target"
    systemctl start yta-android.target
  fi
}

case "$ACTION" in
  start)
    start_manual_ui
    ;;
  stop)
    stop_manual_ui
    ;;
  restart-runtime)
    echo "Starting Android runtime target"
    systemctl start yta-android.target
    ;;
  save-snapshot)
    "$BOOTSTRAP_SCRIPT" save-snapshot
    ;;
  status)
    show_runtime_status
    ;;
  *)
    echo "Usage: $0 {start|stop|save-snapshot|status|restart-runtime}" >&2
    exit 1
    ;;
esac
