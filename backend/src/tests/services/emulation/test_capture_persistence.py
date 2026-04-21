from __future__ import annotations

import datetime
import json
import uuid

import pytest

from app.api.modules.emulation.models import (
    AdCapture,
    AdCaptureScreenshot,
    AnalysisStatus,
    LandingStatus,
    VideoStatus,
)
from app.services.emulation.persistence.captures import CapturePersistenceService


class _FakeAdCaptureGateway:
    def __init__(self) -> None:
        self._captures: list[AdCapture] = []

    async def get_raw_by_session(self, session_id: str) -> list[AdCapture]:
        return [
            capture
            for capture in sorted(self._captures, key=lambda item: item.ad_position)
            if capture.session_id == session_id
        ]

    async def get_by_session(self, session_id: str) -> list[AdCapture]:
        return await self.get_raw_by_session(session_id)

    async def create(self, capture: AdCapture) -> AdCapture:
        if getattr(capture, "id", None) is None:
            capture.id = uuid.uuid4()
        capture.screenshots = []
        self._captures.append(capture)
        return capture

    async def get_or_create(
        self,
        session_id: str,
        ad_position: int,
    ) -> AdCapture:
        for capture in self._captures:
            if capture.session_id == session_id and capture.ad_position == ad_position:
                return capture

        return await self.create(AdCapture(session_id=session_id, ad_position=ad_position))

    async def set_screenshots(
        self,
        capture: AdCapture,
        screenshots: list[AdCaptureScreenshot],
    ) -> None:
        capture.screenshots = screenshots

    async def delete_missing_positions(
        self,
        session_id: str,
        positions: set[int],
    ) -> None:
        self._captures = [
            capture
            for capture in self._captures
            if capture.session_id != session_id or capture.ad_position in positions
        ]

    async def delete_by_ids(self, capture_ids: list[uuid.UUID]) -> None:
        capture_id_set = set(capture_ids)
        self._captures = [
            capture
            for capture in self._captures
            if capture.id not in capture_id_set
        ]


class _FakeUnitOfWork:
    def __init__(self) -> None:
        self.ad_captures = _FakeAdCaptureGateway()
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _watched_ad(
    *,
    position: int,
    capture_id: str | None = None,
    headline_text: str,
    video_file: str,
    screenshot_path: str,
    screenshot_paths: list[object] | None = None,
    landing_dir: str | None = None,
    landing_status: str = LandingStatus.PENDING,
    analysis_status: str = AnalysisStatus.PENDING,
    analysis_summary: dict | None = None,
) -> dict:
    watched_ad = {
        "position": position,
        "headline_text": headline_text,
        "advertiser_domain": "example.com",
        "display_url": "https://example.com/offer",
        "ad_duration_seconds": 17.0,
        "capture": {
            "landing_url": "https://example.com/offer",
            "landing_dir": landing_dir,
            "landing_status": landing_status,
            "video_src_url": None,
            "video_file": video_file,
            "video_status": VideoStatus.COMPLETED,
            "analysis_status": analysis_status,
            "analysis_summary": analysis_summary,
            "screenshot_paths": screenshot_paths or [(0, screenshot_path)],
            "cta_href": "https://example.com/offer",
        },
    }
    if capture_id is not None:
        watched_ad["capture_id"] = capture_id
    return watched_ad


@pytest.mark.asyncio
async def test_persist_ad_captures_resyncs_positions_and_prunes_removed_rows() -> None:
    uow = _FakeUnitOfWork()
    service = CapturePersistenceService(uow)
    session_id = "android-session-sync"

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                capture_id="ad-old-1",
                headline_text="Old first ad",
                video_file="artifacts/probe/old-first.mp4",
                screenshot_path="artifacts/probe/old-first.png",
            ),
            _watched_ad(
                position=2,
                capture_id="ad-old-2",
                headline_text="Old second ad",
                video_file="artifacts/probe/old-second.mp4",
                screenshot_path="artifacts/probe/old-second.png",
            ),
        ],
    )

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                capture_id="ad-final-2",
                headline_text="Final deduped ad",
                video_file="artifacts/probe/final-focused.mp4",
                screenshot_path="artifacts/probe/final-focused.png",
                landing_dir="android_landing/ad-final-2",
                landing_status=LandingStatus.COMPLETED,
                analysis_status=AnalysisStatus.COMPLETED,
                analysis_summary={"result": "relevant", "reason": "matched"},
            ),
        ],
        from_index=0,
        prune_missing=True,
    )

    captures = await uow.ad_captures.get_by_session(session_id)

    assert len(captures) == 1
    assert captures[0].ad_position == 1
    assert captures[0].headline_text == "Final deduped ad"
    assert captures[0].video_file == "artifacts/probe/final-focused.mp4"
    assert captures[0].landing_dir == "android_landing/ad-final-2"
    assert captures[0].landing_status == LandingStatus.COMPLETED
    assert captures[0].analysis_status == AnalysisStatus.COMPLETED
    assert json.loads(captures[0].analysis_summary or "{}") == {
        "result": "relevant",
        "reason": "matched",
    }
    assert [(shot.offset_ms, shot.file_path) for shot in captures[0].screenshots] == [
        (0, "artifacts/probe/final-focused.png"),
    ]
    assert uow.commits == 2


