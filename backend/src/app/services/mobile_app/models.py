from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AndroidProbeSurfaceSnapshot:
    package: str | None = None
    activity: str | None = None
    page_source_length: int = 0


@dataclass(frozen=True)
class AndroidWatchSample:
    offset_seconds: int
    package: str | None = None
    activity: str | None = None
    page_source_length: int = 0
    is_reel_surface: bool = False
    player_visible: bool = False
    watch_panel_visible: bool = False
    results_visible: bool = False
    seekbar_description: str | None = None
    progress_seconds: int | None = None
    duration_seconds: int | None = None
    ad_seekbar_description: str | None = None
    ad_progress_seconds: int | None = None
    ad_duration_seconds: int | None = None
    ad_detected: bool = False
    skip_available: bool = False
    skip_clicked: bool = False
    ad_sponsor_label: str | None = None
    ad_headline_text: str | None = None
    ad_display_url: str | None = None
    ad_cta_text: str | None = None
    ad_visible_lines: list[str] = field(default_factory=list)
    ad_signal_labels: list[str] = field(default_factory=list)
    ad_cta_labels: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AndroidProbeResult:
    avd_name: str
    adb_serial: str
    topic: str
    opened_title: str | None
    home: AndroidProbeSurfaceSnapshot
    results: AndroidProbeSurfaceSnapshot
    watch: AndroidProbeSurfaceSnapshot | None
    artifact_path: Path
    appium_server_url: str
    reused_running_device: bool = False
    started_local_appium: bool = False
    notes: list[str] = field(default_factory=list)
    watch_verified: bool = False
    watch_ad_detected: bool = False
    watch_samples: list[AndroidWatchSample] = field(default_factory=list)
    watch_debug_screen_path: Path | None = None
    watch_debug_page_source_path: Path | None = None
    ad_cta_clicked: bool = False
    ad_cta_label: str | None = None
    ad_cta_destination_package: str | None = None
    ad_cta_destination_activity: str | None = None
    ad_cta_returned_to_youtube: bool = False
    ad_cta_debug_screen_path: Path | None = None
    ad_cta_debug_page_source_path: Path | None = None
    watched_ads: list[dict[str, Any]] = field(default_factory=list)
    ad_analysis_done: int = 0
    ad_analysis_terminal: int = 0
    liked: bool = False
    already_liked: bool = False
    subscribed: bool = False
    already_subscribed: bool = False
    comments_glanced: bool = False


@dataclass(frozen=True)
class AndroidSessionTopicResult:
    topic: str
    opened_title: str | None = None
    notes: list[str] = field(default_factory=list)
    watch_verified: bool = False
    watch_seconds: float | None = None
    target_watch_seconds: float | None = None
    watch_ad_detected: bool = False
    watched_ads: list[dict[str, Any]] = field(default_factory=list)
    liked: bool = False
    already_liked: bool = False
    subscribed: bool = False
    already_subscribed: bool = False
    comments_glanced: bool = False


@dataclass(frozen=True)
class AndroidSessionRunResult:
    avd_name: str
    adb_serial: str
    topics: list[str]
    artifact_path: Path
    appium_server_url: str
    duration_minutes_target: int | None = None
    elapsed_seconds: int = 0
    reused_running_device: bool = False
    started_local_appium: bool = False
    topic_results: list[AndroidSessionTopicResult] = field(default_factory=list)
    watched_ads: list[dict[str, Any]] = field(default_factory=list)
    bytes_downloaded: int = 0


@dataclass(frozen=True)
class AndroidWarmSnapshotBootstrapResult:
    avd_name: str
    adb_serial: str
    snapshot_name: str
    created_avd: bool
    play_store_available: bool
    opened_play_store: bool
    artifact_path: Path
    notes: list[str] = field(default_factory=list)
