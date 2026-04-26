"""Run an Android YouTube session locally without taskiq/postgres."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s][%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

TOPICS = [
    "quantum ai trading bot",
    "best forex signals provider",
    "prop firm funded account",
]
DURATION_MINUTES = 20
AVD_NAME = "yt_android_playstore_api35_clean"
ARTIFACTS_PATH = Path(__file__).parent.parent / "artifacts"

os.environ["APP__ENV"] = "prod"
os.environ["APP__STORAGE__BASE_PATH"] = str(ARTIFACTS_PATH)
os.environ["APP__ANDROID_APP__ENABLED"] = "true"
os.environ["APP__ANDROID_APP__MANAGE_APPIUM_SERVER"] = "false"
os.environ["APP__ANDROID_APP__APPIUM_SERVER_URL"] = "http://127.0.0.1:4723"
os.environ["APP__ANDROID_APP__DEFAULT_AVD_NAME"] = AVD_NAME
os.environ.setdefault("APP__ANDROID_APP__EMULATOR_GPU_MODE", "auto")
os.environ.setdefault("APP__ANDROID_APP__EMULATOR_ACCEL_MODE", "auto")
os.environ.setdefault("APP__ANDROID_APP__EMULATOR_USE_SNAPSHOTS", "false")
os.environ.setdefault("APP__ANDROID_APP__EMULATOR_HEADLESS", "false")
os.environ.setdefault("APP__ANDROID_APP__PROBE_SCREENRECORD_ENABLED", "true")
# dummy DB — not used by runner directly
os.environ.setdefault("APP__POSTGRES__HOST", "127.0.0.1")
os.environ.setdefault("APP__POSTGRES__PORT", "5432")
os.environ.setdefault("APP__POSTGRES__DB", "app")
os.environ.setdefault("APP__POSTGRES__USER", "postgres")
os.environ.setdefault("APP__POSTGRES__PASSWORD", "postgres")
os.environ.setdefault("APP__REDIS__HOST", "127.0.0.1")
os.environ.setdefault("APP__REDIS__PORT", "6379")
os.environ.setdefault("APP__REDIS__DB", "0")
# Use local Android SDK
ANDROID_SDK = os.environ.get("ANDROID_SDK_ROOT", os.path.expanduser("~/.android/sdk"))
for sdk_path in [
    "/opt/homebrew/share/android-commandlinetools",
    os.path.expanduser("~/Library/Android/sdk"),
    "/opt/android-sdk",
]:
    if os.path.isdir(sdk_path):
        ANDROID_SDK = sdk_path
        break
os.environ["ANDROID_SDK_ROOT"] = ANDROID_SDK
os.environ["ANDROID_HOME"] = ANDROID_SDK

EMULATOR_CANDIDATES = [
    os.path.join(ANDROID_SDK, "emulator", "emulator"),
    "/opt/homebrew/share/android-commandlinetools/emulator/emulator",
]
for emu in EMULATOR_CANDIDATES:
    if os.path.isfile(emu):
        os.environ.setdefault("YTA_EMULATOR_BIN", emu)
        break

print(f"ANDROID_SDK_ROOT={ANDROID_SDK}", flush=True)
print(f"AVD={AVD_NAME}", flush=True)
print(f"Topics={TOPICS}", flush=True)
print(f"Duration={DURATION_MINUTES}m", flush=True)
print(f"Artifacts={ARTIFACTS_PATH}", flush=True)


async def main() -> None:
    from app.settings import Config
    from app.services.mobile_app.android.runner import AndroidYouTubeSessionRunner

    config = Config()
    print(f"Config AVD: {config.android_app.default_avd_name}", flush=True)
    print(f"Config storage: {config.storage.base_path}", flush=True)

    runner = AndroidYouTubeSessionRunner(config)

    print("\n=== Starting session ===\n", flush=True)

    async def on_progress(**kwargs):
        event = kwargs.get("event")
        topic = kwargs.get("current_topic")
        watch = kwargs.get("current_watch") or {}
        if event == "video_opened":
            print(f"[progress] opened topic={topic} title={watch.get('title','?')}", flush=True)
        elif event == "watch_update":
            ws = watch.get("watched_seconds", 0)
            ts = watch.get("target_seconds", 0)
            print(f"[progress] watching topic={topic} {ws:.0f}s/{ts:.0f}s", flush=True)

    result = await runner.run(
        topics=TOPICS,
        duration_minutes=DURATION_MINUTES,
        avd_name=AVD_NAME,
        on_progress=on_progress,
    )

    print("\n=== Session done ===\n", flush=True)
    print(f"Topics run: {len(result.topic_results)}", flush=True)
    total_watch = 0.0
    total_ads = 0
    for tr in result.topic_results:
        ws = getattr(tr, "watch_seconds", 0) or 0
        ads = len(getattr(tr, "watched_ads", []) or [])
        verified = getattr(tr, "watch_verified", False)
        total_watch += ws
        total_ads += ads
        print(f"  topic={tr.topic} watch={ws:.1f}s verified={verified} ads={ads}", flush=True)

    print(f"\nTotal watched: {total_watch:.1f}s ({total_watch/60:.1f}m)", flush=True)
    print(f"Total ads: {total_ads}", flush=True)

    # Save result
    out_path = ARTIFACTS_PATH / "local_session_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.dumps(result.__dict__ if hasattr(result, '__dict__') else str(result), default=str, ensure_ascii=False, indent=2)
        out_path.write_text(raw)
        print(f"\nFull result saved to {out_path}", flush=True)
    except Exception as e:
        print(f"Could not save result: {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
