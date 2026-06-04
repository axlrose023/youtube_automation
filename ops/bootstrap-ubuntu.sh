#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
ANDROID_ENV_FILE="$ROOT_DIR/deploy/android.env"
ANDROID_SDK_ROOT_DEFAULT="/opt/android-sdk"
ANDROID_CMDLINE_TOOLS_ZIP="commandlinetools-linux-14742923_latest.zip"
ANDROID_CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/${ANDROID_CMDLINE_TOOLS_ZIP}"
APPIUM_VERSION="2.19.0"
APPIUM_UIAUTOMATOR2_DRIVER_VERSION="4.2.9"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ ! -f "$ANDROID_ENV_FILE" ]]; then
  echo "Missing $ANDROID_ENV_FILE. Create it from deploy/android.env.example first." >&2
  exit 1
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  if [[ "$line" != *=* ]]; then
    continue
  fi
  export "${line%%=*}=${line#*=}"
done < "$ANDROID_ENV_FILE"

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_SDK_ROOT_DEFAULT}"
ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
PATH="/usr/local/bin:/usr/bin:/bin:${ANDROID_SDK_ROOT}/emulator:${ANDROID_SDK_ROOT}/platform-tools:${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${PATH}"
export ANDROID_SDK_ROOT ANDROID_HOME JAVA_HOME PATH

retry_command() {
  local attempts="$1"
  local delay_seconds="$2"
  shift 2

  local attempt=1
  while true; do
    if "$@"; then
      return 0
    fi

    if (( attempt >= attempts )); then
      return 1
    fi

    echo "Command failed (attempt ${attempt}/${attempts}): $*" >&2
    echo "Retrying in ${delay_seconds}s..." >&2
    sleep "$delay_seconds"
    attempt=$((attempt + 1))
  done
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

echo "[1/8] Installing base packages"
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  unzip \
  jq \
  git \
  gnupg \
  lsb-release \
  openjdk-17-jdk-headless \
  nodejs \
  npm \
  ffmpeg \
  fluxbox \
  libglu1-mesa \
  libpulse0 \
  libnss3 \
  libxcomposite1 \
  libxcursor1 \
  libxdamage1 \
  libxi6 \
  libxkbcommon0 \
  libxrandr2 \
  libgbm1 \
  libasound2t64 \
  novnc \
  qemu-kvm \
  tesseract-ocr \
  websockify \
  x11-utils \
  x11vnc \
  xvfb

echo "[2/8] Installing Docker Engine and Compose plugin"
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
fi
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

echo "[3/8] Installing uv and Python 3.13 toolchain"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
ln -sf /root/.local/bin/uv /usr/local/bin/uv
uv python install 3.13

echo "[4/8] Installing Android SDK command-line tools"
install -d -m 0755 "$ANDROID_SDK_ROOT"
install -d -m 0755 "$ANDROID_SDK_ROOT/cmdline-tools"
if [[ ! -x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" ]]; then
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT
  curl -fsSL "$ANDROID_CMDLINE_TOOLS_URL" -o "$tmp_dir/$ANDROID_CMDLINE_TOOLS_ZIP"
  unzip -q "$tmp_dir/$ANDROID_CMDLINE_TOOLS_ZIP" -d "$tmp_dir"
  rm -rf "$ANDROID_SDK_ROOT/cmdline-tools/latest"
  mv "$tmp_dir/cmdline-tools" "$ANDROID_SDK_ROOT/cmdline-tools/latest"
fi

echo "[5/8] Installing Android SDK packages"
yes | sdkmanager --sdk_root="$ANDROID_SDK_ROOT" --licenses >/dev/null || true
retry_command 3 15 sdkmanager --sdk_root="$ANDROID_SDK_ROOT" \
  "platform-tools" \
  "emulator" \
  "build-tools;35.0.0" \
  "platforms;android-35"
retry_command 5 30 sdkmanager --sdk_root="$ANDROID_SDK_ROOT" \
  "${APP__ANDROID_APP__BOOTSTRAP_SYSTEM_IMAGE_PACKAGE:-system-images;android-35;google_apis_playstore;x86_64}"

echo "[6/8] Installing Appium 2 and uiautomator2 driver"
retry_command 3 15 npm install -g "appium@${APPIUM_VERSION}"
if ! appium driver list --installed | grep -q "uiautomator2"; then
  retry_command 3 15 appium driver install --source=npm "appium-uiautomator2-driver@${APPIUM_UIAUTOMATOR2_DRIVER_VERSION}"
fi

echo "[7/8] Preparing backend host virtualenv"
cd "$BACKEND_DIR"
retry_command 3 15 uv sync --frozen --extra android --extra emulation
retry_command 3 15 uv run playwright install --with-deps chromium

echo "[8/8] Creating AVD and installing systemd units"
if [[ ! -d "/root/.android/avd/${APP__ANDROID_APP__BOOTSTRAP_AVD_NAME}.avd" ]]; then
  printf 'no\n' | avdmanager create avd \
    --force \
    --name "${APP__ANDROID_APP__BOOTSTRAP_AVD_NAME}" \
    --package "${APP__ANDROID_APP__BOOTSTRAP_SYSTEM_IMAGE_PACKAGE}" \
    --device "${APP__ANDROID_APP__BOOTSTRAP_DEVICE_PRESET:-pixel_7}"
fi
upsert_properties_value \
  "/root/.android/avd/${APP__ANDROID_APP__BOOTSTRAP_AVD_NAME}.avd/config.ini" \
  "hw.keyboard" \
  "yes"

install -d -m 0755 /opt/youtube_automation/artifacts
install -m 0644 "$ROOT_DIR/deploy/systemd/yta-appium.service" /etc/systemd/system/yta-appium.service
install -m 0644 "$ROOT_DIR/deploy/systemd/yta-android-worker.service" /etc/systemd/system/yta-android-worker.service
install -m 0644 "$ROOT_DIR/deploy/systemd/yta-android.target" /etc/systemd/system/yta-android.target
systemctl daemon-reload
systemctl enable yta-android.target yta-appium.service yta-android-worker.service

if [[ ! -e /dev/kvm ]]; then
  echo "Warning: /dev/kvm is unavailable on this host. Android emulator will run, if at all, only in degraded software mode."
fi

echo "Bootstrap completed."
