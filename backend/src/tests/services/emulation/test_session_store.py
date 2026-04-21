from __future__ import annotations

from app.services.emulation.session.store import _merge_live_capture_analysis


def test_merge_live_capture_analysis_preserves_media_for_not_relevant_ads() -> None:
    current_ads = [
        {
            "position": 1,
            "capture": {
                "analysis_status": "not_relevant",
                "analysis_summary": {"result": "not_relevant", "reason": "generic"},
                "video_file": "android_probe/video/ad.mp4",
                "landing_url": "https://example.com/landing",
                "landing_dir": "android_landing/session-1",
                "screenshot_paths": [
                    {"offset_ms": 0, "file_path": "android_probe/ad.png"},
                ],
            },
        }
    ]
    next_ads = [
        {
            "position": 1,
            "capture": {
                "analysis_status": "pending",
                "video_file": None,
                "landing_url": None,
                "landing_dir": None,
                "screenshot_paths": [],
            },
        }
    ]

    merged = _merge_live_capture_analysis(
        current_ads=current_ads,
        next_ads=next_ads,
    )

    capture = merged[0]["capture"]
    assert capture["analysis_status"] == "not_relevant"
    assert capture["analysis_summary"] == {"result": "not_relevant", "reason": "generic"}
    assert capture["video_file"] == "android_probe/video/ad.mp4"
    assert capture["landing_url"] == "https://example.com/landing"
    assert capture["landing_dir"] == "android_landing/session-1"
    assert capture["screenshot_paths"] == [
        {"offset_ms": 0, "file_path": "android_probe/ad.png"},
    ]
