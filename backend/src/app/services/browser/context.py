import logging
import random

from playwright.async_api import Browser, BrowserContext

from app.settings import ViewportConfig, get_config

from .useragent import UserAgentProvider

logger = logging.getLogger(__name__)


class ContextFactory:
    def __init__(
        self,
        user_agent_provider: UserAgentProvider | None = None,
        viewport_config: ViewportConfig | None = None,
    ) -> None:
        self._ua_provider = user_agent_provider or UserAgentProvider()
        self._viewport = viewport_config or get_config().viewport

    def _random_viewport(self) -> dict[str, int]:
        return {
            "width": random.randint(self._viewport.width_min, self._viewport.width_max),
            "height": random.randint(
                self._viewport.height_min, self._viewport.height_max
            ),
        }

    async def create(
        self,
        browser: Browser,
    ) -> tuple[BrowserContext, dict]:
        viewport = self._random_viewport()
        user_agent = self._ua_provider.get()

        ctx = await browser.new_context(viewport=viewport, user_agent=user_agent)
        meta = {
            "user_agent": user_agent,
            "viewport": viewport,
        }
        return ctx, meta
