from __future__ import annotations

import logging
import time
import uuid

from dishka import FromDishka
from dishka.integrations.taskiq import inject
from playwright.async_api import BrowserContext, Page

from app.services.browser.provider import BrowserSessionProvider
from app.services.emulation import YouTubeEmulator
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.core.bootstrap import build_bootstrap_payload
from app.services.emulation.core.capture_factory import AdCaptureProviderFactory
try:
    from app.services.emulation.ad_analysis import AdAnalysisService

    _ANALYSIS_AVAILABLE = True
except ModuleNotFoundError:
    _ANALYSIS_AVAILABLE = False
from app.services.emulation.core.config import ORCHESTRATION_RUN_LOCK_TTL_SECONDS
from app.services.emulation.core.orchestration import (
    build_orchestration_payload,
    clamp_non_negative_int,
    pick_chunk_seconds,
)
from app.services.emulation.core.session.store import EmulationSessionStore
from app.services.emulation.orchestrator import EmulationOrchestrationService
from app.services.emulation.persistence import EmulationPersistenceService
from app.settings import Config
from app.tiq import broker

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────


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
            session_id, type(exc).__name__,
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


async def _persist_safely(
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


# ── Orchestration setup ───────────────────────────────────────


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


# ── Store update for completed session ────────────────────────


async def _finalize_completed(
    session_id: str,
    session_store: EmulationSessionStore,
    result,
    current_mode: object,
    current_fatigue: object,
    current_personality: object,
) -> None:
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


# ── Main task ─────────────────────────────────────────────────


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
    ad_analysis: FromDishka[AdAnalysisService] | None = None,
    profile_id: str | None = None,
) -> dict:
    run_holder = f"{session_id}:{uuid.uuid4().hex}"
    profile_lock_holder = f"{run_holder}:profile"
    resolved_profile_id: str | None = None
    ctx: BrowserContext | None = None
    page: Page | None = None
    runtime_debug_state: dict[str, bool] | None = None

    lock_acquired = await session_store.try_acquire_run_lock(
        session_id=session_id, holder=run_holder,
        ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
    )
    if not lock_acquired:
        logger.info("Session %s: skipping — another chunk is active", session_id)
        return {"status": "already_running", "session_id": session_id}

    try:
        # ── Validate session ──────────────────────────────────
        live_payload = await session_store.get(session_id)
        if live_payload is None:
            logger.warning("Session %s: missing store payload, skipping", session_id)
            return {"status": "missing_session", "session_id": session_id}
        if live_payload.get("status") in {"completed", "failed", "stopped"}:
            logger.info("Session %s: already finished, skipping duplicate task", session_id)
            return {"status": "already_finished", "session_id": session_id}

        # ── Profile lock ──────────────────────────────────────
        resolved_profile_id = _normalize_profile_id(profile_id) or _normalize_profile_id(
            live_payload.get("profile_id"),
        )
        if resolved_profile_id:
            profile_locked = await session_store.try_acquire_profile_lock(
                profile_id=resolved_profile_id, holder=profile_lock_holder,
                ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
            )
            if not profile_locked:
                raise RuntimeError(f"AdsPower profile {resolved_profile_id} is already in use")

        # ── Orchestration ─────────────────────────────────────
        started_at_ts = live_payload.get("started_at")
        if not isinstance(started_at_ts, (int, float)):
            started_at_ts = time.time()

        orchestration, chunk_seconds, bootstrap, ad_persist_from = _resolve_orchestration(
            live_payload, duration_minutes,
        )
        should_orchestrate = orchestration is not None

        if bootstrap is None and live_payload.get("resumed_from"):
            bootstrap = build_bootstrap_payload(live_payload)

        await session_store.update(
            session_id, status="running", started_at=started_at_ts,
            finished_at=None, error=None,
            orchestration=orchestration, profile_id=resolved_profile_id,
        )
        live_payload = await session_store.get(session_id) or {}
        await _persist_safely(
            persistence.persist_history_running(
                session_id=session_id, duration_minutes=duration_minutes,
                topics=topics, live_payload=live_payload,
            ),
            session_id, persistence, "running state",
        )

        # ── Early exit for exhausted orchestration budget ─────
        if should_orchestrate and chunk_seconds <= 0:
            return await orchestrator.finalize_without_run(
                session_id, duration_minutes, topics, orchestration,
            )

        # ── Run emulation ─────────────────────────────────────
        run_duration_minutes = max(1, int((chunk_seconds + 59) // 60))
        ctx = await session_provider.acquire_context(profile_id=resolved_profile_id)
        page = await _acquire_page(ctx, session_id)
        runtime_debug_state = _attach_runtime_debug_listeners(
            ctx=ctx,
            page=page,
            session_id=session_id,
        )

        ad_capture_path = config.storage.ad_captures_path
        ad_capture_path.mkdir(parents=True, exist_ok=True)
        capture = capture_factory.create(ctx, ad_capture_path)

        emulator = YouTubeEmulator(
            page=page, topics=topics, duration_minutes=run_duration_minutes,
            session_store=session_store, session_id=session_id,
            capture=capture, bootstrap=bootstrap,
        )
        result = await emulator.run()

        # ── Persist ad captures ───────────────────────────────
        await _persist_safely(
            persistence.persist_ad_captures(
                session_id=session_id, watched_ads=result.watched_ads,
                from_index=ad_persist_from,
            ),
            session_id, persistence, "ad captures",
        )

        # ── Release browser before analysis ───────────────────
        if runtime_debug_state is not None:
            runtime_debug_state["shutting_down"] = True
        if page:
            await page.close()
            page = None
        if ctx:
            await session_provider.release_context(ctx)
            ctx = None
        if resolved_profile_id:
            await session_store.release_profile_lock(resolved_profile_id, profile_lock_holder)
            resolved_profile_id = None

        # ── Analyze ad creatives ───────────────────────────────
        if ad_analysis is not None:
            try:
                await ad_analysis.analyze_session_captures(session_id)
            except Exception:
                logger.exception("Session %s: ad analysis failed", session_id)

        # ── Post-run: orchestrate or complete ─────────────────
        post_run = await session_store.get(session_id) or {}
        current_mode = post_run.get("mode")
        current_fatigue = post_run.get("fatigue")
        current_personality = post_run.get("personality")

        if should_orchestrate:
            return await orchestrator.complete_or_schedule_next_chunk(
                session_id, duration_minutes, topics, resolved_profile_id,
                result, orchestration, current_mode, current_fatigue, current_personality,
            )

        await _finalize_completed(
            session_id, session_store, result,
            current_mode, current_fatigue, current_personality,
        )
        live_payload = await session_store.get(session_id) or {}
        await _persist_safely(
            persistence.persist_history_completed(
                session_id=session_id, duration_minutes=duration_minutes,
                topics=topics, bytes_downloaded=result.bytes_downloaded,
                topics_searched=result.topics_searched, videos_watched=result.videos_watched,
                watched_videos=result.watched_videos, watched_ads=result.watched_ads,
                total_duration_seconds=result.total_duration_seconds, live_payload=live_payload,
            ),
            session_id, persistence, "completed history",
        )

        logger.info("Session %s completed: %s", session_id, result)
        return {"status": "completed", "session_id": session_id}

    except Exception as exc:
        logger.exception("Session %s failed", session_id)
        await session_store.update(
            session_id, status="failed", finished_at=time.time(), error=str(exc),
        )
        live_payload = await session_store.get(session_id) or {}
        await _persist_safely(
            persistence.persist_history_failed(
                session_id=session_id, duration_minutes=duration_minutes,
                topics=topics, error=str(exc), live_payload=live_payload,
            ),
            session_id, persistence, "failure state",
        )
        raise

    finally:
        if runtime_debug_state is not None:
            runtime_debug_state["shutting_down"] = True
        if page:
            await page.close()
        if ctx:
            await session_provider.release_context(ctx)
        if resolved_profile_id:
            await session_store.release_profile_lock(resolved_profile_id, profile_lock_holder)
        await session_store.release_run_lock(session_id, run_holder)
