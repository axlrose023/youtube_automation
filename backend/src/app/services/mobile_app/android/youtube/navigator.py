from __future__ import annotations

import subprocess
import threading
import time
import logging
from urllib.parse import quote_plus
from dataclasses import dataclass
import re

import anyio

from app.settings import AndroidAppConfig

from ..errors import AndroidUiError, is_dead_appium_session_error
from ..tooling import build_android_runtime_env, require_tool_path
from . import selectors

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NativeSelectorCandidate:
    kind: str
    value: str


@dataclass(frozen=True)
class NativeResultCandidate:
    title: str
    bounds: tuple[int, int, int, int]
    is_short: bool
    is_sponsored: bool


class AndroidYouTubeNavigator:
    _SEMANTIC_TOKEN_GROUPS = (
        frozenset({"invest", "trad", "profit", "income", "earn"}),
        frozenset({"crypto", "cryptocurrency"}),
    )
    _BROAD_MONEY_QUERY_TOKENS = frozenset({"earn", "income", "money", "profit", "immediate"})
    _SPECIFIC_FINANCE_QUERY_TOKENS = frozenset(
        {
            "ai",
            "automat",
            "bitcoin",
            "bot",
            "crypto",
            "cryptocurrency",
            "invest",
            "market",
            "platform",
            "quantum",
            "review",
            "software",
            "stock",
            "trad",
            "traderai",
        }
    )
    _GENERIC_MONEY_LISTICLE_TOKENS = frozenset(
        {
            "app",
            "best",
            "cash",
            "daily",
            "game",
            "gift",
            "instant",
            "list",
            "pay",
            "paypal",
            "site",
            "survey",
            "top",
            "way",
            "website",
            "withdraw",
        }
    )
    _GENERIC_MONEY_LISTICLE_SUBSTRINGS = (
        "cashout",
        "gift card",
        "giftcard",
        "paypal",
        "paid apps",
        "paying apps",
        "survey app",
        "survey apps",
    )
    _BROAD_QUERY_TOKENS = frozenset(
        {
            "crypto",
            "cryptocurrency",
            "bitcoin",
            "platform",
            "review",
            "guide",
            "tutorial",
            "course",
            "strategy",
            "strateg",
            "path",
            "automat",
        }
    )
    _FINANCE_TOPIC_HINTS = (
        "crypto",
        "bitcoin",
        "ethereum",
        "forex",
        "finance",
        "financial",
        "invest",
        "income",
        "passive",
        "earn",
        "yield",
        "staking",
        "dividend",
        "stock",
        "market",
        "trading",
        "econom",
    )
    _FINANCE_POSITIVE_TITLE_HINTS = (
        "what is",
        "explained",
        "explanation",
        "guide",
        "tutorial",
        "beginner",
        "beginners",
        "basics",
        "analysis",
        "overview",
        "how to",
        "market",
        "review",
        "full recording",
        "scam",
    )
    _FINANCE_NEGATIVE_TITLE_HINTS = (
        "get rich",
        "overnight",
        "secret",
        "shocking",
        "won't believe",
        "will shock you",
        "make millions",
        "millions",
        "price prediction",
        "prediction",
        "bull run",
        "to the moon",
        "100x",
        "skyrockets",
        "breaking",
        "surges",
        "explodes",
    )
    _FINANCE_ENTERTAINMENT_TITLE_HINTS = (
        "gameplay",
        "walkthrough",
        "boss fight",
        "mmorpg",
        "mmo",
        "rpg",
        "trailer",
        "gaming",
        "let's play",
    )

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
        self._last_tapped_result_title: str | None = None
        self._last_tapped_result_is_short = False
        self._watch_activity_probe_at = 0.0
        self._watch_activity_probe_result = False
        self._rejected_result_titles: set[str] = set()
        self._thread_local = threading.local()
        self._results_source_cache_xml: str | None = None
        self._results_source_cache_at = 0.0

    def _check_sync_deadline(self) -> None:
        deadline = getattr(self._thread_local, "hard_deadline", float("inf"))
        if time.monotonic() > deadline:
            raise AndroidUiError("sync operation hard deadline exceeded")

    def _invalidate_results_source_cache_sync(self) -> None:
        self._results_source_cache_xml = None
        self._results_source_cache_at = 0.0

    async def ensure_app_ready(self) -> None:
        await anyio.to_thread.run_sync(
            self._ensure_app_ready_sync,
            abandon_on_cancel=True,
        )

    async def open_search(self) -> None:
        await anyio.to_thread.run_sync(
            self._open_search_sync,
            abandon_on_cancel=True,
        )

    async def reset_to_home(self, *, deadline: float | None = None) -> None:
        hard_deadline = deadline if deadline is not None else float("inf")

        def _run() -> None:
            self._thread_local.hard_deadline = hard_deadline
            try:
                self._reset_to_home_sync()
            finally:
                self._thread_local.hard_deadline = float("inf")

        await anyio.to_thread.run_sync(_run, abandon_on_cancel=True)

    async def submit_search(self, query: str, *, deadline: float | None = None) -> None:
        hard_deadline = deadline if deadline is not None else float("inf")

        def _run() -> None:
            self._thread_local.hard_deadline = hard_deadline
            try:
                self._submit_search_sync(query)
            finally:
                self._thread_local.hard_deadline = float("inf")

        await anyio.to_thread.run_sync(_run, abandon_on_cancel=True)

    async def reject_result_title(self, title: str | None) -> None:
        await anyio.to_thread.run_sync(
            self._reject_result_title_sync,
            title,
            abandon_on_cancel=True,
        )

    async def wait_for_results(self, query: str | None = None, *, deadline: float | None = None) -> None:
        hard_deadline = deadline if deadline is not None else float("inf")

        def _run() -> None:
            self._thread_local.hard_deadline = hard_deadline
            try:
                self._wait_for_results_sync(query)
            finally:
                self._thread_local.hard_deadline = float("inf")

        await anyio.to_thread.run_sync(_run, abandon_on_cancel=True)

    async def open_first_result(self, query: str | None = None, *, deadline: float | None = None) -> str | None:
        hard_deadline = deadline if deadline is not None else float("inf")
        result: list[str | None] = [None]

        def _run() -> None:
            self._thread_local.hard_deadline = hard_deadline
            try:
                result[0] = self._open_first_result_sync(query)
            finally:
                self._thread_local.hard_deadline = float("inf")

        await anyio.to_thread.run_sync(_run, abandon_on_cancel=True)
        return result[0]

    async def has_query_ready_surface(self, query: str) -> bool:
        return await anyio.to_thread.run_sync(
            self._has_query_ready_surface_sync,
            query,
            abandon_on_cancel=True,
        )

    async def has_query_ready_surface_via_adb(self, query: str) -> bool:
        return await anyio.to_thread.run_sync(
            self._has_query_ready_surface_via_adb_sync,
            query,
            abandon_on_cancel=True,
        )

    async def has_query_results_surface(self, query: str) -> bool:
        return await anyio.to_thread.run_sync(
            self._has_query_results_surface_sync,
            query,
            abandon_on_cancel=True,
        )

    async def dismiss_system_dialogs_via_adb(self) -> bool:
        return await anyio.to_thread.run_sync(
            self._dismiss_system_dialogs_via_adb_sync,
            abandon_on_cancel=True,
        )

    async def recover_from_launcher_anr(self) -> bool:
        return await anyio.to_thread.run_sync(
            self._recover_from_launcher_anr_sync,
            abandon_on_cancel=True,
        )

    async def await_current_watch_title(
        self,
        query: str | None = None,
        *,
        timeout_seconds: float = 4.0,
    ) -> str | None:
        return await anyio.to_thread.run_sync(
            self._await_current_watch_title_sync,
            query,
            timeout_seconds,
            abandon_on_cancel=True,
        )

    async def describe_surface(self) -> tuple[str | None, str | None, int]:
        return await anyio.to_thread.run_sync(
            self._describe_surface_sync,
            abandon_on_cancel=True,
        )

    async def current_package_activity(self) -> tuple[str | None, str | None]:
        return await anyio.to_thread.run_sync(
            lambda: (self._safe_current_package_sync(), self._safe_current_activity_sync()),
            abandon_on_cancel=True,
        )

    async def provisional_watch_title(self, query: str) -> str | None:
        return await anyio.to_thread.run_sync(
            self._provisional_watch_title_sync,
            query,
            abandon_on_cancel=True,
        )

    async def has_watch_surface_for_query(self, query: str | None = None) -> bool:
        return await anyio.to_thread.run_sync(
            self._is_watch_surface_for_query_sync,
            query,
            abandon_on_cancel=True,
        )

    async def has_watch_activity(self) -> bool:
        return await anyio.to_thread.run_sync(
            self._is_watchwhile_activity_sync,
            abandon_on_cancel=True,
        )

    async def reject_reel_watch_surface(self, query: str | None = None) -> bool:
        return await anyio.to_thread.run_sync(
            self._escape_reel_surface_for_query_sync,
            query,
            abandon_on_cancel=True,
        )

    def _ensure_app_ready_sync(self) -> None:
        self._ensure_youtube_foreground_sync(require_interactive=False)
        if self._safe_current_package_sync() != self._config.youtube_package:
            self._launch_youtube_if_needed_sync()
            time.sleep(1.2)
        self._dismiss_possible_dialogs_sync()
        if self._safe_current_package_sync() != self._config.youtube_package:
            self._launch_youtube_if_needed_sync()
            time.sleep(1.0)
            self._dismiss_possible_dialogs_sync()
        current_activity = self._safe_current_activity_sync() or ""
        if current_activity.endswith("NewVersionAvailableActivity"):
            raise AndroidUiError(
                "YouTube app requires update on this Android image. "
                "Create a warm snapshot with an updated YouTube app before running probes."
            )
        if self._safe_current_package_sync() != self._config.youtube_package:
            self._ensure_youtube_foreground_sync(require_interactive=False)
            return
        if self._is_degraded_feed_shell_sync():
            self._launch_youtube_via_adb_sync()
            time.sleep(1.2)
            self._dismiss_possible_dialogs_sync()

    def _reject_result_title_sync(self, title: str | None) -> None:
        normalized = (title or "").strip().casefold()
        if normalized:
            self._rejected_result_titles.add(normalized)

    def _launch_youtube_if_needed_sync(self) -> None:
        current_package = self._safe_current_package_sync()
        if current_package == self._config.youtube_package:
            return

        errors: list[str] = []
        if self._adb_serial:
            try:
                self._launch_youtube_via_adb_sync()
                time.sleep(2)
                if self._wait_for_youtube_package_sync(timeout_seconds=4):
                    return
            except Exception as exc:
                errors.append(f"adb_start: {exc}")
            joined_errors = " | ".join(errors) if errors else "no adb launch method succeeded"
            raise AndroidUiError(f"Failed to launch YouTube app: {joined_errors}")
        try:
            self._driver.activate_app(self._config.youtube_package)
            time.sleep(2)
            if self._wait_for_youtube_package_sync(timeout_seconds=4):
                return
        except Exception as exc:
            errors.append(f"activate_app: {exc}")

        try:
            self._driver.execute_script(
                "mobile: startActivity",
                {
                    "component": (
                        f"{self._config.youtube_package}/{self._config.youtube_activity}"
                    ),
                },
            )
            time.sleep(2)
            if self._wait_for_youtube_package_sync(timeout_seconds=4):
                return
        except Exception as exc:
            errors.append(f"mobile:startActivity: {exc}")

        start_activity = getattr(self._driver, "start_activity", None)
        if callable(start_activity):
            try:
                start_activity(self._config.youtube_package, self._config.youtube_activity)
                if self._wait_for_youtube_package_sync(timeout_seconds=4):
                    return
            except Exception as exc:
                errors.append(f"start_activity: {exc}")

        joined_errors = " | ".join(errors) if errors else "no launch method succeeded"
        raise AndroidUiError(f"Failed to launch YouTube app: {joined_errors}")

    def _wait_for_youtube_package_sync(self, *, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._safe_current_package_sync() == self._config.youtube_package:
                return True
            time.sleep(0.5)
        return False

    def _is_rejected_result_title_sync(self, title: str | None) -> bool:
        normalized = (title or "").strip().casefold()
        return bool(normalized) and normalized in self._rejected_result_titles

    def _should_skip_result_title_for_query_sync(self, title: str | None, query: str | None) -> bool:
        normalized_title = (title or "").strip()
        if not normalized_title:
            return True
        if self._is_placeholder_result_title(normalized_title):
            return True
        if self._is_rejected_result_title_sync(normalized_title):
            return True
        return bool(query) and self._is_disfavored_broad_money_match_sync(normalized_title, query)

    def _ensure_youtube_foreground_sync(self, *, require_interactive: bool) -> None:
        self._check_sync_deadline()
        if self._safe_current_package_sync() != self._config.youtube_package:
            self._launch_youtube_if_needed_sync()
            time.sleep(2.0)
        self._dismiss_possible_dialogs_sync()
        if not require_interactive:
            return
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            self._check_sync_deadline()
            if self._safe_current_package_sync() != self._config.youtube_package:
                self._launch_youtube_if_needed_sync()
                time.sleep(1.5)
                self._dismiss_possible_dialogs_sync()
                continue
            if self._is_degraded_feed_shell_sync():
                self._launch_youtube_via_adb_sync()
                time.sleep(1.8)
                self._dismiss_possible_dialogs_sync()
                continue
            if self._is_interactive_youtube_surface_sync():
                return
            time.sleep(0.6)
            self._dismiss_possible_dialogs_sync()
        self._launch_youtube_if_needed_sync()
        time.sleep(2.0)
        self._dismiss_possible_dialogs_sync()

    def _is_interactive_youtube_surface_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        return (
            self._is_browsing_surface_sync()
            or self._is_search_input_visible_sync()
            or self._has_search_context_sync()
            or self._has_watch_surface_sync()
            or self._has_results_surface_sync()
            or self._is_voice_search_surface_sync()
        )

    @classmethod
    def _source_has_playerless_watch_shell_sync(cls, page_source: str) -> bool:
        if not page_source:
            return False
        lowered = page_source.casefold()
        if any(candidate_id in page_source for candidate_id in selectors.PLAYERLESS_WATCH_HINT_IDS):
            # If real player is also present, thumbnail is just buffering — not a playerless shell
            if any(player_id in page_source for player_id in selectors.WATCH_PLAYER_IDS):
                return False
            return True
        if any(hint.casefold() in lowered for hint in selectors.MINIPLAYER_SURFACE_DESCRIPTION_HINTS):
            # If the full watch panel (watch_list) is present, we're on the real watch screen —
            # "Expand Mini Player" appears as a player overlay hint, not a true miniplayer.
            if any(candidate_id in page_source for candidate_id in selectors.WATCH_LIST_IDS):
                return False
            return any(
                candidate_id in page_source
                for candidate_id in (
                    *selectors.WATCH_PLAYER_IDS,
                    *selectors.MINIPLAYER_SURFACE_IDS,
                )
            )
        return False

    @staticmethod
    def _source_has_any_id_sync(page_source: str, candidate_ids: tuple[str, ...]) -> bool:
        return bool(page_source) and any(candidate_id in page_source for candidate_id in candidate_ids)

    @classmethod
    def _source_has_search_input_markup_sync(cls, page_source: str) -> bool:
        return cls._source_has_any_id_sync(page_source, selectors.SEARCH_INPUT_IDS)

    @classmethod
    def _source_has_search_context_markup_sync(cls, page_source: str) -> bool:
        return cls._source_has_search_input_markup_sync(page_source) or cls._source_has_any_id_sync(
            page_source,
            selectors.SEARCH_QUERY_HEADER_IDS,
        )

    @classmethod
    def _source_has_results_surface_markup_sync(cls, page_source: str) -> bool:
        return cls._source_has_any_id_sync(page_source, selectors.RESULTS_CONTAINER_IDS)

    @classmethod
    def _source_has_watch_surface_markup_sync(cls, page_source: str) -> bool:
        if cls._source_has_any_id_sync(
            page_source,
            (
                *selectors.WATCH_PLAYER_IDS,
                *selectors.WATCH_PANEL_IDS,
                *selectors.WATCH_TIME_BAR_IDS,
            ),
        ):
            return True
        lowered = page_source.casefold()
        has_comments = any(hint.casefold() in lowered for hint in selectors.COMMENT_TEXT_HINTS)
        has_like = any(hint.casefold() in lowered for hint in selectors.LIKE_DESCRIPTION_HINTS)
        return has_comments and has_like

    @classmethod
    def _source_has_reel_watch_surface_markup_sync(cls, page_source: str) -> bool:
        if cls._source_has_any_id_sync(
            page_source,
            (
                *selectors.REEL_WATCH_PLAYER_IDS,
                *selectors.REEL_WATCH_PANEL_IDS,
                *selectors.REEL_WATCH_TIME_BAR_IDS,
            ),
        ):
            return True
        lowered = page_source.casefold()
        return "shorts" in lowered and "reel" in lowered

    @classmethod
    def _source_is_voice_search_surface_sync(
        cls,
        page_source: str,
        current_activity: str | None = None,
    ) -> bool:
        activity = (current_activity or "").casefold()
        if "voice" in activity:
            return True
        lowered = page_source.casefold()
        return any(hint.casefold() in lowered for hint in selectors.VOICE_SEARCH_SCREEN_HINTS)

    def _has_playerless_watch_shell_sync(self, page_source: str | None = None) -> bool:
        if page_source is None:
            try:
                page_source = self._driver.page_source or ""
            except Exception:
                page_source = ""
        return self._source_has_playerless_watch_shell_sync(page_source)

    def _recover_results_surface_sync(self, query: str) -> None:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return
        if self._has_mixed_watch_results_surface_sync():
            if self._press_back_sync():
                time.sleep(1.0)
                self._dismiss_possible_dialogs_sync()
                self._dismiss_miniplayer_on_results_sync()
            if not self._has_mixed_watch_results_surface_sync() and self._has_query_results_surface_sync(query):
                return
            self._restore_results_surface_sync(query, prefer_hard_reset=True)
            return
        if self._is_loading_results_shell_sync():
            self._launch_youtube_via_adb_sync()
            time.sleep(2.0)
            self._dismiss_possible_dialogs_sync()
            self._restore_results_surface_sync(query)
            return
        if self._is_blank_youtube_shell_sync():
            self._launch_youtube_via_adb_sync()
            time.sleep(2.0)
            self._dismiss_possible_dialogs_sync()
            self._restore_results_surface_sync(query)
            return
        if self._is_watch_surface_for_query_sync(query):
            return
        if self._has_query_ready_surface_sync(query):
            return
        if self._should_force_fresh_query_surface_sync(query):
            logger.info(
                "navigator: forcing fresh query surface recovery query=%s visible_query=%s",
                query,
                self._extract_visible_query_text_sync(),
            )
            self._restore_results_surface_sync(query, prefer_hard_reset=True)
            return
        if self._has_results_surface_sync() and self._has_openable_result_sync():
            return
        if self._is_search_input_visible_sync() or self._has_search_context_sync():
            return
        if not self._is_browsing_surface_sync():
            return
        self._restore_results_surface_sync(query)

    def _restore_results_surface_sync(
        self,
        query: str,
        *,
        prefer_hard_reset: bool = False,
    ) -> bool:
        if prefer_hard_reset:
            if self._run_results_deeplink_intent_sync(query, force_stop_before_intent=True):
                logger.info(
                    "navigator: restored results via hard deeplink query=%s",
                    query,
                )
                time.sleep(2.0)
                self._dismiss_possible_dialogs_sync()
                return True
            return False
        if self._open_results_via_deeplink_sync(query):
            time.sleep(2.0)
            self._dismiss_possible_dialogs_sync()
            return True
        if self._run_results_deeplink_intent_sync(query, force_stop_before_intent=True):
            logger.info(
                "navigator: restored results via fallback hard deeplink query=%s",
                query,
            )
            time.sleep(2.0)
            self._dismiss_possible_dialogs_sync()
            return True
        return False

    def _open_search_sync(self) -> None:
        self._rejected_result_titles.clear()
        self._ensure_youtube_foreground_sync(require_interactive=True)
        self._dismiss_possible_dialogs_sync()
        if self._is_search_input_visible_sync():
            return
        candidates = self._find_search_button_candidates_sync()
        if not candidates:
            raise AndroidUiError("Failed to find explicit YouTube search button on current surface")
        for candidate in candidates:
            try:
                candidate.click()
            except Exception:
                continue
            time.sleep(1.2)
            if self._is_search_input_visible_sync():
                return
            if self._is_voice_search_surface_sync():
                self._press_back_sync()
                time.sleep(0.8)
                continue
        raise AndroidUiError("Failed to open native text search without falling into voice search")

    def _reset_to_home_sync(self) -> None:
        self._check_sync_deadline()
        self._rejected_result_titles.clear()
        self._ensure_youtube_foreground_sync(require_interactive=False)
        self._dismiss_possible_dialogs_sync()
        for _ in range(8):
            self._check_sync_deadline()
            if self._is_clean_home_surface_sync():
                return
            if self._safe_current_package_sync() != self._config.youtube_package:
                break
            if self._dismiss_miniplayer_on_results_sync():
                time.sleep(0.6)
            if self._tap_home_button_sync():
                time.sleep(0.8)
                self._dismiss_possible_dialogs_sync()
                if self._dismiss_miniplayer_on_results_sync():
                    time.sleep(0.6)
                if self._is_clean_home_surface_sync():
                    return
            if self._is_browsing_surface_sync() and not self._has_search_context_sync():
                return
            if not self._press_back_sync():
                break
            time.sleep(0.9)
            if self._safe_current_package_sync() != self._config.youtube_package:
                break
            self._dismiss_possible_dialogs_sync()
            if self._dismiss_miniplayer_on_results_sync():
                time.sleep(0.6)
            if self._tap_home_button_sync():
                time.sleep(0.8)
                self._dismiss_possible_dialogs_sync()
            if self._is_clean_home_surface_sync():
                return

        self._check_sync_deadline()
        self._force_stop_youtube_via_adb_sync()
        self._check_sync_deadline()
        time.sleep(1.0)
        self._check_sync_deadline()
        self._launch_youtube_via_adb_sync()
        self._check_sync_deadline()
        time.sleep(2.0)
        self._check_sync_deadline()
        self._dismiss_possible_dialogs_sync()
        if self._tap_home_button_sync():
            self._check_sync_deadline()
            time.sleep(1.0)
            self._check_sync_deadline()
            self._dismiss_possible_dialogs_sync()
        if self._dismiss_miniplayer_on_results_sync():
            self._check_sync_deadline()
            time.sleep(0.8)
            self._check_sync_deadline()
        if self._is_clean_home_surface_sync():
            return
        self._check_sync_deadline()
        self._ensure_youtube_foreground_sync(require_interactive=True)
        if self._tap_home_button_sync():
            self._check_sync_deadline()
            time.sleep(1.0)
            self._check_sync_deadline()
            self._dismiss_possible_dialogs_sync()

    def _submit_search_sync(self, query: str) -> None:
        self._check_sync_deadline()
        self._invalidate_results_source_cache_sync()
        self._last_tapped_result_title = None
        self._last_tapped_result_is_short = False
        self._rejected_result_titles.clear()
        self._ensure_youtube_foreground_sync(require_interactive=False)
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        self._escape_reel_surface_for_query_sync(query)
        if (
            self._safe_current_package_sync() == self._config.youtube_package
            and not self._has_search_context_for_query_sync(query)
            and not self._has_query_results_surface_sync(query)
            and not self._is_watch_surface_for_query_sync(query)
            and (
                self._is_watchwhile_activity_sync()
                or self._has_playerless_watch_shell_sync()
            )
        ):
            # Fast path: try deeplink without force-stop first — avoids full app restart
            if self._dispatch_results_deeplink_sync(query, force_stop_before_intent=False):
                return
            self._reset_to_home_sync()
            self._dismiss_possible_dialogs_sync()
            self._dismiss_miniplayer_on_results_sync()
        if self._should_force_fresh_query_surface_sync(query):
            # First try without force-stop (fast), fall back to force-stop only if needed
            if self._dispatch_results_deeplink_sync(query, force_stop_before_intent=False):
                return
            if self._dispatch_results_deeplink_sync(query, force_stop_before_intent=True):
                return
        self._prepare_for_query_navigation_sync(query)
        self._escape_reel_surface_for_query_sync(query)
        if self._should_force_fresh_query_surface_sync(query):
            if self._dispatch_results_deeplink_sync(query, force_stop_before_intent=True):
                return
        if self._has_query_ready_surface_sync(query):
            return
        if self._dispatch_results_deeplink_sync(
            query,
            force_stop_before_intent=self._should_force_stop_before_results_intent_sync(query),
        ):
            return
        if self._force_text_results_search_sync(query):
            return
        raise AndroidUiError("Failed to submit text search query from native YouTube search screen")

    def _force_text_results_search_sync(self, query: str) -> bool:
        self._ensure_youtube_foreground_sync(require_interactive=True)
        if self._is_voice_search_surface_sync():
            self._press_back_sync()
            time.sleep(0.8)
        try:
            self._open_search_sync()
            field_candidates = [
                NativeSelectorCandidate("id", value) for value in selectors.SEARCH_INPUT_IDS
            ]
            field = self._find_first_sync(field_candidates, timeout_seconds=7)
            text_entered = False
            for _ in range(3):
                try:
                    field.click()
                    time.sleep(0.4)
                    field = self._find_first_sync(field_candidates, timeout_seconds=4)
                    try:
                        field.clear()
                    except Exception:
                        pass
                    field.send_keys(query)
                    text_entered = True
                    break
                except Exception:
                    time.sleep(0.5)
                    field = self._find_first_sync(field_candidates, timeout_seconds=5)
            if not text_entered and self._input_text_via_adb_sync(query):
                text_entered = True
            if not text_entered:
                raise AndroidUiError("Failed to enter native YouTube search query text")
            time.sleep(1)
            if self._has_query_ready_surface_sync(query):
                return

            for _ in range(2):
                if self._click_exact_suggestion_sync(query):
                    if self._await_results_transition_sync():
                        return True
                    if self._has_query_ready_surface_sync(query):
                        return True
                if self._perform_search_action_sync():
                    if self._await_results_transition_sync():
                        return True
                    if self._has_query_ready_surface_sync(query):
                        return True
        except Exception:
            return self._open_results_via_deeplink_sync(query)
        return False

    def _prepare_for_query_navigation_sync(self, query: str) -> None:
        self._check_sync_deadline()
        if self._safe_current_package_sync() != self._config.youtube_package:
            return
        if self._is_watch_surface_for_query_sync(query):
            return
        if self._has_search_context_for_query_sync(query) or self._has_query_results_surface_sync(query):
            return

        if self._is_reel_watch_surface_sync():
            for _ in range(2):
                if not self._press_back_sync():
                    break
                time.sleep(0.8)
                self._dismiss_possible_dialogs_sync()
                self._dismiss_miniplayer_on_results_sync()
                if not self._is_reel_watch_surface_sync():
                    break
            if self._is_reel_watch_surface_sync() and self._tap_home_button_sync():
                time.sleep(1.0)
                self._dismiss_possible_dialogs_sync()
                self._dismiss_miniplayer_on_results_sync()
            if self._is_watch_surface_for_query_sync(query):
                return
            if self._has_search_context_for_query_sync(query) or self._has_query_results_surface_sync(query):
                return

    def _wait_for_results_sync(self, query: str | None = None) -> None:
        self._check_sync_deadline()
        self._ensure_youtube_foreground_sync(require_interactive=False)
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        self._escape_reel_surface_for_query_sync(query)
        if query:
            self._recover_results_surface_sync(query)
        # Fast path: surface already ready before entering the poll loop.
        if query and self._has_query_ready_surface_sync(query):
            return
        if self._has_results_surface_sync() and self._has_openable_result_sync():
            return
        text_fallback_attempted = False
        hard_home_recovery_attempted = False
        hard_cap = min(time.monotonic() + 45, getattr(self._thread_local, "hard_deadline", float("inf")))
        deadline = min(time.monotonic() + 12, hard_cap)
        while time.monotonic() < deadline:
            if query and self._recover_results_after_system_dialog_sync(query):
                deadline = min(max(deadline, time.monotonic() + 8), hard_cap)
                continue
            if self._escape_reel_surface_for_query_sync(query):
                deadline = min(max(deadline, time.monotonic() + 6), hard_cap)
                time.sleep(0.8)
                continue
            handled_dialog = self._dismiss_possible_dialogs_sync()
            if handled_dialog:
                deadline = min(max(deadline, time.monotonic() + 6), hard_cap)
            if self._dismiss_miniplayer_on_results_sync():
                deadline = min(max(deadline, time.monotonic() + 4), hard_cap)
            if query and self._is_blank_youtube_shell_sync():
                if self._open_results_via_deeplink_sync(query):
                    deadline = min(max(deadline, time.monotonic() + 6), hard_cap)
                    time.sleep(1.0)
                    continue
            if query and self._is_loading_results_shell_sync():
                if self._open_results_via_deeplink_sync(query):
                    deadline = min(max(deadline, time.monotonic() + 6), hard_cap)
                    time.sleep(1.0)
                    continue
            if (
                query
                and self._safe_current_package_sync() == self._config.youtube_package
                and self._is_watchwhile_activity_sync()
                and not self._is_reel_watch_surface_sync()
                and not self._is_reel_watch_surface_via_adb_sync()
                and not self._has_playerless_watch_shell_sync()
                and not self._is_search_input_visible_sync()
                and not self._is_voice_search_surface_sync()
            ):
                if self._has_query_watch_transition_sync(query):
                    return
                self._recover_results_surface_sync(query)
                deadline = min(max(deadline, time.monotonic() + 6), hard_cap)
                time.sleep(0.8)
                continue
            if (
                query
                and self._is_home_activity_sync()
                and not self._has_search_context_sync()
                and not self._has_results_surface_sync()
                and not self._is_watch_surface_for_query_sync(query)
            ):
                if not text_fallback_attempted:
                    text_fallback_attempted = True
                    if self._force_text_results_search_sync(query):
                        deadline = min(max(deadline, time.monotonic() + 8), hard_cap)
                        time.sleep(0.8)
                        continue
                if not hard_home_recovery_attempted:
                    hard_home_recovery_attempted = True
                    if self._run_results_deeplink_intent_sync(query, force_stop_before_intent=True):
                        deadline = min(max(deadline, time.monotonic() + 8), hard_cap)
                        time.sleep(0.8)
                        continue
            if (
                query
                and not text_fallback_attempted
                and self._is_browsing_surface_sync()
                and not self._has_search_context_sync()
                and not self._has_results_surface_sync()
                and not self._has_watch_surface_sync()
            ):
                text_fallback_attempted = True
                if self._force_text_results_search_sync(query):
                    deadline = min(max(deadline, time.monotonic() + 8), hard_cap)
                    time.sleep(0.8)
                    continue
            if query and self._has_query_ready_surface_sync(query):
                return
            if self._advance_past_short_only_results_sync(query):
                deadline = min(max(deadline, time.monotonic() + 6), hard_cap)
                continue
            if query:
                self._recover_results_surface_sync(query)
            if query and self._has_query_ready_surface_sync(query):
                return
            if self._is_watch_surface_for_query_sync(query):
                return
            elif self._has_results_surface_sync() and self._has_openable_result_sync():
                return
            time.sleep(1)
        if query and self._open_results_via_deeplink_sync(query):
            second_deadline = time.monotonic() + 4
            while time.monotonic() < second_deadline:
                if self._recover_results_after_system_dialog_sync(query):
                    second_deadline = max(second_deadline, time.monotonic() + 8)
                    continue
                if self._escape_reel_surface_for_query_sync(query):
                    second_deadline = max(second_deadline, time.monotonic() + 6)
                    time.sleep(0.8)
                    continue
                handled_dialog = self._dismiss_possible_dialogs_sync()
                if handled_dialog:
                    second_deadline = max(second_deadline, time.monotonic() + 6)
                if query and self._has_query_ready_surface_sync(query):
                    return
                if self._advance_past_short_only_results_sync(query):
                    second_deadline = max(second_deadline, time.monotonic() + 6)
                    continue
                if self._is_watch_surface_for_query_sync(query):
                    return
                time.sleep(1)
        raise AndroidUiError("Failed to detect native YouTube results list")

    def _has_query_ready_surface_sync(self, query: str) -> bool:
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        if self._is_watch_surface_for_query_sync(query):
            return True
        if self._provisional_watch_title_sync(query):
            return True
        return self._has_query_results_surface_sync(query)

    def _has_query_ready_surface_via_adb_sync(self, query: str) -> bool:
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return False
        for _ in range(3):
            hierarchy = self._dump_ui_hierarchy_via_adb_sync()
            if not hierarchy:
                time.sleep(0.5)
                continue
            lowered = hierarchy.casefold()
            if normalized_query not in lowered:
                time.sleep(0.5)
                continue
            has_results_container = any(
                candidate_id in hierarchy for candidate_id in selectors.RESULTS_CONTAINER_IDS
            )
            has_query_header = any(
                candidate_id in hierarchy for candidate_id in selectors.SEARCH_QUERY_HEADER_IDS
            )
            if has_results_container and has_query_header:
                return True
            time.sleep(0.5)
        return False

    def _await_results_transition_sync(self, timeout_seconds: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._is_voice_search_surface_sync():
                self._press_back_sync()
                time.sleep(0.8)
                return False
            if self._has_watch_surface_sync():
                if self._is_reel_watch_surface_sync():
                    self._reject_reel_watch_surface_sync()
                    return False
                if self._has_playerless_watch_shell_sync():
                    time.sleep(0.4)
                    continue
                return True
            if self._has_results_surface_sync():
                return True
            if self._is_search_input_visible_sync():
                time.sleep(0.4)
                continue
            time.sleep(0.4)
        return False

    def _open_first_result_sync(self, query: str | None = None) -> str | None:
        self._check_sync_deadline()
        self._ensure_youtube_foreground_sync(require_interactive=False)
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        self._escape_reel_surface_for_query_sync(query)
        # Only recover results surface if we're not already on a ready surface —
        # the unconditional recover call was adding 1-3s on every open attempt.
        if query and not self._has_query_ready_surface_sync(query):
            self._recover_results_surface_sync(query)
        if query:
            resolved_watch_title = self._resolve_current_watch_title_sync(query, timeout_seconds=1.6)
            if resolved_watch_title:
                return resolved_watch_title
            if (
                self._safe_current_package_sync() == self._config.youtube_package
                and self._is_watchwhile_activity_sync()
                and not self._is_reel_watch_surface_sync()
                and not self._is_reel_watch_surface_via_adb_sync()
                and not self._has_playerless_watch_shell_sync()
                and not self._has_search_context_sync()
            ):
                delayed_watch_title = self._await_current_watch_title_sync(
                    query,
                    timeout_seconds=2.0,
                )
                if delayed_watch_title:
                    return delayed_watch_title
                extracted_title = self._extract_current_watch_title_sync()
                if (
                    extracted_title
                    and not self._is_placeholder_result_title(extracted_title)
                    and self._is_reasonable_topic_video_title_sync(extracted_title, query)
                ):
                    return extracted_title
            if self._has_stale_previous_watch_surface_sync(query):
                self._recover_results_surface_sync(query)
                time.sleep(0.8)
        if self._is_watch_surface_for_query_sync(query) and not self._is_reel_watch_surface_sync():
            return (
                self._extract_current_watch_title_for_query_sync(query)
                or self._extract_current_watch_title_sync()
            )
        if query and self._has_provisional_watch_surface_for_query_sync(query) and not self._is_reel_watch_surface_sync():
            return (
                self._extract_current_watch_title_for_query_sync(query)
                or self._extract_current_watch_title_sync()
            )
        if self._has_watch_surface_sync() and not self._has_results_surface_sync():
            self._press_back_sync()
            time.sleep(1.0)
        for attempt_idx in range(8):
            sponsor_bounds = self._extract_current_sponsored_bounds_sync()
            if query and self._recover_results_after_system_dialog_sync(query):
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=recover_after_system_dialog",
                    query,
                    attempt_idx + 1,
                )
                time.sleep(0.8)
                continue
            if self._escape_reel_surface_for_query_sync(query):
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=escape_reel",
                    query,
                    attempt_idx + 1,
                )
                time.sleep(0.8)
                continue
            handled_dialog = self._dismiss_possible_dialogs_sync()
            if handled_dialog:
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=dialog_handled",
                    query,
                    attempt_idx + 1,
                )
                time.sleep(1.0)
                if self._is_watch_surface_for_query_sync(query):
                    if self._is_reel_watch_surface_sync():
                        self._press_back_sync()
                        time.sleep(0.8)
                        continue
                    return (
                        self._extract_current_watch_title_for_query_sync(query)
                        or self._extract_current_watch_title_sync()
                    )
                if query and self._has_provisional_watch_surface_for_query_sync(query):
                    if self._is_reel_watch_surface_sync():
                        self._press_back_sync()
                        time.sleep(0.8)
                        continue
                    return (
                        self._extract_current_watch_title_for_query_sync(query)
                        or self._extract_current_watch_title_sync()
                    )
            self._dismiss_miniplayer_on_results_sync()
            if query:
                resolved_watch_title = self._resolve_current_watch_title_sync(query, timeout_seconds=1.2)
                if resolved_watch_title:
                    return resolved_watch_title
                if self._has_stale_previous_watch_surface_sync(query):
                    logger.info(
                        "open_first_result: query=%s attempt=%s branch=stale_previous_watch",
                        query,
                        attempt_idx + 1,
                    )
                    self._recover_results_surface_sync(query)
                    time.sleep(0.8)
                    continue
            if query and self._is_blank_youtube_shell_sync():
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=blank_shell",
                    query,
                    attempt_idx + 1,
                )
                self._recover_results_surface_sync(query)
                time.sleep(0.8)
                continue
            if query:
                results_surface = self._has_results_surface_sync()
                search_context = self._has_search_context_sync()
                watch_surface = self._has_watch_surface_sync()
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=sponsor_entry results=%s search=%s watch=%s",
                    query,
                    attempt_idx + 1,
                    results_surface,
                    search_context,
                    watch_surface,
                )
                if results_surface and search_context and not watch_surface:
                    logger.info(
                        "open_first_result: query=%s attempt=%s branch=skip_recover_ready_results",
                        query,
                        attempt_idx + 1,
                    )
                else:
                    recover_started_at = time.monotonic()
                    self._recover_results_surface_sync(query)
                    logger.info(
                        "open_first_result: query=%s attempt=%s branch=post_recover seconds=%.2f results=%s search=%s watch=%s",
                        query,
                        attempt_idx + 1,
                        time.monotonic() - recover_started_at,
                        self._has_results_surface_sync(),
                        self._has_search_context_sync(),
                        self._has_watch_surface_sync(),
                    )
                playable_started_at = time.monotonic()
                playable_organic_opened = self._tap_first_playable_candidate_below_sponsor_sync(query)
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=post_playable seconds=%.2f opened=%s",
                    query,
                    attempt_idx + 1,
                    time.monotonic() - playable_started_at,
                    playable_organic_opened,
                )
                if playable_organic_opened is not None:
                    return playable_organic_opened
                titled_started_at = time.monotonic()
                titled_organic_opened = self._tap_first_title_below_sponsor_sync(query)
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=post_title seconds=%.2f opened=%s",
                    query,
                    attempt_idx + 1,
                    time.monotonic() - titled_started_at,
                    titled_organic_opened,
                )
                if titled_organic_opened is not None:
                    return titled_organic_opened
                top_region_started_at = time.monotonic()
                top_region_opened = self._tap_top_result_region_sync(query)
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=post_top_region seconds=%.2f opened=%s",
                    query,
                    attempt_idx + 1,
                    time.monotonic() - top_region_started_at,
                    top_region_opened,
                )
                if top_region_opened is not None:
                    return top_region_opened
            if self._advance_past_short_only_results_sync(query):
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=advance_past_nonorganic",
                    query,
                    attempt_idx + 1,
                )
                time.sleep(0.8)
                continue
            if self._has_watch_surface_sync() and not self._is_watch_surface_for_query_sync(query):
                if not self._has_results_surface_sync():
                    logger.info(
                        "open_first_result: query=%s attempt=%s branch=back_from_wrong_watch",
                        query,
                        attempt_idx + 1,
                    )
                    self._press_back_sync()
                    time.sleep(1.0)
                    continue
            if not self._has_results_surface_sync():
                logger.info(
                    "open_first_result: query=%s attempt=%s branch=no_results_surface",
                    query,
                    attempt_idx + 1,
                )
                time.sleep(0.8)
                continue

            for overlap_only in (True, False):
                for candidate in self._rank_result_candidates_sync(
                    query=query,
                    require_overlap=overlap_only,
                ):
                    if self._tap_result_candidate_sync(candidate, query):
                        return candidate.title

                ranked_elements: list[tuple[float, str, object]] = []
                for candidate_id in selectors.RESULT_TITLE_IDS:
                    elements = self._driver.find_elements("id", candidate_id)
                    for element in elements:
                        try:
                            text = (getattr(element, "text", "") or "").strip()
                        except Exception:
                            continue
                        if self._should_skip_result_title_for_query_sync(text, query):
                            continue
                        if self._is_element_short_result_sync(element):
                            continue
                        if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                            continue
                        if overlap_only and query and not self._titles_overlap_sync(text, query):
                            continue
                        if (
                            not overlap_only
                            and query
                            and not self._is_reasonable_topic_video_title_sync(text, query)
                        ):
                            continue
                        ranked_elements.append(
                            (
                                self._score_result_title_for_query_sync(text, query),
                                text,
                                element,
                            )
                        )
                ranked_elements.sort(key=lambda item: item[0], reverse=True)
                for _score, text, element in ranked_elements:
                    if self._try_open_result_element_sync(element, text, query):
                        return text

                ranked_cards: list[tuple[float, str, object]] = []
                for element in self._find_video_result_cards_sync():
                    title = self._extract_result_title_sync(element)
                    if self._should_skip_result_title_for_query_sync(title, query):
                        continue
                    if self._is_element_short_result_sync(element):
                        continue
                    if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                        continue
                    if overlap_only and query and not self._titles_overlap_sync(title, query):
                        continue
                    if (
                        not overlap_only
                        and query
                        and not self._is_reasonable_topic_video_title_sync(title, query)
                    ):
                        continue
                    ranked_cards.append(
                        (
                            self._score_result_title_for_query_sync(title, query),
                            title,
                            element,
                        )
                    )
                ranked_cards.sort(key=lambda item: item[0], reverse=True)
                for _score, title, element in ranked_cards:
                    if self._try_open_result_element_sync(element, title, query):
                        return title

            if query is None:
                top_result_opened = self._tap_top_result_region_sync(query)
                if top_result_opened is not None:
                    return top_result_opened

            for candidate_id in selectors.RESULT_TITLE_IDS:
                elements = self._driver.find_elements("id", candidate_id)
                for element in elements:
                    try:
                        text = (getattr(element, "text", "") or "").strip()
                    except Exception:
                        continue
                    if self._should_skip_result_title_for_query_sync(text, query):
                        continue
                    if self._is_element_short_result_sync(element):
                        continue
                    if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                        continue
                    if query and not self._titles_overlap_sync(text, query):
                        continue
                    if self._try_open_result_element_sync(element, text, query):
                        return text

            self._scroll_results_feed_once_sync()
            time.sleep(1.0)
        return None

    def _escape_reel_surface_for_query_sync(self, query: str | None) -> bool:
        if not (
            self._is_reel_watch_surface_sync()
            or self._is_reel_watch_surface_via_adb_sync()
        ):
            return False
        rejected = self._reject_reel_watch_surface_sync()
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        if query:
            try:
                opened = self._open_results_via_deeplink_sync(query)
            except Exception:
                opened = False
            if opened:
                time.sleep(1.0)
        return rejected

    def _resolve_current_watch_title_sync(
        self,
        query: str,
        *,
        timeout_seconds: float,
    ) -> str | None:
        if self._is_reel_watch_surface_sync() or self._is_reel_watch_surface_via_adb_sync():
            return None
        if self._has_playerless_watch_shell_sync():
            return None
        if not self._has_stable_watch_surface_sync() or self._has_results_surface_sync():
            return None
        return self._await_current_watch_title_sync(query, timeout_seconds=timeout_seconds)

    def _tap_first_organic_region_below_sponsor_sync(self, query: str) -> bool:
        if not self._adb_serial:
            return False
        results_bounds = self._extract_results_bounds_sync()
        if results_bounds is None:
            return False
        sponsor_button_bounds = self._extract_sponsor_cta_bounds_sync()
        if not sponsor_button_bounds:
            return False
        _, _, results_right, results_bottom = results_bounds
        sponsor_bottom = max(bounds[3] for bounds in sponsor_button_bounds)
        candidate_y = min(results_bottom - 180, sponsor_bottom + 260)
        left_x = int(results_right * 0.28)
        right_x = int(results_right * 0.72)
        for x in (left_x, right_x):
            if not self._tap_via_adb_sync(x, candidate_y):
                continue
            if self._wait_for_watch_surface_sync(
                query=query,
                timeout_seconds=8.0,
                allow_heavy_dialog_recovery=False,
            ):
                return True
            time.sleep(0.6)
        return False

    def _tap_first_title_below_sponsor_sync(self, query: str) -> str | None:
        page_source = self._preferred_results_page_source_sync()
        if not page_source:
            return None
        root = self._parse_xml_root_sync(page_source)
        if root is None:
            return None

        sponsor_bounds = self._extract_current_sponsored_bounds_sync()
        if not sponsor_bounds:
            sponsor_bounds = self._extract_sponsor_cta_bounds_sync()
        sponsor_bottom = max((bounds[3] for bounds in sponsor_bounds), default=0)
        results_bounds = self._extract_results_bounds_sync()
        results_left = results_bounds[0] if results_bounds is not None else 0
        results_right = results_bounds[2] if results_bounds is not None else 1080
        results_bottom = results_bounds[3] if results_bounds is not None else 2400
        short_bounds = self._extract_short_result_bounds_sync()

        candidates: list[NativeResultCandidate] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if resource_id not in selectors.RESULT_TITLE_IDS:
                continue
            text = (node.attrib.get("text") or "").strip()
            if self._should_skip_result_title_for_query_sync(text, query):
                continue
            if not self._titles_overlap_sync(text, query):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if any(self._bounds_overlap_sync(bounds, short_bound) for short_bound in short_bounds):
                continue
            left, top, right, bottom = bounds
            if top <= sponsor_bottom:
                continue
            tap_bounds = (
                max(results_left, left - 24),
                max(sponsor_bottom + 12, top - 120),
                min(results_right, max(right + 24, results_right - 32)),
                min(results_bottom, bottom + 220),
            )
            candidate = NativeResultCandidate(
                title=text,
                bounds=tap_bounds,
                is_short=any(
                    self._bounds_overlap_sync(tap_bounds, short_bound)
                    for short_bound in short_bounds
                ),
                is_sponsored=False,
            )
            key = (candidate.title, candidate.bounds)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

        candidates.sort(key=lambda item: (item.bounds[1], item.bounds[0]))
        candidates.sort(
            key=lambda item: (
                item.bounds[1],
                -self._score_result_title_for_query_sync(item.title, query),
                item.bounds[0],
            )
        )
        for candidate in candidates:
            if self._should_skip_result_title_for_query_sync(candidate.title, query):
                continue
            if self._tap_result_candidate_sync(candidate, query):
                return candidate.title
        return None

    def _tap_first_playable_candidate_below_sponsor_sync(self, query: str) -> str | None:
        logger.info("sponsor_fallback_enter: query=%s", query)
        cta_started_at = time.monotonic()
        cta_bounds = self._extract_sponsor_cta_bounds_sync()
        logger.info(
            "sponsor_fallback_step: query=%s step=cta_bounds seconds=%.2f count=%s",
            query,
            time.monotonic() - cta_started_at,
            len(cta_bounds),
        )
        sponsor_started_at = time.monotonic()
        sponsor_bounds = self._extract_current_sponsored_bounds_sync()
        logger.info(
            "sponsor_fallback_step: query=%s step=sponsor_bounds seconds=%.2f count=%s",
            query,
            time.monotonic() - sponsor_started_at,
            len(sponsor_bounds),
        )
        candidate_cutoff = max(
            (
                bounds[3]
                for bounds in (
                    cta_bounds
                    if cta_bounds
                    else sponsor_bounds
                )
            ),
            default=0,
        )

        raw_playable_started_at = time.monotonic()
        raw_playable_candidates = self._extract_result_candidates_from_page_source_sync()
        logger.info(
            "sponsor_fallback_step: query=%s step=raw_playable seconds=%.2f count=%s",
            query,
            time.monotonic() - raw_playable_started_at,
            len(raw_playable_candidates),
        )
        raw_text_started_at = time.monotonic()
        raw_text_candidates = self._extract_text_result_candidates_from_page_source_sync(query)
        logger.info(
            "sponsor_fallback_step: query=%s step=raw_text seconds=%.2f count=%s",
            query,
            time.monotonic() - raw_text_started_at,
            len(raw_text_candidates),
        )
        logger.info(
            "sponsor_fallback_scan: query=%s cutoff=%s cta_bounds=%s sponsor_bounds=%s raw_playable=%s raw_text=%s",
            query,
            candidate_cutoff,
            cta_bounds[:4],
            sponsor_bounds[:4],
            [
                (candidate.title, candidate.bounds, candidate.is_sponsored, candidate.is_short)
                for candidate in raw_playable_candidates[:4]
            ],
            [
                (candidate.title, candidate.bounds, candidate.is_sponsored, candidate.is_short)
                for candidate in raw_text_candidates[:4]
            ],
        )

        candidates: list[NativeResultCandidate] = []
        relaxed_cutoff_used = False
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for candidate in (
            *raw_playable_candidates,
            *raw_text_candidates,
        ):
            key = (candidate.title, candidate.bounds)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_short or candidate.is_sponsored:
                continue
            if self._should_skip_result_title_for_query_sync(candidate.title, query):
                continue
            title_overlap = self._titles_overlap_sync(candidate.title, query)
            title_score = self._score_result_title_for_query_sync(candidate.title, query)
            if not title_overlap and title_score < 5.0:
                continue
            if cta_bounds and candidate.bounds[1] <= candidate_cutoff + 24:
                continue
            candidates.append(candidate)

        if not candidates and not cta_bounds:
            relaxed_candidates: list[NativeResultCandidate] = []
            seen_relaxed: set[tuple[str, tuple[int, int, int, int]]] = set()
            for candidate in (
                *raw_playable_candidates,
                *raw_text_candidates,
            ):
                key = (candidate.title, candidate.bounds)
                if key in seen_relaxed:
                    continue
                seen_relaxed.add(key)
                if candidate.is_short or candidate.is_sponsored:
                    continue
                if self._should_skip_result_title_for_query_sync(candidate.title, query):
                    continue
                title_overlap = self._titles_overlap_sync(candidate.title, query)
                title_score = self._score_result_title_for_query_sync(candidate.title, query)
                if not title_overlap and title_score < 5.0:
                    continue
                relaxed_candidates.append(candidate)
            if relaxed_candidates:
                logger.info(
                    "sponsor_fallback_relaxed_cutoff: query=%s cutoff=%s candidates=%s",
                    query,
                    candidate_cutoff,
                    [
                        (candidate.title, candidate.bounds)
                        for candidate in relaxed_candidates[:4]
                    ],
                )
                candidates = relaxed_candidates
                relaxed_cutoff_used = True

        if not candidates and not cta_bounds:
            surface_candidates: list[NativeResultCandidate] = []
            seen_surface: set[tuple[str, tuple[int, int, int, int]]] = set()
            for candidate in (
                *raw_playable_candidates,
                *raw_text_candidates,
            ):
                key = (candidate.title, candidate.bounds)
                if key in seen_surface:
                    continue
                seen_surface.add(key)
                if candidate.is_sponsored:
                    continue
                if self._should_skip_result_title_for_query_sync(candidate.title, query):
                    continue
                surface_candidates.append(candidate)
            if surface_candidates:
                surface_candidates.sort(key=lambda item: (item.is_short, item.bounds[1], item.bounds[0]))
                logger.info(
                    "sponsor_fallback_surface_order: query=%s candidates=%s",
                    query,
                    [
                        (candidate.title, candidate.bounds)
                        for candidate in surface_candidates[:4]
                    ],
                )
                candidates = surface_candidates
                relaxed_cutoff_used = True

        if not candidates:
            any_search_candidates: list[NativeResultCandidate] = []
            seen_any_search: set[tuple[str, tuple[int, int, int, int]]] = set()
            for candidate in (
                *raw_playable_candidates,
                *raw_text_candidates,
            ):
                key = (candidate.title, candidate.bounds)
                if key in seen_any_search:
                    continue
                seen_any_search.add(key)
                if self._should_skip_result_title_for_query_sync(candidate.title, query):
                    continue
                any_search_candidates.append(candidate)
            if any_search_candidates:
                any_search_candidates.sort(
                    key=lambda item: (
                        item.is_short,
                        item.is_sponsored,
                        item.bounds[1],
                        item.bounds[0],
                    )
                )
                logger.info(
                    "sponsor_fallback_any_search: query=%s candidates=%s",
                    query,
                    [
                        (candidate.title, candidate.bounds, candidate.is_sponsored, candidate.is_short)
                        for candidate in any_search_candidates[:4]
                    ],
                )
                candidates = any_search_candidates
                relaxed_cutoff_used = True

        logger.info(
            "sponsor_fallback_filtered: query=%s candidates=%s",
            query,
            [
                (candidate.title, candidate.bounds)
                for candidate in candidates[:4]
            ],
        )
        candidates.sort(
            key=lambda item: (
                -self._score_result_title_for_query_sync(item.title, query),
                item.bounds[1],
                item.bounds[0],
            )
        )
        for candidate in candidates:
            logger.info(
                "sponsor_fallback: query=%s cutoff=%s candidate=%s bounds=%s",
                query,
                candidate_cutoff,
                candidate.title,
                candidate.bounds,
            )
            opened_title = self._tap_result_candidate_hotspots_sync(
                candidate,
                query,
                prefer_center_first=relaxed_cutoff_used and not candidate.is_sponsored,
                allow_offtopic_result=True,
                allow_reel_result=relaxed_cutoff_used and candidate.is_short,
            )
            if opened_title is not None:
                return opened_title
        return None

    def _tap_result_candidate_hotspots_sync(
        self,
        candidate: NativeResultCandidate,
        query: str,
        *,
        prefer_center_first: bool = False,
        allow_offtopic_result: bool = False,
        allow_reel_result: bool = False,
    ) -> str | None:
        self._last_tapped_result_title = candidate.title
        self._last_tapped_result_is_short = candidate.is_short

        left, top, right, bottom = candidate.bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        tap_points: list[tuple[int, int]] = []
        if prefer_center_first:
            tap_points.append((int(left + width * 0.45), int(top + height * 0.35)))
        tap_points.extend([
            (int(left + width * 0.28), int(top + height * 0.22)),
            (int(left + width * 0.72), int(top + height * 0.22)),
            (int(left + width * 0.34), int(top + height * 0.76)),
            (int(left + width * 0.66), int(top + height * 0.76)),
            (int(left + width * 0.50), int(top + height * 0.32)),
        ])
        seen_points: set[tuple[int, int]] = set()
        for x, y in tap_points:
            point = (x, y)
            if point in seen_points:
                continue
            seen_points.add(point)
            logger.info(
                "sponsor_fallback_tap_attempt: title=%s point=(%s,%s)",
                candidate.title,
                x,
                y,
            )
            tap_sent = self._tap_via_adb_sync(x, y)
            logger.info(
                "sponsor_fallback_tap_sent: title=%s point=(%s,%s) sent=%s",
                candidate.title,
                x,
                y,
                tap_sent,
            )
            if not tap_sent:
                continue
            opened = self._await_watch_open_after_tap_sync(query=query, timeout_seconds=7.5)
            logger.info(
                "sponsor_fallback_tap: title=%s point=(%s,%s) opened=%s",
                candidate.title,
                x,
                y,
                opened,
            )
            if opened:
                is_reel_result = (
                    self._is_reel_watch_surface_sync()
                    or self._is_reel_watch_surface_via_adb_sync()
                )
                if is_reel_result and allow_reel_result:
                    return candidate.title
                if self._reject_reel_watch_surface_sync():
                    self._recover_results_surface_sync(query)
                    time.sleep(0.8)
                    continue
                resolved_title = (
                    self._extract_current_watch_title_for_query_sync(query)
                    or self._extract_current_watch_title_sync()
                )
                resolved_title_matches_query = bool(
                    query
                    and resolved_title
                    and (
                        self._titles_overlap_sync(resolved_title, query)
                        or self._is_reasonable_topic_video_title_sync(resolved_title, query)
                    )
                )
                candidate_title_matches_query = bool(
                    query
                    and (
                        self._titles_overlap_sync(candidate.title, query)
                        or self._is_reasonable_topic_video_title_sync(candidate.title, query)
                    )
                )
                if (
                    resolved_title
                    and not resolved_title_matches_query
                    and candidate_title_matches_query
                ):
                    logger.info(
                        "sponsor_fallback_keep_candidate_title: candidate=%s resolved=%s",
                        candidate.title,
                        resolved_title,
                    )
                    return candidate.title
                if (
                    query
                    and resolved_title
                    and not allow_offtopic_result
                    and not resolved_title_matches_query
                ):
                    logger.info(
                        "sponsor_fallback_reject_opened: candidate=%s resolved=%s",
                        candidate.title,
                        resolved_title,
                    )
                    self._recover_results_surface_sync(query)
                    time.sleep(0.8)
                    continue
                return resolved_title or candidate.title
            if not self._has_results_surface_sync():
                self._recover_results_surface_sync(query)
                time.sleep(0.8)
            else:
                time.sleep(0.6)
        return None

    def _describe_surface_sync(self) -> tuple[str | None, str | None, int]:
        page_source_length = 0
        try:
            page_source_length = len(self._driver.page_source or "")
        except Exception:
            page_source_length = 0
        try:
            package = self._driver.current_package
        except Exception:
            package = None
        try:
            activity = self._driver.current_activity
        except Exception:
            activity = None
        return package, activity, page_source_length

    def _await_current_watch_title_sync(
        self,
        query: str | None = None,
        timeout_seconds: float = 4.0,
    ) -> str | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if query and self._last_tapped_result_is_short:
                if self._has_watch_surface_sync() and not self._has_results_surface_sync():
                    self._press_back_sync()
                    time.sleep(0.6)
                    self._dismiss_possible_dialogs_sync()
                    self._dismiss_miniplayer_on_results_sync()
                return None
            if query and (
                self._is_reel_watch_surface_sync()
                or self._is_reel_watch_surface_via_adb_sync()
            ):
                self._reject_reel_watch_surface_sync()
                return None
            if self._wait_for_watch_surface_sync(query=query, timeout_seconds=1.6):
                title = (
                    self._extract_current_watch_title_for_query_sync(query)
                    if query
                    else self._extract_current_watch_title_sync()
                )
                if title:
                    return title
                if (
                    query
                    and not self._has_playerless_watch_shell_sync()
                    and self._last_tapped_result_title
                    and not self._last_tapped_result_is_short
                    and not self._is_rejected_result_title_sync(self._last_tapped_result_title)
                    and self._titles_overlap_sync(self._last_tapped_result_title, query)
                ):
                    return self._last_tapped_result_title
                if (
                    query
                    and self._is_watchwhile_activity_sync()
                    and self._has_watch_surface_sync()
                    and self._last_tapped_title_matches_query_sync(query)
                ):
                    return self._last_tapped_result_title
            time.sleep(0.4)
        if (
            query
            and self._has_watch_surface_sync()
        ):
            if (
                not self._has_results_surface_sync()
                and not self._is_reel_watch_surface_sync()
                and not self._is_reel_watch_surface_via_adb_sync()
                and not self._has_playerless_watch_shell_sync()
                and not self._last_tapped_result_is_short
            ):
                return (
                    self._extract_current_watch_title_for_query_sync(query)
                    or (
                        None
                        if self._is_rejected_result_title_sync(self._last_tapped_result_title)
                        else self._last_tapped_result_title
                    )
                )
            if self._is_watchwhile_activity_sync() and self._last_tapped_title_matches_query_sync(query):
                return self._last_tapped_result_title
        return None

    def _dismiss_possible_dialogs_sync(self, *, allow_heavy_adb: bool = True) -> bool:
        handled_any = False
        for _ in range(4):
            self._check_sync_deadline()
            handled = False
            handled = self._dismiss_system_dialog_sync(allow_heavy_adb=allow_heavy_adb) or handled
            if allow_heavy_adb and self._has_system_dialog_overlay_via_adb_sync():
                if handled:
                    handled_any = True
                    time.sleep(0.8)
                return handled_any
            handled = self._dismiss_permission_dialog_sync() or handled
            handled = self._dismiss_generic_dialog_sync() or handled
            if not handled:
                return handled_any
            handled_any = True
            time.sleep(0.8)
        return handled_any

    def _dismiss_system_dialog_sync(self, *, allow_heavy_adb: bool = True) -> bool:
        if allow_heavy_adb and self._dismiss_system_dialogs_via_adb_sync():
            return True
        if allow_heavy_adb and self._has_system_dialog_overlay_via_adb_sync():
            return False
        if self._adb_serial and allow_heavy_adb:
            return False
        for candidate_id in selectors.SYSTEM_DIALOG_WAIT_IDS:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements[:1]:
                try:
                    element.click()
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
                    return True
                except Exception:
                    continue
        if not self._has_system_dialog_overlay_sync():
            return self._dismiss_system_dialogs_via_adb_sync() if allow_heavy_adb else False
        if self._tap_system_dialog_wait_via_adb_sync():
            time.sleep(0.8)
            return True
        if allow_heavy_adb and self._tap_system_dialog_wait_via_adb_dump_sync():
            time.sleep(0.8)
            return True
        return False

    def _dismiss_system_dialogs_via_adb_sync(self) -> bool:
        if not self._adb_serial:
            return False
        if not self._wait_for_adb_device_sync(timeout_seconds=6.0):
            return False
        if self._tap_launcher_close_via_adb_dump_sync():
            self._wait_for_adb_device_sync(timeout_seconds=8.0)
            time.sleep(0.8)
            return True
        if self._tap_system_dialog_wait_via_adb_dump_sync():
            self._wait_for_adb_device_sync(timeout_seconds=8.0)
            time.sleep(0.8)
            return True
        if not self._has_system_dialog_overlay_via_adb_sync():
            return False
        if self._tap_launcher_close_via_adb_sync():
            self._wait_for_adb_device_sync(timeout_seconds=8.0)
            time.sleep(0.8)
            return True
        if self._tap_system_dialog_wait_via_adb_sync():
            self._wait_for_adb_device_sync(timeout_seconds=8.0)
            time.sleep(0.8)
            return True
        if self._tap_launcher_close_via_adb_dump_sync():
            self._wait_for_adb_device_sync(timeout_seconds=8.0)
            time.sleep(0.8)
            return True
        if self._tap_system_dialog_wait_via_adb_dump_sync():
            self._wait_for_adb_device_sync(timeout_seconds=8.0)
            time.sleep(0.8)
            return True
        return False

    def _has_system_dialog_overlay_sync(self) -> bool:
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        lowered = page_source.casefold()
        if not lowered:
            return False
        return any(hint.casefold() in lowered for hint in selectors.SYSTEM_DIALOG_TITLE_HINTS)

    def _has_system_dialog_overlay_via_adb_sync(self) -> bool:
        if not self._adb_serial:
            return False
        if not self._wait_for_adb_device_sync(timeout_seconds=3.0):
            return False
        adb_bin = require_tool_path("adb")
        result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "dumpsys",
                "window",
                "windows",
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False
        lowered = f"{result.stdout}\n{result.stderr}".casefold()
        if not lowered:
            return False
        return (
            "application not responding" in lowered
            or any(hint.casefold() in lowered for hint in selectors.SYSTEM_DIALOG_TITLE_HINTS)
        )

    def _tap_system_dialog_wait_via_adb_sync(self) -> bool:
        if not self._adb_serial:
            return False
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            return False
        if not page_source:
            return False
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(page_source)
        except Exception:
            return False
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            if (
                resource_id not in selectors.SYSTEM_DIALOG_WAIT_IDS
                and text not in selectors.SYSTEM_DIALOG_WAIT_TEXTS
            ):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            return self._tap_via_adb_sync(center_x, center_y)
        return False

    def _tap_system_dialog_wait_via_adb_dump_sync(self) -> bool:
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return False
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(hierarchy)
        except Exception:
            return False
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            content_desc = (node.attrib.get("content-desc") or "").strip()
            if (
                resource_id not in selectors.SYSTEM_DIALOG_WAIT_IDS
                and text not in selectors.SYSTEM_DIALOG_WAIT_TEXTS
                and content_desc not in selectors.SYSTEM_DIALOG_WAIT_TEXTS
            ):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            if self._tap_via_adb_sync(center_x, center_y):
                return True
        return False

    def _tap_launcher_close_via_adb_sync(self) -> bool:
        if not self._adb_serial:
            return False
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            return False
        if "pixel launcher isn't responding" not in page_source.casefold():
            return False
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(page_source)
        except Exception:
            return False
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            if (
                resource_id not in selectors.SYSTEM_DIALOG_CLOSE_IDS
                and text not in selectors.SYSTEM_DIALOG_CLOSE_TEXTS
            ):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            return self._tap_via_adb_sync(center_x, center_y)
        return False

    def _tap_launcher_close_via_adb_dump_sync(self) -> bool:
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy or "pixel launcher isn't responding" not in hierarchy.casefold():
            return False
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(hierarchy)
        except Exception:
            return False
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            content_desc = (node.attrib.get("content-desc") or "").strip()
            if (
                resource_id not in selectors.SYSTEM_DIALOG_CLOSE_IDS
                and text not in selectors.SYSTEM_DIALOG_CLOSE_TEXTS
                and content_desc not in selectors.SYSTEM_DIALOG_CLOSE_TEXTS
            ):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            if self._tap_via_adb_sync(center_x, center_y):
                return True
        return False

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

    def _dump_ui_hierarchy_via_adb_sync(self) -> str | None:
        if not self._adb_serial:
            return None
        adb_bin = require_tool_path("adb")
        dump_path = "/sdcard/codex_window_dump.xml"
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
            timeout=40,
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
            timeout=40,
        )
        if cat_result.returncode != 0:
            return None
        return cat_result.stdout or None

    def _recover_results_after_system_dialog_sync(self, query: str) -> bool:
        if not (
            self._has_system_dialog_overlay_sync()
            or self._has_system_dialog_overlay_via_adb_sync()
        ):
            return False
        if not (
            self._dismiss_system_dialog_sync()
            or self._dismiss_system_dialogs_via_adb_sync()
        ):
            return False
        time.sleep(1.4)
        self._dismiss_miniplayer_on_results_sync()
        self._launch_youtube_via_adb_sync()
        time.sleep(2.0)
        self._dismiss_possible_dialogs_sync()
        if self._open_results_via_deeplink_sync(query):
            time.sleep(2.0)
            self._dismiss_possible_dialogs_sync()
        return True

    def _dismiss_permission_dialog_sync(self) -> bool:
        if self._safe_current_package_sync() != "com.google.android.permissioncontroller":
            return False

        for candidate in (
            *(NativeSelectorCandidate("id", value) for value in selectors.PERMISSION_DENY_IDS),
            *(
                NativeSelectorCandidate("text_contains", value)
                for value in selectors.PERMISSION_DENY_TEXTS
            ),
            *(NativeSelectorCandidate("id", value) for value in selectors.PERMISSION_ALLOW_IDS),
            *(
                NativeSelectorCandidate("text_contains", value)
                for value in selectors.PERMISSION_ALLOW_TEXTS
            ),
        ):
            elements = self._find_candidate_sync(candidate)
            if not elements:
                continue
            try:
                elements[0].click()
                return True
            except Exception:
                continue
        return False

    def _tap_text_candidates_from_source_sync(
        self,
        source: str,
        texts: tuple[str, ...],
    ) -> bool:
        if not source or not self._adb_serial:
            return False
        normalized = tuple(text.casefold() for text in texts if text.strip())
        if not normalized:
            return False
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(source)
        except Exception:
            return False
        for node in root.iter():
            self._check_sync_deadline()
            raw_values = (
                (node.attrib.get("text") or "").strip(),
                (node.attrib.get("content-desc") or "").strip(),
            )
            if not any(
                any(candidate in raw_value.casefold() for candidate in normalized)
                for raw_value in raw_values
                if raw_value
            ):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            if self._tap_via_adb_sync(center_x, center_y):
                return True
        return False

    def _dismiss_generic_dialog_sync(self) -> bool:
        self._check_sync_deadline()
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        lowered = page_source.casefold()
        has_results_markup = any(
            candidate_id in page_source for candidate_id in selectors.RESULTS_CONTAINER_IDS
        )
        has_query_header_markup = any(
            candidate_id in page_source for candidate_id in selectors.SEARCH_QUERY_HEADER_IDS
        )
        if any(hint.casefold() in lowered for hint in selectors.SURVEY_MODAL_HINTS):
            for hint in selectors.SURVEY_CLOSE_DESCRIPTION_HINTS:
                try:
                    elements = self._driver.find_elements("accessibility id", hint)
                except Exception:
                    elements = []
                for element in elements[:1]:
                    try:
                        element.click()
                        return True
                    except Exception:
                        continue
            if self._press_back_sync():
                return True
        if has_results_markup and has_query_header_markup:
            return False
        dismiss_texts = tuple(
            text
            for text in selectors.DISMISS_TEXTS
            if text.casefold() in lowered
            and text.casefold() not in {"dismiss", "skip", "закрыть", "пропустить"}
        )
        if not dismiss_texts:
            return False
        if self._tap_text_candidates_from_source_sync(page_source, dismiss_texts):
            time.sleep(0.5)
            return True
        for text in dismiss_texts:
            self._check_sync_deadline()
            elements = self._driver.find_elements(
                "-android uiautomator",
                f'new UiSelector().textContains("{text}")',
            )
            for element in elements[:1]:
                try:
                    element.click()
                    return True
                except Exception:
                    continue
        return False

    def _safe_current_package_sync(self) -> str | None:
        try:
            return self._driver.current_package
        except Exception as exc:
            if is_dead_appium_session_error(exc):
                raise AndroidUiError(f"Appium session died while reading current package: {exc}") from exc
            return None

    def _safe_current_activity_sync(self) -> str | None:
        try:
            return self._driver.current_activity
        except Exception as exc:
            if is_dead_appium_session_error(exc):
                raise AndroidUiError(f"Appium session died while reading current activity: {exc}") from exc
            return None

    def _launch_youtube_via_adb_sync(self) -> None:
        adb_bin = require_tool_path("adb")
        if not self._adb_serial:
            raise AndroidUiError("ADB serial is required for adb-based app launch")
        monkey_result = subprocess.run(
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
            timeout=60,
        )
        monkey_stdout = monkey_result.stdout.strip()
        monkey_stderr = monkey_result.stderr.strip()
        if monkey_result.returncode == 0:
            time.sleep(1.5)
            if self._wait_for_youtube_package_sync(timeout_seconds=4):
                return
        component = self._resolve_launchable_activity_sync().replace("$", "\\$")
        result = subprocess.run(
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
            timeout=60,
        )
        if result.returncode == 0:
            time.sleep(1.5)
            if self._wait_for_youtube_package_sync(timeout_seconds=4):
                return
        stderr = result.stderr.strip() or result.stdout.strip()
        monkey_summary = monkey_stderr or monkey_stdout
        if result.returncode != 0 or monkey_summary:
            raise AndroidUiError(
                f"adb start launch failed: {stderr or '<no output>'} | monkey launch: {monkey_summary or '<no output>'}"
            )

    def _force_stop_youtube_via_adb_sync(self) -> None:
        if not self._adb_serial:
            return
        adb_bin = require_tool_path("adb")
        subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "am",
                "force-stop",
                self._config.youtube_package,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )

    def _force_stop_package_via_adb_sync(self, package_name: str) -> None:
        if not self._adb_serial:
            return
        adb_bin = require_tool_path("adb")
        subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "am",
                "force-stop",
                package_name,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )

    def _recover_from_launcher_anr_sync(self) -> bool:
        if not self._adb_serial:
            return False
        if not self._wait_for_adb_device_sync(timeout_seconds=8.0):
            return False
        self._force_stop_package_via_adb_sync("com.google.android.apps.nexuslauncher")
        time.sleep(0.8)
        if not self._wait_for_adb_device_sync(timeout_seconds=8.0):
            return False
        try:
            self._launch_youtube_via_adb_sync()
        except Exception:
            return False
        time.sleep(1.8)
        self._dismiss_possible_dialogs_sync()
        return self._safe_current_package_sync() == self._config.youtube_package

    def _resolve_launchable_activity_sync(self) -> str:
        adb_bin = require_tool_path("adb")
        if not self._adb_serial:
            raise AndroidUiError("ADB serial is required for launchable activity resolve")
        result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                self._config.youtube_package,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise AndroidUiError(result.stderr.strip() or result.stdout.strip())
        for line in reversed(result.stdout.splitlines()):
            candidate = line.strip()
            if candidate.startswith(f"{self._config.youtube_package}/"):
                return candidate
        return f"{self._config.youtube_package}/{self._config.youtube_activity}"

    def _open_results_via_deeplink_sync(self, query: str) -> bool:
        if not self._adb_serial:
            return False
        if self._is_watch_surface_for_query_sync(query):
            return True
        if self._has_query_results_surface_sync(query):
            return True
        force_stop_before_intent = self._should_force_stop_before_results_intent_sync(query)
        if self._run_results_deeplink_intent_sync(
            query,
            force_stop_before_intent=force_stop_before_intent,
        ):
            return True
        if (
            not force_stop_before_intent
            and self._should_force_stop_before_results_intent_sync(query)
            and self._run_results_deeplink_intent_sync(query, force_stop_before_intent=True)
        ):
            return True
        return False

    def _dispatch_results_deeplink_sync(
        self,
        query: str,
        *,
        force_stop_before_intent: bool,
    ) -> bool:
        if not self._adb_serial:
            return False
        self._invalidate_results_source_cache_sync()
        adb_bin = require_tool_path("adb")
        deep_link = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        command = [
            adb_bin,
            "-s",
            self._adb_serial,
            "shell",
            "am",
            "start",
        ]
        if force_stop_before_intent:
            command.append("-S")
        command.extend(
            [
                "-a",
                "android.intent.action.VIEW",
                "-d",
                deep_link,
                self._config.youtube_package,
            ]
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=4,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _should_force_stop_before_results_intent_sync(self, query: str) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if self._is_watch_surface_for_query_sync(query):
            return False
        if self._is_reel_watch_surface_sync():
            return True
        return (
            self._has_watch_surface_sync()
            and not self._has_results_surface_sync()
            and not self._has_search_context_sync()
        )

    def _run_results_deeplink_intent_sync(
        self,
        query: str,
        *,
        force_stop_before_intent: bool,
    ) -> bool:
        if not self._dispatch_results_deeplink_sync(
            query,
            force_stop_before_intent=force_stop_before_intent,
        ):
            return False
        deadline = time.monotonic() + (6.0 if force_stop_before_intent else 4.5)
        while time.monotonic() < deadline:
            if self._safe_current_package_sync() != self._config.youtube_package:
                time.sleep(0.4)
                continue
            self._dismiss_possible_dialogs_sync()
            self._dismiss_miniplayer_on_results_sync()
            if self._has_query_ready_surface_sync(query):
                return True
            time.sleep(0.4)
        return False

    def _await_query_ready_surface_sync(self, query: str, *, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._dismiss_possible_dialogs_sync()
            self._dismiss_miniplayer_on_results_sync()
            if self._has_query_ready_surface_sync(query):
                return True
            time.sleep(0.5)
        return False

    def _click_exact_suggestion_sync(self, query: str) -> bool:
        normalized_query = query.strip().lower()
        if not normalized_query:
            return False
        for candidate_id in selectors.SEARCH_SUGGESTION_TEXT_IDS:
            elements = self._driver.find_elements("id", candidate_id)
            for element in elements:
                try:
                    text = (getattr(element, "text", "") or "").strip().lower()
                except Exception:
                    continue
                if text != normalized_query:
                    continue
                try:
                    element.click()
                    return True
                except Exception:
                    continue
        for selector in (
            f'new UiSelector().text("{query}")',
            f'new UiSelector().textContains("{query}")',
            f'new UiSelector().description("{query}")',
            f'new UiSelector().descriptionContains("{query}")',
        ):
            try:
                elements = self._driver.find_elements("-android uiautomator", selector)
            except Exception:
                continue
            for element in elements:
                try:
                    element.click()
                    return True
                except Exception:
                    continue
        return False

    def _perform_search_action_sync(self) -> bool:
        action_candidates = (
            lambda: self._driver.execute_script(
                "mobile: performEditorAction",
                {"action": "search"},
            ),
            lambda: self._driver.press_keycode(66),  # type: ignore[attr-defined]
        )
        for action in action_candidates:
            try:
                action()
                return True
            except Exception:
                continue
        if self._adb_serial:
            try:
                self._press_keyevent_via_adb_sync("66")
                return True
            except Exception:
                pass
        return False

    def _press_back_sync(self) -> bool:
        self._invalidate_results_source_cache_sync()
        try:
            self._driver.back()
            return True
        except Exception:
            pass
        if not self._adb_serial:
            return False
        try:
            self._press_keyevent_via_adb_sync("4")
            return True
        except Exception:
            return False

    def _press_keyevent_via_adb_sync(self, key_code: str) -> None:
        adb_bin = require_tool_path("adb")
        if not self._adb_serial:
            raise AndroidUiError("ADB serial is required for adb keyevent")
        result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "input",
                "keyevent",
                key_code,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise AndroidUiError(result.stderr.strip() or result.stdout.strip())

    def _input_text_via_adb_sync(self, text: str) -> bool:
        adb_bin = require_tool_path("adb")
        if not self._adb_serial:
            return False
        normalized = text.strip()
        if not normalized:
            return False
        adb_text = normalized.replace(" ", "%s")
        result = subprocess.run(
            [
                adb_bin,
                "-s",
                self._adb_serial,
                "shell",
                "input",
                "text",
                adb_text,
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.info(
                "adb_tap_failed: serial=%s point=(%s,%s) code=%s stdout=%s stderr=%s",
                self._adb_serial,
                x,
                y,
                result.returncode,
                (result.stdout or "").strip()[:240],
                (result.stderr or "").strip()[:240],
            )
        return result.returncode == 0

    def _find_video_result_cards_sync(self) -> list[object]:
        elements: list[object] = []
        for token in selectors.RESULT_VIDEO_DESCRIPTION_CONTAINS:
            elements.extend(
                self._driver.find_elements(
                    "-android uiautomator",
                    f'new UiSelector().descriptionContains("{token}")',
                )
            )
        return elements

    def _scroll_results_feed_once_sync(self) -> None:
        if not self._adb_serial:
            return
        bounds = self._extract_results_bounds_sync()
        if bounds is None:
            size = self._driver.get_window_size()
            left, top, right, bottom = (
                0,
                int(size["height"] * 0.28),
                int(size["width"]),
                int(size["height"] * 0.82),
            )
        else:
            left, top, right, bottom = bounds
        x = int((left + right) / 2)
        start_y = int(bottom - max(100, (bottom - top) * 0.18))
        end_y = int(top + max(140, (bottom - top) * 0.22))
        self._swipe_via_adb_sync(x=x, start_y=start_y, end_y=end_y)

    @staticmethod
    def _parse_xml_root_sync(page_source: str | None) -> object | None:
        if not page_source:
            return None
        try:
            import xml.etree.ElementTree as ET

            return ET.fromstring(page_source)
        except Exception:
            return None

    @staticmethod
    def _text_has_sponsored_marker_sync(*values: str) -> bool:
        for value in values:
            lowered = (value or "").strip().casefold()
            if not lowered:
                continue
            if "sponsored" in lowered or "спонс" in lowered or "реклама" in lowered:
                return True
        return False

    @classmethod
    def _results_source_stats_sync(cls, page_source: str | None) -> tuple[bool, bool, int, int]:
        root = cls._parse_xml_root_sync(page_source)
        if root is None:
            return False, False, 0, 0

        has_results_container = False
        has_query_header = False
        organic_title_count = 0
        organic_playable_count = 0

        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            content_desc = (node.attrib.get("content-desc") or "").strip()

            if (
                resource_id in selectors.RESULTS_CONTAINER_IDS
                and cls._parse_bounds_sync(node.attrib.get("bounds")) is not None
            ):
                has_results_container = True

            if resource_id in selectors.SEARCH_QUERY_HEADER_IDS and (text or content_desc):
                has_query_header = True

            if resource_id in selectors.RESULT_TITLE_IDS:
                if (
                    text
                    and not cls._is_placeholder_result_title(text)
                    and not cls._text_has_sponsored_marker_sync(text, content_desc)
                ):
                    organic_title_count += 1

            lowered_text = text.casefold()
            lowered_desc = content_desc.casefold()
            has_play_video = (
                "play video" in lowered_text
                or "play video" in lowered_desc
                or "воспроизвести видео" in lowered_text
                or "воспроизвести видео" in lowered_desc
            )
            if has_play_video and not cls._text_has_sponsored_marker_sync(text, content_desc):
                organic_playable_count += 1

        return has_results_container, has_query_header, organic_title_count, organic_playable_count

    @classmethod
    def _score_results_source_sync(cls, page_source: str | None) -> int:
        (
            has_results_container,
            has_query_header,
            organic_title_count,
            organic_playable_count,
        ) = cls._results_source_stats_sync(page_source)
        return (
            (20 if has_results_container else 0)
            + (8 if has_query_header else 0)
            + (organic_title_count * 6)
            + (organic_playable_count * 5)
        )

    def _preferred_results_page_source_sync(self) -> str | None:
        now = time.monotonic()
        cached_xml = self._results_source_cache_xml
        if cached_xml is not None and (now - self._results_source_cache_at) < 0.75:
            return cached_xml or None

        try:
            driver_page_source = self._driver.page_source or ""
        except Exception:
            driver_page_source = ""

        chosen_source = driver_page_source or None
        if self._adb_serial:
            (
                has_results_container,
                has_query_header,
                organic_title_count,
                organic_playable_count,
            ) = self._results_source_stats_sync(driver_page_source)
            needs_adb_fallback = (
                not driver_page_source
                or (
                    (has_results_container or has_query_header)
                    and organic_title_count == 0
                    and organic_playable_count == 0
                )
            )
            if needs_adb_fallback:
                adb_page_source = self._dump_ui_hierarchy_via_adb_sync()
                adb_score = self._score_results_source_sync(adb_page_source)
                driver_score = self._score_results_source_sync(driver_page_source)
                if adb_score > driver_score:
                    logger.info(
                        "results_source: using adb hierarchy over appium page_source "
                        "(appium_score=%s adb_score=%s)",
                        driver_score,
                        adb_score,
                    )
                    chosen_source = adb_page_source

        self._results_source_cache_xml = chosen_source or ""
        self._results_source_cache_at = now
        return chosen_source

    def _extract_results_bounds_sync(self) -> tuple[int, int, int, int] | None:
        page_source = self._preferred_results_page_source_sync()
        if not page_source:
            return None
        root = self._parse_xml_root_sync(page_source)
        if root is None:
            return None
        for node in root.iter():
            if (node.attrib.get("resource-id") or "").strip() not in selectors.RESULTS_CONTAINER_IDS:
                continue
            parsed = self._parse_bounds_sync(node.attrib.get("bounds"))
            if parsed is not None:
                return parsed
        return None

    def _extract_current_sponsored_bounds_sync(self) -> list[tuple[int, int, int, int]]:
        page_source = self._preferred_results_page_source_sync()
        if not page_source:
            return []
        root = self._parse_xml_root_sync(page_source)
        if root is None:
            return []
        return self._extract_sponsored_bounds_sync(root)

    @staticmethod
    def _parse_bounds_sync(bounds: str | None) -> tuple[int, int, int, int] | None:
        if not bounds:
            return None
        import re

        numbers = [int(value) for value in re.findall(r"\d+", bounds)]
        if len(numbers) != 4:
            return None
        return numbers[0], numbers[1], numbers[2], numbers[3]

    def _swipe_via_adb_sync(self, *, x: int, start_y: int, end_y: int) -> None:
        if not self._adb_serial:
            return
        self._invalidate_results_source_cache_sync()
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
                "320",
            ],
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            timeout=30,
        )

    def _has_results_surface_sync(self) -> bool:
        if self._is_reel_watch_surface_sync():
            return False
        has_search_context = self._has_search_context_sync()
        has_results_container = False
        for candidate_id in selectors.RESULTS_CONTAINER_IDS:
            try:
                if self._driver.find_elements("id", candidate_id):
                    has_results_container = True
                    break
            except Exception:
                continue
        if not has_results_container and self._extract_results_bounds_sync() is not None:
            has_results_container = True
        if has_search_context and self._has_openable_result_sync():
            return True
        if self._has_watch_surface_sync():
            return False
        if has_results_container:
            return True
        if not has_search_context:
            return False
        return self._has_openable_result_sync()

    def _has_watch_surface_sync(self) -> bool:
        strong_candidate_ids = (
            *selectors.WATCH_PLAYER_IDS,
            *selectors.WATCH_PANEL_IDS,
            *selectors.WATCH_TIME_BAR_IDS,
            *selectors.REEL_WATCH_PLAYER_IDS,
            *selectors.REEL_WATCH_PANEL_IDS,
        )
        for candidate_id in strong_candidate_ids:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception:
                continue
        # Metadata/list containers are weak watch hints and appear on results cards too.
        # Only honor them when we are not already on a query/results surface.
        if self._has_search_context_sync() or self._extract_results_bounds_sync() is not None:
            return False
        if not self._is_watchwhile_activity_sync():
            return False
        for candidate_id in selectors.WATCH_LIST_IDS:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception:
                continue
        return False

    def _has_stable_watch_surface_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if self._is_reel_watch_surface_sync():
            return False
        if self._has_playerless_watch_shell_sync():
            return False
        if self._is_search_input_visible_sync() or self._has_search_context_sync():
            return False
        if self._is_voice_search_surface_sync():
            return False
        if self._has_watch_surface_sync():
            return True
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not page_source:
            return False
        lowered = page_source.casefold()
        has_comments = any(hint.casefold() in lowered for hint in selectors.COMMENT_TEXT_HINTS)
        has_like = any(hint.casefold() in lowered for hint in selectors.LIKE_DESCRIPTION_HINTS)
        return has_comments and has_like

    def _has_openable_result_sync(self) -> bool:
        if not self._has_search_context_sync() and self._extract_results_bounds_sync() is None:
            return False
        sponsor_bounds = self._extract_current_sponsored_bounds_sync()
        if any(
            not candidate.is_short
            and not candidate.is_sponsored
            and not self._is_placeholder_result_title(candidate.title)
            for candidate in self._extract_result_candidates_from_page_source_sync()
        ):
            return True
        for candidate_id in selectors.RESULT_TITLE_IDS:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements:
                try:
                    text = (getattr(element, "text", "") or "").strip()
                except Exception:
                    continue
                if self._is_element_short_result_sync(element):
                    continue
                if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                    continue
                if text and not self._is_placeholder_result_title(text):
                    return True
        for element in self._find_video_result_cards_sync():
            if self._is_element_short_result_sync(element):
                continue
            if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                continue
            title = self._extract_result_title_sync(element)
            if title and not self._is_placeholder_result_title(title):
                return True
        return False

    def _has_short_only_results_sync(self) -> bool:
        if not self._has_search_context_sync() and self._extract_results_bounds_sync() is None:
            return False
        if self._has_openable_result_sync():
            return False
        return any(
            candidate.is_short and not candidate.is_sponsored
            for candidate in self._extract_result_candidates_from_page_source_sync()
        )

    def _has_nonorganic_only_results_sync(self) -> bool:
        if not self._has_search_context_sync() and self._extract_results_bounds_sync() is None:
            return False
        if self._has_openable_result_sync():
            return False
        candidates = self._extract_result_candidates_from_page_source_sync()
        if any(candidate.is_short or candidate.is_sponsored for candidate in candidates):
            return True
        if self._extract_sponsor_cta_bounds_sync():
            return True
        return False

    def _advance_past_short_only_results_sync(self, query: str | None) -> bool:
        if not self._has_results_surface_sync():
            return False
        if not self._has_nonorganic_only_results_sync():
            return False
        self._scroll_results_feed_once_sync()
        time.sleep(0.8)
        if self._has_nonorganic_only_results_sync():
            self._scroll_results_feed_once_sync()
            time.sleep(0.8)
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        return True

    def _has_search_context_for_query_sync(self, query: str) -> bool:
        normalized_query = query.strip().casefold()
        if not normalized_query or not self._has_search_context_sync():
            return False

        candidate_ids = (
            *selectors.SEARCH_INPUT_IDS,
            *selectors.SEARCH_QUERY_HEADER_IDS,
        )
        for candidate_id in candidate_ids:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements:
                values: list[str] = []
                try:
                    values.append((getattr(element, "text", "") or "").strip())
                except Exception:
                    pass
                for attribute_name in ("contentDescription", "content-desc"):
                    try:
                        values.append((element.get_attribute(attribute_name) or "").strip())  # type: ignore[attr-defined]
                    except Exception:
                        continue
                if any(normalized_query in value.casefold() for value in values if value):
                    return True

        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not page_source:
            return False
        lowered = page_source.casefold()
        return normalized_query in lowered and any(
            candidate_id in page_source for candidate_id in selectors.SEARCH_QUERY_HEADER_IDS
        )

    def _has_query_matching_results_sync(self, query: str) -> bool:
        if not query or not self._has_results_surface_sync():
            return False
        if self._is_watch_surface_for_query_sync(query):
            return True
        sponsor_bounds = self._extract_current_sponsored_bounds_sync()

        seen_titles: set[str] = set()

        def _remember_if_match(raw_title: str | None) -> bool:
            title = (raw_title or "").strip()
            if not title:
                return False
            normalized = title.casefold()
            if normalized in seen_titles:
                return False
            seen_titles.add(normalized)
            if self._should_skip_result_title_for_query_sync(title, query):
                return False
            return self._titles_overlap_sync(title, query)

        for candidate_id in selectors.RESULT_TITLE_IDS:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements:
                if self._is_element_short_result_sync(element):
                    continue
                if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                    continue
                try:
                    if _remember_if_match(getattr(element, "text", "") or ""):
                        return True
                except Exception:
                    continue

        for element in self._find_video_result_cards_sync():
            if self._is_element_short_result_sync(element):
                continue
            if self._is_element_sponsored_result_sync(element, sponsor_bounds=sponsor_bounds):
                continue
            if _remember_if_match(self._extract_result_title_sync(element)):
                return True

        for candidate in self._extract_title_result_candidates_from_page_source_sync():
            if candidate.is_sponsored or candidate.is_short:
                continue
            if _remember_if_match(candidate.title):
                return True

        for candidate in self._extract_result_candidates_from_page_source_sync():
            if candidate.is_sponsored or candidate.is_short:
                continue
            if _remember_if_match(candidate.title):
                return True

        for candidate in self._extract_text_result_candidates_from_page_source_sync(query):
            if candidate.is_sponsored or candidate.is_short:
                continue
            if _remember_if_match(candidate.title):
                return True

        return False

    def _has_query_results_surface_sync(self, query: str) -> bool:
        if not self._has_search_context_for_query_sync(query):
            return False
        if self._has_mixed_watch_results_surface_sync():
            return False
        if self._is_blank_youtube_shell_sync():
            return False
        if self._is_loading_results_shell_sync():
            return False
        for candidate_id in selectors.RESULTS_CONTAINER_IDS:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception:
                continue
        return self._extract_results_bounds_sync() is not None

    def _has_mixed_watch_results_surface_sync(self) -> bool:
        if not self._has_search_context_sync():
            return False
        if self._extract_results_bounds_sync() is None:
            return False
        if not self._has_watch_surface_sync():
            return False
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not page_source:
            return False
        return any(
            candidate_id in page_source for candidate_id in selectors.MINIPLAYER_SURFACE_IDS
        )

    def _requires_hard_query_reset_sync(self, query: str) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if self._is_watch_surface_for_query_sync(query):
            return False
        if self._has_query_ready_surface_sync(query):
            return False
        if self._has_provisional_watch_surface_for_query_sync(query):
            return False
        if self._has_mixed_watch_results_surface_sync():
            return True
        return self._has_stable_watch_surface_sync() and not self._has_search_context_for_query_sync(query)

    def _has_stale_previous_watch_surface_sync(self, query: str) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if not self._has_stable_watch_surface_sync():
            return False
        if self._has_results_surface_sync() or self._has_search_context_sync():
            return False
        if self._is_reel_watch_surface_sync() or self._is_reel_watch_surface_via_adb_sync():
            return False
        return not self._is_watch_surface_for_query_sync(query)

    def _should_force_fresh_query_surface_sync(self, query: str) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return False
        if self._has_query_ready_surface_sync(query):
            return False
        if self._has_mixed_watch_results_surface_sync():
            return True

        visible_query = self._extract_visible_query_text_sync()
        if visible_query and visible_query != normalized_query:
            return True

        if self._has_search_context_sync() and not self._has_search_context_for_query_sync(query):
            return True
        if self._has_results_surface_sync() and not self._has_query_matching_results_sync(query):
            return True
        if self._has_stable_watch_surface_sync() and not self._is_watch_surface_for_query_sync(query):
            return True
        return self._requires_hard_query_reset_sync(query)

    def _extract_visible_query_text_sync(self) -> str | None:
        candidate_ids = (
            *selectors.SEARCH_INPUT_IDS,
            *selectors.SEARCH_QUERY_HEADER_IDS,
        )
        for candidate_id in candidate_ids:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements:
                values: list[str] = []
                try:
                    values.append((getattr(element, "text", "") or "").strip())
                except Exception:
                    pass
                for attribute_name in ("contentDescription", "content-desc"):
                    try:
                        values.append((element.get_attribute(attribute_name) or "").strip())  # type: ignore[attr-defined]
                    except Exception:
                        continue
                for value in values:
                    normalized = value.casefold().strip()
                    if normalized:
                        return normalized
        return None

    def _has_search_button_sync(self) -> bool:
        return self._find_search_button_sync() is not None

    def _find_search_button_sync(self) -> object | None:
        candidates = self._find_search_button_candidates_sync()
        return candidates[0] if candidates else None

    def _find_search_button_candidates_sync(self) -> list[object]:
        found: list[object] = []
        seen_selectors: set[str] = set()
        for value in selectors.SEARCH_BUTTON_ACCESSIBILITY_IDS:
            try:
                elements = self._driver.find_elements("accessibility id", value)
                for element in elements:
                    if element is not None:
                        found.append(element)
            except Exception:
                continue

        page_source = ""
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if page_source:
            try:
                import xml.etree.ElementTree as ET

                root = ET.fromstring(page_source)
            except Exception:
                root = None
            if root is not None:
                for node in root.iter():
                    desc = (node.attrib.get("content-desc") or "").strip()
                    if not desc:
                        continue
                    lowered = desc.casefold()
                    if self._is_voice_search_description(lowered):
                        continue
                    if not any(
                        hint.casefold() in lowered
                        for hint in selectors.SEARCH_BUTTON_DESCRIPTION_HINTS
                    ):
                        continue
                    selector = self._node_to_uiselector(node)
                    if selector is None or selector in seen_selectors:
                        continue
                    seen_selectors.add(selector)
                    try:
                        elements = self._driver.find_elements("-android uiautomator", selector)
                    except Exception:
                        continue
                    if elements:
                        found.extend(elements[:1])
        return found

    def _find_home_button_candidates_sync(self) -> list[object]:
        found: list[object] = []
        for value in selectors.HOME_BUTTON_ACCESSIBILITY_IDS:
            try:
                elements = self._driver.find_elements("accessibility id", value)
            except Exception:
                elements = []
            for element in elements:
                if element is not None:
                    found.append(element)
        return found

    def _tap_home_button_sync(self) -> bool:
        for element in self._find_home_button_candidates_sync()[:2]:
            try:
                element.click()
                return True
            except Exception:
                bounds = self._extract_element_bounds_sync(element)
                if bounds is None:
                    continue
                center_x = int((bounds[0] + bounds[2]) / 2)
                center_y = int((bounds[1] + bounds[3]) / 2)
                if self._tap_via_adb_sync(center_x, center_y):
                    return True
        return False

    def _is_search_input_visible_sync(self) -> bool:
        for candidate_id in selectors.SEARCH_INPUT_IDS:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception:
                continue
        return False

    def _has_search_context_sync(self) -> bool:
        if self._is_reel_watch_surface_sync():
            return False
        if self._is_search_input_visible_sync():
            return True
        for candidate_id in selectors.SEARCH_QUERY_HEADER_IDS:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception:
                continue
        return False

    def _is_browsing_surface_sync(self) -> bool:
        return (
            self._safe_current_package_sync() == self._config.youtube_package
            and not self._is_watchwhile_activity_sync()
            and not self._has_playerless_watch_shell_sync()
            and self._has_search_button_sync()
            and not self._has_watch_surface_sync()
            and not self._is_search_input_visible_sync()
            and not self._is_voice_search_surface_sync()
        )

    def _is_clean_home_surface_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if self._is_watchwhile_activity_sync() or self._has_playerless_watch_shell_sync():
            return False
        try:
            has_logo = bool(
                self._driver.find_elements("id", "com.google.android.youtube:id/youtube_logo")
            )
        except Exception:
            has_logo = False
        if not has_logo:
            return False
        if not self._has_search_button_sync():
            return False
        if self._is_search_input_visible_sync() or self._has_search_context_sync():
            return False
        if self._is_voice_search_surface_sync():
            return False
        if self._is_blank_youtube_shell_sync():
            return False
        return True

    def _is_voice_search_surface_sync(self) -> bool:
        current_activity = (self._safe_current_activity_sync() or "").casefold()
        if "voice" in current_activity:
            return True
        try:
            page_source = (self._driver.page_source or "").casefold()
        except Exception:
            return False
        return any(hint.casefold() in page_source for hint in selectors.VOICE_SEARCH_SCREEN_HINTS)

    def _is_blank_youtube_shell_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if (
            self._has_search_button_sync()
            or self._has_search_context_sync()
            or self._has_results_surface_sync()
            or self._has_watch_surface_sync()
            or self._is_voice_search_surface_sync()
        ):
            return False
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not page_source:
            return True
        lowered = page_source.casefold()
        return (
            "next_gen_watch_container_layout" in lowered
            or "action_bar_root" in lowered
            or "more_drawer_container" in lowered
        )

    def _is_degraded_feed_shell_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if self._has_search_button_sync():
            return False
        if self._is_search_input_visible_sync() or self._has_search_context_sync():
            return False
        if self._is_voice_search_surface_sync():
            return False
        if self._has_stable_watch_surface_sync():
            return False
        if self._is_blank_youtube_shell_sync():
            return True
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not page_source:
            return False
        return bool(self._find_video_result_cards_sync()) or any(
            result_id in page_source for result_id in selectors.RESULT_TITLE_IDS
        )

    def _is_loading_results_shell_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if not self._has_search_context_sync():
            return False
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not page_source:
            return False
        if "com.google.android.youtube:id/load_progress" not in page_source:
            return False
        if "com.google.android.youtube:id/results" not in page_source:
            return False
        return not self._has_openable_result_sync()

    @staticmethod
    def _is_voice_search_description(lowered_description: str) -> bool:
        return any(
            hint.casefold() in lowered_description
            for hint in selectors.VOICE_SEARCH_DESCRIPTION_HINTS
        )

    @staticmethod
    def _extract_result_title_sync(element: object) -> str | None:
        for attribute_name in ("contentDescription", "content-desc"):
            try:
                raw_value = element.get_attribute(attribute_name)  # type: ignore[attr-defined]
            except Exception:
                continue
            text = (raw_value or "").strip()
            if not text:
                continue
            return text.split(" - ", 1)[0].strip() or text
        return None

    @staticmethod
    def _is_placeholder_result_title(text: str) -> bool:
        lowered = text.strip().casefold()
        return lowered in {
            "play video",
            "воспроизвести видео",
            "play",
            "sponsored",
            "sponsor",
            "реклама",
            "спонсировано",
        }

    def _try_open_result_element_sync(self, element: object, title: str, query: str | None = None) -> bool:
        bounds = self._extract_element_bounds_sync(element)
        is_short_result = bounds is not None and self._is_short_result_bounds_sync(bounds)
        self._last_tapped_result_title = title
        self._last_tapped_result_is_short = is_short_result
        if is_short_result:
            return False
        try:
            element.click()
            if self._await_watch_open_after_tap_sync(query=query, timeout_seconds=4.0):
                if self._reject_reel_watch_surface_sync():
                    return False
                return True
        except Exception:
            pass

        if bounds is None:
            return False
        center_x = int((bounds[0] + bounds[2]) / 2)
        center_y = int((bounds[1] + bounds[3]) / 2)
        if not self._tap_via_adb_sync(center_x, center_y):
            return False
        if not self._await_watch_open_after_tap_sync(query=query, timeout_seconds=6.5):
            return False
        if self._reject_reel_watch_surface_sync():
            return False
        return True

    def _is_element_short_result_sync(self, element: object) -> bool:
        bounds = self._extract_element_bounds_sync(element)
        if bounds is not None and self._is_short_result_bounds_sync(bounds):
            return True
        for attribute_name in ("contentDescription", "content-desc"):
            try:
                raw_value = element.get_attribute(attribute_name)  # type: ignore[attr-defined]
            except Exception:
                continue
            lowered = (raw_value or "").strip().casefold()
            if "play short" in lowered or "воспроизвести short" in lowered:
                return True
        return False

    def _is_element_sponsored_result_sync(
        self,
        element: object,
        *,
        sponsor_bounds: list[tuple[int, int, int, int]] | None = None,
    ) -> bool:
        if sponsor_bounds is None:
            sponsor_bounds = self._extract_current_sponsored_bounds_sync()
        if not sponsor_bounds:
            sponsor_bounds = []
        bounds = self._extract_element_bounds_sync(element)
        if bounds is not None and any(
            self._bounds_overlap_sync(bounds, sponsor) for sponsor in sponsor_bounds
        ):
            return True
        try:
            text = (getattr(element, "text", "") or "").strip().casefold()
        except Exception:
            text = ""
        if "sponsored" in text or "спонс" in text or text == "реклама":
            return True
        for attribute_name in ("contentDescription", "content-desc"):
            try:
                raw_value = (element.get_attribute(attribute_name) or "").strip().casefold()  # type: ignore[attr-defined]
            except Exception:
                continue
            if "sponsored" in raw_value or "sponsor" in raw_value or "спонс" in raw_value:
                return True
        return False

    @staticmethod
    def _extract_element_bounds_sync(element: object) -> tuple[int, int, int, int] | None:
        for attribute_name in ("bounds",):
            try:
                raw_value = element.get_attribute(attribute_name)  # type: ignore[attr-defined]
            except Exception:
                continue
            parsed = AndroidYouTubeNavigator._parse_bounds_sync(raw_value)
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

    def _tap_via_adb_sync(self, x: int, y: int) -> bool:
        if not self._adb_serial:
            return False
        self._invalidate_results_source_cache_sync()
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
            capture_output=True,
            check=False,
            env=build_android_runtime_env(),
            text=True,
            timeout=30,
        )
        return result.returncode == 0

    def _tap_top_result_region_sync(self, query: str | None = None) -> str | None:
        if self._has_watch_surface_sync():
            return None
        bounds = self._extract_results_bounds_sync()
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None
        x = int(left + width * 0.5)
        y = int(top + max(220, height * 0.20))
        if not self._tap_via_adb_sync(x, y):
            return None
        time.sleep(2.2)
        if self._await_watch_open_after_tap_sync(query=query, timeout_seconds=7.5):
            title = self._extract_current_watch_title_sync()
            if title:
                return title
        return None

    def _tap_result_candidate_sync(self, candidate: NativeResultCandidate, query: str | None = None) -> bool:
        self._last_tapped_result_title = candidate.title
        self._last_tapped_result_is_short = candidate.is_short
        left, top, right, bottom = candidate.bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        x = int(left + width * 0.45)
        y = int(top + height * 0.35)
        if not self._tap_via_adb_sync(x, y):
            return False
        if not self._await_watch_open_after_tap_sync(query=query, timeout_seconds=6.5):
            return False
        if self._reject_reel_watch_surface_sync():
            return False
        return True

    def _await_watch_open_after_tap_sync(
        self,
        *,
        query: str | None,
        timeout_seconds: float,
    ) -> bool:
        logger.info(
            "await_watch_open_after_tap:start query=%s timeout=%.2f",
            query,
            timeout_seconds,
        )
        started_at = time.monotonic()
        watch_opened = self._wait_for_watch_surface_sync(
            query=query,
            timeout_seconds=timeout_seconds,
            allow_heavy_dialog_recovery=False,
        )
        wait_elapsed = time.monotonic() - started_at
        logger.info(
            "await_watch_open_after_tap:after_wait query=%s watch_opened=%s elapsed=%.2f",
            query,
            watch_opened,
            wait_elapsed,
        )
        if watch_opened:
            return True
        if not query:
            return False
        if (
            self._is_watchwhile_activity_sync()
            and not self._has_playerless_watch_shell_sync()
            and self._has_stable_watch_surface_sync()
            and not self._last_tapped_result_is_short
            and self._last_tapped_title_matches_query_sync(query)
        ):
            return True
        delayed_title = self._await_current_watch_title_sync(
            query,
            timeout_seconds=min(4.0, max(1.6, timeout_seconds * 0.5)),
        )
        logger.info(
            "await_watch_open_after_tap:after_title query=%s delayed_title=%s",
            query,
            delayed_title,
        )
        return bool(delayed_title)

    def _reject_reel_watch_surface_sync(self) -> bool:
        if not (
            self._is_reel_watch_surface_sync()
            or self._is_reel_watch_surface_via_adb_sync()
        ):
            return False
        self._press_back_sync()
        time.sleep(0.8)
        self._dismiss_possible_dialogs_sync()
        self._dismiss_miniplayer_on_results_sync()
        return True

    def _wait_for_watch_surface_sync(
        self,
        query: str | None = None,
        timeout_seconds: float = 7.5,
        *,
        allow_heavy_dialog_recovery: bool = True,
    ) -> bool:
        hard_deadline = getattr(self._thread_local, "hard_deadline", float("inf"))
        started_at = time.monotonic()
        deadline = min(started_at + timeout_seconds, hard_deadline)
        settle_deadline: float | None = None
        while time.monotonic() < deadline:
            self._check_sync_deadline()
            handled_dialog = self._dismiss_possible_dialogs_sync(
                allow_heavy_adb=allow_heavy_dialog_recovery,
            )
            if handled_dialog:
                deadline = min(max(deadline, time.monotonic() + 2.0), hard_deadline)
            current_package = self._safe_current_package_sync()
            if current_package != self._config.youtube_package:
                return False
            if not allow_heavy_dialog_recovery:
                current_activity = self._safe_current_activity_sync()
                try:
                    page_source = self._driver.page_source or ""
                except Exception:
                    page_source = ""
                has_search_input = self._source_has_search_input_markup_sync(page_source)
                has_search_context = has_search_input or self._source_has_search_context_markup_sync(
                    page_source,
                )
                has_results = self._source_has_results_surface_markup_sync(page_source)
                has_watch = self._source_has_watch_surface_markup_sync(page_source)
                if query:
                    if self._source_has_reel_watch_surface_markup_sync(page_source):
                        return False
                    if self._has_playerless_watch_shell_sync(page_source):
                        time.sleep(0.4)
                        continue
                    if has_search_input or self._source_is_voice_search_surface_sync(
                        page_source,
                        current_activity,
                    ):
                        return False
                    if has_watch and not has_search_context and not has_results:
                        return True
                    lowered_activity = (current_activity or "").casefold()
                    if (
                        not has_search_context
                        and not has_results
                        and not self._last_tapped_result_is_short
                        and (
                            "watchwhile" in lowered_activity
                            or "internalmainactivity" in lowered_activity
                        )
                    ):
                        return True
                    if has_watch and (has_search_context or has_results):
                        if settle_deadline is None:
                            settle_deadline = min(deadline, time.monotonic() + 1.2)
                        if time.monotonic() < settle_deadline:
                            time.sleep(0.35)
                            continue
                else:
                    if (
                        has_watch
                        and not has_search_context
                        and not has_results
                        and not self._has_playerless_watch_shell_sync(page_source)
                        and not self._source_has_reel_watch_surface_markup_sync(page_source)
                        and not self._source_is_voice_search_surface_sync(page_source, current_activity)
                    ):
                        return True
                    if has_search_input:
                        return False
                time.sleep(0.4)
                continue
            if query:
                if self._is_reel_watch_surface_sync() or self._is_reel_watch_surface_via_adb_sync():
                    return False
                if self._has_playerless_watch_shell_sync():
                    time.sleep(0.4)
                    continue
                if self._is_watch_surface_for_query_sync(query):
                    return True
                if self._has_provisional_watch_surface_for_query_sync(query):
                    return True
                if self._has_stable_watch_surface_sync() and self._watch_surface_requires_settle_sync(query):
                    if settle_deadline is None:
                        settle_deadline = min(deadline, time.monotonic() + 2.5)
                    if time.monotonic() < settle_deadline:
                        time.sleep(0.4)
                        continue
            elif self._has_stable_watch_surface_sync():
                return True
            if self._is_voice_search_surface_sync() or self._is_search_input_visible_sync():
                return False
            time.sleep(0.4)
        elapsed = time.monotonic() - started_at
        if elapsed > max(timeout_seconds + 2.0, timeout_seconds * 1.5):
            logger.warning(
                "wait_for_watch_surface:slow query=%s timeout=%.2f elapsed=%.2f heavy_dialog=%s",
                query,
                timeout_seconds,
                elapsed,
                allow_heavy_dialog_recovery,
            )
        return False

    def _extract_title_result_candidates_from_page_source_sync(self) -> list[NativeResultCandidate]:
        page_source = self._preferred_results_page_source_sync()
        if not page_source:
            return []
        results_bounds = self._extract_results_bounds_sync()
        if results_bounds is None:
            return []
        root = self._parse_xml_root_sync(page_source)
        if root is None:
            return []

        results_left, results_top, results_right, results_bottom = results_bounds
        short_bounds = self._extract_short_result_bounds_sync()
        sponsor_bounds = self._extract_sponsored_bounds_sync(root)
        candidates: list[NativeResultCandidate] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()

        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if resource_id not in selectors.RESULT_TITLE_IDS:
                continue
            text = (node.attrib.get("text") or "").strip()
            if not text or self._is_placeholder_result_title(text):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            if bottom <= results_top or top >= results_bottom:
                continue
            tap_bounds = (
                max(results_left, left - 24),
                max(results_top, top - 96),
                min(results_right, max(right + 24, results_right - 32)),
                min(results_bottom, bottom + 168),
            )
            is_sponsored = any(
                self._bounds_overlap_sync(tap_bounds, sponsor)
                for sponsor in sponsor_bounds
            ) or any(
                self._bounds_overlap_sync(bounds, sponsor)
                for sponsor in sponsor_bounds
            )
            candidate = NativeResultCandidate(
                title=text,
                bounds=tap_bounds,
                is_short=any(
                    self._bounds_overlap_sync(tap_bounds, short_bound)
                    for short_bound in short_bounds
                ),
                is_sponsored=is_sponsored,
            )
            key = (candidate.title, candidate.bounds)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        candidates.sort(key=lambda item: (item.bounds[1], item.bounds[0]))
        return candidates

    def _extract_text_result_candidates_from_page_source_sync(
        self,
        query: str | None,
    ) -> list[NativeResultCandidate]:
        page_source = self._preferred_results_page_source_sync()
        if not page_source:
            return []
        results_bounds = self._extract_results_bounds_sync()
        if results_bounds is None:
            return []
        root = self._parse_xml_root_sync(page_source)
        if root is None:
            return []

        sponsor_bounds = self._extract_sponsored_bounds_sync(root)
        results_left, results_top, results_right, results_bottom = results_bounds
        short_bounds = self._extract_short_result_bounds_sync()
        candidates: list[NativeResultCandidate] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()

        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if resource_id in selectors.SEARCH_QUERY_HEADER_IDS:
                continue
            text = (node.attrib.get("text") or "").strip()
            if not self._is_viable_result_text_sync(text, query):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            if bottom <= results_top or top >= results_bottom:
                continue
            if any(self._bounds_overlap_sync(bounds, sponsor) for sponsor in sponsor_bounds):
                continue
            tap_bounds = (
                max(results_left, left - 24),
                max(results_top, top - 120),
                results_right - 32,
                min(results_bottom, bottom + 180),
            )
            candidate = NativeResultCandidate(
                title=text,
                bounds=tap_bounds,
                is_short=any(
                    self._bounds_overlap_sync(tap_bounds, short_bound)
                    for short_bound in short_bounds
                ),
                is_sponsored=any(
                    self._bounds_overlap_sync(tap_bounds, sponsor)
                    for sponsor in sponsor_bounds
                ) or any(
                    self._bounds_overlap_sync(bounds, sponsor)
                    for sponsor in sponsor_bounds
                ),
            )
            key = (candidate.title, candidate.bounds)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

        candidates.sort(key=lambda item: (item.bounds[1], item.bounds[0]))
        return candidates

    @staticmethod
    def _normalize_playable_candidate_bounds_sync(
        bounds: tuple[int, int, int, int],
        results_bounds: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int]:
        if results_bounds is None:
            return bounds
        left, top, right, bottom = bounds
        results_left, results_top, results_right, results_bottom = results_bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        min_useful_height = 96
        min_useful_width = int((results_right - results_left) * 0.55)
        if height >= min_useful_height and width >= min_useful_width:
            return bounds
        return (
            results_left,
            max(results_top, top - max(180, height * 12)),
            results_right,
            min(results_bottom, bottom + max(220, height * 18)),
        )

    def _extract_short_result_bounds_sync(self) -> list[tuple[int, int, int, int]]:
        return [
            candidate.bounds
            for candidate in self._extract_result_candidates_from_page_source_sync()
            if candidate.is_short and not candidate.is_sponsored
        ]

    def _is_short_result_bounds_sync(self, bounds: tuple[int, int, int, int]) -> bool:
        expanded_bounds = (
            max(0, bounds[0] - 24),
            max(0, bounds[1] - 120),
            bounds[2] + 24,
            bounds[3] + 220,
        )
        return any(
            self._bounds_overlap_sync(expanded_bounds, short_bound)
            for short_bound in self._extract_short_result_bounds_sync()
        )

    def _extract_result_candidates_from_page_source_sync(self) -> list[NativeResultCandidate]:
        page_source = self._preferred_results_page_source_sync()
        if not page_source:
            return []
        root = self._parse_xml_root_sync(page_source)
        if root is None:
            return []

        results_bounds = self._extract_results_bounds_sync()
        sponsor_bounds = self._extract_sponsored_bounds_sync(root)
        candidates: list[NativeResultCandidate] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()

        for node in root.iter():
            desc = (node.attrib.get("content-desc") or "").strip()
            if not desc:
                continue
            lowered = desc.casefold()
            if "play short" not in lowered and "play video" not in lowered:
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if results_bounds is not None:
                _, results_top, _, results_bottom = results_bounds
                _, top, _, bottom = bounds
                if bottom <= results_top or top >= results_bottom:
                    continue
            title = desc.split(" - ", 1)[0].strip()
            if not title or self._is_placeholder_result_title(title):
                continue
            is_short = "play short" in lowered
            left, top, right, bottom = bounds
            raw_width = max(0, right - left)
            raw_height = max(0, bottom - top)
            if not is_short and (raw_height < 48 or raw_width < 160):
                # YouTube sometimes exposes a "play video" accessibility node with
                # a 1-4px height above the real result card. Tapping its normalized
                # area misses the card and causes open_first_result timeouts.
                continue
            candidate_bounds = (
                bounds
                if is_short
                else self._normalize_playable_candidate_bounds_sync(bounds, results_bounds)
            )
            candidate = NativeResultCandidate(
                title=title,
                bounds=candidate_bounds,
                is_short=is_short,
                is_sponsored=("sponsored" in lowered or "sponsor" in lowered or "спонс" in lowered) or any(
                    self._bounds_overlap_sync(bounds, sponsor)
                    for sponsor in sponsor_bounds
                ),
            )
            key = (candidate.title, candidate.bounds)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        return candidates

    def _extract_current_watch_title_sync(self) -> str | None:
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            return None
        if not page_source:
            return None
        if self._source_has_playerless_watch_shell_sync(page_source):
            return None
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(page_source)
        except Exception:
            return None

        player_bottom = None
        reel_player_bottom = None
        reel_surface = False
        metadata_top = None
        metadata_bottom = None
        screen_height = 2400
        screen_width = 1080
        try:
            size = self._driver.get_window_size()
            screen_height = int(size.get("height", screen_height))
            screen_width = int(size.get("width", screen_width))
        except Exception:
            pass
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if resource_id in selectors.WATCH_PLAYER_IDS and player_bottom is None:
                player_bottom = bounds[3]
            if resource_id in selectors.REEL_WATCH_PLAYER_IDS and reel_player_bottom is None:
                reel_player_bottom = bounds[3]
                reel_surface = True
            if resource_id in selectors.REEL_WATCH_PANEL_IDS:
                reel_surface = True
            if resource_id == "com.google.android.youtube:id/video_metadata_layout":
                metadata_top = bounds[1]
                metadata_bottom = bounds[3]

        if reel_surface:
            return None

        desc_candidates: list[tuple[int, str]] = []
        for node in root.iter():
            desc = (node.attrib.get("content-desc") or "").strip()
            if not desc:
                continue
            lowered = desc.casefold()
            if "play video" not in lowered and "воспроизвести видео" not in lowered:
                continue
            title = desc.split(" - ", 1)[0].strip()
            if not title or self._is_placeholder_result_title(title):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            top = bounds[1]
            bottom = bounds[3]
            if player_bottom is not None and top < player_bottom - 12:
                continue
            if metadata_top is not None and bottom < metadata_top:
                continue
            if metadata_bottom is not None and top > metadata_bottom:
                continue
            desc_candidates.append((top, title))

        if desc_candidates:
            desc_candidates.sort(key=lambda item: item[0])
            return desc_candidates[0][1]

        preferred: list[tuple[int, str]] = []
        fallback: list[tuple[int, str]] = []
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            if resource_id not in selectors.RESULT_TITLE_IDS:
                continue
            text = (node.attrib.get("text") or "").strip()
            if not text or self._is_placeholder_result_title(text):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            top = bounds[1] if bounds is not None else 99999
            if 700 <= top <= 1200:
                preferred.append((top, text))
            else:
                fallback.append((top, text))

        for candidates in (preferred, fallback):
            if not candidates:
                continue
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]
        return None

    def _extract_current_watch_title_for_query_sync(self, query: str) -> str | None:
        if self._has_playerless_watch_shell_sync():
            return None
        title = self._extract_current_watch_title_sync()
        if title and self._titles_overlap_sync(title, query):
            return title
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            return None
        if not page_source:
            return None
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(page_source)
        except Exception:
            return None

        player_bottom = None
        reel_surface = False
        screen_height = 2400
        screen_width = 1080
        try:
            size = self._driver.get_window_size()
            screen_height = int(size.get("height", screen_height))
            screen_width = int(size.get("width", screen_width))
        except Exception:
            pass
        metadata_top = None
        metadata_bottom = None
        for node in root.iter():
            resource_id = (node.attrib.get("resource-id") or "").strip()
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            if resource_id in selectors.WATCH_PLAYER_IDS and player_bottom is None:
                player_bottom = bounds[3]
            if resource_id in selectors.REEL_WATCH_PLAYER_IDS or resource_id in selectors.REEL_WATCH_PANEL_IDS:
                reel_surface = True
            if resource_id == "com.google.android.youtube:id/video_metadata_layout":
                metadata_top = bounds[1]
                metadata_bottom = bounds[3]

        if reel_surface:
            return None

        candidates: list[tuple[int, str]] = []
        for node in root.iter():
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            top = bounds[1]
            bottom = bounds[3]
            if player_bottom is not None and top < player_bottom - 12:
                continue
            if metadata_top is not None and bottom < metadata_top:
                continue
            if metadata_bottom is not None and top > metadata_bottom:
                continue

            desc = (node.attrib.get("content-desc") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            values: list[str] = []
            if desc:
                values.append(desc.split(" - ", 1)[0].strip())
            if text:
                values.append(text)
            for value in values:
                if not value or self._is_placeholder_result_title(value):
                    continue
                if not self._titles_overlap_sync(value, query):
                    continue
                candidates.append((top, value))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _is_watch_surface_for_query_sync(self, query: str | None) -> bool:
        if not self._has_stable_watch_surface_sync():
            return False
        if self._is_reel_watch_surface_sync():
            return False
        if self._has_search_context_sync():
            return False
        if not query:
            return True
        title = self._extract_current_watch_title_for_query_sync(query)
        if not title:
            return self._last_tapped_title_matches_query_sync(query)
        return self._titles_overlap_sync(title, query)

    def _has_provisional_watch_surface_for_query_sync(self, query: str) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        if not self._has_watch_surface_sync():
            return False
        if self._is_reel_watch_surface_sync():
            return False
        if self._has_playerless_watch_shell_sync():
            return False
        title = self._extract_current_watch_title_for_query_sync(query)
        if not title:
            return (
                self._is_watchwhile_activity_sync()
                and not self._has_playerless_watch_shell_sync()
                and self._last_tapped_title_matches_query_sync(query)
            )
        return self._titles_overlap_sync(title, query)

    def _has_query_watch_transition_sync(self, query: str) -> bool:
        return self._is_watch_surface_for_query_sync(query) or (
            self._has_provisional_watch_surface_for_query_sync(query)
        )

    def _last_tapped_title_matches_query_sync(self, query: str) -> bool:
        if self._last_tapped_result_is_short:
            return False
        title = (self._last_tapped_result_title or "").strip()
        if not title:
            return False
        if self._is_rejected_result_title_sync(title):
            return False
        return self._titles_overlap_sync(title, query)

    def _is_watchwhile_activity_sync(self) -> bool:
        current_activity = (self._safe_current_activity_sync() or "").casefold()
        if "watchwhile" in current_activity or "internalmainactivity" in current_activity:
            return True
        if "shell$homeactivity" not in current_activity:
            return False
        return self._has_watchwhile_component_via_adb_sync()

    def _is_home_activity_sync(self) -> bool:
        current_activity = (self._safe_current_activity_sync() or "").casefold()
        if "shell$homeactivity" not in current_activity:
            return False
        return not self._has_watchwhile_component_via_adb_sync()

    def _has_watchwhile_component_via_adb_sync(self) -> bool:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return False
        now = time.monotonic()
        if now - self._watch_activity_probe_at < 1.5:
            return self._watch_activity_probe_result
        self._watch_activity_probe_at = now
        self._watch_activity_probe_result = False
        if not self._adb_serial:
            return False
        if not self._wait_for_adb_device_sync(timeout_seconds=1.0):
            return False
        adb_bin = require_tool_path("adb")
        try:
            result = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    self._adb_serial,
                    "shell",
                    "dumpsys",
                    "activity",
                    "activities",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=8,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
        lowered = result.stdout.casefold()
        self._watch_activity_probe_result = (
            self._config.youtube_package.casefold() in lowered
            and "shell$homeactivity" in lowered
            and (
                "mactivitycomponent=com.google.android.youtube/com.google.android.apps.youtube.app.watchwhile" in lowered
                or "watchwhile.mainactivity" in lowered
                or "internalmainactivity" in lowered
            )
        )
        return self._watch_activity_probe_result

    def _provisional_watch_title_sync(self, query: str) -> str | None:
        if self._safe_current_package_sync() != self._config.youtube_package:
            return None
        if self._is_reel_watch_surface_sync() or self._is_reel_watch_surface_via_adb_sync():
            return None
        if self._has_provisional_watch_surface_for_query_sync(query):
            return (
                self._extract_current_watch_title_for_query_sync(query)
                or self._last_tapped_result_title
            )
        if self._is_watchwhile_activity_sync() and self._last_tapped_title_matches_query_sync(query):
            return self._last_tapped_result_title
        return None

    def _watch_surface_requires_settle_sync(self, query: str) -> bool:
        if not self._has_stable_watch_surface_sync():
            return False
        if self._is_reel_watch_surface_sync():
            return True
        if self._has_search_context_sync():
            return True
        title = self._extract_current_watch_title_for_query_sync(query)
        if not title:
            return True
        return self._has_results_surface_sync() and not self._titles_overlap_sync(title, query)

    def _is_reel_watch_surface_sync(self) -> bool:
        candidate_ids = (
            *selectors.REEL_WATCH_PLAYER_IDS,
            *selectors.REEL_WATCH_PANEL_IDS,
        )
        for candidate_id in candidate_ids:
            try:
                if self._driver.find_elements("id", candidate_id):
                    return True
            except Exception:
                continue
        return False

    def _is_reel_watch_surface_via_adb_sync(self) -> bool:
        hierarchy = self._dump_ui_hierarchy_via_adb_sync()
        if not hierarchy:
            return False
        lowered = hierarchy.casefold()
        if any(candidate_id in hierarchy for candidate_id in selectors.REEL_WATCH_PLAYER_IDS):
            return True
        if any(candidate_id in hierarchy for candidate_id in selectors.REEL_WATCH_PANEL_IDS):
            return True
        return "shorts" in lowered and "reel" in lowered

    def _extract_reel_watch_title_sync(
        self,
        root: object,
        *,
        query: str | None = None,
        screen_width: int,
        screen_height: int,
    ) -> str | None:
        candidates: list[tuple[int, str]] = []
        min_top = int(screen_height * 0.72)
        min_width = int(screen_width * 0.35)
        control_hints = (
            *selectors.LIKE_DESCRIPTION_HINTS,
            *selectors.SUBSCRIBE_DESCRIPTION_HINTS,
            *selectors.COMMENT_TEXT_HINTS,
            *selectors.AD_SIGNAL_DESCRIPTION_FRAGMENTS,
            *selectors.AD_CTA_DESCRIPTIONS,
            "share",
            "save",
            "you",
            "home",
            "shorts",
        )
        for node in root.iter():
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            if top < min_top:
                continue
            if right - left < min_width:
                continue
            values: list[str] = []
            desc = (node.attrib.get("content-desc") or "").strip()
            text = (node.attrib.get("text") or "").strip()
            if desc:
                values.append(desc.split(" - ", 1)[0].strip())
            if text:
                values.append(text)
            for value in values:
                if not value or self._is_placeholder_result_title(value):
                    continue
                lowered = value.casefold()
                if any(hint.casefold() in lowered for hint in control_hints):
                    continue
                if query and not self._titles_overlap_sync(value, query):
                    continue
                candidates.append((top, value))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _dismiss_miniplayer_on_results_sync(self) -> bool:
        if not (
            self._has_search_context_sync()
            or self._has_results_surface_sync()
            or self._page_has_miniplayer_hint_sync()
        ):
            return False
        handled = False
        for candidate_id in selectors.MINIPLAYER_CLOSE_IDS:
            try:
                elements = self._driver.find_elements("id", candidate_id)
            except Exception:
                elements = []
            for element in elements[:1]:
                try:
                    element.click()
                    handled = True
                    break
                except Exception:
                    continue
            if handled:
                time.sleep(0.8)
                return True

        try:
            page_source = self._driver.page_source or ""
        except Exception:
            page_source = ""
        if not self._page_has_miniplayer_hint_sync(page_source):
            return False
        for hint in selectors.MINIPLAYER_CLOSE_DESCRIPTION_HINTS:
            try:
                elements = self._driver.find_elements("accessibility id", hint)
            except Exception:
                elements = []
            for element in elements[:1]:
                try:
                    element.click()
                    time.sleep(0.8)
                    return True
                except Exception:
                    continue
        if self._has_search_context_sync() or self._has_results_surface_sync():
            if self._press_back_sync():
                time.sleep(0.8)
                self._dismiss_possible_dialogs_sync()
                if not self._page_has_miniplayer_hint_sync():
                    return True
        return False

    def _page_has_miniplayer_hint_sync(self, page_source: str | None = None) -> bool:
        if page_source is None:
            try:
                page_source = self._driver.page_source or ""
            except Exception:
                page_source = ""
        lowered = page_source.casefold()
        return any(
            hint.casefold() in lowered for hint in selectors.MINIPLAYER_SURFACE_DESCRIPTION_HINTS
        ) or any(
            candidate_id in page_source for candidate_id in selectors.MINIPLAYER_CLOSE_IDS
        ) or any(
            candidate_id in page_source for candidate_id in selectors.MINIPLAYER_SURFACE_IDS
        )

    @staticmethod
    def _bounds_overlap_sync(
        left_bounds: tuple[int, int, int, int],
        right_bounds: tuple[int, int, int, int],
    ) -> bool:
        left1, top1, right1, bottom1 = left_bounds
        left2, top2, right2, bottom2 = right_bounds
        return not (
            right1 <= left2
            or right2 <= left1
            or bottom1 <= top2
            or bottom2 <= top1
        )

    def _extract_sponsored_bounds_sync(self, root: object) -> list[tuple[int, int, int, int]]:
        sponsored_bounds: list[tuple[int, int, int, int]] = []
        cta_labels = {label.casefold() for label in selectors.AD_CTA_DESCRIPTIONS}
        for node in root.iter():
            text = (node.attrib.get("text") or "").strip().casefold()
            content_desc = (node.attrib.get("content-desc") or "").strip().casefold()
            has_sponsored_label = (
                "sponsored" in text
                or "sponsored" in content_desc
                or "спонс" in text
                or "спонс" in content_desc
                or "реклама" in text
            )
            has_cta_label = any(value in cta_labels for value in (text, content_desc) if value)
            if not has_sponsored_label and not has_cta_label:
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is not None:
                left, top, right, bottom = bounds
                if has_cta_label:
                    # Search-result ads often omit a visible "Sponsored" label in Appium XML,
                    # but reliably expose CTA buttons like "Visit site" / "Learn more".
                    # Expand that CTA region upward to cover the full ad card so result taps
                    # below it can skip the sponsored block.
                    sponsored_bounds.append(
                        (
                            0,
                            max(0, top - 860),
                            1080,
                            bottom + 140,
                        )
                    )
                else:
                    width = max(0, right - left)
                    height = max(0, bottom - top)
                    is_large_card = width >= 700 or height >= 260
                    if is_large_card:
                        expand_top = 48
                        expand_bottom = 32
                        expand_left = 0
                        expand_right = 0
                    else:
                        expand_top = 220
                        expand_bottom = 120
                        expand_left = 48
                        expand_right = 48
                    sponsored_bounds.append(
                        (
                            max(0, left - expand_left),
                            max(0, top - expand_top),
                            max(right + expand_right, 1080),
                            bottom + expand_bottom,
                        )
                    )
        return sponsored_bounds

    def _extract_sponsor_cta_bounds_sync(self) -> list[tuple[int, int, int, int]]:
        try:
            page_source = self._driver.page_source or ""
        except Exception:
            return []
        if not page_source:
            return []
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(page_source)
        except Exception:
            return []
        cta_bounds: list[tuple[int, int, int, int]] = []
        for node in root.iter():
            values = (
                (node.attrib.get("text") or "").strip(),
                (node.attrib.get("content-desc") or "").strip(),
            )
            if not any(
                any(label.casefold() == value.casefold() for label in selectors.AD_CTA_DESCRIPTIONS)
                for value in values
                if value
            ):
                continue
            bounds = self._parse_bounds_sync(node.attrib.get("bounds"))
            if bounds is not None:
                cta_bounds.append(bounds)
        return cta_bounds

    def _is_viable_result_text_sync(self, text: str, query: str | None) -> bool:
        cleaned = text.strip()
        lowered = cleaned.casefold()
        if not cleaned or self._is_placeholder_result_title(cleaned):
            return False
        if query and lowered == query.strip().casefold():
            return False
        if query and not self._titles_overlap_sync(cleaned, query):
            return False
        if lowered in {
            "sponsored",
            "visit site",
            "learn more",
            "watch",
            "save",
            "comments",
            "subscribe",
            "home",
            "shorts",
        }:
            return False
        if len(cleaned) < 12 and len(cleaned.split()) < 2:
            return False
        return True

    def _rank_result_candidates_sync(
        self,
        *,
        query: str | None,
        require_overlap: bool,
    ) -> list[NativeResultCandidate]:
        combined: list[NativeResultCandidate] = [
            *self._extract_result_candidates_from_page_source_sync(),
            *self._extract_title_result_candidates_from_page_source_sync(),
            *self._extract_text_result_candidates_from_page_source_sync(query),
        ]
        ranked: list[NativeResultCandidate] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for candidate in combined:
            key = (candidate.title, candidate.bounds)
            if key in seen:
                continue
            seen.add(key)
            if (
                candidate.is_short
                or candidate.is_sponsored
                or self._should_skip_result_title_for_query_sync(candidate.title, query)
            ):
                continue
            if require_overlap:
                if query and not self._titles_overlap_sync(candidate.title, query):
                    continue
            elif query and not self._is_reasonable_topic_video_title_sync(candidate.title, query):
                continue
            ranked.append(candidate)
        ranked.sort(
            key=lambda item: (
                -self._score_result_title_for_query_sync(item.title, query),
                item.bounds[1],
                item.bounds[0],
            )
        )
        return ranked

    @classmethod
    def _score_result_title_for_query_sync(cls, title: str | None, query: str | None) -> float:
        normalized_title = " ".join((title or "").casefold().split())
        normalized_query = " ".join((query or "").casefold().split())
        if not normalized_title:
            return -10.0

        score = 0.5
        title_tokens = cls._normalized_match_tokens_sync(normalized_title)
        query_tokens = cls._normalized_match_tokens_sync(normalized_query)
        matched_query_tokens = {
            query_token
            for query_token in query_tokens
            if any(cls._tokens_related_sync(query_token, title_token) for title_token in title_tokens)
        }
        score += len(matched_query_tokens) * 1.5

        if normalized_query and normalized_query in normalized_title:
            score += 2.2
        if query and cls._titles_overlap_sync(title or "", query):
            score += 3.2
        elif query and cls._is_reasonable_topic_video_title_sync(title or "", query):
            score += 1.4

        anchor_tokens = cls._query_anchor_tokens_sync(normalized_query)
        anchor_matches = sum(
            1
            for anchor_token in anchor_tokens
            if any(cls._tokens_related_sync(anchor_token, title_token) for title_token in title_tokens)
        )
        score += anchor_matches * 1.8

        word_count = len(re.findall(r"[A-Za-zА-Яа-я0-9]+", normalized_title))
        if word_count >= 4:
            score += 0.4
        if word_count >= 7:
            score += 0.3

        if query and cls._is_disfavored_broad_money_match_sync(title or "", query):
            score -= 8.0

        if cls._is_finance_query_sync(normalized_query):
            score += cls._score_finance_title_quality_sync(normalized_title)
            score += cls._score_finance_noise_penalty_sync(normalized_title, normalized_query)

        if normalized_title.startswith("i ") or normalized_title.startswith("i'm ") or normalized_title.startswith("my "):
            score -= 1.0

        return score

    @classmethod
    def _is_finance_query_sync(cls, query: str) -> bool:
        lowered = query.casefold()
        return any(hint in lowered for hint in cls._FINANCE_TOPIC_HINTS)

    @classmethod
    def _score_finance_title_quality_sync(cls, normalized_title: str) -> float:
        score = 0.0
        for hint in cls._FINANCE_POSITIVE_TITLE_HINTS:
            if hint in normalized_title:
                score += 0.8
        for hint in cls._FINANCE_NEGATIVE_TITLE_HINTS:
            if hint in normalized_title:
                score -= 1.6
        if "news" in normalized_title:
            score -= 0.5
        if "opinion" in normalized_title or "reacts" in normalized_title:
            score -= 0.8
        if normalized_title.count("!") >= 2:
            score -= 0.8
        elif "!" in normalized_title:
            score -= 0.5
        return score

    @classmethod
    def _score_finance_noise_penalty_sync(cls, normalized_title: str, normalized_query: str) -> float:
        score = 0.0
        for hint in cls._FINANCE_ENTERTAINMENT_TITLE_HINTS:
            if hint in normalized_title:
                score -= 4.0
        if any(token in normalized_query for token in ("earn", "income", "profit")):
            for hint in cls._FINANCE_ENTERTAINMENT_TITLE_HINTS:
                if hint in normalized_title:
                    score -= 3.0
        return score

    @classmethod
    def _is_reasonable_topic_video_title_sync(cls, title: str, query: str) -> bool:
        if cls._is_disfavored_broad_money_match_sync(title, query):
            return False
        if cls._titles_overlap_sync(title, query):
            return True
        title_tokens = cls._normalized_match_tokens_sync(title)
        query_tokens = cls._normalized_match_tokens_sync(query)
        if not title_tokens or not query_tokens:
            return False

        finance_tokens = {
            "ai",
            "automat",
            "bitcoin",
            "bot",
            "crypto",
            "cryptocurrency",
            "earn",
            "edge",
            "income",
            "immediate",
            "invest",
            "market",
            "money",
            "path",
            "platform",
            "profit",
            "quantum",
            "review",
            "software",
            "stock",
            "trad",
            "traderai",
        }
        if not any(token in query_tokens for token in finance_tokens):
            return False
        return any(token in title_tokens for token in finance_tokens)

    @classmethod
    def _is_disfavored_broad_money_match_sync(cls, title: str, query: str) -> bool:
        return cls._is_broad_money_query_sync(query) and cls._is_generic_money_listicle_title_sync(title)

    @classmethod
    def _is_broad_money_query_sync(cls, query: str) -> bool:
        query_tokens = cls._normalized_match_tokens_sync(query)
        if not query_tokens:
            return False
        if not any(token in query_tokens for token in cls._BROAD_MONEY_QUERY_TOKENS):
            return False
        return not any(token in query_tokens for token in cls._SPECIFIC_FINANCE_QUERY_TOKENS)

    @classmethod
    def _is_generic_money_listicle_title_sync(cls, title: str) -> bool:
        lowered = title.casefold()
        title_tokens = cls._normalized_match_tokens_sync(title)
        if not title_tokens:
            return False
        has_money_signal = any(token in title_tokens for token in cls._BROAD_MONEY_QUERY_TOKENS) or (
            "make money" in lowered or "earn money" in lowered
        )
        if not has_money_signal:
            return False
        if any(fragment in lowered for fragment in cls._GENERIC_MONEY_LISTICLE_SUBSTRINGS):
            return True
        has_listicle_signal = any(
            token in title_tokens for token in cls._GENERIC_MONEY_LISTICLE_TOKENS
        )
        if not has_listicle_signal:
            return False
        return bool(re.search(r"\b\d+\b", lowered)) or any(
            token in title_tokens for token in {"app", "paypal", "site", "survey", "website"}
        )

    @staticmethod
    def _titles_overlap_sync(title: str, query: str) -> bool:
        title_tokens = AndroidYouTubeNavigator._normalized_match_tokens_sync(title)
        query_tokens = AndroidYouTubeNavigator._normalized_match_tokens_sync(query)
        if not title_tokens or not query_tokens:
            return False
        matched_query_tokens = {
            query_token
            for query_token in query_tokens
            if any(
                AndroidYouTubeNavigator._tokens_related_sync(query_token, title_token)
                for title_token in title_tokens
            )
        }
        anchor_tokens = AndroidYouTubeNavigator._query_anchor_tokens_sync(query)
        if anchor_tokens and not any(
            any(
                AndroidYouTubeNavigator._tokens_related_sync(anchor_token, title_token)
                for title_token in title_tokens
            )
            for anchor_token in anchor_tokens
        ):
            return False
        if len(matched_query_tokens) >= 2:
            return True
        return bool(matched_query_tokens) and len(query_tokens) == 1

    @classmethod
    def _query_anchor_tokens_sync(cls, query: str) -> set[str]:
        query_tokens = cls._normalized_match_tokens_sync(query)
        anchor_tokens: set[str] = set()
        for token in query_tokens:
            if token in cls._BROAD_QUERY_TOKENS:
                continue
            if any(token in group for group in cls._SEMANTIC_TOKEN_GROUPS):
                continue
            anchor_tokens.add(token)
        return anchor_tokens

    @staticmethod
    def _normalized_match_tokens_sync(value: str) -> set[str]:
        tokens: set[str] = set()
        for raw in re.findall(r"[A-Za-zА-Яа-я0-9]+", value.casefold()):
            token = raw.strip()
            if len(token) < 4:
                continue
            for suffix in ("ments", "ment", "ings", "ing", "ers", "er", "ies", "es", "s"):
                if len(token) > len(suffix) + 2 and token.endswith(suffix):
                    token = token[: -len(suffix)]
                    break
            if len(token) >= 4:
                tokens.add(token)
        return tokens

    @staticmethod
    def _tokens_related_sync(left: str, right: str) -> bool:
        if left == right:
            return True
        for group in AndroidYouTubeNavigator._SEMANTIC_TOKEN_GROUPS:
            if left in group and right in group:
                return True
        shorter, longer = sorted((left, right), key=len)
        return len(shorter) >= 5 and longer.startswith(shorter)

    @staticmethod
    def _node_to_uiselector(node: object) -> str | None:
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

    def _find_first_sync(
        self,
        candidates: list[NativeSelectorCandidate],
        timeout_seconds: float,
    ) -> object:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for candidate in candidates:
                elements = self._find_candidate_sync(candidate)
                if elements:
                    return elements[0]
            time.sleep(1)
        raise AndroidUiError(f"Failed to find native element from {candidates}")

    def _find_candidate_sync(self, candidate: NativeSelectorCandidate) -> list[object]:
        kind = candidate.kind
        value = candidate.value
        if kind == "id":
            return list(self._driver.find_elements("id", value))
        if kind == "accessibility_id":
            return list(self._driver.find_elements("accessibility id", value))
        if kind == "text_contains":
            return list(
                self._driver.find_elements(
                    "-android uiautomator",
                    f'new UiSelector().textContains("{value}")',
                )
            )
        return []
