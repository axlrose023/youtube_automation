from __future__ import annotations

import logging
import time
import uuid

from dishka import FromDishka
from dishka.integrations.taskiq import inject

from app.services.browser.provider import BrowserSessionProvider
from app.services.emulation import YouTubeEmulator
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.core.bootstrap import build_bootstrap_payload
from app.services.emulation.core.capture_factory import AdCaptureProviderFactory
from app.services.emulation.core.config import ORCHESTRATION_RUN_LOCK_TTL_SECONDS
from app.services.emulation.core.orchestration import (
    build_orchestration_payload,
    clamp_non_negative_int,
    pick_chunk_seconds,
)
from app.services.emulation.core.session_store import EmulationSessionStore
from app.services.emulation.orchestrator import EmulationOrchestrationService
from app.services.emulation.persistence import EmulationPersistenceService
from app.settings import Config
from app.tiq import broker

logger = logging.getLogger(__name__)


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
    realistic_window: bool | None = None,
) -> dict:
    run_holder = f"{session_id}:{uuid.uuid4().hex}"
    lock_acquired = await session_store.try_acquire_run_lock(
        session_id=session_id,
        holder=run_holder,
        ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
    )
    if not lock_acquired:
        logger.info(
            "Session %s: skipping task run because another chunk is active",
            session_id,
        )
        return {"status": "already_running", "session_id": session_id}

    ctx = None
    page = None
    try:
        live_payload = await session_store.get(session_id)
        if live_payload is None:
            logger.warning("Session %s: missing store payload, skipping", session_id)
            return {"status": "missing_session", "session_id": session_id}
        if live_payload.get("status") in {"completed", "failed"}:
            logger.info("Session %s: already finished, skipping duplicate task", session_id)
            return {"status": "already_finished", "session_id": session_id}

        now_ts = time.time()
        started_at_ts = live_payload.get("started_at")
        if not isinstance(started_at_ts, int | float):
            started_at_ts = now_ts

        orchestration = build_orchestration_payload(
            live_payload=live_payload,
            duration_minutes=duration_minutes,
            realistic_window=realistic_window,
        )
        should_orchestrate = bool(orchestration)

        await session_store.update(
            session_id,
            status="running",
            started_at=started_at_ts,
            finished_at=None,
            error=None,
            orchestration=orchestration if should_orchestrate else None,
        )
        live_payload = await session_store.get(session_id) or {}
        try:
            await persistence.persist_history_running(
                session_id=session_id,
                duration_minutes=duration_minutes,
                topics=topics,
                live_payload=live_payload,
            )
        except Exception:
            logger.exception("Session %s: failed to persist running state", session_id)
            await persistence.rollback()

        chunk_seconds = duration_minutes * 60
        bootstrap: dict[str, object] | None = None
        ad_persist_from = 0
        if should_orchestrate:
            assert orchestration is not None
            chunk_seconds = pick_chunk_seconds(orchestration)
            if chunk_seconds <= 0:
                return await orchestrator.finalize_without_run(
                    session_id,
                    duration_minutes,
                    topics,
                    orchestration,
                )
            bootstrap = build_bootstrap_payload(live_payload)
            ad_persist_from = clamp_non_negative_int(
                orchestration.get("persisted_ads_count")
            )

        run_duration_minutes = max(1, int((chunk_seconds + 59) // 60))

        ctx = await session_provider.acquire_context()
        page = await ctx.new_page()

        ad_capture_path = config.storage.ad_captures_path
        ad_capture_path.mkdir(parents=True, exist_ok=True)
        capture = capture_factory.create(ctx, ad_capture_path)

        emulator = YouTubeEmulator(
            page=page,
            topics=topics,
            duration_minutes=run_duration_minutes,
            session_store=session_store,
            session_id=session_id,
            capture=capture,
            bootstrap=bootstrap,
        )
        result = await emulator.run()

        try:
            await persistence.persist_ad_captures(
                session_id=session_id,
                watched_ads=result.watched_ads,
                from_index=ad_persist_from,
            )
        except Exception:
            logger.exception("Session %s: failed to persist ad captures", session_id)
            await persistence.rollback()

        post_run_payload = await session_store.get(session_id) or {}
        current_mode = post_run_payload.get("mode")
        current_fatigue = post_run_payload.get("fatigue")
        current_personality = post_run_payload.get("personality")

        if should_orchestrate:
            assert orchestration is not None
            return await orchestrator.complete_or_schedule_next_chunk(
                session_id,
                duration_minutes,
                topics,
                realistic_window,
                result,
                orchestration,
                current_mode,
                current_fatigue,
                current_personality,
            )

        await session_store.update(
            session_id,
            status="completed",
            finished_at=time.time(),
            bytes_downloaded=result.bytes_downloaded,
            topics_searched=result.topics_searched,
            videos_watched=result.videos_watched,
            watched_videos_count=len(result.watched_videos),
            watched_videos=result.watched_videos,
            watched_ads_count=len(result.watched_ads),
            watched_ads=result.watched_ads,
            watched_ads_analytics=build_ads_analytics(result.watched_ads),
            total_duration_seconds=result.total_duration_seconds,
            mode=current_mode,
            fatigue=current_fatigue,
            personality=current_personality,
            orchestration=None,
        )

        live_payload = await session_store.get(session_id) or {}
        try:
            await persistence.persist_history_completed(
                session_id=session_id,
                duration_minutes=duration_minutes,
                topics=topics,
                bytes_downloaded=result.bytes_downloaded,
                topics_searched=result.topics_searched,
                videos_watched=result.videos_watched,
                watched_videos=result.watched_videos,
                watched_ads=result.watched_ads,
                total_duration_seconds=result.total_duration_seconds,
                live_payload=live_payload,
            )
        except Exception:
            logger.exception("Session %s: failed to persist emulation history", session_id)
            await persistence.rollback()

        logger.info("Session %s completed: %s", session_id, result)
        return {"status": "completed", "session_id": session_id}

    except Exception as exc:
        logger.exception("Session %s failed", session_id)
        await session_store.update(
            session_id,
            status="failed",
            finished_at=time.time(),
            error=str(exc),
        )
        live_payload = await session_store.get(session_id) or {}
        try:
            await persistence.persist_history_failed(
                session_id=session_id,
                duration_minutes=duration_minutes,
                topics=topics,
                error=str(exc),
                live_payload=live_payload,
            )
        except Exception:
            logger.exception(
                "Session %s: failed to persist emulation history failure state",
                session_id,
            )
            await persistence.rollback()
        raise
    finally:
        if page:
            await page.close()
        if ctx:
            await session_provider.release_context(ctx)
        await session_store.release_run_lock(session_id, run_holder)
