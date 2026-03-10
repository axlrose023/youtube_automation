import logging
import random
from typing import TYPE_CHECKING

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..core.config import IDLE_FATIGUE_THRESHOLD, IDLE_FATIGUED_RANGE, IDLE_NORMAL_RANGE
from ..core.selectors import (
    CONSENT_SELECTORS,
    RECOMMENDED_SELECTORS,
    VIDEO_SELECTORS,
    YOUTUBE_URL,
)
from ..core.state import SessionState
from .humanizer import Humanizer
from .video_finder import VideoFinder

if TYPE_CHECKING:
    from .searcher import Searcher

logger = logging.getLogger(__name__)


class Navigator:
    def __init__(
        self,
        page: Page,
        state: SessionState,
        humanizer: Humanizer,
        finder: VideoFinder,
    ) -> None:
        self._page = page
        self._state = state
        self._h = humanizer
        self._finder = finder
        self._searcher: Searcher | None = None

    def attach_searcher(self, searcher: "Searcher") -> None:
        self._searcher = searcher

    async def open_youtube(self) -> None:
        await self._page.goto(YOUTUBE_URL, wait_until="domcontentloaded", timeout=30_000)
        logger.info("Session %s: opened YouTube", self._state.session_id)
        self._state.on_video_page = False
        await self.dismiss_consent()
        await self._h.delay(1.0, 2.0)

    async def has_feed_content(self) -> bool:
        for selector in VIDEO_SELECTORS:
            try:
                video_element = await self._page.query_selector(selector)
                if video_element:
                    return True
            except Exception:
                continue
        return False

    async def safe_go_home(self) -> None:
        try:
            await self._page.goto(YOUTUBE_URL, wait_until="domcontentloaded", timeout=15_000)
            self._state.on_video_page = False
            await self._h.delay(1.0, 3.0)
        except Exception:
            logger.warning("Session %s: failed to navigate home", self._state.session_id)

    async def go_home(self) -> None:
        await self.safe_go_home()

    async def go_back(self) -> None:
        url = self._page.url
        if not url or "youtube.com" not in url:
            await self.safe_go_home()
            return
        try:
            await self._page.go_back(timeout=5000)
            await self._h.delay(1.0, 2.5)
            if "youtube.com" not in self._page.url:
                await self.safe_go_home()
            else:
                self._state.on_video_page = False
        except PlaywrightTimeout:
            await self.safe_go_home()

    async def search(self) -> None:
        await self._get_searcher().search()

    async def refine_search(self) -> None:
        await self._get_searcher().refine_search()

    async def scroll_feed(self) -> None:
        direction = "down"
        if "/watch" in self._page.url or self._state.on_video_page:
            direction = "up" if random.random() < 0.7 else "down"
        await self._h.scroll(direction, amount=random.randint(2, 5))

    async def idle(self) -> None:
        pause = random.uniform(*IDLE_NORMAL_RANGE)
        if self._state.fatigue > IDLE_FATIGUE_THRESHOLD:
            pause = random.uniform(*IDLE_FATIGUED_RANGE)
        logger.info("Session %s: idle for %.0fs", self._state.session_id, pause)
        await self._h.delay(pause, pause)
        if random.random() < 0.5:
            await self._h.wiggle_mouse()

    async def click_any_video(self) -> bool:
        on_watch_page = "/watch" in self._page.url or self._state.on_video_page
        selectors = RECOMMENDED_SELECTORS if on_watch_page else VIDEO_SELECTORS + RECOMMENDED_SELECTORS
        clicked = await self._finder.find_and_click(
            selectors,
            limit=8,
            require_topic_match=bool(self._state.topics),
            preferred_topic=self._state.current_topic if on_watch_page else None,
            allow_shorts=False,
        )
        return clicked

    async def click_recommended(self) -> bool:
        topical_click = await self._finder.find_and_click(
            RECOMMENDED_SELECTORS,
            limit=10,
            require_topic_match=bool(self._state.topics),
            preferred_topic=self._state.current_topic,
            allow_shorts=False,
        )
        if topical_click:
            return True

        if not self._state.topics:
            return False

        logger.info(
            "Session %s: no recommendation for current topic, retry with any input topic",
            self._state.session_id,
        )
        return await self._finder.find_and_click(
            RECOMMENDED_SELECTORS,
            limit=8,
            require_topic_match=True,
            preferred_topic=None,
            allow_shorts=False,
        )

    async def recover_from_no_video(self) -> None:
        url = self._page.url
        try:
            if "/watch" in url or self._state.on_video_page:
                await self._finder.reset_view(force=True)
                await self._h.delay(0.3, 0.8)
                return

            await self._h.scroll("up", amount=random.randint(2, 4))
            await self._h.delay(0.3, 0.8)
        except Exception:
            logger.warning("Session %s: recover_from_no_video failed", self._state.session_id)

    async def dismiss_consent(self) -> None:
        for selector in CONSENT_SELECTORS:
            try:
                consent_button = await self._page.query_selector(selector)
                if consent_button and await consent_button.is_visible():
                    await consent_button.click()
                    await self._h.delay(0.5, 1.5)
                    return
            except Exception:
                continue

    def _get_searcher(self) -> "Searcher":
        if self._searcher is None:
            raise RuntimeError("Navigator searcher is not attached")
        return self._searcher
