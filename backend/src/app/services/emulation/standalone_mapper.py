from __future__ import annotations

import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.api.modules.emulation.models import LandingStatus, VideoStatus

_BANNER_SPONSORED_PREFIX_RE = re.compile(r"^\s*sponsored\s*-\s*", re.IGNORECASE)
_BANNER_DOMAIN_RE = re.compile(
    r"(?i)\b(?:https?://)?(?:www\.)?"
    r"([a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)*\.[a-z]{2,})"
    r"(?:/[^\s]*)?"
)
_BANNER_CTA_RE = re.compile(
    r"\s+(?:-|\|)\s+("
    r"visit site|learn more|book now|sign up|shop now|install|open|download|"
    r"apply now|get quote|contact us|visit store|visit today|докладніше|докладнее|"
    r"подайте заявку|почати"
    r")\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StandaloneLivePayload:
    topics_searched: list[str]
    watched_videos: list[dict[str, object]]
    watched_ads: list[dict[str, object]]
    total_watch_seconds: float


def build_standalone_live_payload(
    *,
    topic_records: list[object],
    run_dir: Path,
    storage_base: Path,
    recorded_at: float | None = None,
) -> StandaloneLivePayload:
    recorded_at = time.time() if recorded_at is None else recorded_at
    topics_searched = _topics_searched(topic_records)
    watched_videos = _build_watched_videos(topic_records, recorded_at=recorded_at)
    watched_ads = _build_watched_ads(
        topic_records=topic_records,
        run_dir=run_dir,
        storage_base=storage_base,
        recorded_at=recorded_at,
    )
    total_watch_seconds = round(
        sum(_as_float(_get(record, "watch_seconds")) for record in topic_records),
        2,
    )
    return StandaloneLivePayload(
        topics_searched=topics_searched,
        watched_videos=watched_videos,
        watched_ads=watched_ads,
        total_watch_seconds=total_watch_seconds,
    )


def _topics_searched(topic_records: list[object]) -> list[str]:
    seen: set[str] = set()
    topics: list[str] = []
    for record in topic_records:
        topic = _as_str(_get(record, "topic"))
        if topic and topic not in seen:
            seen.add(topic)
            topics.append(topic)
    return topics


def _build_watched_videos(
    topic_records: list[object],
    *,
    recorded_at: float,
) -> list[dict[str, object]]:
    watched_videos: list[dict[str, object]] = []
    for record in topic_records:
        topic = _as_str(_get(record, "topic"))
        opened_videos = _get(record, "opened_videos")
        if not isinstance(opened_videos, list) or not opened_videos:
            opened_video = _get(record, "opened_video")
            opened_videos = [opened_video] if opened_video else []

        fallback_share = 0.0
        if opened_videos:
            fallback_share = _as_float(_get(record, "watch_seconds")) / len(opened_videos)

        for opened in opened_videos:
            if not isinstance(opened, dict):
                continue
            watched_seconds = _as_float(opened.get("watch_seconds"))
            if watched_seconds <= 0:
                watched_seconds = fallback_share
            watched_seconds = round(max(watched_seconds, 0.0), 2)
            if watched_seconds <= 0:
                continue

            target_seconds = watched_seconds
            completed = watched_seconds >= 15.0
            title = _as_str(opened.get("title")) or topic
            video_title = _as_str(opened.get("video_title")) or title
            channel_name = _as_str(opened.get("channel_name")) or None
            liked = bool(opened.get("liked"))
            subscribed = bool(opened.get("subscribed"))
            social_actions = [
                str(action)
                for action in (opened.get("social_actions") or [])
                if str(action).strip()
            ]
            watched_videos.append(
                {
                    "position": len(watched_videos) + 1,
                    "action": "watch",
                    "title": title,
                    "video_title": video_title,
                    "channel_name": channel_name,
                    "url": "",
                    "watched_seconds": watched_seconds,
                    "target_seconds": target_seconds,
                    "watch_ratio": 1.0 if target_seconds > 0 else 0.0,
                    "watch_verified": completed,
                    "completed": completed,
                    "search_keyword": topic,
                    "matched_topics": [topic] if topic else [],
                    "keywords": [],
                    "end_reason": opened.get("end_reason"),
                    "like_planned": bool(opened.get("like_planned")),
                    "liked": liked,
                    "liked_video_title": _as_str(opened.get("liked_video_title")) or None,
                    "liked_at": _as_str(opened.get("liked_at")) or None,
                    "subscribe_planned": bool(opened.get("subscribe_planned")),
                    "subscribed": subscribed,
                    "subscribed_channel_name": (
                        _as_str(opened.get("subscribed_channel_name")) or None
                    ),
                    "subscribed_at": _as_str(opened.get("subscribed_at")) or None,
                    "social_actions": social_actions,
                    "recorded_at": recorded_at,
                }
            )
    return watched_videos


def _build_watched_ads(
    *,
    topic_records: list[object],
    run_dir: Path,
    storage_base: Path,
    recorded_at: float,
) -> list[dict[str, object]]:
    capture_items: list[tuple[float, int, dict[str, object]]] = []
    fallback_order = 0
    for record in topic_records:
        topic = _as_str(_get(record, "topic"))

        for banner in _as_list(_get(record, "banners")):
            fallback_order += 1
            capture_items.append(
                (
                    _capture_time(banner, fallback_order),
                    fallback_order,
                    _map_banner(
                        banner=banner,
                        topic=topic,
                        run_dir=run_dir,
                        storage_base=storage_base,
                        recorded_at=recorded_at,
                    ),
                )
            )

        for ad in _as_list(_get(record, "ads")):
            fallback_order += 1
            capture_items.append(
                (
                    _capture_time(ad, fallback_order),
                    fallback_order,
                    _map_video_ad(
                        ad=ad,
                        topic=topic,
                        run_dir=run_dir,
                        storage_base=storage_base,
                        recorded_at=recorded_at,
                    ),
                )
            )

    watched_ads: list[dict[str, object]] = []
    for position, (_, _, ad) in enumerate(sorted(capture_items), start=1):
        ad["position"] = position
        watched_ads.append(ad)
    return watched_ads


def _map_banner(
    *,
    banner: object,
    topic: str,
    run_dir: Path,
    storage_base: Path,
    recorded_at: float,
) -> dict[str, object]:
    landing_url = _as_str(_get(banner, "landing_url")) or None
    screenshot_paths: list[dict[str, object]] = []
    screenshot = _media_ref(
        _as_str(_get(banner, "screenshot")),
        run_dir=run_dir,
        storage_base=storage_base,
    )
    if screenshot:
        screenshot_paths.append({"offset_ms": 0, "file_path": screenshot})
    landing_screenshot = _media_ref(
        _as_str(_get(banner, "landing_screenshot")),
        run_dir=run_dir,
        storage_base=storage_base,
    )
    if landing_screenshot:
        screenshot_paths.append({"offset_ms": 1000, "file_path": landing_screenshot})

    title = _as_str(_get(banner, "title"))
    parsed_text = _parse_banner_text(title)
    advertiser_domain = _domain_from_url(landing_url) or parsed_text["display_domain"]
    headline_text = parsed_text["headline"] or advertiser_domain or title
    description_lines = parsed_text["description_lines"]
    description_text = "\n".join(description_lines)
    return {
        "started_at": recorded_at,
        "ended_at": recorded_at,
        "watched_seconds": 0.0,
        "completed": bool(landing_url or screenshot_paths),
        "skip_clicked": False,
        "skip_visible": False,
        "cta_text": parsed_text["cta_text"] or _infer_banner_cta(title),
        "cta_href": landing_url,
        "sponsor_label": "Sponsored",
        "advertiser_domain": advertiser_domain,
        "display_url": advertiser_domain,
        "landing_urls": [landing_url] if landing_url else [],
        "headline_text": headline_text,
        "description_text": description_text,
        "description_lines": description_lines,
        "my_ad_center_visible": False,
        "full_text": title,
        "full_text_source": "standalone_banner",
        "full_visible_text": title,
        "full_caption_text": "",
        "visible_lines": parsed_text["visible_lines"],
        "caption_lines": [],
        "end_reason": "banner",
        "search_keyword": topic,
        "recorded_at": recorded_at,
        "capture": {
            "video_status": (
                VideoStatus.FALLBACK_SCREENSHOTS
                if screenshot_paths
                else VideoStatus.NO_SRC
            ),
            "landing_url": landing_url,
            "landing_status": (
                LandingStatus.COMPLETED
                if landing_url or landing_screenshot
                else LandingStatus.SKIPPED
            ),
            "landing_dir": None,
            "screenshot_paths": screenshot_paths,
        },
    }


def _map_video_ad(
    *,
    ad: object,
    topic: str,
    run_dir: Path,
    storage_base: Path,
    recorded_at: float,
) -> dict[str, object]:
    landing_url = _as_str(_get(ad, "landing_url")) or None
    video_file = _media_ref(
        _as_str(_get(ad, "video")),
        run_dir=run_dir,
        storage_base=storage_base,
    )
    landing_screenshot = _media_ref(
        _as_str(_get(ad, "landing_screenshot")),
        run_dir=run_dir,
        storage_base=storage_base,
    )
    screenshot_paths = (
        [{"offset_ms": 0, "file_path": landing_screenshot}]
        if landing_screenshot
        else []
    )
    watched_seconds = round(_as_float(_get(ad, "recorded_seconds")), 2)
    cta_label = _as_str(_get(ad, "cta_label")) or None
    advertiser_domain = _domain_from_url(landing_url)
    return {
        "started_at": recorded_at,
        "ended_at": recorded_at + watched_seconds,
        "watched_seconds": watched_seconds,
        "completed": bool(video_file or landing_url or landing_screenshot),
        "skip_clicked": False,
        "skip_visible": False,
        "cta_text": cta_label,
        "cta_href": landing_url,
        "sponsor_label": "Sponsored",
        "advertiser_domain": advertiser_domain,
        "display_url": advertiser_domain,
        "landing_urls": [landing_url] if landing_url else [],
        "headline_text": advertiser_domain or cta_label or "Video ad",
        "description_text": "",
        "description_lines": [],
        "ad_duration_seconds": watched_seconds,
        "my_ad_center_visible": False,
        "full_text": " ".join(part for part in (cta_label, advertiser_domain) if part),
        "full_text_source": "standalone_video_ad",
        "full_visible_text": " ".join(part for part in (cta_label, advertiser_domain) if part),
        "full_caption_text": "",
        "visible_lines": [part for part in (cta_label, advertiser_domain) if part],
        "caption_lines": [],
        "end_reason": "video_ad",
        "search_keyword": topic,
        "recorded_at": recorded_at,
        "capture": {
            "video_file": video_file,
            "video_status": (
                VideoStatus.COMPLETED if video_file else VideoStatus.NO_SRC
            ),
            "recorded_video_duration_seconds": watched_seconds,
            "landing_url": landing_url,
            "landing_status": (
                LandingStatus.COMPLETED
                if landing_url or landing_screenshot
                else LandingStatus.SKIPPED
            ),
            "landing_dir": None,
            "screenshot_paths": screenshot_paths,
        },
    }


def _media_ref(value: str, *, run_dir: Path, storage_base: Path) -> str | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    path = path.resolve()
    try:
        return path.relative_to(storage_base.resolve()).as_posix()
    except ValueError:
        return str(path)


def _capture_time(item: object, fallback_order: int) -> float:
    value = _get(item, "captured_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(fallback_order)


def _parse_banner_text(title: str) -> dict[str, Any]:
    visible_lines = [_clean_banner_line(line) for line in title.splitlines()]
    visible_lines = [line for line in visible_lines if line]
    if not visible_lines:
        return {
            "headline": "",
            "description_lines": [],
            "display_domain": None,
            "cta_text": None,
            "visible_lines": [],
        }

    content_lines = list(visible_lines)
    content_lines[0] = _BANNER_SPONSORED_PREFIX_RE.sub("", content_lines[0]).strip()
    content_lines = [line for line in content_lines if line]

    first_line = content_lines[0] if content_lines else visible_lines[0]
    headline = _compact_banner_headline(first_line)
    if headline.casefold() in {"visit site banner", "visual sponsored card"}:
        headline = ""

    description_lines: list[str] = []
    for index, line in enumerate(content_lines[1:], start=1):
        is_last = index == len(content_lines) - 1
        if is_last and _BANNER_CTA_RE.search(line):
            continue
        cleaned = _strip_banner_urls(_BANNER_CTA_RE.sub("", line)).strip(" -")
        if cleaned:
            description_lines.append(cleaned)

    return {
        "headline": headline,
        "description_lines": description_lines,
        "display_domain": _domain_from_text(title),
        "cta_text": _extract_banner_cta(title),
        "visible_lines": visible_lines,
    }


def _compact_banner_headline(value: str) -> str:
    text = _strip_banner_urls(_BANNER_CTA_RE.sub("", value)).strip(" -")
    parts = [part.strip() for part in re.split(r"\s+-\s+", text) if part.strip()]
    return parts[0] if parts else text


def _extract_banner_cta(value: str) -> str | None:
    for line in reversed(value.splitlines() or [value]):
        match = _BANNER_CTA_RE.search(_clean_banner_line(line))
        if match:
            return match.group(1)
    match = _BANNER_CTA_RE.search(_clean_banner_line(value))
    return match.group(1) if match else None


def _domain_from_text(value: str) -> str | None:
    for match in _BANNER_DOMAIN_RE.finditer(value):
        domain = match.group(1).removeprefix("www.").lower()
        if domain not in {"google.com", "youtube.com"}:
            return domain
    return None


def _strip_banner_urls(value: str) -> str:
    return _BANNER_DOMAIN_RE.sub("", value)


def _clean_banner_line(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _infer_banner_cta(title: str) -> str | None:
    low = title.lower()
    for label in (
        "Visit site",
        "Learn more",
        "Book now",
        "Sign up",
        "Shop now",
        "Install",
    ):
        if label.lower() in low:
            return label
    return None


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname
    except Exception:
        return None
    if not host:
        return None
    return host.removeprefix("www.").lower()


def _get(item: object, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
