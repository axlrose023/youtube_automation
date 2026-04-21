from __future__ import annotations

import time

from app.services.mobile_app.models import AndroidSessionTopicResult


def build_topic_watched_video_payload(
    topic_result: AndroidSessionTopicResult,
    *,
    position: int,
    recorded_at: float | None = None,
) -> dict[str, object]:
    watched_seconds = float(topic_result.watch_seconds or 0.0)
    target_seconds = float(topic_result.target_watch_seconds or 0.0)
    if target_seconds <= 0.0:
        target_seconds = watched_seconds

    if target_seconds > 0.0:
        watch_ratio = round(min(watched_seconds / target_seconds, 1.0), 3)
    else:
        watch_ratio = 1.0 if topic_result.watch_verified else 0.0

    return {
        "position": position,
        "action": "watch",
        "title": topic_result.opened_title or topic_result.topic,
        "url": "",
        "watched_seconds": watched_seconds,
        "target_seconds": target_seconds,
        "watch_ratio": watch_ratio,
        "completed": topic_result.watch_verified,
        "search_keyword": topic_result.topic,
        "matched_topics": [topic_result.topic],
        "keywords": [],
        "recorded_at": recorded_at if recorded_at is not None else time.time(),
    }
