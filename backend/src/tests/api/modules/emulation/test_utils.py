from __future__ import annotations

from types import SimpleNamespace

import app.api.modules.emulation.utils as emulation_utils
from app.api.modules.emulation.models import (
    AdCapture,
    AdCaptureScreenshot,
    AnalysisStatus,
    LandingStatus,
    VideoStatus,
)
from app.api.modules.emulation.utils import (
    map_ad_capture,
    normalize_media_reference,
    normalize_screenshot_paths,
)
from app.settings import get_config


def test_normalize_media_reference_relativizes_storage_paths() -> None:
    storage_base = get_config().storage.base_path
    absolute = storage_base / "artifacts" / "android" / "landing.png"

    assert normalize_media_reference(str(absolute)) == "artifacts/android/landing.png"


def test_normalize_screenshot_paths_relativizes_absolute_entries() -> None:
    storage_base = get_config().storage.base_path
    absolute = storage_base / "artifacts" / "android" / "ad.png"

    assert normalize_screenshot_paths([(1500, str(absolute))]) == [
        {
            "offset_ms": 1500,
            "file_path": "artifacts/android/ad.png",
        }
    ]


def test_normalize_media_reference_relativizes_host_storage_alias(
    tmp_path,
    monkeypatch,
) -> None:
    storage_base = tmp_path / "artifacts"
    screenshot_path = storage_base / "android_probe" / "ad.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path.write_bytes(b"png")

    monkeypatch.setattr(
        emulation_utils,
        "get_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(
                base_path=storage_base,
                ad_captures_path=storage_base / "ad_captures",
            )
        ),
    )

    assert emulation_utils.normalize_media_reference(
        "/opt/youtube_automation/artifacts/android_probe/ad.png"
    ) == "android_probe/ad.png"
    assert emulation_utils.resolve_media_path(
        "/opt/youtube_automation/artifacts/android_probe/ad.png"
    ) == screenshot_path.resolve()


def test_map_ad_capture_preserves_media_for_not_relevant_ads() -> None:
    capture = AdCapture(
        session_id="session-1",
        ad_position=1,
        video_file="android_probe/video/ad.mp4",
        video_status=VideoStatus.COMPLETED,
        landing_url="https://example.com/landing",
        landing_dir="android_landing/session-1",
        landing_status=LandingStatus.COMPLETED,
        analysis_status=AnalysisStatus.NOT_RELEVANT,
        analysis_summary='{"result":"not_relevant","reason":"generic"}',
    )
    capture.screenshots = [
        AdCaptureScreenshot(offset_ms=250, file_path="android_probe/ad.png"),
    ]

    mapped = map_ad_capture(capture)

    assert mapped.video_file == "android_probe/video/ad.mp4"
    assert mapped.landing_url == "https://example.com/landing"
    assert mapped.landing_dir == "android_landing/session-1"
    assert mapped.screenshot_paths[0].file_path == "android_probe/ad.png"
    assert mapped.analysis_status == AnalysisStatus.NOT_RELEVANT
