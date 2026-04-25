#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_file "$ANDROID_ENV_FILE"
load_env_file "$ANDROID_ENV_FILE"

ACTION="${1:-start}"
STATE_DIR="${YTA_ANDROID_BOOTSTRAP_STATE_DIR:-/tmp/yta-android-bootstrap}"
DISPLAY_NUM="${YTA_ANDROID_BOOTSTRAP_DISPLAY_NUM:-99}"
DISPLAY=":${DISPLAY_NUM}"
XVFB_LOCK="/tmp/.X${DISPLAY_NUM}-lock"
VNC_PORT="${YTA_ANDROID_BOOTSTRAP_VNC_PORT:-5900}"
NOVNC_PORT="${YTA_ANDROID_BOOTSTRAP_NOVNC_PORT:-6080}"
NOVNC_HOST="${YTA_ANDROID_BOOTSTRAP_NOVNC_HOST:-0.0.0.0}"
NOVNC_WEB_DIR="${YTA_ANDROID_BOOTSTRAP_NOVNC_WEB_DIR:-/usr/share/novnc}"
VNC_PASSWORD="${YTA_ANDROID_BOOTSTRAP_VNC_PASSWORD:-}"
VNC_PASSWORD_FILE="$STATE_DIR/x11vnc.passwd"
BOOTSTRAP_AVD_NAME="${APP__ANDROID_APP__BOOTSTRAP_AVD_NAME:-${APP__ANDROID_APP__DEFAULT_AVD_NAME:-}}"
GPU_MODE="${APP__ANDROID_APP__BOOTSTRAP_EMULATOR_GPU_MODE:-swiftshader_indirect}"
ACCEL_MODE="${APP__ANDROID_APP__BOOTSTRAP_EMULATOR_ACCEL_MODE:-auto}"
SNAPSHOT_NAME="${APP__ANDROID_APP__RUNTIME_SNAPSHOT_NAME:-youtube_warm_updated}"
START_EMULATOR="${YTA_ANDROID_BOOTSTRAP_START_EMULATOR:-1}"

mkdir -p "$STATE_DIR"

ensure_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

avd_config_path() {
  printf '/root/.android/avd/%s.avd/config.ini\n' "$1"
}

upsert_properties_value() {
  local file_path="$1"
  local key="$2"
  local value="$3"

  if [[ ! -f "$file_path" ]]; then
    return 1
  fi

  if grep -q "^${key}=" "$file_path"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file_path"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file_path"
  fi
}

spawn_detached() {
  local pid_file="$1"
  local log_file="$2"
  shift 2
  setsid "$@" >"$log_file" 2>&1 < /dev/null &
  echo $! >"$pid_file"
}

require_pid_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

