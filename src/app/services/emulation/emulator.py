import logging
import random

from playwright.async_api import Page

from .core.session_store import EmulationSessionStore
from .core.state import EmulationResult, SessionState
from .runtime import build_runtime

logger = logging.getLogger(__name__)

_WATCH_ACTIONS = {
    "watch_long",
    "watch_focused",
    "surf_video",
    "click_recommended",
}


class YouTubeEmulator:
    def __init__(
        self,
        page: Page,
        topics: list[str],
        duration_minutes: int,
        session_store: EmulationSessionStore,
        session_id: str,
    ) -> None:
        self.session_store = session_store

        session_state = SessionState(
            topics=topics,
            duration_minutes=duration_minutes,
            session_id=session_id,
        )
        self.session_state = session_state

        runtime = build_runtime(page, session_state)
        self.humanizer = runtime.humanizer
        self.navigator = runtime.navigator
        self.session_clock = runtime.clock
        self.action_picker = runtime.picker
        self.fatigue_manager = runtime.fatigue
        self.action_dispatcher = runtime.dispatcher
        self.traffic_tracker = runtime.traffic

    async def run(self) -> EmulationResult:
        self.log_session_start()
        await self.bootstrap_session()
        await self.run_cycles()
        result = await self.build_result()
        session_id = self.session_state.session_id
        logger.info(
            "Session %s: DONE — videos=%d, topics=%s, bytes=%d",
            session_id, result.videos_watched, result.topics_searched, result.bytes_downloaded,
        )
        return result

    def log_session_start(self) -> None:
        session_id = self.session_state.session_id
        personality = self.session_state.personality
        logger.info(
            "Session %s: START — topics=%s, duration=%dm, mode=%s, "
            "personality(pace=%.2f, patience=%.2f, focus=%.2f, search_style=%.2f, ad_tol=%.2f)",
            session_id,
            self.session_state.topics,
            self.session_state.duration_minutes,
            self.session_state.mode.value,
            personality.pace,
            personality.patience,
            personality.focus_span,
            personality.search_style,
            personality.ad_tolerance,
        )

    async def bootstrap_session(self) -> None:
        session_id = self.session_state.session_id
        await self.navigator.open_youtube()
        if await self.navigator.has_feed_content():
            logger.info("Session %s: feed has content, scanning previews", session_id)
            await self.humanizer.scan_previews(random.uniform(3, 8))
        else:
            logger.info("Session %s: empty feed, skipping to search", session_id)

    async def run_cycles(self) -> None:
        session_id = self.session_state.session_id
        cycle_number = 0
        while not self.session_clock.deadline_reached():
            remaining = self.session_state.remaining_seconds()
            if remaining <= 1.0:
                logger.info(
                    "Session %s: stopping run loop — remaining %.1fs",
                    session_id,
                    remaining,
                )
                break

            cycle_number += 1
            self.session_clock.start_cycle()
            logger.info("Session %s: === CYCLE %d ===", session_id, cycle_number)
            await self.run_cycle()

            remaining = self.session_state.remaining_seconds()
            if self.session_clock.deadline_reached() or remaining <= 1.0:
                break

            if self.session_clock.time_for_break():
                logger.info("Session %s: taking break after cycle %d", session_id, cycle_number)
                await self.fatigue_manager.take_break()
                self.fatigue_manager.maybe_switch_mode()

    async def run_cycle(self) -> None:
        session_id = self.session_state.session_id
        action_number = 0
        while not self.session_clock.deadline_reached() and self.session_clock.cycle_active():
            remaining = self.session_state.remaining_seconds()
            if remaining <= 1.0:
                logger.info("Session %s: stopping cycle — remaining %.1fs", session_id, remaining)
                return

            action_number += 1
            action = self.action_picker.pick()
            min_required = 20.0 if action in _WATCH_ACTIONS else 5.0
            if remaining < min_required:
                logger.info(
                    "Session %s: skipping %s — remaining %.1fs < %.1fs threshold",
                    session_id,
                    action,
                    remaining,
                    min_required,
                )
                await self.humanizer.delay(remaining, remaining)
                return

            logger.info(
                "Session %s [%s] action #%d: %s (topic=%s, remaining_topics=%d, fatigue=%.2f, videos=%d, remaining=%.0fs)",
                session_id,
                self.session_state.mode.value,
                action_number,
                action,
                self.session_state.current_topic or "<none>",
                len(self.session_state.unsearched_topics()),
                self.session_state.fatigue,
                self.session_state.videos_watched,
                remaining,
            )

            await self.action_dispatcher.execute(action)
            self.fatigue_manager.update()
            if action_number % 5 == 0:
                self.fatigue_manager.maybe_switch_mode()
            await self.session_store.sync_progress(
                self.session_state.session_id,
                self.session_state,
                self.traffic_tracker.bytes_downloaded,
            )
            await self.humanizer.delay(0.5, 2.0)

    async def build_result(self) -> EmulationResult:
        final_bytes = await self.traffic_tracker.finalize()
        return EmulationResult(
            topics_searched=self.session_state.searched_topics,
            videos_watched=self.session_state.videos_watched,
            bytes_downloaded=final_bytes,
        )
