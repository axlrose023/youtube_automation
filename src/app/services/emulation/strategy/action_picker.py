import logging
import random

from ..core.selectors import MAX_RECOMMENDED_STREAK, MAX_SURF_STREAK
from ..core.state import Mode, SessionState

logger = logging.getLogger(__name__)


class ActionPicker:
    def __init__(self, state: SessionState) -> None:
        self._state = state
        self._just_searched: bool = False
        self._actions_since_search = 0
        self._reanchor_limit = random.randint(3, 6)
        self._offtopic_reanchor_limit = random.randint(1, 2)

    def pick(self) -> str:
        unsearched = self._state.unsearched_topics()

        if self._state.no_video_streak >= 2:
            self._state.no_video_streak = 0
            if unsearched:
                logger.info("Session %s: no-video recovery -> search", self._state.session_id)
                return self._finalize("search")
            logger.info("Session %s: no-video recovery -> go_home", self._state.session_id)
            return self._finalize("go_home")

        # Too many recommendations in a row — return to topic search
        if self._state.recommended_streak >= MAX_RECOMMENDED_STREAK:
            logger.info(
                "Session %s: recommended streak limit -> search to stay on topic",
                self._state.session_id,
            )
            return self._finalize("search")

        if self._state.topic_drifted:
            logger.info(
                "Session %s: topic drift detected -> re-anchor search",
                self._state.session_id,
            )
            return self._finalize("search")

        if self._state.offtopic_or_reco_streak >= self._offtopic_reanchor_limit:
            logger.info(
                "Session %s: off-topic/recommendation streak %d >= %d -> search",
                self._state.session_id,
                self._state.offtopic_or_reco_streak,
                self._offtopic_reanchor_limit,
            )
            return self._finalize("search")

        if self._state.topics and self._actions_since_search >= self._reanchor_limit:
            logger.info(
                "Session %s: periodic re-anchor after %d actions -> search",
                self._state.session_id,
                self._actions_since_search,
            )
            return self._finalize("search")

        # After search → user clicks a video from results
        if self._just_searched:
            self._just_searched = False
            if self._state.mode == Mode.B:
                action = "watch_focused"
            else:
                action = "watch_long"
            logger.info("Session %s: post-search -> %s", self._state.session_id, action)
            return self._finalize(action)

        if not self._state.searched_topics and unsearched:
            logger.info(
                "Session %s: first topic search (topics=%d)",
                self._state.session_id,
                len(self._state.topics),
            )
            return self._finalize("search")

        if unsearched and self._should_prioritize_search(unsearched):
            logger.info(
                "Session %s: dynamic coverage push (%d topics left) -> search",
                self._state.session_id,
                len(unsearched),
            )
            return self._finalize("search")

        if self._state.mode == Mode.B:
            return self._finalize(self._pick_task_mode(unsearched))
        return self._finalize(self._pick_entertainment_mode(unsearched))

    def _pick_entertainment_mode(self, unsearched: list[str]) -> str:
        weights = {
            "watch_long": 30,
            "click_recommended": 30,
            "scroll_feed": 15,
            "search": 10 if unsearched else 0,
            "idle": 5,
            "go_home": 3,
        }

        if self._state.fatigue > 0.6:
            weights["idle"] += 5
            weights["watch_long"] += 5
            weights["search"] = 0

        if self._state.fatigue > 0.8:
            weights["watch_long"] += 20
            weights["idle"] += 15
            weights["scroll_feed"] += 10
            weights["search"] = 0
            weights["click_recommended"] = max(weights["click_recommended"] - 10, 0)

        return self._weighted_choice(weights)

    def _pick_task_mode(self, unsearched: list[str]) -> str:
        if self._state.surf_streak >= MAX_SURF_STREAK:
            self._state.surf_streak = 0
            if unsearched:
                return "refine_search"
            return "click_recommended"

        weights = {
            "watch_focused": 30,
            "search": 20 if unsearched else 3,
            "surf_video": 18,
            "click_recommended": 12,
            "scroll_results": 8,
            "idle": 3,
            "go_back": 3,
        }

        if self._state.fatigue > 0.5:
            weights["surf_video"] += 8
            weights["idle"] += 3

        if self._state.fatigue > 0.8:
            weights["watch_focused"] += 15
            weights["idle"] += 10
            weights["scroll_results"] += 8
            weights["search"] = 0
            weights["surf_video"] = max(weights["surf_video"] - 5, 0)

        return self._weighted_choice(weights)

    @staticmethod
    def _weighted_choice(weights: dict[str, int]) -> str:
        actions = list(weights.keys())
        action_weights = [weights[action] for action in actions]
        if sum(action_weights) == 0:
            return "scroll_feed"
        return random.choices(actions, weights=action_weights, k=1)[0]

    def _finalize(self, action: str) -> str:
        if action in ("search", "refine_search"):
            self._just_searched = True
            self._actions_since_search = 0
            self._reanchor_limit = random.randint(3, 6)
            self._offtopic_reanchor_limit = random.randint(1, 2)
            self._state.topic_drifted = False
            self._state.offtopic_or_reco_streak = 0
        else:
            self._actions_since_search += 1

        if action == "click_recommended":
            self._state.recommended_streak += 1
        else:
            self._state.recommended_streak = 0

        return action

    def _should_prioritize_search(self, unsearched: list[str]) -> bool:
        remaining_seconds = max(self._state.remaining_seconds(), 1.0)
        per_topic_budget = remaining_seconds / max(len(unsearched), 1)

        if per_topic_budget < 90:
            pressure = 0.80
        elif per_topic_budget < 180:
            pressure = 0.60
        elif per_topic_budget < 360:
            pressure = 0.42
        else:
            pressure = 0.25

        style_adjustment = (0.5 - self._state.personality.search_style) * 0.20
        pressure = min(max(pressure + style_adjustment, 0.10), 0.85)
        return random.random() < pressure
