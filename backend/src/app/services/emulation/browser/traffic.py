import logging

from playwright.async_api import Page, Response

logger = logging.getLogger(__name__)


class TrafficTracker:
    def __init__(self, page: Page) -> None:
        self._page = page
        self._bytes: int = 0
        self._page.on("response", self._on_response)

    @property
    def bytes_downloaded(self) -> int:
        return self._bytes

    async def _on_response(self, response: Response) -> None:
        try:
            content_length = response.headers.get("content-length")
            if content_length:
                self._bytes += int(content_length)
        except Exception:
            pass

    async def finalize(self) -> int:
        try:
            cdp = await self._page.context.new_cdp_session(self._page)
            await cdp.send("Performance.enable")
            metrics = await cdp.send("Performance.getMetrics")
            for m in metrics.get("metrics", []):
                if m["name"] == "ReceivedBytes":
                    precise = int(m["value"])
                    if precise > self._bytes:
                        self._bytes = precise
                    break
            await cdp.detach()
        except Exception:
            logger.debug("CDP metrics unavailable, using header-based count")
        return self._bytes
