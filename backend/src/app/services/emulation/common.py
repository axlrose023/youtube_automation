from __future__ import annotations

import datetime


def to_utc_datetime(value: object) -> datetime.datetime | None:
    if isinstance(value, int | float):
        return datetime.datetime.fromtimestamp(float(value), tz=datetime.UTC)
    return None


def derive_watched_video_counters(
    watched_videos: object,
    *,
    fallback_completed: int = 0,
    fallback_total: int = 0,
) -> tuple[int, int]:
    normalized = _normalize_watched_videos(watched_videos)
    if normalized:
        total = len(normalized)
        completed = sum(1 for item in normalized if bool(item.get("completed")))
        return completed, total

    completed = _coerce_non_negative_int(fallback_completed)
    total = max(_coerce_non_negative_int(fallback_total), completed)
    return completed, total


def completed_watched_videos_count(
    watched_videos: object,
    *,
    fallback: int = 0,
) -> int:
    completed, _ = derive_watched_video_counters(
        watched_videos,
        fallback_completed=fallback,
        fallback_total=fallback,
    )
    return completed


def watched_videos_count(
    watched_videos: object,
    *,
    fallback: int = 0,
) -> int:
    _, total = derive_watched_video_counters(
        watched_videos,
        fallback_completed=fallback,
        fallback_total=fallback,
    )
    return total


def _normalize_watched_videos(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def _coerce_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    return 0
