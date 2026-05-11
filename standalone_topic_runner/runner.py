"""Standalone topic runner.

Boots an Android emulator, opens YouTube search results for each topic,
slowly scrolls collecting sponsored banners, then watches organic videos.
Records each in-video ad for a fixed window, clicks the CTA, captures the
landing screenshot + URL, and continues until the session budget is reached.
Results saved to JSON.

Run from the project root:

    python -m standalone_topic_runner.runner \
        --avd yt_android_playstore_api35_clean \
        --topic "best forex profit" \
        --topic "quantum ai trading bot"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

# Make the project's backend modules importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from app.settings import AndroidAppConfig
from app.services.mobile_app.android.avd_manager import (
    AndroidAvdManager,
    AndroidEmulatorLaunchOptions,
)
from app.services.mobile_app.android.appium_provider import AppiumSessionProvider
from app.services.mobile_app.android.proxy_bridge import (
    AndroidHttpProxyBridge,
    AndroidHttpProxyBridgeHandle,
)
from app.services.mobile_app.android.screenrecord import AndroidScreenRecorder
from app.services.mobile_app.android.tooling import (
    build_android_runtime_env,
    require_tool_path,
)


CHROME_PACKAGE = "com.android.chrome"
PLAY_STORE_PACKAGE = "com.android.vending"

_CLOSE_EXTERNAL_DEBUG_DIR: Path | None = None

AD_SKIP_BUTTON_IDS = (
    "com.google.android.youtube:id/modern_skip_ad_button",
    "com.google.android.youtube:id/skip_ad_button",
    "com.google.android.youtube:id/skip_ad_button_container",
)
AD_CTA_BUTTON_IDS = (
    "com.google.android.youtube:id/modern_action_button",
    "com.google.android.youtube:id/player_learn_more_button",
)
AD_CTA_TEXT_IDS = (
    "com.google.android.youtube:id/modern_mono_action_button_text",
    "com.google.android.youtube:id/modern_action_button_text",
)
AD_SPONSOR_TEXT_IDS = (
    "com.google.android.youtube:id/ad_text",
    "com.google.android.youtube:id/ad_progress_text",
    "com.google.android.youtube:id/ad_badge_text",
)
AD_HEADLINE_IDS = (
    "com.google.android.youtube:id/ad_headline",
    "com.google.android.youtube:id/ad_info_text",
)
AD_DISPLAY_URL_IDS = (
    "com.google.android.youtube:id/action_description_text",
    "com.google.android.youtube:id/ad_url_text",
    "com.google.android.youtube:id/ad_display_url",
)
AD_CTA_WEB_LABEL_TOKENS = (
    "visit advertiser",
    "visit site",
    "learn more",
    "shop now",
    "sign up",
    "subscribe",
    "book now",
    "contact us",
    "get offer",
    "apply now",
    "buy now",
    "see more",
)
WATCH_PANEL_WEB_CTA_LABEL_TOKENS = AD_CTA_WEB_LABEL_TOKENS + (
    "get quote",
    "докладніше",
    "подробнее",
    "відвідайте сайт",
    "відвідати сайт",
    "перейти на сайт",
)
AD_CTA_PLAY_STORE_LABEL_TOKENS = (
    "install",
    "get the app",
    "open",
    "update",
)
PLAY_STORE_BANNER_HINT_TOKENS = (
    "play.google.com",
    "google play",
    "in-app",
    "in app",
)

def _env_probability(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, min(1.0, value))


VIDEO_LIKE_PROBABILITY = _env_probability(
    "STANDALONE_VIDEO_LIKE_PROBABILITY", 0.70
)
VIDEO_SUBSCRIBE_PROBABILITY = _env_probability(
    "STANDALONE_VIDEO_SUBSCRIBE_PROBABILITY", 0.30
)
VIDEO_SOCIAL_MAX_ATTEMPTS = 6
VIDEO_SOCIAL_RETRY_SECONDS = 3.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


SHORTS_PHASE_ENABLED = _env_bool("STANDALONE_SHORTS_PHASE_ENABLED", True)
SHORTS_PHASE_MAX_SECONDS = _env_int("STANDALONE_SHORTS_PHASE_MAX_SECONDS", 120, minimum=15)
SHORTS_PHASE_GROUPS = _env_int("STANDALONE_SHORTS_PHASE_GROUPS", 2, minimum=1)
SHORTS_SWIPES_PER_OPEN = _env_int("STANDALONE_SHORTS_SWIPES_PER_OPEN", 3, minimum=0)
SHORTS_WATCH_MIN_SECONDS = _env_int("STANDALONE_SHORTS_WATCH_MIN_SECONDS", 6, minimum=1)
SHORTS_WATCH_MAX_SECONDS = _env_int("STANDALONE_SHORTS_WATCH_MAX_SECONDS", 10, minimum=1)
SHORTS_AD_RECORD_SECONDS = _env_int("STANDALONE_SHORTS_AD_RECORD_SECONDS", 10, minimum=3)

URL_DAT_RE = re.compile(r"\bdat=(https?://[^\s,}\]]+)", re.IGNORECASE)


# ---------- result records ----------


@dataclass
class BannerRecord:
    scroll_round: int
    position: int
    title: str
    screenshot: str
    bounds: tuple[int, int, int, int]
    landing_url: str | None = None
    landing_screenshot: str | None = None
    captured_at: float = field(default_factory=time.time)


@dataclass
class AdRecord:
    video: str | None
    recorded_seconds: float
    cta_label: str | None
    cta_kind: str  # "web", "play_store", "unknown", "none"
    landing_url: str | None
    landing_screenshot: str | None
    screenshot: str | None = None
    captured_at: float = field(default_factory=time.time)


@dataclass
class TopicRecord:
    topic: str
    started_at: str
    finished_at: str | None = None
    scroll_rounds: int = 0
    banners: list[BannerRecord] = field(default_factory=list)
    opened_video: dict | None = None
    opened_videos: list[dict] = field(default_factory=list)
    ads: list[AdRecord] = field(default_factory=list)
    watch_seconds: float = 0.0
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class StandaloneProgressEvent:
    event: str
    run_dir: Path
    topic: str | None = None
    topic_record: TopicRecord | None = None
    topics: list[TopicRecord] = field(default_factory=list)
    payload: dict[str, object] = field(default_factory=dict)


StandaloneProgressCallback = Callable[[StandaloneProgressEvent], Awaitable[None]]


@dataclass
class StandaloneRunOptions:
    topics: list[str]
    max_watch_seconds: float
    scroll_rounds: int = 30
    ad_record_seconds: float = 30.0
    avd_name: str | None = None
    manage_appium: bool = True
    headless: bool | None = None
    proxy_url: str | None = None
    run_dir: Path | None = None
    android_config: AndroidAppConfig | None = None
    stop_event: asyncio.Event | None = None
    on_progress: StandaloneProgressCallback | None = None


@dataclass
class StandaloneRunResult:
    started_at: str
    finished_at: str
    avd: str
    run_dir: Path
    topics: list[TopicRecord]


async def emit_progress(
    callback: StandaloneProgressCallback | None,
    *,
    event: str,
    run_dir: Path,
    topic: str | None = None,
    topic_record: TopicRecord | None = None,
    topics: list[TopicRecord] | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    if callback is None:
        return
    try:
        await callback(
            StandaloneProgressEvent(
                event=event,
                run_dir=run_dir,
                topic=topic,
                topic_record=topic_record,
                topics=list(topics or []),
                payload=dict(payload or {}),
            )
        )
    except Exception as exc:
        print(
            f"[topic-runner] progress callback failed event={event}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


# ---------- ADB helpers ----------


def adb(serial: str, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [require_tool_path("adb"), "-s", serial, *args],
        capture_output=True,
        check=False,
        env=build_android_runtime_env(),
        text=True,
        timeout=timeout,
    )


def adb_shell(serial: str, *args: str, timeout: float = 10.0) -> str:
    result = adb(serial, "shell", *args, timeout=timeout)
    return result.stdout or ""


def adb_screencap(serial: str, out_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [require_tool_path("adb"), "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False
    if result.returncode != 0 or not result.stdout:
        return False
    out_path.write_bytes(result.stdout)
    return out_path.stat().st_size > 0


def adb_tap(serial: str, x: int, y: int) -> bool:
    for attempt in range(1, 3):
        try:
            result = adb(
                serial,
                "shell",
                "input",
                "tap",
                str(x),
                str(y),
                timeout=12,
            )
        except subprocess.TimeoutExpired:
            print(
                f"[topic-runner] adb_tap timeout serial={serial} "
                f"x={x} y={y} attempt={attempt}",
                flush=True,
            )
            time.sleep(0.8)
            continue
        if result.returncode == 0:
            return True
        print(
            f"[topic-runner] adb_tap failed serial={serial} x={x} y={y} "
            f"attempt={attempt} rc={result.returncode}",
            flush=True,
        )
        time.sleep(0.4)
    return False


def adb_keyevent(serial: str, key_code: str) -> bool:
    try:
        result = adb(serial, "shell", "input", "keyevent", key_code, timeout=8)
    except subprocess.TimeoutExpired:
        print(
            f"[topic-runner] adb_keyevent timeout serial={serial} key={key_code}",
            flush=True,
        )
        return False
    return result.returncode == 0


def adb_force_stop(serial: str, package: str) -> None:
    adb(serial, "shell", "am", "force-stop", package, timeout=5)


def adb_screen_size(serial: str) -> tuple[int, int]:
    output = adb_shell(serial, "wm", "size", timeout=5)
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", output)
    if not match:
        return 1080, 2400
    return int(match.group(1)), int(match.group(2))


def adb_uiautomator_page_source(serial: str) -> str | None:
    dump_path = "/sdcard/standalone_topic_runner_dump.xml"
    try:
        dumped = adb(serial, "shell", "uiautomator", "dump", dump_path, timeout=10)
    except subprocess.TimeoutExpired:
        print(
            f"[topic-runner] adb_uiautomator dump timeout serial={serial}",
            flush=True,
        )
        return None
    if dumped.returncode != 0:
        return None
    try:
        source = adb_shell(serial, "cat", dump_path, timeout=10)
    except subprocess.TimeoutExpired:
        print(
            f"[topic-runner] adb_uiautomator cat timeout serial={serial}",
            flush=True,
        )
        return None
    return source or None


def open_results_deeplink(serial: str, query: str, youtube_pkg: str) -> bool:
    deep_link = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    result = adb(
        serial,
        "shell",
        "am",
        "start",
        "-S",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        deep_link,
        youtube_pkg,
        timeout=8,
    )
    return result.returncode == 0


def activate_youtube(serial: str, youtube_pkg: str, activity: str) -> None:
    adb(
        serial,
        "shell",
        "am",
        "start",
        "-n",
        f"{youtube_pkg}/{activity}",
        timeout=6,
    )


def android_global_proxy_value(emulator_proxy_url: str | None) -> str | None:
    if not emulator_proxy_url:
        return None
    parsed = urlparse(emulator_proxy_url)
    host = parsed.hostname
    port = parsed.port
    if not host or port is None:
        return None
    if host in {"127.0.0.1", "localhost", "0.0.0.0"}:
        host = "10.0.2.2"
    return f"{host}:{port}"


def set_android_global_http_proxy(serial: str, emulator_proxy_url: str | None) -> bool:
    proxy_value = android_global_proxy_value(emulator_proxy_url)
    if proxy_value is None:
        return False
    result = adb(
        serial,
        "shell",
        "settings",
        "put",
        "global",
        "http_proxy",
        proxy_value,
        timeout=8,
    )
    if result.returncode == 0:
        print(f"[topic-runner] android global http_proxy={proxy_value}", flush=True)
        return True
    print(
        f"[topic-runner] android global http_proxy:set_failed "
        f"rc={result.returncode} stderr={(result.stderr or '').strip()[:160]!r}",
        flush=True,
    )
    return False


def clear_android_global_http_proxy(serial: str) -> None:
    # Keep both forms: Android versions differ in whether `delete` alone
    # clears the active proxy cache immediately, while `:0` is the common
    # no-proxy sentinel used by adb settings examples.
    commands = (
        ("settings", "put", "global", "http_proxy", ":0"),
        ("settings", "delete", "global", "http_proxy"),
        ("settings", "delete", "global", "global_http_proxy_host"),
        ("settings", "delete", "global", "global_http_proxy_port"),
        ("settings", "delete", "global", "global_http_proxy_exclusion_list"),
    )
    for args in commands:
        try:
            adb(serial, "shell", *args, timeout=6)
        except Exception:
            continue


# ---------- XML parsing ----------


def safe_page_source(driver) -> str | None:
    try:
        return driver.page_source
    except Exception:
        return None


def dump_xml_snapshot(driver, snapshot_dir: Path, tag: str) -> None:
    """Save current page_source under snapshot_dir/<ts>_<tag>.xml.

    Best-effort — failures are swallowed so debug logging never breaks the
    main loop. Used to capture XML at decision points (record start, wait
    enter/exit) for offline review.
    """
    src = safe_page_source(driver)
    if not src:
        return
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S_%f")[:-3]
        path = snapshot_dir / f"{ts}_{tag}.xml"
        path.write_text(src, encoding="utf-8")
    except Exception:
        pass


def dump_debug_screenshot(serial: str, snapshot_dir: Path, tag: str) -> None:
    """Save a best-effort PNG next to debug XML for states where visual
    context matters more than the accessibility tree alone."""
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S_%f")[:-3]
        adb_screencap(serial, snapshot_dir / f"{ts}_{tag}.png")
    except Exception:
        pass


def dump_text_snapshot(snapshot_dir: Path, tag: str, text: str) -> None:
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S_%f")[:-3]
        path = snapshot_dir / f"{ts}_{tag}.txt"
        path.write_text(text, encoding="utf-8")
    except Exception:
        pass


def parse_xml(page_source: str | None) -> ET.Element | None:
    if not page_source:
        return None
    try:
        return ET.fromstring(page_source)
    except ET.ParseError:
        return None


def _source_top_package(root: ET.Element) -> str:
    for node in root.iter():
        package = (node.attrib.get("package") or "").strip()
        if package:
            return package
    return ""


def _is_external_package(package: str) -> bool:
    return package in (CHROME_PACKAGE, PLAY_STORE_PACKAGE)


def detect_surface_from_source(page_source: str | None) -> str:
    root = parse_xml(page_source)
    if root is None:
        return SURFACE_OTHER
    if _is_external_package(_source_top_package(root)):
        return SURFACE_OTHER
    if _root_has_resource_id(root, MINIPLAYER_RESOURCE_IDS):
        return SURFACE_WATCH_MINIMIZED
    if _root_has_resource_id(root, WATCH_FULL_RESOURCE_IDS):
        return SURFACE_WATCH_FULL
    if _root_has_resource_id(root, RESULTS_RESOURCE_IDS):
        return SURFACE_RESULTS
    return SURFACE_OTHER


RESULTS_RESOURCE_IDS = ("com.google.android.youtube:id/results",)
WATCH_FULL_RESOURCE_IDS = (
    "com.google.android.youtube:id/watch_player",
    "com.google.android.youtube:id/watch_panel",
    "com.google.android.youtube:id/watch_list",
    "com.google.android.youtube:id/video_metadata_layout",
)
# A minimized miniplayer always exposes its dedicated close button. We rely
# on that one id only — the "Expand Mini Player" content-desc lives on the
# swipe handle of the *full* player too, so it falsely fires there.
MINIPLAYER_RESOURCE_IDS = (
    "com.google.android.youtube:id/modern_miniplayer_close",
)

SURFACE_WATCH_FULL = "watch_full"
SURFACE_WATCH_MINIMIZED = "watch_minimized"
SURFACE_RESULTS = "results"
SURFACE_OTHER = "other"


def _root_has_resource_id(root: ET.Element, ids: tuple[str, ...]) -> bool:
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid in ids:
            return True
    return False


def detect_surface(driver) -> str:
    """Single source of truth for what's on screen.

    Order of checks matters: a minimized miniplayer overlays whatever is
    behind it (results / home), so we check it before watch_full.
    """
    return detect_surface_from_source(safe_page_source(driver))


def has_results_surface(driver) -> bool:
    return detect_surface(driver) == SURFACE_RESULTS


PLAY_PAUSE_BUTTON_RESOURCE_IDS = (
    "com.google.android.youtube:id/player_control_play_pause_replay_button",
)


def _paused_play_button_bounds(root: ET.Element) -> tuple[int, int, int, int] | None:
    player_bounds = _player_surface_bounds(root)
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in PLAY_PAUSE_BUTTON_RESOURCE_IDS:
            continue
        desc = (node.attrib.get("content-desc") or "").strip().casefold()
        if desc != "play video":
            return None
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is not None and _node_inside_player(bounds, player_bounds):
            return bounds

    # Fallback for watch-page resume: after returning from a banner landing,
    # YouTube can expose only a generic "Play video" node inside watch_player.
    # Keep it confined to the player so recommendation items below are ignored.
    for node in root.iter():
        desc = (node.attrib.get("content-desc") or "").strip().casefold()
        if desc != "play video":
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is not None and _node_inside_player(bounds, player_bounds):
            return bounds
    return None


def tap_play_if_paused(driver, serial: str) -> bool:
    """Tap the player play/pause button when its content-desc says
    "Play video", which on YouTube means the player is currently paused.
    No-op (returns False) when the player is already playing or the button
    is offscreen — caller doesn't need to special-case those.
    """
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return False
    bounds = _paused_play_button_bounds(root)
    if bounds is None:
        return False
    cx = (bounds[0] + bounds[2]) // 2
    cy = (bounds[1] + bounds[3]) // 2
    return adb_tap(serial, cx, cy)


async def ensure_watch_video_playing(driver, serial: str, *, attempts: int = 4) -> bool:
    """Best-effort resume for the main watch video after returning from a
    recommendation-banner landing. Stops immediately if an in-video ad appears.
    """
    for _ in range(attempts):
        if detect_surface(driver) != SURFACE_WATCH_FULL:
            return False
        if read_ad_playback_state(driver).is_ad:
            return False
        if not tap_play_if_paused(driver, serial):
            return True
        await asyncio.sleep(0.8)
    if detect_surface(driver) != SURFACE_WATCH_FULL:
        return False
    if read_ad_playback_state(driver).is_ad:
        return False
    root = parse_xml(safe_page_source(driver))
    return root is not None and _paused_play_button_bounds(root) is None


def tap_skip_ad_if_present(driver, serial: str) -> bool:
    """Tap the in-player Skip button if visible. Used to terminate a long
    ad after we've finished recording its 30-second window so the loop
    doesn't catch the same ad again on the next iteration."""
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return False
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in AD_SKIP_BUTTON_IDS:
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        cx = (bounds[0] + bounds[2]) // 2
        cy = (bounds[1] + bounds[3]) // 2
        return adb_tap(serial, cx, cy)
    return False


AD_GENERIC_TITLE_ID = "com.google.android.youtube:id/title"


def _read_first_text_by_ids(root: ET.Element, ids: tuple[str, ...]) -> str | None:
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in ids:
            continue
        text = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if text:
            return text
    return None


def _read_ad_text_node(root: ET.Element) -> tuple[str, tuple[int, int, int, int]] | None:
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in AD_SPONSOR_TEXT_IDS:
            continue
        text = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        bounds = parse_bounds(node.attrib.get("bounds"))
        if text and bounds is not None:
            return text, bounds
    return None


_POD_POSITION_RE = re.compile(r"\b(\d+)\s+of\s+(\d+)\b", re.IGNORECASE)
_DURATION_PAIR_RE = re.compile(
    r"(?:(\d+)\s+minutes?\s+)?(\d+)\s+seconds?\s+of\s+(?:(\d+)\s+minutes?\s+)?(\d+)\s+seconds?",
    re.IGNORECASE,
)


def parse_duration_pair(text: str) -> tuple[int, int] | None:
    """Parse strings like '0 minutes 4 seconds of 0 minutes 12 seconds' or
    '47 seconds of 47 seconds' into (elapsed_seconds, total_seconds)."""
    match = _DURATION_PAIR_RE.search(text)
    if match is None:
        return None
    em, es, tm, ts = match.groups()
    elapsed = (int(em) if em else 0) * 60 + int(es)
    total = (int(tm) if tm else 0) * 60 + int(ts)
    return elapsed, total


@dataclass(frozen=True)
class AdPlaybackState:
    """Snapshot of the in-player ad state at a point in time. All fields
    are independently optional — a real ad may expose any subset depending
    on whether YouTube's overlay is expanded.

    `same_ad(start, current)` only treats two states as different when at
    least one signal disagrees explicitly; missing signals are taken as
    "no opinion".
    """

    is_ad: bool
    pod_index: int | None
    pod_total: int | None
    elapsed_seconds: int | None
    total_seconds: int | None
    signature: str | None  # URL / headline (no pod-string fallback)


def _signature_from_root(root: ET.Element) -> str | None:
    display_url = _read_first_text_by_ids(root, AD_DISPLAY_URL_IDS)
    if display_url:
        return display_url.casefold()
    headline = _read_first_text_by_ids(root, AD_HEADLINE_IDS)
    if headline:
        return headline.casefold()
    if _read_ad_text_node(root) is not None:
        title = _read_first_text_by_ids(root, (AD_GENERIC_TITLE_ID,))
        if title:
            return title.casefold()
    return None


