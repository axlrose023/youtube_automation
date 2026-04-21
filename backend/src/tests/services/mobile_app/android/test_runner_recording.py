from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.mobile_app.android.runner as runner_module
from app.services.mobile_app.android.runner import AndroidYouTubeProbeRunner


@pytest.mark.asyncio
async def test_start_recording_handle_creates_video_recorder_with_expected_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[str, object | None]] = []

    class FakeRecorder:
        def __init__(self, *, adb_serial: str, artifacts_dir, bitrate: str | None = None) -> None:
            assert adb_serial == "emulator-5554"
            assert artifacts_dir == tmp_path / "android_probe" / "video"
            assert bitrate == "6M"

        async def start(self, *, artifact_prefix: str):
            started.append((artifact_prefix, None))
            return SimpleNamespace(local_path=tmp_path / "android_probe" / "video" / "clip.mp4")

    monkeypatch.setattr(runner_module, "AndroidScreenRecorder", FakeRecorder)

    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    runner._config = SimpleNamespace(
        storage=SimpleNamespace(base_path=tmp_path),
        android_app=SimpleNamespace(
            probe_screenrecord_artifacts_subdir="android_probe/video",
            probe_screenrecord_bitrate="6M",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_build_safe_artifact_prefix",
        lambda topic: topic.replace(" ", "_"),
    )

    recorder, handle = await runner._start_recording_handle(
        label="midroll",
        topic="forex investing",
        adb_serial="emulator-5554",
        round_index=1,
    )

    assert recorder is not None
    assert getattr(handle, "local_path", None) == (
        tmp_path / "android_probe" / "video" / "clip.mp4"
    )
    assert started == [("forex_investing", None)]


def test_recorder_wait_cap_tightens_untrusted_debug_xml_duration() -> None:
    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    samples = [SimpleNamespace(ad_detected=True, ad_progress_seconds=None, ad_duration_seconds=None)]

    cap_seconds = runner._recorder_wait_cap_seconds(
        clicked=True,
        remaining_source="debug_xml",
        debug_duration=285.0,
        samples=samples,
    )

    assert cap_seconds == 12.0


def test_recorder_wait_cap_keeps_standard_limit_for_sample_backed_ad_timing() -> None:
    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    samples = [SimpleNamespace(ad_detected=True, ad_progress_seconds=17.0, ad_duration_seconds=285.0)]

    cap_seconds = runner._recorder_wait_cap_seconds(
        clicked=True,
        remaining_source="debug_xml",
        debug_duration=285.0,
        samples=samples,
    )

    assert cap_seconds == 45.0
