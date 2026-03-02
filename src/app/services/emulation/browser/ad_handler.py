import logging
import random

from playwright.async_api import Page

from ..core.state import SessionState
from .humanizer import Humanizer

logger = logging.getLogger(__name__)

_AD_OVERLAY_SELECTOR = (
    ".ytp-ad-player-overlay, "
    ".ytp-ad-text, "
    ".ad-showing, "
    ".ytp-ad-skip-button-container"
)
_AD_SKIP_SELECTOR = (
    "button.ytp-skip-ad-button, "
    "button.ytp-ad-skip-button, "
    "button.ytp-ad-skip-button-modern, "
    ".ytp-ad-skip-button-container button"
)


class AdHandler:
    def __init__(self, page: Page, humanizer: Humanizer, state: SessionState) -> None:
        self._page = page
        self._h = humanizer
        self._state = state

    async def check(self) -> bool:
        try:
            overlay = await self._page.query_selector(_AD_OVERLAY_SELECTOR)
            return overlay is not None and await overlay.is_visible()
        except Exception:
            return False

    async def try_skip(self) -> bool:
        try:
            skip_button = await self._page.query_selector(_AD_SKIP_SELECTOR)
            if skip_button and await skip_button.is_visible():
                await self._h.delay(0.5, 2.5)
                await self._h.click(skip_button)
                return True
        except Exception:
            pass
        return False

    async def handle(self, *, patient: bool) -> None:
        await self._h.delay(1.0, 3.0)
        if not await self.check():
            return

        ad_tol = self._state.personality.ad_tolerance
        logger.info("Session %s: ad detected (patient=%s, ad_tol=%.2f)", self._state.session_id, patient, ad_tol)
        if patient:
            wait = random.uniform(3, 12) * ad_tol
            await self._h.delay(wait, wait)
        else:
            await self._h.delay(0.5 * ad_tol, 2.0 * ad_tol)

        if not await self.try_skip():
            logger.info("Session %s: ad skip not available, waiting", self._state.session_id)
            await self._h.delay(5 * ad_tol, 30 * ad_tol)
            await self.try_skip()
