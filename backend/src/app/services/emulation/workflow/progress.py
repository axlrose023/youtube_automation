from __future__ import annotations

import logging

from app.api.modules.emulation.models import PostProcessingStatus
from app.database.engine import SessionFactory
from app.database.uow import UnitOfWork
from app.services.emulation.persistence import EmulationPersistenceService
from app.services.emulation.session.store import EmulationSessionStore
from app.tiq import analysis_dispatch_broker

logger = logging.getLogger(__name__)


async def persist_safely(
    coro,
    session_id: str,
    persistence: EmulationPersistenceService,
    label: str,
) -> None:
    try:
        await coro
    except Exception:
        logger.exception("Session %s: failed to persist %s", session_id, label)
        await persistence.rollback()


async def queue_ad_analysis(
    *,
    session_id: str,
    session_store: EmulationSessionStore,
    ad_analysis_service_available: bool,
    total_hint: int | None = None,
) -> None:
    live_payload = await session_store.get(session_id) or {}
    if live_payload.get("post_processing_status") in {PostProcessingStatus.QUEUED, PostProcessingStatus.RUNNING}:
        return

    if not ad_analysis_service_available:
        logger.warning("Session %s: ad analysis service unavailable", session_id)
        await session_store.update(
            session_id,
            post_processing_status=None,
            post_processing_done=0,
            post_processing_total=0,
        )
        return

    previous_done = live_payload.get("post_processing_done")
    previous_total = live_payload.get("post_processing_total")
    analysis_total = 0
    if isinstance(previous_total, int | float):
        analysis_total = max(int(previous_total), 0)
    if isinstance(previous_done, int | float):
        analysis_total = max(analysis_total, int(previous_done))
    if isinstance(total_hint, int | float):
        hint_value = max(int(total_hint), 0)
        base_done = max(int(previous_done), 0) if isinstance(previous_done, int | float) else 0
        analysis_total = max(analysis_total, base_done + hint_value)

    await session_store.update(
        session_id,
        post_processing_status=PostProcessingStatus.QUEUED,
        post_processing_done=0,
        post_processing_total=analysis_total,
    )

    try:
        from taskiq.kicker import AsyncKicker

        await AsyncKicker(
            broker=analysis_dispatch_broker,
            task_name="ad_analysis_task",
            labels={},
        ).kiq(session_id)
    except Exception:
        logger.exception("Session %s: failed to queue ad analysis task", session_id)
        await session_store.update(
            session_id,
            post_processing_status=PostProcessingStatus.FAILED,
            post_processing_done=0,
            post_processing_total=analysis_total,
        )


async def persist_incremental_ad_captures(
    *,
    session_id: str,
    watched_ads: list[dict[str, object]],
    from_index: int,
) -> None:
    async with SessionFactory() as session:
        async with UnitOfWork(session) as uow:
            persistence = EmulationPersistenceService(uow)
            await persistence.persist_ad_captures(
                session_id=session_id,
                watched_ads=watched_ads,
                from_index=from_index,
            )
