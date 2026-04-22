from __future__ import annotations

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


def test_dismiss_system_dialog_skips_adb_probe_when_page_has_no_dialog_hints() -> None:
    watcher = _WatcherWithCounters(_FakeDriver(page_source=_clean_watch_page_source()), AndroidAppConfig())

    assert watcher._dismiss_system_dialog_sync() is False
    assert watcher.dialog_adb_calls == 0


def test_collect_sample_sync_skips_adb_fallback_for_clean_watch_surface() -> None:
    watcher = _WatcherWithoutDialogProbe(
        _FakeDriver(page_source=_clean_watch_page_source()),
        AndroidAppConfig(),
    )

    sample, page_source = watcher._collect_sample_sync(0)

    assert watcher.adb_dump_calls == 0
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
