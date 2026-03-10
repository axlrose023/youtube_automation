from __future__ import annotations

import logging
import random
import time

from playwright.async_api import Page

from .browser.ad_capture import AdCaptureProvider
from .core.session_store import EmulationSessionStore
from .core.state import EmulationResult, SessionState
from .runtime import build_runtime
from .session_loop import SessionLoop

logger = logging.getLogger(__name__)


class YouTubeEmulator:
    def __init__(
        self,
        page: Page,
        topics: list[str],
        duration_minutes: int,
        session_store: EmulationSessionStore,
        session_id: str,
        capture: AdCaptureProvider | None = None,
        bootstrap: dict[str, object] | None = None,
    ) -> None:
        self.session_store = session_store

        session_state = SessionState(
            topics=topics,
            duration_minutes=duration_minutes,
            session_id=session_id,
            bootstrap=bootstrap,
        )
        self.session_state = session_state

        runtime = build_runtime(page, session_state, capture)
        self.ads = runtime.ads
        self.humanizer = runtime.humanizer
        self.navigator = runtime.navigator
        self.traffic_tracker = runtime.traffic

        self._loop = SessionLoop(
            state=session_state,
            clock=runtime.clock,
            picker=runtime.picker,
            dispatcher=runtime.dispatcher,
            fatigue=runtime.fatigue,
            humanizer=runtime.humanizer,
            traffic=runtime.traffic,
            session_store=session_store,
        )

    async def run(self) -> EmulationResult:
        self._log_session_start()
        await self._bootstrap()
        await self._loop.run()
        await self.ads.flush_pending_captures()
        result = await self._build_result()
        logger.info(
            "Session %s: DONE — videos=%d, ads=%d, topics=%s, bytes=%d, duration=%ds, tracked_videos=%d",
            self.session_state.session_id,
            result.videos_watched,
            len(result.watched_ads),
            result.topics_searched,
            result.bytes_downloaded,
            result.total_duration_seconds,
            len(result.watched_videos),
        )
        return result

    def _log_session_start(self) -> None:
        session_id = self.session_state.session_id
        personality = self.session_state.personality
        logger.info(
            "Session %s: START — topics=%s, duration=%dm, mode=%s, mode_locked=%s, "
            "personality(pace=%.2f, patience=%.2f, focus=%.2f, search_style=%.2f, ad_tol=%.2f)",
            session_id,
            self.session_state.topics,
            self.session_state.duration_minutes,
            self.session_state.mode.value,
            self.session_state.mode_locked,
            personality.pace,
            personality.patience,
            personality.focus_span,
            personality.search_style,
            personality.ad_tolerance,
        )

    async def _bootstrap(self) -> None:
        session_id = self.session_state.session_id
        await self.navigator.open_youtube()
        if await self.navigator.has_feed_content():
            logger.info("Session %s: feed has content, scanning previews", session_id)
            await self.humanizer.scan_previews(random.uniform(3, 8))
        else:
            logger.info("Session %s: empty feed, skipping to search", session_id)

    async def _build_result(self) -> EmulationResult:
        final_bytes = await self.traffic_tracker.finalize()
        elapsed_monotonic = max(
            time.monotonic() - self.session_state.started_at_monotonic,
            0.0,
        )
        elapsed_wallclock = max(
            time.time() - self.session_state.started_at_wallclock,
            0.0,
        )
        total_duration_seconds = int(max(elapsed_monotonic, elapsed_wallclock))
        return EmulationResult(
            topics_searched=self.session_state.searched_topics,
            videos_watched=self.session_state.videos_watched,
            bytes_downloaded=final_bytes,
            total_duration_seconds=total_duration_seconds,
            watched_videos=self.session_state.watched_videos,
            watched_ads=self.session_state.watched_ads,
        )
