from __future__ import annotations

from appium.webdriver.appium_connection import AppiumConnection
from selenium.webdriver.remote.client_config import ClientConfig

from app.services.mobile_app.android.appium_provider import AppiumSessionProvider


def test_requires_adb_recovery_skips_uiautomation_crashes() -> None:
    exc = RuntimeError(
        "SessionNotCreatedException: java.lang.IllegalStateException: UiAutomation not connected"
    )

    assert AppiumSessionProvider._requires_adb_recovery(exc) is False


def test_requires_adb_recovery_keeps_offline_device_recovery() -> None:
    exc = RuntimeError(
        "Failed to create Appium session: Error executing adbExec. Original error: adb: device offline"
    )

    assert AppiumSessionProvider._requires_adb_recovery(exc) is True


def test_skip_device_initialization_only_on_first_attempt() -> None:
    assert (
        AppiumSessionProvider._should_skip_device_initialization(
            configured_skip_device_initialization=True,
            attempt=0,
        )
        is True
    )
    assert (
        AppiumSessionProvider._should_skip_device_initialization(
            configured_skip_device_initialization=True,
            attempt=1,
        )
        is False
    )
    assert (
        AppiumSessionProvider._should_skip_device_initialization(
            configured_skip_device_initialization=False,
            attempt=0,
        )
        is False
    )


def test_detects_missing_android_sdk_env_error() -> None:
    exc = RuntimeError(
        "Neither ANDROID_HOME nor ANDROID_SDK_ROOT environment variable was exported."
    )

    assert AppiumSessionProvider._is_sdk_env_error(exc) is True


def test_ignores_non_sdk_env_errors() -> None:
    exc = RuntimeError("adb: device offline")

    assert AppiumSessionProvider._is_sdk_env_error(exc) is False


def test_pool_manager_client_config_enables_wider_appium_connection_pool() -> None:
    client_config = ClientConfig(
        remote_server_addr="http://127.0.0.1:4723",
        timeout=10,
        init_args_for_pool_manager=AppiumSessionProvider._build_pool_manager_client_config(),
    )
    conn = AppiumConnection(client_config=client_config)

    assert conn._conn.connection_pool_kw["maxsize"] == 8
    assert conn._conn.connection_pool_kw["block"] is False
