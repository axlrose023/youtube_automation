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
    SEARCH_MIN_ACTIONS_BETWEEN_OPTIONAL_SEARCHES,
    SEARCH_PRESSURE_STYLE_WEIGHT,
    SEARCH_PRESSURE_THRESHOLDS,
    TOPIC_BALANCE_FORCE_SEARCH_EXCESS_S,
    TOPIC_BALANCE_FORCE_SEARCH_MIN_REMAINING_S,
    TASK_FATIGUE_HIGH,
    TASK_FATIGUE_MEDIUM,
    VIDEO_PACE_GRACE_COMPLETED,
    VIDEO_PACE_PRE_COVERAGE_MIN_COMPLETED,
    VIDEO_PACE_PRE_COVERAGE_MIN_ELAPSED_MIN,
    VIDEO_PACE_PRE_COVERAGE_PENDING_BONUS_CAP,
    VIDEO_PACE_PRE_COVERAGE_PENDING_BONUS_PER_TOPIC,
    VIDEO_PACE_TARGET_MIN_PER_COMPLETED,
)
from ..core.session.state import Mode, SessionState

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

        if self._state.resume_needs_reanchor and self._state.current_topic:
            logger.info(
                "Session %s: resume bootstrap -> re-anchor search for %s",
                self._state.session_id,
                self._state.current_topic,
            )
            self._state.resume_needs_reanchor = False
            return self._finalize(Action.SEARCH)

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

        if self._state.should_force_pre_coverage_rotation():
            logger.info(
                "Session %s: pre-coverage rotation -> search (current=%s, spent=%.0fs, pending_topics=%d)",
                self._state.session_id,
                self._state.current_topic or "<none>",
                self._state.current_topic_watch_seconds(),
                len(unsearched),
            )
            return self._finalize(Action.SEARCH)

        if self._should_force_topic_rebalance(unsearched):
            logger.info(
                "Session %s: topic balance rebalance -> search (current=%s, next=%s, excess=%.0fs)",
                self._state.session_id,
                self._state.current_topic or "<none>",
                self._state.least_covered_topic() or "<none>",
                self._state.current_topic_excess_seconds(),
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

        if unsearched and self._can_run_optional_search() and self._should_prioritize_search(unsearched):
            logger.info(
                "Session %s: dynamic coverage push (%d topics left) -> search",
                self._state.session_id,
                len(unsearched),
            )
            return self._finalize(Action.SEARCH)

        pace_guard_reason = self._pace_guard_reason(unsearched)
        if pace_guard_reason:
            action = self._pick_video_pace_guard_action(pre_coverage=pace_guard_reason == "pre_coverage")
            logger.info(
                "Session %s: pace guard (%s, completed=%d, pending_topics=%d) -> %s",
                self._state.session_id,
                pace_guard_reason,
                self._state.videos_watched,
                len(unsearched),
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
            Action.SEARCH: 5 if unsearched and self._can_run_optional_search() else 0,
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
            Action.SEARCH: 12 if unsearched and self._can_run_optional_search() else 1,
            Action.SURF_VIDEO: 6,
            Action.CLICK_RECOMMENDED: 4,
            Action.SCROLL_RESULTS: 6,
            Action.IDLE: 8,
            Action.GO_BACK: 2,
        }

        if unsearched and self._state.should_block_recommended_before_coverage():
            weights[Action.CLICK_RECOMMENDED] = 0
            weights[Action.WATCH_FOCUSED] = max(weights[Action.WATCH_FOCUSED] - 20, 10)
            weights[Action.SEARCH] += 18
            weights[Action.SCROLL_RESULTS] += 10
            weights[Action.IDLE] += 6

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

    def _can_run_optional_search(self) -> bool:
        if not self._state.searched_topics:
            return True
        return self._actions_since_search >= SEARCH_MIN_ACTIONS_BETWEEN_OPTIONAL_SEARCHES

    def _should_force_topic_rebalance(self, unsearched: list[str]) -> bool:
        if unsearched or not self._state.topic_balance_enabled():
            return False
        if self._actions_since_search < 1:
            return False
        if self._state.remaining_seconds() < TOPIC_BALANCE_FORCE_SEARCH_MIN_REMAINING_S:
            return False
        return self._state.current_topic_excess_seconds() >= TOPIC_BALANCE_FORCE_SEARCH_EXCESS_S

    def _pace_guard_reason(self, unsearched: list[str]) -> str | None:
        if not unsearched and self._is_completed_video_pace_high():
            return "post_coverage"
        if self._is_pre_coverage_video_pace_high(unsearched):
            return "pre_coverage"
        return None

    def _is_completed_video_pace_high(self) -> bool:
        return self._state.videos_watched > self._allowed_completed_videos()

    def _is_pre_coverage_video_pace_high(self, unsearched: list[str]) -> bool:
        if not unsearched:
            return False
        if self._state.videos_watched < VIDEO_PACE_PRE_COVERAGE_MIN_COMPLETED:
            return False

        elapsed_minutes = self._elapsed_minutes()
        if elapsed_minutes < VIDEO_PACE_PRE_COVERAGE_MIN_ELAPSED_MIN:
            return False

        pending_bonus = min(
            len(unsearched) * VIDEO_PACE_PRE_COVERAGE_PENDING_BONUS_PER_TOPIC,
            VIDEO_PACE_PRE_COVERAGE_PENDING_BONUS_CAP,
        )
        allowed_completed = self._allowed_completed_videos() + pending_bonus
        return self._state.videos_watched > allowed_completed

    def _allowed_completed_videos(self) -> float:
        elapsed_minutes = self._elapsed_minutes()
        return (elapsed_minutes / VIDEO_PACE_TARGET_MIN_PER_COMPLETED) + VIDEO_PACE_GRACE_COMPLETED

    def _elapsed_minutes(self) -> float:
        return max(
            (self._state.duration_minutes * 60 - self._state.remaining_seconds()) / 60.0,
            0.1,
        )

    def _pick_video_pace_guard_action(self, *, pre_coverage: bool) -> Action:
        if self._state.mode == Mode.B:
            if pre_coverage:
                return self._weighted_choice(
                    {
                        Action.IDLE: 45,
                        Action.SCROLL_RESULTS: 35,
                        Action.GO_BACK: 20,
                    },
                )
            return self._weighted_choice(
                {
                    Action.IDLE: 55,
                    Action.SCROLL_RESULTS: 25,
                    Action.GO_BACK: 20,
                },
            )
        if pre_coverage:
            return self._weighted_choice(
                {
                    Action.IDLE: 45,
                    Action.SCROLL_FEED: 35,
                    Action.GO_HOME: 20,
                },
            )
        return self._weighted_choice(
            {
                Action.IDLE: 50,
                Action.SCROLL_FEED: 30,
                Action.GO_HOME: 20,
            },
        )
