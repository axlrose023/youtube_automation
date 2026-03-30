from __future__ import annotations

from app.database.uow import UnitOfWork

from .captures import CapturePersistenceService
from .history import HistoryPersistenceService


class EmulationPersistenceService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._captures = CapturePersistenceService(uow)
        self._history = HistoryPersistenceService(uow)

    async def persist_ad_captures(
        self,
        session_id: str,
        watched_ads: list[dict],
        from_index: int = 0,
    ) -> None:
        await self._captures.persist_ad_captures(
            session_id=session_id,
            watched_ads=watched_ads,
            from_index=from_index,
        )

    async def persist_history(self, **kwargs) -> None:
        await self._history.persist_history(**kwargs)

    async def persist_history_running(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        await self._history.persist_history_running(
            session_id=session_id,
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
        await self._history.persist_history_completed(
            session_id=session_id,
            duration_minutes=duration_minutes,
            topics=topics,
            bytes_downloaded=bytes_downloaded,
            topics_searched=topics_searched,
            videos_watched=videos_watched,
            watched_videos=watched_videos,
            watched_ads=watched_ads,
            total_duration_seconds=total_duration_seconds,
            live_payload=live_payload,
        )

    async def persist_history_completed_from_live_payload(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        await self._history.persist_history_completed_from_live_payload(
            session_id=session_id,
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
        await self._history.persist_history_failed(
            session_id=session_id,
            duration_minutes=duration_minutes,
            topics=topics,
            error=error,
            live_payload=live_payload,
        )

    async def rollback(self) -> None:
        await self._uow.rollback()
