from __future__ import annotations

from app.services.mobile_app.android.result_payloads import (
    build_topic_watched_video_payload,
)
from app.services.mobile_app.models import AndroidSessionTopicResult


def test_build_topic_watched_video_payload_preserves_target_watch_seconds() -> None:
    payload = build_topic_watched_video_payload(
        AndroidSessionTopicResult(
            topic="quantum ai trading",
            opened_title="Pocket Broker review",
            watch_verified=True,
            watch_seconds=57.0,
            target_watch_seconds=104.4,
        ),
        position=1,
        recorded_at=123.0,
    )

    assert payload["watched_seconds"] == 57.0
    assert payload["target_seconds"] == 104.4
    assert payload["watch_ratio"] == 0.546
    assert payload["completed"] is True
    assert payload["recorded_at"] == 123.0


def test_build_topic_watched_video_payload_falls_back_to_watched_seconds_when_target_missing() -> None:
    payload = build_topic_watched_video_payload(
        AndroidSessionTopicResult(
            topic="forex trading strategy 2026",
            watch_verified=True,
            watch_seconds=123.0,
            target_watch_seconds=None,
        ),
        position=2,
        recorded_at=456.0,
    )

    assert payload["watched_seconds"] == 123.0
    assert payload["target_seconds"] == 123.0
    assert payload["watch_ratio"] == 1.0
    assert payload["recorded_at"] == 456.0
