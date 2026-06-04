from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .youtube import selectors


CHROME_PACKAGE = "com.android.chrome"

_FIRST_RUN_ACTIVITY_FRAGMENTS = (
    "firstrun",
    "first_run",
)

_FIRST_RUN_TEXTS = (
    "Accept & continue",
    "Accept and continue",
    "Continue as",
    "Use without an account",
    "No thanks",
    "Not now",
    "Skip",
    "Принять и продолжить",
    "Продолжить как",
    "Использовать без аккаунта",
    "Нет, спасибо",
    "Не сейчас",
    "Пропустить",
)

_ADDRESS_BAR_RESOURCE_IDS = (
    "com.android.chrome:id/url_bar",
    "com.android.chrome:id/search_box_text",
    "com.android.chrome:id/title_url_text",
    "com.android.chrome:id/location_bar_status_icon",
)


@dataclass(frozen=True)
class AndroidChromeWarmupResult:
    attempted: bool = False
    first_run_detected: bool = False
    ready: bool = False
    landing_url: str | None = None
    notes: list[str] = field(default_factory=list)


class AndroidChromeWarmup:
    def __init__(self, driver: object) -> None:
        self._driver = driver

    def prepare_after_external_open(self) -> AndroidChromeWarmupResult:
        notes: list[str] = []
        package = self._safe_driver_attr("current_package")
        activity = self._safe_driver_attr("current_activity")
        if package != CHROME_PACKAGE:
            return AndroidChromeWarmupResult(
                attempted=False,
                first_run_detected=False,
                ready=False,
                notes=["chrome_not_foreground"],
            )

        first_run_detected = self._is_first_run_activity(activity)
        if first_run_detected:
            notes.append("first_run_detected")
            self._dismiss_first_run_flow(notes)

        self._wait_for_settle()
        if self._dismiss_system_dialog():
            notes.append("chrome_system_dialog_waited")
            self._wait_for_settle()
        landing_url = self._extract_landing_url()
        ready = self._safe_driver_attr("current_package") == CHROME_PACKAGE and not self._is_first_run_activity(
            self._safe_driver_attr("current_activity")
        )
        if ready:
            notes.append("chrome_ready")
        if landing_url:
            notes.append(f"landing_url:{landing_url}")
        return AndroidChromeWarmupResult(
            attempted=True,
            first_run_detected=first_run_detected,
            ready=ready,
            landing_url=landing_url,
            notes=notes,
        )

    def _dismiss_first_run_flow(self, notes: list[str]) -> None:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            activity = self._safe_driver_attr("current_activity")
            if not self._is_first_run_activity(activity):
                notes.append("first_run_cleared")
                return
            clicked_any = False
            for text in _FIRST_RUN_TEXTS:
                if self._click_by_text_contains(text):
                    notes.append(f"clicked:{text}")
                    clicked_any = True
                    self._wait_for_settle()
                    break
            if not clicked_any:
                time.sleep(1.0)

    def _extract_landing_url(self) -> str | None:
        page_source = self._safe_page_source()
        if not page_source:
            return None
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return None

        for node in root.iter():
            resource_id = node.attrib.get("resource-id") or ""
            if resource_id not in _ADDRESS_BAR_RESOURCE_IDS:
                continue
            for raw in (node.attrib.get("text"), node.attrib.get("content-desc")):
                value = (raw or "").strip()
                if not value:
                    continue
                normalized = self._normalize_url_like(value)
                if normalized:
                    return normalized
        return None

    @staticmethod
    def _normalize_url_like(value: str) -> str | None:
        stripped = value.strip()
        if not stripped:
            return None
        lowered = stripped.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return stripped
        if "." in stripped and " " not in stripped:
            return stripped
        return None

    @staticmethod
    def _is_first_run_activity(activity: str | None) -> bool:
        normalized = (activity or "").lower()
        return any(fragment in normalized for fragment in _FIRST_RUN_ACTIVITY_FRAGMENTS)

    def _click_by_text_contains(self, text: str) -> bool:
        try:
            elements = self._driver.find_elements(
                "-android uiautomator",
                f'new UiSelector().textContains("{text}")',
            )
        except Exception:
            return False
        for element in elements:
            try:
                element.click()
                return True
            except Exception:
                continue
        return False

    def _wait_for_settle(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self._dismiss_system_dialog()
            time.sleep(0.25)

    def _dismiss_system_dialog(self) -> bool:
        page_source = self._safe_page_source()
        lowered = page_source.casefold()
        if not any(hint.casefold() in lowered for hint in selectors.SYSTEM_DIALOG_TITLE_HINTS):
            return False
        for candidate_id in selectors.SYSTEM_DIALOG_WAIT_IDS:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements[:1]:
                try:
                    element.click()
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue
        for text in selectors.SYSTEM_DIALOG_WAIT_TEXTS:
            if self._click_by_text_contains(text):
                time.sleep(1.0)
                return True
        return False

    def _safe_driver_attr(self, name: str) -> str | None:
        try:
            return getattr(self._driver, name)
        except Exception:
            return None

    def _safe_page_source(self) -> str:
        try:
            return self._driver.page_source or ""
        except Exception:
            return ""