def read_ad_playback_state(driver) -> AdPlaybackState:
    """Single-pass XML read for everything we know about the ad currently
    in the player. Anchoring the entire decision on one snapshot avoids
    racing the overlay between separate page_source reads."""
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return AdPlaybackState(False, None, None, None, None, None)
    if _root_has_resource_id(root, MINIPLAYER_RESOURCE_IDS):
        return AdPlaybackState(False, None, None, None, None, None)
    if not _root_has_resource_id(root, WATCH_FULL_RESOURCE_IDS):
        return AdPlaybackState(False, None, None, None, None, None)
    is_ad = _root_has_resource_id(
        root, AD_SKIP_BUTTON_IDS + AD_SPONSOR_TEXT_IDS
    )
    if not is_ad:
        return AdPlaybackState(False, None, None, None, None, None)

    pod_index: int | None = None
    pod_total: int | None = None
    elapsed_seconds: int | None = None
    total_seconds: int | None = None

    ad_text_match = _read_ad_text_node(root)
    if ad_text_match is not None:
        ad_text_value, _ = ad_text_match
        pod_match = _POD_POSITION_RE.search(ad_text_value)
        if pod_match:
            pod_index = int(pod_match.group(1))
            pod_total = int(pod_match.group(2))

    # The seekbar exposes elapsed/total as content-desc on some node
    # inside the player overlay. The exact resource-id varies, so we scan
    # all content-descs for the duration-pair pattern.
    duration_pairs: list[tuple[int, int]] = []
    for node in root.iter():
        desc = (node.attrib.get("content-desc") or "").strip()
        if not desc:
            continue
        pair = parse_duration_pair(desc)
        if pair is not None:
            duration_pairs.append(pair)
    if duration_pairs:
        # Some final CTA/end-card overlays expose a tiny 5s `time_bar`
        # alongside the real ad seekbar. The real creative duration is the
        # larger total; using the first pair misclassifies tails as fresh ads.
        elapsed_seconds, total_seconds = max(duration_pairs, key=lambda p: p[1])

    return AdPlaybackState(
        is_ad=True,
        pod_index=pod_index,
        pod_total=pod_total,
        elapsed_seconds=elapsed_seconds,
        total_seconds=total_seconds,
        signature=_signature_from_root(root),
    )


def state_has_anchor(state: AdPlaybackState) -> bool:
    """An anchor is a stable signal we can compare across reads. Without
    any of these we have nothing to detect a transition with."""
    return (
        state.pod_index is not None
        or state.total_seconds is not None
        or state.signature is not None
    )


def state_anchor_score(state: AdPlaybackState) -> int:
    """Count of stable identity signals (pod position, total length,
    URL/headline). Used to pick whichever pre-CTA snapshot is richer."""
    if not state.is_ad:
        return 0
    score = 0
    if state.pod_index is not None and state.pod_total is not None:
        score += 1
    if state.total_seconds is not None:
        score += 1
    if state.signature is not None:
        score += 1
    return score


def enrich_ad_state(
    state: AdPlaybackState, supplemental: AdPlaybackState | None
) -> AdPlaybackState:
    """Fill missing stable identity fields from a nearby snapshot.

    YouTube often exposes pod position only for a brief expanded-overlay
    moment (for example right after returning from a CTA lander), then
    collapses it to plain "Sponsored" before recording starts. If the
    current snapshot has no conflicting identity signal, keep that earlier
    pod/total/signature anchor so skip/drain decisions still know which ad
    we started recording.
    """
    if supplemental is None or not state.is_ad or not supplemental.is_ad:
        return state
    if (
        state_has_anchor(state)
        and state_has_anchor(supplemental)
        and not same_ad_identity(supplemental, state)
    ):
        return state
    return AdPlaybackState(
        is_ad=state.is_ad,
        pod_index=state.pod_index
        if state.pod_index is not None
        else supplemental.pod_index,
        pod_total=state.pod_total
        if state.pod_total is not None
        else supplemental.pod_total,
        # Elapsed is intentionally not copied from the supplemental state:
        # it is a moving counter, not a stable identity anchor.
        elapsed_seconds=state.elapsed_seconds,
        total_seconds=state.total_seconds
        if state.total_seconds is not None
        else supplemental.total_seconds,
        signature=state.signature if state.signature is not None else supplemental.signature,
    )


def same_ad(start: AdPlaybackState, current: AdPlaybackState) -> bool:
    if not current.is_ad:
        return False
    # Pod position changing is an explicit transition between ads in the
    # same pod (1 of 2 → 2 of 2).
    if (
        start.pod_index is not None
        and current.pod_index is not None
        and start.pod_total is not None
        and current.pod_total is not None
    ):
        if (
            start.pod_index != current.pod_index
            or start.pod_total != current.pod_total
        ):
            return False
    # Total length is per-ad and stable; a different total means a
    # different creative (works even when pod_index isn't surfaced).
    if (
        start.total_seconds is not None
        and current.total_seconds is not None
        and start.total_seconds != current.total_seconds
    ):
        return False
    # URL / headline change.
    if (
        start.signature is not None
        and current.signature is not None
        and start.signature != current.signature
    ):
        return False
    # Elapsed counter rewinding by 2s or more means we crossed into a
    # new ad of the same length (the seekbar only ticks forward within a
    # single ad, so a backwards jump is the transition signal).
    if (
        start.elapsed_seconds is not None
        and current.elapsed_seconds is not None
        and start.elapsed_seconds - current.elapsed_seconds >= 2
    ):
        return False
    return True


def same_ad_identity(start: AdPlaybackState, current: AdPlaybackState) -> bool:
    """Compare stable ad identity only. Unlike `same_ad`, this deliberately
    ignores elapsed rewinds because YouTube can reset the elapsed counter to
    0 after a CTA round-trip while the same ad continues."""
    if not current.is_ad:
        return False
    if (
        start.pod_index is not None
        and current.pod_index is not None
        and start.pod_total is not None
        and current.pod_total is not None
    ):
        if (
            start.pod_index != current.pod_index
            or start.pod_total != current.pod_total
        ):
            return False
    if (
        start.total_seconds is not None
        and current.total_seconds is not None
        and start.total_seconds != current.total_seconds
    ):
        return False
    if (
        start.signature is not None
        and current.signature is not None
        and start.signature != current.signature
    ):
        return False
    return True


def ad_record_time_limit(state: AdPlaybackState, ad_record_seconds: float) -> float:
    if state.elapsed_seconds is None or state.total_seconds is None:
        return ad_record_seconds
    remaining = max(0.0, float(state.total_seconds - state.elapsed_seconds))
    # Keep only a small polling grace: larger tails can absorb the next ad
    # when recording starts near the end of a short creative.
    return max(1.0, min(ad_record_seconds, remaining + 0.5))


def is_terminal_ad_tail(state: AdPlaybackState) -> bool:
    """Final CTA/end-card YouTube shows after an ad reaches its full duration.
    It still exposes Sponsored/CTA nodes, but it is not a new ad creative."""
    return (
        state.is_ad
        and state.elapsed_seconds is not None
        and state.total_seconds is not None
        and state.total_seconds > 0
        and state.elapsed_seconds >= state.total_seconds
    )


async def wait_past_terminal_ad_tail(
    driver, *, timeout: float = 8.0
) -> AdPlaybackState:
    started = time.monotonic()
    last = read_ad_playback_state(driver)
    while is_terminal_ad_tail(last) and time.monotonic() - started < timeout:
        await asyncio.sleep(0.5)
        last = read_ad_playback_state(driver)
    return last


async def wait_for_ad_state(driver, timeout: float) -> AdPlaybackState:
    """Poll until the current ad state has at least one stable anchor
    (pod position, total length, or URL/headline). Returns the most
    recent read on timeout — anchorless but still usable for is_ad."""
    started = time.monotonic()
    last = read_ad_playback_state(driver)
    while True:
        if state_has_anchor(last):
            return last
        if time.monotonic() - started >= timeout:
            return last
        await asyncio.sleep(0.5)
        last = read_ad_playback_state(driver)


def parse_bounds(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))


def _label_contains_token(label: str, token: str) -> bool:
    """Word-boundary token match — keeps 'subscribe' from matching the
    'subscribers' channel-info text under the player."""
    return re.search(rf"\b{re.escape(token)}\b", label, flags=re.IGNORECASE) is not None


def _classify_cta_label(label: str) -> str:
    if any(_label_contains_token(label, t) for t in AD_CTA_PLAY_STORE_LABEL_TOKENS):
        return "play_store"
    if any(_label_contains_token(label, t) for t in AD_CTA_WEB_LABEL_TOKENS):
        return "web"
    return "unknown"


def _text_within_bounds(root: ET.Element, bounds: tuple[int, int, int, int]) -> str:
    """Concatenated lowercase text/desc of nodes whose bounds fall inside `bounds`."""
    left, top, right, bottom = bounds
    parts: list[str] = []
    for node in root.iter():
        node_bounds = parse_bounds(node.attrib.get("bounds"))
        if node_bounds is None:
            continue
        if node_bounds[1] < top or node_bounds[3] > bottom:
            continue
        if node_bounds[0] < left or node_bounds[2] > right:
            continue
        for attr in ("text", "content-desc"):
            value = (node.attrib.get(attr) or "").strip()
            if value:
                parts.append(value)
    return " | ".join(parts).casefold()


def _player_surface_bounds(root: ET.Element) -> tuple[int, int, int, int] | None:
    """Bounds of the watch_player container — used to confine every CTA
    candidate to nodes overlaid on the actual player, not the metadata
    panel below it (where channel/subscribers labels live and where the
    same modern_action_button id can also surface for sponsored cards)."""
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in WATCH_FULL_RESOURCE_IDS:
            continue
        if not rid.endswith(":id/watch_player"):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is not None:
            return bounds
    return None


def _node_inside_player(
    bounds: tuple[int, int, int, int] | None,
    player_bounds: tuple[int, int, int, int] | None,
) -> bool:
    """A node counts as player-overlay if it doesn't sit entirely below
    the player. When player_bounds is unknown we don't filter — better
    to risk a false positive than miss the CTA on a malformed XML."""
    if bounds is None:
        return False
    if player_bounds is None:
        return True
    return bounds[1] < player_bounds[3]


def _node_in_watch_action_row(
    bounds: tuple[int, int, int, int] | None,
    player_bounds: tuple[int, int, int, int] | None,
) -> bool:
    """Confine non-ad video actions to the row directly under watch_player.

    Recommendation cards and feed snippets also expose "Subscribe" nodes.
    Liking/subscribing is optional, so if the player bounds are unavailable
    we skip instead of risking a tap outside the current video metadata.
    """
    if bounds is None or player_bounds is None:
        return False
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return False
    player_bottom = player_bounds[3]
    if bounds[1] < player_bottom - 20:
        return False
    return bounds[1] <= player_bottom + 420


def _find_video_like_button_bounds(
    root: ET.Element,
) -> tuple[int, int, int, int] | None:
    player_bounds = _player_surface_bounds(root)
    for node in root.iter():
        if (node.attrib.get("clickable") or "").strip() != "true":
            continue
        desc = (node.attrib.get("content-desc") or "").strip()
        low = desc.casefold()
        if not low.startswith("like this video"):
            continue
        if "dislike" in low or low.startswith(("unlike", "liked")):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if _node_in_watch_action_row(bounds, player_bounds):
            return bounds
    return None


def _find_video_subscribe_button_bounds(
    root: ET.Element,
) -> tuple[int, int, int, int] | None:
    info = _find_video_subscribe_button_info(root)
    return info[0] if info is not None else None


def _find_video_subscribe_button_info(
    root: ET.Element,
) -> tuple[tuple[int, int, int, int], str | None] | None:
    player_bounds = _player_surface_bounds(root)
    for node in root.iter():
        if (node.attrib.get("clickable") or "").strip() != "true":
            continue
        desc = (node.attrib.get("content-desc") or "").strip()
        low = desc.casefold()
        if not (low == "subscribe" or low.startswith("subscribe to ")):
            continue
        if "subscribers" in low or low.startswith("subscribed"):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if _node_in_watch_action_row(bounds, player_bounds):
            return bounds, _channel_name_from_subscribe_desc(desc)
    return None


def _notification_prompt_no_thanks_bounds(
    root: ET.Element,
) -> tuple[int, int, int, int] | None:
    has_notification_prompt = False
    no_thanks_bounds: list[tuple[int, int, int, int]] = []
    for node in root.iter():
        text = (
            (node.attrib.get("text") or "")
            + " "
            + (node.attrib.get("content-desc") or "")
        ).strip()
        low = text.casefold()
        if "turn on notifications" in low:
            has_notification_prompt = True
        if low in {"no thanks", "not now"} or "no thanks" in low:
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is not None:
                no_thanks_bounds.append(bounds)
    if not has_notification_prompt or not no_thanks_bounds:
        return None
    return sorted(no_thanks_bounds, key=lambda b: (b[1], b[0]))[0]


async def dismiss_youtube_notification_prompt_if_present(
    driver,
    serial: str,
    debug_dir: Path,
) -> bool:
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return False
    bounds = _notification_prompt_no_thanks_bounds(root)
    if bounds is None:
        return False
    dump_xml_snapshot(driver, debug_dir, "notification_prompt_before_dismiss")
    dump_debug_screenshot(serial, debug_dir, "notification_prompt_before_dismiss")
    tapped = adb_tap(
        serial,
        (bounds[0] + bounds[2]) // 2,
        (bounds[1] + bounds[3]) // 2,
    )
    if tapped:
        await asyncio.sleep(0.8)
        dump_xml_snapshot(driver, debug_dir, "notification_prompt_after_dismiss")
        dump_debug_screenshot(serial, debug_dir, "notification_prompt_after_dismiss")
        print("[topic-runner] notification_prompt:dismissed", flush=True)
    return tapped


async def perform_video_social_action_attempt(
    *,
    driver,
    serial: str,
    debug_dir: Path,
    topic: str,
    opened_video: dict | None,
    like_pending: bool,
    subscribe_pending: bool,
) -> tuple[bool, bool]:
    if not like_pending and not subscribe_pending:
        return False, False
    if detect_surface(driver) != SURFACE_WATCH_FULL:
        return False, False
    if read_ad_playback_state(driver).is_ad:
        return False, False

    root = parse_xml(safe_page_source(driver))
    if root is None or not _root_has_resource_id(root, WATCH_FULL_RESOURCE_IDS):
        return False, False

    tapped_like = False
    tapped_subscribe = False
    dumped_before = False
    subscribed_channel_name: str | None = None

    if like_pending:
        bounds = _find_video_like_button_bounds(root)
        if bounds is not None:
            dump_xml_snapshot(driver, debug_dir, "social_before")
            dump_debug_screenshot(serial, debug_dir, "social_before")
            dumped_before = True
            tapped_like = adb_tap(
                serial,
                (bounds[0] + bounds[2]) // 2,
                (bounds[1] + bounds[3]) // 2,
            )
            if tapped_like:
                await asyncio.sleep(0.6)

    if subscribe_pending:
        if tapped_like:
            root = parse_xml(safe_page_source(driver))
        if root is not None:
            subscribe_info = _find_video_subscribe_button_info(root)
            if subscribe_info is not None:
                bounds, subscribed_channel_name = subscribe_info
                if not dumped_before:
                    dump_xml_snapshot(driver, debug_dir, "social_before")
                    dump_debug_screenshot(serial, debug_dir, "social_before")
                tapped_subscribe = adb_tap(
                    serial,
                    (bounds[0] + bounds[2]) // 2,
                    (bounds[1] + bounds[3]) // 2,
                )
                if tapped_subscribe:
                    await asyncio.sleep(0.8)
                    await dismiss_youtube_notification_prompt_if_present(
                        driver, serial, debug_dir
                    )

    actions: list[str] = []
    if tapped_like:
        actions.append("like")
    if tapped_subscribe:
        actions.append("subscribe")
    if actions:
        dump_xml_snapshot(driver, debug_dir, "social_after")
        dump_debug_screenshot(serial, debug_dir, "social_after")
        if opened_video is not None:
            display_title = (
                str(opened_video.get("video_title") or "").strip()
                or parse_video_tile_metadata(str(opened_video.get("title") or "")).get("video_title")
                or str(opened_video.get("title") or "").strip()
            )
            opened_video["liked"] = bool(opened_video.get("liked")) or tapped_like
            opened_video["subscribed"] = (
                bool(opened_video.get("subscribed")) or tapped_subscribe
            )
            opened_video.setdefault("social_actions", []).extend(actions)
            if tapped_like:
                opened_video["liked_video_title"] = display_title
                opened_video["liked_at"] = utc_now_iso()
            if tapped_subscribe:
                channel_name = (
                    subscribed_channel_name
                    or str(opened_video.get("channel_name") or "").strip()
                    or None
                )
                opened_video["subscribed_channel_name"] = channel_name
                opened_video["subscribed_at"] = utc_now_iso()
                if channel_name:
                    opened_video["channel_name"] = channel_name
        print(
            f"[topic-runner] video_social topic={topic!r} actions={','.join(actions)} "
            f"video={opened_video.get('liked_video_title') if opened_video else None!r} "
            f"channel={opened_video.get('subscribed_channel_name') if opened_video else None!r}",
            flush=True,
        )
    return tapped_like, tapped_subscribe


def find_cta_node(driver) -> tuple[str, tuple[int, int, int, int], str] | None:
    """Find the in-player ad CTA button.

    Returns (label, bounds, kind) where kind ∈ {"web", "play_store", "unknown"}.
    Classification uses only the button's own label (and adjacent ad CTA-text
    nodes resolved via resource-id), not page-wide text — the latter pulls in
    text from feed sponsored cards behind the player and misclassifies.
    """
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return None

    cta_button_ids = AD_CTA_BUTTON_IDS
    cta_text_ids = AD_CTA_TEXT_IDS
    player_bounds = _player_surface_bounds(root)

    # Prefer the explicit CTA button id. If it has no readable label, look up
    # the sibling text node by id and use that.
    cta_text_label = ""
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in cta_text_ids:
            continue
        node_bounds = parse_bounds(node.attrib.get("bounds"))
        if not _node_inside_player(node_bounds, player_bounds):
            continue
        text = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if text:
            cta_text_label = text
            break

    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if rid not in cta_button_ids:
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        if not _node_inside_player(bounds, player_bounds):
            continue
        own_label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        label = own_label or cta_text_label or "cta"
        return label, bounds, _classify_cta_label(label)

    # Fallback: a clickable element labelled with a known CTA token, but
    # only inside the player overlay. Channel/subscriber labels in the
    # metadata panel below the player must not be picked up — they
    # historically caused us to tap "187K subscribers".
    for node in root.iter():
        if (node.attrib.get("clickable") or "").strip() != "true":
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        if not _node_inside_player(bounds, player_bounds):
            continue
        for attr in ("text", "content-desc"):
            value = (node.attrib.get(attr) or "").strip()
            if not value:
                continue
            kind = _classify_cta_label(value)
            if kind == "unknown":
                continue
            return value, bounds, kind
    return None


async def wait_for_cta_node(
    driver, timeout: float
) -> tuple[str, tuple[int, int, int, int], str] | None:
    """Poll find_cta_node until a CTA button is on screen or `timeout`
    elapses. Google reveals the CTA 1-2s after the ad starts, so a
    one-shot read at ad-detect time is too early."""
    started = time.monotonic()
    while True:
        cta = find_cta_node(driver)
        if cta is not None:
            return cta
        if time.monotonic() - started >= timeout:
            return None
        await asyncio.sleep(0.5)



# ---------- landing URL ----------


_HOST_RE = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)
_HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+", re.IGNORECASE)
_BASE64URL_BLOB_RE = re.compile(r"aHR0c[A-Za-z0-9_-]{10,}")
_TRACKING_HOSTS = frozenset({
    "googleadservices.com",
    "www.googleadservices.com",
    "doubleclick.net",
    "googleads.g.doubleclick.net",
    "googlesyndication.com",
    "gstatic.com",
    "google.com",
    "www.google.com",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "googletagmanager.com",
})


def _landing_url_host(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    try:
        return (urlparse(raw_url).netloc or "").casefold()
    except Exception:
        return ""


def _is_tracking_landing_url(raw_url: str | None) -> bool:
    host = _landing_url_host(raw_url)
    return bool(
        host
        and (
            host in _TRACKING_HOSTS
            or any(host.endswith("." + h) for h in _TRACKING_HOSTS)
        )
    )


def _clean_landing_url(raw_url: str) -> str:
    cleaned = raw_url.strip()
    cleaned = re.split(r"[\x00-\x1f\x7f]", cleaned, maxsplit=1)[0]
    return cleaned.rstrip(".,;]})\"'")


def _extract_embedded_landing_urls(raw_url: str | None) -> list[str]:
    if not raw_url:
        return []
    import base64

    texts: list[str] = []
    decoded = raw_url
    texts.append(decoded)
    for _ in range(2):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
        texts.append(decoded)

    for blob in _BASE64URL_BLOB_RE.findall(raw_url):
        for pad in range(4):
            try:
                value = base64.urlsafe_b64decode(blob + ("=" * pad)).decode(
                    "utf-8", errors="ignore"
                )
                texts.append(value)
                break
            except Exception:
                continue

    # Some Google click wrappers contain a protobuf-ish base64 field where
    # the decoded string starts a few bytes before "https://", so the raw
    # base64 marker can be shifted (`odHRw...`, `dHRw...`, `Hmh0...`) rather
    # than plain `aHR0...`. Decode short prefixes around common shifted
    # "http(s)://" fragments to recover those destinations without pulling in
    # a full protobuf parser.
    for marker in (
        "aHR0c",
        "odHRw",
        "dHRw",
        "h0dHB",
        "dHBzOi8v",
        "dHA6Ly8",
        "czovL2",
    ):
        for match in re.finditer(marker, raw_url):
            for back in range(0, 13):
                start = max(0, match.start() - back)
                chunk_match = re.match(r"[A-Za-z0-9_-]{16,}", raw_url[start:])
                if chunk_match is None:
                    continue
                chunk = chunk_match.group(0)[:360]
                max_end = min(len(chunk), 300)
                for end in range(16, max_end + 1):
                    fragment = chunk[:end]
                    for pad in range(4):
                        try:
                            value = base64.urlsafe_b64decode(
                                fragment + ("=" * pad)
                            ).decode("utf-8", errors="ignore")
                            if "http" in value:
                                texts.append(value)
                        except Exception:
                            continue

    urls: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _HTTP_URL_RE.finditer(text):
            url = _clean_landing_url(match.group(0))
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def resolve_landing_url(raw_url: str | None) -> str | None:
    """Prefer the real advertiser URL over Google click wrappers.

    Chrome sometimes exposes a googleadservices URL before the redirect
    finishes. The wrapper usually contains the real destination either as a
    query param or as a base64 URL blob; returning that destination keeps
    result.json useful even when the foreground parser is unreliable.
    """
    if not raw_url:
        return None
    candidates = [_clean_landing_url(raw_url)]
    try:
        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query)
        for key in ("adurl", "url", "q"):
            for value in query.get(key) or []:
                decoded = unquote(value)
                if decoded.startswith(("http://", "https://")):
                    candidates.append(_clean_landing_url(decoded))
    except Exception:
        pass
    candidates.extend(_extract_embedded_landing_urls(raw_url))

    best_tracking: str | None = None
    best_non_tracking: str | None = None
    for candidate in candidates:
        if not candidate.startswith(("http://", "https://")):
            continue
        host = _landing_url_host(candidate)
        if not host or "." not in host:
            continue
        if _is_tracking_landing_url(candidate):
            if best_tracking is None:
                best_tracking = candidate
            continue
        if best_non_tracking is None or len(candidate) > len(best_non_tracking):
            best_non_tracking = candidate
    return best_non_tracking or best_tracking


