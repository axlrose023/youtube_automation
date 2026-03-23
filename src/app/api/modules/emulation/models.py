from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, DateTimeMixin


class EmulationSessionHistory(Base, DateTimeMixin):
    __tablename__ = "emulation_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")

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
