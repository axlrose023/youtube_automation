from __future__ import annotations

from typing import Any

from dishka import FromDishka
from dishka.integrations.taskiq import inject

from app.services.browser.provider import BrowserSessionProvider
from app.services.emulation.core.capture_factory import AdCaptureProviderFactory
from app.services.emulation.orchestrator import EmulationOrchestrationService
from app.services.emulation.persistence import EmulationPersistenceService
from app.services.emulation.run import EmulationRunService
from app.services.emulation.session.store import EmulationSessionStore
from app.settings import Config
from app.tiq import broker

try:
    from app.services.emulation.ad_analysis import AdAnalysisService
except ModuleNotFoundError:
    AdAnalysisService = Any


@broker.task(task_name="emulation_task", timeout=28800)
@inject
async def emulation_task(
    session_id: str,
    duration_minutes: int,
    topics: list[str],
    session_provider: FromDishka[BrowserSessionProvider],
    session_store: FromDishka[EmulationSessionStore],
    capture_factory: FromDishka[AdCaptureProviderFactory],
    config: FromDishka[Config],
    persistence: FromDishka[EmulationPersistenceService],
    orchestrator: FromDishka[EmulationOrchestrationService],
    ad_analysis: FromDishka[AdAnalysisService] = None,
    profile_id: str | None = None,
) -> dict:
    run_service = EmulationRunService(
        session_provider=session_provider,
        session_store=session_store,
        capture_factory=capture_factory,
        config=config,
        persistence=persistence,
        orchestrator=orchestrator,
        ad_analysis=ad_analysis,
    )
    return await run_service.run(
        session_id=session_id,
        duration_minutes=duration_minutes,
        topics=topics,
        profile_id=profile_id,
    )
