from __future__ import annotations

import datetime
import uuid

from fastapi import HTTPException
from taskiq.kicker import AsyncKicker

from app.database.uow import UnitOfWork
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.core.session_store import EmulationSessionStore

from .gateway import EmulationHistoryQuery
from .models import EmulationSessionHistory
from .schema import (
    EmulationAdCaptureHistory,
    EmulationCaptureSummary,
    EmulationHistoryDetailResponse,
    EmulationHistoryItem,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
)
from .utils import (
    build_capture_summary,
    calculate_history_elapsed_minutes,
    calculate_session_elapsed_minutes,
    map_ad_capture,
    normalized_ads_count,
    normalized_videos_count,
)


class EmulationSessionService:
    def __init__(
        self,
        session_store: EmulationSessionStore,
        history_service: EmulationHistoryService,
    ) -> None:
        self._session_store = session_store
        self._history_service = history_service

    async def start_emulation(self, request: StartEmulationRequest) -> StartEmulationResponse:
        session_id = str(uuid.uuid4())
        profile_id = self._normalize_profile_id(request.profile_id)
        await self._session_store.create(
            session_id,
            request.topics,
            request.duration_minutes,
            profile_id=profile_id,
        )
        await self._history_service.register_queued_session(
            session_id=session_id,
            duration_minutes=request.duration_minutes,
            topics=request.topics,
        )

        try:
            from app.tiq import EMULATION_QUEUE_NAME, broker

            await AsyncKicker(
                broker=broker,
                task_name="emulation_task",
                labels={"queue_name": EMULATION_QUEUE_NAME},
            ).kiq(
                session_id,
                request.duration_minutes,
                request.topics,
                profile_id=profile_id,
            )
        except Exception as exc:
            await self._session_store.update(
                session_id,
                status="failed",
                finished_at=datetime.datetime.now(datetime.UTC).timestamp(),
                error=str(exc),
            )
            await self._history_service.mark_enqueue_failed(session_id=session_id, error=str(exc))
            raise HTTPException(status_code=500, detail="Failed to queue emulation task") from exc

        return StartEmulationResponse(session_id=session_id, status="queued")

    async def get_status(self, session_id: str) -> EmulationSessionStatus:
        data = await self._session_store.get(session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Session not found")

        orchestration = data.get("orchestration")
        if not isinstance(orchestration, dict):
            orchestration = {}

        return EmulationSessionStatus(
            session_id=session_id,
            status=data["status"],
            profile_id=self._normalize_profile_id(data.get("profile_id")),
            elapsed_minutes=calculate_session_elapsed_minutes(data),
            orchestration_enabled=bool(orchestration.get("enabled")),
            orchestration_phase=(
                str(orchestration.get("phase")) if orchestration.get("phase") else None
            ),
            next_resume_at=(
                float(orchestration["next_resume_at"])
                if isinstance(orchestration.get("next_resume_at"), int | float)
                else None
            ),
            active_budget_seconds=(
                int(orchestration["active_budget_seconds"])
                if isinstance(orchestration.get("active_budget_seconds"), int | float)
                else None
            ),
            active_spent_seconds=(
                int(orchestration["active_spent_seconds"])
                if isinstance(orchestration.get("active_spent_seconds"), int | float)
                else None
            ),
            bytes_downloaded=data.get("bytes_downloaded", 0),
            topics_searched=data.get("topics_searched", []),
            videos_watched=data.get("videos_watched", 0),
            watched_videos_count=data.get("watched_videos_count", 0),
            total_duration_seconds=data.get("total_duration_seconds", 0),
            watched_videos=data.get("watched_videos", []),
            watched_ads_count=data.get("watched_ads_count", 0),
            watched_ads=data.get("watched_ads", []),
            watched_ads_analytics=data.get("watched_ads_analytics")
            or build_ads_analytics(data.get("watched_ads", [])),
            mode=data.get("mode"),
            fatigue=data.get("fatigue"),
            error=data.get("error"),
        )

    @staticmethod
    def _normalize_profile_id(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None


class EmulationHistoryService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def register_queued_session(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
    ) -> None:
        await self.uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
        )
        await self.uow.commit()

    async def mark_enqueue_failed(self, session_id: str, error: str) -> None:
        await self.uow.emulation_history.update_session(
            session_id,
            status="failed",
            finished_at=datetime.datetime.now(datetime.UTC),
            error=error,
        )
        await self.uow.commit()

    async def get_history(
        self,
        params: EmulationHistoryParams,
    ) -> EmulationHistoryResponse:
        query = EmulationHistoryQuery(
            session_id=str(params.session_id) if params.session_id else None,
            status=params.status,
            mode=params.mode,
            topic_search=params.topic__search,
            has_ads=params.has_ads,
            has_video_capture=params.has_video_capture,
            has_screenshot_capture=params.has_screenshot_capture,
            queued_from=params.queued_from,
            queued_to=params.queued_to,
            started_from=params.started_from,
            started_to=params.started_to,
            finished_from=params.finished_from,
            finished_to=params.finished_to,
        )
        total = await self.uow.emulation_history.get_total_count(query)
        rows = await self.uow.emulation_history.get_history(
            query=query,
            limit=params.page_size,
            offset=params.offset,
        )

        captures_by_session: dict[str, list[EmulationAdCaptureHistory]] = {}
        if params.include_captures and rows:
            raw_captures = await self.uow.emulation_history.get_ad_captures_by_sessions(
                [row.session.session_id for row in rows]
            )
            captures_by_session = {
                sid: [map_ad_capture(capture) for capture in captures]
                for sid, captures in raw_captures.items()
            }

        items: list[EmulationHistoryItem] = []
        for row in rows:
            ad_captures = captures_by_session.get(row.session.session_id)
            summary = build_capture_summary(
                ad_captures=ad_captures,
                fallback_ads_total=row.ads_total,
                fallback_video_captures=row.video_captures,
                fallback_screenshot_fallbacks=row.screenshot_fallbacks,
            )

            items.append(
                self._map_history_item(
                    row.session,
                    capture_summary=summary,
                    include_details=params.include_details,
                    include_raw_ads=params.include_raw_ads,
                    ad_captures=ad_captures,
                )
            )

        return EmulationHistoryResponse(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
        )

    async def get_session_detail(
        self,
        session_id: str,
        include_raw_ads: bool,
        include_captures: bool,
    ) -> EmulationHistoryDetailResponse:
        payload = await self.uow.emulation_history.get_by_session_id(session_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Session history not found")

        captures = []
        if include_captures:
            capture_rows = await self.uow.emulation_history.get_ad_captures_by_session(
                session_id
            )
            captures = [map_ad_capture(capture) for capture in capture_rows]

        summary = build_capture_summary(
            ad_captures=captures if include_captures else None,
            fallback_ads_total=normalized_ads_count(payload),
            fallback_video_captures=0,
            fallback_screenshot_fallbacks=0,
        )
        return EmulationHistoryDetailResponse(
            **self._map_history_item(
                payload,
                capture_summary=summary,
                include_details=True,
                include_raw_ads=include_raw_ads,
                ad_captures=captures if include_captures else None,
            ).model_dump()
        )

    def _map_history_item(
        self,
        payload: EmulationSessionHistory,
        capture_summary: EmulationCaptureSummary,
        include_details: bool,
        include_raw_ads: bool,
        ad_captures: list[EmulationAdCaptureHistory] | None,
    ) -> EmulationHistoryItem:
        watched_videos = payload.watched_videos if include_details else None
        watched_ads_analytics = payload.watched_ads_analytics if include_details else None
        watched_ads = payload.watched_ads if (include_details and include_raw_ads) else None
        watched_videos_count = normalized_videos_count(payload)
        watched_ads_count = normalized_ads_count(payload)

        return EmulationHistoryItem(
            session_id=payload.session_id,
            status=payload.status,
            requested_duration_minutes=payload.requested_duration_minutes,
            requested_topics=payload.requested_topics or [],
            queued_at=payload.queued_at,
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            elapsed_minutes=calculate_history_elapsed_minutes(payload),
            mode=payload.mode,
            fatigue=payload.fatigue,
            bytes_downloaded=payload.bytes_downloaded,
            total_duration_seconds=payload.total_duration_seconds,
            videos_watched=payload.videos_watched,
            watched_videos_count=watched_videos_count,
            watched_ads_count=watched_ads_count,
            topics_searched=payload.topics_searched if include_details else [],
            watched_videos=watched_videos,
            watched_ads=watched_ads,
            watched_ads_analytics=watched_ads_analytics,
            error=payload.error,
            captures=capture_summary,
            ad_captures=ad_captures,
        )
