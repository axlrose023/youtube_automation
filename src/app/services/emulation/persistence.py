from __future__ import annotations

from datetime import UTC, datetime

from app.api.modules.ad_captures.models import AdCapture, AdCaptureScreenshot
from app.database.uow import UnitOfWork
from app.services.emulation.core.ad_analytics import build_ads_analytics


class EmulationPersistenceService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def persist_ad_captures(
        self,
        session_id: str,
        watched_ads: list[dict],
        from_index: int = 0,
    ) -> None:
        start_index = max(from_index, 0)
        if start_index >= len(watched_ads):
            return

        existing = await self._uow.ad_captures.get_by_session(session_id)
        existing_positions = {
            capture.ad_position
            for capture in existing
            if isinstance(capture.ad_position, int) and capture.ad_position > 0
        }

        for i, ad in enumerate(watched_ads[start_index:], start=start_index + 1):
            if not ad.get("capture_id"):
                continue

            cap = ad.get("capture") or {}
            ad_position = ad.get("position")
            if not isinstance(ad_position, int) or ad_position <= 0:
                ad_position = i
            if ad_position in existing_positions:
                continue

            record = AdCapture(
                session_id=session_id,
                ad_position=ad_position,
                advertiser_domain=ad.get("advertiser_domain"),
                cta_href=ad.get("cta_href"),
                display_url=ad.get("display_url"),
                headline_text=ad.get("headline_text"),
                ad_duration_seconds=ad.get("ad_duration_seconds"),
                landing_url=cap.get("landing_url"),
                landing_dir=cap.get("landing_dir"),
                landing_status=cap.get("landing_status", "skipped"),
                video_src_url=cap.get("video_src_url"),
                video_file=cap.get("video_file"),
                video_status=cap.get("video_status", "no_src"),
            )
            await self._uow.ad_captures.create(record)
            existing_positions.add(ad_position)

            for offset_ms, file_path in cap.get("screenshot_paths", []):
                await self._uow.ad_captures.add_screenshot(
                    AdCaptureScreenshot(
                        capture_id=record.id,
                        offset_ms=offset_ms,
                        file_path=file_path,
                    ),
                )

        await self._uow.commit()

    async def persist_history_running(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        watched_videos = live_payload.get("watched_videos") or []
        watched_ads = live_payload.get("watched_ads") or []
        watched_ads_analytics = live_payload.get("watched_ads_analytics")
        if not watched_ads_analytics:
            watched_ads_analytics = build_ads_analytics(watched_ads)

        await self._uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
            queued_at=self._ts_to_dt(live_payload.get("created_at")) or self._utcnow(),
        )
        await self._uow.emulation_history.update_session(
            session_id,
            status="running",
            started_at=self._ts_to_dt(live_payload.get("started_at")) or self._utcnow(),
            finished_at=None,
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            bytes_downloaded=self._as_int(live_payload.get("bytes_downloaded")),
            topics_searched=live_payload.get("topics_searched") or [],
            videos_watched=self._as_int(live_payload.get("videos_watched")),
            watched_videos_count=max(
                self._as_int(live_payload.get("watched_videos_count")),
                len(watched_videos),
            ),
            watched_videos=watched_videos,
            watched_ads_count=max(
                self._as_int(live_payload.get("watched_ads_count")),
                len(watched_ads),
            ),
            watched_ads=watched_ads,
            watched_ads_analytics=watched_ads_analytics,
            total_duration_seconds=self._as_int(live_payload.get("total_duration_seconds")),
            error=None,
        )
        await self._uow.commit()

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
        await self._uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
            queued_at=self._ts_to_dt(live_payload.get("created_at")) or self._utcnow(),
        )
        await self._uow.emulation_history.update_session(
            session_id,
            status="completed",
            started_at=self._ts_to_dt(live_payload.get("started_at")),
            finished_at=self._ts_to_dt(live_payload.get("finished_at")) or self._utcnow(),
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            bytes_downloaded=bytes_downloaded,
            topics_searched=topics_searched,
            videos_watched=videos_watched,
            watched_videos_count=len(watched_videos),
            watched_videos=watched_videos,
            watched_ads_count=len(watched_ads),
            watched_ads=watched_ads,
            watched_ads_analytics=build_ads_analytics(watched_ads),
            total_duration_seconds=total_duration_seconds,
            error=None,
        )
        await self._uow.commit()

    async def persist_history_completed_from_live_payload(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        watched_videos = live_payload.get("watched_videos") or []
        watched_ads = live_payload.get("watched_ads") or []
        await self.persist_history_completed(
            session_id=session_id,
            duration_minutes=duration_minutes,
            topics=topics,
            bytes_downloaded=self._as_int(live_payload.get("bytes_downloaded")),
            topics_searched=live_payload.get("topics_searched") or [],
            videos_watched=self._as_int(live_payload.get("videos_watched")),
            watched_videos=watched_videos,
            watched_ads=watched_ads,
            total_duration_seconds=self._as_int(live_payload.get("total_duration_seconds")),
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
        watched_videos = live_payload.get("watched_videos") or []
        watched_ads = live_payload.get("watched_ads") or []
        watched_ads_analytics = live_payload.get("watched_ads_analytics")
        if not watched_ads_analytics:
            watched_ads_analytics = build_ads_analytics(watched_ads)

        await self._uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
            queued_at=self._ts_to_dt(live_payload.get("created_at")) or self._utcnow(),
        )
        await self._uow.emulation_history.update_session(
            session_id,
            status="failed",
            started_at=self._ts_to_dt(live_payload.get("started_at")),
            finished_at=self._ts_to_dt(live_payload.get("finished_at")) or self._utcnow(),
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            bytes_downloaded=self._as_int(live_payload.get("bytes_downloaded")),
            topics_searched=live_payload.get("topics_searched") or [],
            videos_watched=self._as_int(live_payload.get("videos_watched")),
            watched_videos_count=self._as_int(live_payload.get("watched_videos_count")),
            watched_videos=watched_videos,
            watched_ads_count=self._as_int(live_payload.get("watched_ads_count")),
            watched_ads=watched_ads,
            watched_ads_analytics=watched_ads_analytics,
            total_duration_seconds=self._as_int(live_payload.get("total_duration_seconds")),
            error=error,
        )
        await self._uow.commit()

    async def rollback(self) -> None:
        await self._uow.rollback()

    @staticmethod
    def _ts_to_dt(value: object) -> datetime | None:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=UTC)
        return None

    @staticmethod
    def _as_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int | float):
            return int(value)
        return 0

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)
