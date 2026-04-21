from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid

from dishka import FromDishka
from dishka.integrations.taskiq import inject

from app.api.modules.emulation.models import SessionStatus
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.config import ORCHESTRATION_RUN_LOCK_TTL_SECONDS
from app.services.emulation.persistence import EmulationPersistenceService
from app.services.emulation.session.store import EmulationSessionStore, merge_live_watched_ads
from app.settings import Config
from app.tiq import broker

logger = logging.getLogger(__name__)

_ANDROID_QUEUE_POLL_SECONDS = 5
_ANDROID_HEARTBEAT_SECONDS = 5


def _android_device_lock_id(config: Config) -> str:
    avd_name = (config.android_app.default_avd_name or "").strip() or "default"
    return f"android-device:{avd_name}"


async def _android_session_heartbeat(
    session_id: str,
    session_store: EmulationSessionStore,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            payload = await session_store.get(session_id)
            if payload is None:
                return
            if payload.get("status") in {
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.STOPPED,
            }:
                return
            await session_store.update(session_id, mode="android")
        except Exception:
            logger.exception("Android session %s: heartbeat update failed", session_id)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_ANDROID_HEARTBEAT_SECONDS)
        except TimeoutError:
            continue


@broker.task(task_name="android_emulation_task", timeout=28800)
@inject
async def android_emulation_task(
    session_id: str,
    duration_minutes: int,
    topics: list[str],
    session_store: FromDishka[EmulationSessionStore],
    persistence: FromDishka[EmulationPersistenceService],
    config: FromDishka[Config],
    proxy_url: str | None = None,
    headless: bool | None = None,
) -> dict:
    from app.services.mobile_app.android.runner import AndroidYouTubeSessionRunner
    from app.services.mobile_app.android.result_payloads import (
        build_topic_watched_video_payload,
    )

    run_holder = f"{session_id}:{uuid.uuid4().hex}"
    device_lock_id = _android_device_lock_id(config)
    device_lock_holder = f"{run_holder}:android-device"
    started_at_ts = time.time()
    _last_persisted_ads_count = 0
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task[None] | None = None

    lock_acquired = await session_store.try_acquire_run_lock(
        session_id=session_id,
        holder=run_holder,
        ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
    )
    if not lock_acquired:
        logger.info("Android session %s: skipping duplicate task", session_id)
        return {"status": "already_running", "session_id": session_id}

    device_lock_acquired = False
    try:
        while True:
            live_payload = await session_store.get(session_id)
            if live_payload is None:
                logger.warning("Android session %s: missing store payload, skipping", session_id)
                return {"status": "missing_session", "session_id": session_id}

            current_status = live_payload.get("status")
            if current_status == SessionStatus.STOPPING:
                await session_store.update(
                    session_id,
                    status=SessionStatus.STOPPED,
                    finished_at=time.time(),
                    error="Stopped by user",
                    queue_reason=None,
                )
                logger.info("Android session %s: stopped while waiting for device slot", session_id)
                return {"status": SessionStatus.STOPPED, "session_id": session_id}
            if current_status in {
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.STOPPED,
            }:
                logger.info("Android session %s: already finished, skipping", session_id)
                return {"status": "already_finished", "session_id": session_id}

            device_lock_acquired = await session_store.try_acquire_profile_lock(
                profile_id=device_lock_id,
                holder=device_lock_holder,
                ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
            )
            if device_lock_acquired:
                break

            await session_store.update(
                session_id,
                status=SessionStatus.QUEUED,
                mode="android",
                queue_reason=f"Waiting for Android device slot {device_lock_id}",
            )
            await asyncio.sleep(_ANDROID_QUEUE_POLL_SECONDS)

        await session_store.update(
            session_id,
            status=SessionStatus.RUNNING,
            started_at=started_at_ts,
            finished_at=None,
            error=None,
            mode="android",
            queue_reason=None,
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
            pass
        heartbeat_task = asyncio.create_task(
            _android_session_heartbeat(
                session_id=session_id,
                session_store=session_store,
                stop_event=heartbeat_stop,
            )
        )

        async def on_progress(**kwargs: object) -> None:
            nonlocal _last_persisted_ads_count
            event = kwargs.pop("event", None)

            ads = kwargs.get("watched_ads")
            if isinstance(ads, list):
                current_payload = await session_store.get(session_id) or {}
                merged_ads = merge_live_watched_ads(
                    current_ads=current_payload.get("watched_ads") or [],
                    next_ads=ads,
                )
                kwargs["watched_ads"] = merged_ads
                kwargs["watched_ads_count"] = len(merged_ads)
                kwargs["watched_ads_analytics"] = build_ads_analytics(merged_ads)

            # Always sync to Redis for SSE
            await session_store.update(session_id, **kwargs)

            # On ad_captured — persist new captures and queue analysis immediately.
            # On ad_updated — persist refreshed capture media (for example landing_dir)
            # without waiting for the session to finish.
            if event in {"ad_captured", "ad_updated"}:
                ads = kwargs.get("watched_ads") or []
                if len(ads) > _last_persisted_ads_count:
                    new_ads_count = len(ads) - _last_persisted_ads_count
                    try:
                        await persistence.persist_ad_captures(
                            session_id=session_id,
                            watched_ads=ads,
                            from_index=_last_persisted_ads_count,
                        )
                        _last_persisted_ads_count = len(ads)
                    except Exception:
                        pass
                    if event == "ad_captured":
                        try:
                            from app.services.emulation.workflow.progress import queue_ad_analysis

                            await queue_ad_analysis(
                                session_id=session_id,
                                session_store=session_store,
                                ad_analysis_service_available=True,
                                total_hint=max(new_ads_count, 1),
                            )
                        except Exception:
                            pass
                elif event == "ad_updated" and ads:
                    try:
                        await persistence.persist_ad_captures(
                            session_id=session_id,
                            watched_ads=ads,
                            from_index=0,
                        )
                    except Exception:
                        pass

        try:
            runner = AndroidYouTubeSessionRunner(config)
            result = await runner.run(
                topics=topics,
                duration_minutes=duration_minutes,
                proxy_url=proxy_url,
                headless=headless,
                on_progress=on_progress,
            )

            raw_ads = result.watched_ads or []
            watched_ads = [
                {**ad, "position": idx + 1}
                for idx, ad in enumerate(raw_ads)
            ]
            topics_searched = [tr.topic for tr in result.topic_results]
            verified_count = sum(1 for tr in result.topic_results if tr.watch_verified)
            watched_videos = [
                build_topic_watched_video_payload(
                    tr,
                    position=idx + 1,
                    recorded_at=time.time(),
                )
                for idx, tr in enumerate(result.topic_results)
            ]

            await session_store.update(
                session_id,
                status=SessionStatus.COMPLETED,
                finished_at=time.time(),
                current_watch=None,
                watched_ads=watched_ads,
                watched_ads_count=len(watched_ads),
                topics_searched=topics_searched,
                total_duration_seconds=result.elapsed_seconds,
                videos_watched=verified_count,
                watched_videos_count=verified_count,
                watched_videos=watched_videos,
                bytes_downloaded=result.bytes_downloaded,
                mode="android",
            )

            live_payload = await session_store.get(session_id) or {}
            # Re-sync captures from the final Android result after background
            # landing scraping / analysis / dedup so DB rows reflect the final state.
            try:
                await persistence.persist_ad_captures(
                    session_id=session_id,
                    watched_ads=watched_ads,
                    from_index=0,
                    prune_missing=True,
                )
            except Exception:
                pass
            try:
                await persistence.persist_history_completed(
                    session_id=session_id,
                    duration_minutes=duration_minutes,
                    topics=topics,
                    bytes_downloaded=result.bytes_downloaded,
                    topics_searched=topics_searched,
                    videos_watched=verified_count,
                    watched_videos=watched_videos,
                    watched_ads=watched_ads,
                    total_duration_seconds=result.elapsed_seconds,
                    live_payload=live_payload,
                )
            except Exception:
                pass

            # Final analysis pass for any remaining ads
            if watched_ads:
                try:
                    from app.services.emulation.workflow.progress import queue_ad_analysis

                    await queue_ad_analysis(
                        session_id=session_id,
                        session_store=session_store,
                        ad_analysis_service_available=True,
                        total_hint=len(watched_ads),
                    )
                except Exception:
                    pass

            return {"status": SessionStatus.COMPLETED, "session_id": session_id}

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            await session_store.update(
                session_id,
                status=SessionStatus.FAILED,
                finished_at=time.time(),
                error=error_msg,
            )
            live_payload = await session_store.get(session_id) or {}
            try:
                await persistence.persist_history_failed(
                    session_id=session_id,
                    duration_minutes=duration_minutes,
                    topics=topics,
                    error=error_msg,
                    live_payload=live_payload,
                )
            except Exception:
                traceback.print_exc()
            raise
    finally:
        heartbeat_stop.set()
        if heartbeat_task is not None:
            try:
                await heartbeat_task
            except Exception:
                logger.exception("Android session %s: heartbeat task shutdown failed", session_id)
        if device_lock_acquired:
            await session_store.release_profile_lock(device_lock_id, device_lock_holder)
        await session_store.release_run_lock(session_id, run_holder)
