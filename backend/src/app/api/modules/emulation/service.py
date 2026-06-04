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
from .models import AndroidAccountProfile, EmulationSessionHistory, SessionStatus
from .schema import (
    AndroidAccountProfileCreate,
    AndroidAccountProfileListResponse,
    AndroidAccountProfileRead,
    AndroidAccountProfileUpdate,
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
        requested_runner_kind = (request.runner or "android").lower()
        runner_kind = "android"
        android_profile = (
            await self._resolve_android_account_profile(request.android_account_id)
            if request.android_account_id is not None
            else None
        )
        android_runtime_profile = (
            self._android_account_runtime_payload(android_profile)
            if android_profile is not None
            else None
        )

        proxy_url: str | None = None
        proxy_country_code: str | None = None
        if runner_kind == "android" and request.proxy_id is not None:
            proxy_url, proxy_country_code = await self._resolve_proxy_url(request.proxy_id)
            if proxy_url is None:
                raise HTTPException(status_code=404, detail="Proxy not found")

        await self._session_store.create(
            session_id,
            request.topics,
            request.duration_minutes,
            profile_id=profile_id,
        )
        await self._session_store.update(
            session_id,
            runner_kind=runner_kind,
            requested_runner_kind=requested_runner_kind,
            proxy_id=str(request.proxy_id) if request.proxy_id else None,
            android_account_id=(
                str(android_profile.id) if android_profile is not None else None
            ),
            android_google_email=(
                android_profile.google_email if android_profile is not None else None
            ),
            android_avd_name=(
                android_profile.avd_name if android_profile is not None else None
            ),
            android_account_profile=android_runtime_profile,
        )
        await self._history_service.register_queued_session(
            session_id=session_id,
            duration_minutes=request.duration_minutes,
            topics=request.topics,
            proxy_country_code=proxy_country_code,
            android_account_id=android_profile.id if android_profile is not None else None,
            android_google_email=(
                android_profile.google_email if android_profile is not None else None
            ),
            android_avd_name=android_profile.avd_name if android_profile is not None else None,
        )

        try:
            from app.tiq import ANDROID_EMULATION_QUEUE_NAME, android_emulation_dispatch_broker

            await AsyncKicker(
                broker=android_emulation_dispatch_broker,
                task_name="android_emulation_task",
                labels={"queue_name": ANDROID_EMULATION_QUEUE_NAME},
            ).kiq(
                session_id,
                request.duration_minutes,
                request.topics,
                proxy_url=proxy_url,
                headless=request.headless,
                android_account_profile=android_runtime_profile,
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

    async def _resolve_proxy_url(self, proxy_id: uuid.UUID) -> tuple[str | None, str | None]:
        """Returns (proxy_url, country_code)."""
        proxy = await self._history_service.uow.proxies.get_by_id(proxy_id)
        if proxy is None:
            return None, None
        return proxy.to_url(), proxy.country_code

    async def _resolve_android_account_profile(
        self,
        profile_id: uuid.UUID,
    ) -> AndroidAccountProfile:
        profile = await self._history_service.uow.android_account_profiles.get_by_id(
            profile_id
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="Android account profile not found")
        if not profile.is_active or profile.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Android account profile is not ready "
                    f"(status={profile.status}, active={profile.is_active})"
                ),
            )
        return profile

    @staticmethod
    def _android_account_runtime_payload(
        profile: AndroidAccountProfile,
    ) -> dict[str, object]:
        return {
            "id": str(profile.id),
            "label": profile.label,
            "google_email": profile.google_email,
            "avd_name": profile.avd_name,
            "snapshot_name": profile.snapshot_name,
            "appium_port": profile.appium_port,
            "uiautomator2_system_port": profile.uiautomator2_system_port,
            "mjpeg_server_port": profile.mjpeg_server_port,
            "emulator_port": profile.emulator_port,
            "emulator_memory_mb": profile.emulator_memory_mb,
        }

    @staticmethod
    def _to_android_account_read(
        profile: AndroidAccountProfile,
    ) -> AndroidAccountProfileRead:
        return AndroidAccountProfileRead.model_validate(profile)

    async def list_android_account_profiles(
        self,
        active_only: bool = False,
    ) -> AndroidAccountProfileListResponse:
        rows = await self._history_service.uow.android_account_profiles.list_all(
            active_only=active_only
        )
        items = [self._to_android_account_read(row) for row in rows]
        return AndroidAccountProfileListResponse(items=items, total=len(items))

    async def create_android_account_profile(
        self,
        payload: AndroidAccountProfileCreate,
    ) -> AndroidAccountProfileRead:
        profile = AndroidAccountProfile(**payload.model_dump())
        created = await self._history_service.uow.android_account_profiles.create(profile)
        await self._history_service.uow.commit()
        return self._to_android_account_read(created)

    async def update_android_account_profile(
        self,
        profile_id: uuid.UUID,
        payload: AndroidAccountProfileUpdate,
    ) -> AndroidAccountProfileRead:
        updated = await self._history_service.uow.android_account_profiles.update(
            profile_id,
            **payload.model_dump(exclude_unset=True),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Android account profile not found")
        await self._history_service.uow.commit()
        return self._to_android_account_read(updated)

    async def stop_session(self, session_id: str) -> StopEmulationResponse:
        data = await self._session_store.get(session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Session not found")

        status = data.get("status")
        if status == SessionStatus.STOPPING:
            return StopEmulationResponse(session_id=session_id, status=SessionStatus.STOPPING)

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
            status=SessionStatus.STOPPING,
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
            runner_kind = data.get("runner_kind", "android")
            proxy_id_str = data.get("proxy_id")
            android_account_id = data.get("android_account_id")
        else:
            assert history is not None
            topics = history.requested_topics or []
            duration_minutes = history.requested_duration_minutes
            profile_id = None
            runner_kind = "android"
            proxy_id_str = None
            android_account_id = history.android_account_id

        return await self.start_emulation(
            StartEmulationRequest(
                duration_minutes=duration_minutes,
                topics=topics,
                profile_id=profile_id,
                runner=runner_kind,
                proxy_id=proxy_id_str,
                android_account_id=android_account_id,
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
            runner_kind = data.get("runner_kind", "android")
            proxy_id_str = data.get("proxy_id")
            android_account_id = data.get("android_account_id")
        else:
            assert history is not None
            topics = history.requested_topics or []
            duration_minutes = history.requested_duration_minutes
            profile_id = None
            elapsed_minutes = elapsed_minutes_from_history(history)
            resume_seed = build_resume_seed_from_history(history)
            runner_kind = "android"
            proxy_id_str = None
            android_account_id = history.android_account_id

        remaining_minutes = self._calculate_remaining_minutes(
            requested_duration_minutes=duration_minutes,
            elapsed_minutes=elapsed_minutes,
        )

        new_response = await self.start_emulation(
            StartEmulationRequest(
                duration_minutes=remaining_minutes,
                topics=topics,
                profile_id=profile_id,
                runner=runner_kind,
                proxy_id=proxy_id_str,
                android_account_id=android_account_id,
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
            profile_id=_live_android_lock_id(data)
            or normalize_profile_id(data.get("profile_id")),
        )
        live_payload = await self._session_store.get(session_id) or {**data}
        live_payload["status"] = SessionStatus.FAILED
        live_payload["finished_at"] = finished_at
        live_payload["current_watch"] = None
        live_payload["error"] = error
        await self._history_service.mark_stale_failed(session_id, live_payload, error)
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
    def __init__(self, uow: UnitOfWork, session_store: EmulationSessionStore) -> None:
        self.uow = uow
        self._session_store = session_store

    async def register_queued_session(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        proxy_country_code: str | None = None,
        android_account_id: uuid.UUID | None = None,
        android_google_email: str | None = None,
        android_avd_name: str | None = None,
    ) -> None:
        await self.uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
            proxy_country_code=proxy_country_code,
            android_account_id=android_account_id,
            android_google_email=android_google_email,
            android_avd_name=android_avd_name,
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
            android_account_id=_uuid_or_none(live_payload.get("android_account_id")),
            android_google_email=live_payload.get("android_google_email"),
            android_avd_name=live_payload.get("android_avd_name"),
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

        live_payloads: dict[str, dict] = {}
        active_session_ids = [
            row.session.session_id
            for row in rows
            if row.session.status in {SessionStatus.QUEUED, SessionStatus.RUNNING}
        ]
        if active_session_ids:
            live_results = await asyncio.gather(
                *(self._session_store.get(session_id) for session_id in active_session_ids),
                return_exceptions=True,
            )
            live_payloads = {
                session_id: result
                for session_id, result in zip(active_session_ids, live_results, strict=False)
                if isinstance(result, dict)
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
                    live_payload=live_payloads.get(row.session.session_id),
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
        live_payload = None
        if payload.status in {SessionStatus.QUEUED, SessionStatus.RUNNING}:
            live_result = await self._session_store.get(session_id)
            if isinstance(live_result, dict):
                live_payload = live_result
        return EmulationHistoryDetailResponse(
            **self._map_history_item(
                payload,
                capture_summary=summary,
                include_details=True,
                include_raw_ads=include_raw_ads,
                ad_captures=captures if include_captures else None,
                live_payload=live_payload,
            ).model_dump()
        )

    def _map_history_item(
        self,
        payload: EmulationSessionHistory,
        capture_summary: EmulationCaptureSummary,
        include_details: bool,
        include_raw_ads: bool,
        ad_captures: list[EmulationAdCaptureHistory] | None,
        live_payload: dict[str, object] | None = None,
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

        item = EmulationHistoryItem(
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
            proxy_country_code=payload.proxy_country_code,
            android_account_id=payload.android_account_id,
            android_google_email=payload.android_google_email,
            android_avd_name=payload.android_avd_name,
            captures=capture_summary,
            ad_captures=ad_captures,
        )
        if not live_payload:
            return item

        live_status = build_status_response(str(payload.session_id), live_payload)
        return item.model_copy(
            update={
                "status": live_status.status,
                "post_processing_status": live_status.post_processing_status,
                "post_processing_progress": live_status.post_processing_progress,
                "elapsed_minutes": live_status.elapsed_minutes,
                "mode": live_status.mode,
                "fatigue": live_status.fatigue,
                "bytes_downloaded": live_status.bytes_downloaded,
                "total_duration_seconds": live_status.total_duration_seconds,
                "videos_watched": live_status.videos_watched,
                "watched_videos_count": live_status.watched_videos_count,
                "watched_ads_count": live_status.watched_ads_count,
                "topics_searched": live_status.topics_searched,
                "watched_videos": live_status.watched_videos if include_details else item.watched_videos,
                "watched_ads": live_status.watched_ads if (include_details and include_raw_ads) else item.watched_ads,
                "watched_ads_analytics": live_status.watched_ads_analytics if include_details else item.watched_ads_analytics,
                "error": live_status.error,
                "android_account_id": live_status.android_account_id,
                "android_google_email": live_status.android_google_email,
                "android_avd_name": live_status.android_avd_name,
            }
        )


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _live_android_lock_id(data: dict[str, object]) -> str | None:
    android_account_id = normalize_profile_id(data.get("android_account_id"))
    if android_account_id:
        return f"android-account:{android_account_id}"
    android_avd_name = normalize_profile_id(data.get("android_avd_name"))
    if android_avd_name:
        return f"android-device:{android_avd_name}"
    return None