async def wait_for_resolved_landing_url(
    serial: str,
    youtube_pkg: str,
    *,
    timeout: float = 12.0,
) -> str | None:
    started = time.monotonic()
    best: str | None = None
    while time.monotonic() - started < timeout:
        raw_url = read_landing_url(serial, youtube_pkg)
        resolved = resolve_landing_url(raw_url)
        if resolved:
            best = resolved
            if not _is_tracking_landing_url(resolved):
                return resolved
        await asyncio.sleep(1.0)
    return best


async def capture_settled_landing_screenshot(
    *,
    driver,
    serial: str,
    path: Path,
    timeout: float = 15.0,
    min_bytes: int = 120_000,
) -> bool:
    """Capture a landing screenshot only after the page has visibly painted.

    Several real XML/screenshot samples showed Chrome with a resolved URL but
    only a blank progress page after the old fixed 5s sleep. A byte-size gate is
    intentionally simple here: retry while the screenshot still looks like an
    empty Chrome shell, and don't attach a bad landing image if it never paints.
    """
    deadline = time.monotonic() + timeout
    while True:
        await dismiss_browser_permission_prompt_if_present(driver, serial)
        if adb_screencap(serial, path):
            try:
                if path.stat().st_size >= min_bytes:
                    return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(1.0)
    try:
        if path.exists() and path.stat().st_size < min_bytes:
            path.unlink()
    except OSError:
        pass
    return False


def extract_advertiser_hosts(landing_url: str | None) -> set[str]:
    """Pull real advertiser hostnames out of a googleadservices wrapper URL.

    The wrapper hides the destination both URL-encoded ('%3A%2F%2F') and as
    base64-encoded blobs starting with 'aHR0c' (the prefix bytes for
    'http' / 'https'). We unfold both forms and return all distinct
    hostnames excluding well-known tracking/wrapper hosts."""
    if not landing_url:
        return set()
    import base64
    from urllib.parse import unquote

    candidates: list[str] = [landing_url]
    decoded = landing_url
    for _ in range(2):
        decoded = unquote(decoded)
        if decoded != candidates[-1]:
            candidates.append(decoded)
        else:
            break

    for blob in _BASE64URL_BLOB_RE.findall(landing_url):
        for pad in range(4):
            try:
                value = base64.urlsafe_b64decode(blob + ("=" * pad)).decode(
                    "utf-8", errors="ignore"
                )
                candidates.append(value)
                break
            except Exception:
                continue
    resolved = resolve_landing_url(landing_url)
    if resolved and resolved != landing_url:
        candidates.append(resolved)

    hosts: set[str] = set()
    for candidate in candidates:
        for match in _HOST_RE.finditer(candidate):
            host = match.group(1).lower().rstrip(".")
            if host in _TRACKING_HOSTS:
                continue
            hosts.add(host)
    return hosts


def read_landing_url(serial: str, youtube_pkg: str) -> str | None:
    try:
        result = adb(serial, "shell", "dumpsys", "activity", "activities", timeout=10)
    except subprocess.TimeoutExpired:
        print("[topic-runner] read_landing_url:timeout", flush=True)
        return None
    if result.returncode != 0:
        return None
    output = result.stdout or ""
    relevant = (youtube_pkg, CHROME_PACKAGE)
    lines = output.splitlines()
    for i, line in enumerate(lines):
        match = URL_DAT_RE.search(line)
        if not match:
            continue
        candidate = match.group(1).rstrip("},;]\"'")
        context = "\n".join(lines[max(0, i - 15) : min(len(lines), i + 5)])
        if any(pkg in context for pkg in relevant):
            return candidate
    return None


def current_foreground_package(serial: str) -> str:
    try:
        output = adb_shell(serial, "dumpsys", "window", "windows", timeout=6)
    except subprocess.TimeoutExpired:
        print("[topic-runner] foreground_package:timeout", flush=True)
        return ""
    for pattern in (
        r"mCurrentFocus=.*?\s([\w.]+)/[\w.\$]+",
        r"mFocusedApp=.*?\s([\w.]+)/[\w.\$]+",
        r"topResumedActivity=.*?\s([\w.]+)/[\w.\$]+",
    ):
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    return ""


def dump_results_not_loaded_debug(driver, serial: str, nav_dir: Path) -> None:
    dump_xml_snapshot(driver, nav_dir, "results_not_loaded")
    adb_source = adb_uiautomator_page_source(serial)
    if adb_source:
        try:
            nav_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S_%f")[:-3]
            (nav_dir / f"{ts}_results_not_loaded_uiautomator.xml").write_text(
                adb_source, encoding="utf-8"
            )
        except Exception:
            pass
    adb_screencap(serial, nav_dir / "results_not_loaded.png")
    foreground = current_foreground_package(serial)
    connectivity = adb_shell(serial, "dumpsys", "connectivity", timeout=8)
    interesting_connectivity = []
    for line in connectivity.splitlines():
        low = line.casefold()
        if any(
            token in low
            for token in (
                "defaultnetwork",
                "internet",
                "validated",
                "wifi",
                "cellular",
                "networkagentinfo",
            )
        ):
            interesting_connectivity.append(line)
    dump_text_snapshot(
        nav_dir,
        "results_not_loaded_debug",
        "foreground_package="
        + foreground
        + "\n\nconnectivity_excerpt:\n"
        + "\n".join(interesting_connectivity[:160]),
    )


# ---------- main flow ----------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_topic_slug(topic: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", topic.casefold()).strip("_")
    return cleaned or "topic"


SYSTEM_ANR_TITLE_TOKENS = (
    "isn't responding",
    "is not responding",
    "isn’t responding",
    "не отвечает",
)
SYSTEM_ANR_WAIT_TOKENS = (
    "wait",
    "ожидать",
    "зачекати",
)
SYSTEM_ANR_CLOSE_TOKENS = (
    "close app",
    "закрыть приложение",
    "закрити додаток",
)
BROWSER_PERMISSION_TITLE_TOKENS = (
    "wants to use your device's location",
    "wants to use your location",
    "wants to know your location",
    "wants to show notifications",
)
BROWSER_PERMISSION_BLOCK_TOKENS = (
    "block",
    "deny",
    "don't allow",
)


def _system_anr_action(
    page_source: str | None,
) -> tuple[str, tuple[int, int, int, int], str] | None:
    root = parse_xml(page_source)
    if root is None:
        return None
    has_anr = False
    title_text = ""
    close_bounds: list[tuple[int, int, int, int]] = []
    wait_bounds: list[tuple[int, int, int, int]] = []
    for node in root.iter():
        text = (
            (node.attrib.get("text") or "")
            + " "
            + (node.attrib.get("content-desc") or "")
        ).strip()
        low = text.casefold()
        if any(token in low for token in SYSTEM_ANR_TITLE_TOKENS):
            has_anr = True
            title_text = text
        if any(token == low or token in low for token in SYSTEM_ANR_CLOSE_TOKENS):
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is not None:
                close_bounds.append(bounds)
        if any(token == low or token in low for token in SYSTEM_ANR_WAIT_TOKENS):
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is not None:
                wait_bounds.append(bounds)
    if not has_anr:
        return None
    title_low = title_text.casefold()
    if "chrome" in title_low and close_bounds:
        return "close_app", sorted(close_bounds, key=lambda b: (b[1], b[0]))[0], title_text
    if wait_bounds:
        return "wait", sorted(wait_bounds, key=lambda b: (b[1], b[0]))[0], title_text
    return None


def handle_system_anr_dialog_if_present(driver, serial: str) -> bool:
    action = _system_anr_action(safe_page_source(driver))
    if action is None:
        return False
    action_name, bounds, title = action
    left, top, right, bottom = bounds
    tapped = adb_tap(serial, (left + right) // 2, (top + bottom) // 2)
    if tapped:
        print(
            f"[topic-runner] system_dialog:{action_name} title={title!r}",
            flush=True,
        )
        if action_name == "close_app" and "chrome" in title.casefold():
            try:
                adb_force_stop(serial, CHROME_PACKAGE)
            except Exception as exc:
                print(
                    f"[topic-runner] system_dialog:chrome_force_stop_failed err={type(exc).__name__}",
                    flush=True,
                )
    return tapped


def _browser_permission_block_bounds(page_source: str | None) -> tuple[int, int, int, int] | None:
    root = parse_xml(page_source)
    if root is None:
        return None
    has_permission_dialog = False
    block_bounds: list[tuple[int, int, int, int]] = []
    for node in root.iter():
        text = (
            (node.attrib.get("text") or "")
            + " "
            + (node.attrib.get("content-desc") or "")
        ).strip()
        low = text.casefold()
        if any(token in low for token in BROWSER_PERMISSION_TITLE_TOKENS):
            has_permission_dialog = True
        if any(token == low for token in BROWSER_PERMISSION_BLOCK_TOKENS):
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is not None:
                block_bounds.append(bounds)
    if not has_permission_dialog or not block_bounds:
        return None
    return sorted(block_bounds, key=lambda b: (b[1], b[0]))[0]


async def dismiss_browser_permission_prompt_if_present(driver, serial: str) -> bool:
    bounds = _browser_permission_block_bounds(safe_page_source(driver))
    if bounds is None:
        bounds = _browser_permission_block_bounds(adb_uiautomator_page_source(serial))
    if bounds is None:
        return False
    left, top, right, bottom = bounds
    if not adb_tap(serial, (left + right) // 2, (top + bottom) // 2):
        return False
    print("[topic-runner] browser_permission:block", flush=True)
    await asyncio.sleep(0.8)
    return True


async def wait_for_results(driver, timeout: float, serial: str | None = None) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if has_results_surface(driver):
            return True
        if serial and handle_system_anr_dialog_if_present(driver, serial):
            await asyncio.sleep(1.0)
            continue
        if serial:
            adb_source = adb_uiautomator_page_source(serial)
            if detect_surface_from_source(adb_source) == SURFACE_RESULTS:
                return True
        await asyncio.sleep(0.7)
    if has_results_surface(driver):
        return True
    if serial:
        adb_source = adb_uiautomator_page_source(serial)
        if detect_surface_from_source(adb_source) == SURFACE_RESULTS:
            return True
    return False


async def wait_for_watch(driver, timeout: float, serial: str | None = None) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if detect_surface(driver) == SURFACE_WATCH_FULL:
            return True
        if serial and handle_system_anr_dialog_if_present(driver, serial):
            await asyncio.sleep(1.0)
            continue
        if serial:
            adb_source = adb_uiautomator_page_source(serial)
            if detect_surface_from_source(adb_source) == SURFACE_WATCH_FULL:
                return True
        await asyncio.sleep(0.7)
    if detect_surface(driver) == SURFACE_WATCH_FULL:
        return True
    if serial:
        adb_source = adb_uiautomator_page_source(serial)
        if detect_surface_from_source(adb_source) == SURFACE_WATCH_FULL:
            return True
    return False


def adb_swipe(
    serial: str,
    *,
    x: int,
    start_y: int,
    end_y: int,
    duration_ms: int = 600,
) -> bool:
    for attempt in range(2):
        try:
            result = adb(
                serial,
                "shell",
                "input",
                "swipe",
                str(x),
                str(start_y),
                str(x),
                str(end_y),
                str(duration_ms),
                timeout=8,
            )
            if result.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            print(
                f"[topic-runner] adb_swipe timeout serial={serial} "
                f"attempt={attempt + 1}",
                flush=True,
            )
        time.sleep(0.3)
    return False


def get_screen_size(driver) -> tuple[int, int]:
    try:
        size = driver.get_window_size()
        return int(size["width"]), int(size["height"])
    except Exception:
        return 1080, 2400


async def slow_scroll_step(driver, serial: str, step_fraction: float = 0.42) -> None:
    """Scroll feed by ~step_fraction of screen height. Smaller steps keep
    banners visible long enough to capture cleanly."""
    width, height = get_screen_size(driver)
    x = width // 2
    start_y = int(height * 0.78)
    end_y = int(start_y - height * step_fraction)
    end_y = max(int(height * 0.18), end_y)
    adb_swipe(serial, x=x, start_y=start_y, end_y=end_y, duration_ms=550)


async def center_banner_in_view(
    driver,
    serial: str,
    bounds: tuple[int, int, int, int],
    *,
    target_top_fraction: float = 0.22,
) -> None:
    """Best-effort one-shot scroll so the banner top sits near
    `target_top_fraction`. Caller re-reads bounds after this."""
    width, height = get_screen_size(driver)
    target_top = int(height * target_top_fraction)
    delta = bounds[1] - target_top
    if abs(delta) < int(height * 0.08):
        return
    x = width // 2
    if delta > 0:
        start_y = int(height * 0.55)
        end_y = max(int(height * 0.18), start_y - delta)
    else:
        start_y = int(height * 0.45)
        end_y = min(int(height * 0.85), start_y - delta)
    adb_swipe(serial, x=x, start_y=start_y, end_y=end_y, duration_ms=500)
    await asyncio.sleep(0.9)


SPONSORED_LABEL_TOKENS = ("sponsored", "промо", "спонс", "реклама")
RESULT_TILE_DESC_TOKENS = ("play video", "воспроизвести видео")
SHORT_TILE_DESC_TOKENS = ("play short", "воспроизвести short")
SHORTS_FILTER_CHIP_LABELS = ("Shorts", "Шортс")
RESULTS_VIDEO_FILTER_CHIP_LABELS = ("Videos", "Видео")
RESULTS_ALL_FILTER_CHIP_LABELS = ("All", "Все")
REEL_WATCH_RESOURCE_IDS = (
    "com.google.android.youtube:id/reel_watch_player",
    "com.google.android.youtube:id/reel_watch_fragment_root",
    "com.google.android.youtube:id/reel_time_bar",
)
DISPLAY_URL_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})(/[^\s|]*)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Banner:
    bounds: tuple[int, int, int, int]
    title: str
    tap_bounds: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class _WatchPanelAd:
    bounds: tuple[int, int, int, int]
    title: str
    tap_bounds: tuple[int, int, int, int]
    close_bounds: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class _VideoTile:
    bounds: tuple[int, int, int, int]
    title: str


@dataclass(frozen=True)
class _ShortTile:
    bounds: tuple[int, int, int, int]
    title: str


@dataclass(frozen=True)
class _ShortsReelAd:
    bounds: tuple[int, int, int, int]
    title: str


VIDEO_TILE_METADATA_RE = re.compile(
    r"^(?P<title>.+?)\s+-\s+"
    r"(?P<duration>(?:(?:\d+\s+hours?)(?:,\s*)?)?"
    r"(?:(?:\d+\s+minutes?)(?:,\s*)?)?"
    r"(?:\d+\s+seconds?))\s+-\s+"
    r"(?:Go to channel|Перейти на канал)\s+"
    r"(?P<channel>.+?)(?:\s+-\s+|$)",
    re.IGNORECASE,
)


def parse_video_tile_metadata(description: str) -> dict[str, str | None]:
    """Extract stable display fields from YouTube's video tile content-desc."""
    description = re.sub(r"\s+", " ", (description or "").strip())
    if not description:
        return {"video_title": None, "channel_name": None}
    match = VIDEO_TILE_METADATA_RE.search(description)
    if not match:
        return {"video_title": description, "channel_name": None}
    return {
        "video_title": match.group("title").strip() or None,
        "channel_name": match.group("channel").strip().rstrip(".") or None,
    }


def _channel_name_from_subscribe_desc(description: str) -> str | None:
    description = re.sub(r"\s+", " ", (description or "").strip())
    prefix = "subscribe to "
    if not description.casefold().startswith(prefix):
        return None
    channel = description[len(prefix):].strip().rstrip(".")
    return channel or None


def _normalized_video_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.casefold()).strip()


def _node_text(node: ET.Element) -> str:
    return ((node.attrib.get("text") or "") + " " + (node.attrib.get("content-desc") or "")).strip()


def _bounds_within(
    inner: tuple[int, int, int, int],
    outer: tuple[int, int, int, int],
) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _banner_dedup_key(banner: _Banner) -> str:
    title = banner.title.strip()
    generic_titles = {"visit site banner", "visual sponsored card"}
    if title and title.casefold() not in generic_titles:
        display_url_match = DISPLAY_URL_RE.search(banner.title)
        if display_url_match:
            display_host = display_url_match.group(1).casefold()
            display_path = (display_url_match.group(2) or "").rstrip("/").casefold()
            return f"display:{display_host}{display_path}"
        normalized_title = re.sub(r"\s+", " ", banner.title.casefold()).strip()
        if normalized_title:
            return f"title:{normalized_title[:240]}"
    return f"bounds:{banner.bounds[1] // 50}:{banner.bounds[3] // 50}:{banner.tap_bounds is not None}"


def _landing_destination_key(landing_url: str | None) -> str | None:
    if not landing_url:
        return None
    try:
        parsed = urlparse(landing_url)
        query = parse_qs(parsed.query)
        destination = ""
        for param in ("adurl", "url", "q"):
            values = query.get(param)
            if values and values[0]:
                destination = unquote(values[0])
                break
        if destination:
            dest_parsed = urlparse(destination)
        else:
            dest_parsed = parsed
        if dest_parsed.netloc and dest_parsed.netloc.casefold() not in _TRACKING_HOSTS:
            path = dest_parsed.path.rstrip("/")
            return f"{dest_parsed.netloc.casefold()}{path.casefold()}"
        advertiser_hosts = extract_advertiser_hosts(landing_url)
        if advertiser_hosts:
            return "hosts:" + ",".join(sorted(advertiser_hosts))
        destination = destination or landing_url
        return re.sub(r"\s+", " ", destination.casefold()).strip()
    except Exception:
        return landing_url.casefold()


def _is_play_store_card(root: ET.Element, card_bounds: tuple[int, int, int, int]) -> bool:
    """A sponsored card is Play-Store-bound if its enclosed text mentions
    Google Play or carries a Play Store CTA token (Install / Get the app)."""
    blob = _text_within_bounds(root, card_bounds)
    if any(hint in blob for hint in PLAY_STORE_BANNER_HINT_TOKENS):
        return True
    return any(token in blob for token in AD_CTA_PLAY_STORE_LABEL_TOKENS)


def _label_has_any_token(label: str, tokens: tuple[str, ...]) -> bool:
    low = label.casefold()
    return any(token in low for token in tokens)


