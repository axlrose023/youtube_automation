from __future__ import annotations

import asyncio
import datetime
import math
import uuid

from fastapi import HTTPException
from taskiq.kicker import AsyncKicker

from app.database.uow import UnitOfWork
from app.services.emulation.common import derive_watched_video_counters, to_utc_datetime
from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.session.store import EmulationSessionStore

from .gateway import EmulationHistoryQuery
from .models import EmulationSessionHistory, SessionStatus
from .schema import (
    EmulationAdCaptureHistory,
    EmulationCapturesResponse,
    EmulationCaptureSummary,
    EmulationDashboardSummaryItem,
    EmulationDashboardSummaryResponse,
    EmulationHistoryDetailResponse,
    EmulationHistoryItem,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationStatusBatchResponse,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
    StopEmulationResponse,
)
from .services.session_runtime import (
    build_resume_seed_from_history,
    build_resume_seed_from_live_payload,
    build_status_response,
    elapsed_minutes_from_history,
    elapsed_minutes_from_live_payload,
    is_break_phase_active,
    last_activity_timestamp,
    normalize_profile_id,
)
from .utils import (
    build_capture_summary,
    build_post_processing_state,
    calculate_history_elapsed_minutes,
    map_ad_capture,
    normalize_watched_ads_payload,
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
        profile_id = normalize_profile_id(request.profile_id)
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
                status=SessionStatus.FAILED,
                finished_at=datetime.datetime.now(datetime.UTC).timestamp(),
                error=str(exc),
            )
            await self._history_service.mark_enqueue_failed(session_id=session_id, error=str(exc))
            raise HTTPException(status_code=500, detail="Failed to queue emulation task") from exc

        return StartEmulationResponse(session_id=session_id, status=SessionStatus.QUEUED)

    async def stop_session(self, session_id: str) -> StopEmulationResponse:
        data = await self._session_store.get(session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Session not found")

        status = data.get("status")
        if status not in (SessionStatus.RUNNING, SessionStatus.QUEUED):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot stop session with status '{status}'",
            )

        now_ts = datetime.datetime.now(datetime.UTC).timestamp()
        if status == SessionStatus.QUEUED:
            await self._session_store.update(
                session_id,
                status=SessionStatus.STOPPED,
                stop_requested=False,
                finished_at=now_ts,
                current_watch=None,
                error="Stopped by user",
            )
            await self._history_service.mark_stopped(
                session_id,
                await self._session_store.get(session_id) or {},
            )
            return StopEmulationResponse(session_id=session_id, status=SessionStatus.STOPPED)

        await self._session_store.update(
            session_id,
            stop_requested=True,
            error=None,
        )
        return StopEmulationResponse(session_id=session_id, status=SessionStatus.STOPPING)

    async def retry_session(self, session_id: str) -> StartEmulationResponse:
        data, history = await self._resolve_terminal_session(session_id)
        if data:
            topics = data.get("topics", [])
            duration_minutes = data.get("duration_minutes", 60)
            profile_id = normalize_profile_id(data.get("profile_id"))
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
            profile_id = normalize_profile_id(data.get("profile_id"))
            elapsed_minutes = elapsed_minutes_from_live_payload(data)
            resume_seed = build_resume_seed_from_live_payload(data)
        else:
            assert history is not None
            topics = history.requested_topics or []
            duration_minutes = history.requested_duration_minutes
            profile_id = None
            elapsed_minutes = elapsed_minutes_from_history(history)
            resume_seed = build_resume_seed_from_history(history)

        remaining_minutes = self._calculate_remaining_minutes(
            requested_duration_minutes=duration_minutes,
            elapsed_minutes=elapsed_minutes,
        )

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
            data = await self._reconcile_stale_stopping_session(session_id, data)
            status = data.get("status")
            if status not in (SessionStatus.FAILED, SessionStatus.STOPPED):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot retry/resume session with status '{status}'",
                )
            if await self._session_store.is_run_lock_active(session_id):
                raise HTTPException(
                    status_code=409,
                    detail="Session is still finalizing; retry or resume is not available yet",
                )
            return data, None

        history = await self._history_service.get_session_record(session_id)
        if history is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if history.status not in (SessionStatus.FAILED, SessionStatus.STOPPED):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot retry/resume session with status '{history.status}'",
            )
        return None, history

    async def get_status(self, session_id: str) -> EmulationSessionStatus:
        data = await self._session_store.get(session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Session not found")

        data = await self._reconcile_stale_stopping_session(session_id, data)
        data = await self._reconcile_stale_running_session(session_id, data)
        return build_status_response(session_id, data)

    async def get_status_batch(
        self,
        session_ids: list[str],
    ) -> EmulationStatusBatchResponse:
        unique_session_ids = list(dict.fromkeys(session_ids))
        if len(unique_session_ids) > 50:
            raise HTTPException(status_code=400, detail="Too many session ids")

        results = await asyncio.gather(
            *(self.get_status(session_id) for session_id in unique_session_ids),
            return_exceptions=True,
        )

        statuses: dict[str, EmulationSessionStatus] = {}
        for session_id, result in zip(unique_session_ids, results, strict=False):
            if isinstance(result, HTTPException) and result.status_code == 404:
                continue
            if isinstance(result, Exception):
                continue
            statuses[session_id] = result

        return EmulationStatusBatchResponse(statuses=statuses)

    async def _reconcile_stale_running_session(
        self,
        session_id: str,
        data: dict,
    ) -> dict:
        if data.get("status") != SessionStatus.RUNNING:
            return data

        if is_break_phase_active(data):
            return data

        started_at = data.get("started_at")
        duration_minutes = data.get("duration_minutes")
        if not isinstance(started_at, int | float) or not isinstance(duration_minutes, int | float):
            return data

        now_ts = datetime.datetime.now(datetime.UTC).timestamp()
        runtime_grace_seconds = 60
        if (started_at + (float(duration_minutes) * 60.0) + runtime_grace_seconds) > now_ts:
            return data

        last_activity_at = last_activity_timestamp(data)
        if (now_ts - last_activity_at) <= 60:
            return data

        if await self._session_store.is_run_lock_active(session_id) and (now_ts - last_activity_at) <= 90:
            return data

        error = (
            "Session stale: no recent progress after expected runtime window; "
            "marked as failed during status reconciliation"
        )
        finished_at = now_ts
        await self._session_store.update(
            session_id,
            status=SessionStatus.FAILED,
            finished_at=finished_at,
            current_watch=None,
            error=error,
        )
        await self._session_store.clear_session_locks(
            session_id,
            profile_id=normalize_profile_id(data.get("profile_id")),
        )
        live_payload = await self._session_store.get(session_id) or {**data}
        live_payload["status"] = SessionStatus.FAILED
        live_payload["finished_at"] = finished_at
        live_payload["current_watch"] = None
        live_payload["error"] = error
        await self._history_service.mark_stale_failed(session_id, live_payload, error)
        return live_payload

    async def _reconcile_stale_stopping_session(
        self,
        session_id: str,
        data: dict,
    ) -> dict:
        status = data.get("status")
        if status not in {SessionStatus.RUNNING, SessionStatus.STOPPING}:
            return data
        if status == SessionStatus.RUNNING and not data.get("stop_requested"):
            return data
        if await self._session_store.is_run_lock_active(session_id):
            return data

        now_ts = datetime.datetime.now(datetime.UTC).timestamp()
        await self._session_store.update(
            session_id,
            status=SessionStatus.STOPPED,
            stop_requested=False,
            finished_at=now_ts,
            current_watch=None,
            error="Stopped by user",
        )
        live_payload = await self._session_store.get(session_id) or {**data}
        live_payload["status"] = SessionStatus.STOPPED
        live_payload["stop_requested"] = False
        live_payload["finished_at"] = now_ts
        live_payload["current_watch"] = None
        live_payload["error"] = "Stopped by user"
        await self._history_service.mark_stopped(session_id, live_payload)
        return live_payload

    @staticmethod
    def _calculate_remaining_minutes(
        *,
        requested_duration_minutes: int,
        elapsed_minutes: float,
    ) -> int:
        remaining = max(float(requested_duration_minutes) - elapsed_minutes, 0.0)
        return max(1, int(math.ceil(remaining)))

    async def delete_session(self, session_id: str) -> None:
        await self._history_service.delete_session(session_id)
        await self._session_store.delete(session_id)

    async def get_history(
        self,
        params: EmulationHistoryParams,
    ) -> EmulationHistoryResponse:
        await self._reconcile_stale_history_records()
        return await self._history_service.get_history(params)

    async def get_dashboard_summary(self) -> EmulationDashboardSummaryResponse:
        await self._reconcile_stale_history_records()
        return await self._history_service.get_dashboard_summary()

    async def get_session_detail(
        self,
        session_id: str,
        *,
        include_raw_ads: bool,
        include_captures: bool,
    ) -> EmulationHistoryDetailResponse:
        await self._reconcile_stale_history_records(session_id=session_id)
        detail = await self._history_service.get_session_detail(
            session_id=session_id,
            include_raw_ads=include_raw_ads,
            include_captures=include_captures,
        )
        if detail.status == SessionStatus.FAILED and not detail.error:
            live_payload = await self._session_store.get(session_id)
            live_error = live_payload.get("error") if isinstance(live_payload, dict) else None
            if isinstance(live_error, str) and live_error.strip():
                detail = detail.model_copy(update={"error": live_error})
        return detail

    async def _reconcile_stale_history_records(self, session_id: str | None = None) -> None:
        if session_id:
            record = await self._history_service.get_session_record(session_id)
            if record and record.status in (SessionStatus.QUEUED, SessionStatus.RUNNING):
                await self._reconcile_stale_history_record(record)
            return

        records = await self._history_service.get_active_session_records()
        for record in records:
            await self._reconcile_stale_history_record(record)

    async def _reconcile_stale_history_record(
        self,
        record: EmulationSessionHistory,
    ) -> None:
        if record.status not in (SessionStatus.QUEUED, SessionStatus.RUNNING):
            return

        live_payload = await self._session_store.get(record.session_id)
        if live_payload is not None:
            if record.status == SessionStatus.RUNNING:
                await self._reconcile_stale_running_session(record.session_id, live_payload)
            return

        now = datetime.datetime.now(datetime.UTC)
        error: str | None = None

        if record.status == SessionStatus.QUEUED:
            if (now - record.queued_at) > datetime.timedelta(hours=1):
                error = "Session stale: queued session did not start within expected time window"
        elif record.status == SessionStatus.RUNNING and record.started_at is not None:
            expected_end = record.started_at + datetime.timedelta(
                minutes=record.requested_duration_minutes,
            )
            grace = datetime.timedelta(minutes=5)
            if now > (expected_end + grace):
                error = (
                    "Session stale: running session exceeded expected runtime window "
                    "without live state"
                )

        if error:
            await self._history_service.mark_history_stale_failed(
                session_id=record.session_id,
                error=error,
            )


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
            status=SessionStatus.FAILED,
            finished_at=datetime.datetime.now(datetime.UTC),
            error=error,
        )
        await self.uow.commit()

    async def get_session_record(self, session_id: str) -> EmulationSessionHistory | None:
        return await self.uow.emulation_history.get_by_session_id(session_id)

    async def get_active_session_records(self) -> list[EmulationSessionHistory]:
        return await self.uow.emulation_history.get_by_statuses([SessionStatus.QUEUED, SessionStatus.RUNNING])

    async def mark_stale_failed(
        self,
        session_id: str,
        live_payload: dict,
        error: str,
    ) -> None:
        await self._update_terminal_from_live_payload(
            session_id=session_id,
            status=SessionStatus.FAILED,
            live_payload=live_payload,
            error=error,
        )

    async def mark_stopped(
        self,
        session_id: str,
        live_payload: dict,
    ) -> None:
        await self._update_terminal_from_live_payload(
            session_id=session_id,
            status=SessionStatus.STOPPED,
            live_payload=live_payload,
            error="Stopped by user",
        )

    async def mark_history_stale_failed(
        self,
        *,
        session_id: str,
        error: str,
    ) -> None:
        await self.uow.emulation_history.update_session(
            session_id,
            status=SessionStatus.FAILED,
            finished_at=datetime.datetime.now(datetime.UTC),
            error=error,
        )
        await self.uow.commit()

    async def _update_terminal_from_live_payload(
        self,
        *,
        session_id: str,
        status: str,
        live_payload: dict,
        error: str,
    ) -> None:
        watched_videos = live_payload.get("watched_videos") or []
        videos_watched, watched_videos_count = derive_watched_video_counters(
            watched_videos,
            fallback_completed=int(live_payload.get("videos_watched") or 0),
            fallback_total=int(live_payload.get("watched_videos_count") or 0),
        )
        watched_ads = live_payload.get("watched_ads") or []
        await self.uow.emulation_history.update_session(
            session_id,
            status=status,
            started_at=to_utc_datetime(live_payload.get("started_at")),
            finished_at=datetime.datetime.now(datetime.UTC),
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            current_topic=live_payload.get("current_topic"),
            personality=live_payload.get("personality"),
            bytes_downloaded=int(live_payload.get("bytes_downloaded") or 0),
            total_duration_seconds=int(live_payload.get("total_duration_seconds") or 0),
            videos_watched=videos_watched,
            watched_videos_count=watched_videos_count,
            watched_ads_count=int(live_payload.get("watched_ads_count") or 0),
            topics_searched=live_payload.get("topics_searched") or [],
            watched_videos=watched_videos,
            watched_ads=watched_ads,
            watched_ads_analytics=live_payload.get("watched_ads_analytics")
            or build_ads_analytics(watched_ads),
            error=error,
        )
        await self.uow.commit()

    async def delete_session(self, session_id: str) -> None:
        history = await self.uow.emulation_history.get_by_session_id(session_id)
        if history is None:
            raise HTTPException(status_code=404, detail="Session history not found")
        if history.status in (SessionStatus.RUNNING, SessionStatus.QUEUED):
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

    async def get_dashboard_summary(self) -> EmulationDashboardSummaryResponse:
        base = await self.uow.emulation_history.get_dashboard_base_summary()
        capture_summary = await self.uow.emulation_history.get_dashboard_capture_summary()
        top_topics = await self.uow.emulation_history.get_top_requested_topics()

        total_sessions = base["total_sessions"]
        avg_videos_per_session = (
            round(base["total_videos_watched"] / total_sessions, 1)
            if total_sessions > 0
            else 0.0
        )

        return EmulationDashboardSummaryResponse(
            total_sessions=total_sessions,
            completed=base["completed"],
            running=base["running"],
            failed=base["failed"],
            stopped=base["stopped"],
            total_videos_watched=base["total_videos_watched"],
            avg_videos_per_session=avg_videos_per_session,
            total_ads_watched=base["total_ads_watched"],
            total_ad_captures=int(capture_summary["total_ad_captures"]),
            video_captures=int(capture_summary["video_captures"]),
            screenshot_fallbacks=int(capture_summary["screenshot_fallbacks"]),
            landing_completed=int(capture_summary["landing_completed"]),
            relevant_ads=int(capture_summary["relevant_ads"]),
            not_relevant_ads=int(capture_summary["not_relevant_ads"]),
            analyzed_ads=int(capture_summary["analyzed_ads"]),
            top_advertisers=[
                EmulationDashboardSummaryItem(label=label, value=value)
                for label, value in capture_summary["top_advertisers"]
            ],
            top_topics=[
                EmulationDashboardSummaryItem(label=label, value=value)
                for label, value in top_topics
            ],
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
        watched_ads = (
            normalize_watched_ads_payload(payload.watched_ads)
            if (include_details and include_raw_ads)
            else None
        )
        videos_watched, _ = derive_watched_video_counters(
            payload.watched_videos or [],
            fallback_completed=int(payload.videos_watched or 0),
            fallback_total=normalized_videos_count(payload),
        )
        watched_videos_count = normalized_videos_count(payload)
        watched_ads_count = normalized_ads_count(payload)
        post_processing_status, post_processing_progress = build_post_processing_state(
            session_status=payload.status,
            ad_captures=ad_captures,
        )

        return EmulationHistoryItem(
            session_id=payload.session_id,
            status=payload.status,
            post_processing_status=post_processing_status,
            post_processing_progress=post_processing_progress,
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
            videos_watched=videos_watched,
            watched_videos_count=watched_videos_count,
            watched_ads_count=watched_ads_count,
            topics_searched=payload.topics_searched or [],
            watched_videos=watched_videos,
            watched_ads=watched_ads,
            watched_ads_analytics=watched_ads_analytics,
            error=payload.error,
            captures=capture_summary,
            ad_captures=ad_captures,
        )
