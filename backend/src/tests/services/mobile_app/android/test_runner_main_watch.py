from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.mobile_app.android.runner as runner_module
from app.services.mobile_app.android.runner import (
    AndroidYouTubeProbeRunner,
    AndroidYouTubeSessionRunner,
)
from app.services.mobile_app.models import AndroidWatchSample
from app.services.mobile_app.android.youtube.watcher import AndroidWatchResult


@pytest.mark.asyncio
async def test_advance_main_watch_iteration_consumes_pending_midroll_without_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)

    async def fail_continue(**kwargs):
        raise AssertionError("should not call _continue_main_watch_if_needed")

    monkeypatch.setattr(
        runner,
        "_continue_main_watch_if_needed",
        fail_continue,
        raising=False,
    )

    watch_result = SimpleNamespace(
        verified=True,
        samples=[
            AndroidWatchSample(
                offset_seconds=13,
                player_visible=True,
                watch_panel_visible=True,
            )
        ],
    )
    pending_midroll = SimpleNamespace(
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_progress_seconds=55,
                ad_duration_seconds=99,
            )
        ]
    )

    next_result, note, extension_result, sample_finished_at = (
        await runner._advance_main_watch_iteration(
            watcher=object(),
            watch_result=watch_result,
            target_watch_seconds=90.0,
            pending_midroll_result=pending_midroll,
            pending_midroll_samples_ended_monotonic=123.4,
        )
    )

    assert next_result is watch_result
    assert note == "main_watch_ad_detected:pending_residual"
    assert extension_result is pending_midroll
    assert sample_finished_at == 123.4


@pytest.mark.asyncio
async def test_advance_main_watch_iteration_uses_continue_when_no_pending_midroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    watch_result = SimpleNamespace(samples=[])
    continued_result = SimpleNamespace(samples=["merged"])
    monotonic_calls: list[float] = []

    async def fake_continue(**kwargs):
        assert kwargs["watch_result"] is watch_result
        assert kwargs["target_watch_seconds"] == 90.0
        return continued_result, "main_watch_extended:24", SimpleNamespace(samples=["extra"])

    monkeypatch.setattr(
        runner,
        "_continue_main_watch_if_needed",
        fake_continue,
        raising=False,
    )
    monkeypatch.setattr(
        runner_module.time,
        "monotonic",
        lambda: monotonic_calls.append(456.7) or 456.7,
    )

    next_result, note, extension_result, sample_finished_at = (
        await runner._advance_main_watch_iteration(
            watcher=object(),
            watch_result=watch_result,
            target_watch_seconds=90.0,
            pending_midroll_result=None,
            pending_midroll_samples_ended_monotonic=None,
        )
    )

    assert next_result is continued_result
    assert note == "main_watch_extended:24"
    assert extension_result.samples == ["extra"]
    assert sample_finished_at == 456.7
    assert monotonic_calls == [456.7]


@pytest.mark.asyncio
async def test_continue_main_watch_uses_timeout_above_watcher_internal_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(probe_watch_sample_interval_seconds=1)
    )
    wait_for_timeouts: list[float] = []

    async def fake_wait_for(awaitable, timeout):
        wait_for_timeouts.append(timeout)
        return await awaitable

    class FakeWatcher:
        async def watch_current(self, *, watch_seconds: int):
            assert watch_seconds == 12
            return AndroidWatchResult(
                verified=True,
                samples=[
                    AndroidWatchSample(
                        offset_seconds=12,
                        player_visible=True,
                        watch_panel_visible=True,
                    )
                ],
            )

    monkeypatch.setattr(runner_module.asyncio, "wait_for", fake_wait_for)

    watch_result = AndroidWatchResult(
        verified=True,
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                player_visible=True,
                watch_panel_visible=True,
            )
        ],
    )

    next_result, note, extension_result = await runner._continue_main_watch_if_needed(
        watcher=FakeWatcher(),
        watch_result=watch_result,
        target_watch_seconds=12.0,
    )

    assert wait_for_timeouts == [52.0]
    assert note == "main_watch_extended:12"
    assert extension_result is not None
    assert next_result.samples[-1].offset_seconds == 13


