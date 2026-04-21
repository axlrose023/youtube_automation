#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.prod.yml"
BACKEND_ENV_FILE="$ROOT_DIR/deploy/backend.env"
ANDROID_ENV_FILE="$ROOT_DIR/deploy/android.env"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-yta}"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 1
  fi
}

load_env_file() {
  local path="$1"
  if [[ -f "$path" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
      if [[ "$line" != *=* ]]; then
        continue
      fi
      export "${line%%=*}=${line#*=}"
    done < "$path"
  fi

  export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
  export ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
  export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
  export PATH="${ANDROID_SDK_ROOT}/emulator:${ANDROID_SDK_ROOT}/platform-tools:${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${JAVA_HOME}/bin:${PATH}"
}

compose() {
  docker compose \
    --project-name "$COMPOSE_PROJECT_NAME" \
    --env-file "$BACKEND_ENV_FILE" \
    -f "$COMPOSE_FILE" \
    "$@"
}

public_url() {
  if [[ -n "${YTA_PUBLIC_URL:-}" ]]; then
    printf '%s\n' "${YTA_PUBLIC_URL%/}"
    return
  fi

  local host_ip
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -z "$host_ip" ]]; then
    host_ip="127.0.0.1"
  fi
  printf 'http://%s:%s\n' "$host_ip" "${YTA_PUBLIC_HTTP_PORT:-80}"
}

api_ping_url() {
  printf '%s/api/ping\n' "$(public_url)"
}

android_enabled() {
  [[ -f "$ANDROID_ENV_FILE" ]] || return 1
  load_env_file "$ANDROID_ENV_FILE"
  [[ "${YTA_ENABLE_ANDROID:-1}" != "0" ]]
}
