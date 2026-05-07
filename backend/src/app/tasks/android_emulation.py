from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback
import uuid
from pathlib import Path

from dishka import FromDishka
from dishka.integrations.taskiq import inject
from redis.asyncio import Redis

from app.api.modules.emulation.models import SessionStatus
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.config import ORCHESTRATION_RUN_LOCK_TTL_SECONDS
from app.services.emulation.persistence import EmulationPersistenceService
from app.services.emulation.session.store import EmulationSessionStore, merge_live_watched_ads
from app.services.emulation.standalone_mapper import build_standalone_live_payload
from app.services.mobile_app.android.avd_manager import AndroidEmulatorLaunchOptions
from app.services.mobile_app.android.config_ui import (
    ANDROID_CONFIG_SESSION_ID,
    android_config_snapshot_name,
    load_android_config_state,
    patch_android_config_state,
)
from app.services.mobile_app.android.runtime import build_android_probe_runtime
from app.settings import Config
from app.tiq import broker

logger = logging.getLogger(__name__)

_ANDROID_QUEUE_POLL_SECONDS = 5
_ANDROID_HEARTBEAT_SECONDS = 5
_ANDROID_CONFIG_TIMEOUT_SECONDS = 8 * 60 * 60


def _android_device_lock_id(config: Config) -> str:
    avd_name = (config.android_app.default_avd_name or "").strip() or "default"
    return f"android-device:{avd_name}"


def _project_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (*current.parents, Path.cwd()):
        if (candidate / "standalone_topic_runner").is_dir():
            return candidate
    return current.parents[3]


def _ensure_project_root_on_path() -> None:
    project_root = str(_project_root())
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _standalone_run_dir(config: Config, session_id: str) -> Path:
    run_ts = time.strftime("%Y%m%d_%H%M%S")
    return (
        config.storage.base_path
        / config.android_app.session_artifacts_subdir
        / session_id
        / f"run_{run_ts}"
    )


async def _android_stop_watcher(
    session_id: str,
    session_store: EmulationSessionStore,
    runner_stop_event: asyncio.Event,
    heartbeat_stop: asyncio.Event,
) -> None:
    """Polls Redis every 3 s; sets runner_stop_event when session moves to STOPPING."""
    while not heartbeat_stop.is_set():
        try:
            payload = await session_store.get(session_id)
            if payload is not None and payload.get("status") == SessionStatus.STOPPING:
                logger.info("Android session %s: stop_watcher detected STOPPING — signalling runner", session_id)
                runner_stop_event.set()
                return
        except Exception:
            pass
        try:
            await asyncio.wait_for(heartbeat_stop.wait(), timeout=3)
        except TimeoutError:
            continue


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


