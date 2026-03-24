import random
import time
from dataclasses import dataclass, field
from enum import StrEnum

from ..config import LOCK_TASK_MODE
from .topic_matcher import (
    build_topic_tokens,
    is_title_on_specific_topic,
    is_title_on_topic,
    matched_topics_for_title,
)
from .video_url import is_same_video_url, video_id_from_url


def _gauss_clamp(mean: float, std: float, lo: float, hi: float) -> float:
    return max(lo, min(random.gauss(mean, std), hi))


@dataclass
class EmulationResult:
    topics_searched: list[str] = field(default_factory=list)
    videos_watched: int = 0
    bytes_downloaded: int = 0
    total_duration_seconds: int = 0
    watched_videos: list[dict[str, object]] = field(default_factory=list)
    watched_ads: list[dict[str, object]] = field(default_factory=list)


@dataclass
class SessionPersonality:
    pace: float = field(default_factory=lambda: _gauss_clamp(1.0, 0.15, 0.6, 1.4))
    patience: float = field(default_factory=lambda: _gauss_clamp(1.0, 0.2, 0.5, 1.5))
    focus_span: float = field(default_factory=lambda: _gauss_clamp(1.0, 0.15, 0.7, 1.3))
    search_style: float = field(default_factory=lambda: random.random())
    ad_tolerance: float = field(default_factory=lambda: _gauss_clamp(0.7, 0.2, 0.3, 1.0))


class Mode(StrEnum):
    A = "entertainment"
    B = "task"


