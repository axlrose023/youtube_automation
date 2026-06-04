from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from dishka import FromDishka
from dishka.integrations.taskiq import inject
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from taskiq.kicker import AsyncKicker

from app.api.modules.emulation.models import PostProcessingStatus

try:
    from app.services.emulation.ads.analysis.service import AdAnalysisService
except ModuleNotFoundError:
    AdAnalysisService = Any
from app.services.emulation.session.store import EmulationSessionStore
from app.tiq import analysis_dispatch_broker, broker

logger = logging.getLogger(__name__)

_ANALYSIS_LOCK_TTL_SECONDS = 14400
_MAX_ANALYSIS_DB_RETRIES = 3


def _is_retryable_database_error(exc: BaseException) -> bool:
    if isinstance(exc, (InterfaceError, OperationalError)):
        return True
    if isinstance(exc, DBAPIError) and bool(getattr(exc, "connection_invalidated", False)):
        return True

    message = str(exc).lower()
    retryable_fragments = (
        "connection is closed",
        "connection was closed",
        "connection has been closed",
        "connection terminated",
        "server closed the connection",
        "terminating connection",
    )
    return any(fragment in message for fragment in retryable_fragments)


def _live_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, int | float):
        return max(int(value), 0)
    return 0


async def _mark_retryable_database_failure(
    *,
    session_id: str,
    session_store: EmulationSessionStore,
    pending_total: int,
) -> int | None:
    live_payload = await session_store.get(session_id) or {}
    retry_count = _live_int(live_payload, "post_processing_retry_count")
    previous_total = _live_int(live_payload, "post_processing_total")
    analysis_total = max(int(pending_total or 0), previous_total)

    if retry_count >= _MAX_ANALYSIS_DB_RETRIES:
        await session_store.update(
            session_id,
            post_processing_status=PostProcessingStatus.FAILED,
            post_processing_done=0,
            post_processing_total=analysis_total,
            post_processing_enqueued_at=None,
            post_processing_started_at=None,
        )
        return None

    next_retry_count = retry_count + 1
    await session_store.update(
        session_id,
        post_processing_status=PostProcessingStatus.QUEUED,
        post_processing_done=0,
        post_processing_total=analysis_total,
        post_processing_enqueued_at=time.time(),
        post_processing_started_at=None,
        post_processing_retry_count=next_retry_count,
    )
    return next_retry_count


async def _sync_live_capture_analysis_state(
    *,
    session_id: str,
    session_store: EmulationSessionStore,
    ad_analysis: AdAnalysisService,
) -> None:
    live_payload = await session_store.get(session_id)
    if live_payload is None:
        return

    watched_ads = live_payload.get("watched_ads")
    if not isinstance(watched_ads, list) or not watched_ads:
        return

    updates = await ad_analysis.build_live_capture_analysis_state(session_id)
    if not updates:
        return

    changed = False
    normalized_ads: list[dict[str, object]] = []
    for item in watched_ads:
        if not isinstance(item, dict):
            normalized_ads.append(item)
            continue

        ad = dict(item)
        ad_position = ad.get("position")
        if not isinstance(ad_position, int):
            normalized_ads.append(ad)
            continue

        capture_update = updates.get(ad_position)
        if capture_update is None:
            normalized_ads.append(ad)
            continue

        capture_payload = ad.get("capture")
        if isinstance(capture_payload, dict):
            merged_capture = dict(capture_payload)
        else:
            merged_capture = {}
        merged_capture.update(capture_update)
        ad["capture"] = merged_capture
        normalized_ads.append(ad)
        changed = True

    if not changed:
        return

    await session_store.update(
        session_id,
        watched_ads=normalized_ads,
        watched_ads_count=max(int(live_payload.get("watched_ads_count") or 0), len(normalized_ads)),
    )