kill_if_running() {
  local pid_file="$1"
  if require_pid_running "$pid_file"; then
    local pid
    pid="$(cat "$pid_file")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 15); do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}

get_serial() {
  adb devices | awk '$2 == "device" && $1 ~ /^emulator-/ {print $1; exit}'
}

wait_for_boot() {
  local serial=""
  for _ in $(seq 1 180); do
    serial="$(get_serial)"
    if [[ -n "$serial" ]]; then
      local boot_completed
      boot_completed="$(adb -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
      if [[ "$boot_completed" == "1" ]]; then
        printf '%s\n' "$serial"
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

public_host() {
  if [[ -n "${YTA_PUBLIC_URL:-}" ]]; then
    printf '%s\n' "${YTA_PUBLIC_URL%/}"
    return
  fi
  local host_ip
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf 'http://%s\n' "${host_ip:-127.0.0.1}"
}

ensure_vnc_password_file() {
  if [[ -z "$VNC_PASSWORD" ]]; then
    rm -f "$VNC_PASSWORD_FILE"
    return 0
  fi
  x11vnc -storepasswd "$VNC_PASSWORD" "$VNC_PASSWORD_FILE" >/dev/null
  chmod 600 "$VNC_PASSWORD_FILE"
}

start_stack() {
  ensure_command Xvfb
  ensure_command fluxbox
  ensure_command x11vnc
  ensure_command websockify
  ensure_command xdpyinfo
  ensure_command adb
  if [[ "$START_EMULATOR" != "0" ]]; then
    ensure_command emulator
  fi

  if [[ "$START_EMULATOR" != "0" && -z "$BOOTSTRAP_AVD_NAME" ]]; then
    echo "Android bootstrap AVD name is not configured." >&2
    exit 1
  fi

  if [[ "$START_EMULATOR" != "0" ]]; then
    upsert_properties_value "$(avd_config_path "$BOOTSTRAP_AVD_NAME")" "hw.keyboard" "yes"
  fi
  ensure_vnc_password_file

  export DISPLAY
  rm -f "$XVFB_LOCK" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true

  if ! require_pid_running "$STATE_DIR/xvfb.pid"; then
    spawn_detached \
      "$STATE_DIR/xvfb.pid" \
      "$STATE_DIR/xvfb.log" \
      Xvfb "$DISPLAY" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset
  fi

  for _ in $(seq 1 30); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "Xvfb did not become ready." >&2
    exit 1
  fi

  if ! require_pid_running "$STATE_DIR/fluxbox.pid"; then
    spawn_detached "$STATE_DIR/fluxbox.pid" "$STATE_DIR/fluxbox.log" fluxbox
  fi

  if ! require_pid_running "$STATE_DIR/x11vnc.pid"; then
    if [[ -n "$VNC_PASSWORD" ]]; then
      spawn_detached \
        "$STATE_DIR/x11vnc.pid" \
        "$STATE_DIR/x11vnc.log" \
        x11vnc -display "$DISPLAY" -forever -shared -rfbport "$VNC_PORT" -rfbauth "$VNC_PASSWORD_FILE"
    else
      spawn_detached \
        "$STATE_DIR/x11vnc.pid" \
        "$STATE_DIR/x11vnc.log" \
        x11vnc -display "$DISPLAY" -forever -shared -rfbport "$VNC_PORT" -nopw
    fi
  fi

  if ! require_pid_running "$STATE_DIR/websockify.pid"; then
    spawn_detached \
      "$STATE_DIR/websockify.pid" \
      "$STATE_DIR/websockify.log" \
      websockify --web "$NOVNC_WEB_DIR" "${NOVNC_HOST}:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}"
  fi

  adb start-server >/dev/null 2>&1 || true

  if [[ "$START_EMULATOR" != "0" ]]; then
    if ! require_pid_running "$STATE_DIR/emulator.pid"; then
      spawn_detached \
        "$STATE_DIR/emulator.pid" \
        "$STATE_DIR/emulator.log" \
        emulator \
        -avd "$BOOTSTRAP_AVD_NAME" \
        -gpu "$GPU_MODE" \
        -accel "$ACCEL_MODE" \
        -no-boot-anim
    fi

    local serial=""
    if serial="$(wait_for_boot)"; then
      echo "Android emulator booted: $serial"
    else
      echo "Android emulator start initiated, but boot completion timed out." >&2
    fi
  else
    echo "Android display stack started without booting an emulator."
  fi

  local base_url
  base_url="$(public_host)"
  echo "noVNC: ${base_url}:${NOVNC_PORT}/vnc.html"
  if [[ -n "$VNC_PASSWORD" ]]; then
    echo "VNC password: configured in YTA_ANDROID_BOOTSTRAP_VNC_PASSWORD"
  else
    echo "VNC password: disabled"
  fi
  echo "State dir: $STATE_DIR"
  if [[ "$START_EMULATOR" != "0" ]]; then
    echo "After manual Google login + YouTube update run:"
    echo "  $ROOT_DIR/ops/android-bootstrap-ui.sh save-snapshot"
  fi
}

save_snapshot() {
  ensure_command adb
  local serial
  serial="$(get_serial)"
  if [[ -z "$serial" ]]; then
    echo "No running Android emulator device found." >&2
    exit 1
  fi
  adb -s "$serial" emu avd snapshot save "$SNAPSHOT_NAME"
  echo "Snapshot saved: $SNAPSHOT_NAME"
}

show_status() {
  echo "Display: $DISPLAY"
  echo "State dir: $STATE_DIR"
  for name in xvfb fluxbox x11vnc websockify emulator; do
    local pid_file="$STATE_DIR/${name}.pid"
    if require_pid_running "$pid_file"; then
      echo "$name: running (pid $(cat "$pid_file"))"
    else
      echo "$name: stopped"
    fi
  done
  local serial
  serial="$(get_serial)"
  if [[ -n "$serial" ]]; then
    echo "adb serial: $serial"
  fi
  local base_url
  base_url="$(public_host)"
  echo "noVNC: ${base_url}:${NOVNC_PORT}/vnc.html"
  if [[ -n "$VNC_PASSWORD" ]]; then
    echo "VNC password: configured in YTA_ANDROID_BOOTSTRAP_VNC_PASSWORD"
  else
    echo "VNC password: disabled"
  fi
}

stop_stack() {
  ensure_command adb
  local serial
  serial="$(get_serial)"
  if [[ -n "$serial" ]]; then
    adb -s "$serial" emu kill >/dev/null 2>&1 || true
  fi
  kill_if_running "$STATE_DIR/emulator.pid"
  kill_if_running "$STATE_DIR/websockify.pid"
  kill_if_running "$STATE_DIR/x11vnc.pid"
  kill_if_running "$STATE_DIR/fluxbox.pid"
  kill_if_running "$STATE_DIR/xvfb.pid"
  rm -f "$VNC_PASSWORD_FILE"
  rm -f "$XVFB_LOCK" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
  echo "Android bootstrap UI stopped."
}

case "$ACTION" in
  start)
    start_stack
    ;;
  save-snapshot)
    save_snapshot
    ;;
  status)
    show_status
    ;;
  stop)
    stop_stack
    ;;
  *)
    echo "Usage: $0 {start|save-snapshot|status|stop}" >&2
    exit 1
    ;;
esac
