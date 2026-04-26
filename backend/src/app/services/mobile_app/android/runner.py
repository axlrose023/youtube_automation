from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import json
import logging
import math
import re
import subprocess
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
import random
import time

logger = logging.getLogger(__name__)
_MAX_RELIABLE_AD_DURATION_SECONDS = 600.0
_MAX_FALLBACK_SEEKBAR_AD_DURATION_SECONDS = 300.0

from app.services.emulation.config import (
    COVERAGE_CAP_BUDGET_FRACTION,
    COVERAGE_CAP_DEFAULT,
    COVERAGE_CAP_MIN_S,
    COVERAGE_SEARCH_OVERHEAD_S,
    REALISM_MIN_WATCH_AFTER_COVERAGE_S,
    REALISM_MIN_WATCH_S,
    REALISM_MIN_WATCH_TRIGGER_REMAINING_S,
    REALISM_MULTI_TOPIC_BUDGET_FRACTION,
    REALISM_MULTI_TOPIC_MAX_WATCH_S,
    REALISM_MULTI_TOPIC_MIN_WATCH_S,
    TOPIC_BALANCE_POST_COVERAGE_CAP_S,
    WATCH_LONG_FALLBACK,
)
from app.api.modules.emulation.models import VideoStatus
from app.services.mobile_app.models import (
    AndroidProbeResult,
    AndroidProbeSurfaceSnapshot,
    AndroidSessionRunResult,
    AndroidSessionTopicResult,
)
from app.settings import Config

from .analysis import AndroidAdAnalysisCoordinator, _build_ad_video_focus_window
from .landing_scraper import AndroidLandingPageScraper
from .result_payloads import build_topic_watched_video_payload
from .runtime import build_android_probe_runtime
from .avd_manager import AndroidEmulatorLaunchOptions
from .errors import AndroidUiError
from .proxy_bridge import AndroidHttpProxyBridge, AndroidHttpProxyBridgeHandle
from .screenrecord import AndroidScreenRecorder
from .tooling import build_android_runtime_env, require_tool_path
from ..profiles.adspower_client import AdsPowerProfileClient
from .youtube.ads import AndroidYouTubeAdInteractor
from .youtube.ad_record import _parse_debug_watch_metadata, build_watched_ad_record
from .youtube.engagement import AndroidYouTubeEngagementController
from .youtube.navigator import AndroidYouTubeNavigator
from .youtube.watcher import AndroidYouTubeWatcher


class AndroidYouTubeProbeRunner:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._runtime = build_android_probe_runtime(config.android_app)
        self._adspower_client = AdsPowerProfileClient(config.adspower)
        self._proxy_bridge = AndroidHttpProxyBridge()

    async def _prepare_emulator_proxy(
        self,
        *,
        proxy_url: str | None,
        adspower_profile_id: str | None,
    ) -> tuple[str | None, str | None, list[str], AndroidHttpProxyBridgeHandle | None]:
        """Returns (emulator_proxy_url, host_proxy_url, notes, bridge_handle).

        - emulator_proxy_url: HTTP proxy URL the Android emulator should use (via 10.0.2.2).
        - host_proxy_url: HTTP proxy URL for processes on the host (Playwright) — same upstream.
        """
        notes: list[str] = []
        resolved_proxy_url = (proxy_url or "").strip() or None
        if resolved_proxy_url is None and adspower_profile_id:
            proxy = await self._adspower_client.fetch_proxy(adspower_profile_id)
            if proxy is None:
                notes.append(f"proxy:none:{adspower_profile_id}")
                return None, None, notes, None
            resolved_proxy_url = proxy.upstream_url
            notes.append(
                f"proxy:adspower:{adspower_profile_id}:{proxy.proxy_type}:{proxy.country_code or 'unknown'}"
            )
        if resolved_proxy_url is None:
            return None, None, notes, None

        normalized_proxy = resolved_proxy_url.casefold()
        if normalized_proxy.startswith("http://") or normalized_proxy.startswith("https://"):
            # If the proxy is on 0.0.0.0 or 127.0.0.1 (host loopback), remap to 10.0.2.2
            # so the Android emulator can reach it (emulator's alias for the host machine).
            emulator_proxy_url = (
                resolved_proxy_url
                .replace("//0.0.0.0:", "//10.0.2.2:")
                .replace("//127.0.0.1:", "//10.0.2.2:")
                .replace("//localhost:", "//10.0.2.2:")
            )
            # Host-side form: the original URL (host loopback) is fine for Playwright.
            host_proxy_url = (
                resolved_proxy_url
                .replace("//0.0.0.0:", "//127.0.0.1:")
                .replace("//localhost:", "//127.0.0.1:")
            )
            notes.append(f"proxy:http:{emulator_proxy_url}")
            return emulator_proxy_url, host_proxy_url, notes, None

        bridge_handle = await self._proxy_bridge.start(resolved_proxy_url)
        notes.append(
            f"proxy:bridge:{resolved_proxy_url.split('://', 1)[0]}->{bridge_handle.emulator_proxy_url}"
        )
        return bridge_handle.emulator_proxy_url, bridge_handle.host_proxy_url, notes, bridge_handle

    async def run(
        self,
        *,
        topic: str,
        avd_name: str | None = None,
        proxy_url: str | None = None,
        adspower_profile_id: str | None = None,
        headless: bool | None = None,
    ) -> AndroidProbeResult:
        resolved_avd_name = avd_name or self._config.android_app.default_avd_name
        snapshot_name = (
            self._config.android_app.runtime_snapshot_name
            if self._config.android_app.emulator_use_snapshots
            else None
        )
        device = None
        session = None
        proxy_bridge_handle = None
        ad_analysis = AndroidAdAnalysisCoordinator(self._config.gemini, self._config.storage)
        proxy_notes: list[str] = []
        emulator_http_proxy = None
        host_http_proxy = None
        landing_scraper: AndroidLandingPageScraper | None = None
        try:
            (
                emulator_http_proxy,
                host_http_proxy,
                proxy_notes,
                proxy_bridge_handle,
            ) = await self._prepare_emulator_proxy(
                proxy_url=proxy_url,
                adspower_profile_id=adspower_profile_id,
            )
            landing_scraper = AndroidLandingPageScraper(
                self._config.storage,
                proxy_url=host_http_proxy,
            )
            await landing_scraper.start()
            device = await self._runtime.avd_manager.ensure_device(
                avd_name=resolved_avd_name,
                launch=AndroidEmulatorLaunchOptions(
                    headless=(
                        self._config.android_app.emulator_headless
                        if headless is None
                        else headless
                    ),
                    gpu_mode=self._config.android_app.emulator_gpu_mode,
                    accel_mode=self._config.android_app.emulator_accel_mode,
                    http_proxy=emulator_http_proxy,
                    load_snapshot=self._config.android_app.emulator_use_snapshots,
                    save_snapshot=False,
                    snapshot_name=snapshot_name,
                    force_snapshot_load=bool(snapshot_name),
                    skip_adb_auth=self._config.android_app.emulator_skip_adb_auth,
                    force_stop_running=self._config.android_app.emulator_force_restart_before_run,
                ),
            )
            session = await self._runtime.appium_provider.create_youtube_session(
                adb_serial=device.adb_serial,
                avd_name=device.avd_name,
            )
            navigator = AndroidYouTubeNavigator(
                session.driver,
                self._config.android_app,
                adb_serial=device.adb_serial,
            )
            await navigator.ensure_app_ready()
            home = AndroidProbeSurfaceSnapshot(*await navigator.describe_surface())

            await navigator.submit_search(topic)
            await navigator.wait_for_results(topic)
            results = AndroidProbeSurfaceSnapshot(*await navigator.describe_surface())

            opened_title = await navigator.open_first_result(topic)
            watch = None
            watch_samples = []
            watch_verified = False
            watch_ad_detected = False
            watch_debug_screen_path = None
            watch_debug_page_source_path = None
            ad_cta_clicked = False
            ad_cta_label = None
            ad_cta_destination_package = None
            ad_cta_destination_activity = None
            ad_cta_returned_to_youtube = False
            ad_cta_debug_screen_path = None
            ad_cta_debug_page_source_path = None
            watched_ads = []
            liked = False
            already_liked = False
            subscribed = False
            already_subscribed = False
            comments_glanced = False
            runtime_notes: list[str] = [*proxy_notes]
            if opened_title:
                watch = AndroidProbeSurfaceSnapshot(*await navigator.describe_surface())
                watcher = AndroidYouTubeWatcher(
                    session.driver,
                    self._config.android_app,
                    adb_serial=device.adb_serial,
                )
                recorder = None
                recording_handle = None
                recorded_video_path = None
                _probe_samples_ended_monotonic = time.monotonic()
                try:
                    if self._config.android_app.probe_screenrecord_enabled:
                        recorder = AndroidScreenRecorder(
                            adb_serial=device.adb_serial,
                            artifacts_dir=(
                                self._config.storage.base_path
                                / self._config.android_app.probe_screenrecord_artifacts_subdir
                            ),
                            bitrate=self._config.android_app.probe_screenrecord_bitrate,
                        )
                        recording_handle = await recorder.start(
                            artifact_prefix=self._build_safe_artifact_prefix(topic),
                        )
                    watch_result = await watcher.watch_current(
                        watch_seconds=self._config.android_app.probe_watch_seconds
                    )
                    watch_result, watch_extension_note = await self._extend_ad_watch_if_needed(
                        watcher=watcher,
                        watch_result=watch_result,
                    )
                    _probe_samples_ended_monotonic = time.monotonic()
                    if watch_extension_note is not None:
                        runtime_notes.append(watch_extension_note)
                finally:
                    # Recorder is stopped after probe_cta (below) to capture the full ad.
                    # Here we only stop if no ad was detected (cleanup path).
                    pass
                watch_samples = watch_result.samples
                watch_verified = watch_result.verified
                watch_ad_detected = any(sample.ad_detected for sample in watch_samples)
                if not watch_ad_detected:
                    # No ad — stop and discard recording now
                    if recorder is not None and recording_handle is not None:
                        with contextlib.suppress(Exception):
                            await recorder.stop(recording_handle, keep_local=False)
                        recorder = None
                        recording_handle = None
                if not watch_ad_detected and recorded_video_path:
                    recorded_video_abs = self._config.storage.base_path / recorded_video_path
                    recorded_video_abs.unlink(missing_ok=True)
                    recorded_video_path = None
                if (
                    self._config.android_app.session_engagement_enabled
                    and self._can_attempt_engagement(watch_result.samples)
                ):
                    runtime_notes.extend(
                        await self._prepare_for_engagement(
                            watcher=watcher,
                        )
                    )
                    engagement_result = await self._run_engagement_safe(
                        driver=session.driver,
                        adb_serial=device.adb_serial,
                        topic=topic,
                        opened_title=opened_title,
                        notes=runtime_notes,
                        note_prefix="engagement_failed",
                    )
                    if engagement_result is not None:
                        liked = engagement_result.liked
                        already_liked = engagement_result.already_liked
                        subscribed = engagement_result.subscribed
                        already_subscribed = engagement_result.already_subscribed
                        comments_glanced = engagement_result.comments_glanced
                        runtime_notes.extend(
                            await self._stabilize_playback_after_engagement(
                                watcher=watcher,
                            )
                        )
                        if not comments_glanced and watch_debug_screen_path is None:
                            watch_debug_screen_path, watch_debug_page_source_path = (
                                self._write_watch_debug_artifacts(
                                    driver=session.driver,
                                    topic=topic,
                                )
                            )
                    elif watch_debug_screen_path is None:
                        watch_debug_screen_path, watch_debug_page_source_path = (
                            self._write_watch_debug_artifacts(
                                driver=session.driver,
                                topic=topic,
                            )
                        )
                else:
                    reason = (
                        "disabled_for_probe_stability"
                        if not self._config.android_app.session_engagement_enabled
                        else self._engagement_gate_reason(watch_result.samples)
                    )
                    runtime_notes.append(f"pre_ad_engagement_skipped:{reason}")
                if watch_ad_detected or any(sample.error_messages for sample in watch_samples):
                    watch_debug_screen_path, watch_debug_page_source_path = (
                        self._write_watch_debug_artifacts(
                            driver=session.driver,
                            topic=topic,
                            adb_serial=device.adb_serial,
                            page_source_override=getattr(
                                watch_result,
                                "ad_debug_page_source",
                                None,
                            ),
                        )
                    )
                if watch_ad_detected:
                    try:
                        built_ad = None
                        ad_interactor = AndroidYouTubeAdInteractor(
                            session.driver,
                            self._config.android_app,
                            adb_serial=device.adb_serial,
                        )
                        _probe_cta_started_at = time.monotonic()
                        ad_cta_result = await ad_interactor.probe_cta(
                            artifact_dir=(
                                self._config.storage.base_path
                                / self._config.android_app.artifacts_subdir
                            ),
                            artifact_prefix=self._build_safe_artifact_prefix(topic),
                        )
                        # Full elapsed from last sample to here: debug artifact writes + CTA probe.
                        # The ad continues playing the whole time; subtract all of it from remaining.
                        _probe_cta_elapsed = time.monotonic() - _probe_samples_ended_monotonic
                        if recorder is not None and recording_handle is not None:
                            (
                                recorded_video_path,
                                recorded_video_duration_seconds,
                            ) = await self._finalize_recording_after_cta(
                                label="probe",
                                recorder=recorder,
                                recording_handle=recording_handle,
                                samples=watch_samples,
                                debug_page_source_path=watch_debug_page_source_path,
                                clicked=ad_cta_result.clicked,
                                returned_to_youtube=ad_cta_result.returned_to_youtube,
                                elapsed_since_samples=_probe_cta_elapsed,
                            )
                            recorder = None
                            recording_handle = None
                        ad_cta_clicked = ad_cta_result.clicked
                        ad_cta_label = ad_cta_result.label
                        ad_cta_destination_package = ad_cta_result.destination_package
                        ad_cta_destination_activity = ad_cta_result.destination_activity
                        ad_cta_returned_to_youtube = ad_cta_result.returned_to_youtube
                        ad_cta_debug_screen_path = ad_cta_result.debug_screen_path
                        ad_cta_debug_page_source_path = ad_cta_result.debug_page_source_path
                        built_ad = build_watched_ad_record(
                            watch_samples=watch_samples,
                            watch_debug_screen_path=watch_debug_screen_path,
                            watch_debug_page_source_path=watch_debug_page_source_path,
                            ad_cta_result=ad_cta_result,
                            recorded_video_path=recorded_video_path,
                            recorded_video_duration_seconds=recorded_video_duration_seconds,
                        )
                        if built_ad is not None:
                            built_ad = self._with_watched_ad_position(
                                built_ad,
                                len(watched_ads) + 1,
                            )
                            await self._focus_captured_ad_video_if_needed(built_ad)
                            ad_analysis.submit(built_ad)
                            landing_scraper.submit(built_ad)
                            watched_ads.append(built_ad)
                            await _notify_ad_captured()
                        if ad_cta_result.returned_to_youtube:
                            try:
                                post_ad_watch, post_ad_notes = await self._resume_after_ad_return(
                                    watcher=watcher,
                                    built_ad=built_ad,
                                )
                                runtime_notes.extend(post_ad_notes)
                            except Exception as exc:
                                runtime_notes.append(f"post_ad_watch_failed:{type(exc).__name__}")
                                post_ad_watch = None
                            if post_ad_watch is not None:
                                watch_result = replace(
                                    watch_result,
                                    verified=bool(
                                        getattr(watch_result, "verified", False)
                                        or getattr(post_ad_watch, "verified", False)
                                    ),
                                    samples=self._merge_watch_samples(
                                        list(getattr(watch_result, "samples", []) or []),
                                        list(getattr(post_ad_watch, "samples", []) or []),
                                    ),
                                    ad_debug_page_source=self._preferred_ad_debug_page_source(
                                        post_ad_watch,
                                        watch_result,
                                    ),
                                )
                            if (
                                self._config.android_app.session_engagement_enabled
                                and post_ad_watch is not None
                                and self._can_attempt_engagement(post_ad_watch.samples)
                            ):
                                runtime_notes.extend(
                                    await self._prepare_for_engagement(
                                        watcher=watcher,
                                    )
                                )
                                engagement_result = await self._run_engagement_safe(
                                    driver=session.driver,
                                    adb_serial=device.adb_serial,
                                    topic=topic,
                                    opened_title=opened_title,
                                    notes=runtime_notes,
                                    note_prefix="post_ad_engagement_failed",
                                )
                                if engagement_result is not None:
                                    liked = engagement_result.liked
                                    already_liked = engagement_result.already_liked
                                    subscribed = engagement_result.subscribed
                                    already_subscribed = engagement_result.already_subscribed
                                    comments_glanced = engagement_result.comments_glanced
                                    runtime_notes.extend(
                                        await self._stabilize_playback_after_engagement(
                                            watcher=watcher,
                                        )
                                    )
                            else:
                                reason = (
                                    "disabled_for_probe_stability"
                                    if not self._config.android_app.session_engagement_enabled
                                    else self._engagement_gate_reason(
                                        post_ad_watch.samples if post_ad_watch is not None else None
                                    )
                                )
                                runtime_notes.append(f"post_ad_engagement_skipped:{reason}")
                    except Exception as exc:
                        runtime_notes.append(f"ad_flow_failed:{type(exc).__name__}:{exc}")
                        if watch_debug_screen_path is None:
                            watch_debug_screen_path, watch_debug_page_source_path = (
                                self._write_watch_debug_artifacts(
                                    driver=session.driver,
                                    topic=topic,
                                )
                            )

            await ad_analysis.drain(timeout_seconds=90.0)
            self._cleanup_irrelevant_ad_videos(watched_ads)
            ad_analysis_done = self._count_ad_analysis_done(watched_ads)
            ad_analysis_terminal = self._count_ad_analysis_terminal(watched_ads)
            artifact_path = self._write_artifact(
                avd_name=device.avd_name,
                adb_serial=device.adb_serial,
                topic=topic,
                opened_title=opened_title,
                home=home,
                results=results,
                watch=watch,
                appium_server_url=session.server_url,
                reused_running_device=device.reused_running_device,
                started_local_appium=session.started_local_server,
                watch_verified=watch_verified,
                watch_ad_detected=watch_ad_detected,
                watch_samples=watch_samples,
                watch_debug_screen_path=watch_debug_screen_path,
                watch_debug_page_source_path=watch_debug_page_source_path,
                ad_cta_clicked=ad_cta_clicked,
                ad_cta_label=ad_cta_label,
                ad_cta_destination_package=ad_cta_destination_package,
                ad_cta_destination_activity=ad_cta_destination_activity,
                ad_cta_returned_to_youtube=ad_cta_returned_to_youtube,
                ad_cta_debug_screen_path=ad_cta_debug_screen_path,
                ad_cta_debug_page_source_path=ad_cta_debug_page_source_path,
                watched_ads=watched_ads,
                ad_analysis_done=ad_analysis_done,
                ad_analysis_terminal=ad_analysis_terminal,
                liked=liked,
                already_liked=already_liked,
                subscribed=subscribed,
                already_subscribed=already_subscribed,
                comments_glanced=comments_glanced,
                notes=(
                    [
                        *( [f"snapshot:{snapshot_name}"] if snapshot_name else [] ),
                        f"watch_seconds:{self._config.android_app.probe_watch_seconds}",
                        f"watch_verified:{str(watch_verified).lower()}",
                        f"watch_ad_detected:{str(watch_ad_detected).lower()}",
                        f"ad_cta_clicked:{str(ad_cta_clicked).lower()}",
                        *runtime_notes,
                    ]
                ),
            )

            return AndroidProbeResult(
                avd_name=device.avd_name,
                adb_serial=device.adb_serial,
                topic=topic,
                opened_title=opened_title,
                home=home,
                results=results,
                watch=watch,
                artifact_path=artifact_path,
                appium_server_url=session.server_url,
                reused_running_device=device.reused_running_device,
                started_local_appium=session.started_local_server,
                watch_verified=watch_verified,
                watch_ad_detected=watch_ad_detected,
                watch_samples=watch_samples,
                watch_debug_screen_path=watch_debug_screen_path,
                watch_debug_page_source_path=watch_debug_page_source_path,
                ad_cta_clicked=ad_cta_clicked,
                ad_cta_label=ad_cta_label,
                ad_cta_destination_package=ad_cta_destination_package,
                ad_cta_destination_activity=ad_cta_destination_activity,
                ad_cta_returned_to_youtube=ad_cta_returned_to_youtube,
                ad_cta_debug_screen_path=ad_cta_debug_screen_path,
                ad_cta_debug_page_source_path=ad_cta_debug_page_source_path,
                watched_ads=watched_ads,
                ad_analysis_done=ad_analysis_done,
                ad_analysis_terminal=ad_analysis_terminal,
                liked=liked,
                already_liked=already_liked,
                subscribed=subscribed,
                already_subscribed=already_subscribed,
                comments_glanced=comments_glanced,
                notes=(
                    [
                        *( [f"snapshot:{snapshot_name}"] if snapshot_name else [] ),
                        f"watch_seconds:{self._config.android_app.probe_watch_seconds}",
                        f"watch_verified:{str(watch_verified).lower()}",
                        f"watch_ad_detected:{str(watch_ad_detected).lower()}",
                        f"ad_cta_clicked:{str(ad_cta_clicked).lower()}",
                        *runtime_notes,
                    ]
                ),
            )
        finally:
            with contextlib.suppress(Exception):
                await ad_analysis.drain(timeout_seconds=5.0)
            if landing_scraper is not None:
                with contextlib.suppress(Exception):
                    await landing_scraper.drain(timeout_seconds=60.0)
                with contextlib.suppress(Exception):
                    await landing_scraper.stop()
            with contextlib.suppress(Exception):
                self._backfill_advertiser_from_landing_scrape(watched_ads)
            with contextlib.suppress(Exception):
                self._dedupe_watched_ads(watched_ads)
            try:
                if session is not None:
                    print("[android-probe] teardown:close_session:start", flush=True)
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(
                            self._runtime.appium_provider.close_session(session),
                            timeout=30,
                        )
                    print("[android-probe] teardown:close_session:done", flush=True)
            finally:
                if (
                    device is not None
                    and self._config.android_app.emulator_stop_after_run
                ):
                    try:
                        print(
                            f"[android-probe] teardown:stop_device:start serial={device.adb_serial}",
                            flush=True,
                        )
                        await asyncio.wait_for(
                            self._runtime.avd_manager.stop_device(
                                device.adb_serial,
                                avd_name=device.avd_name,
                            ),
                            timeout=30,
                        )
                        print("[android-probe] teardown:stop_device:done", flush=True)
                    except Exception:
                        with contextlib.suppress(Exception):
                            print(
                                f"[android-probe] teardown:force_cleanup:start serial={device.adb_serial}",
                                flush=True,
                            )
                            await self._runtime.avd_manager.force_cleanup_device(
                                adb_serial=device.adb_serial,
                                avd_name=device.avd_name,
                            )
                            print("[android-probe] teardown:force_cleanup:done", flush=True)
                    else:
                        with contextlib.suppress(Exception):
                            print(
                                f"[android-probe] teardown:force_cleanup:post_stop serial={device.adb_serial}",
                                flush=True,
                            )
                            await self._runtime.avd_manager.force_cleanup_device(
                                adb_serial=device.adb_serial,
                                avd_name=device.avd_name,
                            )
                            print("[android-probe] teardown:force_cleanup:post_stop_done", flush=True)
                if proxy_bridge_handle is not None:
                    with contextlib.suppress(Exception):
                        await self._proxy_bridge.stop(proxy_bridge_handle)

    def _write_artifact(
        self,
        *,
        avd_name: str,
        adb_serial: str,
        topic: str,
        opened_title: str | None,
        home: AndroidProbeSurfaceSnapshot,
        results: AndroidProbeSurfaceSnapshot,
        watch: AndroidProbeSurfaceSnapshot | None,
        appium_server_url: str,
        reused_running_device: bool,
        started_local_appium: bool,
        watch_verified: bool,
        watch_ad_detected: bool,
        watch_samples: list[object],
        watch_debug_screen_path: Path | None,
        watch_debug_page_source_path: Path | None,
        ad_cta_clicked: bool,
        ad_cta_label: str | None,
        ad_cta_destination_package: str | None,
        ad_cta_destination_activity: str | None,
        ad_cta_returned_to_youtube: bool,
        ad_cta_debug_screen_path: Path | None,
        ad_cta_debug_page_source_path: Path | None,
        watched_ads: list[dict[str, object]],
        ad_analysis_done: int,
        ad_analysis_terminal: int,
        liked: bool,
        already_liked: bool,
        subscribed: bool,
        already_subscribed: bool,
        comments_glanced: bool,
        notes: list[str],
    ) -> Path:
        base_dir = (
            self._config.storage.base_path
            / self._config.android_app.artifacts_subdir
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        artifact_path = base_dir / f"probe_{timestamp}.json"
        payload = {
            "avd_name": avd_name,
            "adb_serial": adb_serial,
            "topic": topic,
            "opened_title": opened_title,
            "home": asdict(home),
            "results": asdict(results),
            "watch": asdict(watch) if watch is not None else None,
            "appium_server_url": appium_server_url,
            "reused_running_device": reused_running_device,
            "started_local_appium": started_local_appium,
            "watch_verified": watch_verified,
            "watch_ad_detected": watch_ad_detected,
            "watch_samples": [asdict(sample) for sample in watch_samples],
            "watch_debug_screen_path": (
                str(watch_debug_screen_path) if watch_debug_screen_path is not None else None
            ),
            "watch_debug_page_source_path": (
                str(watch_debug_page_source_path)
                if watch_debug_page_source_path is not None
                else None
            ),
            "ad_cta_clicked": ad_cta_clicked,
            "ad_cta_label": ad_cta_label,
            "ad_cta_destination_package": ad_cta_destination_package,
            "ad_cta_destination_activity": ad_cta_destination_activity,
            "ad_cta_returned_to_youtube": ad_cta_returned_to_youtube,
            "ad_cta_debug_screen_path": (
                str(ad_cta_debug_screen_path) if ad_cta_debug_screen_path is not None else None
            ),
            "ad_cta_debug_page_source_path": (
                str(ad_cta_debug_page_source_path)
                if ad_cta_debug_page_source_path is not None
                else None
            ),
            "watched_ads": watched_ads,
            "ad_analysis_done": ad_analysis_done,
            "ad_analysis_terminal": ad_analysis_terminal,
            "liked": liked,
            "already_liked": already_liked,
            "subscribed": subscribed,
            "already_subscribed": already_subscribed,
            "comments_glanced": comments_glanced,
            "notes": notes,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return artifact_path

    @staticmethod
    def _count_ad_analysis_done(watched_ads: list[dict[str, object]]) -> int:
        return sum(
            1
            for item in watched_ads
            if isinstance(item, dict)
            and isinstance(item.get("capture"), dict)
            and str(item["capture"].get("analysis_status") or "") != ""
        )

    @staticmethod
    def _count_ad_analysis_terminal(watched_ads: list[dict[str, object]]) -> int:
        return sum(
            1
            for item in watched_ads
            if isinstance(item, dict)
            and isinstance(item.get("capture"), dict)
            and str(item["capture"].get("analysis_status") or "") in {
                "completed",
                "not_relevant",
                "skipped",
                "failed",
            }
        )

    @staticmethod
    def _with_watched_ad_position(
        ad: dict[str, object],
        position: int,
    ) -> dict[str, object]:
        positioned = dict(ad)
        positioned["position"] = position
        capture = positioned.get("capture")
        if isinstance(capture, dict):
            positioned_capture = dict(capture)
            positioned_capture["ad_position"] = position
            positioned["capture"] = positioned_capture
        return positioned

    @classmethod
    def _normalize_watched_ad_positions(
        cls,
        watched_ads: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [
            cls._with_watched_ad_position(ad, idx + 1)
            if isinstance(ad, dict)
            else ad
            for idx, ad in enumerate(watched_ads)
        ]

    @staticmethod
    def _build_safe_artifact_prefix(topic: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(ch if ch.isalnum() else "_" for ch in topic).strip("_") or "topic"
        return f"watch_debug_{timestamp}_{safe_topic}"

    def _write_watch_debug_artifacts(
        self,
        *,
        driver: object,
        topic: str,
        adb_serial: str | None = None,
        page_source_override: str | None = None,
    ) -> tuple[Path | None, Path | None]:
        base_dir = (
            self._config.storage.base_path
            / self._config.android_app.artifacts_subdir
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(ch if ch.isalnum() else "_" for ch in topic).strip("_") or "topic"

        screen_path = base_dir / f"watch_debug_{timestamp}_{safe_topic}.png"
        xml_path = base_dir / f"watch_debug_{timestamp}_{safe_topic}.xml"

        written_screen = None
        written_xml = None

        if adb_serial:
            try:
                adb_bin = require_tool_path("adb")
                result = subprocess.run(
                    [
                        adb_bin,
                        "-s",
                        adb_serial,
                        "exec-out",
                        "screencap",
                        "-p",
                    ],
                    capture_output=True,
                    check=False,
                    timeout=12,
                )
                if result.returncode == 0 and result.stdout:
                    screen_path.write_bytes(result.stdout)
                    written_screen = screen_path
            except Exception:
                written_screen = None
        if written_screen is None:
            try:
                driver.save_screenshot(str(screen_path))  # type: ignore[attr-defined]
                written_screen = screen_path
            except Exception:
                written_screen = None

        page_source = page_source_override
        if page_source:
            try:
                xml_path.write_text(page_source, encoding="utf-8")
                written_xml = xml_path
            except Exception:
                written_xml = None
        if written_xml is None and adb_serial:
            try:
                adb_bin = require_tool_path("adb")
                dump_path = "/sdcard/codex_runner_debug_dump.xml"
                dump_result = subprocess.run(
                    [
                        adb_bin,
                        "-s",
                        adb_serial,
                        "shell",
                        "uiautomator",
                        "dump",
                        dump_path,
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=20,
                )
                if dump_result.returncode == 0:
                    cat_result = subprocess.run(
                        [
                            adb_bin,
                            "-s",
                            adb_serial,
                            "shell",
                            "cat",
                            dump_path,
                        ],
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=12,
                    )
                    if cat_result.returncode == 0 and cat_result.stdout:
                        xml_path.write_text(cat_result.stdout, encoding="utf-8")
                        written_xml = xml_path
            except Exception:
                written_xml = None
        if page_source is None and written_xml is None and not adb_serial:
            try:
                page_source = driver.page_source  # type: ignore[attr-defined]
            except Exception:
                page_source = None
        if page_source and written_xml is None:
            try:
                xml_path.write_text(page_source, encoding="utf-8")
                written_xml = xml_path
            except Exception:
                written_xml = None

        return written_screen, written_xml

    def _append_stage_debug_artifacts(
        self,
        *,
        driver: object | None,
        topic_notes: list[str],
        topic: str,
        stage_label: str,
        adb_serial: str | None = None,
    ) -> None:
        if driver is None:
            return
        debug_screen_path, debug_page_source_path = self._write_watch_debug_artifacts(
            driver=driver,
            topic=f"{topic}_{stage_label}",
            adb_serial=adb_serial,
        )
        if debug_screen_path is not None:
            topic_notes.append(f"{stage_label}_debug:{debug_screen_path}")
        if debug_page_source_path is not None:
            topic_notes.append(f"{stage_label}_debug_xml:{debug_page_source_path}")

    async def _stop_recording_handle(
        self,
        *,
        recorder: AndroidScreenRecorder | None,
        recording_handle: object | None,
    ) -> str | None:
        if recorder is None or recording_handle is None:
            return None
        local_video = await recorder.stop(
            recording_handle,
            keep_local=True,
        )
        if local_video is None:
            return None
        try:
            return str(local_video.relative_to(self._config.storage.base_path))
        except ValueError:
            return str(local_video)

    async def _start_recording_handle(
        self,
        *,
        label: str,
        topic: str,
        adb_serial: str,
        round_index: int | None = None,
    ) -> tuple[AndroidScreenRecorder, object]:
        recorder = AndroidScreenRecorder(
            adb_serial=adb_serial,
            artifacts_dir=(
                self._config.storage.base_path
                / self._config.android_app.probe_screenrecord_artifacts_subdir
            ),
            bitrate=self._config.android_app.probe_screenrecord_bitrate,
        )
        recording_handle = await recorder.start(
            artifact_prefix=self._build_safe_artifact_prefix(topic),
        )
        if round_index is None:
            logger.info(
                "recorder[%s]: started topic=%s path=%s",
                label,
                topic,
                getattr(recording_handle, "local_path", None),
            )
        else:
            logger.info(
                "recorder[%s]: started round=%s topic=%s path=%s",
                label,
                round_index,
                topic,
                getattr(recording_handle, "local_path", None),
            )
        return recorder, recording_handle

    def _probe_recorded_video_duration(self, recorded_video_path: str | None) -> float | None:
        if not recorded_video_path:
            return None
        video_path = self._config.storage.base_path / recorded_video_path
        if not video_path.exists():
            return None
        ffprobe_bin = require_tool_path("ffprobe")
        completed = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nk=1:nw=1",
                str(video_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=build_android_runtime_env(),
        )
        if completed.returncode != 0:
            return None
        try:
            duration = float((completed.stdout or "").strip())
        except ValueError:
            return None
        if duration <= 0:
            return None
        return duration

    async def _extend_ad_watch_if_needed(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        watch_result: object,
    ) -> tuple[object, str | None]:
        samples = list(getattr(watch_result, "samples", []) or [])
        if not samples or not any(getattr(sample, "ad_detected", False) for sample in samples):
            return watch_result, None

        # Keep enough footage for analysis, but avoid drifting into the next ad
        # in a pod before we probe the CTA for the current creative.
        desired_ad_window_seconds = max(
            5.0,
            min(8.0, float(self._config.android_app.probe_watch_seconds)),
        )
        observed_ad_window_seconds = self._observed_ad_window_seconds(samples)
        extra_seconds = max(0.0, desired_ad_window_seconds - observed_ad_window_seconds)
        remaining_current_ad_seconds = self._remaining_current_ad_seconds(samples)
        if remaining_current_ad_seconds is not None:
            extra_seconds = min(
                extra_seconds,
                max(0.0, remaining_current_ad_seconds - 1.0),
            )
        extra_seconds = max(0, int(math.ceil(extra_seconds)))
        if extra_seconds <= 0:
            return watch_result, None

        extra_result = await watcher.watch_current(watch_seconds=extra_seconds)
        merged_samples = self._merge_watch_samples(samples, getattr(extra_result, "samples", []))
        return (
            replace(
                watch_result,
                verified=bool(
                    getattr(watch_result, "verified", False)
                    or getattr(extra_result, "verified", False)
                ),
                samples=merged_samples,
                ad_debug_page_source=self._preferred_ad_debug_page_source(
                    extra_result,
                    watch_result,
                ),
            ),
            f"ad_watch_extended:{extra_seconds}",
        )

    async def _settle_post_ad_watch_if_needed(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        watch_seconds: int | None = None,
        max_cycles: int = 3,
    ) -> tuple[object, list[str]]:
        notes: list[str] = []
        settle_seconds = watch_seconds or self._config.android_app.probe_post_ad_watch_seconds
        merged_result = await watcher.watch_current(
            watch_seconds=settle_seconds
        )
        for cycle in range(1, max_cycles):
            merged_samples = list(getattr(merged_result, "samples", []) or [])
            if not merged_samples or not any(
                self._sample_has_active_ad_ui(sample) for sample in merged_samples
            ):
                break
            extra_result = await watcher.watch_current(
                watch_seconds=settle_seconds
            )
            merged_result = replace(
                merged_result,
                verified=bool(
                    getattr(merged_result, "verified", False)
                    or getattr(extra_result, "verified", False)
                    or self._is_watch_verified_by_stable_dwell(merged_samples)
                ),
                samples=self._merge_watch_samples(
                    merged_samples,
                    list(getattr(extra_result, "samples", []) or []),
                ),
                ad_debug_page_source=self._preferred_ad_debug_page_source(
                    extra_result,
                    merged_result,
                ),
            )
            notes.append(
                f"post_ad_watch_extended:{settle_seconds}:cycle{cycle}"
            )
        return merged_result, notes

    async def _resume_after_ad_return(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        built_ad: object | None,
    ) -> tuple[object | None, list[str]]:
        notes: list[str] = []
        with contextlib.suppress(Exception):
            await watcher.dismiss_engagement_panel()
        if await watcher.restore_primary_watch_surface():
            notes.append("post_ad_surface:restored")
        # Check for a second ad in the pod — do NOT skip it, let midroll loop handle it.
        pre_skip_result = await watcher.watch_current(watch_seconds=4)
        pre_skip_samples = list(getattr(pre_skip_result, "samples", []) or [])
        if any(getattr(s, "ad_detected", False) for s in pre_skip_samples):
            # A new ad is playing — return it so the caller (midroll loop) can catch it.
            notes.append("post_ad_residual_detected:returning_for_midroll")
            return pre_skip_result, notes

        quick_settle_seconds = max(
            4,
            min(6, self._config.android_app.probe_post_ad_watch_seconds),
        )
        quick_result, quick_notes = await self._settle_post_ad_watch_if_needed(
            watcher=watcher,
            watch_seconds=quick_settle_seconds,
            max_cycles=1,
        )
        notes.extend(quick_notes)

        quick_samples = list(getattr(quick_result, "samples", []) or [])
        quick_has_active_ad = any(
            self._sample_has_active_ad_ui(sample) for sample in quick_samples
        )
        if not quick_samples or not quick_has_active_ad:
            if await watcher.ensure_playing():
                notes.append("post_ad_playback:resume_requested")
            else:
                notes.append("post_ad_playback:resume_missed")
            if await watcher.restore_primary_watch_surface():
                notes.append("post_ad_surface:restored_after_quick")
            return quick_result, notes

        if self._should_skip_post_ad_settle(built_ad):
            notes.append("post_ad_watch_limited:ad_completed_after_return")
            if await watcher.restore_primary_watch_surface():
                notes.append("post_ad_surface:restored_limited")
            return quick_result, notes

        extended_result, extended_notes = await self._settle_post_ad_watch_if_needed(
            watcher=watcher,
        )
        if quick_samples:
            extended_result = replace(
                extended_result,
                verified=bool(
                    getattr(quick_result, "verified", False)
                    or getattr(extended_result, "verified", False)
                ),
                samples=self._merge_watch_samples(
                    quick_samples,
                    list(getattr(extended_result, "samples", []) or []),
                ),
                ad_debug_page_source=self._preferred_ad_debug_page_source(
                    extended_result,
                    quick_result,
                ),
            )
        notes.extend(extended_notes)
        if await watcher.restore_primary_watch_surface():
            notes.append("post_ad_surface:restored_after_extended")
        return extended_result, notes

    async def _probe_unaccepted_watch_surface_for_ad(
        self,
        *,
        driver: object | None,
        navigator: AndroidYouTubeNavigator | None,
        watcher: AndroidYouTubeWatcher | None,
        adb_serial: str | None,
        topic: str,
        stage_label: str,
        topic_notes: list[str],
        ad_analysis: AndroidAdAnalysisCoordinator,
        landing_scraper: AndroidLandingPageScraper,
        watched_ads: list[dict[str, object]],
        topic_watched_ads: list[dict[str, object]],
        notify_ad_captured: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        if driver is None or navigator is None or watcher is None or adb_serial is None:
            return False
        topic_notes.append(f"{stage_label}:probe_started")
        try:
            if not await navigator.has_watch_activity():
                topic_notes.append(f"{stage_label}:no_watch_activity")
                return False
        except Exception:
            topic_notes.append(f"{stage_label}:watch_activity_probe_failed")
            return False

        probe_watch_seconds = max(4, min(8, self._config.android_app.probe_watch_seconds))
        # Dismiss any sponsored landing panel that may be open over the player
        # (happens when we clicked a sponsored search result). Recording with the
        # panel open captures the lendging card instead of the ad video.
        with contextlib.suppress(Exception):
            await watcher.dismiss_residual_ad_if_present()
        with contextlib.suppress(Exception):
            await watcher.restore_primary_watch_surface()
        with contextlib.suppress(Exception):
            await watcher.dismiss_engagement_panel()
        recorder = None
        recording_handle = None
        recorded_video_path = None
        recorded_video_duration_seconds = None
        probe_result = None
        probe_error: Exception | None = None
        _stage_samples_ended_monotonic = time.monotonic()
        try:
            if self._config.android_app.probe_screenrecord_enabled:
                recorder = AndroidScreenRecorder(
                    adb_serial=adb_serial,
                    artifacts_dir=(
                        self._config.storage.base_path
                        / self._config.android_app.probe_screenrecord_artifacts_subdir
                    ),
                    bitrate=self._config.android_app.probe_screenrecord_bitrate,
                )
                recording_handle = await recorder.start(
                    artifact_prefix=self._build_safe_artifact_prefix(f"{topic}_{stage_label}"),
                )
            try:
                probe_result = await watcher.watch_current(
                    watch_seconds=probe_watch_seconds,
                    timeout_grace_seconds=12.0,
                    timeout_floor_seconds=18.0,
                )
                probe_result, probe_extension_note = await self._extend_ad_watch_if_needed(
                    watcher=watcher,
                    watch_result=probe_result,
                )
                _stage_samples_ended_monotonic = time.monotonic()
                if probe_extension_note is not None:
                    topic_notes.append(f"{stage_label}:{probe_extension_note}")
            except Exception as exc:
                probe_error = exc
        finally:
            if probe_error is not None and recorder is not None and recording_handle is not None:
                # Error path: stop and discard recording immediately
                with contextlib.suppress(Exception):
                    await recorder.stop(recording_handle, keep_local=False)
                recorder = None
                recording_handle = None

        if probe_error is not None:
            topic_notes.append(f"{stage_label}:probe_error:{type(probe_error).__name__}")
            return False

        if probe_result is None:
            topic_notes.append(f"{stage_label}:probe_failed")
            if recorder is not None and recording_handle is not None:
                with contextlib.suppress(Exception):
                    await recorder.stop(recording_handle, keep_local=False)
            return False

        probe_samples = list(getattr(probe_result, "samples", []) or [])
        probe_ad_detected = any(sample.ad_detected for sample in probe_samples)
        if not probe_ad_detected:
            topic_notes.append(f"{stage_label}:no_ad")
            if recorder is not None and recording_handle is not None:
                with contextlib.suppress(Exception):
                    await recorder.stop(recording_handle, keep_local=False)
            return False

        topic_notes.append(f"{stage_label}:ad_detected")
        watch_debug_screen_path, watch_debug_page_source_path = self._write_watch_debug_artifacts(
            driver=driver,
            topic=f"{topic}_{stage_label}",
            adb_serial=adb_serial,
            page_source_override=getattr(probe_result, "ad_debug_page_source", None),
        )
        if watch_debug_screen_path is not None:
            topic_notes.append(f"{stage_label}_debug:{watch_debug_screen_path}")
        if watch_debug_page_source_path is not None:
            topic_notes.append(f"{stage_label}_debug_xml:{watch_debug_page_source_path}")

        ad_cta_result = None
        try:
            ad_interactor = AndroidYouTubeAdInteractor(
                driver,
                self._config.android_app,
                adb_serial=adb_serial,
            )
            ad_cta_result = await ad_interactor.probe_cta(
                artifact_dir=(
                    self._config.storage.base_path
                    / self._config.android_app.artifacts_subdir
                ),
                artifact_prefix=self._build_safe_artifact_prefix(f"{topic}_{stage_label}"),
            )
            topic_notes.append(
                f"{stage_label}_ad_cta_returned:{str(ad_cta_result.returned_to_youtube).lower()}"
            )
        except Exception as exc:
            topic_notes.append(f"{stage_label}_ad_cta_probe_failed:{type(exc).__name__}:{exc}")
        # Full elapsed from last sample: debug artifact writes + CTA probe.
        _stage_cta_elapsed = time.monotonic() - _stage_samples_ended_monotonic

        # Stop recording after CTA probe — subtract elapsed CTA probe time from remaining
        # since ad progressed during that window.
        if recorder is not None and recording_handle is not None:
            (
                recorded_video_path,
                recorded_video_duration_seconds,
            ) = await self._finalize_recording_after_cta(
                label=stage_label,
                recorder=recorder,
                recording_handle=recording_handle,
                samples=probe_samples,
                debug_page_source_path=watch_debug_page_source_path,
                clicked=ad_cta_result.clicked if ad_cta_result is not None else None,
                returned_to_youtube=(
                    ad_cta_result.returned_to_youtube
                    if ad_cta_result is not None
                    else None
                ),
                elapsed_since_samples=_stage_cta_elapsed,
            )

        built_ad = build_watched_ad_record(
            watch_samples=probe_samples,
            watch_debug_screen_path=watch_debug_screen_path,
            watch_debug_page_source_path=watch_debug_page_source_path,
            ad_cta_result=ad_cta_result,
            recorded_video_path=recorded_video_path,
            recorded_video_duration_seconds=recorded_video_duration_seconds,
        )
        if built_ad is None:
            return False

        built_ad = self._with_watched_ad_position(built_ad, len(watched_ads) + 1)
        await self._focus_captured_ad_video_if_needed(built_ad)
        ad_analysis.submit(built_ad)
        landing_scraper.submit(built_ad)
        topic_watched_ads.append(built_ad)
        watched_ads.append(built_ad)
        if notify_ad_captured is not None:
            await notify_ad_captured()
        topic_notes.append(f"{stage_label}:ad_done")

        if ad_cta_result is not None and ad_cta_result.returned_to_youtube:
            with contextlib.suppress(Exception):
                await watcher.restore_primary_watch_surface()
            with contextlib.suppress(Exception):
                await watcher.dismiss_residual_ad_if_present()
            with contextlib.suppress(Exception):
                await watcher.ensure_playing()
        else:
            with contextlib.suppress(Exception):
                current_package, _ = await navigator.current_package_activity()
                if current_package != self._config.android_app.youtube_package:
                    await navigator.ensure_app_ready()
                    topic_notes.append(f"{stage_label}:youtube_reacquired")

        return True

    def _merge_watch_samples(self, base_samples: list[object], extra_samples: list[object]) -> list[object]:
        if not base_samples:
            return list(extra_samples)
        if not extra_samples:
            return list(base_samples)
        offset_base = self._observed_watch_seconds(base_samples)
        interval = max(1, self._config.android_app.probe_watch_sample_interval_seconds)
        shifted_extra = [
            replace(
                sample,
                offset_seconds=getattr(sample, "offset_seconds", 0) + offset_base + interval,
            )
            for sample in extra_samples
        ]
        return [*base_samples, *shifted_extra]

    @staticmethod
    def _result_has_ad_samples(result: object | None) -> bool:
        if result is None:
            return False
        return any(
            getattr(sample, "ad_detected", False)
            for sample in list(getattr(result, "samples", []) or [])
        )

    @staticmethod
    def _result_is_banner_only_ad(result: object | None) -> bool:
        """Return True when the detected ad is a banner overlay, not a video ad.

        Banner ads show a Sponsored card while the main video keeps playing.
        The key signal: main video progress_seconds increases across ad samples
        (video not paused). Video ads pause the main video — progress stays flat.
        """
        samples = list(getattr(result, "samples", []) or [])
        ad_samples = [s for s in samples if getattr(s, "ad_detected", False)]
        if not ad_samples:
            return False
        # If any sample has an ad timer or skip button → definitely a video ad
        has_timer = any(
            isinstance(getattr(s, "ad_duration_seconds", None), (int, float))
            for s in ad_samples
        )
        has_skip = any(getattr(s, "skip_available", False) for s in ad_samples)
        if has_timer or has_skip:
            return False
        # Check if main video progress advances → video is still playing → banner
        progress_values = [
            getattr(s, "progress_seconds", None)
            for s in ad_samples
            if isinstance(getattr(s, "progress_seconds", None), (int, float))
        ]
        if len(progress_values) >= 2:
            return progress_values[-1] > progress_values[0]
        # Only one sample with no timer/skip — can't tell, assume video ad to be safe
        return False

    @staticmethod
    def _result_is_play_store_ad(result: object | None) -> bool:
        """Return True when the detected ad is a Google Play Store app-install banner.

        These banners are irrelevant (no landing page, no video) and should be skipped.
        The only reliable signal is display_url containing play.google.com — "Install"
        CTA alone is not sufficient because financial app ads also use it.
        """
        samples = list(getattr(result, "samples", []) or [])
        ad_samples = [s for s in samples if getattr(s, "ad_detected", False)]
        for s in ad_samples:
            display = str(getattr(s, "ad_display_url", "") or "").casefold()
            if "play.google.com" in display:
                return True
            # CTA labels visible in ad overlay text
            for line in list(getattr(s, "ad_visible_lines", []) or []):
                if "play.google.com" in str(line).casefold():
                    return True
        return False

    async def _advance_main_watch_iteration(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        watch_result: object,
        target_watch_seconds: float,
        pending_midroll_result: object | None,
        pending_midroll_samples_ended_monotonic: float | None,
    ) -> tuple[object, str | None, object | None, float | None]:
        if pending_midroll_result is not None:
            return (
                watch_result,
                "main_watch_ad_detected:pending_residual",
                pending_midroll_result,
                pending_midroll_samples_ended_monotonic or time.monotonic(),
            )
        next_result, note, extension_result = await self._continue_main_watch_if_needed(
            watcher=watcher,
            watch_result=watch_result,
            target_watch_seconds=target_watch_seconds,
        )
        return next_result, note, extension_result, time.monotonic()

    @staticmethod
    def _preferred_ad_debug_page_source(*results: object) -> str | None:
        for result in results:
            candidate = getattr(result, "ad_debug_page_source", None)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return None

    async def _trim_recording_tail_to_ad(
        self,
        *,
        built_ad: dict[str, object] | None,
        ad_sample_count: int,
        extension_samples: list[object] | None = None,
        ad_detected_after_watch_seconds: float | None = None,
    ) -> None:
        """Trim the recording to contain only the current ad. The recorder
        runs for the whole watch chunk, so without trimming the clip mixes
        the regular video, pod transitions, and neighbouring ads."""
        if not isinstance(built_ad, dict):
            return
        capture = built_ad.get("capture")
        if not isinstance(capture, dict):
            return
        video_file = capture.get("video_file")
        if not isinstance(video_file, str) or not video_file.strip():
            return
        rec_dur = self._coerce_float(capture.get("recorded_video_duration_seconds"))
        if rec_dur is None or rec_dur <= 0:
            return
        watched = self._coerce_float(built_ad.get("watched_seconds")) or 0.0
        ad_duration_hint = self._coerce_float(built_ad.get("ad_duration_seconds"))
        ad_seconds = max(
            float(ad_sample_count),
            watched,
            ad_duration_hint or 0.0,
        )
        if ad_seconds <= 0:
            return
        trim_window = self._calculate_ad_trim_window(
            recorded_duration_seconds=rec_dur,
            ad_seconds=ad_seconds,
            ad_sample_start_seconds=self._coerce_float(
                built_ad.get("first_ad_offset_seconds")
            ),
            ad_detected_after_watch_seconds=ad_detected_after_watch_seconds,
        )
        if trim_window is None:
            return
        start_seconds, ad_span = trim_window
        remainder_after_ad = rec_dur - start_seconds - ad_span
        logger.info(
            "trim_recording: rec_dur=%.1fs start=%.1fs ad_span=%.1fs remainder=%.1fs detected_after=%s sample_start=%s",
            rec_dur,
            start_seconds,
            ad_span,
            remainder_after_ad,
            ad_detected_after_watch_seconds,
            built_ad.get("first_ad_offset_seconds"),
        )
        if start_seconds < 0.5 and rec_dur - ad_span < 1.0:
            return
        source_path = self._config.storage.base_path / video_file
        if not source_path.exists():
            return
        trimmed_path = source_path.with_name(f"{source_path.stem}_adonly{source_path.suffix}")
        ffmpeg_bin = require_tool_path("ffmpeg")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-y",
                "-i", str(source_path),
                "-ss", f"{start_seconds:.2f}",
                "-t", f"{ad_span:.2f}",
                "-map", "0:v:0",
                "-an",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(trimmed_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=build_android_runtime_env(),
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
        except Exception:
            trimmed_path.unlink(missing_ok=True)
            return
        if proc.returncode != 0 or not trimmed_path.exists() or trimmed_path.stat().st_size == 0:
            trimmed_path.unlink(missing_ok=True)
            return
        with contextlib.suppress(Exception):
            source_path.unlink(missing_ok=True)
        try:
            trimmed_rel = str(trimmed_path.relative_to(self._config.storage.base_path))
        except ValueError:
            trimmed_rel = str(trimmed_path)
        new_dur = self._probe_recorded_video_duration(trimmed_rel)
        capture["video_file"] = trimmed_rel
        built_ad["video_file"] = trimmed_rel
        if new_dur is not None:
            capture["recorded_video_duration_seconds"] = new_dur
            built_ad["recorded_video_duration_seconds"] = new_dur
        # After tail-trimming, the saved clip should start at the ad itself.
        built_ad["first_ad_offset_seconds"] = 0.0
        capture["first_ad_offset_seconds"] = 0.0

    @staticmethod
    def _calculate_ad_trim_window(
        *,
        recorded_duration_seconds: float,
        ad_seconds: float,
        ad_sample_start_seconds: float | None = None,
        ad_detected_after_watch_seconds: float | None = None,
    ) -> tuple[float, float] | None:
        if recorded_duration_seconds <= 0.0 or ad_seconds <= 0.0:
            return None
        tail_buffer_seconds = 2.0
        desired_span = min(recorded_duration_seconds, ad_seconds + tail_buffer_seconds)
        tail_start = max(0.0, recorded_duration_seconds - desired_span)
        start_seconds = tail_start
        if (
            isinstance(ad_sample_start_seconds, (int, float))
            and 0.0 <= float(ad_sample_start_seconds) <= 3.0
            and ad_detected_after_watch_seconds is None
        ):
            # When recorder starts while an ad is already on screen, tail-trimming
            # can keep only the CTA/landing tail and drop the ad creative. In that
            # case the first ad sample is near the beginning, so keep the head.
            start_seconds = 0.0
        if (
            isinstance(ad_detected_after_watch_seconds, (int, float))
            and 0.0 < float(ad_detected_after_watch_seconds) < recorded_duration_seconds
        ):
            detected_start = float(ad_detected_after_watch_seconds)
            remaining_after_detected = recorded_duration_seconds - detected_start
            min_remaining = min(max(ad_seconds * 0.45, 8.0), 20.0)
            if remaining_after_detected >= min_remaining:
                biased_start = detected_start + 1.5
                if recorded_duration_seconds - biased_start >= min_remaining:
                    detected_start = biased_start
                start_seconds = max(start_seconds, detected_start)
        ad_span = min(desired_span, recorded_duration_seconds - start_seconds)
        if ad_span <= 0.0:
            return None
        return start_seconds, ad_span

    @staticmethod
    def _main_watch_ad_detected_after_seconds(note: str | None) -> float | None:
        if not isinstance(note, str):
            return None
        prefix = "main_watch_ad_detected:"
        if not note.startswith(prefix):
            return None
        raw_value = note[len(prefix):].strip()
        try:
            value = float(raw_value)
        except ValueError:
            return None
        return value if value > 0.0 else None

    async def _focus_captured_ad_video_if_needed(
        self,
        built_ad: dict[str, object] | None,
    ) -> None:
        if not isinstance(built_ad, dict):
            return
        capture = built_ad.get("capture")
        if not isinstance(capture, dict):
            return
        video_file = capture.get("video_file")
        if not isinstance(video_file, str) or not video_file.strip():
            return

        first_ad_off = self._coerce_float(built_ad.get("first_ad_offset_seconds"))
        watched_sec = self._coerce_float(built_ad.get("watched_seconds"))
        ad_dur = self._coerce_float(built_ad.get("ad_duration_seconds"))
        rec_dur = self._coerce_float(capture.get("recorded_video_duration_seconds"))
        logger.info(
            "focus_window: first_ad_offset=%.1fs watched=%.1fs ad_duration=%.1fs recorded=%.1fs",
            first_ad_off or 0, watched_sec or 0, ad_dur or 0, rec_dur or 0,
        )
        focus_window = _build_ad_video_focus_window(
            first_ad_offset_seconds=first_ad_off,
            watched_seconds=watched_sec,
            ad_duration_seconds=ad_dur,
            recorded_video_duration_seconds=rec_dur,
        )
        if focus_window is None:
            logger.info("focus_window: skipped (window is None)")
            return

        source_path = self._config.storage.base_path / video_file
        if not source_path.exists():
            return

        start_seconds, max_duration_seconds = focus_window
        if start_seconds < 0.5 and rec_dur is not None and rec_dur - max_duration_seconds < 1.0:
            logger.info(
                "focus_window: skipped (already focused) video=%s start=%.1fs window=%.1fs",
                video_file,
                start_seconds,
                max_duration_seconds,
            )
            return

        focused_path = source_path.with_name(f"{source_path.stem}_focused{source_path.suffix}")
        ffmpeg_bin = require_tool_path("ffmpeg")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-y",
                "-i", str(source_path),
                "-ss", f"{start_seconds:.2f}",
                "-t", f"{max_duration_seconds:.2f}",
                "-map", "0:v:0",
                "-an",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(focused_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=build_android_runtime_env(),
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
        except Exception as exc:
            logger.info("focus_window: failed to create focused video=%s error=%s", video_file, exc)
            focused_path.unlink(missing_ok=True)
            return
        if proc.returncode != 0 or not focused_path.exists() or focused_path.stat().st_size == 0:
            logger.info("focus_window: ffmpeg returned no focused video=%s rc=%s", video_file, proc.returncode)
            focused_path.unlink(missing_ok=True)
            return

        try:
            focused_rel = str(focused_path.relative_to(self._config.storage.base_path))
        except ValueError:
            focused_rel = str(focused_path)
        new_dur = self._probe_recorded_video_duration(focused_rel)
        if new_dur is None or new_dur <= 0:
            logger.info(
                "focus_window: focused video is not playable source=%s focused=%s",
                video_file,
                focused_rel,
            )
            focused_path.unlink(missing_ok=True)
            return

        capture["source_video_file"] = video_file
        built_ad["source_video_file"] = video_file
        if rec_dur is not None:
            capture["source_recorded_video_duration_seconds"] = rec_dur
            built_ad["source_recorded_video_duration_seconds"] = rec_dur
        capture["video_file"] = focused_rel
        built_ad["video_file"] = focused_rel
        if new_dur is not None:
            capture["recorded_video_duration_seconds"] = new_dur
            built_ad["recorded_video_duration_seconds"] = new_dur

        adjusted_first = max(0.0, (first_ad_off or 0.0) - start_seconds)
        built_ad["first_ad_offset_seconds"] = adjusted_first
        capture["first_ad_offset_seconds"] = adjusted_first
        last_ad_off = self._coerce_float(built_ad.get("last_ad_offset_seconds"))
        if last_ad_off is not None:
            adjusted_last = max(adjusted_first, last_ad_off - start_seconds)
            built_ad["last_ad_offset_seconds"] = adjusted_last
            capture["last_ad_offset_seconds"] = adjusted_last

        logger.info(
            "focus_window: created focused video source=%s focused=%s start=%.1fs window=%.1fs duration=%s",
            video_file,
            focused_rel,
            start_seconds,
            max_duration_seconds,
            new_dur,
        )

    @staticmethod
    def _bump_watched_seconds_from_rec_dur(
        built_ad: dict[str, object],
        rec_dur: float,
    ) -> None:
        """If rec_dur is a better estimate of watched time than what build_watched_ad_record
        computed (e.g. because rec_dur was None at build time), update watched_seconds."""
        ad_dur = None
        raw_ad_dur = built_ad.get("ad_duration_seconds")
        if isinstance(raw_ad_dur, (int, float)):
            ad_dur = float(raw_ad_dur)
        current_watched = built_ad.get("watched_seconds")
        if not isinstance(current_watched, (int, float)):
            current_watched = 0.0
        # Accept rec_dur as watched only when it doesn't overshoot the known ad duration.
        if ad_dur is not None and rec_dur > ad_dur + 5.0:
            return
        if rec_dur > float(current_watched):
            built_ad["watched_seconds"] = rec_dur

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _observed_watch_seconds(samples: list[object]) -> int:
        return max((int(getattr(sample, "offset_seconds", 0)) for sample in samples), default=0)

    def _observed_ad_window_seconds(self, samples: list[object]) -> float:
        ad_offsets = [
            float(getattr(sample, "offset_seconds"))
            for sample in samples
            if getattr(sample, "ad_detected", False)
            and isinstance(getattr(sample, "offset_seconds", None), (int, float))
        ]
        if not ad_offsets:
            return 0.0
        interval = max(1, self._config.android_app.probe_watch_sample_interval_seconds)
        return max(float(interval), (max(ad_offsets) - min(ad_offsets)) + float(interval))

    @staticmethod
    def _remaining_current_ad_seconds(samples: list[object]) -> float | None:
        for sample in reversed(samples):
            if not getattr(sample, "ad_detected", False):
                continue
            progress_seconds = getattr(sample, "ad_progress_seconds", None)
            duration_seconds = getattr(sample, "ad_duration_seconds", None)
            if not isinstance(progress_seconds, (int, float)):
                continue
            if not isinstance(duration_seconds, (int, float)):
                continue
            remaining_seconds = float(duration_seconds) - float(progress_seconds)
            if remaining_seconds >= 0.0:
                return remaining_seconds
        return None

    @staticmethod
    def _sample_debug_summary(
        samples: list[object],
        *,
        max_items: int = 6,
    ) -> str:
        if not samples:
            return "[]"
        rendered: list[str] = []
        for sample in samples[-max_items:]:
            offset = getattr(sample, "offset_seconds", None)
            ad_detected = bool(getattr(sample, "ad_detected", False))
            progress = getattr(sample, "ad_progress_seconds", None)
            duration = getattr(sample, "ad_duration_seconds", None)
            skip = bool(getattr(sample, "skip_available", False))
            seekbar = "seek" if getattr(sample, "ad_seekbar_description", None) else "noseek"
            sponsor = "s" if getattr(sample, "ad_sponsor_label", None) else "-"
            display = "u" if getattr(sample, "ad_display_url", None) else "-"
            cta = "c" if getattr(sample, "ad_cta_text", None) else "-"
            rendered.append(
                f"{offset}:{'ad' if ad_detected else 'noad'}:{progress}/{duration}:"
                f"skip={int(skip)}:{seekbar}:{sponsor}{display}{cta}"
            )
        return "[" + ", ".join(rendered) + "]"

    @staticmethod
    def _recording_runtime_seconds(
        recording_handle: object | None,
    ) -> float | None:
        started_monotonic = getattr(recording_handle, "started_monotonic", None)
        if not isinstance(started_monotonic, (int, float)):
            return None
        return max(0.0, time.monotonic() - float(started_monotonic))

    @staticmethod
    def _remaining_current_ad_seconds_from_debug_xml(
        debug_page_source_path: Path | None,
    ) -> tuple[float | None, float | None, float | None]:
        if debug_page_source_path is None:
            return None, None, None
        metadata = _parse_debug_watch_metadata(debug_page_source_path)
        progress_seconds = metadata.get("ad_progress_seconds")
        duration_seconds = metadata.get("ad_duration_seconds")
        if not isinstance(progress_seconds, (int, float)):
            return None, None, duration_seconds if isinstance(duration_seconds, (int, float)) else None
        if not isinstance(duration_seconds, (int, float)):
            return None, float(progress_seconds), None
        if float(duration_seconds) > _MAX_RELIABLE_AD_DURATION_SECONDS:
            return None, float(progress_seconds), None
        if (
            metadata.get("ad_timing_from_fallback_seekbar")
            and float(duration_seconds) > _MAX_FALLBACK_SEEKBAR_AD_DURATION_SECONDS
        ):
            return None, float(progress_seconds), None
        remaining_seconds = float(duration_seconds) - float(progress_seconds)
        if remaining_seconds < 0.0:
            return None, float(progress_seconds), float(duration_seconds)
        return remaining_seconds, float(progress_seconds), float(duration_seconds)

    def _estimate_remaining_current_ad_seconds(
        self,
        samples: list[object],
        *,
        debug_page_source_path: Path | None = None,
    ) -> tuple[float | None, str, float | None, float | None]:
        sample_remaining = self._remaining_current_ad_seconds(samples)
        if sample_remaining is not None:
            return sample_remaining, "samples", None, None
        debug_remaining, debug_progress, debug_duration = (
            self._remaining_current_ad_seconds_from_debug_xml(debug_page_source_path)
        )
        if debug_remaining is not None:
            return debug_remaining, "debug_xml", debug_progress, debug_duration
        return None, "none", debug_progress, debug_duration

    @staticmethod
    def _sample_has_explicit_ad_timing(sample: object) -> bool:
        if not getattr(sample, "ad_detected", False):
            return False
        return isinstance(getattr(sample, "ad_progress_seconds", None), (int, float)) or isinstance(
            getattr(sample, "ad_duration_seconds", None),
            (int, float),
        )

    def _recorder_wait_cap_seconds(
        self,
        *,
        clicked: bool | None,
        remaining_source: str,
        debug_duration: float | None,
        samples: list[object],
    ) -> float:
        base_cap = 55.0 if not clicked else 45.0
        if remaining_source != "debug_xml":
            return base_cap
        if any(self._sample_has_explicit_ad_timing(sample) for sample in samples):
            return base_cap
        if not isinstance(debug_duration, (int, float)) or float(debug_duration) > 120.0:
            return min(base_cap, 12.0 if clicked else 18.0)
        if float(debug_duration) > 60.0:
            return min(base_cap, 18.0 if clicked else 24.0)
        return min(base_cap, 24.0 if clicked else 30.0)

    def _log_recorder_decision(
        self,
        *,
        label: str,
        decision: str,
        samples: list[object],
        recording_handle: object | None,
        debug_page_source_path: Path | None = None,
    ) -> None:
        runtime_seconds = self._recording_runtime_seconds(recording_handle)
        remaining_seconds, remaining_source, debug_progress, debug_duration = (
            self._estimate_remaining_current_ad_seconds(
                samples,
                debug_page_source_path=debug_page_source_path,
            )
        )
        logger.info(
            "recorder[%s]: %s runtime=%s remaining=%s source=%s "
            "debug_progress=%s debug_duration=%s samples=%s",
            label,
            decision,
            f"{runtime_seconds:.1f}s" if runtime_seconds is not None else "n/a",
            f"{remaining_seconds:.1f}s" if remaining_seconds is not None else None,
            remaining_source,
            debug_progress,
            debug_duration,
            self._sample_debug_summary(samples),
        )

    async def _finalize_recording_after_cta(
        self,
        *,
        label: str,
        recorder: AndroidScreenRecorder,
        recording_handle: object,
        samples: list[object],
        debug_page_source_path: Path | None = None,
        clicked: bool | None = None,
        returned_to_youtube: bool | None = None,
        elapsed_since_samples: float = 0.0,
    ) -> tuple[str | None, float | None]:
        self._log_recorder_decision(
            label=label,
            decision="pre_stop_after_cta",
            samples=samples,
            recording_handle=recording_handle,
            debug_page_source_path=debug_page_source_path,
        )
        (
            remaining_seconds,
            remaining_source,
            debug_progress,
            debug_duration,
        ) = self._estimate_remaining_current_ad_seconds(
            samples,
            debug_page_source_path=debug_page_source_path,
        )
        # For "samples" source: progress was last known at end-of-samples, so subtract
        # elapsed_since_samples to get current remaining.
        # For "debug_xml" source: debug_progress was captured during the CTA probe, which
        # already happened inside elapsed_since_samples window — so remaining = duration -
        # debug_progress is already close to current. Don't subtract elapsed again.
        if remaining_seconds is not None and remaining_source == "samples" and elapsed_since_samples > 0.0:
            remaining_seconds = max(0.0, remaining_seconds - elapsed_since_samples)
        if remaining_seconds is not None and remaining_seconds > 1.0:
            cap_seconds = self._recorder_wait_cap_seconds(
                clicked=clicked,
                remaining_source=remaining_source,
                debug_duration=debug_duration,
                samples=samples,
            )
            sleep_seconds = min(remaining_seconds, cap_seconds)
            logger.info(
                "recorder[%s]: waiting %.1fs for ad to finish "
                "(clicked=%s, returned=%s, remaining=%.1fs, cap=%.1fs, "
                "elapsed_since_samples=%.1fs, source=%s, debug_progress=%s, debug_duration=%s)",
                label,
                sleep_seconds,
                clicked,
                returned_to_youtube,
                remaining_seconds,
                cap_seconds,
                elapsed_since_samples,
                remaining_source,
                debug_progress,
                debug_duration,
            )
            await asyncio.sleep(sleep_seconds)
        else:
            logger.info(
                "recorder[%s]: no remaining ad time "
                "(clicked=%s, returned=%s, remaining=%s, source=%s, "
                "debug_progress=%s, debug_duration=%s)",
                label,
                clicked,
                returned_to_youtube,
                remaining_seconds,
                remaining_source,
                debug_progress,
                debug_duration,
            )

        recorded_video_path = await self._stop_recording_handle(
            recorder=recorder,
            recording_handle=recording_handle,
        )
        recorded_video_duration_seconds = self._probe_recorded_video_duration(
            recorded_video_path
        )
        print(
            f"[android-session] recorder[{label}]: stop_done path={recorded_video_path} "
            f"rec_dur={recorded_video_duration_seconds}",
            flush=True,
        )
        last_ad_duration = next(
            (
                getattr(sample, "ad_duration_seconds", None)
                for sample in reversed(samples)
                if isinstance(getattr(sample, "ad_duration_seconds", None), (int, float))
            ),
            None,
        )
        logger.info(
            "recorder[%s]: stopped, recorded_duration=%.1fs path=%s ad_duration=%s",
            label,
            recorded_video_duration_seconds or 0.0,
            recorded_video_path,
            last_ad_duration,
        )
        return recorded_video_path, recorded_video_duration_seconds

    async def _discard_recording_handle(
        self,
        *,
        label: str,
        topic: str,
        recorder: AndroidScreenRecorder,
        recording_handle: object,
        reason: str,
    ) -> None:
        """Stop and delete a recorder segment that must not be attached to an ad.

        The midroll loop starts recording before probing the next watch chunk. If
        that chunk turns out to be a duplicate/residual ad, keeping the recorder
        alive pollutes the next real capture with the old ad or regular video.
        """
        try:
            local_video = await recorder.stop(recording_handle, keep_local=True)
        except Exception as exc:
            logger.warning(
                "recorder[%s]: failed to discard segment topic=%s reason=%s error=%s",
                label,
                topic,
                reason,
                exc,
            )
            return
        if local_video is None:
            logger.info(
                "recorder[%s]: discarded empty segment topic=%s reason=%s",
                label,
                topic,
                reason,
            )
            return
        logger.info(
            "recorder[%s]: discarded segment topic=%s reason=%s path=%s",
            label,
            topic,
            reason,
            local_video,
        )
        with contextlib.suppress(Exception):
            local_video.unlink(missing_ok=True)

    @staticmethod
    def _normalize_watched_ad_identity_value(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().casefold().split())
        return normalized or None

    def _watched_ad_identity_key(
        self, ad: dict[str, object] | None,
    ) -> str | None:
        from urllib.parse import parse_qs, urlsplit

        _REDIRECT_HOSTS = {
            "googleadservices.com", "www.googleadservices.com",
            "google.com", "www.google.com",
            "doubleclick.net", "www.doubleclick.net", "googleads.g.doubleclick.net",
        }

        if not isinstance(ad, dict):
            return None

        capture = ad.get("capture") if isinstance(ad.get("capture"), dict) else {}

        def _host_path_key(url: object) -> str | None:
            if not isinstance(url, str) or not url.strip():
                return None
            try:
                parts = urlsplit(url.strip() if "://" in url else f"https://{url}")
            except Exception:
                return None
            host = (parts.netloc or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if not host or host in _REDIRECT_HOSTS:
                return None
            return f"url:{host}{parts.path or ''}"

        def _ai_key(url: object) -> str | None:
            if not isinstance(url, str) or not url.strip():
                return None
            try:
                parts = urlsplit(url.strip())
                ai = (parse_qs(parts.query).get("ai") or [""])[0]
            except Exception:
                return None
            return f"ai:{ai}" if ai else None

        url_candidates = (
            ad.get("display_url"),
            ad.get("landing_url"),
            capture.get("landing_scrape_url"),
            capture.get("pre_click_display_url"),
        )
        for candidate in url_candidates:
            key = _host_path_key(candidate)
            if key:
                return key
        for candidate in url_candidates:
            key = _ai_key(candidate)
            if key:
                return key

        advertiser = self._normalize_watched_ad_identity_value(
            ad.get("advertiser_domain")
        )
        headline = self._normalize_watched_ad_identity_value(
            ad.get("headline_text") or capture.get("headline_text")
        )
        if advertiser or headline:
            return f"meta:{advertiser or '-'}|{headline or '-'}"
        return None

    def _dedupe_watched_ads(
        self, watched_ads: list[dict[str, object]],
    ) -> None:
        """Collapse duplicate creatives across the whole session.

        YouTube frequently replays the same ad across topic runs; our per-topic
        dedup inside the midroll loop doesn't catch those. We identify the same
        creative by a stable watched-ad identity key and keep only the first
        occurrence in the session-level summary."""
        seen: dict[str, int] = {}
        to_remove: list[int] = []
        for idx, ad in enumerate(watched_ads):
            key = self._watched_ad_identity_key(ad)
            if not key:
                continue
            if key in seen:
                to_remove.append(idx)
            else:
                seen[key] = idx
        for idx in reversed(to_remove):
            del watched_ads[idx]
        if to_remove:
            print(f"[android-session] dedup: removed {len(to_remove)} duplicate ads", flush=True)

    def _midroll_continues_previous_ad(
        self,
        *,
        previous_ad: dict[str, object] | None,
        extension_samples: list[object],
    ) -> bool:
        if not isinstance(previous_ad, dict) or not extension_samples:
            return False

        previous_progress = self._coerce_float(previous_ad.get("ad_last_progress_seconds"))
        if previous_progress is None:
            return False

        current_progress_values = [
            float(getattr(sample, "ad_progress_seconds"))
            for sample in extension_samples
            if getattr(sample, "ad_detected", False)
            and isinstance(getattr(sample, "ad_progress_seconds", None), (int, float))
        ]
        if not current_progress_values:
            return False

        current_progress = max(current_progress_values)
        if current_progress + 1.0 < previous_progress:
            return False

        previous_duration = self._coerce_float(previous_ad.get("ad_duration_seconds"))
        current_duration_values = [
            float(getattr(sample, "ad_duration_seconds"))
            for sample in extension_samples
            if getattr(sample, "ad_detected", False)
            and isinstance(getattr(sample, "ad_duration_seconds", None), (int, float))
        ]
        current_duration = max(current_duration_values) if current_duration_values else None
        if (
            previous_duration is not None
            and current_duration is not None
            and abs(current_duration - previous_duration) > 2.0
        ):
            return False

        if current_progress >= previous_progress + 8.0:
            return True
        return False

    def _merge_midroll_continuation_into_previous_ad(
        self,
        *,
        previous_ad: dict[str, object],
        extension_samples: list[object],
    ) -> None:
        current_progress_values = [
            float(getattr(sample, "ad_progress_seconds"))
            for sample in extension_samples
            if getattr(sample, "ad_detected", False)
            and isinstance(getattr(sample, "ad_progress_seconds", None), (int, float))
        ]
        if current_progress_values:
            previous_progress = self._coerce_float(previous_ad.get("ad_last_progress_seconds")) or 0.0
            previous_ad["ad_last_progress_seconds"] = max(previous_progress, max(current_progress_values))

        current_duration_values = [
            float(getattr(sample, "ad_duration_seconds"))
            for sample in extension_samples
            if getattr(sample, "ad_detected", False)
            and isinstance(getattr(sample, "ad_duration_seconds", None), (int, float))
        ]
        if current_duration_values and previous_ad.get("ad_duration_seconds") in (None, 0, 0.0):
            previous_ad["ad_duration_seconds"] = max(current_duration_values)

        updated_progress = self._coerce_float(previous_ad.get("ad_last_progress_seconds"))
        updated_duration = self._coerce_float(previous_ad.get("ad_duration_seconds"))
        if (
            updated_progress is not None
            and updated_duration is not None
            and updated_progress >= max(1.0, updated_duration - 1.0)
        ):
            previous_ad["completed"] = True

    def _backfill_advertiser_from_landing_scrape(
        self, watched_ads: list[dict[str, object]],
    ) -> None:
        """After Playwright scrapes the landing URL, the final redirect
        destination is stored in capture['landing_scrape_url']. Use it to
        resolve advertiser_domain + headline_text for ads where the original
        aclk/pagead link couldn't be unwrapped statically."""
        from urllib.parse import urlsplit
        from app.services.mobile_app.android.youtube.ad_record import _SUPPRESSED_ADVERTISER_HOSTS

        def _host(value: object) -> str:
            if not isinstance(value, str) or not value.strip():
                return ""
            raw = value.strip()
            try:
                parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
            except Exception:
                return ""
            host = (parsed.netloc or "").lower()
            return host[4:] if host.startswith("www.") else host

        def _set_final_landing_identity(
            *,
            ad: dict[str, object],
            capture: dict[str, object],
            final_url: str,
            final_host: str,
            note: str | None,
        ) -> None:
            if note:
                notes = capture.setdefault("capture_notes", [])
                if isinstance(notes, list) and note not in notes:
                    notes.append(note)
            ad["advertiser_domain"] = final_host
            ad["display_url"] = final_url
            ad["display_url_decoded"] = final_url
            ad["landing_url"] = final_url
            ad["landing_urls"] = [final_url]
            capture["display_url"] = final_url
            capture["landing_url"] = final_url
            cta_host = _host(ad.get("cta_href") or capture.get("cta_href"))
            if not cta_host or cta_host in _SUPPRESSED_ADVERTISER_HOSTS:
                ad["cta_href"] = final_url
                capture["cta_href"] = final_url
            scrape_title = capture.get("landing_scrape_title")
            if isinstance(scrape_title, str) and scrape_title.strip():
                title = scrape_title.strip()
                ad["headline_text"] = title
                capture["headline_text"] = title

        for ad in watched_ads:
            if not isinstance(ad, dict):
                continue
            capture = ad.get("capture") if isinstance(ad.get("capture"), dict) else {}
            final_url = capture.get("landing_scrape_url") if isinstance(capture, dict) else None
            final_host = _host(final_url)
            if final_host and final_host not in _SUPPRESSED_ADVERTISER_HOSTS and isinstance(final_url, str):
                existing_hosts = {
                    _host(ad.get("advertiser_domain")),
                    _host(ad.get("display_url")),
                    _host(capture.get("display_url")),
                    _host(capture.get("pre_click_display_url")),
                }
                existing_hosts.discard("")
                existing_hosts = {
                    host for host in existing_hosts if host not in _SUPPRESSED_ADVERTISER_HOSTS
                }
                conflicting_hosts = sorted(host for host in existing_hosts if host != final_host)
                if not ad.get("advertiser_domain") or conflicting_hosts:
                    note = None
                    if conflicting_hosts:
                        note = (
                            "mixed_ad_identity_detected:landing_scrape_host_mismatch:"
                            f"{','.join(conflicting_hosts)}->{final_host}"
                        )
                    _set_final_landing_identity(
                        ad=ad,
                        capture=capture,
                        final_url=final_url,
                        final_host=final_host,
                        note=note,
                    )
            if not ad.get("headline_text") and isinstance(capture, dict):
                scrape_title = capture.get("landing_scrape_title")
                if isinstance(scrape_title, str) and scrape_title.strip():
                    scrape_host = _host(capture.get("landing_scrape_url"))
                    if scrape_host not in _SUPPRESSED_ADVERTISER_HOSTS:
                        ad["headline_text"] = scrape_title.strip()
                        capture["headline_text"] = scrape_title.strip()

    def _cleanup_irrelevant_ad_videos(self, watched_ads: list[dict[str, object]]) -> None:
        """Retain debug artifacts for not_relevant ads.

        Media visibility is already handled by the API/frontend serializers, so
        deleting files here only makes post-run debugging harder and can make it
        look like captures were never recorded.
        """
        _ = watched_ads

    def _should_skip_post_ad_settle(self, built_ad: object | None) -> bool:
        if not isinstance(built_ad, dict):
            return False
        if bool(built_ad.get("completed")):
            return True
        watched_seconds = built_ad.get("watched_seconds")
        try:
            watched_value = float(watched_seconds)
        except (TypeError, ValueError):
            return False
        min_watch_threshold = max(
            12.0,
            float(self._config.android_app.probe_ad_min_watch_seconds) - 2.0,
        )
        if watched_value >= min_watch_threshold:
            return True
        ad_duration_seconds = built_ad.get("ad_duration_seconds")
        try:
            duration_value = float(ad_duration_seconds)
        except (TypeError, ValueError):
            duration_value = None
        if duration_value is not None and watched_value >= max(1.0, duration_value - 1.0):
            return True
        return False

    @staticmethod
    def _can_attempt_engagement(samples: list[object]) -> bool:
        return AndroidYouTubeProbeRunner._engagement_gate_reason(samples) == "ok"

    @staticmethod
    def _sample_has_active_ad_ui(sample: object) -> bool:
        if getattr(sample, "skip_available", False):
            return True
        if isinstance(getattr(sample, "ad_progress_seconds", None), (int, float)):
            return True
        if isinstance(getattr(sample, "ad_duration_seconds", None), (int, float)):
            return True
        has_main_watch_progress = isinstance(getattr(sample, "progress_seconds", None), (int, float))
        has_main_watch_surface = bool(
            getattr(sample, "player_visible", False)
            and getattr(sample, "watch_panel_visible", False)
        )
        ad_signal_labels = [
            str(value).casefold()
            for value in (getattr(sample, "ad_signal_labels", []) or [])
            if str(value).strip()
        ]
        has_active_signal = any(
            token in label
            for label in ad_signal_labels
            for token in (
                "visit advertiser",
                "learn more",
                "like ad",
                "share ad",
                "close ad panel",
                "install",
                "buy now",
                "shop now",
                "відвідати",
                "подробнее",
            )
        )
        if has_active_signal:
            return True
        ad_cta_text = str(getattr(sample, "ad_cta_text", "") or "").strip()
        if not ad_cta_text:
            return False
        if has_main_watch_surface and has_main_watch_progress:
            return False
        return True

    @staticmethod
    def _engagement_gate_reason(samples: list[object] | None) -> str:
        if not samples:
            return "no_samples"
        for sample in reversed(samples):
            if getattr(sample, "is_reel_surface", False):
                return "short_form"
            if not (
                getattr(sample, "player_visible", False)
                and getattr(sample, "watch_panel_visible", False)
            ):
                continue
            if bool(getattr(sample, "error_messages", [])):
                return "player_errors"
            if AndroidYouTubeProbeRunner._sample_has_active_ad_ui(sample):
                return "ad_detected"
            return "ok"
        if any(bool(getattr(sample, "error_messages", [])) for sample in samples):
            return "player_errors"
        if any(getattr(sample, "is_reel_surface", False) for sample in samples):
            return "short_form"
        return "no_watch_surface"

    async def _run_engagement_safe(
        self,
        *,
        driver: object,
        adb_serial: str | None,
        topic: str,
        opened_title: str | None,
        notes: list[str],
        note_prefix: str,
    ):
        try:
            engagement = AndroidYouTubeEngagementController(
                driver,
                self._config.android_app,
                adb_serial=adb_serial,
            )
            engagement_result = await asyncio.wait_for(
                engagement.engage(
                    topic=topic,
                    opened_title=opened_title,
                ),
                timeout=14.0,
            )
        except asyncio.TimeoutError:
            notes.append(f"{note_prefix}:TimeoutError:engagement_timeout")
            return None
        except Exception as exc:
            notes.append(f"{note_prefix}:{type(exc).__name__}:{exc}")
            return None

        notes.extend(engagement_result.notes)
        return engagement_result


class AndroidYouTubeSessionRunner(AndroidYouTubeProbeRunner):
    _SEMANTIC_TOKEN_GROUPS = (
        frozenset({"invest", "trad", "profit", "income", "earn"}),
        frozenset({"crypto", "cryptocurrency"}),
    )
    _BROAD_QUERY_TOKENS = frozenset(
        {
            "crypto",
            "cryptocurrency",
            "bitcoin",
            "platform",
            "review",
            "guide",
            "tutorial",
            "course",
            "strategy",
            "strateg",
            "path",
            "automat",
        }
    )

    async def run(
        self,
        *,
        topics: list[str],
        duration_minutes: int | None = None,
        avd_name: str | None = None,
        proxy_url: str | None = None,
        adspower_profile_id: str | None = None,
        headless: bool | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> AndroidSessionRunResult:
        resolved_topics = [topic.strip() for topic in topics if topic.strip()]
        if not resolved_topics:
            raise ValueError("Android session runner requires at least one topic")
        # Topic count is now governed by the hard cap inside the main loop
        # (max(1, (duration_minutes - 2) // 5)) which stops starting new topics
        # once the budget is exhausted. The old upfront trim is removed because
        # it used a different formula, shuffled topic order unpredictably, and
        # ran in parallel with the new cap — making behaviour non-deterministic.

        resolved_avd_name = avd_name or self._config.android_app.default_avd_name
        snapshot_name = (
            self._config.android_app.runtime_snapshot_name
            if self._config.android_app.emulator_use_snapshots
            else None
        )
        device = None
        session = None
        navigator = None
        watcher = None
        dialog_watchdog_task: asyncio.Task[None] | None = None
        dialog_watchdog_stop: asyncio.Event | None = None
        launcher_tripwire: asyncio.Event | None = None
        proxy_bridge_handle = None
        ad_analysis = AndroidAdAnalysisCoordinator(self._config.gemini, self._config.storage)
        proxy_notes: list[str] = []
        emulator_http_proxy = None
        host_http_proxy = None
        landing_scraper: AndroidLandingPageScraper | None = None
        try:
            (
                emulator_http_proxy,
                host_http_proxy,
                proxy_notes,
                proxy_bridge_handle,
            ) = await self._prepare_emulator_proxy(
                proxy_url=proxy_url,
                adspower_profile_id=adspower_profile_id,
            )
            topic_results: list[AndroidSessionTopicResult] = []
            watched_ads: list[dict[str, object]] = []
            _initial_rx_bytes: int = 0

            async def _read_rx_bytes() -> int:
                """Read total received bytes from emulator via ADB."""
                try:
                    if device is None:
                        return 0
                    adb_bin = "adb"
                    proc = await asyncio.create_subprocess_exec(
                        adb_bin, "-s", device.adb_serial,
                        "shell", "cat", "/proc/net/dev",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                    total = 0
                    for line in stdout.decode(errors="ignore").splitlines():
                        line = line.strip()
                        if ":" not in line or line.startswith("Inter") or line.startswith("face"):
                            continue
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                total += int(parts[1])
                            except ValueError:
                                pass
                    return total
                except Exception:
                    return 0

            async def _notify_ads_progress(event_name: str) -> None:
                if on_progress is None:
                    return
                try:
                    _ads = [
                        {**ad, "position": idx + 1}
                        for idx, ad in enumerate(watched_ads)
                    ]
                    await on_progress(
                        event=event_name,
                        watched_ads=_ads,
                        watched_ads_count=len(_ads),
                    )
                except Exception:
                    pass

            async def _notify_ad_captured() -> None:
                await _notify_ads_progress("ad_captured")

            async def _notify_ad_updated(_: dict[str, object]) -> None:
                await _notify_ads_progress("ad_updated")

            landing_scraper = AndroidLandingPageScraper(
                self._config.storage,
                proxy_url=host_http_proxy,
                on_result=_notify_ad_updated,
            )
            await landing_scraper.start()

            started_at = time.monotonic()
            session_artifact_path = self._build_session_artifact_path()
            infra_failure_streak = 0
            surface_failure_streak = 0
            deadline = (
                started_at + (duration_minutes * 60)
                if duration_minutes is not None and duration_minutes > 0
                else None
            )
            topic_cursor = 0
            topics_cycled_once = False  # True after first full pass through all topics
            topic_cap_logged = False
            last_adb_serial: str | None = None
            last_server_url: str | None = None
            last_reused_running_device = False
            last_started_local_appium = False
            print(
                f"[android-session] start avd={resolved_avd_name} topics={len(resolved_topics)} duration_minutes={duration_minutes or 0} proxy={emulator_http_proxy or 'none'}",
                flush=True,
            )

            while True:
                active_topics = self._active_topic_pool(
                    topics=resolved_topics,
                    duration_minutes=duration_minutes,
                )
                if len(active_topics) < len(resolved_topics) and not topic_cap_logged:
                    topic_cap_logged = True
                    print(
                        "[android-session] topic_pool:capped "
                        f"active={len(active_topics)} requested={len(resolved_topics)} "
                        f"duration_minutes={duration_minutes}",
                        flush=True,
                    )
                if deadline is not None and topic_results and time.monotonic() >= deadline:
                    break
                if deadline is not None:
                    remaining_seconds = deadline - time.monotonic()
                    topic_buffer = self._next_topic_start_buffer_seconds(
                        topics=active_topics,
                        topic_results=topic_results,
                        topics_cycled_once=topics_cycled_once,
                    )
                    if remaining_seconds <= topic_buffer:
                        pending_uncovered = len(
                                [
                                    candidate
                                    for candidate in active_topics
                                    if candidate not in self._covered_topics(topic_results)
                                ]
                        )
                        print(
                            "[android-session] stop:no_time_for_next_topic "
                            f"remaining_seconds={max(0, int(remaining_seconds))} "
                            f"topic_buffer={int(topic_buffer)} "
                            f"pending_uncovered={pending_uncovered}",
                            flush=True,
                        )
                        break
                if session is None or device is None or navigator is None or watcher is None:
                    print("[android-session] device_or_session:acquire", flush=True)
                    try:
                        launch_options = AndroidEmulatorLaunchOptions(
                            headless=(
                                self._config.android_app.emulator_headless
                                if headless is None
                                else headless
                            ),
                            gpu_mode=self._config.android_app.emulator_gpu_mode,
                            accel_mode=self._config.android_app.emulator_accel_mode,
                            http_proxy=emulator_http_proxy,
                            load_snapshot=self._config.android_app.emulator_use_snapshots,
                            save_snapshot=False,
                            snapshot_name=snapshot_name,
                            force_snapshot_load=bool(snapshot_name),
                            skip_adb_auth=self._config.android_app.emulator_skip_adb_auth,
                            force_stop_running=self._config.android_app.emulator_force_restart_before_run,
                        )
                        if device is None:
                            print("[android-session] acquire:ensure_device", flush=True)
                            try:
                                device = await asyncio.wait_for(
                                    self._runtime.avd_manager.ensure_device(
                                        avd_name=resolved_avd_name,
                                        launch=launch_options,
                                    ),
                                    timeout=(
                                        self._config.android_app.emulator_start_timeout_seconds
                                        + self._config.android_app.device_ready_timeout_seconds
                                        + 30
                                    ),
                                )
                            except asyncio.TimeoutError as exc:
                                raise AndroidUiError("ensure_device timed out") from exc
                            print(
                                f"[android-session] acquire:device_ready serial={device.adb_serial} reused={str(device.reused_running_device).lower()}",
                                flush=True,
                            )
                        else:
                            print(
                                f"[android-session] acquire:reuse_device serial={device.adb_serial}",
                                flush=True,
                            )
                            # Proactively reset UiAutomator2 helpers before reusing the device
                            # to prevent "UiAutomationService already registered" crashes.
                            with contextlib.suppress(Exception):
                                await self._runtime.appium_provider.recover_session_environment(
                                    adb_serial=device.adb_serial,
                                )
                        print("[android-session] acquire:create_session", flush=True)
                        try:
                            session = await asyncio.wait_for(
                                self._runtime.appium_provider.create_youtube_session(
                                    adb_serial=device.adb_serial,
                                    avd_name=device.avd_name,
                                ),
                                timeout=(
                                    self._config.android_app.device_ready_timeout_seconds
                                    + self._config.android_app.appium_create_session_timeout_seconds
                                    + self._config.android_app.appium_validate_session_timeout_seconds
                                    + 45
                                ),
                            )
                        except asyncio.TimeoutError as exc:
                            raise AndroidUiError("create_youtube_session timed out") from exc
                        last_adb_serial = device.adb_serial
                        last_server_url = session.server_url
                        last_reused_running_device = device.reused_running_device
                        last_started_local_appium = session.started_local_server
                        navigator = AndroidYouTubeNavigator(
                            session.driver,
                            self._config.android_app,
                            adb_serial=device.adb_serial,
                        )
                        watcher = AndroidYouTubeWatcher(
                            session.driver,
                            self._config.android_app,
                            adb_serial=device.adb_serial,
                        )
                        acquire_notes: list[str] = []
                        print("[android-session] stage:ensure_app_ready", flush=True)
                        await self._ensure_app_ready_with_timeout(
                            navigator=navigator,
                            topic_notes=acquire_notes,
                            stage_label="ensure_app_ready",
                            launcher_tripwire=None,
                        )
                        for note in acquire_notes:
                            print(f"[android-session] acquire_note:{note}", flush=True)
                        dialog_watchdog_stop = asyncio.Event()
                        launcher_tripwire = asyncio.Event()
                        dialog_watchdog_task = asyncio.create_task(
                            self._system_dialog_watchdog_loop(
                                navigator=navigator,
                                stop_event=dialog_watchdog_stop,
                                tripwire_event=launcher_tripwire,
                                log_prefix="[android-session]",
                            )
                        )
                        # Skip home reset — submit_search will deeplink directly to
                        # the first topic from whatever surface YouTube is on.
                        acquire_notes.append("reset_to_home_initial_skipped:deeplink_will_handle")
                        print("[android-session] stage:reset_to_home_initial:skipped", flush=True)
                        if _initial_rx_bytes == 0:
                            _initial_rx_bytes = await _read_rx_bytes()
                        print(
                            f"[android-session] device_or_session:ready serial={device.adb_serial} reused={str(device.reused_running_device).lower()}",
                            flush=True,
                        )
                    except Exception as exc:
                        print(
                            f"[android-session] device_or_session:error error={type(exc).__name__}:{exc}",
                            flush=True,
                        )
                        if not self._is_infrastructure_failure(str(exc)):
                            raise
                        infra_failure_streak += 1
                        if device is not None:
                            acquire_diag_notes = self._write_infra_diagnostic_artifacts(
                                topic="acquire",
                                stage_label="device_or_session_error",
                                adb_serial=device.adb_serial,
                            )
                            for note in acquire_diag_notes:
                                print(f"[android-session] acquire_note:{note}", flush=True)
                        if session is not None:
                            await self._stop_system_dialog_watchdog(
                                stop_event=dialog_watchdog_stop,
                                task=dialog_watchdog_task,
                            )
                            dialog_watchdog_stop = None
                            dialog_watchdog_task = None
                            launcher_tripwire = None
                            with contextlib.suppress(Exception):
                                if (
                                    device is not None
                                    and self._is_session_only_infrastructure_failure(str(exc))
                                ):
                                    await self._runtime.appium_provider.close_broken_session(
                                        session,
                                        adb_serial=device.adb_serial,
                                    )
                                else:
                                    await self._runtime.appium_provider.close_session(session)
                        if (
                            device is not None
                            and self._config.android_app.emulator_stop_after_run
                            and not self._is_session_only_infrastructure_failure(str(exc))
                        ):
                            await self._cleanup_device_after_failure(device)
                        session = None
                        _device_gone = self._is_device_gone_failure(str(exc))
                        if not self._is_session_only_infrastructure_failure(str(exc)) or _device_gone:
                            device = None
                        navigator = None
                        watcher = None
                        print(
                            f"[android-session] checkpoint acquire_failed infra_failure_streak={infra_failure_streak}",
                            flush=True,
                        )
                        if infra_failure_streak >= 5:
                            break
                        await asyncio.sleep(2)
                        continue
                topic = active_topics[topic_cursor % len(active_topics)]
                topic_cursor += 1
                if topic_cursor >= len(active_topics):
                    topics_cycled_once = True
                print(
                    f"[android-session] topic:start idx={len(topic_results) + 1} topic={topic}",
                    flush=True,
                )
                if on_progress is not None:
                    try:
                        await on_progress(
                            event="topic_start",
                            current_topic=topic,
                            topics_searched=[tr.topic for tr in topic_results],
                            total_duration_seconds=max(0, int(time.monotonic() - started_at)),
                        )
                    except Exception:
                        pass
                topic_notes: list[str] = []
                opened_title: str | None = None
                current_watch_started_at: float | None = None
                topic_started_at = time.monotonic()
                watch_verified = False
                watch_seconds: float | None = None
                watch_ad_detected = False
                target_watch_seconds: float = 0.0
                liked = False
                already_liked = False
                subscribed = False
                already_subscribed = False
                comments_glanced = False
                topic_watched_ads: list[dict[str, object]] = []
                discard_topic_result = False
                retry_same_topic = False
                pending_midroll_result: object | None = None
                pending_midroll_samples_ended_monotonic: float | None = None

                try:
                    _net_ok = await self._ensure_topic_network_ready(
                        adb_serial=device.adb_serial if device is not None else None,
                        topic=topic,
                        topic_notes=topic_notes,
                    )
                    if not _net_ok:
                        topic_notes.append("network_check_failed:soft_proceed")

                    results_ready_confirmed = True
                    print("[android-session] stage:submit_search", flush=True)
                    await self._submit_search_with_timeout(
                        navigator=navigator,
                        topic=topic,
                        topic_notes=topic_notes,
                        stage_label="submit_search",
                        launcher_tripwire=launcher_tripwire,
                    )
                    print("[android-session] stage:wait_for_results", flush=True)
                    try:
                        await self._wait_for_results_with_timeout(
                            navigator=navigator,
                            topic=topic,
                            topic_notes=topic_notes,
                            stage_label="wait_for_results",
                            launcher_tripwire=launcher_tripwire,
                        )
                    except Exception as exc:
                        if "Failed to detect native YouTube results list" not in str(exc):
                            raise
                        results_ready_confirmed = False
                        topic_notes.append("wait_for_results_failed:first_attempt")
                        self._append_stage_debug_artifacts(
                            driver=session.driver if session is not None else None,
                            topic_notes=topic_notes,
                            topic=topic,
                            stage_label="wait_for_results_first_attempt",
                            adb_serial=device.adb_serial if device is not None else None,
                        )
                        print(
                            "[android-session] topic:wait_for_results_failed attempt=1",
                            flush=True,
                        )
                        wait_results_ad_captured = (
                            await self._probe_unaccepted_watch_surface_for_ad(
                                driver=session.driver if session is not None else None,
                                navigator=navigator,
                                watcher=watcher,
                                adb_serial=device.adb_serial if device is not None else None,
                                topic=topic,
                                stage_label="wait_for_results_first_attempt",
                                topic_notes=topic_notes,
                                ad_analysis=ad_analysis,
                                landing_scraper=landing_scraper,
                                watched_ads=watched_ads,
                                topic_watched_ads=topic_watched_ads,
                                notify_ad_captured=_notify_ad_captured,
                            )
                        )
                        if wait_results_ad_captured:
                            print(
                                f"[android-session] stage:wait_for_results_first_attempt_ad_done total_ads={len(watched_ads)}",
                                flush=True,
                            )
                    if not results_ready_confirmed:
                        with contextlib.suppress(Exception):
                            if await navigator.has_query_ready_surface_via_adb(topic):
                                results_ready_confirmed = True
                                topic_notes.append("wait_for_results_recovered:surface_ready_adb")
                        if not results_ready_confirmed:
                            with contextlib.suppress(Exception):
                                if await navigator.has_query_ready_surface(topic):
                                    results_ready_confirmed = True
                                    topic_notes.append("wait_for_results_recovered:surface_ready")
                    if results_ready_confirmed and not opened_title:
                        provisional_opened_title = (
                            await self._provisional_watch_title_with_hard_timeout(
                                navigator=navigator,
                                topic=topic,
                                topic_notes=topic_notes,
                                stage_label="opened_title_after_wait_for_results",
                                timeout_seconds=2.0,
                            )
                        )
                        if provisional_opened_title:
                            opened_title = provisional_opened_title
                            topic_notes.append("opened_title_recovered_after_wait_for_results")
                    if results_ready_confirmed and not opened_title:
                        delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
                            navigator=navigator,
                            topic=topic,
                            topic_notes=topic_notes,
                            stage_label="opened_title_after_wait_for_results_delay",
                            timeout_seconds=2.0,
                        )
                        if delayed_opened_title:
                            opened_title = delayed_opened_title
                            topic_notes.append(
                                "opened_title_recovered_after_wait_for_results_delay"
                            )
                    if results_ready_confirmed and not opened_title:
                        print("[android-session] stage:open_first_result", flush=True)
                        opened_title = await self._open_first_result_with_timeout(
                            navigator=navigator,
                            topic=topic,
                            topic_notes=topic_notes,
                            stage_label="open_first_result",
                            launcher_tripwire=launcher_tripwire,
                        )
                    elif not results_ready_confirmed and not opened_title:
                        topic_notes.append("open_first_result_skipped:no_results_surface")
                        topic_notes.append("open_result_failure:no_surface:first_attempt")
                    if not opened_title:
                        self._append_open_result_diagnostics_notes(
                            topic_notes=topic_notes,
                            attempt_label="first_attempt",
                            diagnostics=await navigator.last_open_result_diagnostics(),
                        )
                        delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
                            navigator=navigator,
                            topic=topic,
                            topic_notes=topic_notes,
                            stage_label="await_current_watch_title_first_attempt",
                            timeout_seconds=8.5,
                        )
                        if delayed_opened_title:
                            opened_title = delayed_opened_title
                            topic_notes.append("opened_title_recovered_after_delay:first_attempt")
                    if opened_title and self._opened_title_is_provisional(opened_title, topic):
                        topic_notes.append("opened_title_provisional:first_attempt")
                        delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
                            navigator=navigator,
                            topic=topic,
                            topic_notes=topic_notes,
                            stage_label="await_current_watch_title_first_attempt_provisional",
                            timeout_seconds=5.0,
                        )
                        if (
                            delayed_opened_title
                            and not self._opened_title_is_provisional(delayed_opened_title, topic)
                        ):
                            opened_title = delayed_opened_title
                            topic_notes.append("opened_title_resolved:first_attempt")
                        elif (
                            any(
                                note in topic_notes
                                for note in (
                                    "opened_title_provisional_allowed:watch_activity",
                                    "opened_title_provisional_allowed:watch_surface",
                                )
                            )
                            and await navigator.has_watch_surface_for_query(topic)
                        ):
                            topic_notes.append("opened_title_provisional_kept:first_attempt")
                        else:
                            opened_title = None
                            topic_notes.append("opened_title_provisional_unresolved:first_attempt")
                    if opened_title:
                        opened_title = await self._reconcile_opened_title_with_topic(
                            navigator=navigator,
                            topic=topic,
                            opened_title=opened_title,
                            topic_notes=topic_notes,
                            attempt_label="first_attempt",
                        )
                    if not opened_title:
                        topic_notes.append("no_result_opened:first_attempt")
                        self._append_stage_debug_artifacts(
                            driver=session.driver if session is not None else None,
                            topic_notes=topic_notes,
                            topic=topic,
                            stage_label="open_first_result_first_attempt",
                            adb_serial=device.adb_serial if device is not None else None,
                        )
                        print("[android-session] topic:no_result_opened attempt=1", flush=True)
                        unaccepted_surface_ad_captured = (
                            await self._probe_unaccepted_watch_surface_for_ad(
                                driver=session.driver if session is not None else None,
                                navigator=navigator,
                                watcher=watcher,
                                adb_serial=device.adb_serial if device is not None else None,
                                topic=topic,
                                stage_label="no_result_opened_first_attempt",
                                topic_notes=topic_notes,
                                ad_analysis=ad_analysis,
                                landing_scraper=landing_scraper,
                                watched_ads=watched_ads,
                                topic_watched_ads=topic_watched_ads,
                            )
                        )
                        if unaccepted_surface_ad_captured:
                            print(
                                f"[android-session] stage:no_result_opened_first_attempt_ad_done total_ads={len(watched_ads)}",
                                flush=True,
                            )
                        retry_open_attempted = False
                        retry_surface_ad_captured = False
                        delayed_opened_title = None
                        if not unaccepted_surface_ad_captured:
                            delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
                                navigator=navigator,
                                topic=topic,
                                topic_notes=topic_notes,
                                stage_label="await_current_watch_title_first_attempt",
                                timeout_seconds=6.5,
                            )
                        if delayed_opened_title:
                            opened_title = delayed_opened_title
                            topic_notes.append("opened_title_recovered_before_retry:first_attempt")
                        else:
                            should_retry_topic = self._should_retry_topic_attempt(
                                topic_started_at=topic_started_at,
                                session_started_at=started_at,
                                duration_minutes=duration_minutes,
                            )
                            if not should_retry_topic:
                                topic_notes.append("topic_retry_skipped:budget")
                                topic_notes.append("open_first_result_retry_skipped:budget")
                            else:
                                retry_open_attempted = True
                                retry_results_ready_confirmed = True
                                print("[android-session] stage:submit_search_retry", flush=True)
                                await self._submit_search_with_timeout(
                                    navigator=navigator,
                                    topic=topic,
                                    topic_notes=topic_notes,
                                    stage_label="submit_search_retry",
                                    launcher_tripwire=launcher_tripwire,
                                )
                                print("[android-session] stage:wait_for_results_retry", flush=True)
                                try:
                                    await self._wait_for_results_with_timeout(
                                        navigator=navigator,
                                        topic=topic,
                                        topic_notes=topic_notes,
                                        stage_label="wait_for_results_retry",
                                        launcher_tripwire=launcher_tripwire,
                                    )
                                except Exception as exc:
                                    if "Failed to detect native YouTube results list" not in str(exc):
                                        raise
                                    retry_results_ready_confirmed = False
                                    topic_notes.append("wait_for_results_failed:retry")
                                    self._append_stage_debug_artifacts(
                                        driver=session.driver if session is not None else None,
                                        topic_notes=topic_notes,
                                        topic=topic,
                                        stage_label="wait_for_results_retry",
                                        adb_serial=device.adb_serial if device is not None else None,
                                    )
                                    print(
                                        "[android-session] topic:wait_for_results_failed attempt=2",
                                        flush=True,
                                    )
                                    retry_surface_ad_captured = (
                                        await self._probe_unaccepted_watch_surface_for_ad(
                                            driver=session.driver if session is not None else None,
                                            navigator=navigator,
                                            watcher=watcher,
                                            adb_serial=device.adb_serial if device is not None else None,
                                            topic=topic,
                                            stage_label="wait_for_results_retry",
                                            topic_notes=topic_notes,
                                            ad_analysis=ad_analysis,
                                            landing_scraper=landing_scraper,
                                            watched_ads=watched_ads,
                                            topic_watched_ads=topic_watched_ads,
                                            notify_ad_captured=_notify_ad_captured,
                                        )
                                    )
                                    if retry_surface_ad_captured:
                                        print(
                                            f"[android-session] stage:wait_for_results_retry_ad_done total_ads={len(watched_ads)}",
                                            flush=True,
                                        )
                                if retry_results_ready_confirmed:
                                    print("[android-session] stage:open_first_result_retry", flush=True)
                                    opened_title = await self._open_first_result_with_timeout(
                                        navigator=navigator,
                                        topic=topic,
                                        topic_notes=topic_notes,
                                        stage_label="open_first_result_retry",
                                        launcher_tripwire=launcher_tripwire,
                                    )
                                else:
                                    topic_notes.append("open_first_result_retry_skipped:no_results_surface")
                                    topic_notes.append("open_result_failure:no_surface:retry")
                                if not opened_title:
                                    self._append_open_result_diagnostics_notes(
                                        topic_notes=topic_notes,
                                        attempt_label="retry",
                                        diagnostics=await navigator.last_open_result_diagnostics(),
                                    )
                                    delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
                                        navigator=navigator,
                                        topic=topic,
                                        topic_notes=topic_notes,
                                        stage_label="await_current_watch_title_retry",
                                        timeout_seconds=8.5,
                                    )
                                    if delayed_opened_title:
                                        opened_title = delayed_opened_title
                                        topic_notes.append("opened_title_recovered_after_delay:retry")
                                if opened_title and self._opened_title_is_provisional(opened_title, topic):
                                    topic_notes.append("opened_title_provisional:retry")
                                    delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
                                        navigator=navigator,
                                        topic=topic,
                                        topic_notes=topic_notes,
                                        stage_label="await_current_watch_title_retry_provisional",
                                        timeout_seconds=5.0,
                                    )
                                    if (
                                        delayed_opened_title
                                        and not self._opened_title_is_provisional(
                                            delayed_opened_title,
                                            topic,
                                        )
                                    ):
                                        opened_title = delayed_opened_title
                                        topic_notes.append("opened_title_resolved:retry")
                                    elif (
                                        any(
                                            note in topic_notes
                                            for note in (
                                                "opened_title_provisional_allowed:watch_activity",
                                                "opened_title_provisional_allowed:watch_surface",
                                            )
                                        )
                                        and await navigator.has_watch_surface_for_query(topic)
                                    ):
                                        topic_notes.append("opened_title_provisional_kept:retry")
                                    else:
                                        opened_title = None
                                        topic_notes.append("opened_title_provisional_unresolved:retry")
                            if opened_title:
                                opened_title = await self._reconcile_opened_title_with_topic(
                                    navigator=navigator,
                                    topic=topic,
                                    opened_title=opened_title,
                                    topic_notes=topic_notes,
                                    attempt_label="retry",
                                )
                    if not opened_title:
                        should_probe_final_unaccepted_surface = (
                            retry_open_attempted
                            and not unaccepted_surface_ad_captured
                            and not retry_surface_ad_captured
                        )
                        if should_probe_final_unaccepted_surface:
                            final_unaccepted_surface_ad_captured = (
                                await self._probe_unaccepted_watch_surface_for_ad(
                                    driver=session.driver if session is not None else None,
                                    navigator=navigator,
                                    watcher=watcher,
                                    adb_serial=device.adb_serial if device is not None else None,
                                    topic=topic,
                                    stage_label="no_result_opened_final",
                                    topic_notes=topic_notes,
                                    ad_analysis=ad_analysis,
                                    landing_scraper=landing_scraper,
                                    watched_ads=watched_ads,
                                    topic_watched_ads=topic_watched_ads,
                                    notify_ad_captured=_notify_ad_captured,
                                )
                            )
                            if final_unaccepted_surface_ad_captured:
                                print(
                                    f"[android-session] stage:no_result_opened_final_ad_done total_ads={len(watched_ads)}",
                                    flush=True,
                                )
                        else:
                            if not retry_open_attempted:
                                topic_notes.append("no_result_opened_final_skipped:no_retry")
                            elif unaccepted_surface_ad_captured or retry_surface_ad_captured:
                                topic_notes.append("no_result_opened_final_skipped:ad_already_captured")
                            else:
                                topic_notes.append("no_result_opened_final_skipped:not_needed")
                        _both_no_surface = (
                            "open_result_failure:no_surface:first_attempt" in topic_notes
                            and "open_result_failure:no_surface:retry" in topic_notes
                        )
                        if _both_no_surface:
                            print("[android-session] topic:no_results_surface:deeplink_rescue", flush=True)
                            topic_notes.append("no_results_surface_deeplink_rescue")
                            try:
                                await self._submit_search_with_timeout(
                                    navigator=navigator,
                                    topic=topic,
                                    topic_notes=topic_notes,
                                    stage_label="no_results_surface_deeplink_rescue",
                                    launcher_tripwire=launcher_tripwire,
                                    timeout_seconds=25.0,
                                )
                            except Exception:
                                pass
                        last_resort_title = await self._last_resort_open_any_result(
                            navigator=navigator,
                            topic=topic,
                            topic_notes=topic_notes,
                            stage_label="no_result_opened",
                        )
                        if last_resort_title:
                            opened_title = last_resort_title
                        else:
                            topic_notes.append("no_result_opened")
                            print("[android-session] topic:no_result_opened attempt=2", flush=True)
                        if session is not None:
                            debug_screen_path, debug_page_source_path = self._write_watch_debug_artifacts(
                                driver=session.driver,
                                topic=f"{topic}_results",
                            )
                            if debug_screen_path is not None:
                                topic_notes.append(f"results_debug:{debug_screen_path}")
                            if debug_page_source_path is not None:
                                topic_notes.append(f"results_debug_xml:{debug_page_source_path}")
                    else:
                        print(
                            f"[android-session] topic:opened title={opened_title}",
                            flush=True,
                        )
                        if on_progress is not None:
                            try:
                                current_watch_started_at = time.time()
                                await on_progress(
                                    event="video_opened",
                                    current_topic=topic,
                                    current_watch={
                                        "action": "watch",
                                        "title": opened_title or topic,
                                        "url": "",
                                        "started_at": current_watch_started_at,
                                        "watched_seconds": 0,
                                        "target_seconds": 0,
                                        "search_keyword": topic,
                                        "matched_topics": [topic],
                                        "keywords": [],
                                    },
                                )
                            except Exception:
                                pass
                        recording_handle = None
                        recorder = None
                        recorded_video_path = None
                        recorded_video_duration_seconds = None
                        try:
                            # Dismiss any engagement/landing panel that may be
                            # covering the player right after video opens.
                            with contextlib.suppress(Exception):
                                await watcher.dismiss_residual_ad_if_present()
                            with contextlib.suppress(Exception):
                                await watcher.restore_primary_watch_surface()
                            with contextlib.suppress(Exception):
                                await watcher.dismiss_engagement_panel()
                            if self._config.android_app.probe_screenrecord_enabled:
                                (
                                    recorder,
                                    recording_handle,
                                ) = await self._start_recording_handle(
                                    label="session",
                                    topic=topic,
                                    adb_serial=device.adb_serial,
                                )
                            initial_watch_seconds = max(
                                4,
                                min(6, self._config.android_app.probe_watch_seconds),
                            )
                            print("[android-session] stage:watch_current", flush=True)
                            watch_result = await watcher.watch_current(
                                watch_seconds=initial_watch_seconds
                            )
                            watch_result, watch_extension_note = await self._extend_ad_watch_if_needed(
                                watcher=watcher,
                                watch_result=watch_result,
                            )
                            _samples_ended_monotonic = time.monotonic()
                            if watch_extension_note is not None:
                                topic_notes.append(watch_extension_note)
                                print(
                                    f"[android-session] stage:watch_extension note={watch_extension_note}",
                                    flush=True,
                                )
                        except Exception:
                            if recorder is not None and recording_handle is not None:
                                recorded_video_path = await self._stop_recording_handle(
                                    recorder=recorder,
                                    recording_handle=recording_handle,
                                )
                                recording_handle = None
                                recorded_video_duration_seconds = self._probe_recorded_video_duration(
                                    recorded_video_path
                                )
                            raise

                        target_watch_seconds = self._decide_session_target_watch_seconds(
                            topic=topic,
                            topics=active_topics,
                            topic_results=topic_results,
                            watch_samples=list(getattr(watch_result, "samples", []) or []),
                            started_at=started_at,
                            duration_minutes=duration_minutes,
                        )
                        topic_notes.append(f"session_target_watch:{target_watch_seconds}")
                        watch_debug_screen_path = None
                        watch_debug_page_source_path = None
                        watch_verified = watch_result.verified
                        watch_seconds = self._derive_watch_seconds(watch_result.samples)
                        watch_ad_detected = any(
                            sample.ad_detected for sample in watch_result.samples
                        )
                        if on_progress is not None and current_watch_started_at is not None:
                            try:
                                await on_progress(
                                    event="watch_target_planned",
                                    current_topic=topic,
                                    current_watch={
                                        "action": "watch",
                                        "title": opened_title or topic,
                                        "url": "",
                                        "started_at": current_watch_started_at,
                                        "watched_seconds": float(watch_seconds or 0.0),
                                        "target_seconds": target_watch_seconds,
                                        "search_keyword": topic,
                                        "matched_topics": [topic],
                                        "keywords": [],
                                    },
                                )
                            except Exception:
                                pass
                        if opened_title and self._opened_title_is_provisional(opened_title, topic):
                            refreshed_title = await navigator.await_current_watch_title(
                                topic,
                                timeout_seconds=3.0,
                                deadline=time.monotonic() + 3.0,
                            )
                            if (
                                refreshed_title
                                and not self._opened_title_is_provisional(refreshed_title, topic)
                            ):
                                opened_title = refreshed_title
                                topic_notes.append("opened_title_resolved_after_watch")
                        if not watch_ad_detected:
                            watch_result, opened_title, watch_surface_recovery_notes = (
                                await self._recover_unstable_watch_surface(
                                    navigator=navigator,
                                    watcher=watcher,
                                    topic=topic,
                                    opened_title=opened_title,
                                    watch_result=watch_result,
                                )
                            )
                            topic_notes.extend(watch_surface_recovery_notes)
                            watch_verified = watch_result.verified
                            watch_seconds = self._derive_watch_seconds(watch_result.samples)
                            watch_ad_detected = any(
                                sample.ad_detected for sample in watch_result.samples
                            )
                            if (
                                watch_debug_screen_path is None
                                and "watch_surface_probe:no_watch_surface" in watch_surface_recovery_notes
                            ):
                                watch_debug_screen_path, watch_debug_page_source_path = (
                                    self._write_watch_debug_artifacts(
                                        driver=session.driver,
                                        topic=f"{topic}_watch_surface",
                                    )
                                )
                                if watch_debug_screen_path is not None:
                                    topic_notes.append(f"watch_debug:{watch_debug_screen_path}")
                        if not watch_ad_detected:
                            watch_result, verify_probe_note = await self._reinforce_watch_verification_if_needed(
                                watcher=watcher,
                                watch_result=watch_result,
                            )
                            if verify_probe_note is not None:
                                topic_notes.append(verify_probe_note)
                            watch_verified = watch_result.verified
                            watch_seconds = self._derive_watch_seconds(watch_result.samples)
                            watch_ad_detected = any(
                                sample.ad_detected for sample in watch_result.samples
                            )
                        if (
                            not watch_ad_detected
                            and recorder is not None
                            and recording_handle is not None
                        ):
                            watch_result, missed_ad_note, missed_ad_probe = (
                                await self._probe_current_ad_surface_for_missed_ad(
                                    watcher=watcher,
                                    watch_result=watch_result,
                                )
                            )
                            if missed_ad_note is not None:
                                topic_notes.append(missed_ad_note)
                            if missed_ad_probe is not None:
                                watch_verified = bool(
                                    getattr(watch_result, "verified", False)
                                )
                                watch_seconds = self._derive_watch_seconds(
                                    list(getattr(watch_result, "samples", []) or [])
                                )
                                watch_ad_detected = True
                        _ad_sample_detail = [
                            f"off={getattr(s,'offset_seconds','?')} prog={getattr(s,'ad_progress_seconds','?')} dur={getattr(s,'ad_duration_seconds','?')} skip={getattr(s,'skip_available','?')}"
                            for s in watch_result.samples if getattr(s, "ad_detected", False)
                        ]
                        print(
                            f"[android-session] stage:watch_done verified={str(watch_verified).lower()} ad_detected={str(watch_ad_detected).lower()} samples={len(watch_result.samples)} target_watch={target_watch_seconds} ad_samples={len(_ad_sample_detail)} recorder={'yes' if recorder else 'no'}",
                            flush=True,
                        )
                        for _asd in _ad_sample_detail:
                            print(f"[android-session] ad_sample: {_asd}", flush=True)
                        if watch_ad_detected or any(
                            sample.error_messages for sample in watch_result.samples
                        ):
                            watch_debug_screen_path, watch_debug_page_source_path = (
                                self._write_watch_debug_artifacts(
                                    driver=session.driver,
                                    topic=topic,
                                    adb_serial=device.adb_serial,
                                    page_source_override=getattr(
                                        watch_result,
                                        "ad_debug_page_source",
                                        None,
                                    ),
                                )
                            )
                            if watch_debug_screen_path is not None:
                                topic_notes.append(f"watch_debug:{watch_debug_screen_path}")

                        topic_notes.append(
                            f"engagement_deferred:{self._engagement_gate_reason(watch_result.samples)}"
                        )

                        # Banner ads (no timer, no skip) don't need video — discard and note
                        _is_banner_ad = watch_ad_detected and self._result_is_banner_only_ad(watch_result)
                        if _is_banner_ad:
                            topic_notes.append("watch_ad_banner_only:video_skipped")
                            print("[android-session] stage:banner_ad_detected:no_video", flush=True)

                        # Play Store app-install banners are irrelevant — skip entirely
                        _is_play_store_ad = watch_ad_detected and not _is_banner_ad and self._result_is_play_store_ad(watch_result)
                        if _is_play_store_ad:
                            watch_ad_detected = False
                            topic_notes.append("watch_ad_play_store:skipped")
                            print("[android-session] stage:play_store_ad_detected:skipped", flush=True)

                        # If no ad detected (or banner-only/play-store), stop recording and discard
                        if (not watch_ad_detected or _is_banner_ad) and recorder is not None and recording_handle is not None:
                            try:
                                local_video = await recorder.stop(recording_handle, keep_local=True)
                                if local_video is not None:
                                    logger.info(
                                        "recorder[session]: discarded no-ad recording topic=%s path=%s",
                                        topic,
                                        local_video,
                                    )
                                    local_video.unlink(missing_ok=True)
                            except Exception:
                                pass
                            recording_handle = None

                        skip_main_watch_after_ad = False
                        if watch_ad_detected:
                            print("[android-session] stage:ad_probe_cta", flush=True)
                            try:
                                built_ad = None
                                ad_cta_result = None
                                try:
                                    ad_interactor = AndroidYouTubeAdInteractor(
                                        session.driver,
                                        self._config.android_app,
                                        adb_serial=device.adb_serial,
                                    )
                                    ad_cta_result = await ad_interactor.probe_cta(
                                        artifact_dir=(
                                            self._config.storage.base_path
                                            / self._config.android_app.artifacts_subdir
                                        ),
                                        artifact_prefix=self._build_safe_artifact_prefix(topic),
                                    )
                                except Exception as exc:
                                    topic_notes.append(
                                        f"ad_cta_probe_failed:{type(exc).__name__}:{exc}"
                                    )
                                # Total elapsed since last sample = debug artifacts + CTA probe
                                _cta_probe_elapsed = time.monotonic() - _samples_ended_monotonic
                                print(
                                    f"[android-session] stage:cta_done elapsed_since_samples={_cta_probe_elapsed:.1f}s "
                                    f"cta_clicked={getattr(ad_cta_result,'clicked',None)} "
                                    f"cta_returned={getattr(ad_cta_result,'returned_to_youtube',None)} "
                                    f"recorder={'active' if recorder and recording_handle else 'none'}",
                                    flush=True,
                                )
                                # Stop recording after CTA probe — wait for remaining ad time first.
                                # Subtract CTA probe elapsed time since ad progressed during that window.
                                if recorder is not None and recording_handle is not None:
                                    _watch_samples = list(watch_result.samples)
                                    (
                                        recorded_video_path,
                                        recorded_video_duration_seconds,
                                    ) = await self._finalize_recording_after_cta(
                                        label="session",
                                        recorder=recorder,
                                        recording_handle=recording_handle,
                                        samples=_watch_samples,
                                        debug_page_source_path=watch_debug_page_source_path,
                                        clicked=(
                                            ad_cta_result.clicked if ad_cta_result is not None else None
                                        ),
                                        returned_to_youtube=(
                                            ad_cta_result.returned_to_youtube
                                            if ad_cta_result is not None
                                            else None
                                        ),
                                        elapsed_since_samples=_cta_probe_elapsed,
                                    )
                                    recording_handle = None
                                built_ad = build_watched_ad_record(
                                    watch_samples=watch_result.samples,
                                    watch_debug_screen_path=watch_debug_screen_path,
                                    watch_debug_page_source_path=watch_debug_page_source_path,
                                    ad_cta_result=ad_cta_result,
                                    recorded_video_path=recorded_video_path,
                                    recorded_video_duration_seconds=recorded_video_duration_seconds,
                                )
                                if built_ad is None:
                                    # Fallback: ad was detected but samples had no ad_detected=True.
                                    # Record minimal entry from XML+cta so we never silently drop it.
                                    print(
                                        f"[android-session] stage:build_ad_fallback recorded_video={recorded_video_path} rec_dur={recorded_video_duration_seconds}",
                                        flush=True,
                                    )
                                    built_ad = build_watched_ad_record(
                                        watch_samples=watch_result.samples,
                                        watch_debug_screen_path=watch_debug_screen_path,
                                        watch_debug_page_source_path=watch_debug_page_source_path,
                                        ad_cta_result=ad_cta_result,
                                        recorded_video_path=recorded_video_path,
                                        recorded_video_duration_seconds=recorded_video_duration_seconds,
                                        force_from_debug=True,
                                    )
                                if built_ad is not None:
                                    built_ad = self._with_watched_ad_position(
                                        built_ad,
                                        len(watched_ads) + 1,
                                    )
                                    await self._trim_recording_tail_to_ad(
                                        built_ad=built_ad,
                                        ad_sample_count=sum(
                                            1
                                            for sample in list(watch_result.samples)
                                            if getattr(sample, "ad_detected", False)
                                        ),
                                        extension_samples=list(watch_result.samples),
                                    )
                                    await self._focus_captured_ad_video_if_needed(built_ad)
                                    print("[android-session] stage:ad_analyze", flush=True)
                                    ad_analysis.submit(built_ad)
                                    landing_scraper.submit(built_ad)
                                    topic_watched_ads.append(built_ad)
                                    watched_ads.append(built_ad)
                                    await _notify_ad_captured()
                                    print(
                                        f"[android-session] stage:ad_done total_ads={len(watched_ads)}",
                                        flush=True,
                                    )
                                if ad_cta_result is not None:
                                    topic_notes.append(
                                        f"ad_cta_returned:{str(ad_cta_result.returned_to_youtube).lower()}"
                                    )
                                if ad_cta_result is not None and ad_cta_result.returned_to_youtube:
                                    post_ad_watch = None
                                    try:
                                        print("[android-session] stage:post_ad_watch", flush=True)
                                        post_ad_watch, post_ad_notes = await self._resume_after_ad_return(
                                            watcher=watcher,
                                            built_ad=built_ad,
                                        )
                                        topic_notes.extend(post_ad_notes)
                                        for note in post_ad_notes:
                                            print(
                                                f"[android-session] stage:post_ad_watch_extension note={note}",
                                                flush=True,
                                            )
                                    except Exception as exc:
                                        topic_notes.append(f"post_ad_watch_failed:{type(exc).__name__}")
                                        if self._should_skip_post_ad_settle(built_ad):
                                            topic_notes.append("post_ad_watch_recover:completed_ad")
                                            try:
                                                if await watcher.restore_primary_watch_surface():
                                                    topic_notes.append(
                                                        "post_ad_surface:restored_after_failure"
                                                    )
                                            except Exception as restore_exc:
                                                topic_notes.append(
                                                    f"post_ad_surface_restore_failed:{type(restore_exc).__name__}"
                                                )
                                            try:
                                                if await watcher.ensure_playing():
                                                    topic_notes.append(
                                                        "post_ad_playback:resume_after_failure"
                                                    )
                                            except Exception as playback_exc:
                                                topic_notes.append(
                                                    f"post_ad_playback_resume_failed:{type(playback_exc).__name__}"
                                                )
                                    if post_ad_watch is not None:
                                        watch_result = replace(
                                            watch_result,
                                            verified=bool(
                                                getattr(watch_result, "verified", False)
                                                or getattr(post_ad_watch, "verified", False)
                                            ),
                                            samples=self._merge_watch_samples(
                                                list(getattr(watch_result, "samples", []) or []),
                                                list(getattr(post_ad_watch, "samples", []) or []),
                                            ),
                                            ad_debug_page_source=self._preferred_ad_debug_page_source(
                                                post_ad_watch,
                                                watch_result,
                                            ),
                                        )
                                        if self._result_has_ad_samples(post_ad_watch):
                                            pending_midroll_result = post_ad_watch
                                            pending_midroll_samples_ended_monotonic = (
                                                time.monotonic()
                                            )
                                    elif self._should_skip_post_ad_settle(built_ad):
                                        topic_notes.append(
                                            "post_ad_watch_recover:completed_ad_no_result"
                                        )
                                        try:
                                            if await watcher.restore_primary_watch_surface():
                                                topic_notes.append(
                                                    "post_ad_surface:restored_after_no_result"
                                                )
                                        except Exception as restore_exc:
                                            topic_notes.append(
                                                f"post_ad_surface_restore_failed:{type(restore_exc).__name__}"
                                            )
                                        try:
                                            if await watcher.ensure_playing():
                                                topic_notes.append(
                                                    "post_ad_playback:resume_after_no_result"
                                                )
                                        except Exception as playback_exc:
                                            topic_notes.append(
                                                f"post_ad_playback_resume_failed:{type(playback_exc).__name__}"
                                            )
                            except Exception as exc:
                                topic_notes.append(f"ad_flow_failed:{type(exc).__name__}:{exc}")
                                print(
                                    f"[android-session] stage:ad_flow_failed error={type(exc).__name__}:{exc}",
                                    flush=True,
                                )
                                if watch_debug_screen_path is None:
                                    watch_debug_screen_path, watch_debug_page_source_path = (
                                        self._write_watch_debug_artifacts(
                                            driver=session.driver,
                                            topic=topic,
                                            adb_serial=device.adb_serial,
                                            page_source_override=getattr(
                                                watch_result,
                                                "ad_debug_page_source",
                                                None,
                                            ),
                                        )
                                    )

                        # ── main watch loop: watch → catch mid-roll ads → resume → repeat ──
                        _max_midroll_rounds = 10
                        _midroll_continuation_duplicate_rounds = 0
                        _midroll_duplicate_rounds = 0
                        _midroll_residual_return_rounds = 0
                        for _midroll_round in range(_max_midroll_rounds):
                            watch_gate_reason = self._engagement_gate_reason(
                                list(getattr(watch_result, "samples", []) or [])
                            )
                            if skip_main_watch_after_ad:
                                topic_notes.append("main_watch_skipped:post_ad_handoff")
                                break
                            elif (
                                not watch_verified
                                and watch_gate_reason in {"no_watch_surface", "short_form"}
                            ):
                                topic_notes.append(f"main_watch_skipped:{watch_gate_reason}")
                                break

                            if (
                                self._config.android_app.probe_screenrecord_enabled
                                and recording_handle is None
                            ):
                                with contextlib.suppress(Exception):
                                    await watcher.dismiss_engagement_panel()
                                (
                                    recorder,
                                    recording_handle,
                                ) = await self._start_recording_handle(
                                    label="midroll",
                                    topic=topic,
                                    adb_serial=device.adb_serial,
                                    round_index=_midroll_round + 1,
                                )

                            (
                                watch_result,
                                main_watch_extension_note,
                                _extension_extra_result,
                                _mr_samples_ended_monotonic,
                            ) = await self._advance_main_watch_iteration(
                                watcher=watcher,
                                watch_result=watch_result,
                                target_watch_seconds=target_watch_seconds,
                                pending_midroll_result=pending_midroll_result,
                                pending_midroll_samples_ended_monotonic=(
                                    pending_midroll_samples_ended_monotonic
                                ),
                            )
                            pending_midroll_result = None
                            pending_midroll_samples_ended_monotonic = None
                            if main_watch_extension_note is not None:
                                topic_notes.append(main_watch_extension_note)
                                print(
                                    f"[android-session] stage:main_watch_extension note={main_watch_extension_note}",
                                    flush=True,
                                )

                            # Check if a NEW mid-roll ad appeared during this main_watch chunk
                            _midroll_ad_found = (
                                main_watch_extension_note is not None
                                and "main_watch_ad_detected" in main_watch_extension_note
                            )
                            if not _midroll_ad_found:
                                break  # no ad — main watch completed to target

                            # ── catch the mid-roll ad ──
                            # Dedup: check if this mid-roll is the same ad we already caught.
                            # Use ONLY the new extension samples (not the full merged result) to avoid
                            # false-positive dedup against the original pre-roll ad samples.
                            _DEDUP_GENERIC = {
                                "sponsored", "skip ad", "skip", "visit advertiser", "visit site",
                                "shop now", "learn more", "install", "get quote", "sign up",
                                "like ad", "share ad", "more options", "close ad panel", "more info",
                                "minimize", "captions", "pause video", "enter fullscreen",
                                "expand mini player", "drag handle", "my ad center",
                            }
                            def _dedup_key(lines: list) -> set:
                                result = set()
                                for line in lines:
                                    s = str(line).strip()
                                    if not s or len(s) < 4:
                                        continue
                                    # Skip generic labels and pod position indicators ("1 of 2")
                                    if s.casefold() in _DEDUP_GENERIC:
                                        continue
                                    if s.casefold().startswith("sponsored"):
                                        continue
                                    result.add(s[:80])
                                return result

                            # Use only NEW extension samples for dedup key — avoids matching
                            # the original pre-roll ad's lines (which are in the merged watch_result)
                            _mr_new_samples = list(getattr(_extension_extra_result, "samples", []) or []) if _extension_extra_result is not None else []
                            if (
                                topic_watched_ads
                                and self._midroll_continues_previous_ad(
                                    previous_ad=topic_watched_ads[-1],
                                    extension_samples=_mr_new_samples,
                                )
                            ):
                                self._merge_midroll_continuation_into_previous_ad(
                                    previous_ad=topic_watched_ads[-1],
                                    extension_samples=_mr_new_samples,
                                )
                                if recorder is not None and recording_handle is not None:
                                    await self._discard_recording_handle(
                                        label="midroll",
                                        topic=topic,
                                        recorder=recorder,
                                        recording_handle=recording_handle,
                                        reason="duplicate_continuation",
                                    )
                                    recording_handle = None
                                    topic_notes.append(
                                        f"midroll_duplicate_recording_discarded:round{_midroll_round + 1}:continuation"
                                    )
                                _midroll_continuation_duplicate_rounds += 1
                                topic_notes.append(
                                    f"midroll_ad_skip_duplicate:round{_midroll_round + 1}:continuation"
                                )
                                print(
                                    f"[android-session] stage:midroll_ad_skip_duplicate round={_midroll_round + 1} reason=continuation",
                                    flush=True,
                                )
                                if _midroll_continuation_duplicate_rounds >= 2:
                                    topic_notes.append(
                                        "midroll_ad_skip_duplicate_cap_reached:continuation"
                                    )
                                    print(
                                        "[android-session] stage:midroll_ad_skip_duplicate "
                                        f"round={_midroll_round + 1} reason=continuation_cap",
                                        flush=True,
                                    )
                                    with contextlib.suppress(Exception):
                                        await watcher.restore_primary_watch_surface()
                                    with contextlib.suppress(Exception):
                                        await watcher.dismiss_residual_ad_if_present()
                                    with contextlib.suppress(Exception):
                                        await watcher.ensure_playing()
                                    _midroll_continuation_duplicate_rounds = 0
                                    topic_notes.append(
                                        f"midroll_duplicate_cap_break:round{_midroll_round + 1}:continuation"
                                    )
                                    break
                                continue
                            _midroll_continuation_duplicate_rounds = 0
                            _mr_raw_hints: list[str] = []
                            for _s in _mr_new_samples:
                                if getattr(_s, "ad_detected", False):
                                    _mr_raw_hints.extend(getattr(_s, "ad_visible_lines", []) or [])
                            _mr_sponsor_hints = _dedup_key(_mr_raw_hints)
                            _mr_is_duplicate = False
                            if topic_watched_ads and _mr_sponsor_hints:
                                for _prev_ad in topic_watched_ads:
                                    _prev_lines = _dedup_key(
                                        (_prev_ad.get("visible_lines") or [])
                                    )
                                    if _prev_lines and _prev_lines & _mr_sponsor_hints:
                                        _mr_is_duplicate = True
                                        break
                            if not _mr_is_duplicate and topic_watched_ads:
                                def _host(u):
                                    if not isinstance(u, str) or not u:
                                        return None
                                    from urllib.parse import urlsplit
                                    try:
                                        h = urlsplit(u if "://" in u else f"https://{u}").netloc.lower()
                                    except Exception:
                                        return None
                                    return h[4:] if h.startswith("www.") else h or None
                                _mr_new_hosts = {
                                    _host(getattr(_s, "ad_display_url", None))
                                    for _s in _mr_new_samples
                                }
                                _mr_new_hosts.discard(None)
                                _mr_new_hosts.discard("www.googleadservices.com")
                                _mr_new_hosts.discard("googleadservices.com")
                                _prev_hosts: set = set()
                                for _prev_ad in topic_watched_ads:
                                    cap = _prev_ad.get("capture") or {}
                                    for u in (
                                        _prev_ad.get("display_url"),
                                        _prev_ad.get("landing_url"),
                                        cap.get("landing_scrape_url"),
                                        cap.get("pre_click_display_url"),
                                    ):
                                        h = _host(u)
                                        if h and h not in {"googleadservices.com", "www.googleadservices.com"}:
                                            _prev_hosts.add(h)
                                if _mr_new_hosts and _mr_new_hosts & _prev_hosts:
                                    _mr_is_duplicate = True
                            if _mr_is_duplicate:
                                if recorder is not None and recording_handle is not None:
                                    await self._discard_recording_handle(
                                        label="midroll",
                                        topic=topic,
                                        recorder=recorder,
                                        recording_handle=recording_handle,
                                        reason="duplicate_identity",
                                    )
                                    recording_handle = None
                                    topic_notes.append(
                                        f"midroll_duplicate_recording_discarded:round{_midroll_round + 1}"
                                    )
                                _midroll_duplicate_rounds += 1
                                topic_notes.append(f"midroll_ad_skip_duplicate:round{_midroll_round + 1}")
                                print(
                                    f"[android-session] stage:midroll_ad_skip_duplicate round={_midroll_round + 1}",
                                    flush=True,
                                )
                                with contextlib.suppress(Exception):
                                    await watcher.restore_primary_watch_surface()
                                with contextlib.suppress(Exception):
                                    await watcher.dismiss_residual_ad_if_present()
                                with contextlib.suppress(Exception):
                                    await watcher.ensure_playing()
                                topic_notes.append(
                                    f"midroll_duplicate_continue_watch:round{_midroll_round + 1}"
                                )
                                if _midroll_duplicate_rounds >= 2:
                                    topic_notes.append(
                                        f"midroll_duplicate_cap_break:round{_midroll_round + 1}"
                                    )
                                    _midroll_duplicate_rounds = 0
                                    pending_midroll_result = None
                                    pending_midroll_samples_ended_monotonic = None
                                    # Attempt remaining watch immediately; if a new ad appears
                                    # let the outer loop handle it normally.
                                    try:
                                        _cap_next, _cap_note, _cap_extra = await self._continue_main_watch_if_needed(
                                            watcher=watcher,
                                            watch_result=watch_result,
                                            target_watch_seconds=target_watch_seconds,
                                        )
                                        if _cap_next is not watch_result:
                                            watch_result = _cap_next
                                            watch_verified = bool(getattr(watch_result, "verified", False))
                                        if _cap_note:
                                            topic_notes.append(f"midroll_cap_fill:{_cap_note}")
                                        if _cap_note and "main_watch_ad_detected" in _cap_note:
                                            pending_midroll_result = _cap_extra
                                            pending_midroll_samples_ended_monotonic = time.monotonic()
                                            continue
                                    except Exception as _cap_exc:
                                        topic_notes.append(f"midroll_cap_fill_failed:{type(_cap_exc).__name__}")
                                    break
                                continue  # same banner — clear it and keep filling watch target
                            _midroll_duplicate_rounds = 0

                            topic_notes.append(f"midroll_ad_catch:round{_midroll_round + 1}")
                            print(
                                f"[android-session] stage:midroll_ad_catch round={_midroll_round + 1}",
                                flush=True,
                            )
                            # Play Store app-install midroll: skip entirely
                            _mr_is_play_store = self._result_is_play_store_ad(_extension_extra_result or watch_result)
                            if _mr_is_play_store:
                                if recorder is not None and recording_handle is not None:
                                    await self._discard_recording_handle(
                                        label="midroll",
                                        topic=topic,
                                        recorder=recorder,
                                        recording_handle=recording_handle,
                                        reason="play_store",
                                    )
                                    recording_handle = None
                                topic_notes.append(f"midroll_play_store:skipped:round{_midroll_round + 1}")
                                print(f"[android-session] stage:midroll_play_store_ad:skipped round={_midroll_round + 1}", flush=True)
                                with contextlib.suppress(Exception):
                                    await watcher.restore_primary_watch_surface()
                                with contextlib.suppress(Exception):
                                    await watcher.ensure_playing()
                                continue

                            # Banner-only midroll: discard video, keep screenshot only
                            _mr_is_banner = self._result_is_banner_only_ad(_extension_extra_result or watch_result)
                            if _mr_is_banner and recorder is not None and recording_handle is not None:
                                await self._discard_recording_handle(
                                    label="midroll",
                                    topic=topic,
                                    recorder=recorder,
                                    recording_handle=recording_handle,
                                    reason="banner_only",
                                )
                                recording_handle = None
                                topic_notes.append(f"midroll_banner_only:video_skipped:round{_midroll_round + 1}")
                                print(f"[android-session] stage:midroll_banner_ad:no_video round={_midroll_round + 1}", flush=True)
                            try:
                                _mr_watch_debug_screen, _mr_watch_debug_xml = (
                                    self._write_watch_debug_artifacts(
                                        driver=session.driver,
                                        topic=topic,
                                        adb_serial=device.adb_serial,
                                        page_source_override=getattr(
                                            watch_result, "ad_debug_page_source", None,
                                        ),
                                    )
                                )
                                if _mr_watch_debug_screen is not None:
                                    topic_notes.append(f"midroll_debug:{_mr_watch_debug_screen}")
                                _mr_video_path = None
                                _mr_video_dur = None
                                _mr_cta_result = None
                                try:
                                    _mr_interactor = AndroidYouTubeAdInteractor(
                                        session.driver,
                                        self._config.android_app,
                                        adb_serial=device.adb_serial,
                                    )
                                    _mr_cta_result = await _mr_interactor.probe_cta(
                                        artifact_dir=(
                                            self._config.storage.base_path
                                            / self._config.android_app.artifacts_subdir
                                        ),
                                        artifact_prefix=self._build_safe_artifact_prefix(topic),
                                    )
                                except Exception as exc:
                                    topic_notes.append(f"midroll_cta_failed:{type(exc).__name__}:{exc}")
                                # Full elapsed from last sample: debug artifact write + CTA probe.
                                _mr_cta_elapsed = time.monotonic() - _mr_samples_ended_monotonic
                                if recorder is not None and recording_handle is not None:
                                    (
                                        _mr_video_path,
                                        _mr_video_dur,
                                    ) = await self._finalize_recording_after_cta(
                                        label=f"midroll_round{_midroll_round + 1}",
                                        recorder=recorder,
                                        recording_handle=recording_handle,
                                        samples=(
                                            _mr_new_samples
                                            if _mr_new_samples
                                            else list(getattr(watch_result, "samples", []) or [])
                                        ),
                                        debug_page_source_path=_mr_watch_debug_xml,
                                        clicked=(
                                            _mr_cta_result.clicked
                                            if _mr_cta_result is not None
                                            else None
                                        ),
                                        returned_to_youtube=(
                                            _mr_cta_result.returned_to_youtube
                                            if _mr_cta_result is not None
                                            else None
                                        ),
                                        elapsed_since_samples=_mr_cta_elapsed,
                                    )
                                    recording_handle = None
                                else:
                                    logger.warning(
                                        "recorder[midroll]: missing active recording round=%s topic=%s",
                                        _midroll_round + 1,
                                        topic,
                                    )
                                # Use only the NEW extension samples for the midroll ad record.
                                # The full merged watch_result.samples includes original pre-roll
                                # samples which would corrupt the midroll ad metadata.
                                _mr_watch_samples = _mr_new_samples if _mr_new_samples else list(getattr(watch_result, "samples", []) or [])
                                _mr_built_ad = build_watched_ad_record(
                                    watch_samples=_mr_watch_samples,
                                    watch_debug_screen_path=_mr_watch_debug_screen,
                                    watch_debug_page_source_path=_mr_watch_debug_xml,
                                    ad_cta_result=_mr_cta_result,
                                    recorded_video_path=_mr_video_path,
                                    recorded_video_duration_seconds=_mr_video_dur,
                                )
                                _mr_identity_matches_previous = bool(
                                    _mr_built_ad is not None
                                    and topic_watched_ads
                                    and self._watched_ad_identity_key(_mr_built_ad)
                                    == self._watched_ad_identity_key(topic_watched_ads[-1])
                                )
                                if _mr_built_ad is not None:
                                    _mr_built_ad = self._with_watched_ad_position(
                                        _mr_built_ad,
                                        len(watched_ads) + 1,
                                    )
                                    await self._trim_recording_tail_to_ad(
                                        built_ad=_mr_built_ad,
                                        ad_sample_count=len(_mr_new_samples),
                                        extension_samples=_mr_new_samples,
                                        ad_detected_after_watch_seconds=(
                                            self._main_watch_ad_detected_after_seconds(
                                                main_watch_extension_note
                                            )
                                        ),
                                    )
                                    await self._focus_captured_ad_video_if_needed(_mr_built_ad)
                                    ad_analysis.submit(_mr_built_ad)
                                    landing_scraper.submit(_mr_built_ad)
                                    topic_watched_ads.append(_mr_built_ad)
                                    watched_ads.append(_mr_built_ad)
                                    await _notify_ad_captured()
                                    print(
                                        f"[android-session] stage:midroll_ad_done total_ads={len(watched_ads)}",
                                        flush=True,
                                    )
                                # Return to YouTube and resume
                                if _mr_cta_result is not None and _mr_cta_result.returned_to_youtube:
                                    try:
                                        _mr_post_ad, _mr_post_notes = await self._resume_after_ad_return(
                                            watcher=watcher, built_ad=_mr_built_ad,
                                        )
                                        topic_notes.extend(_mr_post_notes)
                                        _midroll_residual_detected = any(
                                            note == "post_ad_residual_detected:returning_for_midroll"
                                            for note in _mr_post_notes
                                        )
                                        if _midroll_residual_detected:
                                            _midroll_residual_return_rounds += 1
                                        else:
                                            _midroll_residual_return_rounds = 0
                                        if _mr_post_ad is not None:
                                            watch_result = replace(
                                                watch_result,
                                                verified=bool(
                                                    getattr(watch_result, "verified", False)
                                                    or getattr(_mr_post_ad, "verified", False)
                                                ),
                                                samples=self._merge_watch_samples(
                                                    list(getattr(watch_result, "samples", []) or []),
                                                    list(getattr(_mr_post_ad, "samples", []) or []),
                                                ),
                                            )
                                            if self._result_has_ad_samples(_mr_post_ad):
                                                if (
                                                    _mr_identity_matches_previous
                                                    and _midroll_residual_detected
                                                ):
                                                    topic_notes.append(
                                                        f"midroll_repeat_identity_cap_reached:round{_midroll_round + 1}"
                                                    )
                                                    print(
                                                        "[android-session] stage:midroll_repeat_identity_cap "
                                                        f"round={_midroll_round + 1}",
                                                        flush=True,
                                                    )
                                                    if await watcher.restore_primary_watch_surface():
                                                        topic_notes.append(
                                                            "post_ad_surface:restored_midroll_identity_cap"
                                                        )
                                                    # Reset counters and attempt remaining watch immediately.
                                                    _midroll_residual_return_rounds = 0
                                                    _midroll_duplicate_rounds = 0
                                                    pending_midroll_result = None
                                                    pending_midroll_samples_ended_monotonic = None
                                                    try:
                                                        _cap_next, _cap_note, _cap_extra = await self._continue_main_watch_if_needed(
                                                            watcher=watcher,
                                                            watch_result=watch_result,
                                                            target_watch_seconds=target_watch_seconds,
                                                        )
                                                        if _cap_next is not watch_result:
                                                            watch_result = _cap_next
                                                            watch_verified = bool(getattr(watch_result, "verified", False))
                                                        if _cap_note:
                                                            topic_notes.append(f"identity_cap_fill:{_cap_note}")
                                                        if _cap_note and "main_watch_ad_detected" in _cap_note:
                                                            pending_midroll_result = _cap_extra
                                                            pending_midroll_samples_ended_monotonic = time.monotonic()
                                                            continue
                                                    except Exception as _cap_exc:
                                                        topic_notes.append(f"identity_cap_fill_failed:{type(_cap_exc).__name__}")
                                                    break
                                                if _midroll_residual_return_rounds >= 3:
                                                    topic_notes.append(
                                                        f"post_ad_residual_cap_reached:round{_midroll_round + 1}"
                                                    )
                                                    print(
                                                        "[android-session] stage:post_ad_residual_cap "
                                                        f"round={_midroll_round + 1}",
                                                        flush=True,
                                                    )
                                                    if await watcher.restore_primary_watch_surface():
                                                        topic_notes.append(
                                                            "post_ad_surface:restored_midroll_residual_cap"
                                                        )
                                                    # Reset counters and attempt remaining watch immediately.
                                                    _midroll_residual_return_rounds = 0
                                                    pending_midroll_result = None
                                                    pending_midroll_samples_ended_monotonic = None
                                                    try:
                                                        _cap_next, _cap_note, _cap_extra = await self._continue_main_watch_if_needed(
                                                            watcher=watcher,
                                                            watch_result=watch_result,
                                                            target_watch_seconds=target_watch_seconds,
                                                        )
                                                        if _cap_next is not watch_result:
                                                            watch_result = _cap_next
                                                            watch_verified = bool(getattr(watch_result, "verified", False))
                                                        if _cap_note:
                                                            topic_notes.append(f"residual_cap_fill:{_cap_note}")
                                                        if _cap_note and "main_watch_ad_detected" in _cap_note:
                                                            pending_midroll_result = _cap_extra
                                                            pending_midroll_samples_ended_monotonic = time.monotonic()
                                                            continue
                                                    except Exception as _cap_exc:
                                                        topic_notes.append(f"residual_cap_fill_failed:{type(_cap_exc).__name__}")
                                                    break
                                                pending_midroll_result = _mr_post_ad
                                                pending_midroll_samples_ended_monotonic = (
                                                    time.monotonic()
                                                )
                                    except Exception as exc:
                                        topic_notes.append(f"midroll_post_ad_failed:{type(exc).__name__}")
                                # Start a fresh recording for the next watch chunk
                                if self._config.android_app.probe_screenrecord_enabled:
                                    (
                                        recorder,
                                        recording_handle,
                                    ) = await self._start_recording_handle(
                                        label="midroll",
                                        topic=topic,
                                        adb_serial=device.adb_serial,
                                        round_index=_midroll_round + 1,
                                    )
                                watch_verified = bool(getattr(watch_result, "verified", False))
                                skip_main_watch_after_ad = False
                            except Exception as exc:
                                topic_notes.append(f"midroll_flow_failed:{type(exc).__name__}:{exc}")
                                break  # don't loop on errors
                        # Refresh after main watch loop
                        watch_verified = bool(getattr(watch_result, "verified", False))
                        watch_seconds = self._derive_watch_seconds(
                            list(getattr(watch_result, "samples", []) or [])
                        )
                        # Stop any remaining recording
                        if recorder is not None and recording_handle is not None:
                            _final_video = await self._stop_recording_handle(
                                recorder=recorder,
                                recording_handle=recording_handle,
                            )
                            recording_handle = None
                            # Discard if no ads used this recording segment
                            if _final_video and not topic_watched_ads:
                                _final_abs = self._config.storage.base_path / _final_video
                                logger.info(
                                    "recorder[midroll]: discarded no-ad segment topic=%s path=%s",
                                    topic,
                                    _final_abs,
                                )
                                _final_abs.unlink(missing_ok=True)
                        if not self._config.android_app.session_engagement_enabled:
                            topic_notes.append("engagement_skipped:disabled_for_session_stability")
                        elif opened_title and self._opened_title_is_provisional(opened_title, topic):
                            topic_notes.append("engagement_skipped:provisional_title")
                        else:
                            engagement_samples, engagement_notes = (
                                await self._prepare_engagement_samples(
                                    watcher=watcher,
                                    samples=list(getattr(watch_result, "samples", []) or []),
                                    force_refresh=bool(topic_watched_ads),
                                )
                            )
                            topic_notes.extend(engagement_notes)
                            if self._can_attempt_engagement(engagement_samples):
                                print("[android-session] stage:engagement_after_watch", flush=True)
                                topic_notes.extend(
                                    await self._prepare_for_engagement(
                                        watcher=watcher,
                                    )
                                )
                                engagement_result = await self._run_engagement_safe(
                                    driver=session.driver,
                                    adb_serial=device.adb_serial,
                                    topic=topic,
                                    opened_title=opened_title,
                                    notes=topic_notes,
                                    note_prefix="engagement_failed",
                                )
                                if engagement_result is not None:
                                    liked = engagement_result.liked
                                    already_liked = engagement_result.already_liked
                                    subscribed = engagement_result.subscribed
                                    already_subscribed = engagement_result.already_subscribed
                                    comments_glanced = engagement_result.comments_glanced
                                    topic_notes.extend(
                                        await self._stabilize_playback_after_engagement(
                                            watcher=watcher,
                                        )
                                    )
                            else:
                                topic_notes.append(
                                    f"engagement_skipped:{self._engagement_gate_reason(engagement_samples)}"
                                )
                except Exception as exc:
                    print(
                        f"[android-session] topic:error topic={topic} error={type(exc).__name__}:{exc}",
                        flush=True,
                    )
                    topic_notes.append(f"topic_failed:{type(exc).__name__}:{exc}")
                    lowered_error = str(exc).casefold()
                    if session is not None and (
                        "submit_search timed out" in lowered_error
                        or "submit_search_retry timed out" in lowered_error
                    ):
                        self._append_stage_debug_artifacts(
                            driver=session.driver,
                            topic_notes=topic_notes,
                            topic=topic,
                            stage_label="submit_search_timeout",
                            adb_serial=device.adb_serial if device is not None else None,
                        )
                    if session is not None and "timed out collecting watch samples" in lowered_error:
                        self._append_stage_debug_artifacts(
                            driver=session.driver,
                            topic_notes=topic_notes,
                            topic=topic,
                            stage_label="watch_timeout",
                            adb_serial=device.adb_serial if device is not None else None,
                        )
                    if session is not None and "lost youtube foreground" in lowered_error:
                        self._append_stage_debug_artifacts(
                            driver=session.driver,
                            topic_notes=topic_notes,
                            topic=topic,
                            stage_label="off_youtube",
                            adb_serial=device.adb_serial if device is not None else None,
                        )
                    if (
                        session is not None
                        and (
                            "failed to detect native youtube results list" in lowered_error
                            or "no_result_opened" in lowered_error
                        )
                    ):
                        debug_screen_path, debug_page_source_path = self._write_watch_debug_artifacts(
                            driver=session.driver,
                            topic=f"{topic}_results",
                            adb_serial=device.adb_serial if device is not None else None,
                        )
                        if debug_screen_path is not None:
                            topic_notes.append(f"results_debug:{debug_screen_path}")
                        if debug_page_source_path is not None:
                            topic_notes.append(f"results_debug_xml:{debug_page_source_path}")
                    if self._is_infrastructure_failure(str(exc)):
                        infra_failure_streak += 1
                        topic_notes.append(f"infra_failure_streak:{infra_failure_streak}")
                        topic_notes.extend(
                            self._write_infra_diagnostic_artifacts(
                                topic=topic,
                                stage_label="topic_error",
                                adb_serial=device.adb_serial if device is not None else None,
                            )
                        )
                        if not self._topic_has_meaningful_progress(
                            opened_title=opened_title,
                            watch_verified=watch_verified,
                            watch_seconds=watch_seconds,
                            topic_watched_ads=topic_watched_ads,
                            current_watch_started_at=current_watch_started_at,
                        ):
                            discard_topic_result = True
                            retry_same_topic = self._should_retry_topic_attempt(
                                topic_started_at=topic_started_at,
                                session_started_at=started_at,
                                duration_minutes=duration_minutes,
                            )
                            topic_notes.append(
                                "infra_topic_discarded_before_watch"
                                if retry_same_topic
                                else "infra_topic_discarded_move_next"
                            )
                        elif self._is_session_only_infrastructure_failure(str(exc)):
                            topic_notes.append("infra_topic_partial_progress_preserved")
                        try:
                            if session is not None:
                                await self._stop_system_dialog_watchdog(
                                    stop_event=dialog_watchdog_stop,
                                    task=dialog_watchdog_task,
                                )
                                dialog_watchdog_stop = None
                                dialog_watchdog_task = None
                                launcher_tripwire = None
                                if (
                                    device is not None
                                    and self._is_session_only_infrastructure_failure(str(exc))
                                ):
                                    await self._runtime.appium_provider.close_broken_session(
                                        session,
                                        adb_serial=device.adb_serial,
                                    )
                                else:
                                    await self._runtime.appium_provider.close_session(session)
                        finally:
                            if (
                                device is not None
                                and self._config.android_app.emulator_stop_after_run
                                and not self._is_session_only_infrastructure_failure(str(exc))
                            ):
                                await self._cleanup_device_after_failure(device)
                        session = None
                        _device_gone = self._is_device_gone_failure(str(exc))
                        if not self._is_session_only_infrastructure_failure(str(exc)) or _device_gone:
                            device = None
                        navigator = None
                        watcher = None
                    else:
                        infra_failure_streak = 0
                else:
                    infra_failure_streak = 0
                    print(
                        f"[android-session] topic:done topic={topic} verified={str(watch_verified).lower()} topic_ads={len(topic_watched_ads)}",
                        flush=True,
                    )

                if retry_same_topic:
                    topic_cursor = max(0, topic_cursor - 1)

                if discard_topic_result:
                    print(
                        f"[android-session] topic:discarded topic={topic} reason=infra_before_watch",
                        flush=True,
                    )
                    if infra_failure_streak >= 5:
                        break
                    continue

                if target_watch_seconds <= 0.0:
                    target_watch_seconds = self._decide_session_target_watch_seconds(
                        topic=topic,
                        topics=active_topics,
                        topic_results=topic_results,
                        watch_samples=[],
                        started_at=started_at,
                        duration_minutes=duration_minutes,
                    )
                    topic_notes.append(f"session_target_watch_fallback:{target_watch_seconds}")

                if self._topic_surface_failed_before_watch(
                    topic_notes=topic_notes,
                    opened_title=opened_title,
                    watch_verified=watch_verified,
                    watch_seconds=watch_seconds,
                    topic_watched_ads=topic_watched_ads,
                    current_watch_started_at=current_watch_started_at,
                ):
                    surface_failure_streak += 1
                    topic_notes.append(f"surface_failure_streak:{surface_failure_streak}")
                else:
                    surface_failure_streak = 0

                topic_results.append(
                    AndroidSessionTopicResult(
                        topic=topic,
                        opened_title=opened_title,
                        notes=topic_notes,
                        watch_verified=watch_verified,
                        watch_seconds=watch_seconds,
                        target_watch_seconds=target_watch_seconds,
                        watch_ad_detected=watch_ad_detected,
                        watched_ads=topic_watched_ads,
                        liked=liked,
                        already_liked=already_liked,
                        subscribed=subscribed,
                        already_subscribed=already_subscribed,
                        comments_glanced=comments_glanced,
                    )
                )
                elapsed_seconds = max(0, int(time.monotonic() - started_at))
                artifact_avd_name = device.avd_name if device is not None else resolved_avd_name
                artifact_adb_serial = (
                    device.adb_serial if device is not None else (last_adb_serial or "unknown")
                )
                artifact_server_url = (
                    session.server_url if session is not None else (last_server_url or "unknown")
                )
                artifact_reused_running_device = (
                    device.reused_running_device
                    if device is not None
                    else last_reused_running_device
                )
                artifact_started_local_appium = (
                    session.started_local_server
                    if session is not None
                    else last_started_local_appium
                )
                self._write_session_artifact(
                    artifact_path=session_artifact_path,
                    avd_name=artifact_avd_name,
                    adb_serial=artifact_adb_serial,
                    topics=resolved_topics,
                    duration_minutes_target=duration_minutes,
                    elapsed_seconds=elapsed_seconds,
                    appium_server_url=artifact_server_url,
                    reused_running_device=artifact_reused_running_device,
                    started_local_appium=artifact_started_local_appium,
                    topic_results=topic_results,
                    watched_ads=watched_ads,
                    notes=(
                        [
                            *proxy_notes,
                            *( [f"snapshot:{snapshot_name}"] if snapshot_name else [] ),
                            f"topics:{len(resolved_topics)}",
                            *( [f"duration_minutes_target:{duration_minutes}"] if duration_minutes is not None else [] ),
                            f"elapsed_seconds:{elapsed_seconds}",
                            f"topic_runs:{len(topic_results)}",
                            f"verified_topics:{sum(1 for item in topic_results if item.watch_verified)}",
                            f"ads:{len(watched_ads)}",
                            f"infra_failure_streak:{infra_failure_streak}",
                            f"surface_failure_streak:{surface_failure_streak}",
                            "completed:false",
                        ]
                    ),
                )
                print(
                    f"[android-session] checkpoint topic_runs={len(topic_results)} verified={sum(1 for item in topic_results if item.watch_verified)} ads={len(watched_ads)} infra_failure_streak={infra_failure_streak}",
                    flush=True,
                )
                if on_progress is not None:
                    try:
                        verified_count = sum(1 for tr in topic_results if tr.watch_verified)
                        _progress_videos = [
                            build_topic_watched_video_payload(
                                tr,
                                position=idx + 1,
                                recorded_at=time.time(),
                            )
                            for idx, tr in enumerate(topic_results)
                        ]
                        _progress_ads = [
                            {**ad, "position": idx + 1}
                            for idx, ad in enumerate(watched_ads)
                        ]
                        _current_rx = await _read_rx_bytes()
                        _dl_bytes = max(0, _current_rx - _initial_rx_bytes) if _initial_rx_bytes else 0
                        await on_progress(
                            event="checkpoint",
                            current_topic=topic,
                            current_watch=None,
                            topics_searched=[tr.topic for tr in topic_results],
                            videos_watched=verified_count,
                            watched_videos_count=verified_count,
                            watched_videos=_progress_videos,
                            watched_ads=_progress_ads,
                            watched_ads_count=len(_progress_ads),
                            total_duration_seconds=elapsed_seconds,
                            bytes_downloaded=_dl_bytes,
                        )
                    except Exception as exc:
                        print(f"[android-session] on_progress error: {exc}", flush=True)
                if surface_failure_streak >= 2:
                    topic_notes.append("session_recreate:surface_failure_streak")
                    print(
                        "[android-session] session:recreate_after_surface_failures "
                        f"streak={surface_failure_streak}",
                        flush=True,
                    )
                    try:
                        if session is not None:
                            await self._stop_system_dialog_watchdog(
                                stop_event=dialog_watchdog_stop,
                                task=dialog_watchdog_task,
                            )
                            dialog_watchdog_stop = None
                            dialog_watchdog_task = None
                            launcher_tripwire = None
                            if device is not None:
                                await self._runtime.appium_provider.close_broken_session(
                                    session,
                                    adb_serial=device.adb_serial,
                                )
                            else:
                                await self._runtime.appium_provider.close_session(session)
                    except Exception:
                        pass
                    session = None
                    navigator = None
                    watcher = None
                    surface_failure_streak = 0
                    continue
                if infra_failure_streak >= 5:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    break
                remaining_after_topic = (
                    deadline - time.monotonic()
                    if deadline is not None
                    else float("inf")
                )
                if remaining_after_topic > 75.0:
                    # Skip reset_to_home — submit_search handles transition from
                    # watch surface to next search via deeplink without force-stop,
                    # saving ~15-30s per topic. Only do a hard reset when the
                    # session is nearly over and we just need to clean up.
                    topic_notes.append("topic_post_reset_skipped:deeplink_will_handle")
                    print("[android-session] topic:post_reset:skipped", flush=True)
                else:
                    topic_notes.append("topic_post_reset_required:endgame_cleanup")
                if navigator is not None and "topic_post_reset_skipped" not in " ".join(topic_notes):
                    _post_reset_exc: Exception | None = None
                    try:
                        await asyncio.wait_for(
                            self._reset_to_home_with_timeout(
                                navigator=navigator,
                                topic_notes=topic_notes,
                                stage_label="topic_post_reset",
                                launcher_tripwire=launcher_tripwire,
                                timeout_seconds=35.0,
                            ),
                            timeout=40.0,
                        )
                        print("[android-session] topic:post_reset:home", flush=True)
                    except asyncio.TimeoutError:
                        topic_notes.append("topic_post_reset_hard_timeout")
                        print("[android-session] topic:post_reset:hard_timeout", flush=True)
                        _post_reset_exc = AndroidUiError("topic_post_reset timed out")
                        if device is not None:
                            try:
                                _adb_bin = require_tool_path("adb")
                                subprocess.run(
                                    [_adb_bin, "-s", device.adb_serial, "shell",
                                     "am", "force-stop", "com.google.android.youtube"],
                                    check=False,
                                    capture_output=True,
                                    env=build_android_runtime_env(),
                                    timeout=10,
                                )
                                print("[android-session] topic:post_reset:force_stopped_yt", flush=True)
                            except Exception:
                                pass
                    except Exception as exc:
                        _post_reset_exc = exc
                    if _post_reset_exc is not None:
                        exc = _post_reset_exc
                        print(
                            f"[android-session] topic:post_reset:failed {type(exc).__name__}:{exc}",
                            flush=True,
                        )
                        if self._is_infrastructure_failure(str(exc)):
                            infra_failure_streak += 1
                            topic_notes.append(
                                f"post_reset_infra_failure_streak:{infra_failure_streak}"
                            )
                            try:
                                if session is not None:
                                    await self._stop_system_dialog_watchdog(
                                        stop_event=dialog_watchdog_stop,
                                        task=dialog_watchdog_task,
                                    )
                                    dialog_watchdog_stop = None
                                    dialog_watchdog_task = None
                                    launcher_tripwire = None
                                    if (
                                        device is not None
                                        and self._is_session_only_infrastructure_failure(str(exc))
                                    ):
                                        await self._runtime.appium_provider.close_broken_session(
                                            session,
                                            adb_serial=device.adb_serial,
                                        )
                                    else:
                                        await self._runtime.appium_provider.close_session(session)
                            except Exception:
                                pass
                            session = None
                            _device_gone = self._is_device_gone_failure(str(exc))
                            if (
                                not self._is_session_only_infrastructure_failure(str(exc))
                                or _device_gone
                            ):
                                device = None
                            navigator = None
                            watcher = None
                            if infra_failure_streak >= 5:
                                break
                            continue

            elapsed_seconds = max(0, int(time.monotonic() - started_at))
            artifact_avd_name = device.avd_name if device is not None else resolved_avd_name
            artifact_adb_serial = (
                device.adb_serial if device is not None else (last_adb_serial or "unknown")
            )
            artifact_server_url = (
                session.server_url if session is not None else (last_server_url or "unknown")
            )
            artifact_reused_running_device = (
                device.reused_running_device
                if device is not None
                else last_reused_running_device
            )
            artifact_started_local_appium = (
                session.started_local_server
                if session is not None
                else last_started_local_appium
            )
            await ad_analysis.drain(timeout_seconds=90.0)
            if landing_scraper is not None:
                with contextlib.suppress(Exception):
                    await landing_scraper.drain(timeout_seconds=60.0)
                with contextlib.suppress(Exception):
                    await landing_scraper.stop()
            with contextlib.suppress(Exception):
                self._backfill_advertiser_from_landing_scrape(watched_ads)
            with contextlib.suppress(Exception):
                self._dedupe_watched_ads(watched_ads)
            self._cleanup_irrelevant_ad_videos(watched_ads)
            artifact_path = self._write_session_artifact(
                artifact_path=session_artifact_path,
                avd_name=artifact_avd_name,
                adb_serial=artifact_adb_serial,
                topics=resolved_topics,
                duration_minutes_target=duration_minutes,
                elapsed_seconds=elapsed_seconds,
                appium_server_url=artifact_server_url,
                reused_running_device=artifact_reused_running_device,
                started_local_appium=artifact_started_local_appium,
                topic_results=topic_results,
                watched_ads=watched_ads,
                notes=(
                    [
                        *proxy_notes,
                        *( [f"snapshot:{snapshot_name}"] if snapshot_name else [] ),
                        f"topics:{len(resolved_topics)}",
                        *( [f"duration_minutes_target:{duration_minutes}"] if duration_minutes is not None else [] ),
                        f"elapsed_seconds:{elapsed_seconds}",
                        f"topic_runs:{len(topic_results)}",
                        f"verified_topics:{sum(1 for item in topic_results if item.watch_verified)}",
                        f"ads:{len(watched_ads)}",
                        f"infra_failure_streak:{infra_failure_streak}",
                        "completed:true",
                    ]
                ),
            )

            _final_rx = await _read_rx_bytes()
            _final_bytes_downloaded = max(0, _final_rx - _initial_rx_bytes) if _initial_rx_bytes else 0
            final_watched_ads = self._normalize_watched_ad_positions(watched_ads)

            return AndroidSessionRunResult(
                avd_name=artifact_avd_name,
                adb_serial=artifact_adb_serial,
                topics=resolved_topics,
                artifact_path=artifact_path,
                appium_server_url=artifact_server_url,
                duration_minutes_target=duration_minutes,
                elapsed_seconds=elapsed_seconds,
                reused_running_device=artifact_reused_running_device,
                started_local_appium=artifact_started_local_appium,
                topic_results=topic_results,
                watched_ads=final_watched_ads,
                bytes_downloaded=_final_bytes_downloaded,
            )
        finally:
            with contextlib.suppress(Exception):
                await ad_analysis.drain(timeout_seconds=5.0)
            if landing_scraper is not None:
                with contextlib.suppress(Exception):
                    await landing_scraper.drain(timeout_seconds=60.0)
                with contextlib.suppress(Exception):
                    await landing_scraper.stop()
            with contextlib.suppress(Exception):
                self._backfill_advertiser_from_landing_scrape(watched_ads)
            print("[android-session] shutdown:start", flush=True)
            try:
                await self._stop_system_dialog_watchdog(
                    stop_event=dialog_watchdog_stop,
                    task=dialog_watchdog_task,
                )
                if session is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(
                            self._runtime.appium_provider.close_session(session),
                            timeout=30,
                        )
            finally:
                if (
                    device is not None
                    and self._config.android_app.emulator_stop_after_run
                ):
                    try:
                        await asyncio.wait_for(
                            self._runtime.avd_manager.stop_device(
                                device.adb_serial,
                                avd_name=device.avd_name,
                            ),
                            timeout=30,
                        )
                    except Exception:
                        with contextlib.suppress(Exception):
                            await self._runtime.avd_manager.force_cleanup_device(
                                adb_serial=device.adb_serial,
                                avd_name=device.avd_name,
                            )
                if proxy_bridge_handle is not None:
                    with contextlib.suppress(Exception):
                        await self._proxy_bridge.stop(proxy_bridge_handle)
            print("[android-session] shutdown:done", flush=True)

    async def _system_dialog_watchdog_loop(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        stop_event: asyncio.Event,
        tripwire_event: asyncio.Event,
        log_prefix: str,
    ) -> None:
        handled_timestamps: list[float] = []
        while not stop_event.is_set():
            try:
                handled = await navigator.dismiss_system_dialogs_via_adb_background()
                if handled:
                    now = time.monotonic()
                    handled_timestamps = [
                        timestamp
                        for timestamp in handled_timestamps
                        if now - timestamp <= 10.0
                    ]
                    handled_timestamps.append(now)
                    print(f"{log_prefix} watchdog:system_dialog_wait_clicked", flush=True)
                    if len(handled_timestamps) >= 2 and not tripwire_event.is_set():
                        tripwire_event.set()
                        print(
                            f"{log_prefix} watchdog:launcher_tripwire_set waits={len(handled_timestamps)}",
                            flush=True,
                        )
                        handled_timestamps.clear()
                        continue
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=1.2)
                    except asyncio.TimeoutError:
                        pass
                    continue
                handled_timestamps.clear()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"{log_prefix} watchdog:error error={type(exc).__name__}:{exc}",
                    flush=True,
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2.5)
            except asyncio.TimeoutError:
                continue

    async def _stop_system_dialog_watchdog(
        self,
        *,
        stop_event: asyncio.Event | None,
        task: asyncio.Task[None] | None,
    ) -> None:
        if stop_event is not None:
            stop_event.set()
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _ensure_app_ready_with_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic_notes: list[str],
        stage_label: str,
        launcher_tripwire: asyncio.Event | None,
        timeout_seconds: float = 24.0,
    ) -> None:
        self._raise_if_launcher_tripwire_set(
            launcher_tripwire=launcher_tripwire,
            stage_label=stage_label,
        )
        ensure_task = asyncio.create_task(navigator.ensure_app_ready())
        tripwire_task = (
            asyncio.create_task(launcher_tripwire.wait())
            if launcher_tripwire is not None
            else None
        )
        try:
            done, _ = await asyncio.wait(
                {ensure_task, *( [tripwire_task] if tripwire_task is not None else [] )},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ensure_task in done:
                await ensure_task
                return
            if tripwire_task is not None and tripwire_task in done:
                raise AndroidUiError(f"launcher anr burst detected during {stage_label}")
            topic_notes.append(f"{stage_label}_timeout")
            with contextlib.suppress(Exception):
                package, activity, page_source_length = await navigator.describe_surface()
                topic_notes.append(
                    f"{stage_label}_foreground:{package}:{activity}:xml={page_source_length}"
                )
                if package == self._config.android_app.youtube_package:
                    topic_notes.append(f"{stage_label}_timeout_proceed:youtube_foreground")
                    return
            recovered = await self._recover_from_launcher_anr_with_timeout(
                navigator=navigator,
            )
            topic_notes.append(f"{stage_label}_launcher_recovery:{str(recovered).lower()}")
            self._raise_if_launcher_tripwire_set(
                launcher_tripwire=launcher_tripwire,
                stage_label=stage_label,
            )
            raise AndroidUiError(f"{stage_label} timed out")
        finally:
            ensure_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                await asyncio.wait_for(asyncio.shield(ensure_task), timeout=0.5)
            if tripwire_task is not None:
                tripwire_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                    await asyncio.wait_for(asyncio.shield(tripwire_task), timeout=0.5)

    def _check_emulator_network_sync(self, adb_serial: str | None) -> bool:
        """Quick network connectivity check via ADB ping inside the emulator.

        Returns True if network is reachable, False otherwise.
        Uses a 3-second timeout so it doesn't block the session loop long.
        """
        if not adb_serial:
            return True  # No ADB — assume ok, let normal flow detect issues
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s", adb_serial,
                    "shell",
                    "ping", "-c", "1", "-W", "3", "8.8.8.8",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=8,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _recover_emulator_network_sync(self, adb_serial: str | None) -> bool:
        if not adb_serial:
            return True
        adb_bin = require_tool_path("adb")
        env = build_android_runtime_env()
        recovery_commands: tuple[tuple[str, ...], ...] = (
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "settings",
                "put",
                "global",
                "airplane_mode_on",
                "0",
            ),
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "am",
                "broadcast",
                "-a",
                "android.intent.action.AIRPLANE_MODE",
                "--ez",
                "state",
                "false",
            ),
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "svc",
                "wifi",
                "enable",
            ),
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "svc",
                "data",
                "enable",
            ),
        )
        try:
            for command in recovery_commands:
                subprocess.run(
                    command,
                    capture_output=True,
                    check=False,
                    env=env,
                    text=True,
                    timeout=20,
                )
        except Exception:
            return False

        for _ in range(3):
            time.sleep(2)
            if self._check_emulator_network_sync(adb_serial):
                return True
        return False

    async def _ensure_topic_network_ready(
        self,
        *,
        adb_serial: str | None,
        topic: str,
        topic_notes: list[str],
    ) -> bool:
        loop = asyncio.get_running_loop()
        network_ok = await loop.run_in_executor(
            None,
            self._check_emulator_network_sync,
            adb_serial,
        )
        if network_ok:
            return True

        topic_notes.append("network_check_failed:first_probe")
        print(
            f"[android-session] network_check_failed:first_probe topic={topic}",
            flush=True,
        )
        network_recovered = await loop.run_in_executor(
            None,
            self._recover_emulator_network_sync,
            adb_serial,
        )
        topic_notes.append(f"network_check_recovered:{str(network_recovered).lower()}")
        if network_recovered:
            print(
                f"[android-session] network_check_recovered topic={topic}",
                flush=True,
            )
            return True

        print(
            f"[android-session] network_check_failed:soft_proceed topic={topic}",
            flush=True,
        )
        return False

    async def _submit_search_with_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
        launcher_tripwire: asyncio.Event | None,
        timeout_seconds: float = 35.0,
    ) -> None:
        started_at = time.monotonic()
        self._raise_if_launcher_tripwire_set(
            launcher_tripwire=launcher_tripwire,
            stage_label=stage_label,
        )
        submit_task = asyncio.create_task(
            navigator.submit_search(topic, deadline=time.monotonic() + timeout_seconds)
        )
        tripwire_task = (
            asyncio.create_task(launcher_tripwire.wait())
            if launcher_tripwire is not None
            else None
        )
        try:
            done, _ = await asyncio.wait(
                {submit_task, *( [tripwire_task] if tripwire_task is not None else [] )},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if submit_task in done:
                await submit_task
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return
            if tripwire_task is not None and tripwire_task in done:
                raise AndroidUiError(f"launcher anr burst detected during {stage_label}")
            topic_notes.append(f"{stage_label}_timeout")
            with contextlib.suppress(Exception):
                await navigator.cancel_active_sync_operation()
            if await self._handle_stage_foreground_loss(
                navigator=navigator,
                topic_notes=topic_notes,
                stage_label=stage_label,
            ):
                raise AndroidUiError(f"{stage_label} lost youtube foreground")
            task_drained = await self._drain_cancelled_stage_task(
                task=submit_task,
                stage_label=stage_label,
            )
            if not task_drained:
                topic_notes.append(f"{stage_label}_cleanup_timed_out")
                raise AndroidUiError(f"{stage_label} cleanup timed out")
            with contextlib.suppress(Exception):
                if await navigator.has_query_ready_surface_via_adb(topic):
                    topic_notes.append(f"{stage_label}_timeout_recovered:surface_ready_adb")
                    topic_notes.append(
                        f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}"
                    )
                    return
            with contextlib.suppress(Exception):
                if await navigator.has_query_results_surface(topic):
                    topic_notes.append(f"{stage_label}_timeout_recovered:results_ready")
                    topic_notes.append(
                        f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}"
                    )
                    return
            topic_notes.append(f"{stage_label}_timeout_proceed_to_wait_for_results")
            topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
            return
        finally:
            submit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                await asyncio.wait_for(asyncio.shield(submit_task), timeout=0.5)
            if tripwire_task is not None:
                tripwire_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                    await asyncio.wait_for(asyncio.shield(tripwire_task), timeout=0.5)

    async def _wait_for_results_with_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
        launcher_tripwire: asyncio.Event | None,
        timeout_seconds: float = 16.0,
    ) -> None:
        started_at = time.monotonic()
        self._raise_if_launcher_tripwire_set(
            launcher_tripwire=launcher_tripwire,
            stage_label=stage_label,
        )
        wait_task = asyncio.create_task(
            navigator.wait_for_results(topic, deadline=time.monotonic() + timeout_seconds)
        )
        tripwire_task = (
            asyncio.create_task(launcher_tripwire.wait())
            if launcher_tripwire is not None
            else None
        )
        try:
            done, _ = await asyncio.wait(
                {wait_task, *( [tripwire_task] if tripwire_task is not None else [] )},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wait_task in done:
                await wait_task
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return
            if tripwire_task is not None and tripwire_task in done:
                raise AndroidUiError(f"launcher anr burst detected during {stage_label}")
            topic_notes.append(f"{stage_label}_timeout")
            with contextlib.suppress(Exception):
                await navigator.cancel_active_sync_operation()
            if await self._handle_stage_foreground_loss(
                navigator=navigator,
                topic_notes=topic_notes,
                stage_label=stage_label,
            ):
                raise AndroidUiError(f"{stage_label} lost youtube foreground")
            task_drained = await self._drain_cancelled_stage_task(
                task=wait_task,
                stage_label=stage_label,
            )
            if not task_drained:
                topic_notes.append(f"{stage_label}_cleanup_timed_out")
                raise AndroidUiError(f"{stage_label} cleanup timed out")
            with contextlib.suppress(Exception):
                if await navigator.has_query_ready_surface_via_adb(topic):
                    topic_notes.append(f"{stage_label}_timeout_recovered:surface_ready_adb")
                    topic_notes.append(
                        f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}"
                    )
                    return
            with contextlib.suppress(Exception):
                if await navigator.has_query_ready_surface(topic):
                    topic_notes.append(f"{stage_label}_timeout_recovered:surface_ready")
                    topic_notes.append(
                        f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}"
                    )
                    return
            recovered = await self._recover_from_launcher_anr_with_timeout(
                navigator=navigator,
            )
            topic_notes.append(f"{stage_label}_launcher_recovery:{str(recovered).lower()}")
            self._raise_if_launcher_tripwire_set(
                launcher_tripwire=launcher_tripwire,
                stage_label=stage_label,
            )
            raise AndroidUiError("Failed to detect native YouTube results list")
        finally:
            wait_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.5)
            if tripwire_task is not None:
                tripwire_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                    await asyncio.wait_for(asyncio.shield(tripwire_task), timeout=0.5)

    async def _open_first_result_with_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
        launcher_tripwire: asyncio.Event | None,
        timeout_seconds: float = 50.0,
    ) -> str | None:
        started_at = time.monotonic()
        self._raise_if_launcher_tripwire_set(
            launcher_tripwire=launcher_tripwire,
            stage_label=stage_label,
        )
        open_task = asyncio.create_task(
            navigator.open_first_result(topic, deadline=time.monotonic() + timeout_seconds)
        )
        tripwire_task = (
            asyncio.create_task(launcher_tripwire.wait())
            if launcher_tripwire is not None
            else None
        )
        try:
            done, _ = await asyncio.wait(
                {open_task, *( [tripwire_task] if tripwire_task is not None else [] )},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if open_task in done:
                opened = await open_task
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return opened
            if tripwire_task is not None and tripwire_task in done:
                raise AndroidUiError(f"launcher anr burst detected during {stage_label}")
            topic_notes.append(f"{stage_label}_timeout")
            with contextlib.suppress(Exception):
                await navigator.cancel_active_sync_operation()
            task_drained = await self._drain_cancelled_stage_task(
                task=open_task,
                stage_label=stage_label,
            )
            if not task_drained:
                topic_notes.append(f"{stage_label}_cleanup_timed_out")
            recovered_title = await self._recover_open_result_timeout_surface(
                navigator=navigator,
                topic=topic,
                topic_notes=topic_notes,
                stage_label=stage_label,
            )
            if recovered_title:
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return recovered_title
            last_resort_title = await self._last_resort_open_any_result(
                navigator=navigator,
                topic=topic,
                topic_notes=topic_notes,
                stage_label=stage_label,
            )
            if last_resort_title:
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return last_resort_title
            recovered = await self._recover_from_launcher_anr_with_timeout(
                navigator=navigator,
            )
            topic_notes.append(f"{stage_label}_launcher_recovery:{str(recovered).lower()}")
            self._raise_if_launcher_tripwire_set(
                launcher_tripwire=launcher_tripwire,
                stage_label=stage_label,
            )
            return None
        except asyncio.TimeoutError:
            topic_notes.append(f"{stage_label}_timeout")
            with contextlib.suppress(Exception):
                await navigator.cancel_active_sync_operation()
            recovered_title = await self._recover_open_result_timeout_surface(
                navigator=navigator,
                topic=topic,
                topic_notes=topic_notes,
                stage_label=stage_label,
            )
            if recovered_title:
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return recovered_title
            last_resort_title = await self._last_resort_open_any_result(
                navigator=navigator,
                topic=topic,
                topic_notes=topic_notes,
                stage_label=stage_label,
            )
            if last_resort_title:
                topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
                return last_resort_title
            recovered = await self._recover_from_launcher_anr_with_timeout(
                navigator=navigator,
            )
            topic_notes.append(f"{stage_label}_launcher_recovery:{str(recovered).lower()}")
            self._raise_if_launcher_tripwire_set(
                launcher_tripwire=launcher_tripwire,
                stage_label=stage_label,
            )
            return None
        finally:
            open_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                await asyncio.wait_for(asyncio.shield(open_task), timeout=0.5)
            if tripwire_task is not None:
                tripwire_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                    await asyncio.wait_for(asyncio.shield(tripwire_task), timeout=0.5)

    async def _reset_to_home_with_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic_notes: list[str],
        stage_label: str,
        launcher_tripwire: asyncio.Event | None,
        timeout_seconds: float = 25.0,
    ) -> None:
        started_at = time.monotonic()
        self._raise_if_launcher_tripwire_set(
            launcher_tripwire=launcher_tripwire,
            stage_label=stage_label,
        )
        try:
            await navigator.reset_to_home(deadline=time.monotonic() + timeout_seconds)
            topic_notes.append(f"{stage_label}_seconds:{time.monotonic() - started_at:.1f}")
            return
        except AndroidUiError as exc:
            if "hard deadline exceeded" not in str(exc):
                raise
        topic_notes.append(f"{stage_label}_timeout")
        recovered = await self._recover_from_launcher_anr_with_timeout(
            navigator=navigator,
        )
        topic_notes.append(f"{stage_label}_launcher_recovery:{str(recovered).lower()}")
        self._raise_if_launcher_tripwire_set(
            launcher_tripwire=launcher_tripwire,
            stage_label=stage_label,
        )
        if recovered:
            return
        raise AndroidUiError(f"{stage_label} timed out")

    async def _drain_cancelled_stage_task(
        self,
        *,
        task: asyncio.Task[object],
        stage_label: str,
        timeout_seconds: float = 1.5,
    ) -> bool:
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.CancelledError:
            return True
        except TimeoutError:
            return False
        except Exception:
            return True
        return True

    async def _last_resort_open_any_result(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
    ) -> str | None:
        """After a timeout, retry open_first_result with a short budget.

        Handles cases where the initial attempt timed out because the results
        surface was blocked by ads/channel cards at the top — open_first_result
        will scroll past them via the last-resort scroll loop in navigator.
        """
        try:
            opened = await asyncio.wait_for(
                navigator.open_first_result(topic, deadline=time.monotonic() + 20.0),
                timeout=25.0,
            )
        except Exception:
            opened = None
        if opened:
            topic_notes.append(f"{stage_label}_last_resort_opened")
        else:
            topic_notes.append(f"{stage_label}_last_resort_failed")
        return opened

    async def _recover_open_result_timeout_surface(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
    ) -> str | None:
        try:
            promoted_title = await asyncio.wait_for(
                navigator.promote_current_watch_surface(
                    topic,
                    deadline=time.monotonic() + 2.0,
                ),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            topic_notes.append(f"{stage_label}_timeout_watch_surface_probe_timeout")
            promoted_title = None
        except Exception:
            promoted_title = None
        if promoted_title:
            topic_notes.append(f"{stage_label}_timeout_recovered:watch_surface")
            return promoted_title

        try:
            recovered_title = await asyncio.wait_for(
                navigator.await_current_watch_title(
                    topic,
                    timeout_seconds=2.5,
                    deadline=time.monotonic() + 2.5,
                ),
                timeout=4.0,
            )
        except asyncio.TimeoutError:
            topic_notes.append(f"{stage_label}_timeout_watch_title_probe_timeout")
            recovered_title = None
        except Exception:
            recovered_title = None
        if recovered_title:
            topic_notes.append(f"{stage_label}_timeout_recovered:watch_title")
            return recovered_title

        try:
            watch_surface = await asyncio.wait_for(
                navigator.has_watch_surface_for_query(),
                timeout=1.5,
            )
        except Exception:
            watch_surface = False
        if not watch_surface:
            return None

        try:
            surface_title = await asyncio.wait_for(
                navigator.await_current_watch_title(
                    timeout_seconds=1.5,
                    deadline=time.monotonic() + 1.5,
                ),
                timeout=3.0,
            )
        except Exception:
            surface_title = None
        if surface_title:
            topic_notes.append(f"{stage_label}_timeout_recovered:watch_surface")
            return surface_title
        return None

    async def _recover_from_launcher_anr_with_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        timeout_seconds: float = 8.0,
    ) -> bool:
        try:
            return await asyncio.wait_for(
                navigator.recover_from_launcher_anr(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            return False

    async def _await_current_watch_title_with_hard_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
        timeout_seconds: float,
    ) -> str | None:
        try:
            return await asyncio.wait_for(
                navigator.await_current_watch_title(
                    topic,
                    timeout_seconds=timeout_seconds,
                    deadline=time.monotonic() + timeout_seconds,
                ),
                timeout=timeout_seconds + 2.0,
            )
        except AndroidUiError as exc:
            if "hard deadline exceeded" in str(exc):
                topic_notes.append(f"{stage_label}_timeout")
                return None
            raise
        except asyncio.TimeoutError:
            topic_notes.append(f"{stage_label}_timeout")
            return None

    async def _provisional_watch_title_with_hard_timeout(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        topic_notes: list[str],
        stage_label: str,
        timeout_seconds: float,
    ) -> str | None:
        try:
            return await asyncio.wait_for(
                navigator.provisional_watch_title(
                    topic,
                    deadline=time.monotonic() + timeout_seconds,
                ),
                timeout=timeout_seconds,
            )
        except AndroidUiError as exc:
            if "hard deadline exceeded" in str(exc):
                topic_notes.append(f"{stage_label}_timeout")
                return None
            raise
        except asyncio.TimeoutError:
            topic_notes.append(f"{stage_label}_timeout")
            return None

    async def _handle_stage_foreground_loss(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic_notes: list[str],
        stage_label: str,
    ) -> bool:
        try:
            package, activity = await asyncio.wait_for(
                navigator.current_package_activity(),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            topic_notes.append(f"{stage_label}_foreground_probe_timeout")
            return False
        except Exception as exc:
            topic_notes.append(f"{stage_label}_foreground_probe_failed:{type(exc).__name__}")
            return False
        topic_notes.append(
            f"{stage_label}_foreground:{package or 'unknown'}:{activity or 'unknown'}"
        )
        if package == self._config.android_app.youtube_package:
            return False
        try:
            await navigator.ensure_app_ready()
            recovered_package, recovered_activity = await asyncio.wait_for(
                navigator.current_package_activity(),
                timeout=2.0,
            )
            topic_notes.append(
                f"{stage_label}_foreground_recovery:true:{recovered_package or 'unknown'}:{recovered_activity or 'unknown'}"
            )
            if recovered_package == self._config.android_app.youtube_package:
                return False
        except Exception as exc:
            topic_notes.append(
                f"{stage_label}_foreground_recovery:false:{type(exc).__name__}"
            )
        return True

    async def _cleanup_device_after_failure(self, device: object) -> None:
        adb_serial = getattr(device, "adb_serial", None)
        avd_name = getattr(device, "avd_name", None)
        if not adb_serial or not avd_name:
            return
        try:
            await asyncio.wait_for(
                self._runtime.avd_manager.stop_device(
                    adb_serial,
                    avd_name=avd_name,
                ),
                timeout=30,
            )
        except Exception:
            with contextlib.suppress(Exception):
                await self._runtime.avd_manager.force_cleanup_device(
                    adb_serial=adb_serial,
                    avd_name=avd_name,
                )
        else:
            with contextlib.suppress(Exception):
                await self._runtime.avd_manager.force_cleanup_device(
                    adb_serial=adb_serial,
                    avd_name=avd_name,
                )

    def _write_infra_diagnostic_artifacts(
        self,
        *,
        topic: str,
        stage_label: str,
        adb_serial: str | None,
    ) -> list[str]:
        notes: list[str] = []
        artifact_dir = self._config.storage.base_path / self._config.android_app.artifacts_subdir
        artifact_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_topic = self._build_safe_artifact_prefix(f"{topic}_{stage_label}")

        appium_tail = self._runtime.appium_provider.tail_server_log(lines=160)
        if appium_tail:
            appium_path = artifact_dir / f"{timestamp}_{safe_topic}_appium.log"
            appium_path.write_text(appium_tail, encoding="utf-8")
            notes.append(f"{stage_label}_appium_log:{appium_path}")

        if adb_serial:
            logcat_path = self._write_logcat_snapshot_sync(
                adb_serial=adb_serial,
                output_path=artifact_dir / f"{timestamp}_{safe_topic}_logcat.log",
            )
            if logcat_path is not None:
                notes.append(f"{stage_label}_logcat:{logcat_path}")

        return notes

    def _write_logcat_snapshot_sync(
        self,
        *,
        adb_serial: str,
        output_path: Path,
    ) -> Path | None:
        try:
            result = subprocess.run(
                [
                    require_tool_path("adb"),
                    "-s",
                    adb_serial,
                    "logcat",
                    "-d",
                    "-t",
                    "500",
                ],
                capture_output=True,
                check=False,
                env=build_android_runtime_env(),
                text=True,
                timeout=30,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        filtered_lines = [
            line
            for line in (result.stdout or "").splitlines()
            if any(
                token in line.casefold()
                for token in (
                    "activitymanager",
                    "windowmanager",
                    "androidruntime",
                    "uiautomator",
                    "appium",
                    "instrumentation",
                    "anr",
                    "inputdispatcher",
                    "launcher",
                    # Added: URL / navigation / CTA debug
                    "customtab",
                    "chrometabbrowser",
                    "chromium",
                    "chromecustomtab",
                    "intentactivity",
                    "browsableactivity",
                    "intentresolution",
                    "youtube",
                    "com.google.android",
                )
            )
        ]
        content = "\n".join(filtered_lines[-400:] or (result.stdout or "").splitlines()[-400:])
        output_path.write_text(content, encoding="utf-8")
        return output_path

    @staticmethod
    def _raise_if_launcher_tripwire_set(
        *,
        launcher_tripwire: asyncio.Event | None,
        stage_label: str,
    ) -> None:
        if launcher_tripwire is not None and launcher_tripwire.is_set():
            raise AndroidUiError(f"launcher anr burst detected during {stage_label}")

    def _write_session_artifact(
        self,
        *,
        artifact_path: Path | None = None,
        avd_name: str,
        adb_serial: str,
        topics: list[str],
        duration_minutes_target: int | None,
        elapsed_seconds: int,
        appium_server_url: str,
        reused_running_device: bool,
        started_local_appium: bool,
        topic_results: list[AndroidSessionTopicResult],
        watched_ads: list[dict[str, object]],
        notes: list[str],
    ) -> Path:
        base_dir = (
            self._config.storage.base_path
            / self._config.android_app.session_artifacts_subdir
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        if artifact_path is None:
            artifact_path = self._build_session_artifact_path()
        positioned_watched_ads = self._normalize_watched_ad_positions(watched_ads)
        summary = self._build_session_summary(
            topic_results=topic_results,
            watched_ads=positioned_watched_ads,
            elapsed_seconds=elapsed_seconds,
        )
        payload = {
            "avd_name": avd_name,
            "adb_serial": adb_serial,
            "topics": topics,
            "duration_minutes_target": duration_minutes_target,
            "elapsed_seconds": elapsed_seconds,
            "appium_server_url": appium_server_url,
            "reused_running_device": reused_running_device,
            "started_local_appium": started_local_appium,
            "topic_results": [asdict(item) for item in topic_results],
            "watched_ads": [
                self._flatten_watched_ad_summary(item)
                for item in positioned_watched_ads
            ],
            "videos_verified": sum(1 for item in topic_results if item.watch_verified),
            "watched_ads_count": len(positioned_watched_ads),
            "ad_analysis_done": self._count_ad_analysis_done(positioned_watched_ads),
            "ad_analysis_terminal": self._count_ad_analysis_terminal(positioned_watched_ads),
            "session_summary": summary,
            "notes": notes,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return artifact_path

    @staticmethod
    def _build_session_summary(
        *,
        topic_results: list[AndroidSessionTopicResult],
        watched_ads: list[dict[str, object]],
        elapsed_seconds: int,
    ) -> dict[str, object]:
        """Aggregate per-topic stage timings into a session-level efficiency summary.

        Reads existing *_seconds:N.N notes written by each stage and produces
        counters that make run-to-run comparison easy without manual log parsing.
        """
        def _sum_notes(notes: list[str], prefix: str) -> float:
            total = 0.0
            for n in notes:
                if n.startswith(prefix) and ":" in n:
                    try:
                        total += float(n.rsplit(":", 1)[1])
                    except (ValueError, IndexError):
                        pass
            return total

        def _count_tag(notes: list[str], tag: str) -> int:
            return sum(1 for n in notes if n == tag)

        def _count_prefix(notes: list[str], prefix: str) -> int:
            return sum(1 for n in notes if n.startswith(prefix))

        video_watch_seconds = 0.0
        bootstrap_overhead_seconds = 0.0
        post_reset_overhead_seconds = 0.0
        ad_flow_overhead_seconds = 0.0
        submit_search_seconds = 0.0
        wait_for_results_seconds = 0.0
        open_first_result_seconds = 0.0
        topics_started = len(topic_results)
        topics_completed = sum(1 for tr in topic_results if tr.watch_verified)
        topic_reset_attempts = 0
        topic_reset_timeouts = 0
        wait_for_results_timeouts = 0
        search_cycles = topics_started
        no_result_opened_count = 0
        open_result_no_surface_count = 0
        open_result_no_candidates_count = 0
        watch_opened_but_rejected_count = 0
        zero_watch_topics_count = 0
        midroll_duplicate_count = 0
        midroll_duplicate_break_count = 0
        midroll_duplicate_recording_discarded_count = 0
        post_ad_residual_detected_count = 0
        analysis_failed_count = 0
        focused_video_count = 0
        mixed_identity_detected_count = 0

        for tr in topic_results:
            notes = tr.notes or []
            # session_target_watch:N is the actual main-watch duration; watch_seconds
            # only covers the short initial probe, so use target note when present.
            target_from_notes = (
                _sum_notes(notes, "session_target_watch:")
                + _sum_notes(notes, "session_target_watch_fallback:")
            )
            if target_from_notes > 0:
                video_watch_seconds += target_from_notes
            elif tr.watch_seconds:
                video_watch_seconds += tr.watch_seconds
            if (
                not tr.watch_verified
                and isinstance(tr.target_watch_seconds, (int, float))
                and tr.target_watch_seconds > 0
            ):
                zero_watch_topics_count += 1
            # Sum first-attempt and retry timings together — both contribute to
            # real wall-clock overhead and must be included for accurate per-topic
            # bootstrap cost.
            s = _sum_notes(notes, "submit_search_seconds:") + _sum_notes(notes, "submit_search_retry_seconds:")
            w = _sum_notes(notes, "wait_for_results_seconds:") + _sum_notes(notes, "wait_for_results_retry_seconds:")
            o = _sum_notes(notes, "open_first_result_seconds:") + _sum_notes(notes, "open_first_result_retry_seconds:")
            r = _sum_notes(notes, "topic_post_reset_seconds:")
            submit_search_seconds += s
            wait_for_results_seconds += w
            open_first_result_seconds += o
            bootstrap_overhead_seconds += s + w + o
            post_reset_overhead_seconds += r
            # ad_flow_overhead: these notes are not yet written by the runner;
            # kept as placeholders for when Iteration C adds cta/landing timing notes.
            ad_flow_overhead_seconds += _sum_notes(notes, "cta_seconds:")
            ad_flow_overhead_seconds += _sum_notes(notes, "landing_seconds:")
            ad_flow_overhead_seconds += _sum_notes(notes, "ad_return_seconds:")
            topic_reset_attempts += _count_tag(notes, "topic_post_reset_timeout") + (
                1 if any(n.startswith("topic_post_reset_seconds:") for n in notes) else 0
            )
            topic_reset_timeouts += _count_tag(notes, "topic_post_reset_timeout")
            wait_for_results_timeouts += (
                _count_tag(notes, "wait_for_results_timeout")
                + _count_tag(notes, "wait_for_results_failed:first_attempt")
                + _count_tag(notes, "wait_for_results_failed:retry")
            )
            no_result_opened_count += _count_tag(notes, "no_result_opened")
            open_result_no_surface_count += _count_prefix(notes, "open_result_failure:no_surface:")
            open_result_no_candidates_count += _count_prefix(notes, "open_result_failure:no_candidates:")
            watch_opened_but_rejected_count += _count_prefix(notes, "watch_opened_but_rejected:")
            midroll_duplicate_count += _count_prefix(notes, "midroll_ad_skip_duplicate:")
            midroll_duplicate_break_count += _count_prefix(notes, "midroll_duplicate_cap_break:")
            midroll_duplicate_recording_discarded_count += _count_prefix(
                notes,
                "midroll_duplicate_recording_discarded:",
            )
            post_ad_residual_detected_count += _count_tag(
                notes,
                "post_ad_residual_detected:returning_for_midroll",
            )
            mixed_identity_detected_count += _count_prefix(notes, "mixed_ad_identity_detected:")
            # Each wait_for_results_retry_seconds:N.N note marks one retry cycle,
            # regardless of whether that retry succeeded or failed.
            search_cycles += _count_prefix(notes, "wait_for_results_retry_seconds:")

        # Use recorded_video_duration_seconds (actual file length after trimming)
        # rather than ad_duration_seconds (estimated from seekbar) — the latter
        # can be inflated by false detections and doesn't reflect what was saved.
        # Fallback chain: capture.recorded_video_duration_seconds →
        #   ad.recorded_video_duration_seconds → ad.ad_duration_seconds.
        # capture dict does NOT have ad_duration_seconds, so never look there.
        recorded_ad_seconds = 0.0
        for ad in watched_ads:
            if not isinstance(ad, dict):
                continue
            capture = ad.get("capture")
            dur = capture.get("recorded_video_duration_seconds") if isinstance(capture, dict) else None
            if not isinstance(dur, (int, float)) or dur <= 0:
                dur = ad.get("recorded_video_duration_seconds")
            if not isinstance(dur, (int, float)) or dur <= 0:
                dur = ad.get("ad_duration_seconds")
            if isinstance(dur, (int, float)) and dur > 0:
                recorded_ad_seconds += float(dur)
            capture = ad.get("capture")
            status = None
            if isinstance(capture, dict):
                status = capture.get("analysis_status")
                if capture.get("source_video_file"):
                    focused_video_count += 1
            if status is None:
                status = ad.get("analysis_status")
            if str(status or "").casefold() == "failed":
                analysis_failed_count += 1
            if isinstance(capture, dict):
                capture_notes = capture.get("capture_notes")
                if isinstance(capture_notes, list):
                    mixed_identity_detected_count += _count_prefix(
                        [str(note) for note in capture_notes],
                        "mixed_ad_identity_detected:",
                    )

        pure_media_seconds = round(video_watch_seconds + recorded_ad_seconds, 1)
        total_overhead_seconds = max(0, elapsed_seconds - pure_media_seconds)
        bootstrap_overhead_per_topic = (
            round(bootstrap_overhead_seconds / topics_started, 1)
            if topics_started > 0 else 0.0
        )

        return {
            "pure_media_seconds": pure_media_seconds,
            "video_watch_seconds": round(video_watch_seconds, 1),
            "recorded_ad_seconds": round(recorded_ad_seconds, 1),
            "total_overhead_seconds": round(total_overhead_seconds, 1),
            "bootstrap_overhead_seconds": round(bootstrap_overhead_seconds, 1),
            "bootstrap_overhead_per_topic": bootstrap_overhead_per_topic,
            "post_reset_overhead_seconds": round(post_reset_overhead_seconds, 1),
            "ad_flow_overhead_seconds": round(ad_flow_overhead_seconds, 1),
            "submit_search_seconds": round(submit_search_seconds, 1),
            "wait_for_results_seconds": round(wait_for_results_seconds, 1),
            "open_first_result_seconds": round(open_first_result_seconds, 1),
            "topics_started": topics_started,
            "topics_completed": topics_completed,
            "search_cycles": search_cycles,
            "topic_reset_attempts": topic_reset_attempts,
            "topic_reset_timeouts": topic_reset_timeouts,
            "wait_for_results_timeouts": wait_for_results_timeouts,
            "no_result_opened_count": no_result_opened_count,
            "open_result_no_surface_count": open_result_no_surface_count,
            "open_result_no_candidates_count": open_result_no_candidates_count,
            "watch_opened_but_rejected_count": watch_opened_but_rejected_count,
            "zero_watch_topics_count": zero_watch_topics_count,
            "midroll_duplicate_count": midroll_duplicate_count,
            "midroll_duplicate_break_count": midroll_duplicate_break_count,
            "midroll_duplicate_recording_discarded_count": (
                midroll_duplicate_recording_discarded_count
            ),
            "post_ad_residual_detected_count": post_ad_residual_detected_count,
            "focused_video_count": focused_video_count,
            "analysis_failed_count": analysis_failed_count,
            # Populated by future iterations (cluster reuse, endgame):
            "same_cluster_reuses": 0,
            "endgame_abort_new_topic": 0,
            "mixed_identity_detected_count": mixed_identity_detected_count,
        }

    @staticmethod
    def _flatten_watched_ad_summary(ad_record: dict[str, object]) -> dict[str, object]:
        flattened = dict(ad_record)
        capture = flattened.get("capture")
        if isinstance(capture, dict):
            for source_key, target_key in (
                ("video_status", "video_status"),
                ("video_file", "video_file"),
                ("source_video_file", "source_video_file"),
                ("source_recorded_video_duration_seconds", "source_recorded_video_duration_seconds"),
                ("landing_url", "landing_url"),
                ("landing_status", "landing_status"),
                ("landing_dir", "landing_dir"),
                ("analysis_status", "analysis_status"),
                ("analysis_summary", "analysis_summary"),
                ("analysis_error", "analysis_error"),
                ("analysis_error_stage", "analysis_error_stage"),
            ):
                if flattened.get(target_key) in (None, "", []):
                    value = capture.get(source_key)
                    if value not in (None, "", []):
                        flattened[target_key] = value

        analysis_summary = flattened.get("analysis_summary")
        if isinstance(analysis_summary, dict) and "analysis" not in flattened:
            flattened["analysis"] = {
                "is_relevant": analysis_summary.get("result") == "relevant",
                "result": analysis_summary.get("result"),
                "reason": analysis_summary.get("reason"),
            }
        return flattened

    def _build_session_artifact_path(self) -> Path:
        base_dir = (
            self._config.storage.base_path
            / self._config.android_app.session_artifacts_subdir
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        return base_dir / f"session_{timestamp}.json"

    @staticmethod
    def _derive_watch_seconds(samples: list[AndroidWatchSample]) -> float | None:
        if not samples:
            return None

        non_ad_offsets = [
            float(sample.offset_seconds)
            for sample in samples
            if isinstance(sample.offset_seconds, (int, float)) and not sample.ad_detected
        ]
        if len(non_ad_offsets) >= 2:
            delta = max(non_ad_offsets) - min(non_ad_offsets)
            if delta > 0:
                return delta
        elif len(non_ad_offsets) == 1:
            return max(non_ad_offsets)

        explicit_progress = [
            float(sample.progress_seconds)
            for sample in samples
            if isinstance(sample.progress_seconds, (int, float)) and not sample.ad_detected
        ]
        if len(explicit_progress) >= 2:
            delta = max(explicit_progress) - min(explicit_progress)
            if delta > 0:
                return delta
        elif len(explicit_progress) == 1:
            return None

        sample_offsets = [
            float(sample.offset_seconds)
            for sample in samples
            if isinstance(sample.offset_seconds, (int, float))
        ]
        if sample_offsets:
            if len(sample_offsets) >= 2:
                delta = max(sample_offsets) - min(sample_offsets)
                if delta > 0:
                    return delta
            return max(sample_offsets)
        return None

    @staticmethod
    def _derive_video_duration(samples: list[AndroidWatchSample]) -> float | None:
        durations = [
            float(sample.duration_seconds)
            for sample in samples
            if isinstance(sample.duration_seconds, (int, float)) and not sample.ad_detected
        ]
        if durations:
            return max(durations)
        return None

    @staticmethod
    def _derive_current_video_progress(samples: list[AndroidWatchSample]) -> float | None:
        progress_points = [
            float(sample.progress_seconds)
            for sample in samples
            if isinstance(sample.progress_seconds, (int, float)) and not sample.ad_detected
        ]
        if progress_points:
            return max(progress_points)
        return None

    @staticmethod
    def _active_topic_pool(
        *,
        topics: list[str],
        duration_minutes: int | None,
    ) -> list[str]:
        if not topics:
            return []
        if duration_minutes is None or duration_minutes <= 0:
            return topics
        distinct_cap = max(1, (duration_minutes - 2) // 5)
        distinct_cap = min(len(topics), distinct_cap)
        return topics[:distinct_cap]

    @staticmethod
    def _covered_topics(topic_results: list[AndroidSessionTopicResult]) -> set[str]:
        return {
            (item.topic or "").strip()
            for item in topic_results
            if item.watch_verified and (item.topic or "").strip()
        }

    @staticmethod
    def _session_remaining_seconds(
        *,
        started_at: float,
        duration_minutes: int | None,
    ) -> float:
        if duration_minutes is None or duration_minutes <= 0:
            return float(WATCH_LONG_FALLBACK[1])
        total = float(duration_minutes * 60)
        elapsed = max(time.monotonic() - started_at, 0.0)
        return max(total - elapsed, 0.0)

    def _next_topic_start_buffer_seconds(
        self,
        *,
        topics: list[str],
        topic_results: list[AndroidSessionTopicResult],
        topics_cycled_once: bool,
    ) -> float:
        if topics_cycled_once:
            return 60.0

        configured_buffer = float(
            self._config.android_app.session_topic_start_buffer_seconds
        )
        if not topic_results:
            return configured_buffer

        pending_uncovered = max(
            len(
                [
                    candidate
                    for candidate in topics
                    if candidate not in self._covered_topics(topic_results)
                ]
            ),
            1,
        )
        # Once the session has already made progress, keep enough time for a
        # search/open cycle without discarding the tail of short runs.
        return min(configured_buffer, 45.0 + pending_uncovered * 30.0)

    @staticmethod
    def _topic_has_meaningful_progress(
        *,
        opened_title: str | None,
        watch_verified: bool,
        watch_seconds: float | None,
        topic_watched_ads: list[dict[str, object]],
        current_watch_started_at: float | None,
    ) -> bool:
        if watch_verified or topic_watched_ads:
            return True
        if opened_title and opened_title.strip():
            return True
        if isinstance(watch_seconds, (int, float)) and watch_seconds > 0.0:
            return True
        return current_watch_started_at is not None

    @classmethod
    def _topic_surface_failed_before_watch(
        cls,
        *,
        topic_notes: list[str],
        opened_title: str | None,
        watch_verified: bool,
        watch_seconds: float | None,
        topic_watched_ads: list[dict[str, object]],
        current_watch_started_at: float | None,
    ) -> bool:
        if cls._topic_has_meaningful_progress(
            opened_title=opened_title,
            watch_verified=watch_verified,
            watch_seconds=watch_seconds,
            topic_watched_ads=topic_watched_ads,
            current_watch_started_at=current_watch_started_at,
        ):
            return False
        failure_prefixes = (
            "submit_search_timeout",
            "wait_for_results_timeout",
            "wait_for_results_failed",
            "open_first_result_timeout",
            "open_first_result_skipped:no_results_surface",
            "no_result_opened",
        )
        return any(
            note.startswith(failure_prefixes)
            for note in topic_notes
            if isinstance(note, str)
        )

    def _should_retry_topic_attempt(
        self,
        *,
        topic_started_at: float,
        session_started_at: float,
        duration_minutes: int | None,
    ) -> bool:
        topic_elapsed = max(time.monotonic() - topic_started_at, 0.0)
        session_remaining = self._session_remaining_seconds(
            started_at=session_started_at,
            duration_minutes=duration_minutes,
        )
        # A full retry costs another search/open cycle. Skip it once the current
        # topic already burned enough wall time or the session is nearly out.
        if topic_elapsed >= 75.0:
            return False
        return session_remaining >= 120.0

    def _decide_session_target_watch_seconds(
        self,
        *,
        topic: str,
        topics: list[str],
        topic_results: list[AndroidSessionTopicResult],
        watch_samples: list[AndroidWatchSample],
        started_at: float,
        duration_minutes: int | None,
    ) -> float:
        remaining_seconds = self._session_remaining_seconds(
            started_at=started_at,
            duration_minutes=duration_minutes,
        )
        pending_topics = [
            candidate
            for candidate in topics
            if candidate not in self._covered_topics(topic_results)
        ]
        video_duration = self._derive_video_duration(watch_samples)

        if video_duration and video_duration > 5:
            if random.random() < 0.18:
                target = video_duration * random.uniform(0.85, 1.0)
            elif video_duration < 180:
                target = max(video_duration * random.uniform(0.4, 0.8), WATCH_LONG_FALLBACK[0])
            elif video_duration < 600:
                target = max(video_duration * random.uniform(0.2, 0.6), WATCH_LONG_FALLBACK[0])
            else:
                target = max(video_duration * random.uniform(0.1, 0.4), WATCH_LONG_FALLBACK[0])
        else:
            target = random.uniform(*WATCH_LONG_FALLBACK)

        before_coverage = any(candidate != topic for candidate in pending_topics) or (
            topic not in self._covered_topics(topic_results)
        )
        if before_coverage:
            default_cap = random.uniform(*COVERAGE_CAP_DEFAULT)
            pending_count = max(len(pending_topics), 1)
            budget = max(remaining_seconds - pending_count * COVERAGE_SEARCH_OVERHEAD_S, 0.0)
            dynamic_cap = max(
                COVERAGE_CAP_MIN_S,
                (budget / max(pending_count, 1)) * COVERAGE_CAP_BUDGET_FRACTION,
            )
            target = min(target, min(default_cap, dynamic_cap))

        if remaining_seconds >= REALISM_MIN_WATCH_TRIGGER_REMAINING_S:
            if before_coverage:
                pending_count = max(len(pending_topics), 1)
                budget_floor = (remaining_seconds / max(pending_count + 1, 1)) * REALISM_MULTI_TOPIC_BUDGET_FRACTION
                floor = min(
                    REALISM_MIN_WATCH_S,
                    max(
                        REALISM_MULTI_TOPIC_MIN_WATCH_S,
                        min(REALISM_MULTI_TOPIC_MAX_WATCH_S, budget_floor),
                    ),
                )
            else:
                floor = REALISM_MIN_WATCH_AFTER_COVERAGE_S
            target = max(target, floor)

        if not before_coverage:
            target = min(target, random.uniform(*TOPIC_BALANCE_POST_COVERAGE_CAP_S))

        target = min(
            target,
            self._cap_short_form_target_watch_seconds(
                watch_samples=watch_samples,
                current_target=target,
            ),
        )
        target = min(target, max(remaining_seconds - 30.0, 5.0))
        return max(12.0, round(target, 1))

    def _cap_short_form_target_watch_seconds(
        self,
        *,
        watch_samples: list[AndroidWatchSample],
        current_target: float,
    ) -> float:
        video_duration = self._derive_video_duration(watch_samples)
        if video_duration is None or video_duration > 70:
            return current_target

        observed_watch_seconds = self._derive_watch_seconds(watch_samples) or 0.0
        current_progress = self._derive_current_video_progress(watch_samples)
        if current_progress is None:
            return min(current_target, max(12.0, min(video_duration + 2.0, 24.0)))

        remaining_to_end = max(0.0, video_duration - current_progress)
        extension_budget = min(max(remaining_to_end + 2.0, 0.0), 12.0)
        short_form_cap = observed_watch_seconds + extension_budget
        short_form_cap = min(short_form_cap, video_duration + 4.0, 24.0)
        return min(current_target, max(observed_watch_seconds, short_form_cap, 12.0))

    @staticmethod
    def _main_watch_chunk_timeout_seconds(watch_seconds: int) -> float:
        # Keep the outer guard slightly above watcher.watch_current()'s own timeout
        # so we do not abort a healthy watch chunk before the sampler is done.
        internal_timeout = max(float(watch_seconds) + 35.0, 45.0)
        return internal_timeout + 5.0

    def _main_watch_sample_interval_seconds(self, watch_seconds: int) -> int:
        base_interval = max(1, self._config.android_app.probe_watch_sample_interval_seconds)
        if watch_seconds >= 10 and base_interval < 2:
            return 2
        return base_interval

    async def _probe_current_ad_surface_for_missed_ad(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        watch_result: object,
        probe_seconds: int = 2,
    ) -> tuple[object, str | None, object | None]:
        try:
            probe_result = await watcher.watch_current(
                watch_seconds=probe_seconds,
                sample_interval_seconds=1,
            )
        except Exception as exc:
            return watch_result, f"missed_ad_probe_failed:{type(exc).__name__}", None

        probe_samples = list(getattr(probe_result, "samples", []) or [])
        if not any(getattr(sample, "ad_detected", False) for sample in probe_samples):
            return watch_result, None, None

        merged_samples = self._merge_watch_samples(
            list(getattr(watch_result, "samples", []) or []),
            probe_samples,
        )
        merged_result = replace(
            watch_result,
            verified=True,
            samples=merged_samples,
            ad_debug_page_source=self._preferred_ad_debug_page_source(
                probe_result,
                watch_result,
            ),
        )
        return merged_result, "missed_ad_probe:ad_detected", probe_result

    async def _continue_main_watch_if_needed(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        watch_result: object,
        target_watch_seconds: float,
    ) -> tuple[object, str | None, object | None]:
        current_watch_seconds = self._derive_watch_seconds(
            list(getattr(watch_result, "samples", []) or [])
        ) or 0.0
        remaining_seconds = max(0, int(round(target_watch_seconds - current_watch_seconds)))
        if remaining_seconds < 6:
            return watch_result, None, None

        merged_result = watch_result
        remaining = remaining_seconds
        watched_extra = 0
        last_extra_result = None
        while remaining >= 6:
            chunk_seconds = min(remaining, 12)
            chunk_timeout = self._main_watch_chunk_timeout_seconds(chunk_seconds)
            sample_interval_seconds = self._main_watch_sample_interval_seconds(chunk_seconds)
            try:
                extra_result = await asyncio.wait_for(
                    watcher.watch_current(
                        watch_seconds=chunk_seconds,
                        sample_interval_seconds=sample_interval_seconds,
                        deadline=time.monotonic() + max(1.0, chunk_timeout - 1.0),
                    ),
                    timeout=chunk_timeout,
                )
            except (asyncio.TimeoutError, AndroidUiError) as exc:
                if isinstance(exc, AndroidUiError) and "Timed out collecting watch samples" not in str(exc):
                    raise
                return merged_result, f"main_watch_timeout:{remaining_seconds}:after{watched_extra}", last_extra_result

            merged_samples = self._merge_watch_samples(
                list(getattr(merged_result, "samples", []) or []),
                list(getattr(extra_result, "samples", []) or []),
            )
            merged_result = replace(
                merged_result,
                verified=bool(
                    getattr(merged_result, "verified", False)
                    or getattr(extra_result, "verified", False)
                    or self._is_watch_verified_by_stable_dwell(merged_samples)
                ),
                samples=merged_samples,
                ad_debug_page_source=self._preferred_ad_debug_page_source(
                    extra_result,
                    merged_result,
                ),
            )
            watched_extra += chunk_seconds
            # Only check NEW samples for ad — old samples from initial watch already handled
            extra_samples = list(getattr(extra_result, "samples", []) or [])
            if any(getattr(sample, "ad_detected", False) for sample in extra_samples):
                last_extra_result = extra_result
                return merged_result, f"main_watch_ad_detected:{watched_extra}", last_extra_result
            last_extra_result = extra_result
            remaining -= chunk_seconds

        merged_result, missed_ad_note, missed_ad_probe = (
            await self._probe_current_ad_surface_for_missed_ad(
                watcher=watcher,
                watch_result=merged_result,
            )
        )
        if missed_ad_probe is not None:
            return merged_result, f"main_watch_ad_detected:{watched_extra}", missed_ad_probe
        return merged_result, f"main_watch_extended:{watched_extra}", last_extra_result

    async def _prepare_engagement_samples(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        samples: list[object],
        force_refresh: bool,
    ) -> tuple[list[object], list[str]]:
        notes: list[str] = []
        current_reason = self._engagement_gate_reason(samples)
        if not force_refresh and current_reason == "ok":
            return samples, notes

        probe_seconds = max(
            4,
            min(6, self._config.android_app.probe_post_ad_watch_seconds),
        )
        best_samples = samples
        best_reason = current_reason

        for attempt in range(1, 3):
            with contextlib.suppress(Exception):
                await watcher.dismiss_residual_ad_if_present()
            await watcher.ensure_playing()
            probe_result = await watcher.watch_current(watch_seconds=probe_seconds)
            probe_samples = list(getattr(probe_result, "samples", []) or [])
            probe_reason = self._engagement_gate_reason(probe_samples)
            notes.append(f"engagement_probe:{attempt}:{probe_reason}")
            if probe_reason == "ok":
                return probe_samples, notes
            if best_reason != "ok" and probe_reason != "no_samples":
                best_samples = probe_samples
                best_reason = probe_reason

        return best_samples, notes

    async def _reinforce_watch_verification_if_needed(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        watch_result: object,
    ) -> tuple[object, str | None]:
        samples = list(getattr(watch_result, "samples", []) or [])
        if getattr(watch_result, "verified", False):
            return watch_result, None
        if any(getattr(sample, "ad_detected", False) for sample in samples):
            return watch_result, None
        if self._engagement_gate_reason(samples) != "ok":
            return watch_result, None

        video_duration = self._derive_video_duration(samples)
        probe_seconds = 3 if video_duration is not None and video_duration <= 70 else 6
        probe_result = await watcher.watch_current(watch_seconds=probe_seconds)
        merged_result = replace(
            watch_result,
            verified=bool(
                getattr(watch_result, "verified", False)
                or getattr(probe_result, "verified", False)
            ),
            samples=self._merge_watch_samples(
                samples,
                list(getattr(probe_result, "samples", []) or []),
            ),
            ad_debug_page_source=self._preferred_ad_debug_page_source(
                probe_result,
                watch_result,
            ),
        )
        return merged_result, f"watch_verify_probe:{probe_seconds}"

    async def _prepare_for_engagement(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
    ) -> list[str]:
        notes: list[str] = []
        if await watcher.restore_primary_watch_surface():
            notes.append("pre_engagement_surface:restored")
        else:
            notes.append("pre_engagement_surface:restore_missed")
        if await watcher.ensure_playing():
            notes.append("pre_engagement_playback:resume_requested")
        else:
            notes.append("pre_engagement_playback:resume_missed")
        return notes

    def _is_short_form_watch_verified_by_dwell(
        self,
        samples: list[AndroidWatchSample],
    ) -> bool:
        video_duration = self._derive_video_duration(samples)
        if video_duration is None or video_duration > 70:
            return False
        if self._engagement_gate_reason(samples) != "ok":
            return False
        stable_samples = [
            sample
            for sample in samples
            if getattr(sample, "player_visible", False)
            and getattr(sample, "watch_panel_visible", False)
            and not getattr(sample, "ad_detected", False)
            and not bool(getattr(sample, "error_messages", []))
        ]
        if len(stable_samples) < 3:
            return False
        watched_seconds = self._derive_watch_seconds(samples) or 0.0
        minimum_verified_dwell = min(12.0, max(8.0, video_duration * 0.35))
        return watched_seconds >= minimum_verified_dwell

    def _is_watch_verified_by_stable_dwell(
        self,
        samples: list[AndroidWatchSample],
    ) -> bool:
        if self._engagement_gate_reason(samples) != "ok":
            return False
        watched_seconds = self._derive_watch_seconds(samples) or 0.0
        return watched_seconds >= 12.0

    async def _recover_unstable_watch_surface(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        watcher: AndroidYouTubeWatcher,
        topic: str,
        opened_title: str | None,
        watch_result: object,
    ) -> tuple[object, str | None, list[str]]:
        notes: list[str] = []
        samples = list(getattr(watch_result, "samples", []) or [])
        gate_reason = self._engagement_gate_reason(samples)
        if getattr(watch_result, "verified", False) or gate_reason not in {
            "no_watch_surface",
            "short_form",
        }:
            return watch_result, opened_title, notes
        if gate_reason == "short_form":
            notes.append("watch_surface_short_form_rejected")
            if await navigator.reject_reel_watch_surface(topic):
                notes.append("watch_surface_short_form_escaped")
            else:
                notes.append("watch_surface_short_form_escape_missed")
            return watch_result, opened_title, notes

        if await navigator.dismiss_system_dialogs_via_adb():
            notes.append("watch_surface_dialog_dismissed")

        current_package, _ = await navigator.current_package_activity()
        if current_package == "android":
            if await navigator.recover_from_launcher_anr():
                notes.append("watch_surface_launcher_recovered")
            current_package, _ = await navigator.current_package_activity()
        if current_package != self._config.android_app.youtube_package:
            notes.append(f"watch_surface_foreground:{current_package or 'unknown'}")
            with contextlib.suppress(Exception):
                await navigator.ensure_app_ready()
            current_package, _ = await navigator.current_package_activity()
            notes.append(
                f"watch_surface_foreground_after_reacquire:{current_package or 'unknown'}"
            )

        if await watcher.restore_primary_watch_surface():
            notes.append("watch_surface_restore:ok")
        else:
            notes.append("watch_surface_restore:missed")

        if await watcher.ensure_playing():
            notes.append("watch_surface_resume:requested")
        else:
            notes.append("watch_surface_resume:missed")

        delayed_opened_title = await navigator.await_current_watch_title(
            topic,
            timeout_seconds=5.0,
            deadline=time.monotonic() + 5.0,
        )
        resolved_title = opened_title
        if delayed_opened_title:
            if not resolved_title:
                resolved_title = delayed_opened_title
                notes.append("opened_title_recovered:watch_surface")
            elif (
                self._opened_title_is_provisional(resolved_title, topic)
                and not self._opened_title_is_provisional(delayed_opened_title, topic)
            ):
                resolved_title = delayed_opened_title
                notes.append("opened_title_resolved:watch_surface")

        probe_seconds = max(
            6,
            min(10, self._config.android_app.probe_post_ad_watch_seconds),
        )
        probe_result = await watcher.watch_current(watch_seconds=probe_seconds)
        probe_samples = list(getattr(probe_result, "samples", []) or [])
        notes.append(f"watch_surface_probe:{self._engagement_gate_reason(probe_samples)}")
        merged_result = replace(
            watch_result,
            verified=bool(
                getattr(watch_result, "verified", False)
                or getattr(probe_result, "verified", False)
            ),
            samples=self._merge_watch_samples(samples, probe_samples),
            ad_debug_page_source=self._preferred_ad_debug_page_source(
                probe_result,
                watch_result,
            ),
        )
        return merged_result, resolved_title, notes

    async def _stabilize_playback_after_engagement(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
    ) -> list[str]:
        notes: list[str] = []
        if await watcher.restore_primary_watch_surface():
            notes.append("post_engagement_surface:restored")
        else:
            notes.append("post_engagement_surface:restore_missed")
        with contextlib.suppress(Exception):
            await watcher.dismiss_residual_ad_if_present()
        if await watcher.ensure_playing():
            notes.append("post_engagement_playback:resume_requested")
            return notes

        notes.append("post_engagement_playback:retry")
        if await watcher.restore_primary_watch_surface():
            notes.append("post_engagement_surface:restored_retry")
        else:
            notes.append("post_engagement_surface:restore_retry_missed")
        if await watcher.ensure_playing():
            notes.append("post_engagement_playback:resume_requested_retry")
        else:
            notes.append("post_engagement_playback:still_stalled")
            notes.append("post_engagement_handoff:next_topic")
        return notes

    async def _run_organic_watch_loop(
        self,
        *,
        watcher: AndroidYouTubeWatcher,
        navigator: AndroidYouTubeNavigator,
        driver: object,
        adb_serial: str | None,
        deadline: float,
        watched_ads: list[dict[str, object]],
        ad_analysis: AndroidAdAnalysisCoordinator,
        landing_scraper: AndroidLandingPageScraper,
    ) -> None:
        """Fill leftover session time by watching recommended videos from the current watch surface."""
        organic_iteration = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining < 90.0:
                break
            organic_iteration += 1
            print(
                f"[android-session] organic_watch:iteration={organic_iteration} remaining_seconds={int(remaining)}",
                flush=True,
            )
            # Open a recommended video from the current watch panel (no search query)
            try:
                opened_title = await asyncio.wait_for(
                    navigator.open_first_result(query=None, deadline=time.monotonic() + 30.0),
                    timeout=35.0,
                )
            except Exception as exc:
                print(f"[android-session] organic_watch:open_failed error={exc}", flush=True)
                break
            if not opened_title:
                print("[android-session] organic_watch:no_result_opened", flush=True)
                break
            print(f"[android-session] organic_watch:opened title={opened_title}", flush=True)
            # Watch the opened video for remaining time (capped at 90s per video)
            watch_seconds = max(20, min(90, int(deadline - time.monotonic()) - 10))
            if watch_seconds < 20:
                break
            recorder = None
            recording_handle = None
            recorded_video_path = None
            recorded_video_duration_seconds = None
            try:
                if self._config.android_app.probe_screenrecord_enabled:
                    recorder = AndroidScreenRecorder(
                        adb_serial=adb_serial,
                        artifacts_dir=(
                            self._config.storage.base_path
                            / self._config.android_app.probe_screenrecord_artifacts_subdir
                        ),
                        bitrate=self._config.android_app.probe_screenrecord_bitrate,
                    )
                    recording_handle = await recorder.start(
                        artifact_prefix=self._build_safe_artifact_prefix(
                            f"organic_{organic_iteration}"
                        ),
                    )
                watch_result = await watcher.watch_current(
                    watch_seconds=watch_seconds,
                    timeout_grace_seconds=12.0,
                    timeout_floor_seconds=18.0,
                )
            except Exception as exc:
                print(f"[android-session] organic_watch:watch_failed error={exc}", flush=True)
                break
            finally:
                if recorder is not None and recording_handle is not None:
                    recorded_video_path = await self._stop_recording_handle(
                        recorder=recorder,
                        recording_handle=recording_handle,
                    )
                    recorded_video_duration_seconds = self._probe_recorded_video_duration(
                        recorded_video_path
                    )

            # Capture any ad seen during organic watch
            ad_cta_result = None
            if any(getattr(s, "ad_detected", False) for s in watch_result.samples):
                try:
                    ad_interactor = AndroidYouTubeAdInteractor(
                        driver,
                        self._config.android_app,
                        adb_serial=adb_serial,
                    )
                    ad_cta_result = await ad_interactor.probe_cta(
                        artifact_dir=(
                            self._config.storage.base_path
                            / self._config.android_app.artifacts_subdir
                        ),
                        artifact_prefix=self._build_safe_artifact_prefix(
                            f"organic_{organic_iteration}"
                        ),
                    )
                except Exception:
                    pass

            built_ad = build_watched_ad_record(
                watch_samples=watch_result.samples,
                watch_debug_screen_path=None,
                watch_debug_page_source_path=None,
                ad_cta_result=ad_cta_result,
                recorded_video_path=recorded_video_path,
                recorded_video_duration_seconds=recorded_video_duration_seconds,
            )
            if built_ad is not None:
                built_ad = self._with_watched_ad_position(
                    built_ad,
                    len(watched_ads) + 1,
                )
                await self._focus_captured_ad_video_if_needed(built_ad)
                ad_analysis.submit(built_ad)
                landing_scraper.submit(built_ad)
                watched_ads.append(built_ad)
                await _notify_ad_captured()
                print(
                    f"[android-session] organic_watch:ad_captured total_ads={len(watched_ads)}",
                    flush=True,
                )

    @staticmethod
    def _has_non_ad_progress(samples: list[object]) -> bool:
        progress_points = [
            getattr(sample, "progress_seconds", None)
            for sample in samples
            if isinstance(getattr(sample, "progress_seconds", None), (int, float))
            and not getattr(sample, "ad_detected", False)
            and not getattr(sample, "is_reel_surface", False)
        ]
        if len(progress_points) >= 2:
            return float(progress_points[-1]) > float(progress_points[0])
        stable_points = [
            sample
            for sample in samples
            if getattr(sample, "player_visible", False)
            and getattr(sample, "watch_panel_visible", False)
            and not getattr(sample, "ad_detected", False)
            and not getattr(sample, "is_reel_surface", False)
            and not bool(getattr(sample, "error_messages", []))
        ]
        return len(stable_points) >= 2

    @staticmethod
    def _sanitize_topic_note_value(value: str | None, *, limit: int = 120) -> str | None:
        cleaned = " ".join((value or "").replace(":", " ").split())
        if not cleaned:
            return None
        return cleaned[:limit]

    @classmethod
    def _append_open_result_diagnostics_notes(
        cls,
        *,
        topic_notes: list[str],
        attempt_label: str,
        diagnostics: dict[str, object] | None,
    ) -> None:
        if not diagnostics:
            return
        reason = str(diagnostics.get("reason") or "").strip()
        if not reason or reason == "not_started":
            return
        topic_notes.append(f"open_result_diag_reason:{attempt_label}:{reason}")
        reason_tags = {
            "candidates_empty": "no_candidates",
            "candidates_filtered_out": "no_candidates",
            "no_results_surface": "no_surface",
            "no_results_surface_after_tap": "no_surface",
            "tap_no_open": "tap_no_open",
            "watch_opened_rejected": "opened_then_rejected",
        }
        normalized_reason = reason_tags.get(reason)
        if normalized_reason is not None:
            topic_notes.append(f"open_result_failure:{normalized_reason}:{attempt_label}")
        candidate_title = cls._sanitize_topic_note_value(
            diagnostics.get("candidate_title") if isinstance(diagnostics.get("candidate_title"), str) else None
        )
        resolved_title = cls._sanitize_topic_note_value(
            diagnostics.get("resolved_title") if isinstance(diagnostics.get("resolved_title"), str) else None
        )
        if candidate_title and reason in {"watch_opened_rejected", "tap_no_open"}:
            topic_notes.append(f"open_result_candidate:{attempt_label}:{candidate_title}")
        if resolved_title and reason == "watch_opened_rejected":
            topic_notes.append(f"open_result_resolved:{attempt_label}:{resolved_title}")

    async def _reconcile_opened_title_with_topic(
        self,
        *,
        navigator: AndroidYouTubeNavigator,
        topic: str,
        opened_title: str,
        topic_notes: list[str],
        attempt_label: str,
    ) -> str | None:
        delayed_opened_title = await self._await_current_watch_title_with_hard_timeout(
            navigator=navigator,
            topic=topic,
            topic_notes=topic_notes,
            stage_label=f"await_current_watch_title_{attempt_label}_mismatch",
            timeout_seconds=3.5,
        )
        if delayed_opened_title:
            if (
                self._opened_title_is_provisional(opened_title, topic)
                and not self._opened_title_is_provisional(delayed_opened_title, topic)
            ):
                topic_notes.append(f"opened_title_recovered_after_mismatch:{attempt_label}")
            elif delayed_opened_title != opened_title:
                topic_notes.append(f"opened_title_refreshed_after_open:{attempt_label}")
            return delayed_opened_title

        diagnostics = await navigator.last_open_result_diagnostics()
        if (
            diagnostics.get("reason") == "post_tap_non_results_accepted_candidate"
            and diagnostics.get("candidate_title") == opened_title
            and self._opened_title_matches_topic(opened_title, topic)
        ):
            topic_notes.append(f"opened_title_kept:post_tap_non_results:{attempt_label}")
            print(
                f"[android-session] topic:opened_title_kept_post_tap attempt={attempt_label} title={opened_title}",
                flush=True,
            )
            return opened_title

        watch_surface = await navigator.has_watch_surface_for_query()
        if watch_surface:
            topic_notes.append(f"opened_title_kept:watch_surface:{attempt_label}")
            print(
                f"[android-session] topic:opened_title_kept attempt={attempt_label} title={opened_title}",
                flush=True,
            )
            return opened_title

        fallback_title = await navigator.await_current_watch_title(
            timeout_seconds=3.0,
            deadline=time.monotonic() + 3.0,
        )
        fallback_watch_surface = await navigator.has_watch_surface_for_query()
        if fallback_title and fallback_watch_surface:
            topic_notes.append(f"opened_title_fallback:{fallback_title}")
            print(
                f"[android-session] topic:opened_title_fallback title={fallback_title}",
                flush=True,
            )
            topic_notes.append(f"opened_title_fallback_kept:{attempt_label}")
            return fallback_title
        return None

    @staticmethod
    def _opened_title_matches_topic(title: str, topic: str) -> bool:
        title_tokens = AndroidYouTubeSessionRunner._normalized_match_tokens(title)
        topic_tokens = AndroidYouTubeSessionRunner._normalized_match_tokens(topic)
        if not title_tokens or not topic_tokens:
            return False
        matched_topic_tokens = {
            topic_token
            for topic_token in topic_tokens
            if any(
                AndroidYouTubeSessionRunner._tokens_related(topic_token, title_token)
                for title_token in title_tokens
            )
        }
        anchor_tokens = AndroidYouTubeSessionRunner._topic_anchor_tokens(topic)
        if anchor_tokens and not any(
            any(
                AndroidYouTubeSessionRunner._tokens_related(anchor_token, title_token)
                for title_token in title_tokens
            )
            for anchor_token in anchor_tokens
        ):
            if anchor_tokens == {"forex"}:
                return AndroidYouTubeSessionRunner._is_reasonable_topic_video_title(
                    title_tokens=title_tokens,
                    topic_tokens=topic_tokens,
                )
            return False
        if len(matched_topic_tokens) >= 2:
            return True
        if bool(matched_topic_tokens) and len(topic_tokens) == 1:
            return True
        return AndroidYouTubeSessionRunner._is_reasonable_topic_video_title(
            title_tokens=title_tokens,
            topic_tokens=topic_tokens,
        )

    @staticmethod
    def _is_reasonable_topic_video_title(
        *,
        title_tokens: set[str],
        topic_tokens: set[str],
    ) -> bool:
        if "forex" in topic_tokens and "trad" in title_tokens and any(
            token in title_tokens for token in {"analysis", "beginner", "guide", "market", "setup", "strategy"}
        ):
            return True
        finance_tokens = {
            "automat",
            "bitcoin",
            "bot",
            "crypto",
            "cryptocurrency",
            "earn",
            "edge",
            "income",
            "immediate",
            "invest",
            "market",
            "money",
            "path",
            "platform",
            "profit",
            "quantum",
            "review",
            "software",
            "stock",
            "trad",
            "traderai",
        }
        if not any(token in topic_tokens for token in finance_tokens):
            return False
        return any(token in title_tokens for token in finance_tokens)

    @staticmethod
    def _opened_title_is_provisional(title: str, topic: str) -> bool:
        normalized_title = AndroidYouTubeSessionRunner._normalize_phrase(title)
        normalized_topic = AndroidYouTubeSessionRunner._normalize_phrase(topic)
        if not normalized_title or not normalized_topic:
            return False
        return normalized_title == normalized_topic

    @staticmethod
    def _normalize_phrase(value: str) -> str:
        return " ".join(re.findall(r"[A-Za-zА-Яа-я0-9]+", value.casefold()))

    @staticmethod
    def _normalized_match_tokens(value: str) -> set[str]:
        tokens: set[str] = set()
        for raw in re.findall(r"[A-Za-zА-Яа-я0-9]+", value.casefold()):
            token = raw.strip()
            if len(token) < 4:
                continue
            for suffix in ("ments", "ment", "ings", "ing", "ers", "er", "ies", "es", "s"):
                if len(token) > len(suffix) + 2 and token.endswith(suffix):
                    token = token[: -len(suffix)]
                    break
            if len(token) >= 4:
                tokens.add(token)
        return tokens

    @classmethod
    def _topic_anchor_tokens(cls, topic: str) -> set[str]:
        topic_tokens = cls._normalized_match_tokens(topic)
        anchor_tokens: set[str] = set()
        for token in topic_tokens:
            if token in cls._BROAD_QUERY_TOKENS:
                continue
            if any(token in group for group in cls._SEMANTIC_TOKEN_GROUPS):
                continue
            anchor_tokens.add(token)
        return anchor_tokens

    @staticmethod
    def _tokens_related(left: str, right: str) -> bool:
        if left == right:
            return True
        for group in AndroidYouTubeSessionRunner._SEMANTIC_TOKEN_GROUPS:
            if left in group and right in group:
                return True
        shorter, longer = sorted((left, right), key=len)
        return len(shorter) >= 5 and longer.startswith(shorter)

    @staticmethod
    def _is_infrastructure_failure(message: str) -> bool:
        lowered = message.casefold()
        return any(
            token in lowered
            for token in (
                "socket hang up",
                "could not proxy command to the remote server",
                "device offline",
                "device not found",
                "adb: device",
                "timed out waiting for emulator device to appear in adb",
                "adb monkey launch failed",
                "instrumentation process is not running",
                "uiautomator2 server",
                "instrumentation process cannot be initialized",
                "cannot be proxied to uiautomator2",
                "connection refused",
                "max retries exceeded with url",
                "failed to establish a new connection",
                "android services did not become ready for appium session",
                "failed to launch youtube app",
                "appium server is not reachable",
                "appium session health check failed",
                "invalidsessionidexception",
                "the session identified by",
                "read timed out",
                "readtimeouterror",
                "httpconnectionpool(host='127.0.0.1', port=4723)",
                "launcher anr burst detected",
                "lost youtube foreground",
                "ensure_app_ready timed out",
                "cleanup timed out",
                "topic_post_reset timed out",
            )
        )

    @staticmethod
    def _is_device_gone_failure(message: str) -> bool:
        """True when the error definitively means the emulator process is gone."""
        lowered = message.casefold()
        return any(
            token in lowered
            for token in (
                "device not found",
                "adb: device",
                "device offline",
                "timed out waiting for emulator device to appear in adb",
            )
        )

    @staticmethod
    def _is_session_only_infrastructure_failure(message: str) -> bool:
        lowered = message.casefold()
        return any(
            token in lowered
            for token in (
                "instrumentation process is not running",
                "uiautomator2 server",
                "instrumentation process cannot be initialized",
                "cannot be proxied to uiautomator2",
                "invalidsessionidexception",
                "invalid session id",
                "the session identified by",
                "could not proxy command to the remote server",
                "socket hang up",
                "read timed out",
                "readtimeouterror",
                "new command timeout",
                "topic_post_reset timed out",
            )
        )
