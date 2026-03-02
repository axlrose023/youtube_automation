import logging

from playwright.async_api import Page

from .humanizer import Humanizer

logger = logging.getLogger(__name__)


class PlaybackController:
    def __init__(self, page: Page, humanizer: Humanizer) -> None:
        self._page = page
        self._h = humanizer

    async def ensure_playing(self, session_id: str) -> None:
        try:
            paused = await self._page.evaluate(
                "(() => { const v = document.querySelector('video'); return v ? v.paused : true })()"
            )
            if paused:
                logger.info("Session %s: video paused, starting playback", session_id)
                played = await self._page.evaluate(
                    "(() => { const v = document.querySelector('video');"
                    " if (!v) return false; v.play(); return true })()"
                )
                if not played:
                    await self._page.keyboard.press("k")
                await self._h.delay(0.5, 1.0)

                still_paused = await self._page.evaluate(
                    "(() => { const v = document.querySelector('video'); return v ? v.paused : true })()"
                )
                if still_paused:
                    logger.warning("Session %s: video still paused after play attempt", session_id)
                else:
                    logger.info("Session %s: video now playing", session_id)
            else:
                logger.info("Session %s: video already playing", session_id)
        except Exception as exc:
            logger.warning("Session %s: ensure_playing failed: %s", session_id, exc)

    async def set_speed(self, speed: float) -> None:
        try:
            await self._page.evaluate(
                f"document.querySelector('video').playbackRate = {speed}"
            )
        except Exception:
            pass

    async def seek_forward(self, seconds: int = 10) -> None:
        for _ in range(max(seconds // 5, 1)):
            await self._page.keyboard.press("ArrowRight")
            await self._h.delay(0.1, 0.3)

    async def get_duration(self) -> float | None:
        # Duration can briefly point to ad media. Prefer canonical YouTube metadata first,
        # then use <video>.duration only when ad overlay is not active.
        for _ in range(5):
            try:
                payload = await self._page.evaluate(
                    "(() => {"
                    "  const adShowing = !!document.querySelector('.ad-showing');"
                    "  const details = window.ytInitialPlayerResponse?.videoDetails;"
                    "  const responseDuration = details?.lengthSeconds ? Number(details.lengthSeconds) : null;"
                    "  const video = document.querySelector('video');"
                    "  const mediaDuration = video && Number.isFinite(video.duration) ? Number(video.duration) : null;"
                    "  return { adShowing, responseDuration, mediaDuration };"
                    "})()"
                )
            except Exception:
                payload = None

            if payload:
                response_duration = payload.get("responseDuration")
                if isinstance(response_duration, (int, float)) and response_duration > 0:
                    return float(response_duration)

                ad_showing = bool(payload.get("adShowing"))
                media_duration = payload.get("mediaDuration")
                if (not ad_showing) and isinstance(media_duration, (int, float)) and media_duration > 0:
                    return float(media_duration)

            await self._h.delay(0.4, 0.9)

        return None

    async def get_title(self) -> str | None:
        try:
            return await self._page.evaluate(
                "(() => {"
                "  const selectors = ["
                "    'h1.ytd-watch-metadata yt-formatted-string',"
                "    'h1.title yt-formatted-string',"
                "    'h1 yt-formatted-string'"
                "  ];"
                "  for (const selector of selectors) {"
                "    const node = document.querySelector(selector);"
                "    const text = node && node.textContent ? node.textContent.replace(/\\s+/g, ' ').trim() : '';"
                "    if (text) return text;"
                "  }"
                "  const fallback = (document.title || '').replace(/\\s*-\\s*YouTube\\s*$/, '').trim();"
                "  return fallback || null;"
                "})()"
            )
        except Exception:
            return None
