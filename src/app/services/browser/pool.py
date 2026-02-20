import asyncio
import logging
import math

from playwright.async_api import Browser, Playwright, async_playwright

from app.settings import PlaywrightConfig

logger = logging.getLogger(__name__)


class BrowserPool:
    def __init__(
        self,
        headless: bool,
        args: list[str],
        config: PlaywrightConfig,
    ) -> None:
        self._headless = headless
        self._args = args
        self._max_browsers = config.max_browsers
        self._contexts_per_browser = config.contexts_per_browser
        self._playwright: Playwright | None = None
        self._browsers: list[Browser] = []
        self._queues: list[asyncio.LifoQueue] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._playwright:
            self._playwright = await async_playwright().start()
            await self._scale(1)
            logger.debug(
                "Browser pool started browsers=%s max=%s slots_per=%s",
                self.browser_count,
                self._max_browsers,
                self._contexts_per_browser,
            )

    async def stop(self) -> None:
        await self._scale(0)
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.debug("Browser pool stopped browsers=%d", self.browser_count)

    async def _scale(self, target: int) -> None:
        if not self._playwright:
            self._playwright = await async_playwright().start()

        target = min(target, self._max_browsers)

        async with self._lock:
            while len(self._browsers) < target:
                browser = await self._playwright.chromium.launch(
                    headless=self._headless, args=self._args
                )
                self._browsers.append(browser)

                queue: asyncio.LifoQueue = asyncio.LifoQueue()
                for slot in range(self._contexts_per_browser):
                    queue.put_nowait(slot)
                self._queues.append(queue)

                await asyncio.sleep(0.5)

            while len(self._browsers) > target:
                browser = self._browsers.pop()
                await browser.close()
                self._queues.pop()

    async def scale_for_tasks(self, count: int) -> None:
        needed = math.ceil(count / self._contexts_per_browser)
        await self._scale(needed)

    async def scale_down(self) -> None:
        await self._scale(1)

    def get_browser(self, index: int) -> tuple[Browser, asyncio.LifoQueue]:
        idx = index % len(self._browsers)
        return self._browsers[idx], self._queues[idx]

    def get_browser_by_instance(self, browser: Browser) -> tuple[Browser, asyncio.LifoQueue]:
        idx = self._browsers.index(browser)
        return self._browsers[idx], self._queues[idx]

    @property
    def browser_count(self) -> int:
        return len(self._browsers)

    @property
    def max_parallel(self) -> int:
        return self._max_browsers * self._contexts_per_browser

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.stop()
