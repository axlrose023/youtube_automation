from __future__ import annotations

from datetime import UTC, datetime

from app.api.modules.emulation.models import SessionStatus
from app.database.uow import UnitOfWork
from app.services.emulation.common import derive_watched_video_counters, to_utc_datetime
from app.services.emulation.core.ad_analytics import build_ads_analytics


class HistoryPersistenceService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def persist_history(
        self,
        *,
        session_id: str,
        status: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
        error: str | None = None,
        bytes_downloaded: int | None = None,
        topics_searched: list[str] | None = None,
        videos_watched: int | None = None,
        watched_videos: list[dict] | None = None,
        watched_ads: list[dict] | None = None,
        total_duration_seconds: int | None = None,
    ) -> None:
        lp_watched_videos = live_payload.get("watched_videos") or []
        lp_watched_ads = live_payload.get("watched_ads") or []

        final_watched_videos = watched_videos if watched_videos is not None else lp_watched_videos
        final_watched_ads = watched_ads if watched_ads is not None else lp_watched_ads
        final_bytes = bytes_downloaded if bytes_downloaded is not None else _as_int(live_payload.get("bytes_downloaded"))
        final_topics = topics_searched if topics_searched is not None else (live_payload.get("topics_searched") or [])
        final_total_duration = total_duration_seconds if total_duration_seconds is not None else _as_int(live_payload.get("total_duration_seconds"))
        final_videos_watched, final_watched_videos_count = derive_watched_video_counters(
            final_watched_videos,
            fallback_completed=(
                videos_watched if videos_watched is not None else _as_int(live_payload.get("videos_watched"))
            ),
            fallback_total=_as_int(live_payload.get("watched_videos_count")),
        )

        ads_analytics = live_payload.get("watched_ads_analytics")
        if not ads_analytics:
            ads_analytics = build_ads_analytics(final_watched_ads)

        finished_at = None
        if status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.STOPPED):
            finished_at = to_utc_datetime(live_payload.get("finished_at")) or _utcnow()

        await self._uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
            queued_at=to_utc_datetime(live_payload.get("created_at")) or _utcnow(),
        )
        await self._uow.emulation_history.update_session(
            session_id,
            status=status,
            started_at=to_utc_datetime(live_payload.get("started_at")) or _utcnow(),
            finished_at=finished_at,
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            current_topic=live_payload.get("current_topic"),
            personality=live_payload.get("personality"),
            bytes_downloaded=final_bytes,
            topics_searched=final_topics,
            videos_watched=final_videos_watched,
            watched_videos_count=final_watched_videos_count,
            watched_videos=final_watched_videos,
            watched_ads_count=max(
                _as_int(live_payload.get("watched_ads_count")),
                len(final_watched_ads),
            ),
            watched_ads=final_watched_ads,
            watched_ads_analytics=ads_analytics,
            total_duration_seconds=final_total_duration,
            error=error,
        )
        await self._uow.commit()

    async def persist_history_running(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        await self.persist_history(
            session_id=session_id,
            status=SessionStatus.RUNNING,
            duration_minutes=duration_minutes,
            topics=topics,
            live_payload=live_payload,
        )

    async def persist_history_completed(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        bytes_downloaded: int,
        topics_searched: list[str],
        videos_watched: int,
        watched_videos: list[dict],
        watched_ads: list[dict],
        total_duration_seconds: int,
        live_payload: dict,
    ) -> None:
        await self.persist_history(
            session_id=session_id,
            status=SessionStatus.COMPLETED,
            duration_minutes=duration_minutes,
            topics=topics,
            live_payload=live_payload,
            bytes_downloaded=bytes_downloaded,
            topics_searched=topics_searched,
            videos_watched=videos_watched,
            watched_videos=watched_videos,
            watched_ads=watched_ads,
            total_duration_seconds=total_duration_seconds,
        )

    async def persist_history_completed_from_live_payload(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        await self.persist_history(
            session_id=session_id,
            status=SessionStatus.COMPLETED,
            duration_minutes=duration_minutes,
            topics=topics,
            live_payload=live_payload,
        )

    async def persist_history_failed(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        error: str,
        live_payload: dict,
    ) -> None:
        await self.persist_history(
            session_id=session_id,
            status=SessionStatus.FAILED,
            duration_minutes=duration_minutes,
            topics=topics,
            live_payload=live_payload,
            error=error,
        )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _utcnow() -> datetime:
    return datetime.now(UTC)
