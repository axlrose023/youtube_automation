from __future__ import annotations

import subprocess

import app.services.mobile_app.android.youtube.watcher as watcher_module
from app.services.mobile_app.android.youtube.watcher import AndroidYouTubeWatcher
from app.settings import AndroidAppConfig


class _FakeDriver:
    def __init__(
        self,
        *,
        page_source: str,
        current_package: str = "com.google.android.youtube",
        current_activity: str = "com.google.android.apps.youtube.app.watchwhile.InternalMainActivity",
    ) -> None:
        self.page_source = page_source
        self.current_package = current_package
        self.current_activity = current_activity

    def find_elements(self, *_args, **_kwargs) -> list[object]:
        return []


class _WatcherWithCounters(AndroidYouTubeWatcher):
    def __init__(self, driver: object, config: AndroidAppConfig) -> None:
        super().__init__(driver, config, adb_serial="emulator-5554")
        self.adb_dump_calls = 0
        self.dialog_adb_calls = 0
        self.adb_hierarchy = ""

    def _dump_ui_hierarchy_via_adb_sync(self, *, timeout_seconds: float = 6.0) -> str | None:
        self.adb_dump_calls += 1
        return self.adb_hierarchy or None

    def _dismiss_system_dialog_via_adb_sync(self) -> bool:
        self.dialog_adb_calls += 1
        return False


class _WatcherWithoutDialogProbe(_WatcherWithCounters):
    def _dismiss_system_dialog_sync(self) -> bool:
        return False


def _clean_watch_page_source() -> str:
    return """
    <hierarchy>
      <android.widget.FrameLayout resource-id="com.google.android.youtube:id/watch_player" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_panel" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_while_time_bar_view">
        <android.widget.SeekBar
          class="android.widget.SeekBar"
          content-desc="0 minutes 4 seconds of 8 minutes 55 seconds" />
      </android.view.ViewGroup>
    </hierarchy>
    """


def _adb_watch_page_source() -> str:
    return """
    <hierarchy>
      <android.widget.FrameLayout resource-id="com.google.android.youtube:id/watch_player" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_panel" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_while_time_bar_view">
        <android.widget.SeekBar
          class="android.widget.SeekBar"
          content-desc="0 minutes 9 seconds of 3 minutes 42 seconds" />
      </android.view.ViewGroup>
    </hierarchy>
    """


def _lead_form_ad_page_source() -> str:
    return """
    <hierarchy>
      <android.widget.FrameLayout resource-id="com.google.android.youtube:id/watch_player" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_panel" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_while_time_bar_view">
        <android.widget.SeekBar
          class="android.widget.SeekBar"
          content-desc="0 minutes 18 seconds of 8 minutes 42 seconds" />
      </android.view.ViewGroup>
      <android.widget.FrameLayout resource-id="com.google.android.youtube:id/skip_ad_button" />
      <android.widget.LinearLayout
        resource-id="com.google.android.youtube:id/skip_ad_button_container"
        content-desc="Skip ad" />
      <android.widget.TextView
        resource-id="com.google.android.youtube:id/title"
        text="Moodle als leeromgeving" />
    </hierarchy>
    """


def _sponsored_bottom_sheet_page_source() -> str:
    return """
    <hierarchy>
      <android.widget.FrameLayout resource-id="com.google.android.youtube:id/watch_player" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_panel" />
      <android.widget.TextView text="Sponsored" />
      <android.widget.TextView text="BeCyprus" />
      <android.widget.TextView text="becyprus.com" />
      <android.widget.TextView text="Luxury &amp; Modern Apartments for Rent" />
      <android.widget.Button text="Book now" />
    </hierarchy>
    """


def test_dismiss_system_dialog_skips_adb_probe_when_page_has_no_dialog_hints() -> None:
    watcher = _WatcherWithCounters(_FakeDriver(page_source=_clean_watch_page_source()), AndroidAppConfig())

    assert watcher._dismiss_system_dialog_sync() is False
    assert watcher.dialog_adb_calls == 0


def test_collect_sample_sync_skips_adb_fallback_for_clean_watch_surface() -> None:
    watcher = _WatcherWithoutDialogProbe(
        _FakeDriver(page_source=_clean_watch_page_source()),
        AndroidAppConfig(),
    )
    watcher.adb_hierarchy = _clean_watch_page_source()

    sample, page_source = watcher._collect_sample_sync(0)

    assert watcher.adb_dump_calls == 1
    assert page_source is not None
    assert sample.player_visible is True
    assert sample.watch_panel_visible is True
    assert sample.progress_seconds == 4
    assert sample.duration_seconds == 535
    assert sample.ad_detected is False


