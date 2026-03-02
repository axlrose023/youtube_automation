import logging
import random
import time

from ..browser.humanizer import Humanizer
from ..browser.navigator import Navigator
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
        elapsed = max(time.monotonic() - self._state.started_at_monotonic, 0.0)
        base = min(elapsed / total, 1.0)
        curve = base ** 0.8
        noise = random.gauss(0, 0.02)
        self._state.fatigue = max(0.0, min(curve + noise, 1.0))

    async def take_break(self) -> None:
        total = self._state.duration_minutes * 60
        min_break = min(total * 0.05, 3 * 60)
        max_break = min(total * 0.10, 7 * 60)
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
        if self._state.fatigue > 0.6 and self._state.mode == Mode.B:
            if random.random() < 0.5:
                self._state.mode = Mode.A
                logger.info("Session %s: fatigue -> Mode A", self._state.session_id)
        elif self._state.fatigue < 0.4 and self._state.mode == Mode.A:
            if random.random() < 0.3:
                self._state.mode = Mode.B
                logger.info("Session %s: refocused -> Mode B", self._state.session_id)
