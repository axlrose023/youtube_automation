from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

from fastapi import HTTPException
from app.settings import get_config
from app.services.emulation.common import watched_videos_count

from .models import (
    ANALYSIS_TERMINAL_STATUSES,
    AdCapture,
    AnalysisStatus,
    EmulationSessionHistory,
    PostProcessingStatus,
    SESSION_TERMINAL_STATUSES,
    SessionStatus,
    VideoStatus,
)
from .schema import (
    EmulationAdCaptureHistory,
    EmulationAdCaptureScreenshotPath,
    EmulationCaptureSummary,
    EmulationPostProcessingProgress,
)


def calculate_session_elapsed_minutes(data: dict[str, object]) -> float | None:
    started_at = data.get("started_at")
    if not isinstance(started_at, int | float):
        return None

    status = data.get("status")
    finished_at = data.get("finished_at")
    if status in SESSION_TERMINAL_STATUSES and isinstance(finished_at, int | float):
        return round((finished_at - started_at) / 60, 1)
    return round((time.time() - started_at) / 60, 1)


def resolve_media_path(media_path: str) -> Path:
    base_path = get_config().storage.ad_captures_path.resolve()
    candidate = (base_path / media_path).resolve()

    try:
        candidate.relative_to(base_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Media file not found") from exc

    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")

    return candidate


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
    if not ad_captures:
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


def build_post_processing_state(
    *,
    session_status: SessionStatus | str,
    ad_captures: list[EmulationAdCaptureHistory] | None,
) -> tuple[PostProcessingStatus | None, EmulationPostProcessingProgress | None]:
    if session_status not in SESSION_TERMINAL_STATUSES:
        return None, None
    if not ad_captures:
        return None, None

    analyzable = [
        capture
        for capture in ad_captures
        if capture.video_status == VideoStatus.COMPLETED
    ]
    total = len(analyzable)
    if total == 0:
        return None, None

    done = sum(
        1
        for capture in analyzable
        if str(capture.analysis_status or "").lower() in ANALYSIS_TERMINAL_STATUSES
    )
    failed = sum(
        1
        for capture in analyzable
        if str(capture.analysis_status or "").lower() == AnalysisStatus.FAILED
    )

    if done < total:
        state = PostProcessingStatus.RUNNING
    elif failed > 0:
        state = PostProcessingStatus.FAILED
    else:
        state = PostProcessingStatus.COMPLETED

    return state, EmulationPostProcessingProgress(done=done, total=total)


def normalized_videos_count(payload: EmulationSessionHistory) -> int:
    return watched_videos_count(
        payload.watched_videos or [],
        fallback=payload.watched_videos_count,
    )


def normalized_ads_count(payload: EmulationSessionHistory) -> int:
    return max(payload.watched_ads_count, len(payload.watched_ads or []))


def _parse_analysis_summary(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def normalize_screenshot_paths(
    value: object,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            offset_ms = item.get("offset_ms")
            file_path = item.get("file_path")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            offset_ms, file_path = item
        else:
            continue

        if not isinstance(offset_ms, int | float) or not isinstance(file_path, str):
            continue
        normalized.append(
            {
                "offset_ms": int(offset_ms),
                "file_path": file_path,
            },
        )
    return normalized


def normalize_watched_ads_payload(
    watched_ads: object,
) -> list[dict[str, object]]:
    if not isinstance(watched_ads, list):
        return []

    normalized: list[dict[str, object]] = []
    for item in watched_ads:
        if not isinstance(item, dict):
            continue
        ad = dict(item)
        capture = item.get("capture")
        if isinstance(capture, dict):
            capture_payload = dict(capture)
            capture_payload["screenshot_paths"] = normalize_screenshot_paths(
                capture.get("screenshot_paths"),
            )
            ad["capture"] = capture_payload
        normalized.append(ad)
    return normalized


def map_ad_capture(capture: AdCapture) -> EmulationAdCaptureHistory:
    analysis_status = capture.analysis_status
    hide_media = str(analysis_status) == AnalysisStatus.NOT_RELEVANT
    screenshot_paths = [
        EmulationAdCaptureScreenshotPath(offset_ms=s.offset_ms, file_path=s.file_path)
        for s in sorted(capture.screenshots, key=lambda x: x.offset_ms)
    ]
    if hide_media:
        screenshot_paths = []
    return EmulationAdCaptureHistory(
        ad_position=capture.ad_position,
        advertiser_domain=capture.advertiser_domain,
        cta_href=capture.cta_href,
        display_url=capture.display_url,
        headline_text=capture.headline_text,
        ad_duration_seconds=capture.ad_duration_seconds,
        landing_url=None if hide_media else capture.landing_url,
        landing_dir=None if hide_media else capture.landing_dir,
        landing_status=capture.landing_status,
        video_src_url=capture.video_src_url,
        video_file=None if hide_media else capture.video_file,
        video_status=capture.video_status,
        analysis_status=analysis_status,
        analysis_summary=_parse_analysis_summary(capture.analysis_summary),
        screenshot_paths=screenshot_paths,
    )
