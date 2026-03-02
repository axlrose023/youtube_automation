import logging
import random

from playwright.async_api import ElementHandle, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..core.state import SessionState
from .humanizer import Humanizer

logger = logging.getLogger(__name__)


class VideoFinder:
    def __init__(self, page: Page, state: SessionState, humanizer: Humanizer) -> None:
        self._page = page
        self._state = state
        self._h = humanizer

    async def find_and_click(
        self,
        selectors: list[str],
        limit: int = 6,
        *,
        require_topic_match: bool = False,
        preferred_topic: str | None = None,
        allow_shorts: bool = False,
    ) -> bool:
        video_element = await self._find_clickable(
            selectors,
            limit,
            require_topic_match=require_topic_match,
            preferred_topic=preferred_topic,
            allow_shorts=allow_shorts,
        )
        if (
            not video_element
            and require_topic_match
            and preferred_topic
        ):
            logger.info(
                "Session %s: no candidate for preferred topic '%s', retrying with any input topic",
                self._state.session_id,
                preferred_topic,
            )
            video_element = await self._find_clickable(
                selectors,
                limit,
                require_topic_match=True,
                preferred_topic=None,
                allow_shorts=allow_shorts,
            )
        if not video_element:
            return False
        return await self._click_element(video_element)

    async def _find_clickable(
        self,
        selectors: list[str],
        limit: int,
        *,
        require_topic_match: bool,
        preferred_topic: str | None,
        allow_shorts: bool,
    ) -> ElementHandle | None:
        for attempt in range(2):
            if attempt == 1:
                await self.reset_view(force=True)

            await self.reset_view()

            for selector in selectors:
                candidate_elements = await self._page.query_selector_all(selector)
                if not candidate_elements:
                    try:
                        await self._page.wait_for_selector(selector, timeout=700)
                    except PlaywrightTimeout:
                        logger.debug("Session %s: selector timeout: %s", self._state.session_id, selector)
                    candidate_elements = await self._page.query_selector_all(selector)

                clickable_elements: list[ElementHandle] = []
                elements_with_video_href = 0
                topic_matched_elements = 0
                seen_filtered_elements = 0
                for candidate_element in candidate_elements[:limit * 6]:
                    try:
                        href = await candidate_element.get_attribute("href")
                        if not self._is_video_href(href):
                            continue
                        if not allow_shorts and "/shorts/" in (href or ""):
                            continue
                        candidate_url = self._to_absolute_url(href)
                        if self._state.is_seen_video(candidate_url):
                            seen_filtered_elements += 1
                            continue
                        elements_with_video_href += 1

                        if require_topic_match:
                            title = await self._extract_element_title(candidate_element)
                            if not self._candidate_matches_topic(title, preferred_topic):
                                continue
                            topic_matched_elements += 1

                        try:
                            await candidate_element.scroll_into_view_if_needed(timeout=1200)
                        except Exception:
                            pass

                        box = await candidate_element.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            clickable_elements.append(candidate_element)
                            if len(clickable_elements) >= limit:
                                break
                    except Exception:
                        continue

                logger.info(
                    "Session %s: selector '%s' -> %d total, %d href, %d topic_match, %d with bbox",
                    self._state.session_id,
                    selector,
                    len(candidate_elements),
                    elements_with_video_href,
                    topic_matched_elements,
                    len(clickable_elements),
                )
                if seen_filtered_elements:
                    logger.info(
                        "Session %s: selector '%s' -> skipped_seen=%d",
                        self._state.session_id,
                        selector,
                        seen_filtered_elements,
                    )
                if clickable_elements:
                    return random.choice(clickable_elements)

        return None

    async def _click_element(self, video_element: ElementHandle) -> bool:
        url_before = self._page.url
        previous_video_id = self._state.video_id_from_url(url_before)
        href = await video_element.get_attribute("href")
        title = await self._extract_element_title(video_element)
        fallback_url = ""
        target_video_id = None
        if href:
            fallback_url = href if href.startswith("http") else f"https://www.youtube.com{href}"
            target_video_id = self._state.video_id_from_url(fallback_url)
        self._state.last_clicked_video_title = title
        self._state.last_clicked_video_url = fallback_url or self._page.url
        logger.info(
            "Session %s: clicking video (href=%s, title=%s)",
            self._state.session_id,
            href,
            title or "<unknown>",
        )

        await self._h.click(video_element)

        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except PlaywrightTimeout:
            pass

        await self._h.delay(0.5, 1.0)

        current_url = self._page.url
        current_video_id = self._state.video_id_from_url(current_url)

        if (
            "/watch" in current_url
            and target_video_id
            and current_video_id == target_video_id
        ):
            self._state.on_video_page = True
            self._state.last_clicked_video_url = current_url
            self._state.mark_video_seen(current_url)
            return True

        click_missed = bool(href) and (
            current_url == url_before
            or (
                previous_video_id is not None
                and current_video_id is not None
                and current_video_id == previous_video_id
            )
        )
        if click_missed:
            logger.info(
                "Session %s: click missed, navigating to %s",
                self._state.session_id,
                href,
            )
            full = fallback_url
            try:
                await self._page.goto(full, wait_until="domcontentloaded", timeout=10_000)
                await self._h.delay(0.5, 1.0)
                self._state.on_video_page = True
                self._state.last_clicked_video_url = self._page.url
                self._state.mark_video_seen(self._page.url or full)
                return True
            except Exception:
                logger.warning("Session %s: fallback navigation failed", self._state.session_id)
                return False

        self._state.on_video_page = "/watch" in current_url
        if self._state.on_video_page:
            self._state.last_clicked_video_url = current_url
            self._state.mark_video_seen(current_url)
        return self._state.on_video_page

    async def _extract_element_title(self, video_element: ElementHandle) -> str | None:
        try:
            attr_title = await video_element.get_attribute("title")
            if attr_title:
                clean_attr = " ".join(attr_title.split())
                if clean_attr:
                    return clean_attr[:140]
        except Exception:
            pass

        try:
            text_content = await video_element.text_content()
            if text_content:
                clean_text = " ".join(text_content.split())
                if clean_text:
                    return clean_text[:140]
        except Exception:
            pass

        return None

    async def reset_view(self, force: bool = False) -> None:
        try:
            scroll_y = await self._page.evaluate("window.scrollY")
        except Exception:
            scroll_y = 0

        if not force and scroll_y < 900 and "/watch" not in self._page.url:
            return

        try:
            await self._page.keyboard.press("Home")
        except Exception:
            pass

        try:
            await self._page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            return

        await self._h.delay(0.2, 0.6)

        if "/watch" in self._page.url:
            try:
                await self._page.evaluate(
                    "(() => {"
                    " const secondary = document.querySelector('#secondary');"
                    " if (secondary) secondary.scrollIntoView({block: 'start'});"
                    "})()"
                )
            except Exception:
                pass

    @staticmethod
    def _is_video_href(href: str | None) -> bool:
        if not href:
            return False
        return "/watch" in href or "/shorts/" in href

    @staticmethod
    def _to_absolute_url(href: str | None) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        return f"https://www.youtube.com{href}"

    def _candidate_matches_topic(self, title: str | None, preferred_topic: str | None) -> bool:
        if preferred_topic:
            if self._state.is_title_on_specific_topic(title, preferred_topic):
                return True
            if not self._state.all_topics_covered():
                return False
        return self._state.is_title_on_topic(title)
