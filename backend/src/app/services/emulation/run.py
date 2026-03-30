from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from playwright.async_api import BrowserContext, Page

from app.api.modules.emulation.models import AnalysisStatus, SessionStatus, VideoStatus
from app.services.browser.provider import BrowserSessionProvider
from app.services.emulation import YouTubeEmulator
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.session.bootstrap import build_bootstrap_payload
from app.services.emulation.core.capture_factory import AdCaptureProviderFactory
from app.services.emulation.config import ORCHESTRATION_RUN_LOCK_TTL_SECONDS
from app.services.emulation.orchestration.policy import (
    build_orchestration_payload,
    clamp_non_negative_int,
    pick_chunk_seconds,
)
from app.services.emulation.orchestrator import EmulationOrchestrationService
from app.services.emulation.persistence import EmulationPersistenceService
from app.services.emulation.session.store import EmulationSessionStore
from app.services.emulation.workflow.finalizer import finalize_completed, finalize_stopped
from app.services.emulation.workflow.progress import (
    persist_incremental_ad_captures,
    persist_safely,
    queue_ad_analysis,
)
from app.settings import Config

logger = logging.getLogger(__name__)


def _normalize_profile_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


async def _acquire_page(ctx: BrowserContext, session_id: str) -> Page:
    try:
        return await ctx.new_page()
    except Exception as exc:
        existing_pages = [page for page in ctx.pages if not page.is_closed()]
        if not existing_pages:
            raise
        page = existing_pages[0]
        logger.warning(
            "Session %s: new_page failed (%s), reusing existing tab",
            session_id,
            type(exc).__name__,
        )
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return page


def _page_url(page: Page) -> str:
    try:
        return page.url or "<unknown>"
    except Exception:
        return "<unavailable>"


def _attach_runtime_debug_listeners(
    *,
    ctx: BrowserContext,
    page: Page,
    session_id: str,
) -> dict[str, bool]:
    runtime_state = {"shutting_down": False}

    page.on(
        "close",
        lambda: logger.warning(
            "Session %s: main page closed unexpectedly (url=%s)",
            session_id,
            _page_url(page),
        ) if not runtime_state["shutting_down"] else None,
    )
    page.on(
        "crash",
        lambda: logger.error(
            "Session %s: main page crashed (url=%s)",
            session_id,
            _page_url(page),
        ),
    )
    ctx.on(
        "close",
        lambda: logger.warning(
            "Session %s: browser context closed",
            session_id,
        ) if not runtime_state["shutting_down"] else None,
    )
    browser = ctx.browser
    if browser is not None:
        browser.on(
            "disconnected",
            lambda: logger.warning(
                "Session %s: browser disconnected",
                session_id,
            ) if not runtime_state["shutting_down"] else None,
        )
    return runtime_state


def _resolve_orchestration(
    live_payload: dict,
    duration_minutes: int,
) -> tuple[dict[str, object] | None, int, dict[str, object] | None, int]:
    orchestration = build_orchestration_payload(
        live_payload=live_payload,
        duration_minutes=duration_minutes,
    )
    if not orchestration:
        return None, duration_minutes * 60, None, 0

    chunk_seconds = pick_chunk_seconds(orchestration)
    bootstrap = build_bootstrap_payload(live_payload)
    ad_persist_from = clamp_non_negative_int(orchestration.get("persisted_ads_count"))
    return orchestration, chunk_seconds, bootstrap, ad_persist_from


