from app.services.mobile_app.android.errors import is_dead_appium_session_error


def test_is_dead_appium_session_error_matches_uiautomator_proxy_failure() -> None:
    assert is_dead_appium_session_error(
        "POST /elements cannot be proxied to UiAutomator2 server because the instrumentation process is not running"
    )


def test_is_dead_appium_session_error_ignores_regular_ui_miss() -> None:
    assert not is_dead_appium_session_error("Failed to detect native YouTube results list")