def test_topic_has_meaningful_progress_for_opened_title() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)

    has_progress = runner._topic_has_meaningful_progress(
        opened_title="Forex Signals Live",
        watch_verified=False,
        watch_seconds=None,
        topic_watched_ads=[],
        current_watch_started_at=None,
    )

    assert has_progress is True


def test_should_retry_topic_attempt_skips_when_topic_already_expensive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    monotonic_values = iter([80.0, 80.0])

    monkeypatch.setattr(runner_module.time, "monotonic", lambda: next(monotonic_values))

    should_retry = runner._should_retry_topic_attempt(
        topic_started_at=0.0,
        session_started_at=0.0,
        duration_minutes=15,
    )

    assert should_retry is False


def test_should_retry_topic_attempt_skips_when_session_nearly_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    monotonic_values = iter([20.0, 190.0])

    monkeypatch.setattr(runner_module.time, "monotonic", lambda: next(monotonic_values))

    should_retry = runner._should_retry_topic_attempt(
        topic_started_at=0.0,
        session_started_at=0.0,
        duration_minutes=5,
    )

    assert should_retry is False


@pytest.mark.asyncio
async def test_ensure_topic_network_ready_soft_proceeds_after_failed_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    monkeypatch.setattr(
        runner,
        "_check_emulator_network_sync",
        lambda adb_serial: False,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_recover_emulator_network_sync",
        lambda adb_serial: False,
        raising=False,
    )

    ready = await runner._ensure_topic_network_ready(
        adb_serial="emulator-5554",
        topic="forex trading strategy",
        topic_notes=topic_notes,
    )

    assert ready is False
    assert topic_notes == [
        "network_check_failed:first_probe",
        "network_check_recovered:false",
    ]


@pytest.mark.asyncio
async def test_ensure_topic_network_ready_recovers_and_allows_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    monkeypatch.setattr(
        runner,
        "_check_emulator_network_sync",
        lambda adb_serial: False,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_recover_emulator_network_sync",
        lambda adb_serial: True,
        raising=False,
    )

    ready = await runner._ensure_topic_network_ready(
        adb_serial="emulator-5554",
        topic="quantum ai trading",
        topic_notes=topic_notes,
    )

    assert ready is True
    assert topic_notes == [
        "network_check_failed:first_probe",
        "network_check_recovered:true",
    ]


def test_midroll_continuation_detects_same_ad_progressing_forward() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    previous_ad = {
        "ad_last_progress_seconds": 17.0,
        "ad_duration_seconds": 87.0,
    }
    extension_samples = [
        AndroidWatchSample(
            offset_seconds=0,
            ad_detected=True,
            ad_progress_seconds=42,
            ad_duration_seconds=87,
        ),
        AndroidWatchSample(
            offset_seconds=1,
            ad_detected=True,
            ad_progress_seconds=47,
            ad_duration_seconds=87,
        ),
    ]

    assert runner._midroll_continues_previous_ad(
        previous_ad=previous_ad,
        extension_samples=extension_samples,
    ) is True

    runner._merge_midroll_continuation_into_previous_ad(
        previous_ad=previous_ad,
        extension_samples=extension_samples,
    )

    assert previous_ad["ad_last_progress_seconds"] == 47.0
    assert previous_ad.get("completed") is not True


def test_midroll_continuation_rejects_next_ad_with_new_duration() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    previous_ad = {
        "ad_last_progress_seconds": 76.0,
        "ad_duration_seconds": 87.0,
    }
    extension_samples = [
        AndroidWatchSample(
            offset_seconds=0,
            ad_detected=True,
            ad_progress_seconds=8,
            ad_duration_seconds=77,
        ),
    ]

    assert runner._midroll_continues_previous_ad(
        previous_ad=previous_ad,
        extension_samples=extension_samples,
    ) is False
