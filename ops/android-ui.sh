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

NOVNC_PORT="${YTA_ANDROID_BOOTSTRAP_NOVNC_PORT:-6080}"

open_novnc_port() {
  if command -v ufw >/dev/null 2>&1; then
    ufw allow "${NOVNC_PORT}/tcp" >/dev/null 2>&1 || true
    echo "Firewall: opened port ${NOVNC_PORT}"
  fi
}

close_novnc_port() {
  if command -v ufw >/dev/null 2>&1; then
    ufw delete allow "${NOVNC_PORT}/tcp" >/dev/null 2>&1 || true
    echo "Firewall: closed port ${NOVNC_PORT}"
  fi
}

start_manual_ui() {
  echo "Stopping Android runtime target to free the emulator"
  systemctl stop yta-android.target || true
  echo
  open_novnc_port
  echo "Starting manual Android UI"
  "$BOOTSTRAP_SCRIPT" start
}

stop_manual_ui() {
  echo "Stopping manual Android UI"
  "$BOOTSTRAP_SCRIPT" stop
  close_novnc_port
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
