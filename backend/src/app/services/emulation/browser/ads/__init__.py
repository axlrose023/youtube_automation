"""Ad helpers for desktop browser automation."""

__all__ = [
    "AdCaptureProvider",
    "AdHandler",
]


def __getattr__(name: str):
    if name == "AdCaptureProvider":
        from .capture import AdCaptureProvider

        return AdCaptureProvider
    if name == "AdHandler":
        from .handler import AdHandler

        return AdHandler
    raise AttributeError(name)
