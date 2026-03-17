from __future__ import annotations

import logging
import random

from ..core.config import (
    COVERAGE_CAP_BUDGET_FRACTION,
    COVERAGE_CAP_MIN_S,
    COVERAGE_SEARCH_OVERHEAD_S,
    FULL_WATCH_CHANCE_LONG_A,
    FULL_WATCH_CHANCE_LONG_B,
    FULL_WATCH_CHANCE_MID_A,
    FULL_WATCH_CHANCE_MID_B,
    FULL_WATCH_CHANCE_SHORT_A,
    FULL_WATCH_CHANCE_SHORT_B,
    REALISM_MIN_WATCH_AFTER_COVERAGE_S,
    REALISM_MIN_WATCH_S,
    REALISM_MULTI_TOPIC_BUDGET_FRACTION,
    REALISM_MULTI_TOPIC_MAX_WATCH_S,
    REALISM_MULTI_TOPIC_MIN_WATCH_S,
    REALISM_MIN_WATCH_TRIGGER_REMAINING_S,
)
from ..core.session.state import SessionState
from .playback import PlaybackController

logger = logging.getLogger(__name__)


class WatchDurationCalculator:
    def __init__(self, state: SessionState, playback: PlaybackController) -> None:
        self._state = state
        self._playback = playback

    async def decide(
        self,
        *,
        mode_a: bool,
        fallback_min: float,
        fallback_max: float,
    ) -> float:
        video_dur = await self._playback.get_duration()
        logger.info(
            "Session %s: video duration = %s",
            self._state.session_id,
            f"{video_dur:.0f}s" if video_dur else "unknown",
        )
        patience = self._state.personality.patience

        if video_dur and video_dur > 5:
            if self._should_watch_full(video_dur, mode_a=mode_a):
                watch = video_dur * random.uniform(0.85, 1.0) * patience
                logger.info(
                    "Session %s: decided full watch %.0fs / %.0fs (patience=%.2f)",
                    self._state.session_id, watch, video_dur, patience,
                )
                return watch

            if video_dur < 180:
                fraction = random.uniform(0.4, 0.8)
            elif video_dur < 600:
                fraction = random.uniform(0.2, 0.6)
            else:
                fraction = random.uniform(0.1, 0.4)

            watch = max(video_dur * fraction, fallback_min) * patience
            logger.info(
                "Session %s: decided partial watch %.0fs / %.0fs (%.0f%%, patience=%.2f)",
                self._state.session_id, watch, video_dur, fraction * 100, patience,
            )
            return watch

        watch = random.uniform(fallback_min, fallback_max) * patience
        logger.info(
            "Session %s: decided fallback watch %.0fs (patience=%.2f)",
            self._state.session_id, watch, patience,
        )
        return watch

    def apply_fatigue_reduction(
        self,
        watch_s: float,
        fatigue_threshold: float,
        fatigue_reduction: float,
    ) -> float:
        if fatigue_threshold <= 0 or fatigue_reduction <= 0:
            return watch_s
        if self._state.fatigue <= fatigue_threshold:
            return watch_s

        before = watch_s
        watch_s *= random.uniform(fatigue_reduction, fatigue_reduction + 0.1)
        watch_s = min(watch_s, before)
        logger.info(
            "Session %s: fatigue %.2f, reduced %.0fs -> %.0fs",
            self._state.session_id, self._state.fatigue, before, watch_s,
        )
        return watch_s

    def cap_before_topic_coverage(
        self,
        seconds: float,
        cap_range: tuple[float, float],
        action: str,
    ) -> float:
        unsearched = self._state.unsearched_topics()
        if not unsearched:
            return seconds

        default_cap = random.uniform(*cap_range)
        pending = len(unsearched)
        remaining_seconds = self._state.remaining_seconds()
        budget = max(remaining_seconds - pending * COVERAGE_SEARCH_OVERHEAD_S, 0.0)
        dynamic_cap = max(COVERAGE_CAP_MIN_S, (budget / max(pending + 1, 1)) * COVERAGE_CAP_BUDGET_FRACTION)
        coverage_cap = min(default_cap, dynamic_cap)
        limited = min(seconds, coverage_cap)

        if limited < seconds:
            logger.info(
                "Session %s: %s capped %.0fs -> %.0fs until all topics covered "
                "(remaining_topics=%d, remaining=%.0fs)",
                self._state.session_id, action, seconds, limited, pending, remaining_seconds,
            )
        return limited

    def apply_realism_floor(
        self,
        seconds: float,
        action: str,
        *,
        mark_completed: bool,
        after_coverage: bool,
    ) -> float:
        if not mark_completed:
            return seconds
        if self._state.remaining_seconds() < REALISM_MIN_WATCH_TRIGGER_REMAINING_S:
            return seconds

        floor = REALISM_MIN_WATCH_AFTER_COVERAGE_S if after_coverage else REALISM_MIN_WATCH_S
        pending_topics = len(self._state.unsearched_topics())
        if not after_coverage and pending_topics > 0:
            per_topic_budget = self._state.remaining_seconds() / max(pending_topics + 1, 1)
            budget_floor = per_topic_budget * REALISM_MULTI_TOPIC_BUDGET_FRACTION
            floor = min(
                floor,
                max(
                    REALISM_MULTI_TOPIC_MIN_WATCH_S,
                    min(REALISM_MULTI_TOPIC_MAX_WATCH_S, budget_floor),
                ),
            )
        if seconds >= floor:
            return seconds

        logger.info(
            "Session %s: %s realism floor %.0fs -> %.0fs (after_coverage=%s)",
            self._state.session_id, action, seconds, floor, after_coverage,
        )
        return floor

    def cap_to_remaining(self, seconds: float) -> float:
        return min(seconds, self._state.remaining_seconds())

    def _should_watch_full(self, video_dur: float, *, mode_a: bool) -> bool:
        if video_dur > 1200 and self._state.fatigue > 0.5:
            return False

        if mode_a:
            chance = (
                FULL_WATCH_CHANCE_SHORT_A if video_dur < 120
                else FULL_WATCH_CHANCE_MID_A if video_dur < 600
                else FULL_WATCH_CHANCE_LONG_A
            )
        else:
            chance = (
                FULL_WATCH_CHANCE_SHORT_B if video_dur < 120
                else FULL_WATCH_CHANCE_MID_B if video_dur < 600
                else FULL_WATCH_CHANCE_LONG_B
            )

        chance *= max(1.0 - self._state.fatigue * 0.5, 0.3)
        return random.random() < chance
