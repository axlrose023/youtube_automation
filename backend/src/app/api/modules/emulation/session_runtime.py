from __future__ import annotations

import datetime
from typing import TypedDict

from app.services.emulation.core.ad_analytics import build_ads_analytics
from app.services.emulation.core.bootstrap import sanitize_watched_ads

from .models import EmulationSessionHistory
from .schema import EmulationSessionStatus
from .utils import (
    calculate_session_elapsed_minutes,
    normalized_ads_count,
    normalized_videos_count,
)


class ResumeSeed(TypedDict, total=False):
    current_topic: str | None
    topics_searched: list[str]
    watched_videos: list[dict]
    watched_ads: list[dict]
    videos_watched: int
    watched_videos_count: int
    watched_ads_count: int
    watched_ads_analytics: list[dict]
    total_duration_seconds: int
    bytes_downloaded: int
    fatigue: float | None
    mode: str | None
    personality: dict | None


def _normalize_live_screenshot_paths(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            offset_ms = item.get("offset_ms")
            file_path = item.get("file_path")
        elif (
            isinstance(item, (list, tuple))
            and len(item) == 2
        ):
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


def _normalize_live_ad_capture(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None

    normalized = dict(value)
    normalized["screenshot_paths"] = _normalize_live_screenshot_paths(
        value.get("screenshot_paths"),
    )
    return normalized


def _normalize_live_watched_ads(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        ad = dict(item)
        ad["capture"] = _normalize_live_ad_capture(item.get("capture"))
        normalized.append(ad)
    return normalized


def normalize_profile_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def build_status_response(
    session_id: str,
    data: dict[str, object],
) -> EmulationSessionStatus:
    orchestration = data.get("orchestration")
    if not isinstance(orchestration, dict):
        orchestration = {}

    status = str(data["status"])
    if status == "running" and bool(data.get("stop_requested")):
        status = "stopping"

    watched_ads = _normalize_live_watched_ads(data.get("watched_ads"))
    return EmulationSessionStatus(
        session_id=session_id,
        status=status,
        profile_id=normalize_profile_id(data.get("profile_id")),
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
        bytes_downloaded=int(data.get("bytes_downloaded", 0) or 0),
        topics_searched=data.get("topics_searched", []),
        videos_watched=int(data.get("videos_watched", 0) or 0),
        watched_videos_count=int(data.get("watched_videos_count", 0) or 0),
        total_duration_seconds=int(data.get("total_duration_seconds", 0) or 0),
        watched_videos=data.get("watched_videos", []),
        current_watch=data.get("current_watch"),
        watched_ads_count=int(data.get("watched_ads_count", 0) or 0),
        watched_ads=watched_ads,
        watched_ads_analytics=data.get("watched_ads_analytics")
        or build_ads_analytics(watched_ads),
        mode=data.get("mode"),
        fatigue=data.get("fatigue"),
        error=data.get("error"),
    )


def last_activity_timestamp(data: dict[str, object]) -> float:
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

    return max(candidates) if candidates else datetime.datetime.now(
        datetime.UTC
    ).timestamp()


def elapsed_minutes_from_live_payload(data: dict[str, object]) -> float:
    started_at = data.get("started_at")
    if isinstance(started_at, int | float):
        finished_at = data.get("finished_at")
        end_ts = (
            float(finished_at)
            if isinstance(finished_at, int | float)
            else datetime.datetime.now(datetime.UTC).timestamp()
        )
        return max((end_ts - float(started_at)) / 60.0, 0.0)
    return max(float(data.get("total_duration_seconds") or 0) / 60.0, 0.0)


def elapsed_minutes_from_history(history: EmulationSessionHistory) -> float:
    if history.started_at:
        end_at = history.finished_at or datetime.datetime.now(datetime.UTC)
        return max((end_at - history.started_at).total_seconds() / 60.0, 0.0)
    return max(float(history.total_duration_seconds or 0) / 60.0, 0.0)


def build_resume_seed_from_live_payload(data: dict[str, object]) -> ResumeSeed:
    watched_videos = data.get("watched_videos") or []
    watched_ads = sanitize_watched_ads(data.get("watched_ads") or [])
    return {
        "current_topic": data.get("current_topic"),
        "topics_searched": data.get("topics_searched") or [],
        "watched_videos": watched_videos,
        "watched_ads": watched_ads,
        "videos_watched": int(data.get("videos_watched") or 0),
        "watched_videos_count": max(
            int(data.get("watched_videos_count") or 0),
            len(watched_videos),
        ),
        "watched_ads_count": max(
            int(data.get("watched_ads_count") or 0),
            len(watched_ads),
        ),
        "watched_ads_analytics": data.get("watched_ads_analytics")
        or build_ads_analytics(watched_ads),
        "total_duration_seconds": int(data.get("total_duration_seconds") or 0),
        "bytes_downloaded": int(data.get("bytes_downloaded") or 0),
        "fatigue": data.get("fatigue"),
        "mode": data.get("mode"),
        "personality": data.get("personality"),
    }


def build_resume_seed_from_history(
    history: EmulationSessionHistory,
) -> ResumeSeed:
    watched_videos = history.watched_videos or []
    watched_ads = sanitize_watched_ads(history.watched_ads or [])
    current_topic = history.current_topic or infer_current_topic(
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
        "watched_ads_analytics": history.watched_ads_analytics
        or build_ads_analytics(watched_ads),
        "total_duration_seconds": int(history.total_duration_seconds or 0),
        "bytes_downloaded": int(history.bytes_downloaded or 0),
        "fatigue": history.fatigue,
        "mode": history.mode,
        "personality": history.personality,
    }


def infer_current_topic(
    topics_searched: list[str],
    watched_videos: list[dict],
) -> str | None:
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


def is_break_phase_active(data: dict[str, object]) -> bool:
    orchestration = data.get("orchestration")
    if not isinstance(orchestration, dict) or not orchestration.get("enabled"):
        return False
    phase = str(orchestration.get("phase") or "")
    next_resume_at = orchestration.get("next_resume_at")
    return (
        phase == "break"
        and isinstance(next_resume_at, int | float)
        and next_resume_at > datetime.datetime.now(datetime.UTC).timestamp()
    )
