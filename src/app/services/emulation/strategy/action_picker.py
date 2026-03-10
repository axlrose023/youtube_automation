import logging
import random

from ..core.actions import Action
from ..core.config import (
    ENTERTAINMENT_FATIGUE_HIGH,
    ENTERTAINMENT_FATIGUE_MEDIUM,
    MAX_RECOMMENDED_STREAK,
    MAX_SURF_STREAK,
    SEARCH_PRESSURE_CLAMP,
    SEARCH_PRESSURE_DEFAULT,
    SEARCH_PRESSURE_STYLE_WEIGHT,
    SEARCH_PRESSURE_THRESHOLDS,
    TASK_FATIGUE_HIGH,
    TASK_FATIGUE_MEDIUM,
    VIDEO_PACE_GRACE_COMPLETED,
    VIDEO_PACE_TARGET_MIN_PER_COMPLETED,
)
from ..core.state import Mode, SessionState

logger = logging.getLogger(__name__)


class ActionPicker:
    def __init__(self, state: SessionState) -> None:
        self._state = state
        self._just_searched: bool = False
        self._actions_since_search = 0
        self._reanchor_limit = random.randint(4, 8)
        self._offtopic_reanchor_limit = random.randint(2, 3)

    def pick(self) -> Action:
        unsearched = self._state.unsearched_topics()

        if self._state.no_video_streak >= 2:
            self._state.no_video_streak = 0
            if unsearched:
                logger.info("Session %s: no-video recovery -> search", self._state.session_id)
                return self._finalize(Action.SEARCH)
            logger.info("Session %s: no-video recovery -> go_home", self._state.session_id)
            return self._finalize(Action.GO_HOME)

        if self._state.recommended_streak >= MAX_RECOMMENDED_STREAK:
            logger.info(
                "Session %s: recommended streak limit -> search to stay on topic",
                self._state.session_id,
            )
            return self._finalize(Action.SEARCH)

        if self._state.topic_drifted:
            logger.info(
                "Session %s: topic drift detected -> re-anchor search",
                self._state.session_id,
            )
            return self._finalize(Action.SEARCH)

        if self._state.offtopic_or_reco_streak >= self._offtopic_reanchor_limit:
            logger.info(
                "Session %s: off-topic/recommendation streak %d >= %d -> search",
                self._state.session_id,
                self._state.offtopic_or_reco_streak,
                self._offtopic_reanchor_limit,
            )
            return self._finalize(Action.SEARCH)

        if self._state.topics and self._actions_since_search >= self._reanchor_limit:
            logger.info(
                "Session %s: periodic re-anchor after %d actions -> search",
                self._state.session_id,
                self._actions_since_search,
            )
            return self._finalize(Action.SEARCH)


        if self._just_searched:
            self._just_searched = False
            if self._state.mode == Mode.B:
                action = Action.WATCH_FOCUSED
            else:
                action = Action.WATCH_LONG
            logger.info("Session %s: post-search -> %s", self._state.session_id, action)
            return self._finalize(action)

        if not self._state.searched_topics and unsearched:
            logger.info(
                "Session %s: first topic search (topics=%d)",
                self._state.session_id,
                len(self._state.topics),
            )
            return self._finalize(Action.SEARCH)

        if unsearched and self._should_prioritize_search(unsearched):
            logger.info(
                "Session %s: dynamic coverage push (%d topics left) -> search",
                self._state.session_id,
                len(unsearched),
            )
            return self._finalize(Action.SEARCH)

        if self._state.all_topics_covered() and self._is_completed_video_pace_high():
            action = self._pick_video_pace_guard_action()
            logger.info(
                "Session %s: pace guard (completed=%d) -> %s",
                self._state.session_id,
                self._state.videos_watched,
                action,
            )
            return self._finalize(action)

        if self._state.mode == Mode.B:
            return self._finalize(self._pick_task_mode(unsearched))
        return self._finalize(self._pick_entertainment_mode(unsearched))

    def _pick_entertainment_mode(self, unsearched: list[str]) -> Action:
        weights: dict[Action, int] = {
            Action.WATCH_LONG: 54,
            Action.CLICK_RECOMMENDED: 8,
            Action.SCROLL_FEED: 10,
            Action.SEARCH: 5 if unsearched else 0,
            Action.IDLE: 14,
            Action.GO_HOME: 6,
        }

        if self._state.fatigue > ENTERTAINMENT_FATIGUE_MEDIUM:
            weights[Action.IDLE] += 8
            weights[Action.WATCH_LONG] += 10
            weights[Action.CLICK_RECOMMENDED] = max(weights[Action.CLICK_RECOMMENDED] - 3, 0)
            weights[Action.SEARCH] = 0

        if self._state.fatigue > ENTERTAINMENT_FATIGUE_HIGH:
            weights[Action.WATCH_LONG] += 25
            weights[Action.IDLE] += 22
            weights[Action.SCROLL_FEED] += 10
            weights[Action.SEARCH] = 0
            weights[Action.CLICK_RECOMMENDED] = max(weights[Action.CLICK_RECOMMENDED] - 5, 0)

        return self._weighted_choice(weights)

    def _pick_task_mode(self, unsearched: list[str]) -> Action:
        if self._state.surf_streak >= MAX_SURF_STREAK:
            self._state.surf_streak = 0
            if unsearched:
                return Action.REFINE_SEARCH
            return Action.CLICK_RECOMMENDED

        weights: dict[Action, int] = {
            Action.WATCH_FOCUSED: 50,
            Action.SEARCH: 12 if unsearched else 1,
            Action.SURF_VIDEO: 6,
            Action.CLICK_RECOMMENDED: 4,
            Action.SCROLL_RESULTS: 6,
            Action.IDLE: 8,
            Action.GO_BACK: 2,
        }

        if self._state.fatigue > TASK_FATIGUE_MEDIUM:
            weights[Action.WATCH_FOCUSED] += 6
            weights[Action.IDLE] += 3
            weights[Action.SURF_VIDEO] = max(weights[Action.SURF_VIDEO] - 3, 0)

        if self._state.fatigue > TASK_FATIGUE_HIGH:
            weights[Action.WATCH_FOCUSED] += 18
            weights[Action.IDLE] += 12
            weights[Action.SCROLL_RESULTS] += 6
            weights[Action.SEARCH] = 0
            weights[Action.SURF_VIDEO] = max(weights[Action.SURF_VIDEO] - 4, 0)
            weights[Action.CLICK_RECOMMENDED] = max(weights[Action.CLICK_RECOMMENDED] - 3, 0)

        return self._weighted_choice(weights)

    @staticmethod
    def _weighted_choice(weights: dict[Action, int]) -> Action:
        actions = list(weights.keys())
        action_weights = [weights[a] for a in actions]
        if sum(action_weights) == 0:
            return Action.SCROLL_FEED
        return random.choices(actions, weights=action_weights, k=1)[0]

    def _finalize(self, action: Action) -> Action:
        if action in (Action.SEARCH, Action.REFINE_SEARCH):
            self._just_searched = True
            self._actions_since_search = 0
            self._reanchor_limit = random.randint(4, 8)
            self._offtopic_reanchor_limit = random.randint(2, 3)
            self._state.topic_drifted = False
            self._state.offtopic_or_reco_streak = 0
        else:
            self._actions_since_search += 1

        if action == Action.CLICK_RECOMMENDED:
            self._state.recommended_streak += 1
        else:
            self._state.recommended_streak = 0

        return action

    def _should_prioritize_search(self, unsearched: list[str]) -> bool:
        remaining_seconds = max(self._state.remaining_seconds(), 1.0)
        per_topic_budget = remaining_seconds / max(len(unsearched), 1)

        pressure = SEARCH_PRESSURE_DEFAULT
        for threshold, value in SEARCH_PRESSURE_THRESHOLDS:
            if per_topic_budget < threshold:
                pressure = value
                break

        style_adjustment = (0.5 - self._state.personality.search_style) * SEARCH_PRESSURE_STYLE_WEIGHT
        lo, hi = SEARCH_PRESSURE_CLAMP
        pressure = min(max(pressure + style_adjustment, lo), hi)
        return random.random() < pressure

    def _is_completed_video_pace_high(self) -> bool:
        elapsed_minutes = max(
            (self._state.duration_minutes * 60 - self._state.remaining_seconds()) / 60.0,
            0.1,
        )
        allowed_completed = (
            elapsed_minutes / VIDEO_PACE_TARGET_MIN_PER_COMPLETED
        ) + VIDEO_PACE_GRACE_COMPLETED
        return self._state.videos_watched > allowed_completed

    def _pick_video_pace_guard_action(self) -> Action:
        if self._state.mode == Mode.B:
            return self._weighted_choice(
                {
                    Action.IDLE: 55,
                    Action.SCROLL_RESULTS: 25,
                    Action.GO_BACK: 20,
                },
            )
        return self._weighted_choice(
            {
                Action.IDLE: 50,
                Action.SCROLL_FEED: 30,
                Action.GO_HOME: 20,
            },
        )
