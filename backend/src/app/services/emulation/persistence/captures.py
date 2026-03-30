from __future__ import annotations

from app.api.modules.emulation.models import (
    AdCapture,
    AdCaptureScreenshot,
    LandingStatus,
    VideoStatus,
)
from app.database.uow import UnitOfWork


class CapturePersistenceService:
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