def _find_watch_panel_bounds(
    root: ET.Element,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    player_bounds = _player_surface_bounds(root)
    min_top = int(height * 0.24)
    if player_bounds is not None:
        min_top = max(min_top, player_bounds[3] - 40)
    candidates: list[tuple[int, int, tuple[int, int, int, int]]] = []
    for node in root.iter():
        rid = (node.attrib.get("resource-id") or "").strip()
        if not rid.endswith(":id/engagement_panel"):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        panel_width = right - left
        panel_height = bottom - top
        if top < min_top:
            continue
        if panel_width < int(width * 0.85):
            continue
        if panel_height < int(height * 0.40):
            continue
        candidates.append((top, -panel_height, bounds))
    if not candidates:
        return None
    return sorted(candidates)[0][2]


def _watch_panel_has_bottom_sheet_header(
    root: ET.Element,
    panel_bounds: tuple[int, int, int, int],
) -> bool:
    panel_top = panel_bounds[1]
    for node in root.iter():
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None or not _bounds_within(bounds, panel_bounds):
            continue
        if bounds[1] > panel_top + 140:
            continue
        rid = (node.attrib.get("resource-id") or "").strip()
        text = _node_text(node).casefold()
        if rid.endswith(":id/arrow_drag_handle") or "drag handle" in text:
            return True
    return False


def find_watch_panel_sponsored_ad(driver) -> _WatchPanelAd | None:
    """Detect a sponsored engagement bottom-sheet over the watch page.

    This is intentionally separate from `find_top_sponsored_banner`: some
    YouTube watch-panel ads expose almost no advertiser text in XML, only a
    large localized web CTA such as "Докладніше". Mixing that geometry-only
    fallback into feed banner detection would make normal recommendation
    cards riskier.
    """
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return None
    if _root_has_resource_id(root, AD_SKIP_BUTTON_IDS + AD_SPONSOR_TEXT_IDS):
        return None
    width, height = get_screen_size(driver)
    panel_bounds = _find_watch_panel_bounds(root, width=width, height=height)
    if panel_bounds is None:
        return None
    if not _watch_panel_has_bottom_sheet_header(root, panel_bounds):
        return None

    panel_left, panel_top, panel_right, panel_bottom = panel_bounds
    cta_candidates: list[
        tuple[int, int, int, tuple[int, int, int, int], str]
    ] = []
    close_candidates: list[tuple[int, int, tuple[int, int, int, int]]] = []

    for node in root.iter():
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None or not _bounds_within(bounds, panel_bounds):
            continue
        left, top, right, bottom = bounds
        node_width = right - left
        node_height = bottom - top
        label = _node_text(node).strip()
        label_low = label.casefold()
        clickable = node.attrib.get("clickable") == "true"

        if (
            label
            and _label_has_any_token(label, WATCH_PANEL_WEB_CTA_LABEL_TOKENS)
            and not _label_has_any_token(label, AD_CTA_PLAY_STORE_LABEL_TOKENS)
            and node_width >= int(width * 0.45)
            and 45 <= node_height <= 190
            and top >= panel_top + int((panel_bottom - panel_top) * 0.45)
        ):
            cta_candidates.append(
                (0 if clickable else 1, -bottom, -node_width, bounds, label)
            )

        if (
            clickable
            and top <= panel_top + 190
            and left >= int(width * 0.70)
            and 35 <= node_width <= 150
            and 35 <= node_height <= 150
        ):
            close_rank = 0 if _label_has_any_token(label_low, ("close", "закрити", "закрыть")) else 1
            close_candidates.append((close_rank, -right, bounds))

    if not cta_candidates:
        return None

    _, _, _, tap_bounds, cta_label = sorted(cta_candidates)[0]
    title = _banner_title_from_bounds(root, panel_bounds)
    if not title:
        title = f"Watch panel sponsored card - {cta_label}"
    close_bounds = sorted(close_candidates)[0][2] if close_candidates else None
    return _WatchPanelAd(
        bounds=panel_bounds,
        title=title,
        tap_bounds=tap_bounds,
        close_bounds=close_bounds,
    )


def dismiss_watch_panel_if_present(driver, serial: str) -> bool:
    panel_ad = find_watch_panel_sponsored_ad(driver)
    if panel_ad is None or panel_ad.close_bounds is None:
        return False
    left, top, right, bottom = panel_ad.close_bounds
    return adb_tap(serial, (left + right) // 2, (top + bottom) // 2)


def _exact_label_bounds(
    node: ET.Element,
    label: str,
    *,
    clickable_only: bool = False,
) -> list[tuple[int, int, int, int]]:
    target = label.casefold()
    bounds: list[tuple[int, int, int, int]] = []
    for child in node.iter():
        if clickable_only and child.attrib.get("clickable") != "true":
            continue
        if _node_text(child).casefold() != target:
            continue
        child_bounds = parse_bounds(child.attrib.get("bounds"))
        if child_bounds is not None:
            bounds.append(child_bounds)
    return bounds


def _banner_title_from_bounds(
    root: ET.Element,
    card_bounds: tuple[int, int, int, int],
) -> str:
    left, top, right, bottom = card_bounds
    ignored = {"sponsored", "visit site", "learn more"}
    for node in root.iter():
        node_bounds = parse_bounds(node.attrib.get("bounds"))
        if node_bounds is None:
            continue
        if node_bounds[1] < top or node_bounds[3] > bottom:
            continue
        if node_bounds[0] < left or node_bounds[2] > right:
            continue
        candidate_text = _node_text(node).strip()
        candidate_low = candidate_text.casefold()
        if len(candidate_text) >= 18 and any(
            token in candidate_low for token in SPONSORED_LABEL_TOKENS
        ):
            return candidate_text
        if parse_duration_pair(candidate_text) is not None:
            continue
        if any(token in candidate_low for token in RESULT_TILE_DESC_TOKENS):
            continue
        if "go to channel" in candidate_low:
            continue
        if len(candidate_text) >= 18 and candidate_low not in ignored:
            return candidate_text
    return ""


def _web_cta_bounds_in_bounds(
    root: ET.Element,
    card_bounds: tuple[int, int, int, int],
    *,
    screen_width: int,
) -> tuple[int, int, int, int] | None:
    candidates: list[tuple[int, int, int, tuple[int, int, int, int]]] = []
    for node in root.iter():
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None or not _bounds_within(bounds, card_bounds):
            continue
        label = _node_text(node).strip()
        if not label:
            continue
        if not _label_has_any_token(label, WATCH_PANEL_WEB_CTA_LABEL_TOKENS):
            continue
        if _label_has_any_token(label, AD_CTA_PLAY_STORE_LABEL_TOKENS):
            continue
        left, top, right, bottom = bounds
        node_width = right - left
        node_height = bottom - top
        if node_width < 90 or node_height < 35:
            continue
        if node_height > 220:
            continue
        if node_width > screen_width and node.attrib.get("clickable") != "true":
            continue
        candidates.append(
            (
                0 if node.attrib.get("clickable") == "true" else 1,
                -bottom,
                -node_width,
                bounds,
            )
        )
    if not candidates:
        return None
    return sorted(candidates)[0][3]


def _find_visit_site_button_banner(
    root: ET.Element,
    *,
    width: int,
    height: int,
    feed_top: int,
    feed_bottom: int,
) -> _Banner | None:
    """Fallback for image-heavy web ad cards whose accessibility tree exposes
    only the CTA row (Learn more / Visit site), not the Sponsored label/title."""
    candidates: list[tuple[int, int, tuple[int, int, int, int], tuple[int, int, int, int]]] = []
    seen_bounds: set[tuple[int, int, int, int]] = set()
    min_card_height = max(520, int(height * 0.22))
    max_card_height = int(height * 0.68)
    min_card_width = int(width * 0.70)

    for node in root.iter():
        card_bounds = parse_bounds(node.attrib.get("bounds"))
        if card_bounds is None or card_bounds in seen_bounds:
            continue
        left, top, right, bottom = card_bounds
        card_width = right - left
        card_height = bottom - top
        if top < feed_top or bottom > feed_bottom:
            continue
        if card_width < min_card_width:
            continue
        if card_height < min_card_height or card_height > max_card_height:
            continue
        learn_bounds = _exact_label_bounds(node, "learn more")
        visit_bounds = _exact_label_bounds(node, "visit site", clickable_only=True)
        if not learn_bounds or not visit_bounds:
            continue
        blob = _text_within_bounds(root, card_bounds)
        if any(token in blob for token in RESULT_TILE_DESC_TOKENS):
            continue
        if _is_play_store_card(root, card_bounds):
            continue
        seen_bounds.add(card_bounds)
        tap_bounds = sorted(visit_bounds, key=lambda b: (b[1], b[0]))[0]
        candidates.append((top, card_height, card_bounds, tap_bounds))

    if not candidates:
        return None
    _, _, card_bounds, tap_bounds = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    title = _banner_title_from_bounds(root, card_bounds) or "Visit site banner"
    return _Banner(bounds=card_bounds, title=title, tap_bounds=tap_bounds)


def _find_visual_only_banner(
    root: ET.Element,
    *,
    width: int,
    height: int,
    feed_top: int,
    feed_bottom: int,
) -> _Banner | None:
    """Fallback for web ad cards whose text is not exposed in Appium XML.

    This intentionally duplicates the CTA-only card geometry guard in a separate
    helper: visual-only cards need a stricter skeleton match so normal videos and
    Play Store cards keep using the existing paths.
    """
    min_card_height = max(720, int(height * 0.30))
    max_card_height = int(height * 0.58)
    min_card_width = int(width * 0.90)
    candidates: list[
        tuple[int, int, tuple[int, int, int, int], tuple[int, int, int, int]]
    ] = []
    seen_bounds: set[tuple[int, int, int, int]] = set()

    for node in root.iter():
        card_bounds = parse_bounds(node.attrib.get("bounds"))
        if card_bounds is None or card_bounds in seen_bounds:
            continue
        left, top, right, bottom = card_bounds
        card_width = right - left
        card_height = bottom - top
        if top < feed_top or bottom > feed_bottom:
            continue
        if card_width < min_card_width:
            continue
        if card_height < min_card_height or card_height > max_card_height:
            continue

        blob = _text_within_bounds(root, card_bounds)
        if any(token in blob for token in RESULT_TILE_DESC_TOKENS):
            continue
        if any(token in blob for token in SHORT_TILE_DESC_TOKENS):
            continue
        if _is_play_store_card(root, card_bounds):
            continue

        meaningful_blob = re.sub(r"\b(?:more options|action menu)\b", "", blob).strip()
        if meaningful_blob:
            continue

        creative_bounds: tuple[int, int, int, int] | None = None
        arrow_bounds: tuple[int, int, int, int] | None = None
        chip_row_bounds: tuple[int, int, int, int] | None = None
        has_more_options = False

        for child in node.iter():
            child_bounds = parse_bounds(child.attrib.get("bounds"))
            if child_bounds is None or not _bounds_within(child_bounds, card_bounds):
                continue
            child_left, child_top, child_right, child_bottom = child_bounds
            child_width = child_right - child_left
            child_height = child_bottom - child_top
            child_class = child.attrib.get("class") or ""
            child_text = _node_text(child).casefold()

            if (
                "more options" in child_text
                and child_left >= int(width * 0.80)
                and child_top >= top + int(card_height * 0.45)
            ):
                has_more_options = True

            if (
                child.attrib.get("clickable") == "true"
                and child_width >= int(width * 0.85)
                and child_height >= max(330, int(height * 0.16))
                and child_top <= top + int(card_height * 0.12)
                and child_bottom <= top + int(card_height * 0.65)
            ):
                if creative_bounds is None or child_top < creative_bounds[1]:
                    creative_bounds = child_bounds

            if (
                creative_bounds is not None
                and "ImageView" in child_class
                and child_width >= 55
                and child_height >= 55
                and child_width <= 180
                and child_height <= 180
                and child_left >= int(width * 0.82)
                and child_top >= creative_bounds[1] + int((creative_bounds[3] - creative_bounds[1]) * 0.50)
                and child_bottom <= creative_bounds[3]
            ):
                arrow_bounds = child_bounds

            if (
                "RecyclerView" in child_class
                and child_width >= int(width * 0.70)
                and 70 <= child_height <= 180
                and child_top >= top + int(card_height * 0.55)
            ):
                chip_count = 0
                for chip in child.iter():
                    chip_bounds = parse_bounds(chip.attrib.get("bounds"))
                    if chip_bounds is None or not _bounds_within(chip_bounds, child_bounds):
                        continue
                    chip_width = chip_bounds[2] - chip_bounds[0]
                    chip_height = chip_bounds[3] - chip_bounds[1]
                    if (
                        chip.attrib.get("clickable") == "true"
                        and chip_width >= 50
                        and chip_height >= 40
                    ):
                        chip_count += 1
                if chip_count >= 2:
                    chip_row_bounds = child_bounds

        if creative_bounds is None or chip_row_bounds is None or not has_more_options:
            continue

        seen_bounds.add(card_bounds)
        tap_bounds = arrow_bounds or creative_bounds
        candidates.append((top, card_height, card_bounds, tap_bounds))

    if not candidates:
        return None
    _, _, card_bounds, tap_bounds = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    return _Banner(bounds=card_bounds, title="Visual sponsored card", tap_bounds=tap_bounds)


def find_top_sponsored_banner(driver) -> _Banner | None:
    """Return the topmost web sponsored card on the current results page.

    Walks every node carrying a "Sponsored"-style label inside the feed,
    builds a card rectangle around it, and skips cards that point to the
    Play Store. If YouTube exposes only a CTA row for an image-heavy web ad,
    falls back to a strict Learn more + Visit site pair inside one feed card.
    """
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return None
    width, height = get_screen_size(driver)
    feed_top = int(height * 0.10)
    feed_bottom = int(height * 0.92)

    clickable_web_sponsored_labels: dict[tuple[int, int, int, int], str] = {}
    candidates: list[tuple[int, int, int, int]] = []
    for node in root.iter():
        text = _node_text(node).casefold()
        if not text:
            continue
        if not any(token in text for token in SPONSORED_LABEL_TOKENS):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None:
            continue
        clickable_web_sponsored = (
            node.attrib.get("clickable") == "true"
            and _label_has_any_token(text, WATCH_PANEL_WEB_CTA_LABEL_TOKENS)
            and not _label_has_any_token(text, AD_CTA_PLAY_STORE_LABEL_TOKENS)
        )
        # Some watch recommendation ads expose one large clickable
        # accessibility node whose bottom sits behind the bottom nav. Treat it
        # as capturable instead of swiping from inside it, which can open the
        # lander accidentally.
        bottom_limit = (
            int(height * 0.96) if clickable_web_sponsored else feed_bottom
        )
        if bounds[1] < feed_top or bounds[3] > bottom_limit:
            continue
        if clickable_web_sponsored:
            clickable_web_sponsored_labels[bounds] = _node_text(node).strip()
        candidates.append(bounds)
    banner_candidates: list[_Banner] = []
    candidates.sort(key=lambda b: b[1])
    for sponsored_bounds in candidates:
        card_top = max(feed_top, sponsored_bounds[1] - 80)
        card_bottom = min(feed_bottom, sponsored_bounds[3] + 700)
        card_bounds = (0, card_top, width, card_bottom)
        if _is_play_store_card(root, card_bounds):
            continue
        title = (
            _banner_title_from_bounds(root, card_bounds)
            or clickable_web_sponsored_labels.get(sponsored_bounds, "")
        )
        tap_bounds = _web_cta_bounds_in_bounds(
            root,
            card_bounds,
            screen_width=width,
        )
        if tap_bounds is None and sponsored_bounds in clickable_web_sponsored_labels:
            tap_bounds = sponsored_bounds
        banner_candidates.append(
            _Banner(bounds=card_bounds, title=title, tap_bounds=tap_bounds)
        )
        break

    cta_only_banner = _find_visit_site_button_banner(
        root,
        width=width,
        height=height,
        feed_top=feed_top,
        feed_bottom=feed_bottom,
    )
    if cta_only_banner is not None:
        banner_candidates.append(cta_only_banner)
    visual_only_banner = _find_visual_only_banner(
        root,
        width=width,
        height=height,
        feed_top=feed_top,
        feed_bottom=feed_bottom,
    )
    if visual_only_banner is not None:
        banner_candidates.append(visual_only_banner)
    if not banner_candidates:
        return None
    return sorted(banner_candidates, key=lambda banner: banner.bounds[1])[0]


def collect_video_tiles(driver, serial: str | None = None) -> list[_VideoTile]:
    """Return all tappable video tiles on the current results page (Shorts excluded)."""
    _, height = get_screen_size(driver)
    feed_top = int(height * 0.10)
    feed_bottom = int(height * 0.92)

    def _collect(root: ET.Element | None) -> list[_VideoTile]:
        if root is None:
            return []
        tiles: list[_VideoTile] = []
        for node in root.iter():
            desc = (node.attrib.get("content-desc") or "").casefold()
            if not desc:
                continue
            if not any(token in desc for token in RESULT_TILE_DESC_TOKENS):
                continue
            if any(token in desc for token in SHORT_TILE_DESC_TOKENS):
                continue
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            if top < feed_top or bottom > feed_bottom:
                continue
            if (right - left) < 200 or (bottom - top) < 120:
                continue
            # Geometry-based Short rejection (vertical narrow cards).
            if (right - left) < 360:
                continue
            title = (node.attrib.get("content-desc") or "").strip()
            tiles.append(_VideoTile(bounds=bounds, title=title))
        return tiles

    tiles = _collect(parse_xml(safe_page_source(driver)))
    if not tiles and serial:
        tiles = _collect(parse_xml(adb_uiautomator_page_source(serial)))
    return tiles


def has_shorts_reel_surface_source(page_source: str | None) -> bool:
    root = parse_xml(page_source)
    if root is None:
        return False
    if _root_has_resource_id(root, RESULTS_RESOURCE_IDS):
        return False
    return _root_has_resource_id(root, REEL_WATCH_RESOURCE_IDS)


def has_shorts_reel_surface(driver) -> bool:
    return has_shorts_reel_surface_source(safe_page_source(driver))


async def wait_for_shorts_reel(driver, timeout: float, serial: str | None = None) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if has_shorts_reel_surface(driver):
            return True
        if serial:
            adb_source = adb_uiautomator_page_source(serial)
            if has_shorts_reel_surface_source(adb_source):
                return True
        await asyncio.sleep(0.35)
    return False


def reveal_results_filter_chips(driver, serial: str) -> None:
    width, height = get_screen_size(driver)
    adb_swipe(
        serial,
        x=width // 2,
        start_y=int(height * 0.36),
        end_y=int(height * 0.62),
        duration_ms=500,
    )


def _find_filter_chip_bounds(
    driver,
    labels: tuple[str, ...],
    serial: str | None = None,
) -> list[tuple[int, int, int, int]]:
    _, height = get_screen_size(driver)
    normalized = {label.casefold() for label in labels}

    def _collect(root: ET.Element | None) -> list[tuple[int, int, int, int]]:
        if root is None:
            return []
        candidates: list[tuple[int, int, int, int]] = []
        seen: set[tuple[int, int, int, int]] = set()
        for node in root.iter():
            text = (node.attrib.get("text") or "").strip()
            desc = (node.attrib.get("content-desc") or "").strip()
            if text.casefold() not in normalized and desc.casefold() not in normalized:
                continue
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            if top > int(height * 0.35):
                # Excludes the bottom-navigation Shorts tab.
                continue
            if right - left < 24 or bottom - top < 24:
                continue
            if bounds in seen:
                continue
            seen.add(bounds)
            candidates.append(bounds)
        return sorted(candidates, key=lambda b: (b[1], b[0]))

    candidates = _collect(parse_xml(safe_page_source(driver)))
    if not candidates and serial:
        candidates = _collect(parse_xml(adb_uiautomator_page_source(serial)))
    return candidates


async def tap_results_filter_chip(
    driver,
    serial: str,
    labels: tuple[str, ...],
    *,
    attempts: int = 3,
) -> bool:
    for attempt in range(attempts):
        chip_bounds = _find_filter_chip_bounds(driver, labels, serial=serial)
        if chip_bounds:
            left, top, right, bottom = chip_bounds[0]
            if adb_tap(serial, (left + right) // 2, (top + bottom) // 2):
                await asyncio.sleep(1.0)
                return True
        if attempt + 1 < attempts:
            reveal_results_filter_chips(driver, serial)
            await asyncio.sleep(0.8)
    return False


def collect_short_tiles(driver, serial: str | None = None) -> list[_ShortTile]:
    width, height = get_screen_size(driver)

    def _collect(root: ET.Element | None) -> list[_ShortTile]:
        if root is None:
            return []
        tiles: list[_ShortTile] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for node in root.iter():
            desc = (node.attrib.get("content-desc") or "").strip()
            if not desc:
                continue
            lowered = desc.casefold()
            if not any(token in lowered for token in SHORT_TILE_DESC_TOKENS):
                continue
            bounds = parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            if bottom <= int(height * 0.18) or top >= int(height * 0.94):
                continue
            if right <= 0 or left >= width:
                continue
            if (right - left) < 140 or (bottom - top) < 120:
                continue
            title = desc
            for marker in (" - play Short", " - Play Short", " - PLAY SHORT"):
                if marker in title:
                    title = title.split(marker, 1)[0].strip()
                    break
            if not title:
                title = "Shorts"
            key = (title, bounds)
            if key in seen:
                continue
            seen.add(key)
            tiles.append(_ShortTile(bounds=bounds, title=title))
        return sorted(tiles, key=lambda tile: (tile.bounds[1], tile.bounds[0]))

    tiles = _collect(parse_xml(safe_page_source(driver)))
    if not tiles and serial:
        tiles = _collect(parse_xml(adb_uiautomator_page_source(serial)))
    return tiles


def shorts_reel_signature(driver) -> str | None:
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return None
    width, height = get_screen_size(driver)
    values: list[str] = []
    seen: set[str] = set()
    generic_values = {
        "go to channel",
        "pause video",
        "play video",
        "previous video",
        "next video",
        "dislike this video",
        "share this video",
        "remix",
        "home",
        "shorts",
        "create",
        "subscriptions",
        "you",
        "search",
        "more",
        "navigate up",
    }
    progress_re = re.compile(r"\bminutes?\b.*\bseconds?\b.*\bof\b", re.IGNORECASE)

    def add(prefix: str, value: str) -> None:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return
        item = f"{prefix}:{normalized.casefold()}"
        if item in seen:
            return
        seen.add(item)
        values.append(item)

    for node in root.iter():
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        value = text or desc
        if not value:
            continue
        lowered = value.casefold()
        if lowered in generic_values or progress_re.search(value):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if desc.casefold().startswith("subscribe to @"):
            add("channel", desc)
            continue
        if "like this video along with" in lowered:
            add("like", value)
            continue
        if lowered.startswith("view ") and "comment" in lowered:
            add("comments", value)
            continue
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        if left < int(width * 0.88) and top > int(height * 0.70) and bottom < int(height * 0.93):
            if len(value) >= 8:
                add("caption", value)
    if not values:
        return None
    return "|".join(values[:6])


def _shorts_reel_ad_title(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return None
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if any(line.casefold() == "ad" for line in lines):
        title = " ".join(line for line in lines if line.casefold() != "ad").strip()
        return title or "Shorts ad"
    if re.search(r"\bSponsored\b", cleaned, flags=re.IGNORECASE):
        return re.sub(r"\bSponsored\b", "", cleaned, flags=re.IGNORECASE).strip() or "Shorts ad"
    return None


def find_shorts_reel_ad(driver) -> _ShortsReelAd | None:
    root = parse_xml(safe_page_source(driver))
    if root is None or not _root_has_resource_id(root, REEL_WATCH_RESOURCE_IDS):
        return None
    candidates: list[tuple[int, int, tuple[int, int, int, int], str]] = []
    fallback: list[tuple[int, int, tuple[int, int, int, int], str]] = []
    for node in root.iter():
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        value = desc or text
        title = _shorts_reel_ad_title(value)
        if title is None:
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None:
            continue
        score = 0 if node.attrib.get("clickable") == "true" else 1
        item = (score, -(bounds[2] - bounds[0]) * (bounds[3] - bounds[1]), bounds, title)
        if node.attrib.get("clickable") == "true":
            candidates.append(item)
        else:
            fallback.append(item)
    pool = candidates or fallback
    if not pool:
        return None
    _, _, bounds, title = sorted(pool, key=lambda item: (item[0], item[1]))[0]
    return _ShortsReelAd(bounds=bounds, title=title)


def find_shorts_reel_ad_cta_bounds(driver) -> tuple[int, int, int, int] | None:
    root = parse_xml(safe_page_source(driver))
    if root is None or not _root_has_resource_id(root, REEL_WATCH_RESOURCE_IDS):
        return None
    width, height = get_screen_size(driver)
    candidates: list[tuple[int, int, int, tuple[int, int, int, int]]] = []
    for node in root.iter():
        label = _node_text(node).strip()
        if not label:
            continue
        if not _label_has_any_token(label, WATCH_PANEL_WEB_CTA_LABEL_TOKENS):
            continue
        if _label_has_any_token(label, AD_CTA_PLAY_STORE_LABEL_TOKENS):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        node_width = right - left
        node_height = bottom - top
        if top < int(height * 0.55) or bottom > int(height * 0.94):
            continue
        if node_width < int(width * 0.40) or node_height < 45:
            continue
        candidates.append(
            (
                0 if node.attrib.get("clickable") == "true" else 1,
                -bottom,
                -node_width,
                bounds,
            )
        )
    if not candidates:
        return None
    return sorted(candidates)[0][3]


def is_shorts_reel_play_store_ad(driver) -> bool:
    root = parse_xml(safe_page_source(driver))
    if root is None or not _root_has_resource_id(root, REEL_WATCH_RESOURCE_IDS):
        return False
    width, height = get_screen_size(driver)
    for node in root.iter():
        label = _node_text(node).strip()
        if not label:
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        if top < int(height * 0.55) or bottom > int(height * 0.94):
            continue
        if right - left < int(width * 0.20) or bottom - top < 35:
            continue
        label_low = label.casefold()
        if any(hint in label_low for hint in PLAY_STORE_BANNER_HINT_TOKENS):
            return True
        if _label_has_any_token(label, AD_CTA_PLAY_STORE_LABEL_TOKENS):
            return True
    return False


async def capture_shorts_reel_ad(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    topic: str,
    record: TopicRecord,
    run_dir: Path,
    recorder: AndroidScreenRecorder,
    ad: _ShortsReelAd,
    debug_dir: Path,
    record_seconds: float,
    stop_event: asyncio.Event | None = None,
    on_progress: StandaloneProgressCallback | None = None,
) -> bool:
    ads_dir = run_dir / "ads" / safe_topic_slug(topic)
    ad_index = len(record.ads) + 1
    tag = f"shorts_ad{ad_index:02d}"
    dump_xml_snapshot(driver, debug_dir, f"{tag}_detect")
    dump_debug_screenshot(serial, debug_dir, f"{tag}_detect")
    screenshot_path = ads_dir / f"{tag}.png"
    screenshot_rel = (
        str(screenshot_path.relative_to(run_dir))
        if adb_screencap(serial, screenshot_path)
        else None
    )

    rec_handle = None
    video_rel: str | None = None
    recorded_seconds = 0.0
    play_store_seen = is_shorts_reel_play_store_ad(driver)
    try:
        rec_handle = await recorder.start(
            artifact_prefix=f"shorts_ad_{ad_index}_{int(time.time())}"
        )
    except Exception as exc:
        print(
            f"[topic-runner] shorts_ad recorder.start failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
    if rec_handle is not None:
        started = time.monotonic()
        while time.monotonic() - started < max(3.0, record_seconds):
            if stop_event is not None and stop_event.is_set():
                break
            if not has_shorts_reel_surface(driver):
                break
            if is_shorts_reel_play_store_ad(driver):
                play_store_seen = True
                break
            await asyncio.sleep(0.5)
        recorded_seconds = round(time.monotonic() - started, 2)
        try:
            video_path = await recorder.stop(rec_handle, keep_local=True)
        except Exception:
            video_path = None
        if video_path:
            video_rel = str(video_path.relative_to(run_dir))
    dump_xml_snapshot(driver, debug_dir, f"{tag}_record_stop")
    dump_debug_screenshot(serial, debug_dir, f"{tag}_record_stop")

    if play_store_seen or is_shorts_reel_play_store_ad(driver):
        if video_rel:
            try:
                (run_dir / video_rel).unlink(missing_ok=True)
            except Exception:
                pass
        if screenshot_rel:
            try:
                (run_dir / screenshot_rel).unlink(missing_ok=True)
            except Exception:
                pass
        print(
            f"[topic-runner] shorts_ad_play_store_skip topic={topic!r} "
            f"title={ad.title!r} sec={recorded_seconds}",
            flush=True,
        )
        return False

    tap_bounds = find_shorts_reel_ad_cta_bounds(driver) or ad.bounds
    landing_path = ads_dir / f"{tag}_landing.png"
    landing_url, landing_screenshot_taken = await click_banner_and_capture_landing(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        bounds=ad.bounds,
        tap_bounds=tap_bounds,
        landing_screenshot_path=landing_path,
    )
    landing_screenshot_rel = (
        str(landing_path.relative_to(run_dir))
        if landing_screenshot_taken and landing_path.exists()
        else None
    )
    await asyncio.sleep(0.8)
    if has_shorts_reel_surface(driver) or await wait_for_shorts_reel(
        driver, timeout=3.0, serial=serial
    ):
        dump_xml_snapshot(driver, debug_dir, f"{tag}_after_landing_reel")
        dump_debug_screenshot(serial, debug_dir, f"{tag}_after_landing_reel")
    else:
        dump_xml_snapshot(driver, debug_dir, f"{tag}_after_landing_unknown")
        dump_debug_screenshot(serial, debug_dir, f"{tag}_after_landing_unknown")

    record.ads.append(
        AdRecord(
            video=video_rel,
            recorded_seconds=recorded_seconds,
            cta_label=ad.title,
            cta_kind="web",
            landing_url=landing_url,
            landing_screenshot=landing_screenshot_rel,
            screenshot=screenshot_rel,
        )
    )
    await emit_progress(
        on_progress,
        event="ad_captured",
        run_dir=run_dir,
        topic=topic,
        topic_record=record,
    )
    print(
        f"[topic-runner] shorts_ad captured topic={topic!r} idx={ad_index} "
        f"title={ad.title!r} sec={recorded_seconds} landing={landing_url}",
        flush=True,
    )
    return True


async def capture_shorts_results_banner_if_present(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    topic: str,
    record: TopicRecord,
    run_dir: Path,
    debug_dir: Path,
    on_progress: StandaloneProgressCallback | None = None,
) -> bool:
    # Duplicates search-results banner capture on purpose: Shorts-filter
    # result pages expose Sponsored cards with the same XML/CTA contract.
    banner = find_top_sponsored_banner(driver)
    if banner is None:
        dump_xml_snapshot(driver, debug_dir, "shorts_banner_none")
        return False
    if banner.tap_bounds is None:
        await center_banner_in_view(driver, serial, banner.bounds)
        await asyncio.sleep(0.6)
        banner = find_top_sponsored_banner(driver) or banner

    banners_dir = run_dir / "banners" / safe_topic_slug(topic)
    banner_index = len(record.banners)
    screenshot_path = banners_dir / f"shorts_banner_{banner_index}.png"
    screenshot_rel = (
        str(screenshot_path.relative_to(run_dir))
        if adb_screencap(serial, screenshot_path)
        else ""
    )
    landing_path = banners_dir / f"shorts_banner_{banner_index}_landing.png"
    landing_url, landing_screenshot_taken = await click_banner_and_capture_landing(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        bounds=banner.bounds,
        tap_bounds=banner.tap_bounds,
        landing_screenshot_path=landing_path,
    )
    landing_screenshot_rel = (
        str(landing_path.relative_to(run_dir))
        if landing_screenshot_taken and landing_path.exists()
        else None
    )
    if await recover_youtube_surface_after_banner_click(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        expected_surface=SURFACE_RESULTS,
        timeout=8.0,
    ):
        dump_xml_snapshot(driver, debug_dir, "shorts_banner_after_landing_close")
        dump_debug_screenshot(serial, debug_dir, "shorts_banner_after_landing_close")
    else:
        dump_xml_snapshot(driver, debug_dir, "shorts_banner_after_landing_not_results")
        dump_debug_screenshot(serial, debug_dir, "shorts_banner_after_landing_not_results")
        return False

    landing_key = _landing_destination_key(landing_url)
    existing_keys = {
        _landing_destination_key(existing.landing_url)
        for existing in record.banners
        if existing.landing_url
    }
    if landing_key and landing_key in existing_keys:
        print(
            f"[topic-runner] shorts_banner_duplicate topic={topic!r} key={landing_key!r}",
            flush=True,
        )
        return False

    record.banners.append(
        BannerRecord(
            scroll_round=record.scroll_rounds,
            position=1,
            title=banner.title,
            screenshot=screenshot_rel,
            bounds=banner.bounds,
            landing_url=landing_url,
            landing_screenshot=landing_screenshot_rel,
        )
    )
    await emit_progress(
        on_progress,
        event="banner_captured",
        run_dir=run_dir,
        topic=topic,
        topic_record=record,
    )
    print(
        f"[topic-runner] shorts_banner topic={topic!r} "
        f"title={banner.title!r} landing={landing_url}",
        flush=True,
    )
    return True


async def open_short_tile(
    driver,
    serial: str,
    tile: _ShortTile,
    *,
    debug_dir: Path,
) -> bool:
    left, top, right, bottom = tile.bounds
    width = max(1, right - left)
    height = max(1, bottom - top)
    tap_points = (
        (int(left + width * 0.50), int(top + height * 0.46)),
        (int(left + width * 0.50), int(top + height * 0.72)),
        (int(left + width * 0.34), int(top + height * 0.30)),
        (int(left + width * 0.66), int(top + height * 0.30)),
    )
    for idx, (x, y) in enumerate(dict.fromkeys(tap_points)):
        dump_xml_snapshot(driver, debug_dir, f"open_short_attempt_{idx:02d}_pre")
        if not adb_tap(serial, x, y):
            continue
        if await wait_for_shorts_reel(driver, timeout=5.0, serial=serial):
            dump_xml_snapshot(driver, debug_dir, f"open_short_attempt_{idx:02d}_opened")
            return True
    dump_xml_snapshot(driver, debug_dir, "open_short_failed")
    dump_debug_screenshot(serial, debug_dir, "open_short_failed")
    return False


async def swipe_to_next_short(
    driver,
    serial: str,
    *,
    debug_dir: Path,
    index: int,
) -> bool:
    if not has_shorts_reel_surface(driver):
        return False
    before_signature = shorts_reel_signature(driver)
    width, height = get_screen_size(driver)
    paths = (
        (width // 2, int(height * 0.82), int(height * 0.20)),
        (width // 2, int(height * 0.86), int(height * 0.16)),
    )
    for attempt, (x, start_y, end_y) in enumerate(paths):
        adb_swipe(serial, x=x, start_y=start_y, end_y=end_y, duration_ms=650)
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            if not has_shorts_reel_surface(driver):
                await asyncio.sleep(0.25)
                continue
            after_signature = shorts_reel_signature(driver)
            if before_signature and after_signature and after_signature != before_signature:
                dump_xml_snapshot(driver, debug_dir, f"short_swipe_{index:02d}_{attempt:02d}_changed")
                return True
            await asyncio.sleep(0.3)
    dump_xml_snapshot(driver, debug_dir, f"short_swipe_{index:02d}_not_changed")
    dump_debug_screenshot(serial, debug_dir, f"short_swipe_{index:02d}_not_changed")
    return False


async def restore_regular_results_after_shorts(
    driver,
    serial: str,
    youtube_pkg: str,
    topic: str,
    *,
    debug_dir: Path,
) -> bool:
    if has_shorts_reel_surface(driver):
        adb_keyevent(serial, "4")
        await asyncio.sleep(1.0)
    if has_results_surface(driver):
        reveal_results_filter_chips(driver, serial)
        await asyncio.sleep(0.8)
        # Prefer Videos before opening a normal watch page. This duplicates the
        # result-chip tapper above intentionally; keep it local to this one-file
        # runner until the Shorts flow stabilizes.
        if await tap_results_filter_chip(driver, serial, RESULTS_VIDEO_FILTER_CHIP_LABELS, attempts=2):
            dump_xml_snapshot(driver, debug_dir, "restore_videos_chip")
            return True
        if await tap_results_filter_chip(driver, serial, RESULTS_ALL_FILTER_CHIP_LABELS, attempts=2):
            dump_xml_snapshot(driver, debug_dir, "restore_all_chip")
            return True
    if open_results_deeplink(serial, topic, youtube_pkg):
        restored = await wait_for_results(driver, timeout=12.0, serial=serial)
        if restored:
            dump_xml_snapshot(driver, debug_dir, "restore_deeplink")
        return restored
    return False


async def run_shorts_phase(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    topic: str,
    record: TopicRecord,
    run_dir: Path,
    recorder: AndroidScreenRecorder,
    max_duration_seconds: float,
    stop_event: asyncio.Event | None = None,
    on_progress: StandaloneProgressCallback | None = None,
) -> int:
    if not SHORTS_PHASE_ENABLED or max_duration_seconds < 20:
        return 0
    debug_dir = run_dir / "debug" / safe_topic_slug(topic) / "shorts"
    deadline = time.monotonic() + min(max_duration_seconds, float(SHORTS_PHASE_MAX_SECONDS))
    watched_count = 0
    opened_groups = 0
    seen_tiles: set[tuple[str, tuple[int, int, int, int]]] = set()
    seen_tile_titles: set[str] = set()

    print(
        f"[topic-runner] shorts_phase:start topic={topic!r} "
        f"budget={min(max_duration_seconds, float(SHORTS_PHASE_MAX_SECONDS)):.1f}s",
        flush=True,
    )
    dump_xml_snapshot(driver, debug_dir, "before_reveal")
    reveal_results_filter_chips(driver, serial)
    await asyncio.sleep(0.8)
    dump_xml_snapshot(driver, debug_dir, "after_reveal")

    if not await tap_results_filter_chip(driver, serial, SHORTS_FILTER_CHIP_LABELS, attempts=3):
        dump_xml_snapshot(driver, debug_dir, "no_shorts_chip")
        dump_debug_screenshot(serial, debug_dir, "no_shorts_chip")
        print(f"[topic-runner] shorts_phase:no_chip topic={topic!r}", flush=True)
        return 0
    dump_xml_snapshot(driver, debug_dir, "after_chip")

    # Shorts-filter results often start with a normal Sponsored card. This is a
    # one-file duplicate of the banner-scroll primitive on purpose: it reuses
    # the proven banner landing capture while keeping the main search harvest
    # untouched.
    await capture_shorts_results_banner_if_present(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        topic=topic,
        record=record,
        run_dir=run_dir,
        debug_dir=debug_dir,
        on_progress=on_progress,
    )
    await slow_scroll_step(driver, serial, step_fraction=0.36)
    await asyncio.sleep(0.8)
    dump_xml_snapshot(driver, debug_dir, "after_initial_scroll")

    while opened_groups < SHORTS_PHASE_GROUPS and time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        tile: _ShortTile | None = None
        for attempt in range(3):
            tiles = collect_short_tiles(driver, serial=serial)
            for candidate in tiles:
                key = (candidate.title, candidate.bounds)
                # Duplicate of the local Shorts tile tracking above, but title-only:
                # after returning from a Short, the same tile can move to new bounds.
                title_key = re.sub(r"\s+", " ", candidate.title.casefold()).strip()
                if key not in seen_tiles and title_key not in seen_tile_titles:
                    tile = candidate
                    seen_tiles.add(key)
                    seen_tile_titles.add(title_key)
                    break
            if tile is not None:
                break
            if attempt < 2:
                await slow_scroll_step(driver, serial, step_fraction=0.42)
                await asyncio.sleep(0.8)
                dump_xml_snapshot(driver, debug_dir, f"group_{opened_groups:02d}_scroll_{attempt:02d}")
        if tile is None:
            print(f"[topic-runner] shorts_phase:no_tiles topic={topic!r}", flush=True)
            break

        if not await open_short_tile(driver, serial, tile, debug_dir=debug_dir):
            await slow_scroll_step(driver, serial, step_fraction=0.42)
            await asyncio.sleep(0.8)
            continue

        print(
            f"[topic-runner] shorts_phase:opened topic={topic!r} "
            f"group={opened_groups + 1} title={tile.title!r}",
            flush=True,
        )
        for swipe_idx in range(SHORTS_SWIPES_PER_OPEN + 1):
            if stop_event is not None and stop_event.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 2.0:
                break
            reel_ad = find_shorts_reel_ad(driver)
            if reel_ad is not None:
                await capture_shorts_reel_ad(
                    driver=driver,
                    serial=serial,
                    youtube_pkg=youtube_pkg,
                    activity=activity,
                    topic=topic,
                    record=record,
                    run_dir=run_dir,
                    recorder=recorder,
                    ad=reel_ad,
                    debug_dir=debug_dir,
                    record_seconds=min(float(SHORTS_AD_RECORD_SECONDS), remaining),
                    stop_event=stop_event,
                    on_progress=on_progress,
                )
                if not has_shorts_reel_surface(driver):
                    if not await wait_for_shorts_reel(
                        driver, timeout=3.0, serial=serial
                    ):
                        break
                if swipe_idx >= SHORTS_SWIPES_PER_OPEN:
                    break
                if not await swipe_to_next_short(
                    driver,
                    serial,
                    debug_dir=debug_dir,
                    index=watched_count + 1,
                ):
                    break
                continue
            watch_for = min(
                random.uniform(
                    float(SHORTS_WATCH_MIN_SECONDS),
                    float(max(SHORTS_WATCH_MIN_SECONDS, SHORTS_WATCH_MAX_SECONDS)),
                ),
                remaining,
            )
            await asyncio.sleep(max(0.5, watch_for))
            watched_count += 1
            dump_xml_snapshot(driver, debug_dir, f"watched_{watched_count:02d}")
            print(
                f"[topic-runner] shorts_phase:watched topic={topic!r} "
                f"n={watched_count} seconds={watch_for:.1f}",
                flush=True,
            )
            if swipe_idx >= SHORTS_SWIPES_PER_OPEN:
                break
            if not await swipe_to_next_short(
                driver,
                serial,
                debug_dir=debug_dir,
                index=watched_count,
            ):
                print(
                    f"[topic-runner] shorts_phase:swipe_failed topic={topic!r} "
                    f"after={watched_count}",
                    flush=True,
                )
                break

        adb_keyevent(serial, "4")
        await asyncio.sleep(1.0)
        if not await wait_for_results(driver, timeout=6.0, serial=serial):
            dump_xml_snapshot(driver, debug_dir, f"group_{opened_groups:02d}_after_back_not_results")
            break
        dump_xml_snapshot(driver, debug_dir, f"group_{opened_groups:02d}_after_back")
        opened_groups += 1
        if opened_groups < SHORTS_PHASE_GROUPS and time.monotonic() < deadline:
            await slow_scroll_step(driver, serial, step_fraction=0.42)
            await asyncio.sleep(0.8)

    await restore_regular_results_after_shorts(
        driver,
        serial,
        youtube_pkg,
        topic,
        debug_dir=debug_dir,
    )
    print(
        f"[topic-runner] shorts_phase:done topic={topic!r} watched={watched_count}",
        flush=True,
    )
    return watched_count


VIDEO_END_TEXT_TOKENS = (
    "suggested video",
    "play now",
    "play next video",
    "replay",
    "play again",
)


def is_video_ended_source(page_source: str | None) -> bool:
    """Detect YouTube's end screen without treating recommendation tiles as end.

    The end screen keeps `watch_full` visible, so surface detection alone is
    not enough. Require both a player progress value at the end and an
    explicit end-screen action/title.
    """
    root = parse_xml(page_source)
    if root is None:
        return False

    has_end_text = False
    has_finished_progress = False
    for node in root.iter():
        text = _node_text(node)
        low = text.casefold()
        if any(token in low for token in VIDEO_END_TEXT_TOKENS):
            has_end_text = True
        pair = parse_duration_pair(text)
        if pair is not None:
            elapsed, total = pair
            if total > 0 and elapsed >= max(0, total - 1):
                has_finished_progress = True
        if has_end_text and has_finished_progress:
            return True
    return False


def is_video_ended_state(driver) -> bool:
    return is_video_ended_source(safe_page_source(driver))


def read_watch_video_duration_pair(driver) -> tuple[int, int] | None:
    """Read the active normal video seekbar duration from the watch player.

    This intentionally scans for the same elapsed/total pattern used by the
    ad-state reader, but it is only called from the non-ad branch. Recommendation
    tiles expose plain durations, not "elapsed of total" pairs.
    """
    root = parse_xml(safe_page_source(driver))
    if root is None:
        return None
    if _root_has_resource_id(root, MINIPLAYER_RESOURCE_IDS):
        return None
    if not _root_has_resource_id(root, WATCH_FULL_RESOURCE_IDS):
        return None
    duration_pairs: list[tuple[int, int]] = []
    for node in root.iter():
        pair = parse_duration_pair(_node_text(node))
        if pair is None:
            continue
        elapsed, total = pair
        if total > 0 and 0 <= elapsed <= total + 2:
            duration_pairs.append(pair)
    if not duration_pairs:
        return None
    return max(duration_pairs, key=lambda p: p[1])


def watch_video_total_changed(anchor_total: int | None, current_total: int | None) -> bool:
    if anchor_total is None or current_total is None:
        return False
    if anchor_total < 30 or current_total < 30:
        return False
    return abs(current_total - anchor_total) >= max(10, int(anchor_total * 0.08))


async def click_banner_and_capture_landing(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    bounds: tuple[int, int, int, int],
    landing_screenshot_path: Path,
    tap_bounds: tuple[int, int, int, int] | None = None,
) -> tuple[str | None, bool]:
    # Duplicates the video CTA landing-capture flow intentionally: banner ads
    # use a different tap target, and keeping this separate avoids changing the
    # proven in-video ad path.
    left, top, right, bottom = tap_bounds or bounds
    tap_x = (left + right) // 2
    if tap_bounds is None:
        tap_y = top + int((bottom - top) * 0.35)
    else:
        tap_y = (top + bottom) // 2
    if not adb_tap(serial, tap_x, tap_y):
        return None, False

    def _external_package_from_sources() -> str:
        fg = current_foreground_package(serial)
        if fg and fg != youtube_pkg:
            return fg
        driver_root = parse_xml(safe_page_source(driver))
        driver_top = _source_top_package(driver_root) if driver_root is not None else ""
        if _is_external_package(driver_top):
            return driver_top
        adb_source = adb_uiautomator_page_source(serial)
        adb_root = parse_xml(adb_source)
        adb_top = _source_top_package(adb_root) if adb_root is not None else ""
        if _is_external_package(adb_top):
            return adb_top
        return ""

    started = time.monotonic()
    opened_external = False
    while time.monotonic() - started < 12.0:
        await asyncio.sleep(0.5)
        if _external_package_from_sources():
            opened_external = True
            break

    # Some Custom Tabs appear late on slow/heavy landing pages. Give them a
    # short second window before deciding this tap did not leave YouTube.
    settle_until = time.monotonic() + (2.0 if opened_external else 3.0)
    while time.monotonic() < settle_until:
        await asyncio.sleep(0.5)
        if _external_package_from_sources():
            opened_external = True
            break
    if not opened_external:
        return None, False

    landing_url = await wait_for_resolved_landing_url(serial, youtube_pkg)
    screenshot_taken = await capture_settled_landing_screenshot(
        driver=driver,
        serial=serial,
        path=landing_screenshot_path,
    )

    await close_external_surface(serial, youtube_pkg, activity)
    return landing_url, screenshot_taken


async def harvest_banners(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    topic: str,
    record: TopicRecord,
    scroll_rounds: int,
    run_dir: Path,
    settle_seconds: float = 1.3,
    stop_event: asyncio.Event | None = None,
    on_progress: StandaloneProgressCallback | None = None,
) -> bool:
    seen_banner_keys: set[str] = set()
    seen_landing_keys: set[str] = set()
    banners_dir = run_dir / "banners" / safe_topic_slug(topic)
    debug_dir = run_dir / "debug" / safe_topic_slug(topic) / "harvest"

    for round_idx in range(scroll_rounds):
        if stop_event is not None and stop_event.is_set():
            return True
        await asyncio.sleep(settle_seconds)
        record.scroll_rounds = round_idx + 1
        if not await recover_youtube_surface_after_banner_click(
            driver=driver,
            serial=serial,
            youtube_pkg=youtube_pkg,
            activity=activity,
            expected_surface=SURFACE_RESULTS,
            timeout=3.0,
        ):
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_not_results_skip")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_not_results_skip")
            return False
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_pre")

        banner = find_top_sponsored_banner(driver)
        if banner is None:
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_no_banner")
        else:
            # Dedup by approximate top-y after centering. CTA-only cards already
            # carry exact Visit site coordinates, so do not move them before tap.
            if banner.tap_bounds is None:
                await center_banner_in_view(driver, serial, banner.bounds)
                await asyncio.sleep(0.6)
                dump_xml_snapshot(
                    driver, debug_dir, f"round_{round_idx:02d}_after_center"
                )
                banner = find_top_sponsored_banner(driver) or banner
            else:
                dump_xml_snapshot(
                    driver, debug_dir, f"round_{round_idx:02d}_after_center"
                )

            dedup_key = _banner_dedup_key(banner)
            if dedup_key not in seen_banner_keys:
                seen_banner_keys.add(dedup_key)

                screenshot_rel = ""
                screenshot_path = banners_dir / f"banner_{round_idx}.png"
                if adb_screencap(serial, screenshot_path):
                    screenshot_rel = str(screenshot_path.relative_to(run_dir))

                landing_url: str | None = None
                landing_screenshot_rel: str | None = None
                landing_path = banners_dir / f"banner_{round_idx}_landing.png"
                landing_url, landing_screenshot_taken = (
                    await click_banner_and_capture_landing(
                        driver=driver,
                        serial=serial,
                        youtube_pkg=youtube_pkg,
                        activity=activity,
                        bounds=banner.bounds,
                        tap_bounds=banner.tap_bounds,
                        landing_screenshot_path=landing_path,
                    )
                )
                if landing_screenshot_taken:
                    landing_screenshot_rel = str(landing_path.relative_to(run_dir))
                if await recover_youtube_surface_after_banner_click(
                    driver=driver,
                    serial=serial,
                    youtube_pkg=youtube_pkg,
                    activity=activity,
                    expected_surface=SURFACE_RESULTS,
                    timeout=8.0,
                ):
                    dump_xml_snapshot(
                        driver, debug_dir, f"round_{round_idx:02d}_after_landing_close"
                    )
                else:
                    dump_xml_snapshot(
                        driver, debug_dir, f"round_{round_idx:02d}_after_landing_not_results"
                    )
                    dump_debug_screenshot(
                        serial, debug_dir, f"round_{round_idx:02d}_after_landing_not_results"
                    )
                    return False

                landing_key = _landing_destination_key(landing_url)
                if landing_key and landing_key in seen_landing_keys:
                    print(
                        f"[topic-runner] banner_duplicate topic={topic!r} "
                        f"round={round_idx} key={landing_key!r}",
                        flush=True,
                    )
                else:
                    if landing_key:
                        seen_landing_keys.add(landing_key)
                    record.banners.append(
                        BannerRecord(
                            scroll_round=round_idx,
                            position=1,
                            title=banner.title,
                            screenshot=screenshot_rel,
                            bounds=banner.bounds,
                            landing_url=landing_url,
                            landing_screenshot=landing_screenshot_rel,
                        )
                    )
                    await emit_progress(
                        on_progress,
                        event="banner_captured",
                        run_dir=run_dir,
                        topic=topic,
                        topic_record=record,
                    )
                    print(
                        f"[topic-runner] banner topic={topic!r} round={round_idx} "
                        f"title={banner.title!r} landing={landing_url}",
                        flush=True,
                    )
            else:
                dump_xml_snapshot(
                    driver, debug_dir, f"round_{round_idx:02d}_dedup_skip"
                )

        if round_idx + 1 < scroll_rounds:
            if not await recover_youtube_surface_after_banner_click(
                driver=driver,
                serial=serial,
                youtube_pkg=youtube_pkg,
                activity=activity,
                expected_surface=SURFACE_RESULTS,
                timeout=3.0,
            ):
                dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_before_scroll_not_results")
                dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_before_scroll_not_results")
                return False
            await slow_scroll_step(driver, serial)
    return True


async def harvest_watch_recommendation_banner_step(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    topic: str,
    record: TopicRecord,
    round_idx: int,
    run_dir: Path,
    seen_banner_keys: set[str],
    seen_landing_keys: set[str],
    stop_event: asyncio.Event | None = None,
    on_progress: StandaloneProgressCallback | None = None,
) -> None:
    """Best-effort one-step banner harvest from recommendations under a
    playing watch page.

    This intentionally duplicates the search-results banner harvest flow for
    now. The watch page has stricter safety gates (never click while an in-video
    ad is active), and keeping the flows separate makes the video-ad path easier
    to reason about until the behavior is stable.
    """
    banners_dir = run_dir / "banners" / safe_topic_slug(topic)
    debug_dir = run_dir / "debug" / safe_topic_slug(topic) / "watch_banners"

    if stop_event is not None and stop_event.is_set():
        return
    if not await recover_youtube_surface_after_banner_click(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        expected_surface=SURFACE_WATCH_FULL,
        timeout=3.0,
    ):
        return
    if read_ad_playback_state(driver).is_ad:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_ad_active_skip")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_ad_active_skip")
        return
    if not await ensure_watch_video_playing(driver, serial, attempts=2):
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_resume_before_scan_failed")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_resume_before_scan_failed")
        return

    dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_pre")
    dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_pre")

    panel_ad = find_watch_panel_sponsored_ad(driver)
    if panel_ad is not None:
        # Duplicates the inline watch-banner landing flow on purpose. Sponsored
        # engagement panels expose a different XML shape and need their own
        # close-after-return step so the same panel is not clicked repeatedly.
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_panel_detected")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_panel_detected")

        screenshot_rel = ""
        screenshot_path = banners_dir / f"watch_panel_banner_{round_idx}.png"
        if adb_screencap(serial, screenshot_path):
            screenshot_rel = str(screenshot_path.relative_to(run_dir))

        landing_path = banners_dir / f"watch_panel_banner_{round_idx}_landing.png"
        landing_url, landing_screenshot_taken = await click_banner_and_capture_landing(
            driver=driver,
            serial=serial,
            youtube_pkg=youtube_pkg,
            activity=activity,
            bounds=panel_ad.bounds,
            tap_bounds=panel_ad.tap_bounds,
            landing_screenshot_path=landing_path,
        )
        landing_screenshot_rel = (
            str(landing_path.relative_to(run_dir))
            if landing_screenshot_taken and landing_path.exists()
            else None
        )

        if await recover_youtube_surface_after_banner_click(
            driver=driver,
            serial=serial,
            youtube_pkg=youtube_pkg,
            activity=activity,
            expected_surface=SURFACE_WATCH_FULL,
            timeout=8.0,
        ):
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_panel_after_landing_close")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_panel_after_landing_close")
            if dismiss_watch_panel_if_present(driver, serial):
                await asyncio.sleep(0.6)
                dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_panel_after_dismiss")
                dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_panel_after_dismiss")
            if not read_ad_playback_state(driver).is_ad:
                resumed = await ensure_watch_video_playing(driver, serial)
                dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_panel_resume_checked")
                dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_panel_resume_checked")
                if not resumed:
                    print(
                        f"[topic-runner] watch_panel_banner_resume_failed topic={topic!r} "
                        f"round={round_idx}",
                        flush=True,
                    )
        else:
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_panel_after_landing_not_watch")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_panel_after_landing_not_watch")

        landing_key = _landing_destination_key(landing_url)
        if landing_key and landing_key in seen_landing_keys:
            print(
                f"[topic-runner] watch_panel_banner_duplicate topic={topic!r} "
                f"round={round_idx} key={landing_key!r}",
                flush=True,
            )
        else:
            if landing_key:
                seen_landing_keys.add(landing_key)
            record.banners.append(
                BannerRecord(
                    scroll_round=round_idx,
                    position=1,
                    title=panel_ad.title,
                    screenshot=screenshot_rel,
                    bounds=panel_ad.bounds,
                    landing_url=landing_url,
                    landing_screenshot=landing_screenshot_rel,
                )
            )
            await emit_progress(
                on_progress,
                event="banner_captured",
                run_dir=run_dir,
                topic=topic,
                topic_record=record,
            )
            print(
                f"[topic-runner] watch_panel_banner topic={topic!r} round={round_idx} "
                f"title={panel_ad.title!r} landing={landing_url}",
                flush=True,
            )
        return

    banner = find_top_sponsored_banner(driver)
    if banner is None:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_no_banner")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_no_banner")
        await slow_scroll_step(driver, serial)
        await asyncio.sleep(0.8)
        if detect_surface(driver) != SURFACE_WATCH_FULL:
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_scroll_not_watch")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_scroll_not_watch")
            return
        if read_ad_playback_state(driver).is_ad:
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_scroll_ad_active")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_scroll_ad_active")
            return
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_scroll")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_scroll")
        banner = find_top_sponsored_banner(driver)

    if banner is None:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_no_banner_after_scroll")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_no_banner_after_scroll")
        return

    if banner.tap_bounds is None:
        await center_banner_in_view(driver, serial, banner.bounds)
        await asyncio.sleep(0.6)
        if detect_surface(driver) != SURFACE_WATCH_FULL:
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_center_not_watch")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_center_not_watch")
            return
        if read_ad_playback_state(driver).is_ad:
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_center_ad_active")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_center_ad_active")
            return
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_center")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_center")
        banner = find_top_sponsored_banner(driver) or banner
    else:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_center")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_center")

    dedup_key = _banner_dedup_key(banner)
    if dedup_key in seen_banner_keys:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_dedup_skip")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_dedup_skip")
        await slow_scroll_step(driver, serial)
        return
    seen_banner_keys.add(dedup_key)

    if detect_surface(driver) != SURFACE_WATCH_FULL:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_before_click_not_watch")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_before_click_not_watch")
        return
    if read_ad_playback_state(driver).is_ad:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_before_click_ad_active")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_before_click_ad_active")
        return

    screenshot_rel = ""
    screenshot_path = banners_dir / f"watch_banner_{round_idx}.png"
    if adb_screencap(serial, screenshot_path):
        screenshot_rel = str(screenshot_path.relative_to(run_dir))

    landing_path = banners_dir / f"watch_banner_{round_idx}_landing.png"
    landing_url, landing_screenshot_taken = await click_banner_and_capture_landing(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        bounds=banner.bounds,
        tap_bounds=banner.tap_bounds,
        landing_screenshot_path=landing_path,
    )
    landing_screenshot_rel = (
        str(landing_path.relative_to(run_dir))
        if landing_screenshot_taken and landing_path.exists()
        else None
    )

    if await recover_youtube_surface_after_banner_click(
        driver=driver,
        serial=serial,
        youtube_pkg=youtube_pkg,
        activity=activity,
        expected_surface=SURFACE_WATCH_FULL,
        timeout=8.0,
    ):
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_landing_close")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_landing_close")
        if not read_ad_playback_state(driver).is_ad:
            resumed = await ensure_watch_video_playing(driver, serial)
            dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_landing_resume_checked")
            dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_landing_resume_checked")
            if not resumed:
                print(
                    f"[topic-runner] watch_banner_resume_failed topic={topic!r} "
                    f"round={round_idx}",
                    flush=True,
                )
    else:
        dump_xml_snapshot(driver, debug_dir, f"round_{round_idx:02d}_after_landing_not_watch")
        dump_debug_screenshot(serial, debug_dir, f"round_{round_idx:02d}_after_landing_not_watch")

    landing_key = _landing_destination_key(landing_url)
    if landing_key and landing_key in seen_landing_keys:
        print(
            f"[topic-runner] watch_banner_duplicate topic={topic!r} "
            f"round={round_idx} key={landing_key!r}",
            flush=True,
        )
    else:
        if landing_key:
            seen_landing_keys.add(landing_key)
        record.banners.append(
            BannerRecord(
                scroll_round=round_idx,
                position=1,
                title=banner.title,
                screenshot=screenshot_rel,
                bounds=banner.bounds,
                landing_url=landing_url,
                landing_screenshot=landing_screenshot_rel,
            )
        )
        await emit_progress(
            on_progress,
            event="banner_captured",
            run_dir=run_dir,
            topic=topic,
            topic_record=record,
        )
        print(
            f"[topic-runner] watch_banner topic={topic!r} round={round_idx} "
            f"title={banner.title!r} landing={landing_url}",
            flush=True,
        )