@dataclass
class SessionState:

    topics: list[str]
    duration_minutes: int
    session_id: str
    bootstrap: dict[str, object] | None = None

    searched_topics: list[str] = field(default_factory=list)
    videos_watched: int = 0
    watched_videos: list[dict[str, object]] = field(default_factory=list)
    watched_ads: list[dict[str, object]] = field(default_factory=list)
    seen_video_ids: set[str] = field(default_factory=set)

    fatigue: float = 0.0
    on_video_page: bool = False
    topic_drifted: bool = False
    last_watch_on_topic: bool | None = None
    current_topic: str | None = None

    consecutive_fails: int = 0
    no_video_streak: int = 0
    surf_streak: int = 0
    recommended_streak: int = 0
    offtopic_or_reco_streak: int = 0

    last_clicked_video_title: str | None = None
    last_clicked_video_url: str | None = None
    current_watch: dict[str, object] | None = None
    stop_requested: bool = False

    cycle_start: float = 0.0
    cycle_duration: float = 0.0
    started_at_monotonic: float = field(default_factory=time.monotonic)
    started_at_wallclock: float = field(default_factory=time.time)

    mode: Mode = field(init=False)
    initial_mode: Mode = field(init=False)
    mode_locked: bool = field(init=False, default=False)
    personality: SessionPersonality = field(init=False)
    topic_tokens: set[str] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.topics = [topic.strip() for topic in self.topics if topic and topic.strip()]
        random.shuffle(self.topics)
        self.mode = Mode.B if self.topics else random.choice([Mode.A, Mode.B])
        self.initial_mode = self.mode
        self.mode_locked = bool(LOCK_TASK_MODE and self.mode == Mode.B)
        self.personality = SessionPersonality()
        self.topic_tokens = build_topic_tokens(self.topics)
        self._apply_bootstrap()

    def remaining_seconds(self) -> float:
        total = self.duration_minutes * 60
        elapsed_monotonic = max(time.monotonic() - self.started_at_monotonic, 0.0)
        elapsed_wallclock = max(time.time() - self.started_at_wallclock, 0.0)
        elapsed = max(elapsed_monotonic, elapsed_wallclock)
        return max(total - elapsed, 0.0)

    def unsearched_topics(self) -> list[str]:
        return [topic for topic in self.topics if topic not in self.searched_topics]

    def all_topics_covered(self) -> bool:
        return not self.unsearched_topics()

    # ── Topic matching (delegates to topic_matcher module) ────

    def is_title_on_topic(self, title: str | None) -> bool:
        return is_title_on_topic(title, self.topics, self.topic_tokens)

    def is_title_on_specific_topic(self, title: str | None, topic: str | None) -> bool:
        return is_title_on_specific_topic(title, topic)

    def matched_topics_for_title(self, title: str | None) -> list[str]:
        return matched_topics_for_title(title, self.topics)

    # ── Video URL helpers (delegates to video_url module) ─────

    @staticmethod
    def video_id_from_url(raw_url: str | None) -> str | None:
        return video_id_from_url(raw_url)

    def is_seen_video(self, raw_url: str | None) -> bool:
        vid = video_id_from_url(raw_url)
        return bool(vid and vid in self.seen_video_ids)

    def mark_video_seen(self, raw_url: str | None) -> None:
        vid = video_id_from_url(raw_url)
        if vid:
            self.seen_video_ids.add(vid)

    # ── Video / Ad recording ─────────────────────────────────

    def add_watched_video(
        self,
        *,
        action: str,
        title: str | None,
        url: str | None,
        watched_seconds: float,
        target_seconds: float,
        completed: bool,
        merge_if_same_url: bool = False,
    ) -> None:
        clean_title = (title or "").strip() or "<unknown>"
        clean_url = (url or "").strip()
        matched = self.matched_topics_for_title(clean_title)
        self.last_watch_on_topic = bool(matched)
        if self.topics and not self.last_watch_on_topic:
            self.topic_drifted = True

        keywords: list[str] = []
        if self.current_topic:
            keywords.append(self.current_topic)
        for topic in matched:
            if topic not in keywords:
                keywords.append(topic)

        watched = round(max(watched_seconds, 0.0), 1)
        target = round(max(target_seconds, 0.0), 1)
        ratio = round(watched / target, 3) if target > 0 else None

        if merge_if_same_url and self.watched_videos:
            previous = self.watched_videos[-1]
            previous_url = str(previous.get("url") or "")
            if is_same_video_url(clean_url, previous_url):
                self._merge_into_previous(previous, watched, target, completed, matched, keywords)
                self.mark_video_seen(clean_url)
                return

        self.watched_videos.append(
            {
                "position": len(self.watched_videos) + 1,
                "action": action,
                "title": clean_title,
                "url": clean_url,
                "watched_seconds": watched,
                "target_seconds": target,
                "watch_ratio": ratio,
                "completed": completed,
                "search_keyword": self.current_topic,
                "matched_topics": matched,
                "keywords": keywords,
                "recorded_at": time.time(),
            }
        )
        self.mark_video_seen(clean_url)

    def _merge_into_previous(
        self,
        previous: dict[str, object],
        watched: float,
        target: float,
        completed: bool,
        matched: list[str],
        keywords: list[str],
    ) -> None:
        prev_watched = float(previous.get("watched_seconds") or 0.0)
        prev_target = float(previous.get("target_seconds") or 0.0)
        merged_watched = round(prev_watched + watched, 1)
        merged_target = round(prev_target + target, 1)
        previous["watched_seconds"] = merged_watched
        previous["target_seconds"] = merged_target
        previous["watch_ratio"] = (
            round(merged_watched / merged_target, 3) if merged_target > 0 else None
        )
        previous["completed"] = bool(previous.get("completed")) or completed
        previous["recorded_at"] = time.time()

        existing_topics = list(previous.get("matched_topics") or [])
        for topic in matched:
            if topic not in existing_topics:
                existing_topics.append(topic)
        previous["matched_topics"] = existing_topics

        existing_keywords = list(previous.get("keywords") or [])
        for kw in keywords:
            if kw not in existing_keywords:
                existing_keywords.append(kw)
        previous["keywords"] = existing_keywords

    def add_watched_ad(self, record: dict[str, object]) -> dict[str, object]:
        ad_record = dict(record)
        ad_record["position"] = len(self.watched_ads) + 1
        ad_record["recorded_at"] = time.time()
        self.watched_ads.append(ad_record)
        return ad_record

    def start_current_watch(
        self,
        *,
        action: str,
        title: str | None,
        url: str | None,
        target_seconds: float | None = None,
    ) -> None:
        clean_title = (title or "").strip() or "<unknown>"
        clean_url = (url or "").strip()
        matched = self.matched_topics_for_title(clean_title)
        keywords: list[str] = []
        if self.current_topic:
            keywords.append(self.current_topic)
        for topic in matched:
            if topic not in keywords:
                keywords.append(topic)

        self.current_watch = {
            "action": action,
            "title": clean_title,
            "url": clean_url,
            "started_at": time.time(),
            "watched_seconds": 0.0,
            "target_seconds": round(target_seconds, 1) if target_seconds and target_seconds > 0 else None,
            "search_keyword": self.current_topic,
            "matched_topics": matched,
            "keywords": keywords,
        }

    def increment_current_watch(self, delta_seconds: float) -> None:
        if not self.current_watch or delta_seconds <= 0:
            return
        watched = float(self.current_watch.get("watched_seconds") or 0.0)
        self.current_watch["watched_seconds"] = round(watched + delta_seconds, 1)

    def update_current_watch(self, *, target_seconds: float | None = None) -> None:
        if not self.current_watch:
            return
        if target_seconds is not None:
            self.current_watch["target_seconds"] = (
                round(target_seconds, 1) if target_seconds > 0 else None
            )

    def clear_current_watch(self) -> None:
        self.current_watch = None

    def finalize_current_watch(
        self,
        *,
        completed: bool,
        merge_if_same_url: bool = True,
        min_watched_seconds: float = 3.0,
    ) -> bool:
        if not self.current_watch:
            return False

        current = dict(self.current_watch)
        self.current_watch = None

        action = str(current.get("action") or "").strip()
        title = str(current.get("title") or "").strip()
        url = str(current.get("url") or "").strip()
        watched_seconds = self._coerce_float(current.get("watched_seconds")) or 0.0
        target_seconds = self._coerce_float(current.get("target_seconds")) or watched_seconds

        if not action or not url or watched_seconds < min_watched_seconds:
            return False

        self.add_watched_video(
            action=action,
            title=title,
            url=url,
            watched_seconds=watched_seconds,
            target_seconds=target_seconds,
            completed=completed,
            merge_if_same_url=merge_if_same_url,
        )
        return True

    def request_stop(self) -> None:
        self.stop_requested = True

    # ── Bootstrap ─────────────────────────────────────────────

    def _apply_bootstrap(self) -> None:
        if not isinstance(self.bootstrap, dict):
            return

        searched_topics = self._coerce_str_list(self.bootstrap.get("searched_topics"))
        if searched_topics:
            self.searched_topics = searched_topics

        watched_videos = self._coerce_dict_list(self.bootstrap.get("watched_videos"))
        if watched_videos:
            self.watched_videos = watched_videos

        watched_ads = self._coerce_dict_list(self.bootstrap.get("watched_ads"))
        if watched_ads:
            self.watched_ads = watched_ads

        videos_watched = self._coerce_int(self.bootstrap.get("videos_watched"))
        if videos_watched >= 0:
            self.videos_watched = videos_watched

        current_topic = self.bootstrap.get("current_topic")
        if isinstance(current_topic, str) and current_topic.strip():
            self.current_topic = current_topic.strip()

        fatigue = self._coerce_float(self.bootstrap.get("fatigue"))
        if fatigue is not None:
            self.fatigue = max(0.0, min(fatigue, 1.0))

        mode_raw = self.bootstrap.get("mode")
        if isinstance(mode_raw, str) and mode_raw in {Mode.A.value, Mode.B.value}:
            self.mode = Mode(mode_raw)
            self.initial_mode = self.mode
            self.mode_locked = bool(LOCK_TASK_MODE and self.mode == Mode.B)

        personality_payload = self.bootstrap.get("personality")
        if isinstance(personality_payload, dict):
            pace = self._coerce_float(personality_payload.get("pace"))
            patience = self._coerce_float(personality_payload.get("patience"))
            focus_span = self._coerce_float(personality_payload.get("focus_span"))
            search_style = self._coerce_float(personality_payload.get("search_style"))
            ad_tolerance = self._coerce_float(personality_payload.get("ad_tolerance"))
            if None not in {pace, patience, focus_span, search_style, ad_tolerance}:
                self.personality = SessionPersonality(
                    pace=max(0.6, min(pace, 1.4)),
                    patience=max(0.5, min(patience, 1.5)),
                    focus_span=max(0.7, min(focus_span, 1.3)),
                    search_style=max(0.0, min(search_style, 1.0)),
                    ad_tolerance=max(0.3, min(ad_tolerance, 1.0)),
                )

        seen_ids = self.bootstrap.get("seen_video_ids")
        if isinstance(seen_ids, list):
            for seen_id in seen_ids:
                if isinstance(seen_id, str) and seen_id:
                    self.seen_video_ids.add(seen_id)

        for video in self.watched_videos:
            if isinstance(video, dict):
                self.mark_video_seen(video.get("url"))

    # ── Coercion helpers ──────────────────────────────────────

    @staticmethod
    def _coerce_str_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        output: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if cleaned and cleaned not in output:
                output.append(cleaned)
        return output

    @staticmethod
    def _coerce_dict_list(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        output: list[dict[str, object]] = []
        for item in value:
            if isinstance(item, dict):
                output.append(dict(item))
        return output

    @staticmethod
    def _coerce_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        return -1

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None
