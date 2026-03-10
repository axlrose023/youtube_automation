from __future__ import annotations

import uuid

from sqlalchemy import UUID, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, DateTimeMixin, UUID7IDMixin


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
    landing_status: Mapped[str] = mapped_column(String(20), default="pending")


    video_src_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_status: Mapped[str] = mapped_column(String(20), default="pending")


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
