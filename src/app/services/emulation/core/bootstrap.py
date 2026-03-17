from __future__ import annotations

from .session.state import SessionState


def build_bootstrap_payload(live_payload: dict) -> dict[str, object]:
    watched_videos = live_payload.get("watched_videos") or []
    watched_ads = live_payload.get("watched_ads") or []
    return {
        "searched_topics": live_payload.get("topics_searched") or [],
        "watched_videos": watched_videos,
        "watched_ads": watched_ads,
        "videos_watched": live_payload.get("videos_watched"),
        "current_topic": live_payload.get("current_topic"),
        "fatigue": live_payload.get("fatigue"),
        "mode": live_payload.get("mode"),
        "personality": live_payload.get("personality"),
        "seen_video_ids": extract_seen_video_ids(watched_videos),
    }


def extract_seen_video_ids(watched_videos: list[dict]) -> list[str]:
    seen_ids: list[str] = []
    for item in watched_videos:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        video_id = SessionState.video_id_from_url(url)
        if video_id and video_id not in seen_ids:
            seen_ids.append(video_id)
    return seen_ids
