from __future__ import annotations

from datetime import UTC, datetime

from app.api.modules.ad_captures.models import (
    AdCapture,
    AdCaptureScreenshot,
    LandingStatus,
    VideoStatus,
)
from app.database.uow import UnitOfWork
from app.services.emulation.core.ad_analytics import build_ads_analytics


class EmulationPersistenceService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ── Ad captures ───────────────────────────────────────────

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
                landing_status=cap.get("landing_status", LandingStatus.SKIPPED),
                video_src_url=cap.get("video_src_url"),
                video_file=cap.get("video_file"),
                video_status=cap.get("video_status", VideoStatus.NO_SRC),
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

    # ── Session history ───────────────────────────────────────

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
        final_videos_watched = videos_watched if videos_watched is not None else _as_int(live_payload.get("videos_watched"))
        final_total_duration = total_duration_seconds if total_duration_seconds is not None else _as_int(live_payload.get("total_duration_seconds"))

        ads_analytics = live_payload.get("watched_ads_analytics")
        if not ads_analytics:
            ads_analytics = build_ads_analytics(final_watched_ads)

        finished_at = None
        if status in ("completed", "failed"):
            finished_at = _ts_to_dt(live_payload.get("finished_at")) or _utcnow()

        await self._uow.emulation_history.create_if_missing(
            session_id=session_id,
            requested_duration_minutes=duration_minutes,
            requested_topics=topics,
            queued_at=_ts_to_dt(live_payload.get("created_at")) or _utcnow(),
        )
        await self._uow.emulation_history.update_session(
            session_id,
            status=status,
            started_at=_ts_to_dt(live_payload.get("started_at")) or _utcnow(),
            finished_at=finished_at,
            mode=live_payload.get("mode"),
            fatigue=live_payload.get("fatigue"),
            current_topic=live_payload.get("current_topic"),
            personality=live_payload.get("personality"),
            bytes_downloaded=final_bytes,
            topics_searched=final_topics,
            videos_watched=final_videos_watched,
            watched_videos_count=max(
                _as_int(live_payload.get("watched_videos_count")),
                len(final_watched_videos),
            ),
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

    # ── Convenience wrappers (preserve call-site signatures) ──

    async def persist_history_running(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        await self.persist_history(
            session_id=session_id, status="running",
            duration_minutes=duration_minutes, topics=topics,
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
            session_id=session_id, status="completed",
            duration_minutes=duration_minutes, topics=topics,
            live_payload=live_payload,
            bytes_downloaded=bytes_downloaded, topics_searched=topics_searched,
            videos_watched=videos_watched, watched_videos=watched_videos,
            watched_ads=watched_ads, total_duration_seconds=total_duration_seconds,
        )

    async def persist_history_completed_from_live_payload(
        self,
        session_id: str,
        duration_minutes: int,
        topics: list[str],
        live_payload: dict,
    ) -> None:
        await self.persist_history(
            session_id=session_id, status="completed",
            duration_minutes=duration_minutes, topics=topics,
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
            session_id=session_id, status="failed",
            duration_minutes=duration_minutes, topics=topics,
            live_payload=live_payload, error=error,
        )

    async def rollback(self) -> None:
        await self._uow.rollback()


# ── Module-level helpers ──────────────────────────────────────


def _ts_to_dt(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _utcnow() -> datetime:
    return datetime.now(UTC)
