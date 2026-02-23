import logging

from app.services.browser.provider import BrowserSessionProvider

logger = logging.getLogger(__name__)


class BrowserService:
    def __init__(self, session_provider: BrowserSessionProvider) -> None:
        self._session_provider = session_provider

    async def open_site(self, url: str) -> None:
        ctx = await self._session_provider.acquire_context()
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            logger.info("Opened %s", url)
        except Exception:
            await self._session_provider.release_context(ctx)
            raise
