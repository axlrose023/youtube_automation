#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_file "$BACKEND_ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

echo "Stopping compose stack"
compose down

if android_enabled; then
  echo "Stopping Android target"
  systemctl stop yta-android.target || true
fi

echo "Remaining compose services"
compose ps || true
