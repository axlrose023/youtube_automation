import logging
import random

from playwright.async_api import ElementHandle, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..core.session.state import SessionState
from .humanizer import Humanizer

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
    "рын",
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
)
_FINANCE_NEGATIVE_TITLE_HINTS = (
    "out of control",
    "eat you alive",
    "do this asap",
    "get rich",
    "overnight",
    "secret",
    "shocking",
    "insane",
    "won't believe",
    "will shock you",
    "make millions",
    "millions",
    "face melting rally",
    "extreme breakout",
    "breakout",
    "crash has begun",
    "countdown to",
    "taking control",
    "undervalued",
    "price prediction",
    "prediction",
    "bull run",
    "to the moon",
    "100x",
    "whales",
    "smashes past",
    "skyrockets",
    "breaking",
    "surges",
    "explodes",
)
_FINANCE_ENTERTAINMENT_TITLE_HINTS = (
    "crimson desert",
    "black desert",
    "hero wars",
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
_FINANCE_GENERAL_TOKENS = ("finance", "financial", "financial literacy", "personal finance")
_FINANCIAL_MARKET_TOKENS = ("financial market", "financial markets", "capital market", "capital markets")
_INVESTMENT_TOKENS = ("investing", "investment", "investments", "portfolio", "diversif")
_STOCK_TOKENS = ("stock market", "stocks", "equities", "equity market")
_CRYPTO_TOKENS = ("crypto", "cryptocurrency", "digital asset", "bitcoin", "ethereum")
_EARNING_TOKENS = ("passive income", "side income", "earn money", "make money", "income", "yield", "staking")
_INCOME_POSITIVE_TOKENS = (
    "dividend",
    "yield",
    "staking",
    "stake",
    "invest",
    "investment",
    "portfolio",
    "reit",
    "crypto",
    "forex",
    "trading",
    "apy",
    "apr",
)
_INCOME_NEGATIVE_TOKENS = (
    "side job",
    "side jobs",
    "job ideas",
    "jobs for",
    "gig",
    "gigs",
    "freelance",
    "freelancer",
    "work from home",
    "remote work",
    "hustle",
    "hustles",
    "doordash",
    "uber",
    "babysitting",
)


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
            if self._should_stay_strict_on_preferred_topic(preferred_topic):
                logger.info(
                    "Session %s: no candidate for preferred topic '%s', staying strict to avoid drift",
                    self._state.session_id,
                    preferred_topic,
                )
                return False
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

                clickable_elements: list[tuple[float, ElementHandle, str | None]] = []
                href_only_elements: list[tuple[float, ElementHandle, str | None]] = []
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

                        title = await self._extract_element_title(candidate_element)
                        if require_topic_match:
                            if not self._candidate_matches_topic(title, preferred_topic):
                                continue
                            topic_matched_elements += 1
                        score = self._score_candidate(title, preferred_topic)

                        box = await candidate_element.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            clickable_elements.append((score, candidate_element, title))
                            if len(clickable_elements) >= limit:
                                break
                        else:
                            href_only_elements.append((score, candidate_element, title))
                    except Exception:
                        continue

                logger.info(
                    "Session %s: selector '%s' -> %d total, %d href, %d topic_match, %d with bbox, %d href_only",
                    self._state.session_id,
                    selector,
                    len(candidate_elements),
                    elements_with_video_href,
                    topic_matched_elements,
                    len(clickable_elements),
                    len(href_only_elements),
                )
                if seen_filtered_elements:
                    logger.info(
                        "Session %s: selector '%s' -> skipped_seen=%d",
                        self._state.session_id,
                        selector,
                        seen_filtered_elements,
                    )
                prefer_best_match = bool(
                    preferred_topic
                    and require_topic_match
                    and self._is_finance_context()
                    and not self._state.all_topics_covered()
                )
                if clickable_elements:
                    return self._pick_ranked_candidate(
                        selector,
                        clickable_elements,
                        href_only=False,
                        prefer_best_match=prefer_best_match,
                    )
                if href_only_elements:
                    logger.info(
                        "Session %s: selector '%s' -> falling back to href-only candidate",
                        self._state.session_id,
                        selector,
                    )
                    return self._pick_ranked_candidate(
                        selector,
                        href_only_elements,
                        href_only=True,
                        prefer_best_match=prefer_best_match,
                    )

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

        await self._prepare_element_for_click(video_element)

        try:
            await self._h.click(video_element)
        except Exception as exc:
            if not fallback_url:
                logger.warning("Session %s: click failed without fallback url: %s", self._state.session_id, exc)
                return False
            logger.info(
                "Session %s: click failed (%s), navigating directly to %s",
                self._state.session_id,
                type(exc).__name__,
                href,
            )
            return await self._navigate_to_fallback(fallback_url)

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
            retried = await self._retry_direct_click(video_element, target_video_id)
            if retried:
                return True
            logger.info(
                "Session %s: click missed, navigating to %s",
                self._state.session_id,
                href,
            )
            return await self._navigate_to_fallback(fallback_url)

        self._state.on_video_page = "/watch" in current_url
        if self._state.on_video_page:
            self._state.last_clicked_video_url = current_url
            self._state.mark_video_seen(current_url)
        return self._state.on_video_page

    async def _prepare_element_for_click(self, video_element: ElementHandle) -> None:
        try:
            await video_element.scroll_into_view_if_needed(timeout=2_500)
            await self._h.delay(0.2, 0.6)
        except Exception:
            return

    async def _retry_direct_click(
        self,
        video_element: ElementHandle,
        target_video_id: str | None,
    ) -> bool:
        try:
            await video_element.scroll_into_view_if_needed(timeout=1_500)
        except Exception:
            pass

        click_error = None
        try:
            await video_element.click(timeout=2_500, force=True)
        except Exception as exc:
            click_error = exc

        if click_error is not None:
            try:
                await video_element.evaluate("(el) => el.click()")
            except Exception:
                logger.debug(
                    "Session %s: direct click retry failed: %s",
                    self._state.session_id,
                    type(click_error).__name__,
                )
                return False

        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=4_000)
        except PlaywrightTimeout:
            pass

        await self._h.delay(0.3, 0.7)
        current_url = self._page.url
        if "/watch" not in current_url:
            return False
        if target_video_id is not None:
            current_video_id = self._state.video_id_from_url(current_url)
            if current_video_id != target_video_id:
                return False

        self._state.on_video_page = True
        self._state.last_clicked_video_url = current_url
        self._state.mark_video_seen(current_url)
        return True

    async def _navigate_to_fallback(self, full_url: str) -> bool:
        if not full_url:
            return False
        try:
            await self._page.goto(full_url, wait_until="domcontentloaded", timeout=10_000)
            await self._h.delay(0.5, 1.0)
            self._state.on_video_page = True
            self._state.last_clicked_video_url = self._page.url
            self._state.mark_video_seen(self._page.url or full_url)
            return True
        except Exception:
            logger.warning("Session %s: fallback navigation failed", self._state.session_id)
            return False

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

    def _pick_ranked_candidate(
        self,
        selector: str,
        candidates: list[tuple[float, ElementHandle, str | None]],
        *,
        href_only: bool,
        prefer_best_match: bool = False,
    ) -> ElementHandle:
        ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
        preview = ", ".join(
            f"{score:.1f}:{(title or '<unknown>')[:72]}"
            for score, _, title in ranked[:3]
        )
        logger.info(
            "Session %s: selector '%s' ranked %s candidates -> %s",
            self._state.session_id,
            selector,
            "href-only" if href_only else "clickable",
            preview or "<none>",
        )

        if prefer_best_match:
            _, element, _ = ranked[0]
            return element

        top_candidates = ranked[: min(3, len(ranked))]
        min_score = min(score for score, _, _ in top_candidates)
        weights = [max(0.25, score - min_score + 0.75) for score, _, _ in top_candidates]
        _, element, _ = random.choices(top_candidates, weights=weights, k=1)[0]
        return element

    def _score_candidate(self, title: str | None, preferred_topic: str | None) -> float:
        normalized = (title or "").strip().lower()
        if not normalized:
            return -2.0

        score = 0.5
        matched_topics = self._state.matched_topics_for_title(title)
        score += len(matched_topics) * 1.5

        if preferred_topic and self._state.is_title_on_specific_topic(title, preferred_topic):
            score += 3.5
        score += self._score_preferred_topic_specificity(normalized, preferred_topic)

        word_count = len(normalized.split())
        if word_count >= 4:
            score += 0.4
        if word_count >= 7:
            score += 0.3

        if self._is_finance_context():
            score += self._score_finance_title_quality(normalized)
            score += self._score_finance_noise_penalty(normalized, preferred_topic)

        if normalized.startswith("i ") or normalized.startswith("i'm ") or normalized.startswith("my "):
            score -= 1.0

        return score

    def _score_preferred_topic_specificity(
        self,
        normalized_title: str,
        preferred_topic: str | None,
    ) -> float:
        normalized_topic = " ".join((preferred_topic or "").lower().split())
        if not normalized_topic:
            return 0.0

        score = 0.0
        if "financial markets" in normalized_topic:
            if self._contains_any(normalized_title, _FINANCIAL_MARKET_TOKENS):
                score += 2.6
            elif self._contains_any(normalized_title, _STOCK_TOKENS):
                score -= 1.2
            elif self._contains_any(normalized_title, _CRYPTO_TOKENS):
                score -= 1.0
        elif normalized_topic in {"investments", "investment", "investing"}:
            if self._contains_any(normalized_title, _INVESTMENT_TOKENS):
                score += 2.2
            elif self._contains_any(normalized_title, _STOCK_TOKENS):
                score -= 0.8
            elif self._contains_any(normalized_title, _CRYPTO_TOKENS):
                score -= 0.5
        elif "bitcoin" in normalized_topic:
            if "bitcoin" in normalized_title:
                score += 1.6
            elif "crypto" in normalized_title:
                score -= 0.5
        elif "ethereum" in normalized_topic:
            if "ethereum" in normalized_title:
                score += 1.6
            elif "crypto" in normalized_title:
                score -= 0.4
        elif "crypto" in normalized_topic:
            if "crypto" in normalized_title or "cryptocurrency" in normalized_title:
                score += 1.5
        elif "stock market" in normalized_topic or normalized_topic == "stocks":
            if self._contains_any(normalized_title, _STOCK_TOKENS):
                score += 1.8
            elif self._contains_any(normalized_title, _FINANCE_GENERAL_TOKENS):
                score -= 0.6
        elif "finance" in normalized_topic:
            if self._contains_any(normalized_title, _FINANCE_GENERAL_TOKENS):
                score += 2.4
            elif self._contains_any(normalized_title, _FINANCIAL_MARKET_TOKENS):
                score += 1.3
            elif self._contains_any(normalized_title, _STOCK_TOKENS):
                score -= 1.2
            elif self._contains_any(normalized_title, _CRYPTO_TOKENS):
                score -= 1.0
        elif "passive income" in normalized_topic or "side income" in normalized_topic:
            if self._contains_any(normalized_title, _INCOME_POSITIVE_TOKENS):
                score += 2.4
            if self._contains_any(normalized_title, _INCOME_NEGATIVE_TOKENS):
                score -= 3.0

        return score

    def _score_finance_title_quality(self, normalized_title: str) -> float:
        score = 0.0
        for hint in _FINANCE_POSITIVE_TITLE_HINTS:
            if hint in normalized_title:
                score += 0.8
        for hint in _FINANCE_NEGATIVE_TITLE_HINTS:
            if hint in normalized_title:
                score -= 1.6
        if "news" in normalized_title:
            score -= 0.5
        if "opinion" in normalized_title or "reacts" in normalized_title:
            score -= 0.8
        if "!" in normalized_title:
            score -= 0.5
        if normalized_title.count("!") >= 2:
            score -= 0.8
        return score

    def _score_finance_noise_penalty(
        self,
        normalized_title: str,
        preferred_topic: str | None,
    ) -> float:
        score = 0.0
        for hint in _FINANCE_ENTERTAINMENT_TITLE_HINTS:
            if hint in normalized_title:
                score -= 4.0

        normalized_topic = " ".join((preferred_topic or "").lower().split())
        if normalized_topic and self._contains_any(normalized_topic, _EARNING_TOKENS):
            if self._contains_any(normalized_title, _FINANCE_ENTERTAINMENT_TITLE_HINTS):
                score -= 3.0
        return score

    @staticmethod
    def _contains_any(normalized_title: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in normalized_title for phrase in phrases)

    def _is_finance_context(self) -> bool:
        topics_blob = " ".join(filter(None, [*self._state.topics, self._state.current_topic or ""])).lower()
        return any(hint in topics_blob for hint in _FINANCE_TOPIC_HINTS)

    def _should_stay_strict_on_preferred_topic(self, preferred_topic: str | None) -> bool:
        return bool(
            preferred_topic
            and self._is_finance_context()
            and not self._state.all_topics_covered()
        )
