import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser.navigator import Navigator
from ..browser.watcher import VideoWatcher
from ..core.selectors import MAX_CONSECUTIVE_FAILURES
from ..core.state import SessionState
from .clock import SessionClock

logger = logging.getLogger(__name__)

_WATCH_ACTIONS = {
    "watch_long",
    "watch_focused",
    "surf_video",
    "click_recommended",
}


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
        self._handlers: dict[str, Callable[[], Awaitable[None]]] = {
            "click_recommended": watcher.click_recommended,
            "watch_long": watcher.watch_long,
            "watch_focused": watcher.watch_focused,
            "surf_video": watcher.surf_video,
            "scroll_feed": navigator.scroll_feed,
            "scroll_results": navigator.scroll_feed,
            "search": navigator.search,
            "refine_search": navigator.refine_search,
            "idle": navigator.idle,
            "go_home": navigator.go_home,
            "go_back": navigator.go_back,
        }

    async def execute(self, action: str) -> None:
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
        except asyncio.TimeoutError:
            self._state.consecutive_fails += 1
            await self._cancel_task(action_task)
            logger.warning(
                "Session %s: action timeout (%d) on %s after %.0fs",
                self._state.session_id, self._state.consecutive_fails, action, timeout_s,
            )
        except PlaywrightTimeout:
            self._state.consecutive_fails += 1
            logger.warning(
                "Session %s: timeout (%d) on %s",
                self._state.session_id, self._state.consecutive_fails, action,
            )
        except Exception:
            self._state.consecutive_fails += 1
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

    def _action_timeout_seconds(self, action: str) -> float:
        remaining = self._clock.remaining_seconds()
        if remaining <= 0:
            return 0.0
        if action in _WATCH_ACTIONS:
            return max(remaining + 20.0, 20.0)
        return min(max(remaining, 5.0), 45.0)

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
