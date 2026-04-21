from __future__ import annotations

from app.services.mobile_app.android.analysis import _build_ad_video_focus_window


def test_build_ad_video_focus_window_for_preroll_prefers_head_clip() -> None:
    window = _build_ad_video_focus_window(
        first_ad_offset_seconds=0.0,
        watched_seconds=9.0,
        ad_duration_seconds=57.0,
        recorded_video_duration_seconds=178.7,
    )

    assert window == (0.0, 30.0)


def test_build_ad_video_focus_window_for_late_ad_clamps_to_remaining_video() -> None:
    window = _build_ad_video_focus_window(
        first_ad_offset_seconds=25.0,
        watched_seconds=25.0,
        ad_duration_seconds=81.0,
        recorded_video_duration_seconds=47.1,
    )

    assert window == (23.0, 24.1)


def test_build_ad_video_focus_window_returns_none_without_ad_offset() -> None:
    window = _build_ad_video_focus_window(
        first_ad_offset_seconds=None,
        watched_seconds=12.0,
        ad_duration_seconds=33.0,
        recorded_video_duration_seconds=42.0,
    )

    assert window is None