@broker.task(task_name="android_config_ui_task", timeout=28800)
@inject
async def android_config_ui_task(
    holder: str,
    session_store: FromDishka[EmulationSessionStore],
    redis: FromDishka[Redis],
    config: FromDishka[Config],
) -> dict:
    device_lock_id = _android_device_lock_id(config)
    lock_holder = f"{ANDROID_CONFIG_SESSION_ID}:{holder}"
    avd_name = (
        config.android_app.bootstrap_avd_name
        or config.android_app.default_avd_name
    )
    snapshot_name = android_config_snapshot_name(
        config.android_app.runtime_snapshot_name,
        config.android_app.warm_snapshot_name,
    )
    runtime = build_android_probe_runtime(config.android_app)
    run_lock_acquired = False
    device_lock_acquired = False
    serial: str | None = None
    saved_snapshot = False
    started_at = time.monotonic()

    async def _state_matches() -> bool:
        state = await load_android_config_state(redis)
        return bool(state and state.get("holder") == holder)

    try:
        await session_store.create(
            ANDROID_CONFIG_SESSION_ID,
            ["android_ui_config"],
            duration_minutes=max(1, _ANDROID_CONFIG_TIMEOUT_SECONDS // 60),
            profile_id=None,
        )
        run_lock_acquired = await session_store.try_acquire_run_lock(
            session_id=ANDROID_CONFIG_SESSION_ID,
            holder=lock_holder,
            ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
        )
        if not run_lock_acquired:
            await patch_android_config_state(
                redis,
                status="failed",
                error="Android config UI task is already running",
                finished_at=time.time(),
            )
            return {"status": "already_running"}

        while True:
            if not await _state_matches():
                return {"status": "superseded"}
            state = await load_android_config_state(redis) or {}
            if state.get("stop_requested"):
                await patch_android_config_state(
                    redis,
                    status="stopped",
                    finished_at=time.time(),
                    message="Stopped before Android device became available",
                )
                return {"status": "stopped"}

            device_lock_acquired = await session_store.try_acquire_profile_lock(
                profile_id=device_lock_id,
                holder=lock_holder,
                ttl_seconds=ORCHESTRATION_RUN_LOCK_TTL_SECONDS,
            )
            if device_lock_acquired:
                break
            await patch_android_config_state(
                redis,
                status="queued",
                queue_reason=f"Waiting for Android device slot {device_lock_id}",
            )
            await asyncio.sleep(_ANDROID_QUEUE_POLL_SECONDS)

        await session_store.update(
            ANDROID_CONFIG_SESSION_ID,
            status=SessionStatus.RUNNING,
            mode="android_config",
            started_at=time.time(),
            finished_at=None,
            error=None,
        )
        await patch_android_config_state(
            redis,
            status="starting",
            started_at=time.time(),
            snapshot_name=snapshot_name,
            error=None,
        )

        snapshot_exists = await runtime.avd_manager.snapshot_exists(avd_name, snapshot_name)
        device = await runtime.avd_manager.ensure_device(
            avd_name=avd_name,
            launch=AndroidEmulatorLaunchOptions(
                headless=False,
                gpu_mode=config.android_app.bootstrap_emulator_gpu_mode,
                accel_mode=config.android_app.bootstrap_emulator_accel_mode,
                load_snapshot=not snapshot_exists,
                save_snapshot=False,
                snapshot_name=snapshot_name if snapshot_exists else None,
                force_snapshot_load=snapshot_exists,
                skip_adb_auth=config.android_app.emulator_skip_adb_auth,
                force_stop_running=True,
            ),
        )
        serial = device.adb_serial
        await patch_android_config_state(redis, status="running", serial=serial)

        while True:
            if not await _state_matches():
                break
            elapsed = time.monotonic() - started_at
            state = await load_android_config_state(redis) or {}
            await session_store.update(
                ANDROID_CONFIG_SESSION_ID,
                status=SessionStatus.RUNNING,
                mode="android_config",
            )
            if state.get("finish_requested"):
                await patch_android_config_state(redis, status="saving")
                await runtime.avd_manager.save_snapshot(serial, snapshot_name)
                saved_snapshot = True
                break
            if state.get("stop_requested"):
                await patch_android_config_state(redis, status="stopping")
                break
            if elapsed >= _ANDROID_CONFIG_TIMEOUT_SECONDS:
                await patch_android_config_state(
                    redis,
                    status="stopping",
                    message="Android config UI timed out",
                )
                break
            await asyncio.sleep(2.0)

        if serial is not None:
            await patch_android_config_state(redis, status="stopping")
            try:
                await runtime.avd_manager.stop_device(serial, avd_name=avd_name)
            except Exception:
                await runtime.avd_manager.force_cleanup_device(
                    adb_serial=serial,
                    avd_name=avd_name,
                )

        await patch_android_config_state(
            redis,
            status="stopped",
            serial=None,
            snapshot_name=snapshot_name if saved_snapshot else None,
            snapshot_saved=saved_snapshot,
            finished_at=time.time(),
            error=None,
        )
        await session_store.update(
            ANDROID_CONFIG_SESSION_ID,
            status=SessionStatus.STOPPED,
            finished_at=time.time(),
            error=None,
        )
        return {
            "status": "stopped",
            "snapshot_saved": saved_snapshot,
            "snapshot_name": snapshot_name if saved_snapshot else None,
        }
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Android config UI task failed")
        await patch_android_config_state(
            redis,
            status="failed",
            serial=None,
            error=error_msg,
            finished_at=time.time(),
        )
        await session_store.update(
            ANDROID_CONFIG_SESSION_ID,
            status=SessionStatus.FAILED,
            finished_at=time.time(),
            error=error_msg,
        )
        if serial is not None:
            try:
                await runtime.avd_manager.force_cleanup_device(
                    adb_serial=serial,
                    avd_name=avd_name,
                )
            except Exception:
                logger.exception("Android config UI cleanup failed")
        raise
    finally:
        if device_lock_acquired:
            await session_store.release_profile_lock(device_lock_id, lock_holder)
        if run_lock_acquired:
            await session_store.release_run_lock(ANDROID_CONFIG_SESSION_ID, lock_holder)


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
    _ensure_project_root_on_path()
    from standalone_topic_runner.runner import (
        StandaloneProgressEvent,
        StandaloneRunOptions,
        run_standalone_session,
    )

    run_holder = f"{session_id}:{uuid.uuid4().hex}"
    device_lock_id = _android_device_lock_id(config)
    device_lock_holder = f"{run_holder}:android-device"
    _last_persisted_ads_count = 0
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task[None] | None = None
    runner_stop_event = asyncio.Event()
    stop_watcher_task: asyncio.Task[None] | None = None

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

        started_at_ts = time.time()
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
        stop_watcher_task = asyncio.create_task(
            _android_stop_watcher(
                session_id=session_id,
                session_store=session_store,
                runner_stop_event=runner_stop_event,
                heartbeat_stop=heartbeat_stop,
            )
        )

        async def on_progress(event: StandaloneProgressEvent) -> None:
            nonlocal _last_persisted_ads_count
            mapped = build_standalone_live_payload(
                topic_records=event.topics,
                run_dir=event.run_dir,
                storage_base=config.storage.base_path,
                recorded_at=time.time(),
            )
            watched_ads = mapped.watched_ads
            current_payload = await session_store.get(session_id) or {}
            merged_ads = merge_live_watched_ads(
                current_ads=current_payload.get("watched_ads") or [],
                next_ads=watched_ads,
            )

            current_watch = current_payload.get("current_watch")
            if event.event == "video_opened" and event.topic_record is not None:
                opened_videos = getattr(event.topic_record, "opened_videos", []) or []
                latest_video = opened_videos[-1] if opened_videos else None
                if isinstance(latest_video, dict):
                    current_watch = {
                        "action": "watch",
                        "title": latest_video.get("title") or event.topic or "",
                        "url": "",
                        "started_at": time.time(),
                        "watched_seconds": 0.0,
                        "target_seconds": None,
                        "search_keyword": event.topic,
                        "matched_topics": [event.topic] if event.topic else [],
                        "keywords": [],
                    }
            elif event.event == "topic_finished":
                current_watch = None

            # Always sync to Redis for SSE
            await session_store.update(
                session_id,
                current_topic=event.topic,
                current_watch=current_watch,
                topics_searched=mapped.topics_searched,
                watched_ads=merged_ads,
                watched_ads_count=len(merged_ads),
                watched_ads_analytics=build_ads_analytics(merged_ads),
                watched_videos=mapped.watched_videos,
                watched_videos_count=len(mapped.watched_videos),
                videos_watched=sum(
                    1 for video in mapped.watched_videos if video.get("completed")
                ),
                total_duration_seconds=int(round(mapped.total_watch_seconds)),
                mode="android",
            )

            # Persist newly surfaced standalone captures during the run so SSE
            # and history/captures stay useful even before final completion.
            if event.event in {"ad_captured", "banner_captured"}:
                if len(merged_ads) > _last_persisted_ads_count:
                    new_ads_count = len(merged_ads) - _last_persisted_ads_count
                    try:
                        await persistence.persist_ad_captures(
                            session_id=session_id,
                            watched_ads=merged_ads,
                            from_index=_last_persisted_ads_count,
                        )
                        _last_persisted_ads_count = len(merged_ads)
                    except Exception:
                        pass
                    if event.event == "ad_captured":
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

        try:
            run_dir = _standalone_run_dir(config, session_id)
            result = await run_standalone_session(
                StandaloneRunOptions(
                    topics=topics,
                    max_watch_seconds=float(duration_minutes * 60),
                    scroll_rounds=20,
                    ad_record_seconds=30.0,
                    avd_name=config.android_app.default_avd_name,
                    manage_appium=config.android_app.manage_appium_server,
                    headless=headless,
                    proxy_url=proxy_url,
                    run_dir=run_dir,
                    android_config=config.android_app,
                    stop_event=runner_stop_event,
                    on_progress=on_progress,
                )
            )

            mapped = build_standalone_live_payload(
                topic_records=result.topics,
                run_dir=result.run_dir,
                storage_base=config.storage.base_path,
                recorded_at=time.time(),
            )
            watched_ads = mapped.watched_ads
            watched_videos = mapped.watched_videos
            topics_searched = mapped.topics_searched
            completed_videos = sum(
                1 for video in watched_videos if video.get("completed")
            )
            total_watch_seconds = int(round(mapped.total_watch_seconds))

            if runner_stop_event.is_set():
                await session_store.update(
                    session_id,
                    status=SessionStatus.STOPPED,
                    finished_at=time.time(),
                    current_watch=None,
                    watched_ads=watched_ads,
                    watched_ads_count=len(watched_ads),
                    watched_ads_analytics=build_ads_analytics(watched_ads),
                    topics_searched=topics_searched,
                    total_duration_seconds=total_watch_seconds,
                    videos_watched=completed_videos,
                    watched_videos_count=len(watched_videos),
                    watched_videos=watched_videos,
                    bytes_downloaded=0,
                    mode="android",
                    error="Stopped by user",
                    queue_reason=None,
                )
                live_payload = await session_store.get(session_id) or {}
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
                    await persistence.persist_history(
                        session_id=session_id,
                        status=SessionStatus.STOPPED,
                        duration_minutes=duration_minutes,
                        topics=topics,
                        bytes_downloaded=0,
                        topics_searched=topics_searched,
                        videos_watched=completed_videos,
                        watched_videos=watched_videos,
                        watched_ads=watched_ads,
                        total_duration_seconds=total_watch_seconds,
                        live_payload=live_payload,
                        error="Stopped by user",
                    )
                except Exception:
                    pass
                return {"status": SessionStatus.STOPPED, "session_id": session_id}

            await session_store.update(
                session_id,
                status=SessionStatus.COMPLETED,
                finished_at=time.time(),
                current_watch=None,
                watched_ads=watched_ads,
                watched_ads_count=len(watched_ads),
                watched_ads_analytics=build_ads_analytics(watched_ads),
                topics_searched=topics_searched,
                total_duration_seconds=total_watch_seconds,
                videos_watched=completed_videos,
                watched_videos_count=len(watched_videos),
                watched_videos=watched_videos,
                bytes_downloaded=0,
                mode="android",
            )

            live_payload = await session_store.get(session_id) or {}
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
                    bytes_downloaded=0,
                    topics_searched=topics_searched,
                    videos_watched=completed_videos,
                watched_videos=watched_videos,
                watched_ads=watched_ads,
                total_duration_seconds=total_watch_seconds,
                live_payload=live_payload,
            )
            except Exception:
                pass

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
        if stop_watcher_task is not None:
            try:
                await stop_watcher_task
            except Exception:
                pass
        if device_lock_acquired:
            await session_store.release_profile_lock(device_lock_id, device_lock_holder)
        await session_store.release_run_lock(session_id, run_holder)
