from __future__ import annotations

import time

from app.api.modules.emulation.models import SessionStatus
from app.services.emulation.common import derive_watched_video_counters
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.session.store import EmulationSessionStore


async def finalize_completed(
    session_id: str,
    session_store: EmulationSessionStore,
    result,
    current_mode: object,
    current_fatigue: object,
    current_personality: object,
    finished_at: float,
) -> None:
    completed_videos, total_videos = derive_watched_video_counters(result.watched_videos)
    await session_store.update(
        session_id,
        status=SessionStatus.COMPLETED,
        stop_requested=False,
        finished_at=finished_at,
        bytes_downloaded=result.bytes_downloaded,
        topics_searched=result.topics_searched,
        videos_watched=completed_videos,
        watched_videos_count=total_videos,
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


async def finalize_stopped(
    session_id: str,
    session_store: EmulationSessionStore,
    result,
    current_mode: object,
    current_fatigue: object,
    current_personality: object,
) -> None:
    current_payload = await session_store.get(session_id) or {}
    finished_at = current_payload.get("finished_at")
    if not isinstance(finished_at, (int, float)):
        finished_at = time.time()
    completed_videos, total_videos = derive_watched_video_counters(result.watched_videos)

    await session_store.update(
        session_id,
        status=SessionStatus.STOPPED,
        stop_requested=False,
        finished_at=finished_at,
        current_watch=None,
        bytes_downloaded=result.bytes_downloaded,
        topics_searched=result.topics_searched,
        videos_watched=completed_videos,
        watched_videos_count=total_videos,
        watched_videos=result.watched_videos,
        watched_ads_count=len(result.watched_ads),
        watched_ads=result.watched_ads,
        watched_ads_analytics=build_ads_analytics(result.watched_ads),
        total_duration_seconds=result.total_duration_seconds,
        mode=current_mode,
        fatigue=current_fatigue,
        personality=current_personality,
        orchestration=None,
        error="Stopped by user",
    )
