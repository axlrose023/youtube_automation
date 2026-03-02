from .core.session_store import EmulationSessionStore
from .core.state import EmulationResult

__all__ = [
    "EmulationResult",
    "EmulationSessionStore",
    "YouTubeEmulator",
]


def __getattr__(name: str):
    if name == "YouTubeEmulator":
        from .emulator import YouTubeEmulator

        return YouTubeEmulator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
