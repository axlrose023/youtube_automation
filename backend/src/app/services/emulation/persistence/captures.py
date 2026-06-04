from __future__ import annotations

import json

from app.api.modules.emulation.gateway import collect_duplicate_capture_ids, collapse_capture_rows
from app.api.modules.emulation.models import (
    AdCaptureScreenshot,
    ANALYSIS_TERMINAL_STATUSES,
    AnalysisStatus,
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
        prune_missing: bool = False,
    ) -> None:
        start_index = max(from_index, 0)
        if start_index >= len(watched_ads) and not prune_missing:
            return

        existing = await self._uow.ad_captures.get_raw_by_session(session_id)
        duplicate_capture_ids = collect_duplicate_capture_ids(existing)
        existing_by_position = {
            capture.ad_position: capture
            for capture in collapse_capture_rows(existing)
            if isinstance(capture.ad_position, int) and capture.ad_position > 0
        }
        desired_positions: set[int] = set()
        for i, ad in enumerate(watched_ads, start=1):
            if not self._should_persist_ad(ad):
                continue
            ad_position = self._resolve_ad_position(ad, index=i)
            if ad_position is not None:
                desired_positions.add(ad_position)

        for i, ad in enumerate(watched_ads[start_index:], start=start_index + 1):
            if not self._should_persist_ad(ad):
                continue

            cap = ad.get("capture") or {}
            ad_position = self._resolve_ad_position(ad, index=i)
            if ad_position is None:
                continue

            record = existing_by_position.get(ad_position)
            if record is None:
                record = await self._uow.ad_captures.get_or_create(
                    session_id=session_id,
                    ad_position=ad_position,
                )
                existing_by_position[ad_position] = record

            record.advertiser_domain = ad.get("advertiser_domain")
            record.cta_href = ad.get("cta_href") or cap.get("cta_href")
            record.display_url = ad.get("display_url")
            record.headline_text = ad.get("headline_text")
            record.ad_duration_seconds = ad.get("ad_duration_seconds")
            record.landing_url = cap.get("landing_url")
            # Prefer Playwright scrape dir over emulator screenshot dir
            record.landing_dir = cap.get("landing_scrape_dir") or cap.get("landing_dir")
            record.landing_status = cap.get("landing_status", LandingStatus.SKIPPED)
            record.video_src_url = cap.get("video_src_url")
            record.video_file = cap.get("video_file")
            record.video_status = cap.get("video_status", VideoStatus.NO_SRC)

            incoming_analysis_status = cap.get("analysis_status")
            incoming_summary = self._serialize_analysis_summary(cap.get("analysis_summary"))
            existing_analysis_status = str(record.analysis_status or "").lower()
            incoming_analysis_status_normalized = str(incoming_analysis_status or "").lower()
            preserve_existing_analysis = (
                existing_analysis_status in ANALYSIS_TERMINAL_STATUSES
                and incoming_analysis_status_normalized
                not in ANALYSIS_TERMINAL_STATUSES
            )
            if not preserve_existing_analysis:
                record.analysis_status = incoming_analysis_status or AnalysisStatus.PENDING
                record.analysis_summary = incoming_summary
            elif incoming_summary is not None:
                record.analysis_summary = incoming_summary

            screenshots = [
                AdCaptureScreenshot(
                    capture_id=record.id,
                    offset_ms=offset_ms,
                    file_path=file_path,
                )
                for offset_ms, file_path in self._iter_screenshot_entries(
                    cap.get("screenshot_paths", []),
                )
            ]
            await self._uow.ad_captures.set_screenshots(record, screenshots)

        await self._uow.ad_captures.delete_by_ids(duplicate_capture_ids)
        if prune_missing:
            await self._uow.ad_captures.delete_missing_positions(
                session_id=session_id,
                positions=desired_positions,
            )

        await self._uow.commit()

    @staticmethod
    def _resolve_ad_position(ad: dict, *, index: int) -> int | None:
        ad_position = ad.get("position")
        if isinstance(ad_position, int) and ad_position > 0:
            return ad_position
        if index > 0:
            return index
        return None

    @staticmethod
    def _should_persist_ad(ad: object) -> bool:
        if not isinstance(ad, dict):
            return False
        if ad.get("capture_id"):
            return True
        capture = ad.get("capture")
        if isinstance(capture, dict) and capture:
            return True
        for key in (
            "headline_text",
            "display_url",
            "cta_href",
            "landing_url",
            "video_file",
            "advertiser_domain",
        ):
            value = ad.get(key)
            if value not in (None, "", [], {}):
                return True
        return False

    @staticmethod
    def _iter_screenshot_entries(raw_items: object) -> list[tuple[int, str]]:
        if not isinstance(raw_items, list):
            return []
        entries: list[tuple[int, str]] = []
        for item in raw_items:
            offset_ms: object | None = None
            file_path: object | None = None
            if isinstance(item, dict):
                offset_ms = item.get("offset_ms")
                file_path = item.get("file_path")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                offset_ms, file_path = item[0], item[1]
            if not isinstance(file_path, str) or not file_path:
                continue
            if not isinstance(offset_ms, int):
                continue
            entries.append((offset_ms, file_path))
        return entries

    @staticmethod
    def _serialize_analysis_summary(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
