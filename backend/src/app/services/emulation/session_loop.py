import asyncio
import logging
from collections.abc import Awaitable, Callable

from .browser.humanizer import Humanizer
from .browser.traffic import TrafficTracker
from .actions import WATCH_ACTIONS
from .config import LIVE_PROGRESS_SYNC_INTERVAL_S, WATCH_ACTION_MIN_REMAINING_S
from .decision import ActionPicker
from .session.clock import SessionClock
from .session.fatigue import FatigueManager
from .session.state import SessionState
from app.api.modules.emulation.models import SessionStatus
from .session.store import EmulationSessionStore
from .workflow.dispatcher import ActionDispatcher

logger = logging.getLogger(__name__)


class SessionLoop:


    def __init__(
        self,
        state: SessionState,
        clock: SessionClock,
        picker: ActionPicker,
        dispatcher: ActionDispatcher,
        fatigue: FatigueManager,
        humanizer: Humanizer,
        traffic: TrafficTracker,
        session_store: EmulationSessionStore,
        flush_pending_captures: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._state = state
        self._clock = clock
        self._picker = picker
        self._dispatcher = dispatcher
        self._fatigue = fatigue
        self._humanizer = humanizer
        self._traffic = traffic
        self._store = session_store
        self._flush_pending_captures = flush_pending_captures
        self._stop_requested = False

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    async def run(self) -> None:
        session_id = self._state.session_id
        cycle_number = 0

        while not self._clock.deadline_reached() and not self._stop_requested:
            remaining = self._state.remaining_seconds()
            if remaining <= 1.0:
                logger.info(
                    "Session %s: stopping run loop — remaining %.1fs",
                    session_id,
                    remaining,
                )
                break

            cycle_number += 1
            self._clock.start_cycle()
            logger.info("Session %s: === CYCLE %d ===", session_id, cycle_number)
            await self._run_cycle()

            remaining = self._state.remaining_seconds()
            if self._clock.deadline_reached() or remaining <= 1.0:
                break

            await self._sync_progress_once()
            if self._stop_requested:
                break

            if self._clock.time_for_break():
                logger.info("Session %s: taking break after cycle %d", session_id, cycle_number)
                await self._fatigue.take_break(stop_check=self._check_stop_requested)
                if self._stop_requested:
                    break
                self._fatigue.maybe_switch_mode()

    async def _run_cycle(self) -> None:
        session_id = self._state.session_id
        action_number = 0

        while not self._clock.deadline_reached() and self._clock.cycle_active() and not self._stop_requested:
            remaining = self._state.remaining_seconds()
            if remaining <= 1.0:
                logger.info("Session %s: stopping cycle — remaining %.1fs", session_id, remaining)
                return

            action_number += 1
            action = self._picker.pick()
            min_required = WATCH_ACTION_MIN_REMAINING_S if action in WATCH_ACTIONS else 5.0

            if remaining < min_required:
                logger.info(
                    "Session %s: skipping %s — remaining %.1fs < %.1fs threshold",
                    session_id,
                    action,
                    remaining,
                    min_required,
                )
                await self._humanizer.delay(remaining, remaining)
                return

            logger.info(
                "Session %s [%s] action #%d: %s (topic=%s, remaining_topics=%d, fatigue=%.2f, videos=%d, remaining=%.0fs)",
                session_id,
                self._state.mode.value,
                action_number,
                action,
                self._state.current_topic or "<none>",
                len(self._state.unsearched_topics()),
                self._state.fatigue,
                self._state.videos_watched,
                remaining,
            )

            progress_task: asyncio.Task[None] | None = None
            try:
                if action in WATCH_ACTIONS:
                    progress_task = asyncio.create_task(self._sync_progress_during_watch())

                await self._dispatcher.execute(action)
                self._fatigue.update()
            finally:
                if progress_task is not None:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass

            if action_number % 5 == 0:
                self._fatigue.maybe_switch_mode()

            await self._sync_progress_once()
            if self._stop_requested:
                return
            await self._humanizer.delay(0.5, 2.0)

    async def _sync_progress_during_watch(self) -> None:
        if LIVE_PROGRESS_SYNC_INTERVAL_S <= 0:
            return

        while True:
            await asyncio.sleep(LIVE_PROGRESS_SYNC_INTERVAL_S)
            await self._sync_progress_once()

    async def _sync_progress_once(self) -> None:
        session_id = self._state.session_id
        data = await self._store.get(session_id)
        if data and (data.get("stop_requested") or data.get("status") == SessionStatus.STOPPED):
            logger.info("Session %s: stop requested by user", session_id)
            self._stop_requested = True
            self._state.request_stop()
            return
        await self._flush_pending_capture_updates()
        await self._store.sync_progress(
            session_id,
            self._state,
            self._traffic.bytes_downloaded,
        )

    async def _flush_pending_capture_updates(self) -> None:
        if self._flush_pending_captures is None:
            return
        try:
            await self._flush_pending_captures()
        except Exception as exc:
            logger.warning(
                "Session %s: pending capture reconciliation failed: %s",
                self._state.session_id,
                exc,
            )

    async def _check_stop_requested(self) -> bool:
        if self._stop_requested or self._state.stop_requested:
            return True
        await self._sync_progress_once()
        return self._stop_requested or self._state.stop_requested