@pytest.mark.asyncio
async def test_persist_ad_captures_does_not_overwrite_terminal_analysis_with_pending_payload() -> None:
    uow = _FakeUnitOfWork()
    service = CapturePersistenceService(uow)
    session_id = "android-session-analysis"

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                capture_id="ad-1",
                headline_text="Analyzed ad",
                video_file="artifacts/probe/analyzed.mp4",
                screenshot_path="artifacts/probe/analyzed.png",
                analysis_status=AnalysisStatus.NOT_RELEVANT,
                analysis_summary={"result": "not_relevant", "reason": "generic"},
            ),
        ],
    )

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                capture_id="ad-1",
                headline_text="Analyzed ad",
                video_file="artifacts/probe/analyzed.mp4",
                screenshot_path="artifacts/probe/analyzed.png",
                analysis_status=AnalysisStatus.PENDING,
                analysis_summary=None,
            ),
        ],
        from_index=0,
    )

    captures = await uow.ad_captures.get_by_session(session_id)

    assert len(captures) == 1
    assert captures[0].analysis_status == AnalysisStatus.NOT_RELEVANT
    assert json.loads(captures[0].analysis_summary or "{}") == {
        "result": "not_relevant",
        "reason": "generic",
    }


@pytest.mark.asyncio
async def test_persist_ad_captures_supports_android_payloads_without_capture_id() -> None:
    uow = _FakeUnitOfWork()
    service = CapturePersistenceService(uow)
    session_id = "android-session-no-capture-id"

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                headline_text="Android trading ad",
                video_file="android_probe/trading.mp4",
                screenshot_path="android_probe/trading.png",
                landing_dir="android_landing/trading-ad",
                landing_status=LandingStatus.COMPLETED,
                analysis_status=AnalysisStatus.COMPLETED,
                analysis_summary={"result": "relevant", "reason": "matched"},
            ),
        ],
    )

    captures = await uow.ad_captures.get_by_session(session_id)

    assert len(captures) == 1
    assert captures[0].ad_position == 1
    assert captures[0].headline_text == "Android trading ad"
    assert captures[0].video_file == "android_probe/trading.mp4"
    assert captures[0].landing_dir == "android_landing/trading-ad"
    assert captures[0].landing_status == LandingStatus.COMPLETED
    assert captures[0].analysis_status == AnalysisStatus.COMPLETED
    assert json.loads(captures[0].analysis_summary or "{}") == {
        "result": "relevant",
        "reason": "matched",
    }
    assert [(shot.offset_ms, shot.file_path) for shot in captures[0].screenshots] == [
        (0, "android_probe/trading.png"),
    ]


@pytest.mark.asyncio
async def test_persist_ad_captures_supports_json_round_tripped_screenshot_paths() -> None:
    uow = _FakeUnitOfWork()
    service = CapturePersistenceService(uow)
    session_id = "android-session-json-screenshots"

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                headline_text="JSON screenshot ad",
                video_file="android_probe/json.mp4",
                screenshot_path="android_probe/json.png",
                screenshot_paths=[
                    {"offset_ms": 0, "file_path": "android_probe/json.png"},
                    {"offset_ms": 1200, "file_path": "android_probe/json-2.png"},
                ],
            ),
        ],
    )

    captures = await uow.ad_captures.get_by_session(session_id)

    assert len(captures) == 1
    assert [(shot.offset_ms, shot.file_path) for shot in captures[0].screenshots] == [
        (0, "android_probe/json.png"),
        (1200, "android_probe/json-2.png"),
    ]


@pytest.mark.asyncio
async def test_persist_ad_captures_collapses_existing_duplicate_positions() -> None:
    uow = _FakeUnitOfWork()
    service = CapturePersistenceService(uow)
    session_id = "android-session-duplicate-positions"

    older = AdCapture(
        id=uuid.uuid4(),
        session_id=session_id,
        ad_position=1,
        headline_text="Older stale row",
        analysis_status=AnalysisStatus.PENDING,
        created_at=datetime.datetime(2026, 4, 17, 12, 4, tzinfo=datetime.UTC),
        updated_at=datetime.datetime(2026, 4, 17, 12, 4, tzinfo=datetime.UTC),
    )
    newer = AdCapture(
        id=uuid.uuid4(),
        session_id=session_id,
        ad_position=1,
        headline_text="Newer canonical row",
        analysis_status=AnalysisStatus.NOT_RELEVANT,
        created_at=datetime.datetime(2026, 4, 17, 12, 8, tzinfo=datetime.UTC),
        updated_at=datetime.datetime(2026, 4, 17, 12, 8, tzinfo=datetime.UTC),
    )
    older.screenshots = []
    newer.screenshots = []
    uow.ad_captures._captures.extend([older, newer])

    await service.persist_ad_captures(
        session_id=session_id,
        watched_ads=[
            _watched_ad(
                position=1,
                headline_text="Final row",
                video_file="artifacts/probe/final.mp4",
                screenshot_path="artifacts/probe/final.png",
                analysis_status=AnalysisStatus.NOT_RELEVANT,
            ),
        ],
        from_index=0,
    )

    captures = await uow.ad_captures.get_raw_by_session(session_id)

    assert len(captures) == 1
    assert captures[0].id == newer.id
    assert captures[0].headline_text == "Final row"