class EmulationRunService:
    def __init__(
        self,
        session_provider: BrowserSessionProvider,
        session_store: EmulationSessionStore,
        capture_factory: AdCaptureProviderFactory,
        config: Config,
        persistence: EmulationPersistenceService,
        orchestrator: EmulationOrchestrationService,
        ad_analysis: Any = None,
    ) -> None:
        self._session_provider = session_provider
        self._session_store = session_store
        self._capture_factory = capture_factory
        self._config = config
        self._persistence = persistence
        self._orchestrator = orchestrator
        self._ad_analysis = ad_analysis

    async def run(
        self,
        *,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        profile_id: str | None = None,
    ) -> dict:
        run_holder = f"{session_id}:{uuid.uuid4().hex}"
        profile_lock_holder = f"{run_holder}:profile"
        resolved_profile_id: str | None = None
        ctx: BrowserContext | None = None
        page: Page | None = None
        runtime_debug_state: dict[str, bool] | None = None

        lock_acquired = await self._session_store.try_acquire_run_lock(
            session_id=session_id,
            holder=run_holder,
            ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
        )
        if not lock_acquired:
            logger.info("Session %s: skipping — another chunk is active", session_id)
            return {"status": "already_running", "session_id": session_id}

        try:
            live_payload = await self._session_store.get(session_id)
            if live_payload is None:
                logger.warning("Session %s: missing store payload, skipping", session_id)
                return {"status": "missing_session", "session_id": session_id}
            if live_payload.get("status") in {
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.STOPPED,
            }:
                logger.info("Session %s: already finished, skipping duplicate task", session_id)
                return {"status": "already_finished", "session_id": session_id}

            resolved_profile_id = _normalize_profile_id(profile_id) or _normalize_profile_id(
                live_payload.get("profile_id"),
            )
            if resolved_profile_id:
                profile_locked = await self._session_store.try_acquire_profile_lock(
                    profile_id=resolved_profile_id,
                    holder=profile_lock_holder,
                    ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
                )
                if not profile_locked:
                    raise RuntimeError(f"AdsPower profile {resolved_profile_id} is already in use")

            started_at_ts = live_payload.get("started_at")
            if not isinstance(started_at_ts, (int, float)):
                started_at_ts = time.time()

            orchestration, chunk_seconds, bootstrap, ad_persist_from = _resolve_orchestration(
                live_payload,
                duration_minutes,
            )
            should_orchestrate = orchestration is not None

            if bootstrap is None and live_payload.get("resumed_from"):
                bootstrap = build_bootstrap_payload(live_payload)

            await self._session_store.update(
                session_id,
                status=SessionStatus.RUNNING,
                started_at=started_at_ts,
                finished_at=None,
                error=None,
                orchestration=orchestration,
                profile_id=resolved_profile_id,
                post_processing_status=None,
                post_processing_done=0,
                post_processing_total=0,
            )
            live_payload = await self._session_store.get(session_id) or {}
            await persist_safely(
                self._persistence.persist_history_running(
                    session_id=session_id,
                    duration_minutes=duration_minutes,
                    topics=topics,
                    live_payload=live_payload,
                ),
                session_id,
                self._persistence,
                "running state",
            )

            if should_orchestrate and chunk_seconds <= 0:
                return await self._orchestrator.finalize_without_run(
                    session_id,
                    duration_minutes,
                    topics,
                    orchestration,
                )

            persisted_ads_count = ad_persist_from

            async def _handle_ready_capture(
                watched_ads: list[dict[str, object]],
                state_entry: dict[str, object],
            ) -> None:
                nonlocal persisted_ads_count, orchestration

                capture_payload = state_entry.get("capture")
                if isinstance(capture_payload, dict):
                    capture_payload.setdefault("analysis_status", AnalysisStatus.PENDING)
                    capture_payload.setdefault("analysis_summary", None)

                await self._session_store.update(
                    session_id,
                    watched_ads=watched_ads,
                    watched_ads_count=len(watched_ads),
                    watched_ads_analytics=build_ads_analytics(watched_ads),
                )

                if persisted_ads_count >= len(watched_ads):
                    return

                await persist_safely(
                    persist_incremental_ad_captures(
                        session_id=session_id,
                        watched_ads=watched_ads,
                        from_index=persisted_ads_count,
                    ),
                    session_id,
                    self._persistence,
                    "incremental ad captures",
                )

                previous_count = persisted_ads_count
                persisted_ads_count = len(watched_ads)
                if orchestration is not None:
                    orchestration["persisted_ads_count"] = persisted_ads_count
                    await self._session_store.update(session_id, orchestration=orchestration)

                try:
                    new_items = watched_ads[previous_count:persisted_ads_count]
                    total_hint = sum(
                        1
                        for item in new_items
                        if isinstance(item, dict)
                        and isinstance(item.get("capture"), dict)
                        and item["capture"].get("video_status") == VideoStatus.COMPLETED
                    )
                    await queue_ad_analysis(
                        session_id=session_id,
                        session_store=self._session_store,
                        ad_analysis_service_available=self._ad_analysis is not None,
                        total_hint=total_hint,
                    )
                except Exception:
                    logger.exception(
                        "Session %s: background ad analysis enqueue failed after incremental capture persist",
                        session_id,
                    )

            run_duration_minutes = max(1, int((chunk_seconds + 59) // 60))
            ctx = await self._session_provider.acquire_context(profile_id=resolved_profile_id)
            page = await _acquire_page(ctx, session_id)
            runtime_debug_state = _attach_runtime_debug_listeners(
                ctx=ctx,
                page=page,
                session_id=session_id,
            )

            ad_capture_path = self._config.storage.ad_captures_path
            ad_capture_path.mkdir(parents=True, exist_ok=True)
            capture = self._capture_factory.create(ctx, ad_capture_path)

            emulator = YouTubeEmulator(
                page=page,
                topics=topics,
                duration_minutes=run_duration_minutes,
                session_store=self._session_store,
                session_id=session_id,
                capture=capture,
                bootstrap=bootstrap,
                on_capture_ready=_handle_ready_capture,
            )
            result = await emulator.run()
            completed_at = time.time()

            await persist_safely(
                self._persistence.persist_ad_captures(
                    session_id=session_id,
                    watched_ads=result.watched_ads,
                    from_index=persisted_ads_count,
                ),
                session_id,
                self._persistence,
                "ad captures",
            )

            if runtime_debug_state is not None:
                runtime_debug_state["shutting_down"] = True
            if page:
                await page.close()
                page = None
            if ctx:
                await self._session_provider.release_context(ctx)
                ctx = None
            if resolved_profile_id:
                await self._session_store.release_profile_lock(
                    resolved_profile_id,
                    profile_lock_holder,
                )
                resolved_profile_id = None

            post_run = await self._session_store.get(session_id) or {}
            current_mode = post_run.get("mode")
            current_fatigue = post_run.get("fatigue")
            current_personality = post_run.get("personality")
            stop_requested = bool(post_run.get("stop_requested"))

            if post_run.get("status") == SessionStatus.STOPPED or stop_requested:
                await finalize_stopped(
                    session_id,
                    self._session_store,
                    result,
                    current_mode,
                    current_fatigue,
                    current_personality,
                )
                live_payload = await self._session_store.get(session_id) or {}
                await persist_safely(
                    self._persistence.persist_history(
                        session_id=session_id,
                        status=SessionStatus.STOPPED,
                        duration_minutes=duration_minutes,
                        topics=topics,
                        live_payload=live_payload,
                        error="Stopped by user",
                        bytes_downloaded=result.bytes_downloaded,
                        topics_searched=result.topics_searched,
                        videos_watched=result.videos_watched,
                        watched_videos=result.watched_videos,
                        watched_ads=result.watched_ads,
                        total_duration_seconds=result.total_duration_seconds,
                    ),
                    session_id,
                    self._persistence,
                    "stopped history",
                )
                logger.info("Session %s stopped: %s", session_id, result)
            elif not should_orchestrate:
                await finalize_completed(
                    session_id,
                    self._session_store,
                    result,
                    current_mode,
                    current_fatigue,
                    current_personality,
                    completed_at,
                )
                live_payload = await self._session_store.get(session_id) or {}
                await persist_safely(
                    self._persistence.persist_history_completed(
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
                    ),
                    session_id,
                    self._persistence,
                    "completed history",
                )

            try:
                await queue_ad_analysis(
                    session_id=session_id,
                    session_store=self._session_store,
                    ad_analysis_service_available=self._ad_analysis is not None,
                )
            except Exception:
                logger.exception("Session %s: background ad analysis enqueue failed", session_id)

            if post_run.get("status") == SessionStatus.STOPPED or stop_requested:
                return {"status": SessionStatus.STOPPED, "session_id": session_id}

            if should_orchestrate:
                return await self._orchestrator.complete_or_schedule_next_chunk(
                    session_id,
                    duration_minutes,
                    topics,
                    resolved_profile_id,
                    result,
                    orchestration,
                    current_mode,
                    current_fatigue,
                    current_personality,
                )

            logger.info("Session %s completed: %s", session_id, result)
            return {"status": SessionStatus.COMPLETED, "session_id": session_id}

        except Exception as exc:
            logger.exception("Session %s failed", session_id)
            await self._session_store.update(
                session_id,
                status=SessionStatus.FAILED,
                stop_requested=False,
                finished_at=time.time(),
                error=str(exc),
            )
            live_payload = await self._session_store.get(session_id) or {}
            await persist_safely(
                self._persistence.persist_history_failed(
                    session_id=session_id,
                    duration_minutes=duration_minutes,
                    topics=topics,
                    error=str(exc),
                    live_payload=live_payload,
                ),
                session_id,
                self._persistence,
                "failure state",
            )
            raise
        finally:
            if runtime_debug_state is not None:
                runtime_debug_state["shutting_down"] = True
            if page:
                await page.close()
            if ctx:
                await self._session_provider.release_context(ctx)
            if resolved_profile_id:
                await self._session_store.release_profile_lock(
                    resolved_profile_id,
                    profile_lock_holder,
                )
            await self._session_store.release_run_lock(session_id, run_holder)
