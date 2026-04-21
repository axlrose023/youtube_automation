#!/usr/bin/env bash

set -euo pipefail

echo "== Android Runtime Preflight =="
echo

check_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    echo "[OK] $name: $(command -v "$name")"
    return 0
  fi
  echo "[MISSING] $name"
  return 1
}

check_env() {
  local name="$1"
  local value="${!name:-}"
  if [ -n "$value" ]; then
    echo "[OK] $name=$value"
    return 0
  fi
  echo "[MISSING] $name"
  return 1
}

FAILED=0

check_cmd java || FAILED=1
check_cmd adb || FAILED=1
check_cmd emulator || FAILED=1
check_cmd sdkmanager || FAILED=1
check_cmd avdmanager || FAILED=1
check_cmd appium || FAILED=1

echo
check_env JAVA_HOME || FAILED=1
check_env ANDROID_HOME || FAILED=1
check_env ANDROID_SDK_ROOT || FAILED=1

if [ -n "${ANDROID_SDK_ROOT:-}" ]; then
  if [ -e "$ANDROID_SDK_ROOT/platform-tools/adb" ]; then
    echo "[OK] SDK platform-tools: $ANDROID_SDK_ROOT/platform-tools/adb"
  else
    echo "[MISSING] SDK platform-tools under ANDROID_SDK_ROOT"
    FAILED=1
  fi
  if find "$ANDROID_SDK_ROOT/build-tools" -maxdepth 2 -name aapt2 2>/dev/null | grep -q .; then
    echo "[OK] SDK build-tools aapt2 present"
  else
    echo "[MISSING] SDK build-tools aapt2 under ANDROID_SDK_ROOT"
    FAILED=1
  fi
fi

echo
if command -v adb >/dev/null 2>&1; then
  echo "== adb devices =="
  adb devices || true
  echo
fi

if [ "$FAILED" -ne 0 ]; then
  echo "Preflight result: FAILED"
  exit 1
fi

echo "Preflight result: OK"
