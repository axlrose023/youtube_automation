from __future__ import annotations

from dataclasses import dataclass

from app.settings import AndroidAppConfig

from .appium_provider import AppiumSessionProvider
from .avd_manager import AndroidAvdManager


@dataclass(frozen=True)
class AndroidProbeRuntime:
    config: AndroidAppConfig
    avd_manager: AndroidAvdManager
    appium_provider: AppiumSessionProvider


def build_android_probe_runtime(config: AndroidAppConfig) -> AndroidProbeRuntime:
    return AndroidProbeRuntime(
        config=config,
        avd_manager=AndroidAvdManager(
            emulator_start_timeout_seconds=config.emulator_start_timeout_seconds,
            device_ready_timeout_seconds=config.device_ready_timeout_seconds,
        ),
        appium_provider=AppiumSessionProvider(config=config),
    )
