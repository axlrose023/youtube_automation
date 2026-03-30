from __future__ import annotations

import datetime
import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import UUID, BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, DateTimeMixin, UUID7IDMixin


class VideoStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NO_SRC = "no_src"
    FALLBACK_SCREENSHOTS = "fallback_screenshots"


class LandingStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    NOT_RELEVANT = "not_relevant"
    SKIPPED = "skipped"
    FAILED = "failed"


class SessionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class PostProcessingStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


SESSION_TERMINAL_STATUSES = frozenset(
    {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.STOPPED,
    }
)

ANALYSIS_TERMINAL_STATUSES = frozenset(
    {
        AnalysisStatus.COMPLETED,
        AnalysisStatus.NOT_RELEVANT,
        AnalysisStatus.SKIPPED,
        AnalysisStatus.FAILED,
    }
)


class AdCapture(Base, UUID7IDMixin, DateTimeMixin):
    __tablename__ = "ad_captures"

    session_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_position: Mapped[int] = mapped_column(Integer)

    advertiser_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cta_href: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ad_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_status: Mapped[str] = mapped_column(String(20), default=LandingStatus.PENDING)

    video_src_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_status: Mapped[str] = mapped_column(String(20), default=VideoStatus.PENDING)

    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(20), default=AnalysisStatus.PENDING)

    screenshots: Mapped[list[AdCaptureScreenshot]] = relationship(
        back_populates="capture", cascade="all, delete-orphan",
    )


class AdCaptureScreenshot(Base, UUID7IDMixin, DateTimeMixin):
    __tablename__ = "ad_capture_screenshots"

    capture_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ad_captures.id", ondelete="CASCADE"),
        index=True,
    )
    offset_ms: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(Text)

    capture: Mapped[AdCapture] = relationship(back_populates="screenshots")


class EmulationSessionHistory(Base, DateTimeMixin):
    __tablename__ = "emulation_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default=SessionStatus.QUEUED)

    requested_duration_minutes: Mapped[int] = mapped_column(Integer)
    requested_topics: Mapped[list[str]] = mapped_column(JSONB, default=list)

    queued_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        index=True,
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    mode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    fatigue: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    personality: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0)
    total_duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    videos_watched: Mapped[int] = mapped_column(Integer, default=0)
    watched_videos_count: Mapped[int] = mapped_column(Integer, default=0)
    watched_ads_count: Mapped[int] = mapped_column(Integer, default=0)

    topics_searched: Mapped[list[str]] = mapped_column(JSONB, default=list)
    watched_videos: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    watched_ads: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    watched_ads_analytics: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
