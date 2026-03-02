import logging
import random
from collections.abc import Awaitable, Callable

from playwright.async_api import ElementHandle, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..core.selectors import (
    SEARCH_BUTTON,
    SEARCH_BUTTON_SELECTORS,
    SEARCH_INPUT_SELECTORS,
    SEARCH_MODIFIERS,
)
from ..core.state import SessionState
from .humanizer import Humanizer

logger = logging.getLogger(__name__)


class Searcher:
    def __init__(
        self,
        page: Page,
        state: SessionState,
        humanizer: Humanizer,
        dismiss_consent: Callable[[], Awaitable[None]],
        go_home: Callable[[], Awaitable[None]],
    ) -> None:
        self._page = page
        self._state = state
        self._h = humanizer
        self._dismiss_consent = dismiss_consent
        self._go_home = go_home

    async def search(self) -> None:
        unsearched = self._state.unsearched_topics()
        style = self._state.personality.search_style
        if unsearched:
            if random.random() > style:
                topic = unsearched[0]
            else:
                topic = random.choice(unsearched)
        elif self._state.topics:
            if random.random() > style and self._state.current_topic in self._state.topics:
                topic = self._state.current_topic
            else:
                topic = random.choice(self._state.topics)
        else:
            topic = "youtube"

        logger.info("Session %s: searching '%s'", self._state.session_id, topic)
        self._state.current_topic = topic
        await self._execute_search(topic, mark_topic_as_covered=topic)

    async def refine_search(self) -> None:
        if not self._state.topics:
            await self.search()
            return

        if self._state.current_topic:
            base_topic = self._state.current_topic
        elif self._state.searched_topics:
            base_topic = self._state.searched_topics[-1]
        else:
            await self.search()
            return

        modifier = random.choice(SEARCH_MODIFIERS)
        refined = f"{base_topic} {modifier}"
        logger.info("Session %s: refine_search '%s' -> '%s'", self._state.session_id, base_topic, refined)
        self._state.current_topic = base_topic
        await self._execute_search(refined, fallback_to_regular_search=True)

    async def _execute_search(
        self,
        topic: str,
        *,
        fallback_to_regular_search: bool = False,
        mark_topic_as_covered: str | None = None,
    ) -> None:
        await self._dismiss_consent()
        search_input = await self._find_search_input()
        if not search_input:
            await self._go_home()
            await self._dismiss_consent()
            search_input = await self._find_search_input(timeout=10_000)
            if not search_input:
                if fallback_to_regular_search:
                    logger.warning(
                        "Session %s: refine input not found, fallback to regular search",
                        self._state.session_id,
                    )
                    await self.search()
                    return
                raise PlaywrightTimeout(f"Search input not found for session {self._state.session_id}")

        await search_input.click(click_count=3)
        await self._h.delay(0.1, 0.3)
        await self._page.keyboard.press("Backspace")
        await self._h.delay(0.1, 0.3)
        await self._h.type_text(topic)
        await self._h.delay(0.3, 0.8)

        await self._submit_search()

        await self._page.wait_for_load_state("domcontentloaded", timeout=10_000)
        await self._h.delay(1.5, 3.0)

        if mark_topic_as_covered and mark_topic_as_covered not in self._state.searched_topics:
            self._state.searched_topics.append(mark_topic_as_covered)
        self._state.on_video_page = False
        self._state.surf_streak = 0

    async def _find_search_input(self, timeout: int = 6000) -> ElementHandle | None:
        per_selector_timeout = max(timeout // len(SEARCH_INPUT_SELECTORS), 1000)
        for selector in SEARCH_INPUT_SELECTORS:
            try:
                search_input_element = await self._page.wait_for_selector(selector, timeout=per_selector_timeout)
                if search_input_element:
                    return search_input_element
            except PlaywrightTimeout:
                continue
        return None

    async def _submit_search(self) -> None:
        if random.random() < 0.6:
            await self._page.keyboard.press("Enter")
        else:
            search_button = await self._page.query_selector(", ".join(SEARCH_BUTTON_SELECTORS))
            if not search_button:
                search_button = await self._page.query_selector(SEARCH_BUTTON)
            if search_button:
                await self._h.click(search_button)
            else:
                await self._page.keyboard.press("Enter")