@broker.task(task_name="ad_analysis_task", timeout=14400)
@inject
async def ad_analysis_task(
    session_id: str,
    session_store: FromDishka[EmulationSessionStore],
    ad_analysis: FromDishka[AdAnalysisService] = None,
) -> dict:
    if ad_analysis is None:
        logger.warning("Session %s: ad analysis service unavailable", session_id)
        await session_store.update(
            session_id,
            post_processing_status=PostProcessingStatus.FAILED,
            post_processing_done=0,
            post_processing_total=0,
        )
        return {"status": "unavailable", "session_id": session_id}

    lock_holder = f"{session_id}:{uuid.uuid4().hex}"
    lock_acquired = await session_store.try_acquire_analysis_lock(
        session_id=session_id,
        holder=lock_holder,
        ttl_seconds=_ANALYSIS_LOCK_TTL_SECONDS,
    )
    if not lock_acquired:
        logger.info("Session %s: skipping ad analysis — another analysis task is active", session_id)
        return {"status": "already_running", "session_id": session_id}

    lock_released = False
    pending_total = 0
    try:
        while True:
            pending_total = await ad_analysis.get_session_analysis_workload(session_id)
            if pending_total <= 0:
                final_status, done, total = await ad_analysis.summarize_session_analysis(session_id)
                await _sync_live_capture_analysis_state(
                    session_id=session_id,
                    session_store=session_store,
                    ad_analysis=ad_analysis,
                )
                if total > 0:
                    await session_store.update(
                        session_id,
                        post_processing_status=final_status,
                        post_processing_done=done,
                        post_processing_total=total,
                        post_processing_enqueued_at=None,
                        post_processing_started_at=None,
                        post_processing_retry_count=0,
                    )
                else:
                    await session_store.update(
                        session_id,
                        post_processing_status=None,
                        post_processing_done=0,
                        post_processing_total=0,
                        post_processing_enqueued_at=None,
                        post_processing_started_at=None,
                        post_processing_retry_count=0,
                    )
                return {
                    "status": final_status or "no_work",
                    "session_id": session_id,
                    "done": done,
                    "total": total,
                }

            await session_store.update(
                session_id,
                post_processing_status=PostProcessingStatus.RUNNING,
                post_processing_done=0,
                post_processing_total=pending_total,
                post_processing_enqueued_at=None,
                post_processing_started_at=time.time(),
            )
            await _sync_live_capture_analysis_state(
                session_id=session_id,
                session_store=session_store,
                ad_analysis=ad_analysis,
            )

            try:
                final_status, done, total = await ad_analysis.analyze_session_captures(session_id)
            except Exception as exc:
                if _is_retryable_database_error(exc):
                    raise
                logger.exception("Session %s: background ad analysis failed", session_id)
                await session_store.update(
                    session_id,
                    post_processing_status=PostProcessingStatus.FAILED,
                    post_processing_done=0,
                    post_processing_total=pending_total,
                    post_processing_enqueued_at=None,
                    post_processing_started_at=None,
                )
                raise

            await _sync_live_capture_analysis_state(
                session_id=session_id,
                session_store=session_store,
                ad_analysis=ad_analysis,
            )

            if total > 0:
                await session_store.update(
                    session_id,
                    post_processing_status=final_status or PostProcessingStatus.COMPLETED,
                    post_processing_done=done,
                    post_processing_total=total,
                    post_processing_enqueued_at=None,
                    post_processing_started_at=None,
                    post_processing_retry_count=0,
                )
            else:
                await session_store.update(
                    session_id,
                    post_processing_status=None,
                    post_processing_done=0,
                    post_processing_total=0,
                    post_processing_enqueued_at=None,
                    post_processing_started_at=None,
                    post_processing_retry_count=0,
                )

            remaining = await ad_analysis.get_session_analysis_workload(session_id)
            if remaining <= 0:
                continue

            await session_store.update(
                session_id,
                post_processing_status=PostProcessingStatus.QUEUED,
                post_processing_done=0,
                post_processing_total=remaining,
                post_processing_enqueued_at=time.time(),
                post_processing_started_at=None,
            )
            logger.info(
                "Session %s: re-running ad analysis for %s newly pending captures",
                session_id,
                remaining,
            )
    except Exception as exc:
        if not _is_retryable_database_error(exc):
            raise

        logger.exception("Session %s: retryable database failure during ad analysis", session_id)
        retry_count = await _mark_retryable_database_failure(
            session_id=session_id,
            session_store=session_store,
            pending_total=pending_total,
        )
        if retry_count is None:
            return {
                "status": PostProcessingStatus.FAILED,
                "session_id": session_id,
                "reason": "database_retry_limit",
            }

        await session_store.release_analysis_lock(session_id, lock_holder)
        lock_released = True
        try:
            await AsyncKicker(
                broker=analysis_dispatch_broker,
                task_name="ad_analysis_task",
                labels={},
            ).kiq(session_id)
        except Exception:
            logger.exception("Session %s: failed to re-queue ad analysis retry", session_id)
            await session_store.update(
                session_id,
                post_processing_status=PostProcessingStatus.FAILED,
                post_processing_done=0,
                post_processing_total=pending_total,
                post_processing_enqueued_at=None,
                post_processing_started_at=None,
            )
            return {
                "status": PostProcessingStatus.FAILED,
                "session_id": session_id,
                "reason": "retry_enqueue_failed",
            }

        return {
            "status": PostProcessingStatus.QUEUED,
            "session_id": session_id,
            "retry_count": retry_count,
        }
    finally:
        if not lock_released:
            await session_store.release_analysis_lock(session_id, lock_holder)
