#!/usr/bin/env bash

set -euo pipefail

ANDROID_SDK_ROOT_DEFAULT="/opt/homebrew/share/android-commandlinetools"
ANDROID_CMDLINE_BIN_DEFAULT="/opt/homebrew/share/android-commandlinetools/cmdline-tools/latest/bin"
ANDROID_PLATFORM_TOOLS_BIN_DEFAULT="$ANDROID_SDK_ROOT_DEFAULT/platform-tools"
ANDROID_EMULATOR_BIN_DEFAULT="/opt/homebrew/share/android-commandlinetools/emulator"
ANDROID_PLATFORM_TOOLS_CASK_ROOT="/opt/homebrew/Caskroom/android-platform-tools"

echo "== Android bootstrap for macOS =="
echo

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required."
  exit 1
fi

echo "Installing host prerequisites..."
HOMEBREW_NO_AUTO_UPDATE=1 brew install openjdk || true
HOMEBREW_NO_AUTO_UPDATE=1 brew install appium || true
brew install --cask android-commandlinetools android-platform-tools || true

export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_SDK_ROOT_DEFAULT}"
export ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
export PATH="/opt/homebrew/opt/openjdk/bin:$ANDROID_EMULATOR_BIN_DEFAULT:$ANDROID_CMDLINE_BIN_DEFAULT:$ANDROID_PLATFORM_TOOLS_BIN_DEFAULT:$PATH"

if [ ! -d "$ANDROID_SDK_ROOT/platform-tools" ] && [ -d "$ANDROID_PLATFORM_TOOLS_CASK_ROOT" ]; then
  latest_platform_tools="$(find "$ANDROID_PLATFORM_TOOLS_CASK_ROOT" -maxdepth 2 -type d -name platform-tools | sort | tail -1)"
  if [ -n "${latest_platform_tools:-}" ]; then
    ln -sfn "$latest_platform_tools" "$ANDROID_SDK_ROOT/platform-tools"
  fi
fi

echo
echo "Using:"
echo "  ANDROID_SDK_ROOT=$ANDROID_SDK_ROOT"
echo "  ANDROID_HOME=$ANDROID_HOME"
echo

if command -v java >/dev/null 2>&1; then
  java -version || true
fi

if command -v sdkmanager >/dev/null 2>&1; then
  yes | sdkmanager --licenses || true
  sdkmanager --sdk_root="$ANDROID_SDK_ROOT" \
    "platform-tools" \
    "emulator" \
    "platforms;android-35" \
    "system-images;android-35;google_apis_playstore;arm64-v8a"
fi

cat <<'EOF'

Next manual step:
  avdmanager create avd \
    --name yt_android_playstore_api35 \
    --package "system-images;android-35;google_apis_playstore;arm64-v8a" \
    --device "pixel_7"

Then run the bootstrap flow:
  cd backend
  uv run python -m cli.cli android_bootstrap_warm_snapshot

Suggested env exports for shell profile:
  export ANDROID_SDK_ROOT=/opt/homebrew/share/android-commandlinetools
  export ANDROID_HOME=$ANDROID_SDK_ROOT
  export PATH="/opt/homebrew/opt/openjdk/bin:/opt/homebrew/share/android-commandlinetools/emulator:/opt/homebrew/share/android-commandlinetools/cmdline-tools/latest/bin:/opt/homebrew/share/android-commandlinetools/platform-tools:$PATH"

Appium:
  appium driver install uiautomator2
  appium

Operational note:
  The bootstrap AVD must be a Play Store image.
  The stock YouTube app on non-Play-Store images forces a mandatory update screen.
  For real probes, create a warm snapshot with an updated YouTube app first.

EOF
