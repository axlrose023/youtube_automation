from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from taskiq.kicker import AsyncKicker

from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.core.orchestration import (
    clamp_non_negative_int,
    pick_break_seconds,
    remaining_window_seconds,
    should_finalize_window,
)
from app.services.emulation.core.session.store import EmulationSessionStore
from app.services.emulation.core.session.state import EmulationResult
from app.services.emulation.persistence import EmulationPersistenceService

logger = logging.getLogger(__name__)


class EmulationOrchestrationService:
    def __init__(
        self,
        session_store: EmulationSessionStore,
        persistence: EmulationPersistenceService,
    ) -> None:
        self._store = session_store
        self._persistence = persistence

    async def complete_or_schedule_next_chunk(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        profile_id: str | None,
        result: EmulationResult,
        orchestration: dict[str, object],
        current_mode: object,
        current_fatigue: object,
        current_personality: object,
    ) -> dict:
        spent_before = clamp_non_negative_int(orchestration.get("active_spent_seconds"))
        active_budget = clamp_non_negative_int(orchestration.get("active_budget_seconds"))
        spent_after = min(active_budget, spent_before + max(result.total_duration_seconds, 0))

        orchestration["active_spent_seconds"] = spent_after
        orchestration["persisted_ads_count"] = len(result.watched_ads)
        orchestration["chunk_index"] = clamp_non_negative_int(orchestration.get("chunk_index")) + 1
        orchestration["last_chunk_seconds"] = max(result.total_duration_seconds, 0)
        orchestration["phase"] = "running"
        orchestration["next_resume_at"] = None

        now_ts = time.time()
        remaining_window = remaining_window_seconds(orchestration, now_ts)
        should_complete = should_finalize_window(
            orchestration,
            spent_after,
            now_ts,
        )

        if should_complete:
            orchestration["phase"] = "completed"
            await self._store.update(
                session_id,
                status="completed",
                finished_at=now_ts,
                bytes_downloaded=result.bytes_downloaded,
                topics_searched=result.topics_searched,
                videos_watched=result.videos_watched,
                watched_videos_count=len(result.watched_videos),
                watched_videos=result.watched_videos,
                watched_ads_count=len(result.watched_ads),
                watched_ads=result.watched_ads,
                watched_ads_analytics=build_ads_analytics(result.watched_ads),
                total_duration_seconds=spent_after,
                mode=current_mode,
                fatigue=current_fatigue,
                personality=current_personality,
                orchestration=orchestration,
            )
            live_payload = await self._store.get(session_id) or {}
            try:
                await self._persistence.persist_history_completed(
                    session_id,
                    duration_minutes,
                    topics,
                    result.bytes_downloaded,
                    result.topics_searched,
                    result.videos_watched,
                    result.watched_videos,
                    result.watched_ads,
                    spent_after,
                    live_payload,
                )
            except Exception:
                logger.exception("Session %s: failed to persist completed history", session_id)
                await self._persistence.rollback()

            logger.info(
                "Session %s completed orchestration window: active_spent=%ss/%ss",
                session_id,
                spent_after,
                active_budget,
            )
            return {"status": "completed", "session_id": session_id}

        break_seconds = pick_break_seconds(orchestration, now_ts)
        resume_at_ts = now_ts + break_seconds
        orchestration["phase"] = "break"
        orchestration["next_resume_at"] = resume_at_ts
        orchestration["last_break_seconds"] = break_seconds

        await self._store.update(
            session_id,
            status="running",
            bytes_downloaded=result.bytes_downloaded,
            topics_searched=result.topics_searched,
            videos_watched=result.videos_watched,
            watched_videos_count=len(result.watched_videos),
            watched_videos=result.watched_videos,
            watched_ads_count=len(result.watched_ads),
            watched_ads=result.watched_ads,
            watched_ads_analytics=build_ads_analytics(result.watched_ads),
            total_duration_seconds=spent_after,
            mode=current_mode,
            fatigue=current_fatigue,
            personality=current_personality,
            orchestration=orchestration,
            finished_at=None,
        )
        live_payload = await self._store.get(session_id) or {}
        try:
            await self._persistence.persist_history_running(
                session_id,
                duration_minutes,
                topics,
                live_payload,
            )
        except Exception:
            logger.exception("Session %s: failed to persist running history", session_id)
            await self._persistence.rollback()

        await self._schedule_next_chunk(
            session_id,
            duration_minutes,
            topics,
            profile_id,
            resume_at_ts,
        )
        logger.info(
            "Session %s: scheduled next chunk in %ss (spent=%ss/%ss, remaining_window=%ss)",
            session_id,
            break_seconds,
            spent_after,
            active_budget,
            remaining_window,
        )
        return {"status": "scheduled", "session_id": session_id}

    async def finalize_without_run(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        orchestration: dict[str, object],
    ) -> dict:
        orchestration["phase"] = "completed"
        orchestration["next_resume_at"] = None

        live_payload = await self._store.get(session_id) or {}
        watched_videos = live_payload.get("watched_videos") or []
        watched_ads = live_payload.get("watched_ads") or []

        await self._store.update(
            session_id,
            status="completed",
            finished_at=time.time(),
            watched_videos_count=len(watched_videos),
            watched_ads_count=len(watched_ads),
            watched_ads_analytics=build_ads_analytics(watched_ads),
            orchestration=orchestration,
        )
        live_payload = await self._store.get(session_id) or {}
        try:
            await self._persistence.persist_history_completed_from_live_payload(
                session_id,
                duration_minutes,
                topics,
                live_payload,
            )
        except Exception:
            logger.exception("Session %s: failed to persist completion without run", session_id)
            await self._persistence.rollback()

        return {"status": "completed", "session_id": session_id}

    async def _schedule_next_chunk(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        profile_id: str | None,
        resume_at_ts: float,
    ) -> None:
        from app.tiq import EMULATION_QUEUE_NAME, broker, dynamic_schedule_source

        resume_at = datetime.fromtimestamp(resume_at_ts, tz=UTC)
        await AsyncKicker(
            broker=broker,
            task_name="emulation_task",
            labels={"queue_name": EMULATION_QUEUE_NAME},
        ).schedule_by_time(
            source=dynamic_schedule_source,
            time=resume_at,
            session_id=session_id,
            duration_minutes=duration_minutes,
            topics=topics,
            profile_id=profile_id,
        )
