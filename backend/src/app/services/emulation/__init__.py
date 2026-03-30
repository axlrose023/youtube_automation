__all__ = [
    "EmulationResult",
    "EmulationOrchestrationService",
    "EmulationPersistenceService",
    "EmulationSessionStore",
    "YouTubeEmulator",
]


def __getattr__(name: str):
    if name == "EmulationResult":
        from .session.state import EmulationResult

        return EmulationResult
    if name == "EmulationSessionStore":
        from .session.store import EmulationSessionStore

        return EmulationSessionStore
    if name == "EmulationOrchestrationService":
        from .orchestration.scheduler import EmulationOrchestrationService

        return EmulationOrchestrationService
    if name == "EmulationPersistenceService":
        from .persistence import EmulationPersistenceService

        return EmulationPersistenceService
    if name == "YouTubeEmulator":
        from .emulator import YouTubeEmulator

        return YouTubeEmulator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
