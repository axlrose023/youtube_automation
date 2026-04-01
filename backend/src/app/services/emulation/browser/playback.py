import logging
from dataclasses import dataclass

from playwright.async_api import Page

from ..session.state import SessionState
from .humanizer import Humanizer
from .youtube_surface import canonicalize_youtube_watch_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaybackSnapshot:
    has_video: bool
    paused: bool
    current_time: float | None
    ready_state: int | None
    ad_showing: bool
    error_visible: bool
    error_text: str | None


class PlaybackController:
    def __init__(self, page: Page, humanizer: Humanizer, state: SessionState) -> None:
        self._page = page
        self._h = humanizer
        self._state = state

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
                    if self._state.is_mobile_surface():
                        await self._page.evaluate(
                            "(() => {"
                            " const v = document.querySelector('video');"
                            " if (!v) return false;"
                            " v.click?.();"
                            " return true;"
                            "})()"
                        )
                    else:
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
        if self._state.is_mobile_surface():
            try:
                await self._page.evaluate(
                    "(seconds) => { const v = document.querySelector('video'); if (v) v.currentTime += seconds; }",
                    seconds,
                )
                return
            except Exception:
                return
        for _ in range(max(seconds // 5, 1)):
            await self._page.keyboard.press("ArrowRight")
            await self._h.delay(0.1, 0.3)

    async def get_duration(self) -> float | None:


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
                "  const clean = (value) => {"
                "    const text = (value || '').replace(/\\s+/g, ' ').trim();"
                "    if (!text) return '';"
                "    return text"
                "      .replace(/^\\d{1,2}:\\d{2}(?::\\d{2})?\\s*/, '')"
                "      .replace(/\\s*-\\s*YouTube\\s*$/i, '')"
                "      .trim();"
                "  };"
                "  const metaSelectors = ["
                "    'meta[property=\"og:title\"]',"
                "    'meta[name=\"title\"]',"
                "    'meta[itemprop=\"name\"]'"
                "  ];"
                "  for (const selector of metaSelectors) {"
                "    const node = document.querySelector(selector);"
                "    const content = clean(node && node.getAttribute ? node.getAttribute('content') : '');"
                "    if (content) return content;"
                "  }"
                "  const selectors = ["
                "    'ytm-slim-video-information-renderer .slim-video-information-title',"
                "    'h1.ytd-watch-metadata yt-formatted-string',"
                "    'h1.title yt-formatted-string',"
                "    'h1 yt-formatted-string',"
                "    'h1.slim-video-information-title',"
                "    'ytm-slim-video-metadata-section h1',"
                "    '.slim-video-metadata-title'"
                "  ];"
                "  for (const selector of selectors) {"
                "    const node = document.querySelector(selector);"
                "    const text = clean(node && node.textContent ? node.textContent : '');"
                "    if (text) return text;"
                "  }"
                "  const fallback = clean(document.title || '');"
                "  return fallback || null;"
                "})()"
            )
        except Exception:
            return None

    async def get_snapshot(self) -> PlaybackSnapshot:
        try:
            payload = await self._page.evaluate(
                """() => {
                    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const video = document.querySelector('video');
                    const errorSelectors = [
                        'ytm-player-error-message-renderer',
                        '.player-error-message',
                        '.ytp-error',
                        '.yt-player-error-message-renderer',
                        '[class*="player-error"]',
                    ];
                    let errorText = '';
                    for (const selector of errorSelectors) {
                        const node = document.querySelector(selector);
                        const text = clean(node && node.textContent ? node.textContent : '');
                        if (text) {
                            errorText = text;
                            break;
                        }
                    }
                    if (!errorText) {
                        const bodyText = clean(document.body && document.body.innerText ? document.body.innerText : '');
                        if (/something went wrong|refresh or try again later/i.test(bodyText)) {
                            errorText = 'Something went wrong';
                        }
                    }
                    return {
                        hasVideo: !!video,
                        paused: video ? !!video.paused : true,
                        currentTime: video && Number.isFinite(video.currentTime) ? Number(video.currentTime) : null,
                        readyState: video ? Number(video.readyState) : null,
                        adShowing: !!document.querySelector('.ad-showing'),
                        errorVisible: !!errorText,
                        errorText: errorText || null,
                    };
                }"""
            )
        except Exception:
            payload = None

        if not isinstance(payload, dict):
            return PlaybackSnapshot(
                has_video=False,
                paused=True,
                current_time=None,
                ready_state=None,
                ad_showing=False,
                error_visible=False,
                error_text=None,
            )

        return PlaybackSnapshot(
            has_video=bool(payload.get("hasVideo")),
            paused=bool(payload.get("paused", True)),
            current_time=float(payload["currentTime"]) if isinstance(payload.get("currentTime"), (int, float)) else None,
            ready_state=int(payload["readyState"]) if isinstance(payload.get("readyState"), (int, float)) else None,
            ad_showing=bool(payload.get("adShowing")),
            error_visible=bool(payload.get("errorVisible")),
            error_text=payload.get("errorText") if isinstance(payload.get("errorText"), str) else None,
        )

    async def recover_player_error(self, session_id: str, *, current_url: str | None = None) -> bool:
        url = (current_url or self._page.url or "").strip()
        if not url:
            return False
        canonical_url = canonicalize_youtube_watch_url(
            url,
            current_url=self._page.url,
            preferred_mode=self._state.surface_mode.value,
        ) or url

        async def _try_recovery_step(step_name: str, action) -> bool:
            logger.warning("Session %s: recovering player error via %s (%s)", session_id, step_name, url)
            try:
                await action()
            except Exception as exc:
                logger.warning("Session %s: player error %s step failed: %s", session_id, step_name, exc)
                return False
            await self._h.delay(1.0, 1.8)
            await self.ensure_playing(session_id)
            snapshot = await self.get_snapshot()
            recovered = snapshot.has_video and not snapshot.error_visible
            if recovered:
                logger.info("Session %s: player error recovery succeeded via %s", session_id, step_name)
            else:
                logger.warning(
                    "Session %s: player error recovery incomplete via %s (has_video=%s paused=%s ready_state=%s error=%s)",
                    session_id,
                    step_name,
                    snapshot.has_video,
                    snapshot.paused,
                    snapshot.ready_state,
                    snapshot.error_text or "<none>",
                )
            return recovered

        reloaded = await _try_recovery_step(
            "page.reload",
            lambda: self._page.reload(wait_until="domcontentloaded", timeout=30_000),
        )
        if reloaded:
            return True

        return await _try_recovery_step(
            "page.goto",
            lambda: self._page.goto(canonical_url, wait_until="domcontentloaded", timeout=30_000),
        )
