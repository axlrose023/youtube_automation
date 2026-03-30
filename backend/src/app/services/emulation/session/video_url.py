from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def video_id_from_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    try:
        parsed = urlparse(raw_url)
        if "/watch" in parsed.path:
            return parse_qs(parsed.query).get("v", [None])[0]
        if "/shorts/" in parsed.path:
            short_id = parsed.path.split("/shorts/")[-1].split("/", 1)[0]
            return short_id or None
    except Exception:
        return None
    return None


def is_same_video_url(left_url: str, right_url: str) -> bool:
    left_id = video_id_from_url(left_url)
    right_id = video_id_from_url(right_url)
    if left_id and right_id:
        return left_id == right_id
    if left_url and right_url:
        return left_url == right_url
    return False
