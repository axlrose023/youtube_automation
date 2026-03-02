from uuid import UUID

from pydantic import BaseModel, Field


class StartEmulationRequest(BaseModel):
    duration_minutes: int = Field(ge=1, le=480, description="Session duration in minutes")
    topics: list[str] = Field(min_length=1, max_length=20, description="Search topics")


class StartEmulationResponse(BaseModel):
    session_id: UUID
    status: str


class EmulationWatchedVideo(BaseModel):
    position: int
    action: str
    title: str
    url: str
    watched_seconds: float
    target_seconds: float
    watch_ratio: float | None = None
    completed: bool
    search_keyword: str | None = None
    matched_topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    recorded_at: float


class EmulationSessionStatus(BaseModel):
    session_id: UUID
    status: str
    elapsed_minutes: float | None = None
    bytes_downloaded: int = 0
    topics_searched: list[str] = Field(default_factory=list)
    videos_watched: int = 0
    watched_videos_count: int = 0
    total_duration_seconds: int = 0
    watched_videos: list[EmulationWatchedVideo] = Field(default_factory=list)
    mode: str | None = None
    fatigue: float | None = None
    error: str | None = None
