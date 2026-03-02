import logging
import random
import time

from ..core.state import SessionState

logger = logging.getLogger(__name__)


class SessionClock:
    def __init__(self, state: SessionState) -> None:
        self._state = state
        self._deadline = time.monotonic() + state.duration_minutes * 60

    def deadline_reached(self) -> bool:
        return time.monotonic() >= self._deadline

    def remaining_seconds(self) -> float:
        return max(self._deadline - time.monotonic(), 0.0)

    def start_cycle(self) -> None:
        self._state.cycle_start = time.monotonic()
        remaining = max(self._deadline - time.monotonic(), 0)

        if random.random() < 0.3:
            target = random.uniform(5, 15) * 60
        else:
            target = random.uniform(20, 50) * 60

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
        if (self._deadline - time.monotonic()) <= 120:
            return False
        if random.random() < 0.2:
            logger.info("Session %s: skipping break", self._state.session_id)
            return False
        return True
