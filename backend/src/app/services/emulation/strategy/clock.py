import logging
import random
import time

from ..core.config import (
    BREAK_MIN_REMAINING_S,
    BREAK_SKIP_PROBABILITY,
    LONG_CYCLE_RANGE,
    SHORT_CYCLE_PROBABILITY,
    SHORT_CYCLE_RANGE,
)
from ..core.session.state import SessionState

logger = logging.getLogger(__name__)


class SessionClock:
    def __init__(self, state: SessionState) -> None:
        self._state = state
        duration_s = state.duration_minutes * 60
        self._deadline_monotonic = state.started_at_monotonic + duration_s
        self._deadline_wallclock = state.started_at_wallclock + duration_s

    def deadline_reached(self) -> bool:
        return (
            time.monotonic() >= self._deadline_monotonic
            or time.time() >= self._deadline_wallclock
        )

    def remaining_seconds(self) -> float:
        mono_left = self._deadline_monotonic - time.monotonic()
        wall_left = self._deadline_wallclock - time.time()
        return max(min(mono_left, wall_left), 0.0)

    def start_cycle(self) -> None:
        self._state.cycle_start = time.monotonic()
        remaining = self.remaining_seconds()

        if random.random() < SHORT_CYCLE_PROBABILITY:
            target = random.uniform(*SHORT_CYCLE_RANGE) * 60
        else:
            target = random.uniform(*LONG_CYCLE_RANGE) * 60

        target *= self._state.personality.focus_span
        self._state.cycle_duration = min(target, remaining)
        self._state.surf_streak = 0
        logger.info(
            "Session %s: %.0fm cycle, mode %s (fatigue=%.2f, focus=%.2f)",
            self._state.session_id,
            self._state.cycle_duration / 60,
            self._state.mode.value,
            self._state.fatigue,
            self._state.personality.focus_span,
        )

    def cycle_active(self) -> bool:
        return (time.monotonic() - self._state.cycle_start) < self._state.cycle_duration

    def time_for_break(self) -> bool:
        if self.remaining_seconds() <= BREAK_MIN_REMAINING_S:
            return False
        if random.random() < BREAK_SKIP_PROBABILITY:
            logger.info("Session %s: skipping break", self._state.session_id)
            return False
        return True
