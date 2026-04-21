from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.services.mobile_app.android.runner import AndroidYouTubeProbeRunner
from app.services.mobile_app.models import AndroidWatchSample


def test_cleanup_irrelevant_ad_videos_keeps_debug_artifacts(tmp_path) -> None:
    video_path = tmp_path / "android_probe" / "video" / "watch_debug.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")

    landing_dir = tmp_path / "android_landing" / "session-1"
    landing_dir.mkdir(parents=True)
    (landing_dir / "index.html").write_text("<html></html>")

    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    runner._config = SimpleNamespace(storage=SimpleNamespace(base_path=tmp_path))

    watched_ads = [
        {
            "analysis_summary": {"result": "not_relevant"},
            "video_file": "android_probe/video/watch_debug.mp4",
            "landing_scrape_dir": "android_landing/session-1",
            "landing_dir": "android_landing/session-1",
            "capture": {
                "analysis_status": "not_relevant",
                "video_file": "android_probe/video/watch_debug.mp4",
                "landing_scrape_dir": "android_landing/session-1",
                "landing_dir": "android_landing/session-1",
            },
        }
    ]

    runner._cleanup_irrelevant_ad_videos(watched_ads)

    assert video_path.exists()
    assert landing_dir.exists()
    assert watched_ads[0]["video_file"] == "android_probe/video/watch_debug.mp4"
    assert watched_ads[0]["landing_dir"] == "android_landing/session-1"
    assert watched_ads[0]["capture"]["video_file"] == "android_probe/video/watch_debug.mp4"
    assert watched_ads[0]["capture"]["landing_dir"] == "android_landing/session-1"


@pytest.mark.asyncio
async def test_finalize_recording_after_cta_uses_debug_xml_remaining_fallback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml_path = tmp_path / "watch.xml"
    xml_path.write_text(
        """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <android.widget.TextView text="Sponsored" />
  <android.widget.SeekBar content-desc="0 minutes 3 seconds of 0 minutes 22 seconds" />
  <android.widget.TextView text="Visit advertiser" />
</hierarchy>
""",
        encoding="utf-8",
    )

    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    runner._config = SimpleNamespace(
        storage=SimpleNamespace(base_path=tmp_path),
        android_app=SimpleNamespace(probe_watch_sample_interval_seconds=4),
    )

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def fake_stop_recording_handle(*, recorder, recording_handle):
        return "android_probe/video/ad.mp4"

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(runner, "_stop_recording_handle", fake_stop_recording_handle)
    monkeypatch.setattr(runner, "_probe_recorded_video_duration", lambda _: 21.7)

    path, duration = await runner._finalize_recording_after_cta(
        label="test",
        recorder=object(),
        recording_handle=SimpleNamespace(started_monotonic=time.monotonic()),
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_cta_text="Visit advertiser",
            )
        ],
        debug_page_source_path=xml_path,
        clicked=False,
        returned_to_youtube=True,
    )

    assert slept == [19.0]
    assert path == "android_probe/video/ad.mp4"
    assert duration == 21.7