async def open_random_video(
    *,
    driver,
    serial: str,
    debug_dir: Path | None = None,
    extra_scrolls: int = 12,
    exclude_titles: set[str] | None = None,
) -> tuple[str, tuple[int, int]] | None:
    """Tap a random video tile from the current results page (Shorts excluded)."""
    import random

    exclude_titles = exclude_titles or set()
    for attempt in range(extra_scrolls + 1):
        if debug_dir is not None:
            dump_xml_snapshot(driver, debug_dir, f"pick_video_attempt_{attempt:02d}")
            dump_debug_screenshot(serial, debug_dir, f"pick_video_attempt_{attempt:02d}")
        tiles = collect_video_tiles(driver, serial=serial)
        eligible_tiles = [
            tile
            for tile in tiles
            if _normalized_video_title(tile.title) not in exclude_titles
        ]
        if eligible_tiles:
            chosen = random.choice(eligible_tiles)
            left, top, right, bottom = chosen.bounds
            tap_x = (left + right) // 2
            tap_y = top + int((bottom - top) * 0.4)
            if adb_tap(serial, tap_x, tap_y):
                return chosen.title, (tap_x, tap_y)
            print(
                f"[topic-runner] video_tap_failed title={chosen.title!r} "
                f"x={tap_x} y={tap_y}",
                flush=True,
            )
            return None
        if attempt < extra_scrolls:
            await slow_scroll_step(driver, serial)
            await asyncio.sleep(1.0)
    return None


