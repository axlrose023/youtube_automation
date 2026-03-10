from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import AdCapture, AdCaptureScreenshot


class AdCaptureGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, capture: AdCapture) -> AdCapture:
        self.session.add(capture)
        await self.session.flush()
        return capture

    async def add_screenshot(self, screenshot: AdCaptureScreenshot) -> None:
        self.session.add(screenshot)
        await self.session.flush()

    async def get_by_session(self, session_id: str) -> list[AdCapture]:
        stmt = (
            select(AdCapture)
            .options(selectinload(AdCapture.screenshots))
            .where(AdCapture.session_id == session_id)
            .order_by(AdCapture.ad_position.asc(), AdCapture.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_landing_status(
        self, capture_id: uuid.UUID, status: str, landing_dir: str | None = None,
    ) -> None:
        capture = await self.session.get(AdCapture, capture_id)
        if capture:
            capture.landing_status = status
            if landing_dir:
                capture.landing_dir = landing_dir

    async def update_video_status(
        self, capture_id: uuid.UUID, status: str, video_file: str | None = None,
    ) -> None:
        capture = await self.session.get(AdCapture, capture_id)
        if capture:
            capture.video_status = status
            if video_file:
                capture.video_file = video_file
