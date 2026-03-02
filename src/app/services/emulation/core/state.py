import random
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum


def _gauss_clamp(mean: float, std: float, lo: float, hi: float) -> float:
    return max(lo, min(random.gauss(mean, std), hi))


@dataclass
class EmulationResult:
    topics_searched: list[str] = field(default_factory=list)
    videos_watched: int = 0
    bytes_downloaded: int = 0


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

    searched_topics: list[str] = field(default_factory=list)
    videos_watched: int = 0
    fatigue: float = 0.0
    consecutive_fails: int = 0
    no_video_streak: int = 0
    surf_streak: int = 0
    recommended_streak: int = 0
    on_video_page: bool = False
    topic_drifted: bool = False
    current_topic: str | None = None
    cycle_start: float = 0.0
    cycle_duration: float = 0.0
    started_at_monotonic: float = field(default_factory=time.monotonic)
    mode: Mode = field(init=False)
    personality: SessionPersonality = field(init=False)
    topic_tokens: set[str] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.topics = [topic.strip() for topic in self.topics if topic and topic.strip()]
        random.shuffle(self.topics)
        self.mode = random.choice([Mode.A, Mode.B])
        self.personality = SessionPersonality()
        self.topic_tokens = self._build_topic_tokens()

    def remaining_seconds(self) -> float:
        total = self.duration_minutes * 60
        elapsed = max(time.monotonic() - self.started_at_monotonic, 0.0)
        return max(total - elapsed, 0.0)

    def unsearched_topics(self) -> list[str]:
        return [topic for topic in self.topics if topic not in self.searched_topics]

    def all_topics_covered(self) -> bool:
        return not self.unsearched_topics()

    def is_title_on_topic(self, title: str | None) -> bool:
        if not title:
            return False
        normalized_title = self._normalize_text(title)

        # Phrase-level match first to support multi-word topics.
        if any(topic and self._normalize_text(topic) in normalized_title for topic in self.topics):
            return True

        if not self.topic_tokens:
            return True
        return any(token in normalized_title for token in self.topic_tokens)

    def is_title_on_specific_topic(self, title: str | None, topic: str | None) -> bool:
        if not title or not topic:
            return False
        normalized_title = self._normalize_text(title)
        normalized_topic = self._normalize_text(topic)
        if normalized_topic and normalized_topic in normalized_title:
            return True

        tokens = [
            token
            for token in re.findall(r"[\wа-яА-ЯёЁ]+", normalized_topic)
            if len(token) >= 3
        ]
        if not tokens:
            return False
        return any(token in normalized_title for token in tokens)

    def _build_topic_tokens(self) -> set[str]:
        tokens: set[str] = set()
        for topic in self.topics:
            for token in re.findall(r"[\wа-яА-ЯёЁ]+", topic.lower()):
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.lower().split())