async def close_external_surface(serial: str, youtube_pkg: str, activity: str) -> None:
    """Force-stop the external apps and bring YouTube back to the front.

    BACK is only used while a non-YouTube package is foreground, so it cannot
    collapse an active YouTube watch page into the miniplayer. Some Chrome
    Custom Tabs live in the YouTube task and do not reliably disappear from a
    force-stop alone; BACK closes that tab before the force-stop fallback.
    """
    debug_dir = _CLOSE_EXTERNAL_DEBUG_DIR
    call_id = datetime.now().strftime("%H%M%S_%f")[:-3]
    fg_initial = current_foreground_package(serial)
    print(
        f"[topic-runner] close_external:start id={call_id} fg={fg_initial!r}",
        flush=True,
    )
    if debug_dir is not None:
        dump_debug_screenshot(serial, debug_dir, f"close_external_{call_id}_00_start")

    for back_idx in range(3):
        fg = current_foreground_package(serial)
        if fg == youtube_pkg:
            print(
                f"[topic-runner] close_external:back_loop id={call_id} step={back_idx} fg=youtube_already",
                flush=True,
            )
            return
        if fg:
            adb_keyevent(serial, "4")
            await asyncio.sleep(0.8)
            fg_after_back = current_foreground_package(serial)
            print(
                f"[topic-runner] close_external:back id={call_id} step={back_idx} fg_before={fg!r} fg_after={fg_after_back!r}",
                flush=True,
            )
        else:
            print(
                f"[topic-runner] close_external:back_loop id={call_id} step={back_idx} fg=empty break",
                flush=True,
            )
            break
    if debug_dir is not None:
        dump_debug_screenshot(serial, debug_dir, f"close_external_{call_id}_01_after_back")

    adb_force_stop(serial, CHROME_PACKAGE)
    adb_force_stop(serial, PLAY_STORE_PACKAGE)
    await asyncio.sleep(0.8)
    fg_after_force_stop = current_foreground_package(serial)
    print(
        f"[topic-runner] close_external:after_force_stop id={call_id} fg={fg_after_force_stop!r}",
        flush=True,
    )
    if debug_dir is not None:
        dump_debug_screenshot(serial, debug_dir, f"close_external_{call_id}_02_after_force_stop")
    if fg_after_force_stop != youtube_pkg:
        activate_youtube(serial, youtube_pkg, activity)
        await asyncio.sleep(1.5)
        fg_after_activate = current_foreground_package(serial)
        print(
            f"[topic-runner] close_external:after_activate id={call_id} fg={fg_after_activate!r}",
            flush=True,
        )
        if debug_dir is not None:
            dump_debug_screenshot(serial, debug_dir, f"close_external_{call_id}_03_after_activate")


