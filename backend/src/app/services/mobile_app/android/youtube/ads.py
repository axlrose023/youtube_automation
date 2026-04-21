from __future__ import annotations

import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.settings import AndroidAppConfig

from ..chrome import AndroidChromeWarmup, CHROME_PACKAGE
from ..errors import AndroidUiError, is_dead_appium_session_error
from ..tooling import build_android_runtime_env, require_tool_path
from . import selectors

_CUSTOM_TAB_CLOSE_TEXTS = (
    "Close",
    "Закрыть",
)
_PLAY_STORE_PACKAGE = "com.android.vending"
_FOCUS_RE = re.compile(r"\s(?:u\d+\s+)?(?P<package>[a-zA-Z0-9._]+)/(?P<activity>[a-zA-Z0-9.$_/]+)")
_CHROME_FIRST_RUN_ACTIVITY_FRAGMENTS = (
    "firstrun",
    "first_run",
)
_CHROME_FIRST_RUN_TEXTS = (
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
_CHROME_ADDRESS_BAR_RESOURCE_IDS = (
    "com.android.chrome:id/url_bar",
    "com.android.chrome:id/search_box_text",
    "com.android.chrome:id/title_url_text",
    "com.android.chrome:id/location_bar_status_icon",
)


@dataclass(frozen=True)
class AndroidAdCtaProbeResult:
    clicked: bool = False
    label: str | None = None
    destination_package: str | None = None
    destination_activity: str | None = None
    landing_url: str | None = None
    chrome_ready: bool = False
    chrome_first_run_detected: bool = False
    notes: list[str] | None = None
    returned_to_youtube: bool = False
    debug_screen_path: Path | None = None
    debug_page_source_path: Path | None = None
    pre_click_display_url: str | None = None
    pre_click_headline_text: str | None = None


class AndroidYouTubeAdInteractor:
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

    async def probe_cta(self, *, artifact_dir: Path, artifact_prefix: str) -> AndroidAdCtaProbeResult:
        with anyio.move_on_after(25) as scope:
            result = await anyio.to_thread.run_sync(
                self._probe_cta_sync,
                artifact_dir,
                artifact_prefix,
                abandon_on_cancel=True,
            )
        if scope.cancel_called:
            fallback_result = await anyio.to_thread.run_sync(
                self._build_timeout_probe_result_sync,
                artifact_dir,
                artifact_prefix,
                abandon_on_cancel=True,
            )
            if fallback_result is not None:
                return fallback_result
            raise AndroidUiError("Timed out probing native ad CTA")
        return result

    def _probe_cta_sync(self, artifact_dir: Path, artifact_prefix: str) -> AndroidAdCtaProbeResult:
        self._dismiss_system_dialog_sync()
        label = self._read_first_text_by_ids_sync(selectors.AD_CTA_TEXT_IDS)

        # --- pre-click snapshot: captures the ad with the CTA button visible ---
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self._write_debug_screenshot_via_adb_sync(
            artifact_dir / f"{artifact_prefix}_pre_click.png"
        )
        _pre_click_xml_path = artifact_dir / f"{artifact_prefix}_pre_click.xml"
        self._write_debug_hierarchy_file_via_adb_sync(_pre_click_xml_path)

        # Capture pre-tap identity from the just-saved XML so we can detect pod
        # transitions (next ad replacing current one between our tap and CCT URL read).
        # Reading from XML is more reliable than live Appium driver queries.
        pre_click_display_url = self._read_first_text_from_xml_by_ids(
            _pre_click_xml_path, selectors.AD_DISPLAY_URL_IDS
        )
        pre_click_headline_text = self._read_first_text_from_xml_by_ids(
            _pre_click_xml_path, selectors.AD_HEADLINE_IDS
        )

        clicked = self._tap_ad_cta_via_adb_sync(preferred_label=label)
        if not clicked:
            button = self._find_first_by_ids_sync(selectors.AD_CTA_BUTTON_IDS)
            if button is None:
                button = self._find_clickable_by_label_sync(
                    [label] if label else list(selectors.AD_CTA_DESCRIPTIONS)
                )
            if button is None:
                return AndroidAdCtaProbeResult(
                    clicked=False,
                    label=label,
                    pre_click_display_url=pre_click_display_url,
                    pre_click_headline_text=pre_click_headline_text,
                )

            try:
                button.click()
                clicked = True
            except Exception as exc:
                raise AndroidUiError(f"Failed to click native ad CTA: {exc}") from exc

        # Wait for destination app to become active.
        # We poll in short intervals and probe for Chrome Custom Tab URL simultaneously,
        # because CCT opens inside YouTube's process so current_package may stay as YouTube.
        # Both uiautomator dump and dumpsys activity run in parallel to minimise latency.
        import concurrent.futures as _cf
        destination_package = None
        destination_activity = None
        landing_url = None
        chrome_ready = False
        chrome_first_run_detected = False
        notes: list[str] = []
        _cct_url_found: str | None = None
        _dumpsys_raw_saved: str | None = None
        for _wait in (1.0, 1.0, 1.0, 1.0, 1.0, 1.0):
            time.sleep(_wait)
            destination_package = self._safe_driver_attr_sync("current_package")
            destination_activity = self._safe_driver_attr_sync("current_activity")
            if destination_package and destination_package != self._config.youtube_package:
                break
            # Package is still YouTube — check for Chrome Custom Tab in-process.
            # Run uiautomator dump and dumpsys activity in parallel.
            if self._adb_serial and _cct_url_found is None:
                with _cf.ThreadPoolExecutor(max_workers=2) as _pool:
                    _ui_fut = _pool.submit(self._extract_chrome_landing_url_via_adb_sync)
                    _ds_fut = _pool.submit(self._extract_url_via_dumpsys_activity_sync)
                    _ui_url = _ui_fut.result()
                    _ds_url, _ds_raw = _ds_fut.result()
                if _ds_raw:
                    _dumpsys_raw_saved = _ds_raw
                _cct_url_found = _ui_url or _ds_url
                if _cct_url_found:
                    if _ui_url:
                        notes.append("chrome_custom_tab_in_youtube")
                    else:
                        notes.append("cct_url_via_dumpsys_parallel")
                    landing_url = _cct_url_found
                    notes.append(f"landing_url:{_cct_url_found}")
                    self._press_back_sync()
                    time.sleep(1.0)
                    destination_package = self._safe_driver_attr_sync("current_package")
                    destination_activity = self._safe_driver_attr_sync("current_activity")
                    break

        if destination_package == self._config.youtube_package and _cct_url_found is None:
            # Last resort: re-query dumpsys in case the parallel attempt was too early.
            if self._adb_serial:
                _dumpsys_url, _dumpsys_raw = self._extract_url_via_dumpsys_activity_sync()
                if _dumpsys_raw:
                    _dumpsys_raw_saved = _dumpsys_raw
                if _dumpsys_url:
                    notes.append("cct_url_via_dumpsys")
                    landing_url = _dumpsys_url
                    notes.append(f"landing_url:{_dumpsys_url}")
                    _cct_url_found = _dumpsys_url
                    self._press_back_sync()
                    time.sleep(1.0)
                    destination_package = self._safe_driver_attr_sync("current_package")
                    destination_activity = self._safe_driver_attr_sync("current_activity")

                # Always save raw dumpsys activities output — needed to debug both
                # URL extraction failures and unexpected activity transitions.
                if _dumpsys_raw_saved and artifact_dir:
                    try:
                        _dumpsys_path = artifact_dir / f"{artifact_prefix}_dumpsys_activity.txt"
                        _dumpsys_path.write_text(_dumpsys_raw_saved, encoding="utf-8")
                        notes.append(f"dumpsys_activity_saved:{_dumpsys_path.name}")
                    except Exception:
                        pass

        # Always save dumpsys window windows — shows focused window and full activity stack.
        # Critical for diagnosing which activity opened after CTA click.
        if self._adb_serial and artifact_dir:
            self._write_dumpsys_windows_sync(artifact_dir / f"{artifact_prefix}_dumpsys_windows.txt")

        # Record current activity for debug visibility regardless of outcome.
        if destination_activity:
            notes.append(f"dest_activity:{destination_activity}")

        if destination_package == self._config.youtube_package and _cct_url_found is None:
            # CTA was clicked but no external app/CCT opened and no landing URL detected.
            # This means one of:
            #   (a) CTA navigated to a YouTube-internal page (channel, browse, search, reels)
            #   (b) CTA opened a different YouTube video (channel's featured video) —
            #       same WatchWhileActivity but a different video/page stack
            #   (c) CTA didn't actually trigger anything
            # Press Back up to 3 times to unwind any internal redirect. Stop early if we
            # leave YouTube (a) or if activity stops changing after a Back (likely back on
            # the original player already).
            _prev_activity = destination_activity
            for _back_attempt in range(3):
                notes.append(
                    f"cta_post_click_no_cct:pressing_back[{_back_attempt + 1}]:{destination_activity}"
                )
                self._press_back_sync()
                time.sleep(1.5)
                destination_package = self._safe_driver_attr_sync("current_package")
                destination_activity = self._safe_driver_attr_sync("current_activity")
                if destination_package != self._config.youtube_package:
                    break
                # If activity didn't change after a Back, we're likely back on the player —
                # don't press again (would exit to home feed or close the app).
                if _back_attempt > 0 and destination_activity == _prev_activity:
                    break
                _prev_activity = destination_activity

        if destination_package == self._config.youtube_package and _cct_url_found is None:
            # Still YouTube, no CCT detected — try confirmation dialog (e.g. "Open in browser")
            confirmation_label = self._click_youtube_confirmation_cta_sync()
            if confirmation_label:
                notes.append(f"youtube_confirmation_cta:{confirmation_label}")
                label = label or confirmation_label
                time.sleep(4)
                destination_package = self._safe_driver_attr_sync("current_package")
                destination_activity = self._safe_driver_attr_sync("current_activity")

        if destination_package == CHROME_PACKAGE:
            if self._adb_serial:
                landing_url, chrome_ready, chrome_first_run_detected, chrome_notes = (
                    self._prepare_after_external_open_via_adb_sync()
                )
                notes.extend(chrome_notes)
            else:
                chrome_warmup = AndroidChromeWarmup(self._driver)
                warmup_result = chrome_warmup.prepare_after_external_open()
                landing_url = warmup_result.landing_url
                chrome_ready = warmup_result.ready
                chrome_first_run_detected = warmup_result.first_run_detected
                notes.extend(warmup_result.notes)
            destination_package = self._safe_driver_attr_sync("current_package")
            destination_activity = self._safe_driver_attr_sync("current_activity")
        elif destination_package == _PLAY_STORE_PACKAGE:
            notes.append("play_store_surface")
            play_store_url = self._extract_play_store_url_sync()
            if play_store_url:
                landing_url = play_store_url
                notes.append(f"play_store_url:{play_store_url}")
        debug_screen_path, debug_page_source_path = self._write_debug_artifacts_sync(
            artifact_dir=artifact_dir,
            artifact_prefix=artifact_prefix,
        )
        # Always save logcat around CTA probes — captures activity transitions,
        # ChromeCustomTab events, and URL intents that are otherwise invisible.
        if self._adb_serial and artifact_dir:
            self._write_logcat_cta_snapshot_sync(
                artifact_dir / f"{artifact_prefix}_logcat.txt"
            )
        returned_to_youtube = self._return_to_youtube_sync()
        return AndroidAdCtaProbeResult(
            clicked=clicked,
            label=label,
            destination_package=destination_package,
            destination_activity=destination_activity,
            landing_url=landing_url,
            chrome_ready=chrome_ready,
            chrome_first_run_detected=chrome_first_run_detected,
            notes=notes,
            returned_to_youtube=returned_to_youtube,
            debug_screen_path=debug_screen_path,
            debug_page_source_path=debug_page_source_path,
            pre_click_display_url=pre_click_display_url,
            pre_click_headline_text=pre_click_headline_text,
        )

    def _click_youtube_confirmation_cta_sync(self) -> str | None:
        labels = [
            label
            for label in selectors.AD_CTA_DESCRIPTIONS
            if label.casefold() not in {"cancel", "отмена"}
        ]
        if self._adb_serial:
            for label in labels:
                if self._tap_matching_node_via_adb_sync(texts=(label,), content_descs=(label,)):
                    return label
        for label in labels:
            element = self._find_clickable_by_label_sync([label])
            if element is None:
                continue
            try:
                element.click()
                return label
            except Exception:
                continue
        return None

    def _return_to_youtube_sync(self) -> bool:
        for _ in range(3):
            self._dismiss_system_dialog_sync()
            current_package = self._safe_driver_attr_sync("current_package")
            if current_package == self._config.youtube_package:
                return True
            if current_package == _PLAY_STORE_PACKAGE:
                if self._press_back_sync():
                    time.sleep(1.0)
                    if self._safe_driver_attr_sync("current_package") == self._config.youtube_package:
                        return True
            if current_package == CHROME_PACKAGE:
                if self._is_custom_tab_sync() and self._close_custom_tab_sync():
                    time.sleep(1.0)
                    continue
                if self._press_back_sync():
                    time.sleep(1.0)
                    continue
            if self._activate_youtube_sync():
                return True
            time.sleep(1.0)
        if self._safe_driver_attr_sync("current_package") == self._config.youtube_package:
            return True
        return self._activate_youtube_sync()

    def _dismiss_system_dialog_sync(self) -> bool:
        if self._dismiss_system_dialog_via_adb_sync():
            return True
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
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
            try:
                elements = self._driver.find_elements(
                    "-android uiautomator",
                    f'new UiSelector().textContains("{text}")',
                )
            except Exception:
                elements = []
            for element in elements[:1]:
                try:
                    element.click()
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue
        return False

    def _dismiss_system_dialog_via_adb_sync(self) -> bool:
        if not self._adb_serial:
            return False
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return False
        if not any(hint.casefold() in hierarchy.casefold() for hint in selectors.SYSTEM_DIALOG_TITLE_HINTS):
            return False
        for text in selectors.SYSTEM_DIALOG_WAIT_TEXTS:
            center = self._find_text_center_via_adb_sync(
                hierarchy=hierarchy,
                needle=text,
            )
            if center is None:
                continue
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s",
                    self._adb_serial,
                    "shell",
                    "input",
                    "tap",
                    str(center[0]),
                    str(center[1]),
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                time.sleep(1.0)
                return True
        return False

    def _press_back_sync(self) -> bool:
        if self._adb_serial:
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
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True
        try:
            self._driver.back()
            return True
        except Exception:
            pass

        if not self._adb_serial:
            return False
        return False

    def _is_custom_tab_sync(self) -> bool:
        activity = (self._safe_driver_attr_sync("current_activity") or "").casefold()
        return "customtab" in activity

    def _close_custom_tab_sync(self) -> bool:
        if self._adb_serial and self._tap_matching_node_via_adb_sync(
            texts=_CUSTOM_TAB_CLOSE_TEXTS,
            content_descs=_CUSTOM_TAB_CLOSE_TEXTS,
        ):
            return True
        for text in _CUSTOM_TAB_CLOSE_TEXTS:
            try:
                elements = self._driver.find_elements(
                    "-android uiautomator",
                    f'new UiSelector().descriptionContains("{text}")',
                )
            except Exception:
                elements = []
            for element in elements:
                try:
                    element.click()
                    return True
                except Exception:
                    continue
        return False

    def _activate_youtube_sync(self) -> bool:
        if self._adb_serial:
            adb_bin = require_tool_path("adb")
            component = (
                f"{self._config.youtube_package}/{self._config.youtube_activity}"
            ).replace("$", "\\$")
            launch_result = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    self._adb_serial,
                    "shell",
                    "am",
                    "start",
                    "-W",
                    "-n",
                    component,
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=30,
            )
            if launch_result.returncode == 0:
                time.sleep(2.0)
                if self._safe_driver_attr_sync("current_package") == self._config.youtube_package:
                    return True
            result = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    self._adb_serial,
                    "shell",
                    "monkey",
                    "-p",
                    self._config.youtube_package,
                    "-c",
                    "android.intent.category.LAUNCHER",
                    "1",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                time.sleep(2.0)
                if self._safe_driver_attr_sync("current_package") == self._config.youtube_package:
                    return True
        try:
            self._driver.activate_app(self._config.youtube_package)
            time.sleep(2.0)
            return self._safe_driver_attr_sync("current_package") == self._config.youtube_package
        except Exception:
            return False

    def _find_first_by_ids_sync(self, candidate_ids: tuple[str, ...]) -> object | None:
        for candidate_id in candidate_ids:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                continue
            for element in elements:
                return element
        return None

    def _read_first_text_by_ids_sync(self, candidate_ids: tuple[str, ...]) -> str | None:
        for candidate_id in candidate_ids:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                continue
            for element in elements:
                try:
                    text = (getattr(element, "text", "") or "").strip()
                except Exception:
                    continue
                if text:
                    return text
        return None

    def _read_first_text_from_xml_by_ids(
        self, xml_path: Path, candidate_ids: tuple[str, ...]
    ) -> str | None:
        if xml_path is None or not xml_path.exists():
            return None
        try:
            root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        for candidate_id in candidate_ids:
            for node in root.iter():
                if (node.attrib.get("resource-id") or "") != candidate_id:
                    continue
                for attr in ("text", "content-desc"):
                    text = (node.attrib.get(attr) or "").strip()
                    if text:
                        return text
        return None

    def _find_clickable_by_label_sync(self, labels: list[str]) -> object | None:
        for label in labels:
            stripped = (label or "").strip()
            if not stripped:
                continue
            for selector in (
                f'new UiSelector().text("{stripped}")',
                f'new UiSelector().textContains("{stripped}")',
                f'new UiSelector().description("{stripped}")',
                f'new UiSelector().descriptionContains("{stripped}")',
            ):
                try:
                    elements = self._driver.find_elements("-android uiautomator", selector)
                except Exception:
                    continue
                for element in elements:
                    return element
        return None

    def _safe_driver_attr_sync(self, name: str) -> str | None:
        if self._adb_serial and name in {"current_package", "current_activity"}:
            package, activity = self._read_current_focus_via_adb_sync()
            if name == "current_package" and package:
                return package
            if name == "current_activity" and activity:
                return activity
        try:
            return getattr(self._driver, name)
        except Exception as exc:
            if is_dead_appium_session_error(exc):
                raise AndroidUiError(f"Appium session died while reading driver attribute {name}: {exc}") from exc
            return None

    def _read_current_focus_via_adb_sync(self) -> tuple[str | None, str | None]:
        if not self._adb_serial:
            return None, None
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s",
                    self._adb_serial,
                    "shell",
                    "dumpsys",
                    "window",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=10,
            )
        except Exception:
            return None, None
        if result.returncode != 0:
            return None, None
        for line in (result.stdout or "").splitlines():
            if "mCurrentFocus" not in line and "mFocusedApp" not in line:
                continue
            match = _FOCUS_RE.search(line)
            if match is None:
                continue
            return match.group("package"), match.group("activity")
        return None, None

    def _dump_ui_hierarchy_via_adb_sync(self) -> str | None:
        if not self._adb_serial:
            return None
        adb_bin = require_tool_path("adb")
        remote_path = "/sdcard/codex_probe_uidump.xml"
        try:
            dump = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    self._adb_serial,
                    "shell",
                    "uiautomator",
                    "dump",
                    remote_path,
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=8,
            )
            if dump.returncode != 0:
                return None
            pulled = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    self._adb_serial,
                    "shell",
                    "cat",
                    remote_path,
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=8,
            )
        except Exception:
            return None
        if pulled.returncode != 0:
            return None
        return (pulled.stdout or "").strip() or None

    @staticmethod
    def _find_text_center_via_adb_sync(
        *,
        hierarchy: str,
        needle: str,
    ) -> tuple[int, int] | None:
        pattern = re.compile(
            rf'text="{re.escape(needle)}".*?bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"'
        )
        match = pattern.search(hierarchy)
        if match is None:
            pattern = re.compile(
                rf'content-desc="{re.escape(needle)}".*?bounds="\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]"'
            )
            match = pattern.search(hierarchy)
        if match is None:
            return None
        left, top, right, bottom = (int(match.group(i)) for i in range(1, 5))
        return ((left + right) // 2, (top + bottom) // 2)

    def _write_debug_artifacts_sync(
        self,
        *,
        artifact_dir: Path,
        artifact_prefix: str,
    ) -> tuple[Path | None, Path | None]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        screen_path = artifact_dir / f"{artifact_prefix}_cta.png"
        xml_path = artifact_dir / f"{artifact_prefix}_cta.xml"

        written_screen = None
        written_xml = None
        if self._adb_serial:
            written_screen = self._write_debug_screenshot_via_adb_sync(screen_path)
            written_xml = self._write_debug_hierarchy_file_via_adb_sync(xml_path)
            if written_screen is not None or written_xml is not None:
                return written_screen, written_xml
        try:
            self._driver.save_screenshot(str(screen_path))
            written_screen = screen_path
        except Exception:
            written_screen = None
        try:
            page_source = self._driver.page_source
            xml_path.write_text(page_source or "", encoding="utf-8")
            written_xml = xml_path
        except Exception:
            written_xml = None
        return written_screen, written_xml

    def _write_debug_screenshot_via_adb_sync(self, screen_path: Path) -> Path | None:
        if not self._adb_serial:
            return None
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s",
                    self._adb_serial,
                    "exec-out",
                    "screencap",
                    "-p",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                timeout=10,
            )
        except Exception:
            return None
        if result.returncode != 0 or not result.stdout:
            return None
        try:
            screen_path.write_bytes(result.stdout)
        except Exception:
            return None
        return screen_path

    def _write_debug_hierarchy_file_via_adb_sync(self, xml_path: Path) -> Path | None:
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            # Fallback to Appium page_source when uiautomator dump is busy
            # (typical when UiAutomator2 driver holds the service).
            try:
                hierarchy = self._driver.page_source
            except Exception:
                hierarchy = None
        if not hierarchy:
            return None
        try:
            xml_path.write_text(hierarchy, encoding="utf-8")
        except Exception:
            return None
        return xml_path

    def _build_timeout_probe_result_sync(
        self,
        artifact_dir: Path,
        artifact_prefix: str,
    ) -> AndroidAdCtaProbeResult | None:
        destination_package = self._safe_driver_attr_sync("current_package")
        destination_activity = self._safe_driver_attr_sync("current_activity")
        landing_url = None
        chrome_ready = False
        chrome_first_run_detected = False
        notes = ["probe_timeout_fallback"]
        if destination_package == CHROME_PACKAGE and self._adb_serial:
            landing_url, chrome_ready, chrome_first_run_detected, chrome_notes = (
                self._prepare_after_external_open_via_adb_sync()
            )
            notes.extend(chrome_notes)
            destination_package = self._safe_driver_attr_sync("current_package")
            destination_activity = self._safe_driver_attr_sync("current_activity")
        debug_screen_path, debug_page_source_path = self._write_debug_artifacts_sync(
            artifact_dir=artifact_dir,
            artifact_prefix=artifact_prefix,
        )
        returned_to_youtube = False
        if destination_package != self._config.youtube_package or landing_url:
            returned_to_youtube = self._return_to_youtube_sync()
        clicked = bool(
            landing_url
            or destination_package in {CHROME_PACKAGE, _PLAY_STORE_PACKAGE}
            or debug_screen_path
            or debug_page_source_path
        )
        return AndroidAdCtaProbeResult(
            clicked=clicked,
            label=None,
            destination_package=destination_package,
            destination_activity=destination_activity,
            landing_url=landing_url,
            chrome_ready=chrome_ready,
            chrome_first_run_detected=chrome_first_run_detected,
            notes=notes,
            returned_to_youtube=returned_to_youtube,
            debug_screen_path=debug_screen_path,
            debug_page_source_path=debug_page_source_path,
        )

    def _tap_ad_cta_via_adb_sync(self, *, preferred_label: str | None) -> bool:
        labels = tuple(
            stripped
            for stripped in (
                *((preferred_label or "").strip(),),
                *selectors.AD_CTA_DESCRIPTIONS,
            )
            if stripped
        )
        return self._tap_matching_node_via_adb_sync(
            resource_ids=(*selectors.AD_CTA_BUTTON_IDS, *selectors.AD_CTA_TEXT_IDS),
            texts=labels,
            content_descs=labels,
        )

    def _prepare_after_external_open_via_adb_sync(
        self,
    ) -> tuple[str | None, bool, bool, list[str]]:
        notes: list[str] = []
        _, activity = self._read_current_focus_via_adb_sync()
        chrome_first_run_detected = self._is_chrome_first_run_activity_sync(activity)
        if chrome_first_run_detected:
            notes.append("first_run_detected")
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                _, current_activity = self._read_current_focus_via_adb_sync()
                if not self._is_chrome_first_run_activity_sync(current_activity):
                    notes.append("first_run_cleared")
                    break
                if not self._tap_matching_node_via_adb_sync(
                    texts=_CHROME_FIRST_RUN_TEXTS,
                    content_descs=_CHROME_FIRST_RUN_TEXTS,
                ):
                    time.sleep(1.0)
                    continue
                time.sleep(1.0)
        # Retry loop: Chrome Custom Tab may take a few seconds to populate the URL bar.
        # Two strategies in parallel each tick:
        #  1. uiautomator dump → url_bar resource-id (fast but blocked when CCT is focused)
        #  2. dumpsys activity activities → dat=https://... intent field (works even when
        #     uiautomator is blocked, because the intent is recorded in the activity stack)
        landing_url = None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            time.sleep(1.0)
            landing_url = self._extract_chrome_landing_url_via_adb_sync()
            if landing_url:
                notes.append("url_via_uiautomator")
                break
            # Fallback: read from activity intent stack
            dumpsys_url, _ = self._extract_url_via_dumpsys_activity_sync()
            if dumpsys_url:
                landing_url = dumpsys_url
                notes.append("url_via_dumpsys_fallback")
                break
        destination_package, destination_activity = self._read_current_focus_via_adb_sync()
        chrome_ready = (
            destination_package == CHROME_PACKAGE
            and not self._is_chrome_first_run_activity_sync(destination_activity)
        )
        if chrome_ready:
            notes.append("chrome_ready")
        if landing_url:
            notes.append(f"landing_url:{landing_url}")
        return landing_url, chrome_ready, chrome_first_run_detected, notes

    def _extract_chrome_landing_url_via_adb_sync(self) -> str | None:
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return None
        try:
            root = ET.fromstring(hierarchy)
        except ET.ParseError:
            return None
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if resource_id not in _CHROME_ADDRESS_BAR_RESOURCE_IDS:
                continue
            for raw in (node.attrib.get("text"), node.attrib.get("content-desc")):
                normalized = self._normalize_url_like((raw or "").strip())
                if normalized:
                    return normalized
        return None

    def _extract_url_via_dumpsys_activity_sync(
        self,
        *,
        require_package_context: bool = True,
    ) -> tuple[str | None, str | None]:
        """Read the URL of the topmost Chrome Custom Tab from the activity intent stack.

        When a CCT opens (either inside YouTube's process or as a separate Chrome
        activity), Android records the intent with dat=https://... in the activity
        stack. This is readable via dumpsys even when uiautomator dump is blocked.

        Args:
            require_package_context: When True (default), only returns a URL if
                YouTube or Chrome package is mentioned near the dat= line.
                Pass False when Chrome is already confirmed as the foreground package
                — in that case any dat=https:// in the top task is accepted.

        Returns (url_or_None, raw_output_or_None).
        """
        if not self._adb_serial:
            return None, None
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s", self._adb_serial,
                    "shell", "dumpsys", "activity", "activities",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=10,
            )
        except Exception:
            return None, None
        if result.returncode != 0:
            return None, None

        raw_output = result.stdout or ""

        # Scan the output for the most recent http(s) VIEW intent.
        # The package appears on the affinity/componentName line, not on the TaskRecord line,
        # so we track relevance by scanning for our packages anywhere near the intent block.
        _url_re = re.compile(r'\bdat=(https?://[^\s,}\]]+)', re.IGNORECASE)
        _relevant_packages = (self._config.youtube_package, CHROME_PACKAGE)

        lines = raw_output.splitlines()
        for i, line in enumerate(lines):
            match = _url_re.search(line)
            if not match:
                continue
            candidate = match.group(1).rstrip("},;]\"'")
            normalized = self._normalize_url_like(candidate)
            if not normalized:
                continue
            if not require_package_context:
                # Chrome is confirmed foreground — take the first valid dat= URL
                return normalized, raw_output
            # Check surrounding context (15 lines before, 5 after) for our packages
            context_start = max(0, i - 15)
            context_end = min(len(lines), i + 5)
            context = "\n".join(lines[context_start:context_end])
            if any(pkg in context for pkg in _relevant_packages):
                return normalized, raw_output

        return None, raw_output

    def _write_logcat_cta_snapshot_sync(self, output_path: Path) -> None:
        """Save the last ~300 logcat lines filtered for navigation/CTA-relevant tags.

        Captures ChromeCustomTab activity launches, YouTube intent handling,
        and ActivityManager transitions — everything needed to trace why a CTA
        click didn't produce a URL or opened an unexpected screen.
        """
        if not self._adb_serial:
            return
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s", self._adb_serial,
                    "logcat", "-d", "-t", "300",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=20,
            )
        except Exception:
            return
        if result.returncode != 0:
            return
        raw_lines = (result.stdout or "").splitlines()
        _keep_re = re.compile(
            r'(ActivityManager|WindowManager|ActivityTaskManager'
            r'|CustomTab|ChromeTab|IntentActivity|BrowsableActivity'
            r'|chromium|com\.android\.chrome'
            r'|com\.google\.android\.youtube'
            r'|IntentResolution|StartActivity'
            r'|ANR|FATAL|AndroidRuntime'
            r'|dat=http|VIEW.*http|http.*dat='
            r'|Appium|UiAutomator)',
            re.IGNORECASE,
        )
        filtered = [line for line in raw_lines if _keep_re.search(line)]
        content = "\n".join(filtered[-300:] or raw_lines[-300:])
        try:
            output_path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _write_dumpsys_windows_sync(self, output_path: Path) -> None:
        """Save `dumpsys window windows` to a file.

        This output shows the focused window, the full activity stack, and any
        overlay windows (e.g. Chrome Custom Tab overlay). It's the most reliable
        way to identify which activity is actually on screen after a CTA click.
        """
        if not self._adb_serial:
            return
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s", self._adb_serial,
                    "shell", "dumpsys", "window", "windows",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=10,
            )
        except Exception:
            return
        if result.returncode != 0:
            return
        raw = result.stdout or ""
        # Keep only the most informative lines — focus/activity/package/url mentions.
        # Saves disk space while retaining everything needed for debugging.
        _keep_re = re.compile(
            r'(mCurrentFocus|mFocusedApp|Window #|package=|ActivityRecord|mOwnerUid'
            r'|windowType|url|dat=|component=|mTaskId|mActivityType'
            r'|com\.google|com\.android\.chrome|CustomTab)',
            re.IGNORECASE,
        )
        filtered = [line for line in raw.splitlines() if _keep_re.search(line)]
        try:
            output_path.write_text("\n".join(filtered), encoding="utf-8")
        except Exception:
            pass

    def _extract_play_store_url_sync(self) -> str | None:
        """Extract Play Store app URL from the current Play Store page.

        Tries to get the exact package ID via the activity intent URI,
        falling back to extracting the app name from the UI hierarchy.
        """
        if not self._adb_serial:
            return None
        # Primary: get current activity intent which contains the package id
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s", self._adb_serial,
                    "shell", "dumpsys", "activity", "activities",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=10,
            )
            for line in (result.stdout or "").splitlines():
                # e.g. "intent={act=android.intent.action.VIEW dat=market://details?id=com.tradematix...}"
                match = re.search(r"id=([a-z][a-z0-9_]*(?:\.[a-z0-9_]+){1,})", line, re.IGNORECASE)
                if match:
                    pkg_id = match.group(1)
                    return f"https://play.google.com/store/apps/details?id={pkg_id}"
        except Exception:
            pass
        # Fallback: extract app name from UI hierarchy title node
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return None
        try:
            root = ET.fromstring(hierarchy)
        except ET.ParseError:
            return None
        _skip_texts = {
            "install", "open", "uninstall", "update", "play store", "search",
            "back", "more options", "share", "wishlist", "games", "apps",
        }
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if not any(hint in resource_id for hint in ("title", "app_title", "header", "label", "name")):
                continue
            if node.attrib.get("package") != _PLAY_STORE_PACKAGE:
                continue
            text = (node.attrib.get("text") or "").strip()
            if text and len(text) > 2 and text.lower() not in _skip_texts and "\n" not in text:
                return f"https://play.google.com/store/search?q={text.replace(' ', '+')}&c=apps"
        return None

    @staticmethod
    def _is_chrome_first_run_activity_sync(activity: str | None) -> bool:
        normalized = (activity or "").casefold()
        return any(fragment in normalized for fragment in _CHROME_FIRST_RUN_ACTIVITY_FRAGMENTS)

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

    def _tap_matching_node_via_adb_sync(
        self,
        *,
        resource_ids: tuple[str, ...] = (),
        texts: tuple[str, ...] = (),
        content_descs: tuple[str, ...] = (),
    ) -> bool:
        if not self._adb_serial:
            return False
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return False
        center = self._find_first_matching_node_center_via_adb_sync(
            hierarchy=hierarchy,
            resource_ids=resource_ids,
            texts=texts,
            content_descs=content_descs,
        )
        if center is None:
            return False
        result = subprocess.run(
            [
                require_tool_path("adb"),
                "-s",
                self._adb_serial,
                "shell",
                "input",
                "tap",
                str(center[0]),
                str(center[1]),
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    @classmethod
    def _find_first_matching_node_center_via_adb_sync(
        cls,
        *,
        hierarchy: str,
        resource_ids: tuple[str, ...] = (),
        texts: tuple[str, ...] = (),
        content_descs: tuple[str, ...] = (),
    ) -> tuple[int, int] | None:
        try:
            root = ET.fromstring(hierarchy)
        except ET.ParseError:
            return None
        lowered_resource_ids = {value.strip() for value in resource_ids if value.strip()}
        lowered_texts = {value.casefold().strip() for value in texts if value.strip()}
        lowered_content_descs = {
            value.casefold().strip() for value in content_descs if value.strip()
        }
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            content_desc = (node.attrib.get("content-desc") or "").strip()
            if lowered_resource_ids and resource_id in lowered_resource_ids:
                bounds = cls._parse_bounds_sync(node.attrib.get("bounds"))
                if bounds is not None:
                    left, top, right, bottom = bounds
                    return ((left + right) // 2, (top + bottom) // 2)
            if lowered_texts and text.casefold() in lowered_texts:
                bounds = cls._parse_bounds_sync(node.attrib.get("bounds"))
                if bounds is not None:
                    left, top, right, bottom = bounds
                    return ((left + right) // 2, (top + bottom) // 2)
            if lowered_content_descs and content_desc.casefold() in lowered_content_descs:
                bounds = cls._parse_bounds_sync(node.attrib.get("bounds"))
                if bounds is not None:
                    left, top, right, bottom = bounds
                    return ((left + right) // 2, (top + bottom) // 2)
        return None

    @staticmethod
    def _parse_bounds_sync(raw_bounds: str | None) -> tuple[int, int, int, int] | None:
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw_bounds or "")
        if match is None:
            return None
        left, top, right, bottom = (int(match.group(index)) for index in range(1, 5))
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom
