from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.services.mobile_app.android.runner as runner_module
from app.services.mobile_app.android.errors import AndroidUiError
from app.services.mobile_app.android.runner import (
    AndroidYouTubeProbeRunner,
    AndroidYouTubeSessionRunner,
)
from app.services.mobile_app.models import AndroidSessionTopicResult, AndroidWatchSample
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
        async def watch_current(
            self,
            *,
            watch_seconds: int,
            sample_interval_seconds: int | None = None,
            deadline: float | None = None,
        ):
            if watch_seconds == 2:
                assert sample_interval_seconds == 1
                assert deadline is None
                return AndroidWatchResult(
                    verified=False,
                    samples=[
                        AndroidWatchSample(
                            offset_seconds=2,
                            player_visible=True,
                            watch_panel_visible=True,
                        )
                    ],
                )
            assert watch_seconds == 12
            assert sample_interval_seconds == 2
            assert isinstance(deadline, float)
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


@pytest.mark.asyncio
async def test_continue_main_watch_marks_merged_stable_dwell_verified() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(probe_watch_sample_interval_seconds=1)
    )

    class FakeWatcher:
        async def watch_current(
            self,
            *,
            watch_seconds: int,
            sample_interval_seconds: int | None = None,
            deadline: float | None = None,
        ):
            if watch_seconds == 2:
                assert sample_interval_seconds == 1
                return AndroidWatchResult(
                    verified=False,
                    samples=[
                        AndroidWatchSample(
                            offset_seconds=2,
                            player_visible=True,
                            watch_panel_visible=True,
                        )
                    ],
                )
            assert watch_seconds == 12
            return AndroidWatchResult(
                verified=False,
                samples=[
                    AndroidWatchSample(
                        offset_seconds=0,
                        player_visible=True,
                        watch_panel_visible=True,
                    ),
                    AndroidWatchSample(
                        offset_seconds=12,
                        player_visible=True,
                        watch_panel_visible=True,
                    ),
                ],
            )

    watch_result = AndroidWatchResult(
        verified=False,
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                player_visible=True,
                watch_panel_visible=True,
            ),
            AndroidWatchSample(
                offset_seconds=6,
                player_visible=True,
                watch_panel_visible=True,
            ),
        ],
    )

    next_result, note, extension_result = await runner._continue_main_watch_if_needed(
        watcher=FakeWatcher(),
        watch_result=watch_result,
        target_watch_seconds=18.0,
    )

    assert next_result.verified is True
    assert note == "main_watch_extended:12"
    assert extension_result is not None


@pytest.mark.asyncio
async def test_continue_main_watch_late_probe_catches_missed_sponsored_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(probe_watch_sample_interval_seconds=1)
    )
    calls: list[int] = []

    async def fake_wait_for(awaitable, timeout):
        return await awaitable

    class FakeWatcher:
        async def watch_current(
            self,
            *,
            watch_seconds: int,
            sample_interval_seconds: int | None = None,
            deadline: float | None = None,
        ):
            calls.append(watch_seconds)
            if watch_seconds == 2:
                return AndroidWatchResult(
                    verified=True,
                    samples=[
                        AndroidWatchSample(
                            offset_seconds=0,
                            player_visible=True,
                            watch_panel_visible=True,
                            ad_detected=True,
                            ad_sponsor_label="Sponsored",
                            ad_display_url="becyprus.com",
                            ad_cta_text="Book now",
                        )
                    ],
                    ad_debug_page_source="<hierarchy />",
                )
            return AndroidWatchResult(
                verified=False,
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
        verified=False,
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

    assert calls == [12, 2]
    assert note == "main_watch_ad_detected:12"
    assert extension_result is not None
    assert any(sample.ad_detected for sample in next_result.samples)
    assert next_result.ad_debug_page_source == "<hierarchy />"


@pytest.mark.asyncio
async def test_recover_open_result_timeout_surface_promotes_existing_watch_surface() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    class FakeNavigator:
        async def promote_current_watch_surface(self, query, *, deadline=None):
            assert query == "quantum ai trading"
            assert isinstance(deadline, float)
            return "Quantum Review: AI-Powered Trading Platform"

    recovered = await runner._recover_open_result_timeout_surface(
        navigator=FakeNavigator(),
        topic="quantum ai trading",
        topic_notes=topic_notes,
        stage_label="open_first_result",
    )

    assert recovered == "Quantum Review: AI-Powered Trading Platform"
    assert "open_first_result_timeout_recovered:watch_surface" in topic_notes


@pytest.mark.asyncio
async def test_reset_to_home_with_timeout_treats_launcher_recovery_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    class FakeNavigator:
        async def reset_to_home(self, *, deadline=None):
            raise AndroidUiError("hard deadline exceeded while resetting")

    async def fake_recover(**kwargs):
        return True

    monkeypatch.setattr(
        runner,
        "_recover_from_launcher_anr_with_timeout",
        fake_recover,
        raising=False,
    )

    await runner._reset_to_home_with_timeout(
        navigator=FakeNavigator(),
        topic_notes=topic_notes,
        stage_label="topic_post_reset",
        launcher_tripwire=None,
        timeout_seconds=1.0,
    )

    assert topic_notes == [
        "topic_post_reset_timeout",
        "topic_post_reset_launcher_recovery:true",
    ]


@pytest.mark.asyncio
async def test_open_first_result_with_timeout_cancels_active_sync_operation_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []
    cancel_calls: list[str] = []

    class FakeNavigator:
        async def open_first_result(self, query, *, deadline=None):
            await asyncio.sleep(60)

        async def cancel_active_sync_operation(self) -> None:
            cancel_calls.append("cancelled")

    async def fake_drain_cancelled_stage_task(**kwargs):
        return True

    async def fake_recover_open_result_timeout_surface(**kwargs):
        return None

    async def fake_recover_from_launcher_anr_with_timeout(**kwargs):
        return False

    monkeypatch.setattr(
        runner,
        "_drain_cancelled_stage_task",
        fake_drain_cancelled_stage_task,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_recover_open_result_timeout_surface",
        fake_recover_open_result_timeout_surface,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_recover_from_launcher_anr_with_timeout",
        fake_recover_from_launcher_anr_with_timeout,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_raise_if_launcher_tripwire_set",
        lambda **kwargs: None,
        raising=False,
    )

    opened = await runner._open_first_result_with_timeout(
        navigator=FakeNavigator(),
        topic="quantum ai trading",
        topic_notes=topic_notes,
        stage_label="open_first_result",
        launcher_tripwire=None,
        timeout_seconds=0.01,
    )

    assert opened is None
    assert cancel_calls == ["cancelled"]
    assert "open_first_result_timeout" in topic_notes


@pytest.mark.asyncio
async def test_await_current_watch_title_with_hard_timeout_downgrades_deadline_error() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    class FakeNavigator:
        async def await_current_watch_title(self, *args, **kwargs):
            raise AndroidUiError("sync operation hard deadline exceeded")

    title = await runner._await_current_watch_title_with_hard_timeout(
        navigator=FakeNavigator(),
        topic="quantum ai trading",
        topic_notes=topic_notes,
        stage_label="opened_title_after_wait_for_results_delay",
        timeout_seconds=2.0,
    )

    assert title is None
    assert "opened_title_after_wait_for_results_delay_timeout" in topic_notes


@pytest.mark.asyncio
async def test_provisional_watch_title_with_hard_timeout_downgrades_deadline_error() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    class FakeNavigator:
        async def provisional_watch_title(self, *args, **kwargs):
            raise AndroidUiError("sync operation hard deadline exceeded")

    title = await runner._provisional_watch_title_with_hard_timeout(
        navigator=FakeNavigator(),
        topic="quantum ai trading",
        topic_notes=topic_notes,
        stage_label="opened_title_after_wait_for_results",
        timeout_seconds=2.0,
    )

    assert title is None
    assert "opened_title_after_wait_for_results_timeout" in topic_notes


def test_opened_title_matches_topic_accepts_forex_strategy_without_literal_forex() -> None:
    assert (
        AndroidYouTubeSessionRunner._opened_title_matches_topic(
            "This Simple Trading Strategy got me 80% Win Rate",
            "forex trading strategy",
        )
        is True
    )


@pytest.mark.asyncio
async def test_reconcile_keeps_post_tap_non_results_candidate_without_watch_probe() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    topic_notes: list[str] = []

    class FakeNavigator:
        async def await_current_watch_title(self, *args, **kwargs):
            return None

        async def last_open_result_diagnostics(self):
            return {
                "reason": "post_tap_non_results_accepted_candidate",
                "candidate_title": "Unlocking AI Trading Secrets: A Step by Step Guide to Dominate Markets with Quantum AI",
            }

        async def has_watch_surface_for_query(self, *args, **kwargs):
            raise AssertionError("watch probe should not be needed for trusted post-tap fallback")

    opened_title = (
        "Unlocking AI Trading Secrets: A Step by Step Guide to Dominate Markets with Quantum AI"
    )

    reconciled = await runner._reconcile_opened_title_with_topic(
        navigator=FakeNavigator(),
        topic="quantum ai trading",
        opened_title=opened_title,
        topic_notes=topic_notes,
        attempt_label="first_attempt",
    )

    assert reconciled == opened_title
    assert "opened_title_kept:post_tap_non_results:first_attempt" in topic_notes


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


def test_next_topic_start_buffer_keeps_full_budget_before_first_topic() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(session_topic_start_buffer_seconds=180)
    )

    buffer_seconds = runner._next_topic_start_buffer_seconds(
        topics=["quantum ai trading", "forex trading strategy", "ai trading bot"],
        topic_results=[],
        topics_cycled_once=False,
    )

    assert buffer_seconds == 180.0


def test_next_topic_start_buffer_shrinks_after_progress() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(session_topic_start_buffer_seconds=180)
    )

    buffer_seconds = runner._next_topic_start_buffer_seconds(
        topics=["quantum ai trading", "forex trading strategy", "ai trading bot"],
        topic_results=[
            AndroidSessionTopicResult(topic="quantum ai trading", watch_verified=True)
        ],
        topics_cycled_once=False,
    )

    assert buffer_seconds == 105.0


def test_next_topic_start_buffer_allows_last_uncovered_topic() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(session_topic_start_buffer_seconds=180)
    )

    buffer_seconds = runner._next_topic_start_buffer_seconds(
        topics=["quantum ai trading", "forex trading strategy", "ai trading bot"],
        topic_results=[
            AndroidSessionTopicResult(topic="quantum ai trading", watch_verified=True),
            AndroidSessionTopicResult(topic="forex trading strategy", watch_verified=True),
        ],
        topics_cycled_once=False,
    )

    assert buffer_seconds == 75.0


def test_next_topic_start_buffer_uses_repeat_cycle_floor() -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    runner._config = SimpleNamespace(
        android_app=SimpleNamespace(session_topic_start_buffer_seconds=180)
    )

    buffer_seconds = runner._next_topic_start_buffer_seconds(
        topics=["quantum ai trading", "forex trading strategy", "ai trading bot"],
        topic_results=[
            AndroidSessionTopicResult(topic="quantum ai trading", watch_verified=True)
        ],
        topics_cycled_once=True,
    )

    assert buffer_seconds == 60.0


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


def test_samples_have_video_ad_signal_for_skippable_app_ad() -> None:
    samples = [
        AndroidWatchSample(
            offset_seconds=0,
            ad_detected=True,
            skip_available=True,
            ad_visible_lines=[
                "Sponsored",
                "Math Makers: Kids School Games",
                "Google Play",
                "Install",
            ],
        )
    ]

    assert AndroidYouTubeSessionRunner._samples_have_video_ad_signal(samples) is True


def test_samples_have_video_ad_signal_rejects_plain_banner_overlay() -> None:
    samples = [
        AndroidWatchSample(
            offset_seconds=0,
            ad_detected=True,
            ad_display_url="Finance.ua",
            ad_visible_lines=["Sponsored", "Finance.ua"],
        )
    ]

    assert AndroidYouTubeSessionRunner._samples_have_video_ad_signal(samples) is False


def test_result_is_app_install_video_ad_requires_video_signal() -> None:
    result = AndroidWatchResult(
        verified=True,
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                skip_available=True,
                ad_visible_lines=[
                    "Sponsored",
                    "Credit7 кредит онлайн на карту",
                    "Google Play",
                    "Install",
                ],
            )
        ],
    )

    assert AndroidYouTubeSessionRunner._result_is_app_install_video_ad(result) is True


def test_result_is_app_install_video_ad_detects_install_cta_without_google_play() -> None:
    result = AndroidWatchResult(
        verified=True,
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                skip_available=True,
                ad_cta_text="Install",
                ad_visible_lines=["Sponsored", "XTrend Speed Trading App"],
            )
        ],
    )

    assert AndroidYouTubeSessionRunner._result_is_app_install_video_ad(result) is True


def test_result_is_app_install_video_ad_rejects_banner_without_video_signal() -> None:
    result = AndroidWatchResult(
        verified=True,
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_visible_lines=[
                    "Sponsored",
                    "Credit7 кредит онлайн на карту",
                    "Google Play",
                    "Install",
                ],
            )
        ],
    )

    assert AndroidYouTubeSessionRunner._result_is_app_install_video_ad(result) is False


def test_ad_trim_window_uses_detected_midroll_offset() -> None:
    window = AndroidYouTubeSessionRunner._calculate_ad_trim_window(
        recorded_duration_seconds=148.936,
        ad_seconds=52.0,
        ad_detected_after_watch_seconds=96.0,
    )

    assert window == pytest.approx((97.5, 51.436), abs=0.01)


def test_ad_trim_window_falls_back_to_tail_when_detection_is_missing() -> None:
    window = AndroidYouTubeSessionRunner._calculate_ad_trim_window(
        recorded_duration_seconds=148.936,
        ad_seconds=52.0,
        ad_detected_after_watch_seconds=None,
    )

    assert window == pytest.approx((94.936, 54.0), abs=0.01)


def test_ad_trim_window_ignores_detected_offset_that_would_overcut_ad() -> None:
    window = AndroidYouTubeSessionRunner._calculate_ad_trim_window(
        recorded_duration_seconds=60.0,
        ad_seconds=52.0,
        ad_detected_after_watch_seconds=55.0,
    )

    assert window == pytest.approx((6.0, 54.0), abs=0.01)


def test_ad_trim_window_keeps_head_when_ad_started_with_recording() -> None:
    window = AndroidYouTubeSessionRunner._calculate_ad_trim_window(
        recorded_duration_seconds=29.117,
        ad_seconds=5.0,
        ad_sample_start_seconds=0.0,
        ad_detected_after_watch_seconds=None,
    )

    assert window == pytest.approx((0.0, 7.0), abs=0.01)


def test_sample_based_recording_ad_window_uses_progress_to_find_true_start() -> None:
    samples = [
        AndroidWatchSample(
            offset_seconds=6,
            ad_detected=True,
            ad_progress_seconds=5,
            ad_duration_seconds=38,
        ),
        AndroidWatchSample(
            offset_seconds=12,
            ad_detected=True,
            ad_progress_seconds=11,
            ad_duration_seconds=38,
        ),
    ]

    window = AndroidYouTubeSessionRunner._sample_based_recording_ad_window_seconds(
        samples,
        recording_watch_start_offset_seconds=None,
        recorded_duration_seconds=55.9,
    )

    assert window == pytest.approx((0.5, 40.0), abs=0.01)


def test_ad_trim_window_prefers_sample_based_window_over_late_detected_offset() -> None:
    window = AndroidYouTubeSessionRunner._calculate_ad_trim_window(
        recorded_duration_seconds=55.9,
        ad_seconds=38.0,
        ad_sample_start_seconds=6.0,
        ad_detected_after_watch_seconds=12.0,
        sample_based_ad_start_seconds=0.5,
        sample_based_ad_end_seconds=40.0,
    )

    assert window == pytest.approx((0.5, 39.5), abs=0.01)


def test_sample_based_recording_ad_window_handles_preopen_recording_offset() -> None:
    samples = [
        AndroidWatchSample(
            offset_seconds=2,
            ad_detected=True,
            ad_progress_seconds=6,
            ad_duration_seconds=10,
        ),
        AndroidWatchSample(
            offset_seconds=7,
            ad_detected=True,
            ad_progress_seconds=8,
            ad_duration_seconds=10,
        ),
    ]

    window = AndroidYouTubeSessionRunner._sample_based_recording_ad_window_seconds(
        samples,
        recording_watch_start_offset_seconds=40.194,
        recorded_duration_seconds=48.994,
    )

    assert window == pytest.approx((35.694, 48.994), abs=0.01)


def test_main_watch_ad_detected_after_seconds_parses_numeric_note() -> None:
    assert (
        AndroidYouTubeSessionRunner._main_watch_ad_detected_after_seconds(
            "main_watch_ad_detected:120"
        )
        == 120.0
    )
    assert (
        AndroidYouTubeSessionRunner._main_watch_ad_detected_after_seconds(
            "main_watch_ad_detected:pending_residual"
        )
        is None
    )


@pytest.mark.asyncio
async def test_discard_recording_handle_deletes_duplicate_segment(tmp_path) -> None:
    runner = AndroidYouTubeSessionRunner.__new__(AndroidYouTubeSessionRunner)
    local_video = tmp_path / "duplicate.mp4"
    local_video.write_bytes(b"fake-video")

    class FakeRecorder:
        async def stop(self, handle, *, keep_local):
            assert handle == "handle"
            assert keep_local is True
            return local_video

    await runner._discard_recording_handle(
        label="midroll",
        topic="forex trading strategy",
        recorder=FakeRecorder(),
        recording_handle="handle",
        reason="duplicate_identity",
    )

    assert not local_video.exists()


def test_session_summary_counts_ad_debug_metrics() -> None:
    summary = AndroidYouTubeSessionRunner._build_session_summary(
        topic_results=[
            AndroidSessionTopicResult(
                topic="forex trading strategy",
                watch_verified=True,
                watch_seconds=30.0,
                target_watch_seconds=60.0,
                notes=[
                    "midroll_ad_skip_duplicate:round1:continuation",
                    "midroll_duplicate_recording_discarded:round1:continuation",
                    "midroll_duplicate_cap_break:round2:continuation",
                    "post_ad_residual_detected:returning_for_midroll",
                    "mixed_ad_identity_detected:round3",
                ],
            )
        ],
        watched_ads=[
            {
                "capture": {
                    "recorded_video_duration_seconds": 12.0,
                    "analysis_status": "failed",
                    "source_video_file": "android_probe/video/source.mp4",
                }
            }
        ],
        elapsed_seconds=100,
    )

    assert summary["midroll_duplicate_count"] == 1
    assert summary["midroll_duplicate_recording_discarded_count"] == 1
    assert summary["midroll_duplicate_break_count"] == 1
    assert summary["post_ad_residual_detected_count"] == 1
    assert summary["mixed_identity_detected_count"] == 1
    assert summary["focused_video_count"] == 1
    assert summary["analysis_failed_count"] == 1


def test_topic_surface_failed_before_watch_detects_dead_results_surface() -> None:
    assert AndroidYouTubeSessionRunner._topic_surface_failed_before_watch(
        topic_notes=[
            "submit_search_timeout",
            "wait_for_results_timeout",
            "open_first_result_skipped:no_results_surface",
            "no_result_opened",
        ],
        opened_title=None,
        watch_verified=False,
        watch_seconds=None,
        topic_watched_ads=[],
        current_watch_started_at=None,
    ) is True


def test_topic_surface_failed_before_watch_ignores_real_progress() -> None:
    assert AndroidYouTubeSessionRunner._topic_surface_failed_before_watch(
        topic_notes=["wait_for_results_timeout", "no_result_opened:first_attempt"],
        opened_title="Quantum Review: AI-Powered Trading Platform",
        watch_verified=False,
        watch_seconds=None,
        topic_watched_ads=[],
        current_watch_started_at=None,
    ) is False
    assert AndroidYouTubeSessionRunner._topic_surface_failed_before_watch(
        topic_notes=["no_result_opened:first_attempt"],
        opened_title=None,
        watch_verified=False,
        watch_seconds=None,
        topic_watched_ads=[{"title": "Sponsored"}],
        current_watch_started_at=None,
    ) is False