async def recover_youtube_surface_after_banner_click(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    expected_surface: str,
    timeout: float = 8.0,
) -> bool:
    """Return to the expected YouTube surface after a banner landing.

    This intentionally duplicates the banner landing safety net for search and
    watch surfaces. Banner taps can open Chrome Custom Tabs late; without an
    ADB foreground check the next scroll may accidentally scroll the landing.
    """
    started = time.monotonic()
    iter_idx = 0
    call_id = datetime.now().strftime("%H%M%S_%f")[:-3]
    print(
        f"[topic-runner] recover_surface:start id={call_id} expected={expected_surface!r} timeout={timeout}",
        flush=True,
    )
    while time.monotonic() - started < timeout:
        fg = current_foreground_package(serial)
        driver_source = safe_page_source(driver)
        driver_root = parse_xml(driver_source)
        driver_top_package = _source_top_package(driver_root) if driver_root is not None else ""
        if handle_system_anr_dialog_if_present(driver, serial):
            await close_external_surface(serial, youtube_pkg, activity)
            await asyncio.sleep(0.5)
            iter_idx += 1
            continue
        print(
            f"[topic-runner] recover_surface:iter id={call_id} i={iter_idx} "
            f"fg={fg!r} driver_top={driver_top_package!r}",
            flush=True,
        )
        if (fg and fg != youtube_pkg) or _is_external_package(driver_top_package):
            await close_external_surface(serial, youtube_pkg, activity)
            await asyncio.sleep(0.5)
            iter_idx += 1
            continue

        if expected_surface == SURFACE_RESULTS:
            driver_surface = detect_surface_from_source(driver_source)
            if driver_surface == SURFACE_RESULTS:
                print(
                    f"[topic-runner] recover_surface:done id={call_id} reason=driver_results",
                    flush=True,
                )
                return True
            adb_source = adb_uiautomator_page_source(serial)
            adb_root = parse_xml(adb_source)
            adb_top_package = _source_top_package(adb_root) if adb_root is not None else ""
            adb_surface = detect_surface_from_source(adb_source)
            print(
                f"[topic-runner] recover_surface:probe id={call_id} i={iter_idx} "
                f"driver_surface={driver_surface!r} adb_top={adb_top_package!r} "
                f"adb_surface={adb_surface!r}",
                flush=True,
            )
            if _is_external_package(adb_top_package):
                await close_external_surface(serial, youtube_pkg, activity)
                await asyncio.sleep(0.5)
                iter_idx += 1
                continue
            if adb_surface == SURFACE_RESULTS:
                print(
                    f"[topic-runner] recover_surface:done id={call_id} reason=adb_results",
                    flush=True,
                )
                return True
        else:
            driver_surface = detect_surface_from_source(driver_source)
            print(
                f"[topic-runner] recover_surface:probe id={call_id} i={iter_idx} driver_surface={driver_surface!r}",
                flush=True,
            )
            if driver_surface == expected_surface:
                return True

        await asyncio.sleep(0.5)
        iter_idx += 1
    print(
        f"[topic-runner] recover_surface:timeout id={call_id} iterations={iter_idx}",
        flush=True,
    )
    return False


@dataclass
class CtaOutcome:
    label: str | None
    kind: str  # "web", "play_store", "unknown", "none"
    landing_url: str | None
    screenshot_taken: bool


async def click_cta_and_capture(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    landing_screenshot_path: Path,
) -> CtaOutcome:
    cta = find_cta_node(driver)
    if cta is None:
        return CtaOutcome(label=None, kind="none", landing_url=None, screenshot_taken=False)

    label, bounds, kind = cta
    if kind == "play_store":
        # Skip: Play Store CTA opens com.android.vending which we don't want
        # to interact with. Recording is already saved by the caller.
        return CtaOutcome(label=label, kind=kind, landing_url=None, screenshot_taken=False)

    cx = (bounds[0] + bounds[2]) // 2
    cy = (bounds[1] + bounds[3]) // 2
    if not adb_tap(serial, cx, cy):
        return CtaOutcome(
            label=label,
            kind=kind,
            landing_url=None,
            screenshot_taken=False,
        )

    # Wait until foreground is no longer YouTube (max ~6 sec).
    started = time.monotonic()
    while time.monotonic() - started < 6.0:
        await asyncio.sleep(0.5)
        fg = current_foreground_package(serial)
        if fg and fg != youtube_pkg:
            break

    # Give redirects and page paint a moment to settle before screenshot.
    landing_url = await wait_for_resolved_landing_url(serial, youtube_pkg)
    screenshot_taken = await capture_settled_landing_screenshot(
        driver=driver,
        serial=serial,
        path=landing_screenshot_path,
    )

    await close_external_surface(serial, youtube_pkg, activity)
    return CtaOutcome(
        label=label,
        kind=kind,
        landing_url=landing_url,
        screenshot_taken=screenshot_taken,
    )


