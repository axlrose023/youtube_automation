from __future__ import annotations

import logging
import random
import re
from urllib.parse import quote_plus
from typing import TYPE_CHECKING

from playwright.async_api import ElementHandle, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..config import TOPIC_BALANCE_ROTATE_SEARCH_PROBABILITY
from ..selectors import (
    SEARCH_BUTTON,
    SEARCH_BUTTON_SELECTORS,
    SEARCH_INPUT_SELECTORS,
    VIDEO_SELECTORS,
)
from ..session.state import SessionState
from .humanizer import Humanizer

if TYPE_CHECKING:
    from .navigator import Navigator

logger = logging.getLogger(__name__)
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
    "крипт",
    "битко",
    "финанс",
    "инвест",
)
_FINANCE_SEARCH_MODIFIERS = (
    "explained",
    "guide",
    "tutorial",
    "for beginners",
    "analysis",
)
_FINANCE_QUERY_HINTS = (
    "explained",
    "guide",
    "tutorial",
    "analysis",
    "for beginners",
    "basics",
    "overview",
)
_FINANCE_TOPIC_QUERY_TEMPLATES = (
    ("financial markets", "financial markets explained capital markets"),
    ("stock market", "stock market explained investing basics"),
    ("stocks", "stocks explained for beginners"),
    ("crypto investments", "crypto investing for beginners portfolio"),
    ("crypto earnings", "crypto staking yield passive income for beginners"),
    ("passive income", "passive income investing dividends staking explained"),
    ("side income", "passive income dividends staking forex crypto investing explained"),
    ("investments", "investing basics portfolio investing for beginners"),
    ("investment", "investment basics portfolio investing"),
    ("crypto", "crypto for beginners explained"),
    ("forex trading", "forex trading for beginners mt5 broker basics"),
    ("bitcoin", "bitcoin explained"),
    ("ethereum", "ethereum explained"),
    ("finance", "financial literacy and finance basics explained"),
)
_CRYPTO_TOKENS = {"crypto", "cryptocurrency", "bitcoin", "btc", "ethereum", "eth", "defi", "blockchain"}
_CRYPTO_EARN_TOKENS = {"earn", "earning", "earnings", "money", "profit", "profits", "yield", "staking", "stake", "passive", "income", "apy", "apr", "rewards"}
_FOREX_TOKENS = {"forex", "fx", "cfd", "mt4", "mt5", "broker"}


