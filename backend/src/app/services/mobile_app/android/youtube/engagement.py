from __future__ import annotations

import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import anyio

from app.settings import AndroidAppConfig

from ..errors import AndroidUiError
from ..tooling import build_android_runtime_env, require_tool_path
from . import selectors

_TOKEN_RE = re.compile(r"[0-9a-zа-яіїєґ]+", re.IGNORECASE)
_STOP_TOKENS = {
    "how", "what", "with", "from", "this", "that", "guide",
    "video", "beginner", "beginners", "minutes", "minute",
    "для", "как", "что", "это", "эти", "guide", "2026",
}


@dataclass(frozen=True)
class AndroidEngagementResult:
    liked: bool = False
    already_liked: bool = False
    subscribed: bool = False
    already_subscribed: bool = False
    comments_glanced: bool = False
    notes: list[str] = field(default_factory=list)


class AndroidYouTubeEngagementController:
    _COMMENT_ATTEMPTS = 3
    _BOTTOM_GESTURE_GUARD_PX = 220
    _TOP_GUARD_RATIO = 0.38
    _BOTTOM_GUARD_RATIO = 0.84
    _COMMENT_BUDGET_SECONDS = 4.5

    def __init__(
        self,
        driver: object,
        config: AndroidAppConfig,
        *,
        adb_serial: str | None = None,
    ) -> None:
        self._driver = driver
        self._config = config
        self._adb_serial = adb_serial

    async def engage(self, *, topic: str, opened_title: str | None) -> AndroidEngagementResult:
        return await anyio.to_thread.run_sync(self._engage_sync, topic, opened_title)

    def _engage_sync(self, topic: str, opened_title: str | None) -> AndroidEngagementResult:
        notes: list[str] = []
        liked = False
        already_liked = False
        subscribed = False
        already_subscribed = False

        if not self._is_stable_watch_surface():
            notes.append("engagement_skipped:not_stable_watch_surface")
            return AndroidEngagementResult(notes=notes)

        short_form_surface = self._is_reel_watch_surface()
        if short_form_surface:
            notes.append("engagement_surface:short_form")

        relevance_score = self._title_topic_overlap(topic, opened_title)
        notes.append(f"relevance_score:{relevance_score}")

        like_element, like_desc = self._find_like_element()
        if like_desc:
            notes.append(f"like_desc:{like_desc}")
        if like_element is not None and like_desc:
            normalized = like_desc.casefold()
            if "unlike" in normalized or "убрать" in normalized or "liked" in normalized:
                already_liked = True
            elif relevance_score >= 1 and self._safe_click(like_element):
                liked = True
                time.sleep(0.5)

        subscribe_element, subscribe_desc = self._find_subscribe_element()
        if subscribe_desc:
            notes.append(f"subscribe_desc:{subscribe_desc}")
        if subscribe_element is not None and subscribe_desc:
            normalized = subscribe_desc.casefold()
            if "subscribed to" in normalized or "unsubscribe from" in normalized or "вы подписаны" in normalized:
                already_subscribed = True
            elif relevance_score >= 2 and self._safe_click(subscribe_element):
                subscribed = True
                time.sleep(0.5)

        if short_form_surface:
            notes.append("comments_skipped:short_form")
            return AndroidEngagementResult(
                liked=liked,
                already_liked=already_liked,
                subscribed=subscribed,
                already_subscribed=already_subscribed,
                comments_glanced=False,
                notes=notes,
            )

        comments_glanced = self._comments_glance(notes)

        return AndroidEngagementResult(
            liked=liked,
            already_liked=already_liked,
            subscribed=subscribed,
            already_subscribed=already_subscribed,
            comments_glanced=comments_glanced,
            notes=notes,
        )

    def _find_like_element(self) -> tuple[object | None, str | None]:
        return self._find_by_content_desc_fragments(selectors.LIKE_DESCRIPTION_HINTS)

    def _find_subscribe_element(self) -> tuple[object | None, str | None]:
        return self._find_by_content_desc_fragments(selectors.SUBSCRIBE_DESCRIPTION_HINTS)

    def _find_by_content_desc_fragments(self, fragments: tuple[str, ...]) -> tuple[object | None, str | None]:
        page_source = self._safe_page_source()
        if not page_source:
            return None, None
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return None, None

        for node in root.iter():
            desc = (node.attrib.get("content-desc") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            probe_text = desc or text
            if not probe_text:
                continue
            lowered = probe_text.casefold()
            if not any(fragment.casefold() in lowered for fragment in fragments):
                continue
            selector = self._node_to_uiselector(node)
            if selector is None:
                continue
            try:
                elements = self._driver.find_elements("-android uiautomator", selector)
            except Exception:
                continue
            if elements:
                return elements[0], probe_text
        return None, None

    def _comments_glance(self, notes: list[str]) -> bool:
        deadline = time.monotonic() + self._COMMENT_BUDGET_SECONDS
        if self._open_comments_panel(notes, deadline=deadline):
            return True
        for attempt in range(self._COMMENT_ATTEMPTS):
            if time.monotonic() >= deadline:
                notes.append("comments_budget_exhausted")
                return False
            if self._page_has_comment_hints():
                notes.append(f"comments_visible:{attempt}")
                self._swipe_watch_feed_up()
                time.sleep(0.6)
                return True
            self._swipe_watch_feed_up()
            time.sleep(0.5)
        visible = self._page_has_comment_hints()
        if visible:
            notes.append("comments_visible:final")
            self._swipe_watch_feed_up()
            time.sleep(0.6)
        return visible

    def _open_comments_panel(self, notes: list[str], *, deadline: float) -> bool:
        for attempt in range(self._COMMENT_ATTEMPTS):
            if time.monotonic() >= deadline:
                notes.append("comments_open_budget_exhausted")
                return False
            if self._tap_comment_card_by_hints():
                notes.append(f"comments_card_tapped:{attempt}")
                time.sleep(0.6)
                self._swipe_watch_feed_up()
                time.sleep(0.5)
                self._swipe_watch_feed_up()
                time.sleep(0.6)
                self._press_back_sync()
                time.sleep(0.5)
                return True
            if self._tap_comment_card_by_layout():
                notes.append(f"comments_layout_tapped:{attempt}")
                time.sleep(0.6)
                self._swipe_watch_feed_up()
                time.sleep(0.5)
                self._swipe_watch_feed_up()
                time.sleep(0.6)
                self._press_back_sync()
                time.sleep(0.5)
                return True
            if self._click_first_text_contains(selectors.COMMENT_TEXT_HINTS):
                notes.append(f"comments_opened:{attempt}")
                time.sleep(0.6)
                self._swipe_watch_feed_up()
                time.sleep(0.5)
                self._swipe_watch_feed_up()
                time.sleep(0.6)
                self._press_back_sync()
                time.sleep(0.5)
                return True
            self._swipe_watch_feed_up()
            time.sleep(0.5)
        return False

    def _tap_comment_card_by_hints(self) -> bool:
        if not self._adb_serial:
            return False
        page_source = self._safe_page_source()
        if not page_source:
            return False
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return False
        try:
            size = self._driver.get_window_size()
            screen_width = int(size["width"])
            screen_height = int(size["height"])
        except Exception:
            screen_width = 1080
            screen_height = 2400

        for node in root.iter():
            values = (
                (node.attrib.get("text") or "").strip(),
                (node.attrib.get("content-desc") or "").strip(),
            )
            if not any(
                any(hint.casefold() in value.casefold() for hint in selectors.COMMENT_TEXT_HINTS)
                for value in values
                if value
            ):
                continue
            bounds = self._parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            expanded = (
                max(24, left - 48),
                max(int(screen_height * 0.40), top - 36),
                min(screen_width - 24, max(right + 80, screen_width - 24)),
                min(int(screen_height * 0.90), bottom + 180),
            )
            if self._tap_bounds_via_adb(expanded):
                return True
        return False

    def _tap_comment_card_by_layout(self) -> bool:
        bounds = self._extract_watch_list_bounds()
        if bounds is None:
            return False
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return False
        tap_bounds = (
            int(left + width * 0.08),
            int(top + height * 0.24),
            int(right - width * 0.08),
            int(min(bottom - 220, top + height * 0.46)),
        )
        return self._tap_bounds_via_adb(tap_bounds)

    def _page_has_comment_hints(self) -> bool:
        page_source = self._safe_page_source()
        if not page_source:
            return False
        lowered = page_source.casefold()
        return any(hint.casefold() in lowered for hint in selectors.COMMENT_TEXT_HINTS)

    def _swipe_watch_feed_up(self) -> None:
        if self._swipe_watch_feed_mobile():
            return
        self._swipe_watch_feed_adb()

    def _swipe_watch_feed_mobile(self) -> bool:
        bounds = self._extract_watch_list_bounds()
        if bounds is None:
            return False
        left, top, right, bottom = bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        try:
            self._driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "direction": "up",
                    "percent": 0.58,
                },
            )
            return True
        except Exception:
            return False

    def _swipe_watch_feed_adb(self) -> None:
        if not self._adb_serial:
            return
        bounds = self._extract_watch_list_bounds()
        if bounds is None:
            size = self._driver.get_window_size()
            left, top, right, bottom = 0, int(size["height"] * 0.35), int(size["width"]), int(size["height"] * 0.92)
        else:
            left, top, right, bottom = bounds
        x = int((left + right) / 2)
        start_y = int(bottom - max(80, (bottom - top) * 0.15))
        end_y = int(top + max(120, (bottom - top) * 0.25))
        adb_bin = require_tool_path("adb")
        subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "input",
                "swipe",
                str(x),
                str(start_y),
                str(x),
                str(end_y),
                "350",
            ],
            check=False,
            capture_output=True,
            env=build_android_runtime_env(),
            timeout=30,
        )

    def _press_back_sync(self) -> bool:
        try:
            self._driver.back()
            return True
        except Exception:
            pass

        if not self._adb_serial:
            return False
        adb_bin = require_tool_path("adb")
        result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "input",
                "keyevent",
                "4",
            ],
            check=False,
            capture_output=True,
            env=build_android_runtime_env(),
            timeout=30,
        )
        return result.returncode == 0

    def _extract_watch_list_bounds(self) -> tuple[int, int, int, int] | None:
        page_source = self._safe_page_source()
        if not page_source:
            return None
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return None
        for node in root.iter():
            if (node.attrib.get("resource-id") or "") not in selectors.WATCH_LIST_IDS:
                continue
            parsed = self._parse_bounds(node.attrib.get("bounds"))
            if parsed is not None:
                return self._normalize_swipe_bounds(parsed)
        return None

    def _normalize_swipe_bounds(self, bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
        left, top, right, bottom = bounds
        try:
            size = self._driver.get_window_size()
            screen_height = int(size["height"])
        except Exception:
            screen_height = max(bottom, 1)
        safe_top = max(top, int(screen_height * self._TOP_GUARD_RATIO))
        safe_bottom = min(
            bottom,
            int(screen_height * self._BOTTOM_GUARD_RATIO),
            screen_height - self._BOTTOM_GESTURE_GUARD_PX,
        )
        if safe_bottom - safe_top < 220:
            return None
        return left, safe_top, right, safe_bottom

    def _is_stable_watch_surface(self) -> bool:
        try:
            current_package = getattr(self._driver, "current_package", None)
        except Exception:
            current_package = None
        if current_package != self._config.youtube_package:
            return False
        page_source = self._safe_page_source()
        lowered = page_source.casefold()
        has_watch_ids = any(
            watch_id in page_source
            for watch_id in (
                *selectors.WATCH_PANEL_IDS,
                *selectors.REEL_WATCH_PANEL_IDS,
                *selectors.REEL_WATCH_PLAYER_IDS,
            )
        )
        return has_watch_ids and "search youtube" not in lowered and "all apps" not in lowered

    def _is_reel_watch_surface(self) -> bool:
        page_source = self._safe_page_source()
        if not page_source:
            return False
        return any(
            watch_id in page_source
            for watch_id in (
                *selectors.REEL_WATCH_PANEL_IDS,
                *selectors.REEL_WATCH_PLAYER_IDS,
            )
        )

    @staticmethod
    def _parse_bounds(bounds: str | None) -> tuple[int, int, int, int] | None:
        if not bounds:
            return None
        numbers = [int(value) for value in re.findall(r"\d+", bounds)]
        if len(numbers) != 4:
            return None
        return numbers[0], numbers[1], numbers[2], numbers[3]

    @staticmethod
    def _node_to_uiselector(node: ET.Element) -> str | None:
        resource_id = (node.attrib.get("resource-id") or "").strip()
        content_desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        if resource_id:
            return f'new UiSelector().resourceId("{resource_id}")'
        if content_desc:
            escaped = content_desc.replace('"', '\\"')
            return f'new UiSelector().description("{escaped}")'
        if text:
            escaped = text.replace('"', '\\"')
            return f'new UiSelector().text("{escaped}")'
        return None

    def _click_first_text_contains(self, fragments: tuple[str, ...]) -> bool:
        seen: set[str] = set()
        for fragment in fragments:
            stripped = fragment.strip()
            if not stripped or stripped.casefold() in seen:
                continue
            seen.add(stripped.casefold())
            for selector in (
                f'new UiSelector().textContains("{stripped}")',
                f'new UiSelector().descriptionContains("{stripped}")',
            ):
                try:
                    elements = self._driver.find_elements("-android uiautomator", selector)
                except Exception:
                    continue
                for element in elements:
                    if self._safe_click(element):
                        return True
            if self._tap_first_bounds_for_fragment(stripped):
                return True
        return False

    def _safe_click(self, element: object) -> bool:
        try:
            element.click()
            return True
        except Exception:
            pass
        bounds = self._extract_element_bounds(element)
        if bounds is not None and self._tap_bounds_via_adb(bounds):
            return True
        return False

    def _tap_first_bounds_for_fragment(self, fragment: str) -> bool:
        page_source = self._safe_page_source()
        if not page_source:
            return False
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return False

        lowered_fragment = fragment.casefold()
        for node in root.iter():
            values = (
                (node.attrib.get("text") or "").strip(),
                (node.attrib.get("content-desc") or "").strip(),
            )
            if not any(lowered_fragment in value.casefold() for value in values if value):
                continue
            bounds = self._parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if self._tap_bounds_via_adb(bounds):
                return True
        return False

    def _tap_bounds_via_adb(self, bounds: tuple[int, int, int, int]) -> bool:
        if not self._adb_serial:
            return False
        left, top, right, bottom = bounds
        x = int((left + right) / 2)
        y = int((top + bottom) / 2)
        adb_bin = require_tool_path("adb")
        result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "input",
                "tap",
                str(x),
                str(y),
            ],
            check=False,
            capture_output=True,
            env=build_android_runtime_env(),
            timeout=30,
        )
        if result.returncode == 0:
            time.sleep(0.8)
            return True
        return False

    @classmethod
    def _extract_element_bounds(cls, element: object) -> tuple[int, int, int, int] | None:
        try:
            raw_value = element.get_attribute("bounds")  # type: ignore[attr-defined]
        except Exception:
            raw_value = None
        parsed = cls._parse_bounds(raw_value)
        if parsed is not None:
            return parsed
        try:
            rect = element.rect  # type: ignore[attr-defined]
        except Exception:
            rect = None
        if isinstance(rect, dict):
            x = int(rect.get("x", 0))
            y = int(rect.get("y", 0))
            width = int(rect.get("width", 0))
            height = int(rect.get("height", 0))
            if width > 0 and height > 0:
                return x, y, x + width, y + height
        return None

    def _safe_page_source(self) -> str:
        try:
            return self._driver.page_source or ""
        except Exception as exc:
            raise AndroidUiError(f"Failed to read current page source: {exc}") from exc

    @staticmethod
    def _title_topic_overlap(topic: str, opened_title: str | None) -> int:
        if not topic or not opened_title:
            return 0
        topic_tokens = {
            token.casefold()
            for token in _TOKEN_RE.findall(topic)
            if len(token) >= 4 and token.casefold() not in _STOP_TOKENS
        }
        title_tokens = {
            token.casefold()
            for token in _TOKEN_RE.findall(opened_title)
            if len(token) >= 4 and token.casefold() not in _STOP_TOKENS
        }
        return len(topic_tokens & title_tokens)
