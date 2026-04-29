from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.api.modules.emulation.models import AnalysisStatus, LandingStatus, VideoStatus
from app.services.mobile_app.models import AndroidWatchSample

from .ads import AndroidAdCtaProbeResult
from . import selectors

logger = logging.getLogger(__name__)
_MAX_RELIABLE_AD_DURATION_SECONDS = 600.0
_MAX_FALLBACK_SEEKBAR_AD_DURATION_SECONDS = 300.0

_DISPLAY_URL_PATTERN = re.compile(
    r"(?i)\b((?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?)"
)
_TIMECODE_PATTERN = re.compile(r"(?<!\d)(?:(\d+):)?([0-5]?\d):([0-5]\d)(?!\d)")
_AD_DURATION_HINT_TOKENS = (
    "sponsored",
    "my ad center",
    "реклама",
    "реклам",
    "спонс",
    "advertiser",
    "visit advertiser",
)
_GENERIC_CTA_TEXTS = {
    "visit advertiser",
}
_GENERIC_VISIBLE_LINES = {
    "video player",
    "expand mini player",
    "drag handle",
    "like ad",
    "share ad",
    "more options",
    "close ad panel",
    "more info",
    "clear",
    "voice search",
    "cast",
    "navigate up",
    "enter fullscreen",
    "exit fullscreen",
    "captions unavailable",
    "captions",
    "pause video",
    "play video",
    "minimize",
    "mute",
    "unmute",
    "settings",
    "my ad center",
    "visit site",
    "visit advertiser",
    "skip ad",
    "skip",
    "sponsored",
    "description",
    "more",
    "dismiss",
    "close",
}
_PRESERVED_GENERIC_VISIBLE_LINES = {
    "sponsored",
    "visit advertiser",
    "visit site",
}
_MAX_SCREENRECORD_FLOOR_FIRST_AD_OFFSET_SECONDS = 12.0
_GENERIC_VISIBLE_SUBSTRINGS = (
    "minutes of",
    "minute of",
    "seconds of",
)
_YOUTUBE_SEARCH_NOISE_SUBSTRINGS = (
    " views",
    " view",
    " million views",
    " years ago",
    " year ago",
    " months ago",
    " month ago",
    " weeks ago",
    " week ago",
    " days ago",
    " day ago",
    " hours ago",
    " hour ago",
    "play short",
    "- play ",
    "subscribers",
    "navigate up",
    "go to channel",
    "subscriptions:",
    "new content available",
    "new content is available",
    "tap to refresh",
    "shorts remix",
    "close sheet",
    "elapsed of",
    "home:",
    "shorts:",
    "you:",
    "library:",
    "notifications",
    "search youtube",
    "watch later",
    "customize and control",
    "google chrome",
    "tap for more",
    "description.",
    "description:",
    "show description",
    "related videos",
    "up next",
    "autoplay",
    "share",
    "save to playlist",
    "download",
    "scrub bar",
    "add to queue",
    "minimized player",
    "mini player",
    "close mini",
    "close player",
    "close video",
    "live chat",
    "like this video",
    "other people",
    "subscriber",
    "verified",
    "channel logo",
    "show channel",
    "comments",
    "reply",
    "show less",
    "show more",
    "of 1 minute",
    "of 2 minute",
    "of 3 minute",
    "of 4 minute",
    "of 5 minute",
    "of 6 minute",
    "of 7 minute",
    "of 8 minute",
    "of 9 minute",
    "of 10 minute",
    "of 11 minute",
    "of 12 minute",
    "of 13 minute",
    "of 14 minute",
    "of 15 minute",
    "next video",
    "previous video",
    "replay",
    "expand description",
    "collapse description",
    # HTTP error pages returned by landing scraper
    "access denied",
    "403 forbidden",
    "404 not found",
    "just a moment",
    "enable javascript",
    "checking your browser",
    "please wait",
)
_SUPPRESSED_ADVERTISER_HOSTS = {
    "www.googleadservices.com",
    "googleadservices.com",
    "www.google.com",
    "google.com",
    "doubleclick.net",
    "www.doubleclick.net",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "consent.youtube.com",
    "consent.google.com",
    "accounts.google.com",
    "play.google.com",
    "support.google.com",
    "googleads.g.doubleclick.net",
}
_GOOGLE_CLICK_HOSTS = {
    "www.google.com",
    "google.com",
    "googleadservices.com",
    "www.googleadservices.com",
}


def _looks_like_youtube_noise(value: str | None) -> bool:
    if not value or not isinstance(value, str):
        return False
    lowered = value.strip().casefold()
    if not lowered:
        return False
    return any(frag in lowered for frag in _YOUTUBE_SEARCH_NOISE_SUBSTRINGS)