class Searcher:
    def __init__(
        self,
        page: Page,
        state: SessionState,
        humanizer: Humanizer,
        navigator: Navigator,
    ) -> None:
        self._page = page
        self._state = state
        self._h = humanizer
        self._nav = navigator

    async def search(self) -> None:
        forced_topic = (self._state.forced_search_topic or "").strip()
        if forced_topic and forced_topic in self._state.topics:
            topic = forced_topic
            self._state.forced_search_topic = None
        else:
            unsearched = self._state.unsearched_topics()
            style = self._state.personality.search_style
            if unsearched:
                if random.random() > style:
                    topic = unsearched[0]
                else:
                    topic = random.choice(unsearched)
            elif self._state.topics:
                rebalance_topic = None
                if self._state.topic_balance_enabled():
                    rebalance_topic = self._state.least_covered_topic()

                if (
                    rebalance_topic
                    and rebalance_topic != self._state.current_topic
                    and random.random() < TOPIC_BALANCE_ROTATE_SEARCH_PROBABILITY
                ):
                    topic = rebalance_topic
                    logger.info(
                        "Session %s: topic balance rotate search -> '%s' (coverage=%s)",
                        self._state.session_id,
                        topic,
                        self._state.topic_watch_seconds_map(),
                    )
                elif random.random() > style and self._state.current_topic in self._state.topics:
                    topic = self._state.current_topic
                else:
                    topic = random.choice(self._state.topics)
            else:
                topic = "youtube"

        query = self._build_search_query(topic)
        if query == topic:
            logger.info("Session %s: searching '%s'", self._state.session_id, topic)
        else:
            logger.info(
                "Session %s: searching '%s' via enriched query '%s'",
                self._state.session_id,
                topic,
                query,
            )
        self._state.current_topic = topic
        await self._execute_search(query, mark_topic_as_covered=topic)

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

        logger.info("Session %s: repeat_search '%s'", self._state.session_id, base_topic)
        self._state.current_topic = base_topic
        await self._execute_search(base_topic, fallback_to_regular_search=True)

    async def _execute_search(
        self,
        topic: str,
        *,
        fallback_to_regular_search: bool = False,
        mark_topic_as_covered: str | None = None,
    ) -> None:
        await self._nav.dismiss_consent()
        search_input = await self._find_search_input()
        if not search_input:
            await self._nav.safe_go_home()
            await self._nav.dismiss_consent()
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
        await self._ensure_search_results_page(topic)
        await self._page.wait_for_load_state("domcontentloaded", timeout=10_000)
        await self._h.delay(1.5, 3.0)
        await self._scan_results()

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

    async def _ensure_search_results_page(self, query: str) -> None:
        try:
            await self._page.wait_for_url(re.compile(r".*/results.*"), timeout=4_000)
        except PlaywrightTimeout:
            pass

        if "/results" in (self._page.url or ""):
            return

        direct_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        logger.warning(
            "Session %s: search submit stayed on %s, falling back to direct results url",
            self._state.session_id,
            self._page.url or "<unknown>",
        )
        await self._page.goto(direct_url, wait_until="domcontentloaded", timeout=10_000)
        await self._h.delay(0.8, 1.6)

    async def _scan_results(self) -> None:
        if "/results" not in (self._page.url or ""):
            return
        if not await self._has_result_candidates():
            await self._h.delay(0.7, 1.4)
            return

        await self._h.delay(0.8, 1.8)
        candidates = await self._collect_result_candidates(limit=10)
        if not candidates:
            return

        hovered = 0
        for candidate in random.sample(candidates, k=min(len(candidates), random.randint(1, 3))):
            try:
                box = await candidate.bounding_box()
                if not box or box["width"] <= 0 or box["height"] <= 0:
                    continue

                target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
                target_y = box["y"] + box["height"] * random.uniform(0.30, 0.70)
                await self._page.mouse.move(target_x, target_y, steps=random.randint(8, 16))
                hovered += 1
                await self._h.delay(0.4, 1.0)
            except Exception:
                continue

        if random.random() < 0.65:
            await self._page.mouse.wheel(0, random.randint(120, 380))
            await self._h.delay(0.3, 0.8)
            if random.random() < 0.35:
                await self._page.mouse.wheel(0, -random.randint(60, 220))
                await self._h.delay(0.2, 0.6)

        logger.info("Session %s: scanned search results before click (hovered=%d)", self._state.session_id, hovered)

    def _build_search_query(self, topic: str) -> str:
        return " ".join(topic.split())

    def _is_finance_context(self, topic: str) -> bool:
        topics_blob = " ".join(filter(None, [*self._state.topics, self._state.current_topic or "", topic])).lower()
        return any(hint in topics_blob for hint in _FINANCE_TOPIC_HINTS)

    async def _has_result_candidates(self) -> bool:
        for _ in range(6):
            for selector in VIDEO_SELECTORS:
                try:
                    if await self._page.query_selector(selector):
                        return True
                except Exception:
                    continue
            await self._h.delay(0.2, 0.5)
        return False

    async def _collect_result_candidates(self, limit: int) -> list[ElementHandle]:
        candidates: list[ElementHandle] = []
        seen_hrefs: set[str] = set()
        for selector in VIDEO_SELECTORS:
            try:
                elements = await self._page.query_selector_all(selector)
            except Exception:
                continue

            for element in elements:
                try:
                    href = await element.get_attribute("href")
                except Exception:
                    continue
                if not href or "/watch" not in href:
                    continue
                normalized_href = href if href.startswith("http") else f"https://www.youtube.com{href}"
                if normalized_href in seen_hrefs:
                    continue
                seen_hrefs.add(normalized_href)
                candidates.append(element)
                if len(candidates) >= limit:
                    return candidates
        return candidates
