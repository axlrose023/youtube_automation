import logging
from typing import Protocol

import httpx
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from app.settings import AdsPowerConfig, PlaywrightConfig, ViewportConfig

from .context import ContextFactory
from .pool import BrowserPool
from .useragent import UserAgentProvider

logger = logging.getLogger(__name__)


class BrowserSessionProvider(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def acquire_context(self) -> BrowserContext: ...
    async def release_context(self, ctx: BrowserContext) -> None: ...


class ChromiumSessionProvider:
    def __init__(
        self,
        playwright_config: PlaywrightConfig,
        viewport_config: ViewportConfig,
        user_agent_provider: UserAgentProvider,
    ) -> None:
        self._headless = playwright_config.headless
        self._browser_args = playwright_config.browser_args
        self._pool = BrowserPool(
            headless=self._headless,
            args=self._browser_args,
            config=playwright_config,
        )
        self._context_factory = ContextFactory(user_agent_provider, viewport_config)
        self._contexts: dict[BrowserContext, int] = {}
        self._task_index = 0

    async def start(self) -> None:
        await self._pool.start()

    async def stop(self) -> None:
        await self._pool.stop()

    async def acquire_context(self) -> BrowserContext:
        browser, queue = self._pool.get_browser(self._task_index)
        self._task_index += 1
        slot = await queue.get()
        ctx, _ = await self._context_factory.create(browser)
        self._contexts[ctx] = slot
        return ctx

    async def release_context(self, ctx: BrowserContext) -> None:
        slot = self._contexts.pop(ctx)
        browser = ctx.browser
        await ctx.close()
        if browser:
            _, queue = self._pool.get_browser_by_instance(browser)
            queue.put_nowait(slot)


class AdsPowerSessionProvider:
    def __init__(self, config: AdsPowerConfig) -> None:
        self._base_url = config.base_url.rstrip("/")
        self._user_id = config.user_id
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/start",
                params={"user_id": self._user_id},
                timeout=30.0,
            )
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"AdsPower failed to start profile {self._user_id}: {data.get('msg')}"
            )

        ws_endpoint = data["data"]["ws"]["puppeteer"]
        self._browser = await self._playwright.chromium.connect_over_cdp(ws_endpoint)
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context()
        logger.debug("AdsPower session started for profile %s", self._user_id)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/stop",
                params={"user_id": self._user_id},
                timeout=10.0,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(
                    "AdsPower failed to stop profile %s: %s",
                    self._user_id,
                    data.get("msg"),
                )

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.debug("AdsPower session stopped for profile %s", self._user_id)

    async def acquire_context(self) -> BrowserContext:
        if not self._context:
            raise RuntimeError("AdsPower session not started")
        return self._context

    async def release_context(self, ctx: BrowserContext) -> None:
        pass
