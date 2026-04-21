#!/usr/bin/env bash

export JAVA_HOME="/opt/homebrew/opt/openjdk"
export ANDROID_SDK_ROOT="/opt/homebrew/share/android-commandlinetools"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export ADB_VENDOR_KEYS="${ADB_VENDOR_KEYS:-$HOME/.android}"
ANDROID_BUILD_TOOLS_DIR="$(find "$ANDROID_SDK_ROOT/build-tools" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -n 1 || true)"
if [ -n "$ANDROID_BUILD_TOOLS_DIR" ]; then
  export PATH="/opt/homebrew/opt/openjdk/bin:/opt/homebrew/share/android-commandlinetools/emulator:/opt/homebrew/share/android-commandlinetools/cmdline-tools/latest/bin:/opt/homebrew/share/android-commandlinetools/platform-tools:$ANDROID_BUILD_TOOLS_DIR:$PATH"
else
  export PATH="/opt/homebrew/opt/openjdk/bin:/opt/homebrew/share/android-commandlinetools/emulator:/opt/homebrew/share/android-commandlinetools/cmdline-tools/latest/bin:/opt/homebrew/share/android-commandlinetools/platform-tools:$PATH"
fi