def _unwrap_google_click_url(value: str | None) -> str | None:
    """Extract the real advertiser URL from a Google aclk/adurl redirect."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if host not in _GOOGLE_CLICK_HOSTS:
        return None
    qs = parse_qs(parsed.query)
    for key in ("adurl", "url", "q"):
        values = qs.get(key) or []
        for raw in values:
            decoded = unquote(raw) if raw else ""
            if decoded and "://" in decoded:
                return decoded
    return None


def _normalize_identity_value(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.casefold()


def _sample_identity_markers(sample: AndroidWatchSample) -> tuple[str, ...]:
    markers: list[str] = []
    for raw in (sample.ad_headline_text, sample.ad_display_url):
        normalized = _normalize_identity_value(raw)
        if normalized and normalized not in markers:
            markers.append(normalized)
    cta_value = _normalize_identity_value(sample.ad_cta_text)
    if cta_value and cta_value not in markers:
        markers.append(f"cta:{cta_value}")
    return tuple(markers)


def _sample_starts_new_ad_segment(
    previous: AndroidWatchSample,
    current: AndroidWatchSample,
) -> bool:
    previous_progress = previous.ad_progress_seconds
    current_progress = current.ad_progress_seconds
    if isinstance(previous_progress, (int, float)) and isinstance(current_progress, (int, float)):
        if current_progress + 1.0 < previous_progress:
            return True

    previous_duration = previous.ad_duration_seconds
    current_duration = current.ad_duration_seconds
    if (
        isinstance(previous_duration, (int, float))
        and isinstance(current_duration, (int, float))
    ):
        duration_delta = abs(float(current_duration) - float(previous_duration))
        if duration_delta >= 10.0:
            return True
        if (
            duration_delta >= 2.0
            and isinstance(current_progress, (int, float))
            and isinstance(previous_progress, (int, float))
            and current_progress <= previous_progress
        ):
            return True

    previous_markers = set(_sample_identity_markers(previous))
    current_markers = set(_sample_identity_markers(current))
    if previous_markers and current_markers and previous_markers != current_markers:
        return True
    return False


def _sample_has_segment_signal(sample: AndroidWatchSample) -> bool:
    return bool(
        _sample_identity_markers(sample)
        or isinstance(sample.ad_progress_seconds, (int, float))
        or isinstance(sample.ad_duration_seconds, (int, float))
    )


def _segment_reference_sample(segment: list[AndroidWatchSample]) -> AndroidWatchSample:
    for sample in reversed(segment):
        if _sample_has_segment_signal(sample):
            return sample
    return segment[-1]


def _split_ad_segments(ad_samples: list[AndroidWatchSample]) -> list[list[AndroidWatchSample]]:
    if not ad_samples:
        return []
    segments: list[list[AndroidWatchSample]] = [[ad_samples[0]]]
    for sample in ad_samples[1:]:
        current_segment = segments[-1]
        reference_sample = _segment_reference_sample(current_segment)
        if _sample_starts_new_ad_segment(reference_sample, sample):
            segments.append([sample])
            continue
        current_segment.append(sample)
    return segments


def _segment_matches_probe(
    segment: list[AndroidWatchSample],
    ad_cta_result: AndroidAdCtaProbeResult | None,
) -> bool:
    if ad_cta_result is None:
        return False
    probe_values = {
        _normalize_identity_value(ad_cta_result.pre_click_headline_text),
        _normalize_identity_value(ad_cta_result.pre_click_display_url),
    }
    probe_values.discard(None)
    if not probe_values:
        return False
    segment_values: set[str] = set()
    for sample in segment:
        segment_values.update(_sample_identity_markers(sample))
    return bool(segment_values & probe_values)


def _select_ad_samples(
    ad_samples: list[AndroidWatchSample],
    ad_cta_result: AndroidAdCtaProbeResult | None,
) -> tuple[list[AndroidWatchSample], list[str]]:
    segments = _split_ad_segments(ad_samples)
    if len(segments) <= 1:
        return ad_samples, []

    notes = [f"multi_ad_segments:{len(segments)}"]
    for index, segment in enumerate(segments):
        if _segment_matches_probe(segment, ad_cta_result):
            notes.append(f"selected_segment:probe_match:{index + 1}")
            return segment, notes

    notes.append(f"selected_segment:last:{len(segments)}")
    return segments[-1], notes


def build_watched_ad_record(
    *,
    watch_samples: list[AndroidWatchSample],
    watch_debug_screen_path: Path | None,
    watch_debug_page_source_path: Path | None,
    ad_cta_result: AndroidAdCtaProbeResult | None,
    recorded_video_path: str | None = None,
    recorded_video_duration_seconds: float | None = None,
    force_from_debug: bool = False,
    search_topic: str | None = None,
) -> dict[str, Any] | None:
    ad_samples = [sample for sample in watch_samples if sample.ad_detected]
    logger.info(
        "build_ad_record: total_samples=%d ad_samples=%d force_from_debug=%s "
        "has_debug_xml=%s has_cta_result=%s video_path=%s video_dur=%s",
        len(watch_samples),
        len(ad_samples),
        force_from_debug,
        watch_debug_page_source_path is not None,
        ad_cta_result is not None,
        recorded_video_path,
        recorded_video_duration_seconds,
    )
    if ad_samples:
        for i, s in enumerate(ad_samples[-3:]):
            logger.info(
                "build_ad_record: sample[-%d] offset=%s prog=%s dur=%s skip=%s "
                "headline=%s display=%s cta=%s",
                len(ad_samples) - i,
                s.offset_seconds,
                s.ad_progress_seconds,
                s.ad_duration_seconds,
                s.skip_available,
                (s.ad_headline_text or "")[:40],
                (s.ad_display_url or "")[:40],
                (s.ad_cta_text or "")[:30],
            )
    if not ad_samples:
        if not force_from_debug:
            logger.info("build_ad_record: returning None — no ad_samples, force_from_debug=False")
            return None
        # No ad samples but debug XML or cta_result may have data — build minimal record
        if not watch_debug_page_source_path and not ad_cta_result:
            logger.info("build_ad_record: returning None — no ad_samples, no debug_xml, no cta_result")
            return None

    selected_ad_samples, selection_notes = _select_ad_samples(ad_samples, ad_cta_result)
    if selected_ad_samples:
        ad_samples = selected_ad_samples

    debug_metadata = _parse_debug_watch_metadata(watch_debug_page_source_path)
    debug_visible_lines = list(debug_metadata.get("visible_lines") or [])
    latest = ad_samples[-1]
    first = ad_samples[0]
    sponsor_label = debug_metadata.get("sponsor_label") or _pick_last_str(
        sample.ad_sponsor_label for sample in ad_samples
    )
    _pre_click_headline_raw = (
        ad_cta_result.pre_click_headline_text if ad_cta_result else None
    )
    _pre_click_display_raw = (
        ad_cta_result.pre_click_display_url if ad_cta_result else None
    )
    headline_text = _pre_click_headline_raw or debug_metadata.get("headline_text") or _pick_last_str(
        sample.ad_headline_text for sample in ad_samples
    )
    # Reject headlines that are obviously not ad copy: channel-subscribe rows,
    # video player timecode, "Go to channel" labels, navigation buttons,
    # or YouTube live-chat messages ("@channel. text...").
    if headline_text:
        _h = headline_text.strip()
        _h_low = _h.casefold()
        # "0 minutes 4 seconds elapsed of 25 minutes 12 seconds", "0:04 of 0:11"
        _is_timecode = bool(re.match(r"^\d+\s+(minutes?|seconds?|hours?)\b", _h, re.IGNORECASE)) \
            or bool(re.match(r"^\d+\s*[:.]?\d*\s*(of|/)\s*\d+", _h, re.IGNORECASE)) \
            or bool(re.search(r"\belapsed of\b", _h_low)) \
            or bool(re.search(r"\d+\s*(minutes?|seconds?|hours?)\s+\d+\s*(minutes?|seconds?|hours?)", _h_low))
        _is_channel = (
            _h_low.startswith("subscribe to ")
            or _h_low.startswith("go to channel")
            or _h_low.startswith("playlist")
            or _h_low.startswith("video player")
            or _h_low.startswith("navigate up")
            or _h_low.startswith("action menu")
            or _h_low == "video player"
        )
        _is_live_chat = bool(re.match(r"^@[a-z0-9_\-]+", _h, re.IGNORECASE))
        # CTA labels mistakenly captured as headlines ("Watch", "Install",
        # "Learn more", "Visit site"). These are short and never real ad copy.
        _cta_label_set = {
            "watch", "install", "learn more", "visit site", "shop now",
            "sign up", "subscribe", "open an account", "view channel",
        }
        _is_cta_label = _h_low in _cta_label_set or len(_h) < 6
        # YouTube UI elements that get scraped as ad headlines.
        _ui_label_set = {
            "shorts remix",
            "new content available",
            "new content is available",
            "close sheet",
            "close ad panel",
            "close mini player",
            "expand mini player",
            "close player",
            "close video",
            "tap to refresh",
            "my ad center",
            "sponsored my ad center",
            "skip ad",
            "skip ads",
            "drag handle",
            "more options",
            "more info",
            "watch later",
            "in this video",
        }
        _is_ui_label = _h_low in _ui_label_set
        # "Sponsored · 2 of 2 · 0:04 My Ad Center" — sponsored label dressed up
        # with a pod counter and timecode is not real headline copy.
        _is_sponsored_meta = bool(
            re.match(r"^sponsored\b.*\b(my ad center|of\s+\d|·)", _h_low)
        )
        if _is_timecode or _is_channel or _is_live_chat or _is_cta_label or _is_ui_label or _is_sponsored_meta:
            headline_text = None
        elif search_topic and headline_text and headline_text.strip().casefold() == search_topic.strip().casefold():
            headline_text = None
    display_url = _pre_click_display_raw or debug_metadata.get("display_url") or _pick_last_str(
        sample.ad_display_url for sample in ad_samples
    )
    raw_cta_text = debug_metadata.get("cta_text") or _pick_last_str(
        sample.ad_cta_text for sample in ad_samples
    ) or (
        ad_cta_result.label if ad_cta_result else None
    )
    resolved_landing_url = ad_cta_result.landing_url if ad_cta_result else None
    # Prefer pre-click read from the live overlay; fall back to debug XML / samples.
    pre_click_display_url = (
        ad_cta_result.pre_click_display_url if ad_cta_result else None
    )
    pre_click_headline = (
        ad_cta_result.pre_click_headline_text if ad_cta_result else None
    )
    landing_metadata = _parse_landing_metadata(
        ad_cta_result.debug_page_source_path if ad_cta_result else None,
        ad_cta_result.destination_package if ad_cta_result else None,
    )
    ad_text_hints = _dedupe_strings(
        [
            sponsor_label,
            headline_text,
            display_url,
            raw_cta_text,
            *(sample.ad_sponsor_label for sample in ad_samples),
            *(sample.ad_headline_text for sample in ad_samples),
            *(sample.ad_display_url for sample in ad_samples),
            *(sample.ad_cta_text for sample in ad_samples),
            *(line for sample in ad_samples for line in sample.ad_visible_lines),
            *(label for sample in ad_samples for label in sample.ad_signal_labels),
            *debug_visible_lines,
        ]
    )
    extracted_cta_candidates = _extract_cta_candidates(ad_text_hints)
    cta_candidates = _dedupe_strings(
        [
            *(label for sample in ad_samples for label in sample.ad_cta_labels),
            debug_metadata.get("cta_text"),
            raw_cta_text,
            *extracted_cta_candidates,
            ad_cta_result.label if ad_cta_result else None,
            landing_metadata.get("install_cta"),
        ]
    )
    cta_text = raw_cta_text or _choose_primary_cta(cta_candidates)
    ad_offsets = [
        float(sample.offset_seconds)
        for sample in ad_samples
        if isinstance(sample.offset_seconds, (int, float))
    ]
    first_ad_offset_seconds = min(ad_offsets) if ad_offsets else None
    last_ad_offset_seconds = max(ad_offsets) if ad_offsets else None
    ad_duration_seconds = _coalesce_number(
        debug_metadata.get("ad_duration_seconds"),
        latest.ad_duration_seconds,
        first.ad_duration_seconds,
        _extract_ad_duration_seconds(ad_text_hints),
    )
    # Values above ~10 minutes are always junk. A lower threshold is applied
    # separately when the timing came from the main content seekbar fallback.
    # Discard them so they don't inflate
    # watched_seconds or the remaining-time sleep.
    if (
        isinstance(ad_duration_seconds, float)
        and ad_duration_seconds > _MAX_RELIABLE_AD_DURATION_SECONDS
    ):
        ad_duration_seconds = None
    if (
        isinstance(ad_duration_seconds, float)
        and ad_duration_seconds > _MAX_FALLBACK_SEEKBAR_AD_DURATION_SECONDS
        and (
            any(getattr(sample, "ad_timing_from_main_seekbar", False) for sample in ad_samples)
            or debug_metadata.get("ad_timing_from_fallback_seekbar")
        )
    ):
        ad_duration_seconds = None
    watched_seconds = _estimate_ad_watched_seconds(
        ad_samples=ad_samples,
        ad_duration_seconds=ad_duration_seconds,
        recorded_video_duration_seconds=recorded_video_duration_seconds,
        debug_progress_seconds=debug_metadata.get("ad_progress_seconds"),
    )
    progress_points = _collect_ad_progress_points(
        ad_samples=ad_samples,
        debug_progress_seconds=debug_metadata.get("ad_progress_seconds"),
    )
    first_ad_progress_seconds = progress_points[0] if progress_points else None
    last_ad_progress_seconds = progress_points[-1] if progress_points else None
    landing_surface_title = None
    landing_surface_developer = None
    if ad_cta_result and ad_cta_result.destination_package == "com.android.vending":
        landing_surface_title = landing_metadata.get("title")
        landing_surface_developer = landing_metadata.get("developer")

    sponsor_label = sponsor_label or landing_surface_developer
    if _looks_like_youtube_noise(headline_text):
        headline_text = None
    headline_text = headline_text or landing_surface_title
    if not headline_text:
        _heuristic_candidates = [
            *debug_visible_lines,
            *(line for sample in ad_samples for line in sample.ad_visible_lines),
        ]
        headline_text = _pick_heuristic_headline(
            _heuristic_candidates,
            sponsor=sponsor_label,
            display_url=display_url,
            cta=raw_cta_text,
        )
        # Heuristic must not return the user's search query as a "headline".
        if (
            search_topic
            and headline_text
            and headline_text.strip().casefold() == search_topic.strip().casefold()
        ):
            headline_text = None
    effective_display_url = (
        display_url
        or resolved_landing_url
        or landing_metadata.get("display_url")
        or _extract_display_url_candidate(ad_text_hints)
    )
    identity_notes: list[str] = []
    # If the display/landing URL is a Google click redirect, unwrap it to the
    # real advertiser destination so advertiser_domain reflects the brand,
    # not "www.google.com".
    _unwrapped_landing = (
        _unwrap_google_click_url(resolved_landing_url)
        or _unwrap_google_click_url(effective_display_url)
    )
    if _unwrapped_landing:
        effective_display_url = _unwrapped_landing
        resolved_landing_url = resolved_landing_url or _unwrapped_landing
    landing_domain = _extract_domain(resolved_landing_url)
    display_domain = _extract_domain(effective_display_url)
    if (
        landing_domain
        and display_domain
        and landing_domain not in _SUPPRESSED_ADVERTISER_HOSTS
        and display_domain not in _SUPPRESSED_ADVERTISER_HOSTS
        and landing_domain != display_domain
    ):
        identity_notes.append(
            "mixed_ad_identity_detected:landing_host_mismatch:"
            f"{display_domain}->{landing_domain}"
        )
        effective_display_url = resolved_landing_url
        # CTA landing is the only identity that is guaranteed to match the
        # clicked ad. If the visible overlay belongs to a neighbouring ad in
        # the pod, do not feed that stale headline into analysis.
        if not _pre_click_headline_raw:
            headline_text = landing_surface_title
        # Drop visible_lines from the previous (stale) ad so full_visible_text
        # / full_text don't mix copy from two different advertisers in one row.
        _stale_domain = display_domain
        _filtered_samples: list[AndroidWatchSample] = []
        for _s in ad_samples:
            _markers = " ".join(_sample_identity_markers(_s))
            if _stale_domain and _stale_domain.casefold() in _markers.casefold():
                continue
            _filtered_samples.append(_s)
        if _filtered_samples:
            ad_samples = _filtered_samples
    advertiser_domain = _extract_domain(effective_display_url)
    # Suppress generic Google-ad-click hosts — they are redirect middlemen,
    # not the real advertiser. When we can't unwrap them, leave advertiser blank.
    if advertiser_domain and advertiser_domain in _SUPPRESSED_ADVERTISER_HOSTS:
        advertiser_domain = None
        effective_display_url = None
        display_url = None
    raw_visible_lines = _dedupe_strings(
        [
            sponsor_label,
            headline_text,
            display_url,
            cta_text,
            *debug_visible_lines,
            *(line for sample in ad_samples for line in sample.ad_visible_lines),
            *(label for sample in ad_samples for label in sample.ad_signal_labels),
        ]
    )
    visible_lines = _sanitize_visible_lines(raw_visible_lines)
    if not visible_lines:
        visible_lines = raw_visible_lines
    capture_id = f"android-{int(time.time() * 1000)}"
    screenshot_paths: list[tuple[int, str]] = []
    if ad_cta_result and ad_cta_result.debug_screen_path is not None:
        screenshot_paths.append((0, str(ad_cta_result.debug_screen_path)))
    if watch_debug_screen_path is not None:
        watch_debug_screen = str(watch_debug_screen_path)
        if all(existing_path != watch_debug_screen for _, existing_path in screenshot_paths):
            screenshot_paths.append((len(screenshot_paths), watch_debug_screen))

    # Drop records that have no advertiser identity at all — junk captures
    # (e.g. YouTube Live Chat pinned comment mistaken for a midroll ad,
    # or a stray UI/timecode line scraped as an "ad").
    # `sponsor_label` is just the "Sponsored" badge and is too generic to count
    # on its own — require at least a real domain or a real headline.
    _meaningful_sponsor = bool(
        sponsor_label
        and sponsor_label.strip().casefold() not in {"sponsored", "ad", "реклама"}
    )
    if not advertiser_domain and not headline_text and not _meaningful_sponsor:
        logger.info("build_ad_record: returning None — no advertiser identity (domain/headline/sponsor all empty or generic)")
        return None

    landing_status = LandingStatus.SKIPPED
    landing_dir = None
    landing_url = resolved_landing_url or landing_metadata.get("display_url") or effective_display_url
    capture_notes: list[str] = [*selection_notes, *identity_notes]
    if ad_cta_result and ad_cta_result.clicked:
        if (
            _is_first_run_chrome(ad_cta_result.destination_package, ad_cta_result.destination_activity)
            and not ad_cta_result.chrome_ready
        ):
            landing_status = LandingStatus.FAILED
        elif resolved_landing_url or landing_metadata.get("display_url"):
            landing_status = LandingStatus.PENDING
        else:
            landing_status = LandingStatus.FAILED

    _video_status: VideoStatus
    if recorded_video_path:
        _dur = recorded_video_duration_seconds or 0.0
        _watched = watched_seconds or 0.0
        if _dur <= 0:
            _video_status = VideoStatus.FAILED
        # Partial recording: screenrecord truncated far short of observed ad time.
        elif _dur < 3.0 or (_watched > 0 and _watched > 3.0 * _dur):
            _video_status = VideoStatus.PARTIAL
        else:
            _video_status = VideoStatus.COMPLETED
    elif screenshot_paths:
        _video_status = VideoStatus.FALLBACK_SCREENSHOTS
    else:
        _video_status = VideoStatus.NO_SRC

    capture_payload = {
        "video_src_url": None,
        "video_status": _video_status,
        "video_file": recorded_video_path,
        "landing_url": landing_url,
        "landing_status": landing_status,
        "landing_dir": landing_dir,
        "screenshot_paths": screenshot_paths,
        "cta_href": landing_url,
        "recorded_video_duration_seconds": recorded_video_duration_seconds,
        "first_ad_offset_seconds": first_ad_offset_seconds,
        "last_ad_offset_seconds": last_ad_offset_seconds,
        "analysis_status": AnalysisStatus.PENDING,
        "analysis_summary": None,
        "capture_notes": capture_notes,
        "pre_click_display_url": pre_click_display_url,
        "pre_click_headline_text": pre_click_headline,
    }
    skip_clicked = any(sample.skip_clicked for sample in ad_samples)
    skip_visible = any(sample.skip_available for sample in ad_samples)
    ad_completion_reason = _determine_ad_completion_reason(
        skip_clicked=skip_clicked,
        skip_visible=skip_visible,
        watched_seconds=watched_seconds,
        ad_duration_seconds=ad_duration_seconds,
        last_ad_progress_seconds=last_ad_progress_seconds,
    )

    _now = time.time()
    _result = {
        "position": 0,
        "started_at": _now - float(watched_seconds or 0.0),
        "ended_at": _now,
        "watched_seconds": watched_seconds,
        "completed": True,
        "skip_clicked": skip_clicked,
        "skip_visible": skip_visible,
        "skip_text": None,
        "cta_text": cta_text,
        "cta_candidates": cta_candidates,
        "cta_href": landing_url,
        "sponsor_label": sponsor_label,
        "advertiser_domain": advertiser_domain,
        "display_url": effective_display_url,
        "display_url_decoded": effective_display_url,
        "landing_urls": [landing_url] if landing_url else [],
        "headline_text": headline_text,
        "description_text": None,
        "description_lines": [],
        "ad_pod_position": None,
        "ad_pod_total": None,
        "ad_duration_seconds": ad_duration_seconds,
        "ad_first_progress_seconds": first_ad_progress_seconds,
        "ad_last_progress_seconds": last_ad_progress_seconds,
        "ad_completion_reason": ad_completion_reason,
        "first_ad_offset_seconds": first_ad_offset_seconds,
        "last_ad_offset_seconds": last_ad_offset_seconds,
        "recorded_video_duration_seconds": recorded_video_duration_seconds,
        "my_ad_center_visible": False,
        "full_text": "\n".join(visible_lines),
        "full_text_source": "native_app_overlay",
        "full_visible_text": "\n".join(visible_lines),
        "full_caption_text": "",
        "visible_lines": visible_lines,
        "caption_lines": [],
        "text_samples": [],
        "end_reason": "completed",
        "capture_id": capture_id,
        "capture": capture_payload,
        "recorded_at": _now,
        "ad_type": (
            "app_install"
            if (cta_text or "").strip().lower() == "install"
            else "video_ad"
        ),
    }
    logger.info(
        "build_ad_record: BUILT ad display_url=%s headline=%s "
        "ad_dur=%s last_prog=%s rec_dur=%s video_file=%s skip_visible=%s skip_clicked=%s",
        effective_display_url,
        (headline_text or "")[:50],
        ad_duration_seconds,
        last_ad_progress_seconds,
        recorded_video_duration_seconds,
        recorded_video_path,
        skip_visible,
        skip_clicked,
    )
    return _result


def _parse_debug_watch_metadata(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    sponsor_label = _read_first_text_by_resource_ids(root, selectors.AD_SPONSOR_TEXT_IDS)
    headline_text = _read_first_text_by_resource_ids(root, selectors.AD_HEADLINE_IDS)
    display_url = _read_first_text_by_resource_ids(root, selectors.AD_DISPLAY_URL_IDS)
    cta_text = _read_first_text_by_resource_ids(root, selectors.AD_CTA_TEXT_IDS)
    ad_seekbar_description = _read_seekbar_description_by_resource_ids(root, selectors.AD_TIME_BAR_IDS)
    ad_timing_from_fallback_seekbar = False
    if ad_seekbar_description is None:
        ad_timing_from_fallback_seekbar = True
        ad_seekbar_description = _find_ad_seekbar_description(root)
    ad_progress_seconds, ad_duration_seconds = _parse_seekbar_progress(ad_seekbar_description)
    if (
        isinstance(ad_duration_seconds, float)
        and ad_duration_seconds > _MAX_FALLBACK_SEEKBAR_AD_DURATION_SECONDS
        and ad_timing_from_fallback_seekbar
    ):
        ad_progress_seconds = None
        ad_duration_seconds = None
    visible_lines = _collect_visible_lines(root)
    sponsor_label = sponsor_label or _extract_sponsor_label(visible_lines)
    cta_text = cta_text or _choose_primary_cta(_extract_cta_candidates(visible_lines))
    return {
        "sponsor_label": sponsor_label,
        "headline_text": headline_text,
        "display_url": display_url or _extract_display_url_candidate(visible_lines),
        "cta_text": cta_text,
        "visible_lines": visible_lines,
        "ad_progress_seconds": ad_progress_seconds,
        "ad_duration_seconds": ad_duration_seconds or _extract_ad_duration_seconds(
            [sponsor_label, *(visible_lines[:12])]
        ),
        "ad_timing_from_fallback_seekbar": ad_timing_from_fallback_seekbar,
    }


def _parse_landing_metadata(path: Path | None, destination_package: str | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    visible_lines = _collect_visible_lines(root)

    if destination_package == "com.android.vending":
        title = None
        developer = None
        install_cta = None
        for value in visible_lines:
            lowered = value.casefold()
            if install_cta is None and lowered in {"install", "установить"}:
                install_cta = value
                continue
            if title is None and len(value) >= 6 and lowered not in {"install", "open", "cancel"}:
                title = value
                continue
            if developer is None and title is not None and value != title:
                developer = value
                break
        return {
            "title": title,
            "developer": developer,
            "install_cta": install_cta,
            "visible_lines": visible_lines[:12],
            "display_url": _extract_display_url_candidate(visible_lines),
        }

    return {
        "visible_lines": visible_lines[:12],
        "display_url": _extract_display_url_candidate(visible_lines),
    }


def _pick_last_str(values: Any) -> str | None:
    last_value = None
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                last_value = stripped
    return last_value


def _dedupe_strings(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped and stripped not in result:
            result.append(stripped)
    return result


def _sanitize_visible_lines(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        if lowered in seen:
            continue
        if (
            lowered in _GENERIC_VISIBLE_LINES
            and lowered not in _PRESERVED_GENERIC_VISIBLE_LINES
        ):
            continue
        if any(fragment in lowered for fragment in _GENERIC_VISIBLE_SUBSTRINGS):
            continue
        seen.add(lowered)
        result.append(stripped)
    return result


def _collect_visible_lines(root: ET.Element) -> list[str]:
    visible_lines: list[str] = []
    for node in root.iter():
        for attr_name in ("text", "content-desc"):
            text = (node.attrib.get(attr_name) or "").strip()
            if not text:
                continue
            if text not in visible_lines:
                visible_lines.append(text)
    return visible_lines


def _coalesce_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _pick_heuristic_headline(
    lines: list[str],
    *,
    sponsor: str | None,
    display_url: str | None,
    cta: str | None,
) -> str | None:
    skip = {
        (sponsor or "").casefold().strip(),
        (display_url or "").casefold().strip(),
        (cta or "").casefold().strip(),
    }
    skip.update(s.casefold() for s in _GENERIC_CTA_TEXTS)
    skip.update(_GENERIC_VISIBLE_LINES)
    skip.discard("")
    seen: set[str] = set()
    best: str | None = None
    for raw in lines:
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s or len(s) < 6 or len(s) > 120:
            continue
        lowered = s.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        if lowered in skip:
            continue
        if any(frag in lowered for frag in _GENERIC_VISIBLE_SUBSTRINGS):
            continue
        if any(frag in lowered for frag in _YOUTUBE_SEARCH_NOISE_SUBSTRINGS):
            continue
        if lowered.startswith(("sponsored", "реклам", "ad ", "visit ")):
            continue
        # Reject UI/navigation labels and YouTube live-chat messages.
        if lowered.startswith((
            "subscribe to ", "go to channel", "playlist",
            "video player", "navigate up", "action menu",
        )):
            continue
        if re.match(r"^@[a-z0-9_\-]+", s, re.IGNORECASE):
            continue
        if _TIMECODE_PATTERN.fullmatch(s):
            continue
        if _DISPLAY_URL_PATTERN.fullmatch(s):
            continue
        # Pure timecode/duration phrases like
        # "0 minutes 4 seconds elapsed of 25 minutes 12 seconds".
        if "elapsed of" in lowered:
            continue
        if re.match(r"^\d+\s+(minutes?|seconds?|hours?)\b", lowered):
            continue
        # YouTube UI labels that surface in page_source noise.
        if lowered in {
            "shorts remix",
            "new content available",
            "new content is available",
            "close sheet",
            "close ad panel",
            "close mini player",
            "expand mini player",
            "tap to refresh",
            "my ad center",
            "in this video",
        }:
            continue
        if best is None or len(s) > len(best):
            best = s
    return best


def _extract_display_url_candidate(values: list[str | None]) -> str | None:
    for value in values:
        if not isinstance(value, str):
            continue
        match = _DISPLAY_URL_PATTERN.search(value)
        if match is None:
            continue
        candidate = match.group(1).strip().rstrip(").,;:!?]}>\"'")
        if candidate:
            return candidate
    return None


def _extract_cta_candidates(values: list[str | None]) -> list[str]:
    candidates: list[str] = []
    known_ctas = {value.casefold().strip(): value for value in selectors.AD_CTA_DESCRIPTIONS}
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        direct_match = known_ctas.get(lowered)
        if direct_match and direct_match not in candidates:
            candidates.append(direct_match)
            continue
        for canonical_lowered, canonical in known_ctas.items():
            if canonical_lowered in lowered and canonical not in candidates:
                candidates.append(canonical)
                break
    return candidates


def _choose_primary_cta(values: list[str | None]) -> str | None:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(stripped)
    preferred = [
        value for value in normalized if value.casefold() not in _GENERIC_CTA_TEXTS
    ]
    if preferred:
        return preferred[-1]
    if normalized:
        return normalized[-1]
    return None


def _extract_sponsor_label(values: list[str | None]) -> str | None:
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        if "sponsored" in lowered or "реклам" in lowered or "спонс" in lowered:
            return stripped
    return None


def _extract_ad_duration_seconds(values: list[str | None]) -> float | None:
    for value in values:
        if not isinstance(value, str):
            continue
        lowered = value.casefold()
        if not any(token in lowered for token in _AD_DURATION_HINT_TOKENS):
            continue
        duration = _parse_duration_from_text(value)
        if duration is not None:
            return duration
    return None


def _parse_duration_from_text(value: str | None) -> float | None:
    if not isinstance(value, str):
        return None
    match = _TIMECODE_PATTERN.search(value)
    if match is None:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)


def _estimate_ad_watched_seconds(
    *,
    ad_samples: list[AndroidWatchSample],
    ad_duration_seconds: float | None,
    recorded_video_duration_seconds: float | None,
    debug_progress_seconds: float | None = None,
) -> float | None:
    if not ad_samples:
        return None

    sample_offsets = [
        float(sample.offset_seconds)
        for sample in ad_samples
        if isinstance(sample.offset_seconds, (int, float))
    ]
    first_sample_offset = min(sample_offsets) if sample_offsets else None

    observed_window: float | None = None
    if sample_offsets:
        if len(sample_offsets) >= 2:
            observed_window = max(1.0, sample_offsets[-1] - sample_offsets[0])
        else:
            observed_window = 1.0

    if (
        observed_window is not None
        and recorded_video_duration_seconds is not None
        and abs(recorded_video_duration_seconds - observed_window) <= 3.0
    ):
        observed_window = max(observed_window, recorded_video_duration_seconds)
    elif observed_window is None and recorded_video_duration_seconds is not None:
        observed_window = recorded_video_duration_seconds

    progress_points = _collect_ad_progress_points(
        ad_samples=ad_samples,
        debug_progress_seconds=debug_progress_seconds,
    )
    progress_window: float | None = None
    if len(progress_points) >= 2:
        progress_window = max(0.0, progress_points[-1] - progress_points[0])
        if progress_window <= 0:
            progress_window = None
    elif (
        len(progress_points) == 1
        and recorded_video_duration_seconds is None
        and len(sample_offsets) <= 1
    ):
        progress_window = progress_points[0]

    if observed_window is not None and progress_window is not None:
        watched_seconds = max(observed_window, progress_window)
    elif observed_window is not None:
        watched_seconds = observed_window
    elif progress_window is not None:
        watched_seconds = progress_window
    elif any(sample.skip_available for sample in ad_samples):
        watched_seconds = 5.0
    else:
        watched_seconds = 1.0

    if any(sample.skip_available for sample in ad_samples):
        watched_seconds = max(watched_seconds, 5.0)

    # The screenrecord runs for the full ad window (pre-roll → CTA → sleep → stop).
    # When the recorder was running and captured a long clip, that duration is a strong
    # lower-bound for how long the ad actually played — even when the sample-based
    # observed_window only covers the initial watch chunk (e.g. 8s of a 60s ad).
    # Accept rec_dur as a floor only when it doesn't exceed the known ad duration.
    if (
        recorded_video_duration_seconds is not None
        and recorded_video_duration_seconds > watched_seconds
        and (
            first_sample_offset is None
            or first_sample_offset <= _MAX_SCREENRECORD_FLOOR_FIRST_AD_OFFSET_SECONDS
        )
        and (
            ad_duration_seconds is None
            or recorded_video_duration_seconds <= ad_duration_seconds + 5.0
        )
    ):
        watched_seconds = recorded_video_duration_seconds

    if (
        recorded_video_duration_seconds is not None
        and watched_seconds < 5.0
        and ad_duration_seconds is not None
        and recorded_video_duration_seconds > watched_seconds
        # Raw screenrecord duration can include the whole watch window.
        # Only trust it as a fallback when we do not already have a multi-sample
        # observed ad window or a usable progress delta.
        and len(sample_offsets) <= 1
        and progress_window is None
    ):
        watched_seconds = min(recorded_video_duration_seconds, ad_duration_seconds)
    if ad_duration_seconds is not None:
        return min(watched_seconds, ad_duration_seconds)
    return watched_seconds


def _collect_ad_progress_points(
    *,
    ad_samples: list[AndroidWatchSample],
    debug_progress_seconds: float | None = None,
) -> list[float]:
    progress_points = [
        float(sample.ad_progress_seconds)
        for sample in ad_samples
        if isinstance(sample.ad_progress_seconds, (int, float))
    ]
    if isinstance(debug_progress_seconds, (int, float)):
        progress_points.append(float(debug_progress_seconds))
    return sorted(set(progress_points))


def _determine_ad_completion_reason(
    *,
    skip_clicked: bool,
    skip_visible: bool,
    watched_seconds: float | None,
    ad_duration_seconds: float | None,
    last_ad_progress_seconds: float | None,
) -> str:
    if skip_clicked:
        return "skip_clicked"

    if (
        ad_duration_seconds is not None
        and last_ad_progress_seconds is not None
        and last_ad_progress_seconds >= max(1.0, ad_duration_seconds - 1.0)
    ):
        return "duration_completed"

    if (
        ad_duration_seconds is not None
        and watched_seconds is not None
        and watched_seconds >= max(1.0, ad_duration_seconds - 1.0)
    ):
        return "duration_completed"

    if skip_visible:
        return "skip_available_not_clicked"

    return "observed_window"


def _extract_domain(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    parsed = urlsplit(stripped if "://" in stripped else f"https://{stripped}")
    host = (parsed.netloc or parsed.path or "").strip().lower()
    return host or None


def _is_first_run_chrome(package: str | None, activity: str | None) -> bool:
    if package != "com.android.chrome":
        return False
    normalized = (activity or "").lower()
    return "firstrun" in normalized


def _read_first_text_by_resource_ids(root: ET.Element, resource_ids: tuple[str, ...]) -> str | None:
    for node in root.iter():
        resource_id = node.attrib.get("resource-id") or ""
        if resource_id not in resource_ids:
            continue
        value = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if value:
            return value
    return None


def _read_seekbar_description_by_resource_ids(
    root: ET.Element,
    resource_ids: tuple[str, ...],
) -> str | None:
    for node in root.iter():
        if node.attrib.get("class") != "android.widget.SeekBar":
            continue
        resource_id = node.attrib.get("resource-id") or ""
        if resource_id not in resource_ids:
            continue
        value = (node.attrib.get("content-desc") or "").strip()
        if value:
            return value
    return None


def _find_ad_seekbar_description(root: ET.Element) -> str | None:
    for node in root.iter():
        node_class = (node.attrib.get("class") or "").strip()
        if node_class != "android.widget.SeekBar" and node.tag != "android.widget.SeekBar":
            continue
        value = (node.attrib.get("content-desc") or "").strip()
        if not value:
            continue
        progress_seconds, duration_seconds = _parse_seekbar_progress(value)
        if progress_seconds is None and duration_seconds is None:
            continue
        lowered = value.casefold()
        if "seconds of" in lowered or "minute" in lowered or ":" in value:
            return value
    return None


def _parse_seekbar_progress(description: str | None) -> tuple[int | None, int | None]:
    if not description:
        return None, None
    import re

    numbers = [int(value) for value in re.findall(r"\d+", description)]
    if len(numbers) >= 4:
        current = numbers[0] * 60 + numbers[1]
        total = numbers[2] * 60 + numbers[3]
        return current, total
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    return None, None
