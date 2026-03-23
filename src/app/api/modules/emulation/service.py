from __future__ import annotations

import datetime
import math
import uuid

from fastapi import HTTPException
from taskiq.kicker import AsyncKicker

from app.database.uow import UnitOfWork
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.core.session.store import EmulationSessionStore

from .gateway import EmulationHistoryQuery
from .models import EmulationSessionHistory
from .schema import (
    EmulationAdCaptureHistory,
    EmulationCaptureSummary,
    EmulationCapturesResponse,
    EmulationHistoryDetailResponse,
    EmulationHistoryItem,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
    StopEmulationResponse,
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

    async def stop_session(self, session_id: str) -> StopEmulationResponse:
        data = await self._session_store.get(session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Session not found")

        status = data.get("status")
        if status not in ("running", "queued"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot stop session with status '{status}'",
            )

        now_ts = datetime.datetime.now(datetime.UTC).timestamp()
        await self._session_store.update(
            session_id,
            status="stopped",
            finished_at=now_ts,
            current_watch=None,
            error="Stopped by user",
        )

        live_payload = await self._session_store.get(session_id) or data
        await self._history_service.mark_stopped(session_id, live_payload)
        return StopEmulationResponse(session_id=session_id, status="stopped")

    async def retry_session(self, session_id: str) -> StartEmulationResponse:
        data, history = await self._resolve_terminal_session(session_id)
        if data:
            topics = data.get("topics", [])
            duration_minutes = data.get("duration_minutes", 60)
            profile_id = self._normalize_profile_id(data.get("profile_id"))
        else:
            assert history is not None
            topics = history.requested_topics or []
            duration_minutes = history.requested_duration_minutes
            profile_id = None

        return await self.start_emulation(
            StartEmulationRequest(
                duration_minutes=duration_minutes,
                topics=topics,
                profile_id=profile_id,
            )
        )

    async def resume_session(self, session_id: str) -> StartEmulationResponse:
        data, history = await self._resolve_terminal_session(session_id)

        if data:
            topics = data.get("topics", [])
            duration_minutes = data.get("duration_minutes", 60)
            profile_id = self._normalize_profile_id(data.get("profile_id"))
            elapsed_minutes = self._elapsed_minutes_from_live_payload(data)
            resume_seed = self._build_resume_seed_from_live_payload(data)
        else:
            assert history is not None
            topics = history.requested_topics or []
            duration_minutes = history.requested_duration_minutes
            profile_id = None
            elapsed_minutes = self._elapsed_minutes_from_history(history)
            resume_seed = self._build_resume_seed_from_history(history)

        remaining_minutes = max(1, math.ceil(max(float(duration_minutes) - elapsed_minutes, 0.0)))

        new_response = await self.start_emulation(
            StartEmulationRequest(
                duration_minutes=remaining_minutes,
                topics=topics,
                profile_id=profile_id,
            )
        )

        await self._session_store.update(
            str(new_response.session_id),
            **resume_seed,
            resumed_from=session_id,
        )

        return new_response

    async def _resolve_terminal_session(
        self, session_id: str,
    ) -> tuple[dict | None, EmulationSessionHistory | None]:
        data = await self._session_store.get(session_id)
        if data is not None:
            status = data.get("status")
            if status not in ("failed", "stopped"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot retry/resume session with status '{status}'",
                )
            return data, None

        history = await self._history_service.uow.emulation_history.get_by_session_id(session_id)
        if history is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if history.status not in ("failed", "stopped"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot retry/resume session with status '{history.status}'",
            )
        return None, history

    async def get_status(self, session_id: str) -> EmulationSessionStatus:
        data = await self._session_store.get(session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Session not found")

        data = await self._reconcile_stale_running_session(session_id, data)

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
            current_watch=data.get("current_watch"),
            watched_ads_count=data.get("watched_ads_count", 0),
            watched_ads=data.get("watched_ads", []),
            watched_ads_analytics=data.get("watched_ads_analytics")
            or build_ads_analytics(data.get("watched_ads", [])),
            mode=data.get("mode"),
            fatigue=data.get("fatigue"),
            error=data.get("error"),
        )

    async def _reconcile_stale_running_session(
        self,
        session_id: str,
        data: dict,
    ) -> dict:
        if data.get("status") != "running":
            return data

        orchestration = data.get("orchestration")
        if isinstance(orchestration, dict) and orchestration.get("enabled"):
            phase = str(orchestration.get("phase") or "")
            next_resume_at = orchestration.get("next_resume_at")
            if (
                phase == "break"
                and isinstance(next_resume_at, int | float)
                and next_resume_at > datetime.datetime.now(datetime.UTC).timestamp()
            ):
                return data

        started_at = data.get("started_at")
        duration_minutes = data.get("duration_minutes")
        if not isinstance(started_at, int | float) or not isinstance(duration_minutes, int | float):
            return data

        now_ts = datetime.datetime.now(datetime.UTC).timestamp()
        runtime_grace_seconds = 5 * 60
        if (started_at + (float(duration_minutes) * 60.0) + runtime_grace_seconds) > now_ts:
            return data

        last_activity_at = self._last_activity_timestamp(data)
        if (now_ts - last_activity_at) <= (10 * 60):
            return data

        if await self._session_store.is_run_lock_active(session_id) and (now_ts - last_activity_at) <= (
            20 * 60
        ):
            return data

        error = (
            "Session stale: no recent progress after expected runtime window; "
            "marked as failed during status reconciliation"
        )
        finished_at = now_ts
        await self._session_store.update(
            session_id,
            status="failed",
            finished_at=finished_at,
            current_watch=None,
            error=error,
        )
        live_payload = await self._session_store.get(session_id) or {**data}
        live_payload["status"] = "failed"
        live_payload["finished_at"] = finished_at
        live_payload["current_watch"] = None
        live_payload["error"] = error
        await self._history_service.mark_stale_failed(session_id, live_payload, error)
        return live_payload

    @staticmethod
    def _normalize_profile_id(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _last_activity_timestamp(data: dict) -> float:
        candidates: list[float] = []

        updated_at = data.get("updated_at")
        if isinstance(updated_at, int | float):
            candidates.append(float(updated_at))

        current_watch = data.get("current_watch")
        if isinstance(current_watch, dict):
            started_at = current_watch.get("started_at")
            watched_seconds = current_watch.get("watched_seconds")
            if isinstance(started_at, int | float):
                current_watch_ts = float(started_at)
                if isinstance(watched_seconds, int | float):
                    current_watch_ts += max(float(watched_seconds), 0.0)
                candidates.append(current_watch_ts)

        for video in data.get("watched_videos") or []:
            if not isinstance(video, dict):
                continue
            recorded_at = video.get("recorded_at")
            if isinstance(recorded_at, int | float):
                candidates.append(float(recorded_at))

        for ad in data.get("watched_ads") or []:
            if not isinstance(ad, dict):
                continue
            recorded_at = ad.get("recorded_at")
            if isinstance(recorded_at, int | float):
                candidates.append(float(recorded_at))
            ended_at = ad.get("ended_at")
            if isinstance(ended_at, int | float):
                candidates.append(float(ended_at))

        started_at = data.get("started_at")
        if isinstance(started_at, int | float):
            candidates.append(float(started_at))

        return max(candidates) if candidates else datetime.datetime.now(datetime.UTC).timestamp()

    @staticmethod
    def _elapsed_minutes_from_live_payload(data: dict) -> float:
        started_at = data.get("started_at")
        if isinstance(started_at, int | float):
            finished_at = data.get("finished_at")
            end_ts = float(finished_at) if isinstance(finished_at, int | float) else datetime.datetime.now(datetime.UTC).timestamp()
            return max((end_ts - float(started_at)) / 60.0, 0.0)
        return max(float(data.get("total_duration_seconds") or 0) / 60.0, 0.0)

    @staticmethod
    def _elapsed_minutes_from_history(history: EmulationSessionHistory) -> float:
        if history.started_at:
            end_at = history.finished_at or datetime.datetime.now(datetime.UTC)
            return max((end_at - history.started_at).total_seconds() / 60.0, 0.0)
        return max(float(history.total_duration_seconds or 0) / 60.0, 0.0)

    @staticmethod
    def _build_resume_seed_from_live_payload(data: dict) -> dict[str, object]:
        watched_videos = data.get("watched_videos") or []
        watched_ads = data.get("watched_ads") or []
        return {
            "current_topic": data.get("current_topic"),
            "topics_searched": data.get("topics_searched") or [],
            "watched_videos": watched_videos,
            "watched_ads": watched_ads,
            "videos_watched": int(data.get("videos_watched") or 0),
            "watched_videos_count": max(int(data.get("watched_videos_count") or 0), len(watched_videos)),
            "watched_ads_count": max(int(data.get("watched_ads_count") or 0), len(watched_ads)),
            "watched_ads_analytics": data.get("watched_ads_analytics")
            or build_ads_analytics(watched_ads),
            "total_duration_seconds": int(data.get("total_duration_seconds") or 0),
            "bytes_downloaded": int(data.get("bytes_downloaded") or 0),
            "fatigue": data.get("fatigue"),
            "mode": data.get("mode"),
            "personality": data.get("personality"),
        }

    def _build_resume_seed_from_history(
        self,
        history: EmulationSessionHistory,
    ) -> dict[str, object]:
        watched_videos = history.watched_videos or []
        watched_ads = history.watched_ads or []
        current_topic = history.current_topic or self._infer_current_topic(
            history.topics_searched or [],
            watched_videos,
        )
        return {
            "current_topic": current_topic,
            "topics_searched": history.topics_searched or [],
            "watched_videos": watched_videos,
            "watched_ads": watched_ads,
            "videos_watched": int(history.videos_watched or 0),
            "watched_videos_count": normalized_videos_count(history),
            "watched_ads_count": normalized_ads_count(history),
            "watched_ads_analytics": history.watched_ads_analytics or build_ads_analytics(watched_ads),
            "total_duration_seconds": int(history.total_duration_seconds or 0),
            "bytes_downloaded": int(history.bytes_downloaded or 0),
            "fatigue": history.fatigue,
            "mode": history.mode,
            "personality": history.personality,
        }

    @staticmethod
    def _infer_current_topic(topics_searched: list[str], watched_videos: list[dict]) -> str | None:
        for item in reversed(watched_videos):
            if not isinstance(item, dict):
                continue
            search_keyword = item.get("search_keyword")
            if isinstance(search_keyword, str) and search_keyword.strip():
                return search_keyword.strip()
        for topic in reversed(topics_searched):
            if isinstance(topic, str) and topic.strip():
                return topic.strip()
        return None


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

    async def mark_stale_failed(
        self,
        session_id: str,
        live_payload: dict,
        error: str,
    ) -> None:
        await self.uow.emulation_history.update_session(
            session_id,
            status="failed",
            started_at=self._as_datetime(live_payload.get("started_at")),
            finished_at=datetime.datetime.now(datetime.UTC),
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            current_topic=live_payload.get("current_topic"),
            personality=live_payload.get("personality"),
            bytes_downloaded=int(live_payload.get("bytes_downloaded") or 0),
            total_duration_seconds=int(live_payload.get("total_duration_seconds") or 0),
            videos_watched=int(live_payload.get("videos_watched") or 0),
            watched_videos_count=int(live_payload.get("watched_videos_count") or 0),
            watched_ads_count=int(live_payload.get("watched_ads_count") or 0),
            topics_searched=live_payload.get("topics_searched") or [],
            watched_videos=live_payload.get("watched_videos") or [],
            watched_ads=live_payload.get("watched_ads") or [],
            watched_ads_analytics=live_payload.get("watched_ads_analytics")
            or build_ads_analytics(live_payload.get("watched_ads") or []),
            error=error,
        )
        await self.uow.commit()

    async def mark_stopped(
        self,
        session_id: str,
        live_payload: dict,
    ) -> None:
        await self.uow.emulation_history.update_session(
            session_id,
            status="stopped",
            started_at=self._as_datetime(live_payload.get("started_at")),
            finished_at=datetime.datetime.now(datetime.UTC),
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            current_topic=live_payload.get("current_topic"),
            personality=live_payload.get("personality"),
            bytes_downloaded=int(live_payload.get("bytes_downloaded") or 0),
            total_duration_seconds=int(live_payload.get("total_duration_seconds") or 0),
            videos_watched=int(live_payload.get("videos_watched") or 0),
            watched_videos_count=int(live_payload.get("watched_videos_count") or 0),
            watched_ads_count=int(live_payload.get("watched_ads_count") or 0),
            topics_searched=live_payload.get("topics_searched") or [],
            watched_videos=live_payload.get("watched_videos") or [],
            watched_ads=live_payload.get("watched_ads") or [],
            watched_ads_analytics=live_payload.get("watched_ads_analytics")
            or build_ads_analytics(live_payload.get("watched_ads") or []),
            error="Stopped by user",
        )
        await self.uow.commit()

    async def delete_session(self, session_id: str) -> None:
        history = await self.uow.emulation_history.get_by_session_id(session_id)
        if history is None:
            raise HTTPException(status_code=404, detail="Session history not found")
        if history.status in ("running", "queued"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete session with status '{history.status}'",
            )
        await self.uow.emulation_history.delete_session(session_id)
        await self.uow.commit()

    async def get_session_captures(
        self,
        session_id: str,
        analysis_status: str | None = None,
    ) -> EmulationCapturesResponse:
        captures_raw = await self.uow.emulation_history.get_ad_captures_by_session(session_id)
        captures = [map_ad_capture(c) for c in captures_raw]
        if analysis_status:
            captures = [c for c in captures if c.analysis_status == analysis_status]
        return EmulationCapturesResponse(
            session_id=session_id,
            total=len(captures),
            captures=captures,
        )

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

    @staticmethod
    def _as_datetime(value: object) -> datetime.datetime | None:
        if isinstance(value, int | float):
            return datetime.datetime.fromtimestamp(float(value), tz=datetime.UTC)
        return None
