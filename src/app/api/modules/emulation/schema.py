from uuid import UUID

from pydantic import BaseModel, Field


class StartEmulationRequest(BaseModel):
    duration_minutes: int = Field(ge=1, le=480, description="Session duration in minutes")
    topics: list[str] = Field(min_length=1, max_length=20, description="Search topics")


class StartEmulationResponse(BaseModel):
    session_id: UUID
    status: str


class EmulationSessionStatus(BaseModel):
    session_id: UUID
    status: str
    elapsed_minutes: float | None = None
    bytes_downloaded: int = 0
    topics_searched: list[str] = []
    videos_watched: int = 0
    mode: str | None = None
    fatigue: float | None = None
    error: str | None = None
