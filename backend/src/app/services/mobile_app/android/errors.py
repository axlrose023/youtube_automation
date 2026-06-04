class AndroidRuntimeError(RuntimeError):
    """Base class for Android runtime failures."""


class AndroidToolingError(AndroidRuntimeError):
    """Raised when required host tooling is missing or unusable."""


class AndroidDeviceStartError(AndroidRuntimeError):
    """Raised when AVD startup or device boot does not complete."""


class AndroidAppiumError(AndroidRuntimeError):
    """Raised when Appium server or session startup fails."""


class AndroidUiError(AndroidRuntimeError):
    """Raised when YouTube app UI interaction fails."""


def is_dead_appium_session_error(message: str | BaseException | None) -> bool:
    lowered = str(message or "").casefold()
    return any(
        token in lowered
        for token in (
            "instrumentation process is not running",
            "instrumentation process cannot be initialized",
            "invalidsessionidexception",
            "invalid session id",
            "the session identified by",
            "cannot be proxied to uiautomator2",
            "uiautomator2 server",
            "could not proxy command to the remote server",
            "socket hang up",
            "read timed out",
            "readtimeouterror",
            "httpconnectionpool(host='127.0.0.1', port=4723)",
            "new command timeout",
        )
    )
