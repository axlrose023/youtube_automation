from __future__ import annotations

import datetime
import json
import time

from app.api.modules.ad_captures.models import AdCapture, VideoStatus

from .models import EmulationSessionHistory
from .schema import (
    EmulationAdCaptureHistory,
    EmulationAdCaptureScreenshotPath,
    EmulationCaptureSummary,
)


def calculate_session_elapsed_minutes(data: dict[str, object]) -> float | None:
    started_at = data.get("started_at")
    if not isinstance(started_at, int | float):
        return None

    status = data.get("status")
    finished_at = data.get("finished_at")
    if status in {"completed", "failed", "stopped"} and isinstance(finished_at, int | float):
        return round((finished_at - started_at) / 60, 1)
    return round((time.time() - started_at) / 60, 1)


def calculate_history_elapsed_minutes(payload: EmulationSessionHistory) -> float | None:
    if not payload.started_at:
        return None
    finished_at = payload.finished_at or datetime.datetime.now(datetime.UTC)
    elapsed = (finished_at - payload.started_at).total_seconds() / 60
    return round(max(elapsed, 0.0), 1)


def build_capture_summary(
    ad_captures: list[EmulationAdCaptureHistory] | None,
    fallback_ads_total: int,
    fallback_video_captures: int,
    fallback_screenshot_fallbacks: int,
) -> EmulationCaptureSummary:
    if ad_captures is None:
        return EmulationCaptureSummary(
            ads_total=fallback_ads_total,
            video_captures=fallback_video_captures,
            screenshot_fallbacks=fallback_screenshot_fallbacks,
        )
    return EmulationCaptureSummary(
        ads_total=len(ad_captures),
        video_captures=sum(1 for capture in ad_captures if capture.video_status == VideoStatus.COMPLETED),
        screenshot_fallbacks=sum(
            1 for capture in ad_captures if capture.video_status == VideoStatus.FALLBACK_SCREENSHOTS
        ),
    )


def normalized_videos_count(payload: EmulationSessionHistory) -> int:
    return max(payload.watched_videos_count, len(payload.watched_videos or []))


def normalized_ads_count(payload: EmulationSessionHistory) -> int:
    return max(payload.watched_ads_count, len(payload.watched_ads or []))


def _parse_analysis_summary(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def map_ad_capture(capture: AdCapture) -> EmulationAdCaptureHistory:
    screenshot_paths = [
        EmulationAdCaptureScreenshotPath(offset_ms=s.offset_ms, file_path=s.file_path)
        for s in sorted(capture.screenshots, key=lambda x: x.offset_ms)
    ]
    return EmulationAdCaptureHistory(
        ad_position=capture.ad_position,
        advertiser_domain=capture.advertiser_domain,
        cta_href=capture.cta_href,
        display_url=capture.display_url,
        headline_text=capture.headline_text,
        ad_duration_seconds=capture.ad_duration_seconds,
        landing_url=capture.landing_url,
        landing_dir=capture.landing_dir,
        landing_status=capture.landing_status,
        video_src_url=capture.video_src_url,
        video_file=capture.video_file,
        video_status=capture.video_status,
        analysis_status=capture.analysis_status,
        analysis_summary=_parse_analysis_summary(capture.analysis_summary),
        screenshot_paths=screenshot_paths,
    )