def test_collect_sample_sync_uses_adb_fallback_when_page_source_missing() -> None:
    watcher = _WatcherWithoutDialogProbe(
        _FakeDriver(page_source=""),
        AndroidAppConfig(),
    )
    watcher.adb_hierarchy = _adb_watch_page_source()

    sample, page_source = watcher._collect_sample_sync(0)

    assert watcher.adb_dump_calls == 1
    assert page_source == watcher.adb_hierarchy
    assert sample.player_visible is True
    assert sample.watch_panel_visible is True
    assert sample.progress_seconds == 9
    assert sample.duration_seconds == 222
    assert sample.ad_detected is False


def test_collect_sample_sync_discards_long_main_seekbar_fallback_for_ad_timing() -> None:
    watcher = _WatcherWithoutDialogProbe(
        _FakeDriver(page_source=_lead_form_ad_page_source()),
        AndroidAppConfig(),
    )
    watcher.adb_hierarchy = _lead_form_ad_page_source()

    sample, _page_source = watcher._collect_sample_sync(0)

    assert sample.player_visible is True
    assert sample.watch_panel_visible is True
    assert sample.progress_seconds == 18
    assert sample.duration_seconds == 522
    assert sample.skip_available is True
    assert sample.ad_detected is True
    assert sample.ad_timing_from_main_seekbar is True
    assert sample.ad_progress_seconds is None
    assert sample.ad_duration_seconds is None


def test_collect_sample_sync_detects_sponsored_bottom_sheet_ad() -> None:
    watcher = _WatcherWithoutDialogProbe(
        _FakeDriver(page_source=_sponsored_bottom_sheet_page_source()),
        AndroidAppConfig(),
    )

    sample, _page_source = watcher._collect_sample_sync(0)

    assert sample.player_visible is True
    assert sample.watch_panel_visible is True
    assert sample.ad_detected is True
    assert sample.ad_sponsor_label == "Sponsored"
    assert sample.ad_headline_text == "BeCyprus"
    assert sample.ad_display_url == "becyprus.com"
    assert sample.ad_cta_text == "Book now"


def test_dump_ui_hierarchy_via_adb_returns_none_on_timeout(monkeypatch) -> None:
    watcher = AndroidYouTubeWatcher(
        _FakeDriver(page_source=""),
        AndroidAppConfig(),
        adb_serial="emulator-5554",
    )

    monkeypatch.setattr(
        watcher_module,
        "require_tool_path",
        lambda name: "adb",
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(watcher_module.subprocess, "run", fake_run)

    assert watcher._dump_ui_hierarchy_via_adb_sync(timeout_seconds=2.0) is None


def test_collect_sample_sync_uses_media_session_when_adb_dump_has_no_surface(
    monkeypatch,
) -> None:
    watcher = _WatcherWithoutDialogProbe(
        _FakeDriver(page_source=""),
        AndroidAppConfig(),
    )
    watcher.adb_hierarchy = ""

    monkeypatch.setattr(
        watcher,
        "_read_youtube_media_session_via_adb_sync",
        lambda: watcher_module._YouTubeMediaSessionSnapshot(
            state="PLAYING",
            playing=True,
            position_seconds=2,
            title="This Simple Trading Strategy got me 80% Win Rate",
        ),
    )

    sample, page_source = watcher._collect_sample_sync(9)

    assert page_source == ""
    assert sample.player_visible is True
    assert sample.watch_panel_visible is True
    assert sample.results_visible is False
    assert sample.progress_seconds == 9
    assert sample.seekbar_description == (
        "media_session:PLAYING:This Simple Trading Strategy got me 80% Win Rate"
    )


def test_read_youtube_media_session_via_adb_parses_playing_state(monkeypatch) -> None:
    watcher = AndroidYouTubeWatcher(
        _FakeDriver(page_source=""),
        AndroidAppConfig(),
        adb_serial="emulator-5554",
    )
    media_session_output = """
    YouTube playerlib com.google.android.youtube/YouTube playerlib/7 (userId=0)
      package=com.google.android.youtube
      active=true
      state=PlaybackState {state=PLAYING(3), position=2142, buffered position=0, speed=1.0, updated=324337, actions=8615, custom actions=[], active item id=-1, error=null}
      metadata: size=6, description=This Simple Trading Strategy got me 80% Win Rate, Kimmel Trading
    """

    monkeypatch.setattr(
        watcher_module,
        "require_tool_path",
        lambda name: "adb",
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=media_session_output,
            stderr="",
        )

    monkeypatch.setattr(watcher_module.subprocess, "run", fake_run)

    snapshot = watcher._read_youtube_media_session_via_adb_sync()

    assert snapshot == watcher_module._YouTubeMediaSessionSnapshot(
        state="PLAYING",
        playing=True,
        position_seconds=2,
        title="This Simple Trading Strategy got me 80% Win Rate, Kimmel Trading",
    )
