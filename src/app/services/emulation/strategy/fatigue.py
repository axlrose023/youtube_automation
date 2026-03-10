import logging
import random
import time

from ..browser.humanizer import Humanizer
from ..browser.navigator import Navigator
from ..core.config import (
    BREAK_CAP_RANGE,
    BREAK_FRACTION_RANGE,
    FATIGUE_CURVE_EXPONENT,
    FATIGUE_MODE_SWITCH_TO_A_PROBABILITY,
    FATIGUE_MODE_SWITCH_TO_A_THRESHOLD,
    FATIGUE_MODE_SWITCH_TO_B_PROBABILITY,
    FATIGUE_MODE_SWITCH_TO_B_THRESHOLD,
    FATIGUE_NOISE_STD,
)
from ..core.state import Mode, SessionState

logger = logging.getLogger(__name__)


class FatigueManager:
    def __init__(
        self,
        state: SessionState,
        humanizer: Humanizer,
        navigator: Navigator,
    ) -> None:
        self._state = state
        self._h = humanizer
        self._nav = navigator

    def update(self) -> None:
        total = self._state.duration_minutes * 60
        elapsed_monotonic = max(time.monotonic() - self._state.started_at_monotonic, 0.0)
        elapsed_wallclock = max(time.time() - self._state.started_at_wallclock, 0.0)
        elapsed = max(elapsed_monotonic, elapsed_wallclock)
        base = min(elapsed / total, 1.0)
        curve = base ** FATIGUE_CURVE_EXPONENT
        noise = random.gauss(0, FATIGUE_NOISE_STD)
        self._state.fatigue = max(0.0, min(curve + noise, 1.0))

    async def take_break(self) -> None:
        total = self._state.duration_minutes * 60
        min_break = min(total * BREAK_FRACTION_RANGE[0], BREAK_CAP_RANGE[0])
        max_break = min(total * BREAK_FRACTION_RANGE[1], BREAK_CAP_RANGE[1])
        break_s = random.uniform(min_break, max_break)
        logger.info("Session %s: break for %.0fs", self._state.session_id, break_s)

        if random.random() < 0.4:
            await self._nav.safe_go_home()

        elapsed = 0.0
        while elapsed < break_s:
            chunk = random.uniform(5, 15)
            await self._h.delay(chunk, chunk)
            elapsed += chunk
            if random.random() < 0.2:
                await self._h.wiggle_mouse()

        await self._h.scan_previews(random.uniform(2, 5))

    def maybe_switch_mode(self) -> None:
        if self._state.mode_locked:
            return

        if self._state.fatigue > FATIGUE_MODE_SWITCH_TO_A_THRESHOLD and self._state.mode == Mode.B:
            if random.random() < FATIGUE_MODE_SWITCH_TO_A_PROBABILITY:
                self._state.mode = Mode.A
                logger.info("Session %s: fatigue -> Mode A", self._state.session_id)
        elif self._state.fatigue < FATIGUE_MODE_SWITCH_TO_B_THRESHOLD and self._state.mode == Mode.A:
            if random.random() < FATIGUE_MODE_SWITCH_TO_B_PROBABILITY:
                self._state.mode = Mode.B
                logger.info("Session %s: refocused -> Mode B", self._state.session_id)
