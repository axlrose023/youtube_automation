#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_file "$BACKEND_ENV_FILE"

TAIL_LINES="${TAIL_LINES:-200}"

echo "== Docker Compose Logs =="
compose logs --tail "$TAIL_LINES" "${@}"

if android_enabled; then
  echo
  echo "== Appium Journal =="
  journalctl -u yta-appium.service -n "$TAIL_LINES" --no-pager || true

  echo
  echo "== Android Worker Journal =="
  journalctl -u yta-android-worker.service -n "$TAIL_LINES" --no-pager || true

  state_dir="${YTA_ANDROID_BOOTSTRAP_STATE_DIR:-/tmp/yta-android-bootstrap}"
  if [[ -d "$state_dir" ]]; then
    echo
    echo "== Android Manual UI Logs =="
    for name in xvfb fluxbox x11vnc websockify emulator; do
      log_file="$state_dir/${name}.log"
      if [[ -f "$log_file" ]]; then
        echo
        echo "-- $name --"
        tail -n "$TAIL_LINES" "$log_file" || true
      fi
    done
  fi
fi
