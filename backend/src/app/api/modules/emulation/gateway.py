from __future__ import annotations

import datetime
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import Text, and_, cast, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    AdCapture,
    AdCaptureScreenshot,
    AnalysisStatus,
    EmulationSessionHistory,
    SessionStatus,
    VideoStatus,
)


@dataclass(frozen=True)
class EmulationHistoryListRow:
    session: EmulationSessionHistory
    ads_total: int
    video_captures: int
    screenshot_fallbacks: int


@dataclass(frozen=True)
class EmulationHistoryQuery:
    session_id: str | None = None
    status: str | None = None
    mode: str | None = None
    topic_search: str | None = None
    has_ads: bool | None = None
    has_video_capture: bool | None = None
    has_screenshot_capture: bool | None = None
    queued_from: datetime.datetime | None = None
    queued_to: datetime.datetime | None = None
    started_from: datetime.datetime | None = None
    started_to: datetime.datetime | None = None
    finished_from: datetime.datetime | None = None
    finished_to: datetime.datetime | None = None


class EmulationHistoryGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_if_missing(
        self,
        session_id: str,
        requested_duration_minutes: int,
        requested_topics: list[str],
        queued_at: datetime.datetime | None = None,
    ) -> EmulationSessionHistory:
        existing = await self.get_by_session_id(session_id)
        if existing:
            return existing

        payload = EmulationSessionHistory(
            session_id=session_id,
            status=SessionStatus.QUEUED,
            requested_duration_minutes=requested_duration_minutes,
            requested_topics=requested_topics,
            queued_at=queued_at or datetime.datetime.now(datetime.UTC),
        )
        self.session.add(payload)
        await self.session.flush()
        return payload

    async def get_by_session_id(self, session_id: str) -> EmulationSessionHistory | None:
        stmt = select(EmulationSessionHistory).where(
            EmulationSessionHistory.session_id == session_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_statuses(
        self,
        statuses: list[str],
    ) -> list[EmulationSessionHistory]:
        if not statuses:
            return []

        stmt = (
            select(EmulationSessionHistory)
            .where(EmulationSessionHistory.status.in_(statuses))
            .order_by(
                EmulationSessionHistory.queued_at.desc(),
                EmulationSessionHistory.session_id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_session(
        self,
        session_id: str,
        **fields: object,
    ) -> EmulationSessionHistory | None:
        payload = await self.get_by_session_id(session_id)
        if not payload:
            return None

        for field, value in fields.items():
            if hasattr(payload, field):
                setattr(payload, field, value)
        await self.session.flush()
        return payload

    async def get_total_count(self, query: EmulationHistoryQuery) -> int:
        filters = self._build_filters(query)
        stmt = select(func.count()).select_from(EmulationSessionHistory).where(*filters)
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)

    async def get_history(
        self,
        query: EmulationHistoryQuery,
        limit: int,
        offset: int,
    ) -> list[EmulationHistoryListRow]:
        filters = self._build_filters(query)
        captures_agg = (
            select(
                AdCapture.session_id.label("session_id"),
                func.count(AdCapture.id).label("ads_total"),
                func.count()
                .filter(AdCapture.video_status == VideoStatus.COMPLETED)
                .label("video_captures"),
                func.count()
                .filter(AdCapture.video_status == VideoStatus.FALLBACK_SCREENSHOTS)
                .label("screenshot_fallbacks"),
            )
            .group_by(AdCapture.session_id)
            .subquery()
        )

        stmt = (
            select(
                EmulationSessionHistory,
                func.coalesce(captures_agg.c.ads_total, 0),
                func.coalesce(captures_agg.c.video_captures, 0),
                func.coalesce(captures_agg.c.screenshot_fallbacks, 0),
            )
            .outerjoin(
                captures_agg,
                captures_agg.c.session_id == EmulationSessionHistory.session_id,
            )
            .where(*filters)
            .order_by(
                EmulationSessionHistory.queued_at.desc(),
                EmulationSessionHistory.session_id.desc(),
            )
            .offset(offset=offset)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        rows: list[EmulationHistoryListRow] = []
        for payload, ads_total, video_captures, screenshot_fallbacks in result.all():
            rows.append(
                EmulationHistoryListRow(
                    session=payload,
                    ads_total=int(ads_total),
                    video_captures=int(video_captures),
                    screenshot_fallbacks=int(screenshot_fallbacks),
                )
            )
        return rows

    async def get_ad_captures_by_session(self, session_id: str) -> list[AdCapture]:
        stmt = (
            select(AdCapture)
            .options(selectinload(AdCapture.screenshots))
            .where(AdCapture.session_id == session_id)
            .order_by(AdCapture.ad_position.asc(), AdCapture.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_ad_captures_by_sessions(
        self, session_ids: list[str]
    ) -> dict[str, list[AdCapture]]:
        if not session_ids:
            return {}

        stmt = (
            select(AdCapture)
            .options(selectinload(AdCapture.screenshots))
            .where(AdCapture.session_id.in_(session_ids))
            .order_by(
                AdCapture.session_id.asc(),
                AdCapture.ad_position.asc(),
                AdCapture.created_at.asc(),
            )
        )
        result = await self.session.execute(stmt)
        captures_by_session: dict[str, list[AdCapture]] = {}
        for capture in result.scalars().all():
            captures_by_session.setdefault(capture.session_id, []).append(capture)
        return captures_by_session

    async def delete_session(self, session_id: str) -> bool:
        payload = await self.get_by_session_id(session_id)
        if not payload:
            return False
        await self.session.execute(
            delete(AdCapture).where(AdCapture.session_id == session_id)
        )
        await self.session.delete(payload)
        await self.session.flush()
        return True

    async def get_dashboard_base_summary(self) -> dict[str, int]:
        stmt = select(
            func.count(EmulationSessionHistory.session_id),
            func.coalesce(
                func.count().filter(EmulationSessionHistory.status == SessionStatus.COMPLETED),
                0,
            ),
            func.coalesce(
                func.count().filter(EmulationSessionHistory.status == SessionStatus.RUNNING),
                0,
            ),
            func.coalesce(
                func.count().filter(EmulationSessionHistory.status == SessionStatus.FAILED),
                0,
            ),
            func.coalesce(
                func.count().filter(EmulationSessionHistory.status == SessionStatus.STOPPED),
                0,
            ),
            func.coalesce(func.sum(EmulationSessionHistory.videos_watched), 0),
            func.coalesce(func.sum(EmulationSessionHistory.watched_ads_count), 0),
        )
        result = await self.session.execute(stmt)
        total_sessions, completed, running, failed, stopped, total_videos_watched, total_ads_watched = result.one()
        return {
            "total_sessions": int(total_sessions or 0),
            "completed": int(completed or 0),
            "running": int(running or 0),
            "failed": int(failed or 0),
            "stopped": int(stopped or 0),
            "total_videos_watched": int(total_videos_watched or 0),
            "total_ads_watched": int(total_ads_watched or 0),
        }

    async def get_dashboard_capture_summary(self) -> dict[str, int]:
        advertiser_domain = func.coalesce(func.nullif(func.trim(AdCapture.advertiser_domain), ""), "unknown")
        stmt = select(
            func.count(AdCapture.id),
            func.coalesce(func.count().filter(AdCapture.video_status == VideoStatus.COMPLETED), 0),
            func.coalesce(func.count().filter(AdCapture.video_status == VideoStatus.FALLBACK_SCREENSHOTS), 0),
            func.coalesce(func.count().filter(AdCapture.landing_status == "completed"), 0),
            func.coalesce(func.count().filter(AdCapture.analysis_status == AnalysisStatus.COMPLETED), 0),
            func.coalesce(func.count().filter(AdCapture.analysis_status == AnalysisStatus.NOT_RELEVANT), 0),
            advertiser_domain,
        ).group_by(advertiser_domain)
        result = await self.session.execute(stmt)

        total_ad_captures = 0
        video_captures = 0
        screenshot_fallbacks = 0
        landing_completed = 0
        relevant_ads = 0
        not_relevant_ads = 0
        advertiser_counts: list[tuple[str, int]] = []

        for (
            group_total,
            group_videos,
            group_screenshots,
            group_landings,
            group_relevant,
            group_not_relevant,
            domain,
        ) in result.all():
            total_ad_captures += int(group_total or 0)
            video_captures += int(group_videos or 0)
            screenshot_fallbacks += int(group_screenshots or 0)
            landing_completed += int(group_landings or 0)
            relevant_ads += int(group_relevant or 0)
            not_relevant_ads += int(group_not_relevant or 0)
            advertiser_counts.append((str(domain or "unknown"), int(group_total or 0)))

        advertiser_counts.sort(key=lambda item: (-item[1], item[0]))
        top_advertisers = advertiser_counts[:6]

        return {
            "total_ad_captures": total_ad_captures,
            "video_captures": video_captures,
            "screenshot_fallbacks": screenshot_fallbacks,
            "landing_completed": landing_completed,
            "relevant_ads": relevant_ads,
            "not_relevant_ads": not_relevant_ads,
            "analyzed_ads": relevant_ads + not_relevant_ads,
            "top_advertisers": top_advertisers,
        }

    async def get_top_requested_topics(self, limit: int = 8) -> list[tuple[str, int]]:
        stmt = select(EmulationSessionHistory.requested_topics)
        result = await self.session.execute(stmt)
        counts: Counter[str] = Counter()

        for topics in result.scalars().all():
            if not isinstance(topics, list):
                continue
            for topic in topics:
                if isinstance(topic, str):
                    normalized = topic.strip()
                    if normalized:
                        counts[normalized] += 1

        return counts.most_common(limit)

    def _build_filters(self, query: EmulationHistoryQuery) -> list:
        filters: list = []

        if query.session_id:
            filters.append(EmulationSessionHistory.session_id == query.session_id)
        if query.status:
            filters.append(EmulationSessionHistory.status == query.status)
        if query.mode:
            filters.append(EmulationSessionHistory.mode == query.mode)
        if query.topic_search:
            like_value = f"%{query.topic_search}%"
            filters.append(
                or_(
                    cast(EmulationSessionHistory.requested_topics, Text).ilike(like_value),
                    cast(EmulationSessionHistory.topics_searched, Text).ilike(like_value),
                )
            )
        if query.queued_from:
            filters.append(EmulationSessionHistory.queued_at >= query.queued_from)
        if query.queued_to:
            filters.append(EmulationSessionHistory.queued_at <= query.queued_to)
        if query.started_from:
            filters.append(EmulationSessionHistory.started_at >= query.started_from)
        if query.started_to:
            filters.append(EmulationSessionHistory.started_at <= query.started_to)
        if query.finished_from:
            filters.append(EmulationSessionHistory.finished_at >= query.finished_from)
        if query.finished_to:
            filters.append(EmulationSessionHistory.finished_at <= query.finished_to)

        if query.has_ads is not None:
            any_ads = exists(
                select(1).where(AdCapture.session_id == EmulationSessionHistory.session_id)
            )
            filters.append(any_ads if query.has_ads else ~any_ads)

        if query.has_video_capture is not None:
            any_video_capture = exists(
                select(1).where(
                    and_(
                        AdCapture.session_id == EmulationSessionHistory.session_id,
                        AdCapture.video_status == VideoStatus.COMPLETED,
                    )
                )
            )
            filters.append(any_video_capture if query.has_video_capture else ~any_video_capture)

        if query.has_screenshot_capture is not None:
            any_screenshot_capture = exists(
                select(1).where(
                    and_(
                        AdCapture.session_id == EmulationSessionHistory.session_id,
                        AdCapture.video_status == VideoStatus.FALLBACK_SCREENSHOTS,
                    )
                )
            )
            filters.append(
                any_screenshot_capture
                if query.has_screenshot_capture
                else ~any_screenshot_capture
            )

        return filters


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

    async def update_analysis(
        self, capture_id: uuid.UUID, status: str, summary: str | None = None,
    ) -> None:
        capture = await self.session.get(AdCapture, capture_id)
        if capture:
            capture.analysis_status = status
            if summary is not None:
                capture.analysis_summary = summary
