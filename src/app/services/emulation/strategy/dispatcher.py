import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.navigator import Navigator
from ..browser.watcher import VideoWatcher
from ..core.actions import SEARCH_ACTIONS, WATCH_ACTIONS, Action
from ..core.config import MAX_CONSECUTIVE_FAILURES
from ..core.session.state import SessionState
from .clock import SessionClock

logger = logging.getLogger(__name__)


class SessionRuntimeClosedError(RuntimeError):
    pass


class ActionDispatcher:
    def __init__(
        self,
        state: SessionState,
        navigator: Navigator,
        watcher: VideoWatcher,
        clock: SessionClock,
    ) -> None:
        self._state = state
        self._nav = navigator
        self._clock = clock
        self._handlers: dict[Action, Callable[[], Coroutine[Any, Any, None]]] = {
            Action.CLICK_RECOMMENDED: watcher.click_recommended,
            Action.WATCH_LONG: watcher.watch_long,
            Action.WATCH_FOCUSED: watcher.watch_focused,
            Action.SURF_VIDEO: watcher.surf_video,
            Action.SCROLL_FEED: navigator.scroll_feed,
            Action.SCROLL_RESULTS: navigator.scroll_feed,
            Action.SEARCH: navigator.search,
            Action.REFINE_SEARCH: navigator.refine_search,
            Action.IDLE: navigator.idle,
            Action.GO_HOME: navigator.go_home,
            Action.GO_BACK: navigator.go_back,
        }

    async def execute(self, action: Action) -> None:
        handler = self._handlers.get(action, self._nav.scroll_feed)
        timeout_s = self._action_timeout_seconds(action)
        if timeout_s <= 0:
            logger.info(
                "Session %s: skipping %s — no time left",
                self._state.session_id, action,
            )
            return

        started_at = time.monotonic()
        videos_before = self._state.videos_watched
        topics_before = len(self._state.searched_topics)
        action_task = asyncio.create_task(handler())

        try:
            await asyncio.wait_for(action_task, timeout=timeout_s)
            self._state.consecutive_fails = 0
            elapsed_s = time.monotonic() - started_at
            videos_delta = self._state.videos_watched - videos_before
            topics_delta = len(self._state.searched_topics) - topics_before
            logger.info(
                "Session %s: action %s completed in %.1fs (videos %+d, topics %+d, no_video_streak=%d, surf_streak=%d)",
                self._state.session_id,
                action,
                elapsed_s,
                videos_delta,
                topics_delta,
                self._state.no_video_streak,
                self._state.surf_streak,
            )
            self._update_anchor_streak(action, videos_delta)
        except TimeoutError:
            self._state.consecutive_fails += 1
            await self._cancel_task(action_task)
            if action in WATCH_ACTIONS:
                self._state.clear_current_watch()
            logger.warning(
                "Session %s: action timeout (%d) on %s after %.0fs",
                self._state.session_id, self._state.consecutive_fails, action, timeout_s,
            )
        except PlaywrightTimeout:
            self._state.consecutive_fails += 1
            if action in WATCH_ACTIONS:
                self._state.clear_current_watch()
            logger.warning(
                "Session %s: timeout (%d) on %s",
                self._state.session_id, self._state.consecutive_fails, action,
            )
        except Exception:
            self._state.consecutive_fails += 1
            if action in WATCH_ACTIONS:
                self._state.clear_current_watch()
            exc = action_task.exception() if action_task.done() else None
            if exc is not None and self._is_runtime_closed_error(exc):
                logger.error(
                    "Session %s: fatal runtime closure on %s",
                    self._state.session_id,
                    action,
                )
                raise SessionRuntimeClosedError(
                    f"Session runtime closed during {action}",
                ) from exc
            logger.exception(
                "Session %s: error (%d) on %s",
                self._state.session_id, self._state.consecutive_fails, action,
            )

        if self._state.consecutive_fails >= MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "Session %s: recovering from failure streak",
                self._state.session_id,
            )
            await self._nav.safe_go_home()
            self._state.consecutive_fails = 0

    def _action_timeout_seconds(self, action: Action) -> float:
        remaining = self._clock.remaining_seconds()
        if remaining <= 0:
            return 0.0
        if action in WATCH_ACTIONS:
            return max(remaining + 20.0, 20.0)
        return min(max(remaining, 5.0), 45.0)

    def _update_anchor_streak(self, action: Action, videos_delta: int) -> None:
        if action in SEARCH_ACTIONS:
            self._state.offtopic_or_reco_streak = 0
            return

        if videos_delta <= 0:
            return

        if action == Action.CLICK_RECOMMENDED:
            self._state.offtopic_or_reco_streak += 1
            logger.info(
                "Session %s: anchor streak +1 (reason=recommendation, streak=%d)",
                self._state.session_id,
                self._state.offtopic_or_reco_streak,
            )
            return

        if action not in (Action.WATCH_LONG, Action.WATCH_FOCUSED, Action.SURF_VIDEO):
            return

        if self._state.last_watch_on_topic is False:
            self._state.offtopic_or_reco_streak += 1
            logger.info(
                "Session %s: anchor streak +1 (reason=off-topic-watch, streak=%d)",
                self._state.session_id,
                self._state.offtopic_or_reco_streak,
            )
            return

        if self._state.offtopic_or_reco_streak:
            logger.info(
                "Session %s: anchor streak reset after on-topic watch",
                self._state.session_id,
            )
        self._state.offtopic_or_reco_streak = 0

    @staticmethod
    async def _cancel_task(action_task: asyncio.Task[None]) -> None:
        if action_task.done():
            return

        action_task.cancel()
        try:
            await action_task
        except asyncio.CancelledError:
            return
        except Exception:
            return

    @staticmethod
    def _is_runtime_closed_error(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "target page, context or browser has been closed",
                "target closed",
                "browser has been closed",
            )
        )
