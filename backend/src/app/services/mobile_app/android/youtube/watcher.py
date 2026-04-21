from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import anyio

from app.services.mobile_app.models import AndroidWatchSample
from app.settings import AndroidAppConfig

from ..errors import AndroidUiError, is_dead_appium_session_error
from ..tooling import build_android_runtime_env, require_tool_path
from . import selectors


@dataclass(frozen=True)
class AndroidWatchResult:
    verified: bool
    samples: list[AndroidWatchSample]
    ad_debug_page_source: str | None = None


class AndroidYouTubeWatcher:
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

    async def watch_current(
        self,
        *,
        watch_seconds: int,
        timeout_grace_seconds: float = 35.0,
        timeout_floor_seconds: float = 45.0,
    ) -> AndroidWatchResult:
        timeout_seconds = max(
            float(watch_seconds) + float(timeout_grace_seconds),
            float(timeout_floor_seconds),
        )
        with anyio.move_on_after(timeout_seconds) as scope:
            result = await anyio.to_thread.run_sync(
                self._watch_current_sync,
                watch_seconds,
                abandon_on_cancel=True,
            )
        if scope.cancel_called:
            raise AndroidUiError(
                f"Timed out collecting watch samples after {int(timeout_seconds)}s"
            )
        return result

    async def ensure_playing(self) -> bool:
        with anyio.move_on_after(15) as scope:
            result = await anyio.to_thread.run_sync(
                self._ensure_playing_sync,
                abandon_on_cancel=True,
            )
        if scope.cancel_called:
            raise AndroidUiError("Timed out restoring playback state")
        return result

    async def dismiss_residual_ad_if_present(self) -> bool:
        with anyio.move_on_after(12) as scope:
            result = await anyio.to_thread.run_sync(
                self._dismiss_residual_ad_if_present_sync,
                abandon_on_cancel=True,
            )
        if scope.cancel_called:
            raise AndroidUiError("Timed out dismissing residual ad")
        return result

    async def restore_primary_watch_surface(self) -> bool:
        with anyio.move_on_after(20) as scope:
            result = await anyio.to_thread.run_sync(
                self._restore_primary_watch_surface_sync,
                abandon_on_cancel=True,
            )
        if scope.cancel_called:
            raise AndroidUiError("Timed out restoring primary watch surface")
        return result

    def _watch_current_sync(self, watch_seconds: int) -> AndroidWatchResult:
        interval = max(1, self._config.probe_watch_sample_interval_seconds)
        sample_offsets = list(range(0, max(watch_seconds, 1), interval))
        if not sample_offsets or sample_offsets[-1] != watch_seconds:
            sample_offsets.append(watch_seconds)

        started = time.monotonic()
        samples: list[AndroidWatchSample] = []
        nudged_playback_count = 0
        best_ad_page_source = None
        best_ad_page_source_score = -1

        for offset in sample_offsets:
            remaining = started + offset - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            sample, sample_page_source = self._collect_sample_sync(offset)
            samples.append(sample)
            if sample.ad_detected and sample_page_source:
                sample_score = self._ad_capture_score(sample)
                if sample_score >= best_ad_page_source_score:
                    best_ad_page_source = sample_page_source
                    best_ad_page_source_score = sample_score
            if sample.is_reel_surface:
                break
            if nudged_playback_count < 2 and self._should_nudge_playback(samples):
                if self._nudge_playback_sync():
                    nudged_playback_count += 1
                    time.sleep(1.0)

        verified = self._verify_samples(samples)
        return AndroidWatchResult(
            verified=verified,
            samples=samples,
            ad_debug_page_source=best_ad_page_source,
        )

    def _collect_sample_sync(self, offset_seconds: int) -> tuple[AndroidWatchSample, str | None]:
        self._dismiss_system_dialog_sync()
        page_source = self._safe_page_source_sync()
        package = self._safe_driver_attr_sync("current_package")
        activity = self._safe_driver_attr_sync("current_activity")
        player_visible = False
        watch_panel_visible = False
        results_visible = False
        is_reel_surface = False

        seekbar_description = None
        progress_seconds = None
        duration_seconds = None
        ad_seekbar_description = None
        ad_progress_seconds = None
        ad_duration_seconds = None
        ad_signal_labels: list[str] = []
        ad_cta_labels: list[str] = []
        ad_visible_lines: list[str] = []
        error_messages: list[str] = []
        skip_available = False
        ad_sponsor_label = None
        ad_headline_text = None
        ad_display_url = None
        ad_cta_text = None

        parsed_root = None

        if page_source:
            try:
                parsed_root = ET.fromstring(page_source)
            except ET.ParseError:
                parsed_root = None
            if parsed_root is not None:
                player_visible = self._root_has_any_resource_id(
                    parsed_root,
                    (*selectors.WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PLAYER_IDS),
                )
                watch_panel_visible = self._root_has_any_resource_id(
                    parsed_root,
                    (*selectors.WATCH_PANEL_IDS, *selectors.REEL_WATCH_PANEL_IDS),
                )
                is_reel_surface = self._root_has_any_resource_id(
                    parsed_root,
                    (*selectors.REEL_WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PANEL_IDS),
                )
                results_visible = self._root_has_any_resource_id(
                    parsed_root,
                    selectors.WATCH_RESULTS_IDS,
                )
                skip_available = self._root_has_any_resource_id(
                    parsed_root,
                    selectors.AD_SKIP_BUTTON_IDS,
                )
                ad_sponsor_label = self._read_first_text_by_resource_ids(
                    parsed_root,
                    selectors.AD_SPONSOR_TEXT_IDS,
                )
                ad_headline_text = self._read_first_text_by_resource_ids(
                    parsed_root,
                    selectors.AD_HEADLINE_IDS,
                )
                ad_display_url = self._read_first_text_by_resource_ids(
                    parsed_root,
                    selectors.AD_DISPLAY_URL_IDS,
                )
                ad_cta_text = self._read_first_text_by_resource_ids(
                    parsed_root,
                    selectors.AD_CTA_TEXT_IDS,
                )
                seekbar_description = self._extract_seekbar_description(
                    parsed_root,
                    resource_ids=(
                        *selectors.WATCH_TIME_BAR_IDS,
                        *selectors.REEL_WATCH_TIME_BAR_IDS,
                    ),
                )
                progress_seconds, duration_seconds = self._parse_seekbar_progress(
                    seekbar_description
                )
                ad_seekbar_description = self._extract_seekbar_description(
                    parsed_root,
                    resource_ids=selectors.AD_TIME_BAR_IDS,
                )
                ad_progress_seconds, ad_duration_seconds = self._parse_seekbar_progress(
                    ad_seekbar_description
                )
                ad_signal_labels = self._collect_ad_signal_labels(parsed_root)
                ad_cta_labels = self._collect_ad_cta_labels(parsed_root)
                error_messages = self._collect_error_messages(parsed_root)
                ad_visible_lines = self._collect_visible_lines(parsed_root)
        else:
            player_visible = self._has_any_id_sync(
                (*selectors.WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PLAYER_IDS)
            )
            watch_panel_visible = self._has_any_id_sync(
                (*selectors.WATCH_PANEL_IDS, *selectors.REEL_WATCH_PANEL_IDS)
            )
            is_reel_surface = self._has_any_id_sync(
                (*selectors.REEL_WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PANEL_IDS)
            )
            results_visible = self._has_any_id_sync(selectors.WATCH_RESULTS_IDS)
            skip_available = self._has_any_id_sync(selectors.AD_SKIP_BUTTON_IDS)
            ad_sponsor_label = self._read_first_text_by_ids_sync(selectors.AD_SPONSOR_TEXT_IDS)
            ad_headline_text = self._read_first_text_by_ids_sync(selectors.AD_HEADLINE_IDS)
            ad_display_url = self._read_first_text_by_ids_sync(selectors.AD_DISPLAY_URL_IDS)
            ad_cta_text = self._read_first_text_by_ids_sync(selectors.AD_CTA_TEXT_IDS)

        if self._should_probe_sample_via_adb_sync(
            page_source=page_source,
            parsed_root=parsed_root,
            player_visible=player_visible,
            watch_panel_visible=watch_panel_visible,
            results_visible=results_visible,
            error_messages=error_messages,
        ):
            adb_hierarchy = self._dump_ui_hierarchy_via_adb_sync()
            if adb_hierarchy:
                try:
                    adb_root = ET.fromstring(adb_hierarchy)
                except ET.ParseError:
                    adb_root = None
                if adb_root is not None:
                    page_source = adb_hierarchy
                    player_visible = self._root_has_any_resource_id(
                        adb_root,
                        (*selectors.WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PLAYER_IDS),
                    )
                    watch_panel_visible = self._root_has_any_resource_id(
                        adb_root,
                        (*selectors.WATCH_PANEL_IDS, *selectors.REEL_WATCH_PANEL_IDS),
                    )
                    is_reel_surface = self._root_has_any_resource_id(
                        adb_root,
                        (*selectors.REEL_WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PANEL_IDS),
                    )
                    results_visible = self._root_has_any_resource_id(
                        adb_root,
                        selectors.WATCH_RESULTS_IDS,
                    )
                    if not seekbar_description:
                        seekbar_description = self._extract_seekbar_description(
                            adb_root,
                            resource_ids=(
                                *selectors.WATCH_TIME_BAR_IDS,
                                *selectors.REEL_WATCH_TIME_BAR_IDS,
                            ),
                        )
                        progress_seconds, duration_seconds = self._parse_seekbar_progress(
                            seekbar_description
                        )
                    skip_available = skip_available or self._root_has_any_resource_id(
                        adb_root,
                        selectors.AD_SKIP_BUTTON_IDS,
                    )
                    ad_sponsor_label = ad_sponsor_label or self._read_first_text_by_resource_ids(
                        adb_root,
                        selectors.AD_SPONSOR_TEXT_IDS,
                    )
                    ad_headline_text = ad_headline_text or self._read_first_text_by_resource_ids(
                        adb_root,
                        selectors.AD_HEADLINE_IDS,
                    )
                    ad_display_url = ad_display_url or self._read_first_text_by_resource_ids(
                        adb_root,
                        selectors.AD_DISPLAY_URL_IDS,
                    )
                    ad_cta_text = ad_cta_text or self._read_first_text_by_resource_ids(
                        adb_root,
                        selectors.AD_CTA_TEXT_IDS,
                    )
                    if not ad_seekbar_description:
                        ad_seekbar_description = self._extract_seekbar_description(
                            adb_root,
                            resource_ids=selectors.AD_TIME_BAR_IDS,
                        )
                        ad_progress_seconds, ad_duration_seconds = self._parse_seekbar_progress(
                            ad_seekbar_description
                        )
                    ad_signal_labels = ad_signal_labels or self._collect_ad_signal_labels(
                        adb_root
                    )
                    ad_cta_labels = ad_cta_labels or self._collect_ad_cta_labels(adb_root)
                    error_messages = error_messages or self._collect_error_messages(adb_root)
                    ad_visible_lines = ad_visible_lines or self._collect_visible_lines(adb_root)

        active_ad_signal_labels = self._filter_active_ad_signal_labels(ad_signal_labels)
        active_ad_cta_labels = self._filter_active_ad_cta_labels(
            ad_cta_labels,
            results_visible=results_visible,
        )

        # For skippable ads, YouTube hides the ad-specific time_bar and uses the regular
        # watch_while_time_bar_view seekbar to show the ad's progress. Fall back to the
        # video seekbar description as the ad seekbar so remaining-time math works.
        if skip_available and not ad_seekbar_description and seekbar_description:
            ad_seekbar_description = seekbar_description
            ad_progress_seconds = progress_seconds
            ad_duration_seconds = duration_seconds

        ad_detected = bool(
            skip_available
            or ad_seekbar_description
            or ad_sponsor_label
            or ad_display_url
            or ad_cta_text
            or active_ad_signal_labels
            or active_ad_cta_labels
        )

        return AndroidWatchSample(
            offset_seconds=offset_seconds,
            package=package,
            activity=activity,
            page_source_length=len(page_source),
            is_reel_surface=is_reel_surface,
            player_visible=player_visible,
            watch_panel_visible=watch_panel_visible,
            results_visible=results_visible,
            seekbar_description=seekbar_description,
            progress_seconds=progress_seconds,
            duration_seconds=duration_seconds,
            ad_seekbar_description=ad_seekbar_description,
            ad_progress_seconds=ad_progress_seconds,
            ad_duration_seconds=ad_duration_seconds,
            ad_detected=ad_detected,
            skip_available=skip_available,
            skip_clicked=False,
            ad_sponsor_label=ad_sponsor_label,
            ad_headline_text=ad_headline_text,
            ad_display_url=ad_display_url,
            ad_cta_text=ad_cta_text,
            ad_visible_lines=ad_visible_lines[:24] if ad_detected else [],
            ad_signal_labels=ad_signal_labels,
            ad_cta_labels=ad_cta_labels,
            error_messages=error_messages,
        ), page_source

    def _should_probe_sample_via_adb_sync(
        self,
        *,
        page_source: str,
        parsed_root: ET.Element | None,
        player_visible: bool,
        watch_panel_visible: bool,
        results_visible: bool,
        error_messages: list[str],
    ) -> bool:
        if not self._adb_serial:
            return False
        if not page_source:
            return True
        if parsed_root is None:
            return True
        if error_messages:
            return True
        return not (player_visible or watch_panel_visible or results_visible)

    def _dismiss_system_dialog_sync(self) -> bool:
        page_source = self._safe_page_source_sync()
        lowered = page_source.casefold()
        has_dialog_hint = any(
            hint.casefold() in lowered for hint in selectors.SYSTEM_DIALOG_TITLE_HINTS
        )
        if not has_dialog_hint and page_source:
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
        if self._adb_serial and (has_dialog_hint or not page_source):
            return self._dismiss_system_dialog_via_adb_sync()
        return False

    def _dismiss_system_dialog_via_adb_sync(self) -> bool:
        if not self._adb_serial:
            return False
        if not self._wait_for_adb_device_sync(timeout_seconds=6.0):
            return False
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return False
        try:
            root = ET.fromstring(hierarchy)
        except ET.ParseError:
            return False
        for node in root.iter():
            text = (node.attrib.get("text") or "").strip()
            content_desc = (node.attrib.get("content-desc") or "").strip()
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if (
                resource_id not in selectors.SYSTEM_DIALOG_WAIT_IDS
                and text not in selectors.SYSTEM_DIALOG_WAIT_TEXTS
                and content_desc not in selectors.SYSTEM_DIALOG_WAIT_TEXTS
            ):
                continue
            bounds = self._parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if self._tap_bounds_via_adb(bounds):
                self._wait_for_adb_device_sync(timeout_seconds=8.0)
                return True
        return False

    def _dump_ui_hierarchy_via_adb_sync(self, *, timeout_seconds: float = 6.0) -> str | None:
        if not self._adb_serial:
            return None
        adb_bin = require_tool_path("adb")
        dump_path = "/sdcard/codex_watcher_window_dump.xml"
        dump_result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "uiautomator",
                "dump",
                dump_path,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=timeout_seconds,
        )
        if dump_result.returncode != 0:
            return None
        cat_result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "cat",
                dump_path,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=timeout_seconds,
        )
        if cat_result.returncode != 0:
            return None
        return cat_result.stdout or None

    def _wait_for_adb_device_sync(self, *, timeout_seconds: float) -> bool:
        if not self._adb_serial:
            return False
        adb_bin = require_tool_path("adb")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    self._adb_serial,
                    "get-state",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip() == "device":
                return True
            time.sleep(0.6)
        return False

    def _safe_page_source_sync(self) -> str:
        try:
            return self._driver.page_source or ""
        except Exception as exc:
            if is_dead_appium_session_error(exc):
                raise AndroidUiError(f"Appium session died while reading page source: {exc}") from exc
            return ""

    def _safe_driver_attr_sync(self, name: str) -> str | None:
        try:
            return getattr(self._driver, name)
        except Exception as exc:
            if is_dead_appium_session_error(exc):
                raise AndroidUiError(f"Appium session died while reading driver attribute {name}: {exc}") from exc
            return None

    def _has_any_id_sync(self, candidate_ids: tuple[str, ...]) -> bool:
        for candidate_id in candidate_ids:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception as exc:
                if is_dead_appium_session_error(exc):
                    raise AndroidUiError(
                        f"Appium session died while probing UI id {candidate_id}: {exc}"
                    ) from exc
                continue
        return False

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

    @staticmethod
    def _root_has_any_resource_id(root: ET.Element, candidate_ids: tuple[str, ...]) -> bool:
        candidate_set = set(candidate_ids)
        for node in root.iter():
            if (node.attrib.get("resource-id") or "").strip() in candidate_set:
                return True
        return False

    @staticmethod
    def _read_first_text_by_resource_ids(
        root: ET.Element,
        candidate_ids: tuple[str, ...],
    ) -> str | None:
        candidate_set = set(candidate_ids)
        for node in root.iter():
            if (node.attrib.get("resource-id") or "").strip() not in candidate_set:
                continue
            for raw_value in (node.attrib.get("text"), node.attrib.get("content-desc")):
                value = (raw_value or "").strip()
                if value:
                    return value
        return None


    def _extract_seekbar_description(
        self,
        root: ET.Element,
        *,
        resource_ids: tuple[str, ...],
    ) -> str | None:
        for description in self._iter_seekbar_descriptions(root, resource_ids=resource_ids):
            if description:
                return description
        return None

    def _iter_seekbar_descriptions(
        self,
        root: ET.Element,
        *,
        resource_ids: tuple[str, ...],
    ) -> list[str]:
        descriptions: list[str] = []

        def walk(node: ET.Element, ancestor_ids: tuple[str, ...]) -> None:
            current_id = (node.attrib.get("resource-id") or "").strip()
            next_ancestor_ids = ancestor_ids + ((current_id,) if current_id else ())
            if node.attrib.get("class") == "android.widget.SeekBar":
                candidate_ids = {current_id, *ancestor_ids}
                if any(candidate_id in resource_ids for candidate_id in candidate_ids):
                    description = (node.attrib.get("content-desc") or "").strip()
                    if description:
                        descriptions.append(description)
            for child in list(node):
                walk(child, next_ancestor_ids)

        walk(root, ())
        return descriptions

    @staticmethod
    def _parse_seekbar_progress(description: str | None) -> tuple[int | None, int | None]:
        if not description:
            return None, None
        numbers = [int(value) for value in re.findall(r"\d+", description)]
        if len(numbers) >= 4:
            current = numbers[0] * 60 + numbers[1]
            total = numbers[2] * 60 + numbers[3]
            return current, total
        if len(numbers) >= 2:
            return numbers[0], numbers[1]
        return None, None

    @staticmethod
    def _normalize_label(value: str | None) -> str:
        return (value or "").strip()

    def _collect_ad_signal_labels(self, root: ET.Element) -> list[str]:
        seen: list[str] = []
        for node in root.iter():
            description = self._normalize_label(node.attrib.get("content-desc"))
            text = self._normalize_label(node.attrib.get("text"))
            for candidate in (description, text):
                if not candidate:
                    continue
                lowered = candidate.lower()
                if any(fragment.lower() in lowered for fragment in selectors.AD_SIGNAL_DESCRIPTION_FRAGMENTS):
                    if candidate not in seen:
                        seen.append(candidate)
        return seen

    def _collect_ad_cta_labels(self, root: ET.Element) -> list[str]:
        seen: list[str] = []
        for node in root.iter():
            description = self._normalize_label(node.attrib.get("content-desc"))
            text = self._normalize_label(node.attrib.get("text"))
            for candidate in (description, text):
                if not candidate:
                    continue
                if any(candidate.lower() == label.lower() for label in selectors.AD_CTA_DESCRIPTIONS):
                    if candidate not in seen:
                        seen.append(candidate)
        return seen

    def _collect_error_messages(self, root: ET.Element) -> list[str]:
        seen: list[str] = []
        for node in root.iter():
            for raw_value in (node.attrib.get("text"), node.attrib.get("content-desc")):
                value = self._normalize_label(raw_value)
                if not value:
                    continue
                lowered = value.lower()
                if any(token.lower() in lowered for token in selectors.WATCH_ERROR_TEXTS):
                    if value not in seen:
                        seen.append(value)
        return seen

    def _collect_visible_lines(self, root: ET.Element) -> list[str]:
        seen: list[str] = []
        for node in root.iter():
            for raw_value in (node.attrib.get("text"), node.attrib.get("content-desc")):
                value = self._normalize_label(raw_value)
                if not value:
                    continue
                if value not in seen:
                    seen.append(value)
        return seen

    @staticmethod
    def _filter_active_ad_signal_labels(labels: list[str]) -> list[str]:
        active: list[str] = []
        for label in labels:
            lowered = label.casefold()
            if "sponsored" in lowered and "play video" in lowered:
                continue
            if "sponsored" in lowered and "visit site" in lowered:
                continue
            if not any(
                token in lowered
                for token in (
                    "advertiser",
                    "ad choices",
                    "like ad",
                    "share ad",
                    "close ad panel",
                )
            ):
                continue
            active.append(label)
        return active

    @staticmethod
    def _filter_active_ad_cta_labels(
        labels: list[str],
        *,
        results_visible: bool,
    ) -> list[str]:
        if not labels:
            return []
        if not results_visible:
            return labels
        active: list[str] = []
        for label in labels:
            lowered = label.casefold()
            if lowered in {"visit advertiser", "learn more", "подробнее", "дізнатися більше"}:
                active.append(label)
        return active

    @staticmethod
    def _ad_capture_score(sample: AndroidWatchSample) -> int:
        score = 0
        if sample.ad_sponsor_label:
            score += 5
        if sample.ad_headline_text:
            score += 5
        if sample.ad_display_url:
            score += 5
        if sample.ad_cta_text:
            score += 4
        if sample.ad_seekbar_description:
            score += 3
        if sample.skip_available:
            score += 2
        score += min(len(sample.ad_visible_lines), 8)
        score += min(len(sample.ad_signal_labels), 4)
        score += min(len(sample.ad_cta_labels), 4)
        return score

    def _verify_samples(self, samples: list[AndroidWatchSample]) -> bool:
        if not samples:
            return False
        if any(sample.is_reel_surface for sample in samples):
            return False
        if not any(sample.player_visible and sample.watch_panel_visible for sample in samples):
            return False
        if any(sample.error_messages for sample in samples):
            return False

        progress_points = [
            sample.progress_seconds
            for sample in samples
            if sample.progress_seconds is not None
        ]
        if len(progress_points) >= 2:
            delta = progress_points[-1] - progress_points[0]
            if delta >= self._config.probe_watch_min_progress_delta_seconds:
                return True

        if any(sample.ad_detected for sample in samples):
            return True

        return False

    @staticmethod
    def _should_nudge_playback(samples: list[AndroidWatchSample]) -> bool:
        stable_samples = [
            sample
            for sample in samples
            if sample.player_visible
            and sample.watch_panel_visible
            and not sample.error_messages
        ]
        if len(stable_samples) < 2:
            return False
        last_two = stable_samples[-2:]
        progress_points = [
            AndroidYouTubeWatcher._sample_progress_seconds(sample)
            for sample in last_two
        ]
        if all(point is None for point in progress_points):
            return True
        if all(point is not None for point in progress_points):
            return progress_points[-1] <= progress_points[0]
        return False

    @staticmethod
    def _sample_progress_seconds(sample: AndroidWatchSample) -> int | None:
        if sample.ad_progress_seconds is not None:
            return sample.ad_progress_seconds
        return sample.progress_seconds

    def _nudge_playback_sync(self) -> bool:
        bounds = self._extract_bounds_by_ids_sync(
            (*selectors.WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PLAYER_IDS)
        )
        if bounds is None:
            try:
                size = self._driver.get_window_size()
                bounds = (
                    0,
                    0,
                    int(size["width"]),
                    max(1, int(size["height"] * 0.42)),
                )
            except Exception:
                return False
        left, top, right, bottom = bounds
        x = int((left + right) / 2)
        y = int((top + bottom) / 2)
        try:
            self._driver.execute_script(
                "mobile: clickGesture",
                {
                    "x": x,
                    "y": y,
                },
            )
            return True
        except Exception:
            return False

    def _ensure_playing_sync(self) -> bool:
        attempts = 3
        for _ in range(attempts):
            if self._dismiss_system_dialog_sync():
                time.sleep(0.8)
            playback_state = self._playback_control_state_sync()
            if playback_state == "playing":
                return True
            if playback_state == "paused":
                if self._tap_playback_control_sync():
                    time.sleep(0.8)
                    if self._playback_control_state_sync() != "paused":
                        return True
            if self._nudge_playback_sync():
                time.sleep(0.8)
                playback_state = self._playback_control_state_sync()
                if playback_state != "paused":
                    return True
            time.sleep(0.5)
        return False

    def _playback_control_state_sync(self) -> str | None:
        page_source = self._safe_page_source_sync()
        if not page_source:
            return None
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return None
        target_ids = set(selectors.WATCH_PLAY_PAUSE_CONTROL_IDS)
        for node in root.iter():
            if (node.attrib.get("resource-id") or "").strip() not in target_ids:
                continue
            desc = (node.attrib.get("content-desc") or "").strip().casefold()
            text = (node.attrib.get("text") or "").strip().casefold()
            probe = f"{desc} {text}".strip()
            if "pause video" in probe or "pause" in probe:
                return "playing"
            if "play video" in probe or probe == "play" or "воспроизвести" in probe:
                return "paused"
        return None

    def _tap_playback_control_sync(self) -> bool:
        page_source = self._safe_page_source_sync()
        if not page_source:
            return False
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return False
        target_ids = set(selectors.WATCH_PLAY_PAUSE_CONTROL_IDS)
        for node in root.iter():
            if (node.attrib.get("resource-id") or "").strip() not in target_ids:
                continue
            bounds = self._parse_bounds(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if self._tap_bounds_via_adb(bounds):
                return True
        return False

    def _restore_primary_watch_surface_sync(self) -> bool:
        if self._dismiss_system_dialog_sync():
            time.sleep(0.8)
        if self._is_primary_watch_surface_sync():
            return True
        for _ in range(4):
            self._dismiss_system_dialog_sync()
            self._swipe_watch_feed_down_sync()
            time.sleep(0.8)
            if self._is_primary_watch_surface_sync():
                return True
        return self._is_primary_watch_surface_sync()

    def _dismiss_residual_ad_if_present_sync(self) -> bool:
        for candidate_id in selectors.AD_SKIP_BUTTON_IDS:
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

        for text in selectors.AD_PANEL_CLOSE_HINTS:
            for selector in (
                f'new UiSelector().descriptionContains("{text}")',
                f'new UiSelector().textContains("{text}")',
            ):
                try:
                    elements = self._driver.find_elements(
                        "-android uiautomator",
                        selector,
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
            if self._tap_first_bounds_for_fragment(text):
                return True

        for text in (*selectors.DISMISS_TEXTS, "Skip ad", "Пропустить рекламу"):
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
            if self._tap_first_bounds_for_fragment(text):
                return True

        return False

    def _tap_first_bounds_for_fragment(self, fragment: str) -> bool:
        if not self._adb_serial:
            return False
        page_source = self._safe_page_source_sync()
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

    def _extract_bounds_by_ids_sync(self, candidate_ids: tuple[str, ...]) -> tuple[int, int, int, int] | None:
        page_source = self._safe_page_source_sync()
        if not page_source:
            return None
        try:
            root = ET.fromstring(page_source)
        except ET.ParseError:
            return None
        for node in root.iter():
            if (node.attrib.get("resource-id") or "") not in candidate_ids:
                continue
            return self._parse_bounds(node.attrib.get("bounds"))
        return None

    def _is_primary_watch_surface_sync(self) -> bool:
        bounds = self._extract_bounds_by_ids_sync(
            (*selectors.WATCH_PLAYER_IDS, *selectors.REEL_WATCH_PLAYER_IDS)
        )
        if bounds is None:
            return False
        try:
            size = self._driver.get_window_size()
            screen_height = int(size["height"])
        except Exception:
            screen_height = max(bounds[3], 1)
        left, top, right, bottom = bounds
        height = max(1, bottom - top)
        return top <= int(screen_height * 0.12) and height >= int(screen_height * 0.16)

    def _swipe_watch_feed_down_sync(self) -> None:
        bounds = self._extract_bounds_by_ids_sync(selectors.WATCH_LIST_IDS)
        if bounds is None:
            try:
                size = self._driver.get_window_size()
                bounds = (
                    0,
                    int(size["height"] * 0.30),
                    int(size["width"]),
                    int(size["height"] * 0.90),
                )
            except Exception:
                return
        left, top, right, bottom = bounds
        x = int((left + right) / 2)
        start_y = int(top + max(140, (bottom - top) * 0.28))
        end_y = int(bottom - max(160, (bottom - top) * 0.12))
        adb_bin = require_tool_path("adb")
        if self._adb_serial:
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
                    "320",
                ],
                check=False,
                capture_output=True,
                env=build_android_runtime_env(),
                timeout=30,
            )
            return
        try:
            self._driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": left,
                    "top": top,
                    "width": max(1, right - left),
                    "height": max(1, bottom - top),
                    "direction": "down",
                    "percent": 0.52,
                },
            )
        except Exception:
            return

    @staticmethod
    def _parse_bounds(bounds: str | None) -> tuple[int, int, int, int] | None:
        if not bounds:
            return None
        numbers = [int(value) for value in re.findall(r"\d+", bounds)]
        if len(numbers) != 4:
            return None
        return numbers[0], numbers[1], numbers[2], numbers[3]