async def watch_video_loop(
    *,
    driver,
    serial: str,
    youtube_pkg: str,
    activity: str,
    topic: str,
    record: TopicRecord,
    recorder: AndroidScreenRecorder,
    ad_record_seconds: float,
    max_watch_seconds: float,
    watch_banner_rounds: int,
    run_dir: Path,
    opened_video: dict | None = None,
    stop_event: asyncio.Event | None = None,
    on_progress: StandaloneProgressCallback | None = None,
) -> str:
    ads_dir = run_dir / "ads" / safe_topic_slug(topic)
    ads_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = run_dir / "debug" / safe_topic_slug(topic) / "watch"

    started = time.monotonic()
    poll_interval = 1.5
    ad_index = len(record.ads)
    watch_end_reason = "budget"
    watch_banner_round = 0
    next_watch_banner_scan_at = started + 2.0
    watch_banner_scan_interval = 4.0
    seen_watch_banner_keys: set[str] = set()
    seen_watch_landing_keys: set[str] = {
        key
        for key in (_landing_destination_key(banner.landing_url) for banner in record.banners)
        if key
    }
    social_like_pending = random.random() < VIDEO_LIKE_PROBABILITY
    social_subscribe_pending = random.random() < VIDEO_SUBSCRIBE_PROBABILITY
    social_attempts = 0
    next_social_action_at = started + random.uniform(4.0, 12.0)
    watch_video_total_anchor: int | None = None
    if opened_video is not None:
        opened_video["like_planned"] = social_like_pending
        opened_video["subscribe_planned"] = social_subscribe_pending
        opened_video["liked"] = False
        opened_video["subscribed"] = False
        opened_video["social_actions"] = []
        opened_video.setdefault("liked_video_title", None)
        opened_video.setdefault("liked_at", None)
        opened_video.setdefault("subscribed_channel_name", None)
        opened_video.setdefault("subscribed_at", None)

    while time.monotonic() - started < max_watch_seconds:
        if stop_event is not None and stop_event.is_set():
            watch_end_reason = "stopped"
            break
        if detect_surface(driver) != SURFACE_WATCH_FULL:
            if await dismiss_youtube_notification_prompt_if_present(
                driver, serial, debug_dir
            ):
                continue
            # Give YouTube a brief window to restore after an ad CTA round-trip
            # (close_external returns fg='' until the player regains foreground).
            await asyncio.sleep(0.8)
            if detect_surface(driver) != SURFACE_WATCH_FULL:
                if await dismiss_youtube_notification_prompt_if_present(
                    driver, serial, debug_dir
                ):
                    continue
                # Player collapsed to mini, autoplay rolled into the feed, or
                # YouTube was pushed to background — stop watching.
                watch_end_reason = "surface_lost"
                break

        detect_state = read_ad_playback_state(driver)
        if not detect_state.is_ad:
            duration_pair = read_watch_video_duration_pair(driver)
            if duration_pair is not None:
                _, current_total = duration_pair
                if watch_video_total_anchor is None:
                    watch_video_total_anchor = current_total
                    if opened_video is not None:
                        opened_video["duration_total_seconds"] = current_total
                elif watch_video_total_changed(watch_video_total_anchor, current_total):
                    dump_xml_snapshot(driver, debug_dir, "video_changed")
                    dump_debug_screenshot(serial, debug_dir, "video_changed")
                    if opened_video is not None:
                        opened_video["duration_changed_from_seconds"] = watch_video_total_anchor
                        opened_video["duration_changed_to_seconds"] = current_total
                    print(
                        f"[topic-runner] video:duration_changed topic={topic!r} "
                        f"from={watch_video_total_anchor}s to={current_total}s",
                        flush=True,
                    )
                    watch_end_reason = "autoplay_changed"
                    break
            if (
                (social_like_pending or social_subscribe_pending)
                and time.monotonic() >= next_social_action_at
            ):
                social_attempts += 1
                tapped_like, tapped_subscribe = await perform_video_social_action_attempt(
                    driver=driver,
                    serial=serial,
                    debug_dir=debug_dir,
                    topic=topic,
                    opened_video=opened_video,
                    like_pending=social_like_pending,
                    subscribe_pending=social_subscribe_pending,
                )
                if tapped_like:
                    social_like_pending = False
                if tapped_subscribe:
                    social_subscribe_pending = False
                if social_like_pending or social_subscribe_pending:
                    if social_attempts >= VIDEO_SOCIAL_MAX_ATTEMPTS:
                        if opened_video is not None:
                            opened_video["social_attempts_exhausted"] = True
                        social_like_pending = False
                        social_subscribe_pending = False
                    else:
                        next_social_action_at = (
                            time.monotonic() + VIDEO_SOCIAL_RETRY_SECONDS
                        )
            if is_video_ended_state(driver):
                dump_xml_snapshot(driver, debug_dir, "video_ended")
                dump_debug_screenshot(serial, debug_dir, "video_ended")
                watch_end_reason = "video_ended"
                break
            if (
                watch_banner_round < watch_banner_rounds
                and time.monotonic() >= next_watch_banner_scan_at
            ):
                await harvest_watch_recommendation_banner_step(
                    driver=driver,
                    serial=serial,
                    youtube_pkg=youtube_pkg,
                    activity=activity,
                    topic=topic,
                    record=record,
                    round_idx=watch_banner_round,
                    run_dir=run_dir,
                    seen_banner_keys=seen_watch_banner_keys,
                    seen_landing_keys=seen_watch_landing_keys,
                    stop_event=stop_event,
                    on_progress=on_progress,
                )
                watch_banner_round += 1
                next_watch_banner_scan_at = (
                    time.monotonic() + watch_banner_scan_interval
                )
            else:
                await asyncio.sleep(poll_interval)
            continue
        if is_terminal_ad_tail(detect_state):
            # YouTube keeps a final CTA/end-card on screen for a few seconds
            # after an ad is complete. It still looks like a Sponsored ad in
            # XML, but clicking it would duplicate the just-finished creative.
            dump_xml_snapshot(driver, debug_dir, "terminal_tail_detect")
            dump_debug_screenshot(serial, debug_dir, "terminal_tail_detect")
            await wait_past_terminal_ad_tail(driver)
            continue

        # An ad is on the player. Linear pipeline:
        #   1. wait briefly for a CTA button to appear (Google reveals it
        #      ~1-2s into the ad)
        #   2. click CTA and grab the landing — YouTube auto-pauses while
        #      the lander is foreground, so the ad doesn't run out
        #   3. resume playback, anchor on a fresh playback state, then
        #      record the ad video for at most `ad_record_seconds` seconds
        #      OR until the state shows a different ad
        #   4. apply skip policy based on the recorded state and the
        #      current state
        ad_index += 1
        tag = f"ad{ad_index:02d}"
        dump_xml_snapshot(driver, debug_dir, f"{tag}_detect")
        cta = await wait_for_cta_node(driver, timeout=5.0)

        ad_record = AdRecord(
            video=None,
            recorded_seconds=0.0,
            cta_label=None,
            cta_kind="none",
            landing_url=None,
            landing_screenshot=None,
        )

        cta_pre_state = detect_state
        post_cta_state: AdPlaybackState | None = None
        if cta is not None:
            # The overlay is usually expanded right when the CTA appears
            # (Google reveals headline + URL alongside the button). This
            # snapshot is often a stronger anchor than the detect-time one.
            cta_pre_state = read_ad_playback_state(driver)
            dump_xml_snapshot(driver, debug_dir, f"{tag}_cta_pre")
            dump_debug_screenshot(serial, debug_dir, f"{tag}_cta_pre")
            landing_path = ads_dir / f"ad_{ad_index}_landing.png"
            outcome = await click_cta_and_capture(
                driver=driver,
                serial=serial,
                youtube_pkg=youtube_pkg,
                activity=activity,
                landing_screenshot_path=landing_path,
            )
            dump_xml_snapshot(driver, debug_dir, f"{tag}_cta_post_close")
            dump_debug_screenshot(serial, debug_dir, f"{tag}_cta_post_close")
            post_cta_state = read_ad_playback_state(driver)
            ad_record.cta_label = outcome.label
            ad_record.cta_kind = outcome.kind
            ad_record.landing_url = outcome.landing_url
            if outcome.screenshot_taken and landing_path.exists():
                ad_record.landing_screenshot = str(
                    landing_path.relative_to(run_dir)
                )
            if tap_play_if_paused(driver, serial):
                await asyncio.sleep(0.6)
                post_cta_state = read_ad_playback_state(driver)
                dump_xml_snapshot(driver, debug_dir, f"{tag}_resume_after_cta")
                dump_debug_screenshot(serial, debug_dir, f"{tag}_resume_after_cta")

        # Pick whichever pre-CTA snapshot has more identity signals as
        # the anchor for "this ad" — empirically cta_pre_state usually
        # wins because the overlay is expanded at that moment.
        pre_cta_state = (
            cta_pre_state
            if state_anchor_score(cta_pre_state) >= state_anchor_score(detect_state)
            else detect_state
        )

        # Anchor on the state right before recording, after the lander
        # round-trip. Two reasons we may bail out before recording:
        #   (a) the ad ended completely while we were on the lander
        #   (b) the ad transitioned to the *next* ad in the pod — the CTA
        #       we just clicked was for the previous ad, so recording the
        #       current ad would mis-attribute the file
        # In either case the next iteration will pick up whatever's on
        # screen now with a fresh CTA round-trip.
        record_start_state = await wait_for_ad_state(driver, timeout=5.0)
        record_start_state = enrich_ad_state(record_start_state, post_cta_state)
        ad_ended_during_cta = is_terminal_ad_tail(record_start_state)
        ad_changed_during_cta = (
            pre_cta_state.is_ad
            and record_start_state.is_ad
            and state_has_anchor(pre_cta_state)
            and state_has_anchor(record_start_state)
            and not same_ad_identity(pre_cta_state, record_start_state)
        )
        if not record_start_state.is_ad or ad_ended_during_cta or ad_changed_during_cta:
            note = (
                "ad_changed_before_record"
                if ad_changed_during_cta
                else "ad_ended_before_record"
            )
            dump_xml_snapshot(driver, debug_dir, f"{tag}_{note}")
            dump_debug_screenshot(serial, debug_dir, f"{tag}_{note}")
            if ad_ended_during_cta:
                await wait_past_terminal_ad_tail(driver)
            has_recorded_payload = bool(
                ad_record.video
                or ad_record.landing_url
                or ad_record.landing_screenshot
                or ad_record.screenshot
                or ad_record.recorded_seconds > 0
            )
            if has_recorded_payload:
                record.ads.append(ad_record)
                await emit_progress(
                    on_progress,
                    event="ad_captured",
                    run_dir=run_dir,
                    topic=topic,
                    topic_record=record,
                )
                print(
                    f"[topic-runner] ad captured topic={topic!r} idx={ad_index} "
                    f"sec=0.0 cta_kind={ad_record.cta_kind} "
                    f"cta={ad_record.cta_label} landing={ad_record.landing_url} "
                    f"note={note}",
                    flush=True,
                )
            else:
                print(
                    f"[topic-runner] ad skipped_empty topic={topic!r} idx={ad_index} "
                    f"note={note}",
                    flush=True,
                )
            continue

        recorded_full_window = False
        stop_watch_after_capture = False
        dump_xml_snapshot(driver, debug_dir, f"{tag}_record_start")
        try:
            rec_handle = await recorder.start(
                artifact_prefix=f"ad_{ad_index}_{int(time.time())}"
            )
        except Exception as exc:
            rec_handle = None
            dump_xml_snapshot(driver, debug_dir, f"{tag}_recorder_start_failed")
            dump_debug_screenshot(serial, debug_dir, f"{tag}_recorder_start_failed")
            print(
                f"[topic-runner] recorder.start failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
        if rec_handle is not None:
            record_poll = 0.5
            record_time_limit = ad_record_time_limit(
                record_start_state, ad_record_seconds
            )
            record_limit_state = read_ad_playback_state(driver)
            if same_ad(record_start_state, record_limit_state):
                record_time_limit = min(
                    record_time_limit,
                    ad_record_time_limit(record_limit_state, ad_record_seconds),
                )
            else:
                record_time_limit = min(record_time_limit, 1.0)
            rec_started_at = time.monotonic()
            while time.monotonic() - rec_started_at < record_time_limit:
                if stop_event is not None and stop_event.is_set():
                    break
                current = read_ad_playback_state(driver)
                if not same_ad(record_start_state, current):
                    break
                await asyncio.sleep(record_poll)
            else:
                recorded_full_window = record_time_limit >= ad_record_seconds
            recorded_elapsed = round(time.monotonic() - rec_started_at, 2)
            video_path: Path | None = None
            try:
                video_path = await recorder.stop(rec_handle, keep_local=True)
            except Exception:
                video_path = None
            ad_record.recorded_seconds = recorded_elapsed
            if video_path:
                ad_record.video = str(video_path.relative_to(run_dir))
            dump_xml_snapshot(driver, debug_dir, f"{tag}_record_stop")

            # Skip policy:
            #   - mid_pod (1 of N) → drain unconditionally until the real
            #     next ad takes over or the player exits ad mode. This
            #     must run even when the recording was cut short by a
            #     state change, because the cut is usually a same-
            #     advertiser end-card and letting the main loop go would
            #     re-click the same lander.
            #   - else state changed naturally → no skip
            #   - last in pod (i == n) → skip if same ad still on screen
            #   - pod unknown → skip only after a full record window
            current_state = read_ad_playback_state(driver)
            still_same = same_ad(record_start_state, current_state)
            pod_idx = record_start_state.pod_index
            pod_tot = record_start_state.pod_total
            pod_known = pod_idx is not None and pod_tot is not None
            mid_pod = pod_known and pod_idx < pod_tot  # type: ignore[operator]
            start_total_seconds = record_start_state.total_seconds
            advertiser_hosts = extract_advertiser_hosts(ad_record.landing_url)

            def _looks_like_same_advertiser_tail(state: AdPlaybackState) -> bool:
                sig = state.signature
                if not sig or not advertiser_hosts:
                    return False
                sig_low = sig.lower()
                for host in advertiser_hosts:
                    if host in sig_low or sig_low in host:
                        return True
                return False

            strong_new_ad_evidence = (
                start_total_seconds is not None
                and current_state.total_seconds is not None
                and current_state.total_seconds != start_total_seconds
                and current_state.total_seconds > 10
                and current_state.elapsed_seconds is not None
                and current_state.elapsed_seconds <= 5
                and not is_terminal_ad_tail(current_state)
                and not _looks_like_same_advertiser_tail(current_state)
            )

            if mid_pod and strong_new_ad_evidence:
                # XML already proves the next ad started; let the main loop
                # capture it instead of waiting through a drain timeout.
                print(
                    f"[topic-runner] ad mid-pod next_ad_detected topic={topic!r} "
                    f"idx={ad_index}",
                    flush=True,
                )
            elif mid_pod:
                # Drain — wait for the real next ad in the pod to take over.
                # Cannot exit on every state change: the tail of an ad
                # often shows a 5-second end-card with the same advertiser
                # (same signature, different total), and exiting there
                # makes the main loop click the lander a second time.
                # Cap the drain by remaining watch budget so we don't
                # exceed max_watch_seconds while waiting.
                watch_remaining = max(
                    0.0, max_watch_seconds - (time.monotonic() - started)
                )
                drain_budget = min(360.0, watch_remaining)
                drain_started = time.monotonic()
                drain_timed_out = True
                start_pod_index = record_start_state.pod_index
                start_pod_total = record_start_state.pod_total

                while time.monotonic() - drain_started < drain_budget:
                    if stop_event is not None and stop_event.is_set():
                        break
                    drain_state = read_ad_playback_state(driver)
                    if not drain_state.is_ad:
                        # Mid-pod transitions can briefly expose no ad nodes
                        # between the creative and its final CTA/end-card.
                        # A single read here released the same advertiser
                        # tail to the main loop as a duplicate ad.
                        not_ad_started = time.monotonic()
                        while time.monotonic() - not_ad_started < 2.5:
                            await asyncio.sleep(0.5)
                            retry_state = read_ad_playback_state(driver)
                            if retry_state.is_ad:
                                drain_state = retry_state
                                break
                        else:
                            drain_timed_out = False
                            break
                    if is_terminal_ad_tail(drain_state):
                        # Same-ad final CTA/end-cards often have `elapsed ==
                        # total` and a fresh CTA label. They are transition
                        # surfaces, not new creatives; keep draining until a
                        # real next ad or main video takes over.
                        await asyncio.sleep(0.5)
                        continue
                    # Pod position explicitly advanced — definitive next ad.
                    if (
                        drain_state.pod_index is not None
                        and start_pod_index is not None
                        and drain_state.pod_total == start_pod_total
                        and drain_state.pod_index > start_pod_index
                    ):
                        drain_timed_out = False
                        break
                    # Pod-less transition: trust it only if the new ad
                    # has a different total length, has only just started
                    # (low elapsed) and is not the same-advertiser end-card.
                    if (
                        start_total_seconds is not None
                        and drain_state.total_seconds is not None
                        and drain_state.total_seconds != start_total_seconds
                        and drain_state.total_seconds > 10
                        and drain_state.elapsed_seconds is not None
                        and drain_state.elapsed_seconds <= 5
                        and not is_terminal_ad_tail(drain_state)
                        and not _looks_like_same_advertiser_tail(drain_state)
                    ):
                        drain_timed_out = False
                        break
                    await asyncio.sleep(2.0)
                dump_xml_snapshot(driver, debug_dir, f"{tag}_drained")
                if drain_timed_out:
                    dump_debug_screenshot(serial, debug_dir, f"{tag}_drain_timeout")
                    stop_watch_after_capture = True
                    print(
                        f"[topic-runner] ad mid-pod drain timeout topic={topic!r} "
                        f"idx={ad_index}; stop watch to avoid duplicate capture",
                        flush=True,
                    )
            elif not still_same:
                pass  # transitioned naturally on a non-mid-pod ad — no skip
            elif pod_known or recorded_full_window:
                if tap_skip_ad_if_present(driver, serial):
                    dump_xml_snapshot(driver, debug_dir, f"{tag}_skip_tapped")
                    await asyncio.sleep(1.0)

            record.ads.append(ad_record)
            await emit_progress(
                on_progress,
                event="ad_captured",
                run_dir=run_dir,
                topic=topic,
                topic_record=record,
            )
            print(
                f"[topic-runner] ad captured topic={topic!r} idx={ad_index} "
                f"sec={ad_record.recorded_seconds} cta_kind={ad_record.cta_kind} "
                f"cta={ad_record.cta_label} landing={ad_record.landing_url}",
                flush=True,
            )
            if stop_watch_after_capture:
                watch_end_reason = "ad_drain_timeout"
                break

    record.watch_seconds = round(record.watch_seconds + (time.monotonic() - started), 2)
    return watch_end_reason


async def run_topic(
    *,
    driver,
    serial: str,
    config: AndroidAppConfig,
    topic: str,
    scroll_rounds: int,
    ad_record_seconds: float,
    max_watch_seconds: float,
    run_dir: Path,
    recorder: AndroidScreenRecorder,
    max_videos: int | None = None,
    record: TopicRecord | None = None,
    stop_event: asyncio.Event | None = None,
    on_progress: StandaloneProgressCallback | None = None,
) -> TopicRecord:
    if record is None:
        record = TopicRecord(topic=topic, started_at=utc_now_iso())
    debug_root = run_dir / "debug" / safe_topic_slug(topic)
    nav_dir = debug_root / "nav"
    global _CLOSE_EXTERNAL_DEBUG_DIR
    _CLOSE_EXTERNAL_DEBUG_DIR = debug_root / "close_external"

    topic_started = time.monotonic()
    video_count = len(record.opened_videos)
    # On a follow-up pass for the same topic, keep the already harvested search
    # banners/Shorts slice and go straight to picking another normal video.
    search_banners_harvested = bool(record.banners or record.opened_videos)
    seen_video_titles: set[str] = {
        _normalized_video_title(
            str(video.get("title") or video.get("video_title") or "")
        )
        for video in record.opened_videos
        if video.get("title") or video.get("video_title")
    }

    while time.monotonic() - topic_started < max_watch_seconds:
        if stop_event is not None and stop_event.is_set():
            record.skip_reason = record.skip_reason or "stopped"
            break
        if max_videos is not None and video_count >= max_videos:
            break

        remaining_topic_budget = max_watch_seconds - (time.monotonic() - topic_started)
        if remaining_topic_budget <= 0:
            break

        if not open_results_deeplink(serial, topic, config.youtube_package):
            if video_count == 0:
                record.skipped = True
                record.skip_reason = "deeplink_failed"
            break

        if not await wait_for_results(driver, timeout=20.0, serial=serial):
            dump_results_not_loaded_debug(driver, serial, nav_dir)
            if video_count == 0:
                record.skipped = True
                record.skip_reason = "results_not_loaded"
            break
        results_tag = "results_loaded" if video_count == 0 else f"results_loaded_video_{video_count + 1:02d}"
        dump_xml_snapshot(driver, nav_dir, results_tag)

        if not search_banners_harvested:
            harvest_ok = await harvest_banners(
                driver=driver,
                serial=serial,
                youtube_pkg=config.youtube_package,
                activity=config.youtube_activity,
                topic=topic,
                record=record,
                scroll_rounds=scroll_rounds,
                run_dir=run_dir,
                stop_event=stop_event,
                on_progress=on_progress,
            )
            search_banners_harvested = True
            if not harvest_ok:
                if not await recover_youtube_surface_after_banner_click(
                    driver=driver,
                    serial=serial,
                    youtube_pkg=config.youtube_package,
                    activity=config.youtube_activity,
                    expected_surface=SURFACE_RESULTS,
                    timeout=10.0,
                ):
                    if video_count == 0:
                        record.skipped = True
                        record.skip_reason = "results_lost_after_banner"
                    break

            remaining_after_banner = max_watch_seconds - (time.monotonic() - topic_started)
            if SHORTS_PHASE_ENABLED and remaining_after_banner > 60:
                # Optional Shorts slice after the normal search banner harvest.
                # This keeps the old flow order intact: search results -> banner
                # scrolls -> Shorts slice -> normal video pick/watch. Shorts
                # banner/ad harvesting is intentionally not wired here yet.
                shorts_budget = min(
                    float(SHORTS_PHASE_MAX_SECONDS),
                    max(20.0, remaining_after_banner * 0.25),
                )
                await run_shorts_phase(
                    driver=driver,
                    serial=serial,
                    youtube_pkg=config.youtube_package,
                    activity=config.youtube_activity,
                    topic=topic,
                    record=record,
                    run_dir=run_dir,
                    recorder=recorder,
                    max_duration_seconds=shorts_budget,
                    stop_event=stop_event,
                    on_progress=on_progress,
                )

        if not await recover_youtube_surface_after_banner_click(
            driver=driver,
            serial=serial,
            youtube_pkg=config.youtube_package,
            activity=config.youtube_activity,
            expected_surface=SURFACE_RESULTS,
            timeout=5.0,
        ):
            if video_count == 0:
                record.skipped = True
                record.skip_reason = "results_lost_before_video_pick"
            break

        chosen = await open_random_video(
            driver=driver,
            serial=serial,
            debug_dir=debug_root / "pick" / f"video_{video_count + 1:02d}",
            # Duplicate the search-results scroll budget for video picking:
            # banner harvest and video discovery are separate phases, but the
            # user-facing knob should stay one `--scroll-rounds` value for now.
            extra_scrolls=scroll_rounds,
            exclude_titles=seen_video_titles,
        )
        if chosen is None:
            if video_count == 0:
                record.skipped = True
                record.skip_reason = "no_video"
            break

        title, tap = chosen
        video_metadata = parse_video_tile_metadata(title)
        seen_video_titles.add(_normalized_video_title(title))
        opened_video = {
            "title": title,
            "video_title": video_metadata.get("video_title") or title,
            "channel_name": video_metadata.get("channel_name"),
            "tap": list(tap),
            "started_at": utc_now_iso(),
            "watch_seconds": 0.0,
            "end_reason": None,
            "like_planned": False,
            "liked": False,
            "liked_video_title": None,
            "liked_at": None,
            "subscribe_planned": False,
            "subscribed": False,
            "subscribed_channel_name": None,
            "subscribed_at": None,
            "social_actions": [],
        }
        if record.opened_video is None:
            record.opened_video = opened_video
        record.opened_videos.append(opened_video)
        await emit_progress(
            on_progress,
            event="video_opened",
            run_dir=run_dir,
            topic=topic,
            topic_record=record,
        )
        print(
            f"[topic-runner] opened video topic={topic!r} "
            f"n={video_count + 1} title={title!r} "
            f"channel={opened_video.get('channel_name')!r}",
            flush=True,
        )

        if not await wait_for_watch(driver, timeout=20.0, serial=serial):
            dump_xml_snapshot(driver, nav_dir, f"watch_not_loaded_video_{video_count + 1:02d}")
            if video_count == 0:
                record.skipped = True
                record.skip_reason = "watch_not_loaded"
            break
        watch_tag = "watch_loaded" if video_count == 0 else f"watch_loaded_video_{video_count + 1:02d}"
        dump_xml_snapshot(driver, nav_dir, watch_tag)

        video_count += 1
        watch_budget = max_watch_seconds - (time.monotonic() - topic_started)
        if watch_budget <= 0:
            break
        watch_started_at = time.monotonic()
        try:
            watch_result = await watch_video_loop(
                driver=driver,
                serial=serial,
                youtube_pkg=config.youtube_package,
                activity=config.youtube_activity,
                topic=topic,
                record=record,
                recorder=recorder,
                ad_record_seconds=ad_record_seconds,
                max_watch_seconds=watch_budget,
                watch_banner_rounds=scroll_rounds,
                run_dir=run_dir,
                opened_video=opened_video,
                stop_event=stop_event,
                on_progress=on_progress,
            )
        except Exception:
            opened_video["watch_seconds"] = round(
                time.monotonic() - watch_started_at,
                2,
            )
            opened_video["end_reason"] = "exception"
            opened_video["finished_at"] = utc_now_iso()
            record.watch_seconds = round(
                record.watch_seconds + (time.monotonic() - watch_started_at),
                2,
            )
            raise
        print(
            f"[topic-runner] video:done topic={topic!r} n={video_count} "
            f"reason={watch_result} topic_watch={record.watch_seconds}s",
            flush=True,
        )
        opened_video["watch_seconds"] = round(time.monotonic() - watch_started_at, 2)
        opened_video["end_reason"] = watch_result
        opened_video["finished_at"] = utc_now_iso()
        if watch_result == "video_ended":
            continue
        remaining_after_watch = max_watch_seconds - (time.monotonic() - topic_started)
        if (
            watch_result in {"surface_lost", "ad_drain_timeout", "autoplay_changed"}
            and remaining_after_watch >= 60.0
            and (max_videos is None or video_count < max_videos)
        ):
            print(
                f"[topic-runner] video:recoverable_end topic={topic!r} "
                f"reason={watch_result} remaining={remaining_after_watch:.1f}s; "
                "opening another video",
                flush=True,
            )
            continue
        break

    record.finished_at = utc_now_iso()
    return record


def topic_record_to_dict(record: TopicRecord) -> dict:
    return {
        **asdict(record),
        "banners": [asdict(b) for b in record.banners],
        "ads": [asdict(a) for a in record.ads],
    }


def _write_result_json(
    *,
    run_dir: Path,
    started_at: str,
    finished_at: str,
    avd_name: str,
    topic_records: list[TopicRecord],
) -> None:
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "started_at": started_at,
                "finished_at": finished_at,
                "avd": avd_name,
                "topics": [topic_record_to_dict(t) for t in topic_records],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def _prepare_emulator_proxy(
    proxy_url: str | None,
) -> tuple[str | None, AndroidHttpProxyBridge | None, AndroidHttpProxyBridgeHandle | None]:
    resolved = (proxy_url or "").strip()
    if not resolved:
        return None, None, None

    lowered = resolved.casefold()
    if lowered.startswith(("http://", "https://")):
        emulator_proxy = (
            resolved
            .replace("//0.0.0.0:", "//10.0.2.2:")
            .replace("//127.0.0.1:", "//10.0.2.2:")
            .replace("//localhost:", "//10.0.2.2:")
        )
        return emulator_proxy, None, None

    proxy_bridge = AndroidHttpProxyBridge()
    bridge_handle = await proxy_bridge.start(resolved)
    return bridge_handle.emulator_proxy_url, proxy_bridge, bridge_handle


async def run_standalone_session(options: StandaloneRunOptions) -> StandaloneRunResult:
    base_config = options.android_config or AndroidAppConfig()
    config = base_config.model_copy(
        update={
            "enabled": True,
            "manage_appium_server": options.manage_appium,
            "emulator_force_restart_before_run": False,
        }
    )
    if options.avd_name:
        config = config.model_copy(update={"default_avd_name": options.avd_name})
    avd_name = options.avd_name or config.default_avd_name

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = (
        Path(options.run_dir)
        if options.run_dir is not None
        else Path(__file__).resolve().parent / "results" / f"run_{run_ts}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[topic-runner] run_dir={run_dir}", flush=True)

    emulator_proxy_url, proxy_bridge, proxy_bridge_handle = await _prepare_emulator_proxy(
        options.proxy_url
    )

    avd_manager = AndroidAvdManager(
        emulator_start_timeout_seconds=config.emulator_start_timeout_seconds,
        device_ready_timeout_seconds=config.device_ready_timeout_seconds,
    )
    handle = await avd_manager.ensure_device(
        avd_name=avd_name,
        launch=AndroidEmulatorLaunchOptions(
            headless=False if options.headless is None else options.headless,
            gpu_mode=config.emulator_gpu_mode,
            accel_mode=config.emulator_accel_mode,
            http_proxy=emulator_proxy_url,
            skip_adb_auth=config.emulator_skip_adb_auth,
            # Proxy changes are process-level for `-http-proxy`; ADB global
            # settings alone cannot update a running emulator launched with a
            # different proxy. Always start each session from a fresh emulator
            # so the next run cannot inherit stale proxy state.
            force_stop_running=True,
        ),
    )
    serial = handle.adb_serial
    print(f"[topic-runner] device ready serial={serial}", flush=True)
    if emulator_proxy_url:
        set_android_global_http_proxy(serial, emulator_proxy_url)
    else:
        clear_android_global_http_proxy(serial)

    # Appium session.
    appium = AppiumSessionProvider(config)
    driver_handle = await appium.create_youtube_session(
        adb_serial=serial, avd_name=avd_name
    )
    driver = driver_handle.driver
    print("[topic-runner] appium session ready", flush=True)

    recorder = AndroidScreenRecorder(
        adb_serial=serial,
        artifacts_dir=run_dir / "ads_raw",
        bitrate=config.probe_screenrecord_bitrate,
    )

    started_at = utc_now_iso()
    session_started = time.monotonic()
    session_budget = max(0.0, float(options.max_watch_seconds))
    topics = [topic.strip() for topic in options.topics if topic.strip()]
    if not topics:
        raise ValueError("Standalone runner requires at least one topic")
    short_session_mode = len(topics) > 1 and session_budget <= 30 * 60
    topic_records: list[TopicRecord] = []

    try:
        await emit_progress(
            options.on_progress,
            event="session_started",
            run_dir=run_dir,
            topics=topic_records,
            payload={"avd": avd_name},
        )
        print(
            f"[topic-runner] schedule mode="
            f"{'short_one_video_pass' if short_session_mode else 'topic_blocks'} "
            f"topics={len(topics)} session_budget={session_budget:.1f}s",
            flush=True,
        )
        for topic_idx, topic in enumerate(topics):
            if options.stop_event is not None and options.stop_event.is_set():
                print("[topic-runner] stop_event before next topic", flush=True)
                break
            session_elapsed = time.monotonic() - session_started
            session_remaining = session_budget - session_elapsed
            if session_remaining <= 0:
                print("[topic-runner] session budget exhausted", flush=True)
                break

            if short_session_mode:
                topic_budget = session_remaining
                max_videos = 1 if topic_idx < len(topics) - 1 else None
            else:
                remaining_topics = max(1, len(topics) - topic_idx)
                topic_budget = session_remaining / remaining_topics
                max_videos = None

            print(
                f"[topic-runner] topic:start {topic!r} "
                f"budget={topic_budget:.1f}s max_videos={max_videos}",
                flush=True,
            )
            record = TopicRecord(topic=topic, started_at=utc_now_iso())
            await emit_progress(
                options.on_progress,
                event="topic_started",
                run_dir=run_dir,
                topic=topic,
                topic_record=record,
                topics=[*topic_records, record],
            )

            async def _topic_progress(event: StandaloneProgressEvent) -> None:
                current_records = [*topic_records]
                if event.topic_record is not None:
                    current_records.append(event.topic_record)
                await emit_progress(
                    options.on_progress,
                    event=event.event,
                    run_dir=event.run_dir,
                    topic=event.topic,
                    topic_record=event.topic_record,
                    topics=current_records,
                    payload=event.payload,
                )

            try:
                record = await run_topic(
                    driver=driver,
                    serial=serial,
                    config=config,
                    topic=topic,
                    scroll_rounds=options.scroll_rounds,
                    ad_record_seconds=options.ad_record_seconds,
                    max_watch_seconds=topic_budget,
                    run_dir=run_dir,
                    recorder=recorder,
                    max_videos=max_videos,
                    record=record,
                    stop_event=options.stop_event,
                    on_progress=_topic_progress,
                )
            except Exception as exc:
                record.skipped = True
                record.skip_reason = f"exception:{type(exc).__name__}:{exc}"
                record.finished_at = utc_now_iso()
                print(
                    f"[topic-runner] topic:error {topic!r} {type(exc).__name__}: {exc}",
                    flush=True,
                )
            topic_records.append(record)
            _write_result_json(
                run_dir=run_dir,
                started_at=started_at,
                finished_at=utc_now_iso(),
                avd_name=avd_name,
                topic_records=topic_records,
            )
            await emit_progress(
                options.on_progress,
                event="topic_finished",
                run_dir=run_dir,
                topic=topic,
                topic_record=record,
                topics=topic_records,
            )
            print(
                f"[topic-runner] topic:done {topic!r} skipped={record.skipped} "
                f"banners={len(record.banners)} ads={len(record.ads)} "
                f"watch={record.watch_seconds}s",
                flush=True,
            )

        topup_round = 0
        while not short_session_mode:
            if options.stop_event is not None and options.stop_event.is_set():
                print("[topic-runner] stop_event before top-up", flush=True)
                break
            session_elapsed = time.monotonic() - session_started
            session_remaining = session_budget - session_elapsed
            if session_remaining < 60.0:
                break
            playable_records = [
                record
                for record in topic_records
                if record.opened_videos and record.skip_reason != "stopped"
            ]
            if not playable_records:
                break
            topup_round += 1
            made_progress = False
            for record in playable_records:
                if options.stop_event is not None and options.stop_event.is_set():
                    break
                session_elapsed = time.monotonic() - session_started
                session_remaining = session_budget - session_elapsed
                if session_remaining < 60.0:
                    break
                topic_budget = max(60.0, session_remaining / len(playable_records))
                before_watch = float(record.watch_seconds or 0.0)
                before_videos = len(record.opened_videos)
                print(
                    f"[topic-runner] topic:topup_start {record.topic!r} "
                    f"round={topup_round} budget={topic_budget:.1f}s",
                    flush=True,
                )

                async def _topup_progress(event: StandaloneProgressEvent) -> None:
                    await emit_progress(
                        options.on_progress,
                        event=event.event,
                        run_dir=event.run_dir,
                        topic=event.topic,
                        topic_record=event.topic_record,
                        topics=topic_records,
                        payload=event.payload,
                    )

                try:
                    await run_topic(
                        driver=driver,
                        serial=serial,
                        config=config,
                        topic=record.topic,
                        scroll_rounds=options.scroll_rounds,
                        ad_record_seconds=options.ad_record_seconds,
                        max_watch_seconds=topic_budget,
                        run_dir=run_dir,
                        recorder=recorder,
                        max_videos=None,
                        record=record,
                        stop_event=options.stop_event,
                        on_progress=_topup_progress,
                    )
                except Exception as exc:
                    record.skip_reason = f"topup_exception:{type(exc).__name__}:{exc}"
                    record.finished_at = utc_now_iso()
                    print(
                        f"[topic-runner] topic:topup_error {record.topic!r} "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                after_watch = float(record.watch_seconds or 0.0)
                after_videos = len(record.opened_videos)
                made_progress = made_progress or (
                    after_watch > before_watch + 5.0 or after_videos > before_videos
                )
                _write_result_json(
                    run_dir=run_dir,
                    started_at=started_at,
                    finished_at=utc_now_iso(),
                    avd_name=avd_name,
                    topic_records=topic_records,
                )
                await emit_progress(
                    options.on_progress,
                    event="topic_finished",
                    run_dir=run_dir,
                    topic=record.topic,
                    topic_record=record,
                    topics=topic_records,
                )
                print(
                    f"[topic-runner] topic:topup_done {record.topic!r} "
                    f"videos={len(record.opened_videos)} watch={record.watch_seconds}s",
                    flush=True,
                )
            if not made_progress:
                print("[topic-runner] top-up made no progress; stopping", flush=True)
                break
    finally:
        try:
            await appium.close_session(driver_handle)
        except Exception:
            try:
                driver.quit()
            except Exception:
                pass
        try:
            clear_android_global_http_proxy(serial)
        except Exception as exc:
            print(
                f"[topic-runner] android global http_proxy:clear_error "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
        try:
            print(f"[topic-runner] device shutdown:start serial={serial}", flush=True)
            await avd_manager.stop_device(serial, avd_name=avd_name)
            print(f"[topic-runner] device shutdown:done serial={serial}", flush=True)
        except Exception as exc:
            print(
                f"[topic-runner] device shutdown:error {type(exc).__name__}: {exc}",
                flush=True,
            )
        if proxy_bridge is not None and proxy_bridge_handle is not None:
            try:
                await proxy_bridge.stop(proxy_bridge_handle)
            except Exception as exc:
                print(
                    f"[topic-runner] proxy bridge stop:error {type(exc).__name__}: {exc}",
                    flush=True,
                )

    finished_at = utc_now_iso()
    _write_result_json(
        run_dir=run_dir,
        started_at=started_at,
        finished_at=finished_at,
        avd_name=avd_name,
        topic_records=topic_records,
    )
    print(f"[topic-runner] done. results: {run_dir / 'result.json'}", flush=True)
    result = StandaloneRunResult(
        started_at=started_at,
        finished_at=finished_at,
        avd=avd_name,
        run_dir=run_dir,
        topics=topic_records,
    )
    await emit_progress(
        options.on_progress,
        event="session_finished",
        run_dir=run_dir,
        topics=topic_records,
        payload={"avd": avd_name},
    )
    return result


async def main_async(args: argparse.Namespace) -> int:
    await run_standalone_session(
        StandaloneRunOptions(
            topics=args.topic,
            max_watch_seconds=args.max_watch_seconds,
            scroll_rounds=args.scroll_rounds,
            ad_record_seconds=args.ad_record_seconds,
            avd_name=args.avd,
            manage_appium=args.manage_appium,
        )
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--avd",
        default=None,
        help="AVD name (defaults to AndroidAppConfig.default_avd_name).",
    )
    parser.add_argument(
        "--topic",
        action="append",
        required=True,
        help="Search query. Repeatable.",
    )
    parser.add_argument("--scroll-rounds", type=int, default=30)
    parser.add_argument("--ad-record-seconds", type=float, default=30.0)
    parser.add_argument("--max-watch-seconds", type=float, default=900.0)
    parser.add_argument(
        "--manage-appium",
        action="store_true",
        help="Start a local Appium server (default: assume one is already running on 4723).",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
