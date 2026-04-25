import subprocess
from types import SimpleNamespace

import pytest

import app.services.mobile_app.android.youtube.navigator as navigator_module
from app.services.mobile_app.android.errors import AndroidUiError
from app.services.mobile_app.android.youtube.navigator import (
    AndroidYouTubeNavigator,
    NativeResultCandidate,
)


def test_extract_result_candidates_marks_cta_backed_ad_card_as_sponsored(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Visit site"
        bounds="[551,1248][1048,1343]"
      />
      <android.view.ViewGroup
        content-desc="Learn more"
        bounds="[32,1248][530,1343]"
      />
      <android.view.ViewGroup
        content-desc="Quantum AI Trading Review - play video"
        bounds="[40,1080][1040,1460]"
      />
      <android.view.ViewGroup
        content-desc="Quantum AI Trading Full Guide - play video"
        bounds="[40,1640][1040,1880]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = None
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 902, 1080, 2274),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_short_result_bounds_sync",
        lambda: [],
        raising=False,
    )

    candidates = navigator._extract_result_candidates_from_page_source_sync()

    assert [candidate.title for candidate in candidates] == [
        "Quantum AI Trading Review",
        "Quantum AI Trading Full Guide",
    ]
    assert [candidate.is_sponsored for candidate in candidates] == [True, False]


def test_tap_first_playable_candidate_below_sponsor_uses_desc_hotspot(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Visit site"
        bounds="[551,1248][1048,1343]"
      />
      <android.view.ViewGroup
        content-desc="Learn more"
        bounds="[32,1248][530,1343]"
      />
      <android.view.ViewGroup
        content-desc="Quantum AI Trading Review - play video"
        bounds="[40,1080][1040,1460]"
      />
      <android.view.ViewGroup
        content-desc="Quantum AI Trading Full Guide - play video"
        bounds="[40,1640][1040,1880]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = None
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0
    navigator._rejected_result_titles = set()
    taps: list[tuple[int, int]] = []

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 902, 1080, 2274),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_short_result_bounds_sync",
        lambda: [],
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_via_adb_sync",
        lambda x, y: taps.append((x, y)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_watch_open_after_tap_sync",
        lambda query, timeout_seconds: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_reject_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_for_query_sync",
        lambda query: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_sync",
        lambda: None,
        raising=False,
    )

    title = navigator._tap_first_playable_candidate_below_sponsor_sync("quantum ai trading")

    assert title == "Quantum AI Trading Full Guide"
    assert taps == [(540, 1731)]


def test_extract_result_candidates_expands_thin_playable_bounds(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Best Top Down Analysis Strategy for 2026 | Forex Trading Guide - play video"
        bounds="[0,2267][1080,2274]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = None
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 902, 1080, 2274),
        raising=False,
    )

    candidates = navigator._extract_result_candidates_from_page_source_sync()

    assert [candidate.title for candidate in candidates] == [
        "Best Top Down Analysis Strategy for 2026 | Forex Trading Guide"
    ]
    assert candidates[0].bounds == (0, 2087, 1080, 2274)


def test_extract_result_candidates_does_not_mark_result_below_search_ad_cta_as_sponsored(
    monkeypatch,
) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,262][1080,2148]" />
      <android.widget.TextView text="Sponsored" bounds="[160,1000][320,1040]" />
      <android.view.ViewGroup content-desc="Visit site" bounds="[32,1067][1048,1162]" />
      <android.view.ViewGroup
        content-desc="Unlocking AI Trading Secrets: A Step by Step Guide to Dominate Markets with Quantum AI - play video"
        bounds="[0,1247][1080,2044]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = None
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 262, 1080, 2148),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_short_result_bounds_sync",
        lambda: [],
        raising=False,
    )

    candidates = navigator._extract_result_candidates_from_page_source_sync()

    assert [candidate.title for candidate in candidates] == [
        "Unlocking AI Trading Secrets: A Step by Step Guide to Dominate Markets with Quantum AI"
    ]
    assert candidates[0].is_sponsored is False


def test_tap_first_playable_candidate_prefers_non_sponsored_finance_match(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Visit site"
        bounds="[551,1248][1048,1343]"
      />
      <android.view.ViewGroup
        content-desc="Learn more"
        bounds="[32,1248][530,1343]"
      />
      <android.view.ViewGroup
        content-desc="This trading strategy is boring, but it makes me $150,000/week - play video"
        bounds="[0,1407][1080,2204]"
      />
      <android.view.ViewGroup
        content-desc="This Simple Trading Strategy got me 80% Win Rate - play video"
        bounds="[0,2267][1080,2274]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = None
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0
    navigator._rejected_result_titles = set()
    taps: list[tuple[int, int]] = []

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 902, 1080, 2274),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_short_result_bounds_sync",
        lambda: [],
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_via_adb_sync",
        lambda x, y: taps.append((x, y)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_watch_open_after_tap_sync",
        lambda query, timeout_seconds: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_reject_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_for_query_sync",
        lambda query: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_sync",
        lambda: None,
        raising=False,
    )

    title = navigator._tap_first_playable_candidate_below_sponsor_sync("forex trading strategy")

    assert title == "This Simple Trading Strategy got me 80% Win Rate"
    assert taps == [(540, 2158)]


def test_tap_first_playable_candidate_uses_high_score_sponsored_candidate_when_alone(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Visit site"
        bounds="[551,1248][1048,1343]"
      />
      <android.view.ViewGroup
        content-desc="Learn more"
        bounds="[32,1248][530,1343]"
      />
      <android.view.ViewGroup
        content-desc="The Only Trading Strategy You'll Ever Need - play video"
        bounds="[0,1407][1080,2148]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = "emulator-5554"
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0
    navigator._rejected_result_titles = set()
    taps: list[tuple[int, int]] = []

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 902, 1080, 2274),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_short_result_bounds_sync",
        lambda: [],
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_via_adb_sync",
        lambda x, y: taps.append((x, y)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_watch_open_after_tap_sync",
        lambda query, timeout_seconds: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_reject_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_for_query_sync",
        lambda query: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_sync",
        lambda: None,
        raising=False,
    )

    title = navigator._tap_first_playable_candidate_below_sponsor_sync("forex trading strategy")

    assert title == "The Only Trading Strategy You'll Ever Need"
    assert taps == [(540, 1688)]


def test_tap_first_playable_candidate_scrolls_past_fullscreen_sponsor_block(monkeypatch) -> None:
    """When a CTA-backed sponsor block covers the entire results area, the
    method should scroll down once and find organic candidates below it."""
    # Initial page: CTA buttons at bottom of a huge sponsor card, only candidate
    # is above the cutoff (inside the ad block).
    page_source_initial = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,262][1080,2274]" />
      <android.view.ViewGroup content-desc="Visit site" bounds="[551,1637][1048,1732]" />
      <android.view.ViewGroup content-desc="Learn more" bounds="[32,1637][530,1732]" />
      <android.view.ViewGroup
        content-desc="This trading strategy makes me $150k/week - play video"
        bounds="[0,262][1080,1801]"
      />
    </hierarchy>
    """
    # Page after scroll: sponsor is gone, real video is visible below cutoff.
    page_source_after_scroll = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,262][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Best Forex Trading Strategy for Beginners - play video"
        bounds="[0,1800][1080,2274]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source_initial)
    navigator._adb_serial = "emulator-5554"
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0
    navigator._rejected_result_titles = set()

    call_counts = {"scroll": 0}
    current_source = [page_source_initial]

    def _preferred_source():
        return current_source[0]

    def _extract_results_bounds():
        return (0, 262, 1080, 2274)

    def _scroll():
        call_counts["scroll"] += 1
        current_source[0] = page_source_after_scroll
        navigator._driver = SimpleNamespace(page_source=page_source_after_scroll)

    taps: list[tuple[int, int]] = []

    monkeypatch.setattr(navigator, "_preferred_results_page_source_sync", _preferred_source, raising=False)
    monkeypatch.setattr(navigator, "_extract_results_bounds_sync", _extract_results_bounds, raising=False)
    monkeypatch.setattr(navigator, "_extract_short_result_bounds_sync", lambda: [], raising=False)
    monkeypatch.setattr(navigator, "_scroll_results_feed_once_sync", _scroll, raising=False)
    monkeypatch.setattr(navigator, "_tap_via_adb_sync", lambda x, y: taps.append((x, y)) or True, raising=False)
    monkeypatch.setattr(navigator, "_await_watch_open_after_tap_sync", lambda query, timeout_seconds: True, raising=False)
    monkeypatch.setattr(navigator, "_reject_reel_watch_surface_sync", lambda: False, raising=False)
    monkeypatch.setattr(navigator, "_extract_current_watch_title_for_query_sync", lambda query: None, raising=False)
    monkeypatch.setattr(navigator, "_extract_current_watch_title_sync", lambda: None, raising=False)

    title = navigator._tap_first_playable_candidate_below_sponsor_sync("forex trading strategies")

    assert title == "Best Forex Trading Strategy for Beginners"
    assert call_counts["scroll"] == 1
    assert len(taps) == 1


def test_tap_first_playable_candidate_relaxes_cutoff_when_only_non_sponsored_match_is_above_it(
    monkeypatch,
) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,262][1080,2274]" />
      <android.widget.TextView
        text="Sponsored"
        bounds="[64,2058][312,2090]"
      />
      <android.view.ViewGroup
        content-desc="This trading strategy is boring, but it makes me $150,000/week - play video"
        bounds="[0,439][1080,1236]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._adb_serial = "emulator-5554"
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0
    navigator._rejected_result_titles = set()
    taps: list[tuple[int, int]] = []

    monkeypatch.setattr(
        navigator,
        "_preferred_results_page_source_sync",
        lambda: page_source,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 262, 1080, 2274),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_short_result_bounds_sync",
        lambda: [],
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_via_adb_sync",
        lambda x, y: taps.append((x, y)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_watch_open_after_tap_sync",
        lambda query, timeout_seconds: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_reject_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_for_query_sync",
        lambda query: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_sync",
        lambda: None,
        raising=False,
    )

    title = navigator._tap_first_playable_candidate_below_sponsor_sync("forex trading strategy")

    assert title == "This trading strategy is boring, but it makes me $150,000/week"
    assert taps == [(540, 741)]

def test_tap_result_candidate_hotspots_keeps_candidate_title_when_watch_opens_offtopic(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._rejected_result_titles = set()
    navigator._last_tapped_result_title = None
    navigator._last_tapped_result_is_short = False

    candidate = NativeResultCandidate(
        title="Best Top Down Analysis Strategy for 2026 | Forex Trading Guide",
        bounds=(0, 1407, 1080, 2148),
        is_short=False,
        is_sponsored=True,
    )

    taps: list[tuple[int, int]] = []
    resolved_titles = iter(["Гражданство Евросоюза. От 4.000€", None])

    monkeypatch.setattr(
        navigator,
        "_tap_via_adb_sync",
        lambda x, y: taps.append((x, y)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_watch_open_after_tap_sync",
        lambda query, timeout_seconds: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_reject_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_for_query_sync",
        lambda query: next(resolved_titles),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_sync",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_recover_results_surface_sync",
        lambda query: None,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.mobile_app.android.youtube.navigator.time.sleep",
        lambda *_args, **_kwargs: None,
    )

    title = navigator._tap_result_candidate_hotspots_sync(candidate, "forex trading strategy")

    assert title == "Best Top Down Analysis Strategy for 2026 | Forex Trading Guide"
    assert taps == [(540, 1688)]


def test_await_watch_open_after_tap_uses_lightweight_dialog_recovery(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._last_tapped_result_title = None
    navigator._last_tapped_result_is_short = False

    recorded_calls: list[tuple[str | None, float, bool]] = []

    monkeypatch.setattr(
        navigator,
        "_wait_for_watch_surface_sync",
        lambda query, timeout_seconds, allow_heavy_dialog_recovery=True: (
            recorded_calls.append((query, timeout_seconds, allow_heavy_dialog_recovery)) or False
        ),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_watchwhile_activity_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_stable_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_current_watch_title_sync",
        lambda query, timeout_seconds: None,
        raising=False,
    )

    assert navigator._await_watch_open_after_tap_sync(
        query="forex trading strategy",
        timeout_seconds=7.5,
    ) is False
    assert recorded_calls == [("forex trading strategy", 7.5, False)]


def test_await_watch_open_after_tap_accepts_stable_watch_surface_without_activity_probe(
    monkeypatch,
) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._last_tapped_result_title = "THE BEST FOREX TRADING STRATEGY | KEEP IT SIMPLE"
    navigator._last_tapped_result_is_short = False

    monkeypatch.setattr(
        navigator,
        "_wait_for_watch_surface_sync",
        lambda query, timeout_seconds, allow_heavy_dialog_recovery=True: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_stable_watch_surface_sync",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_watchwhile_activity_sync",
        lambda: (_ for _ in ()).throw(AssertionError("activity probe should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_current_watch_title_sync",
        lambda query, timeout_seconds: (_ for _ in ()).throw(
            AssertionError("title recovery should not run when stable watch surface already exists")
        ),
        raising=False,
    )

    assert navigator._await_watch_open_after_tap_sync(
        query="forex trading strategy",
        timeout_seconds=4.0,
    ) is True


def test_dismiss_system_dialog_skips_heavy_adb_path_when_disabled(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._driver = SimpleNamespace(find_elements=lambda *_args, **_kwargs: [])

    heavy_calls: list[str] = []

    monkeypatch.setattr(
        navigator,
        "_dismiss_system_dialogs_via_adb_sync",
        lambda: heavy_calls.append("dismiss_via_adb") or False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_via_adb_sync",
        lambda: heavy_calls.append("overlay_via_adb") or False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_sync",
        lambda: False,
        raising=False,
    )

    assert navigator._dismiss_system_dialog_sync(allow_heavy_adb=False) is False
    assert heavy_calls == []


def test_dismiss_system_dialogs_via_adb_skips_dump_without_overlay(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._driver = SimpleNamespace(page_source="")
    navigator._thread_local = SimpleNamespace(
        hard_deadline=float("inf"),
        sync_operation_generation=1,
    )
    navigator._sync_operation_generation = 1

    monkeypatch.setattr(
        navigator,
        "_wait_for_adb_device_sync",
        lambda timeout_seconds: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_via_adb_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_launcher_close_via_adb_dump_sync",
        lambda: (_ for _ in ()).throw(AssertionError("dump close should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_system_dialog_wait_via_adb_dump_sync",
        lambda: (_ for _ in ()).throw(AssertionError("dump wait should not run")),
        raising=False,
    )

    assert navigator._dismiss_system_dialogs_via_adb_sync() is False


def test_dismiss_system_dialogs_via_adb_uses_dump_after_overlay_detected(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._driver = SimpleNamespace(page_source="")
    navigator._thread_local = SimpleNamespace(
        hard_deadline=float("inf"),
        sync_operation_generation=1,
    )
    navigator._sync_operation_generation = 1

    dump_calls: list[str] = []
    wait_calls: list[float] = []

    monkeypatch.setattr(
        navigator,
        "_wait_for_adb_device_sync",
        lambda timeout_seconds: wait_calls.append(timeout_seconds) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_via_adb_sync",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_launcher_close_via_adb_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_system_dialog_wait_via_adb_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_launcher_close_via_adb_dump_sync",
        lambda: dump_calls.append("close_dump") or False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_system_dialog_wait_via_adb_dump_sync",
        lambda: dump_calls.append("wait_dump") or True,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.mobile_app.android.youtube.navigator.time.sleep",
        lambda *_args, **_kwargs: None,
    )

    assert navigator._dismiss_system_dialogs_via_adb_sync() is True
    assert dump_calls == ["close_dump", "wait_dump"]
    assert wait_calls == [6.0, 8.0]


def test_dismiss_system_dialogs_via_adb_background_never_uses_appium_overlay(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._driver = SimpleNamespace(page_source="")
    navigator._thread_local = SimpleNamespace(
        hard_deadline=float("inf"),
        sync_operation_generation=1,
    )
    navigator._sync_operation_generation = 1

    calls: list[str] = []

    monkeypatch.setattr(
        navigator,
        "_wait_for_adb_device_sync",
        lambda timeout_seconds: calls.append(f"wait:{timeout_seconds}") or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_sync",
        lambda: (_ for _ in ()).throw(AssertionError("appium overlay probe should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_system_dialog_overlay_via_adb_sync",
        lambda: calls.append("overlay_adb") or False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_launcher_close_via_adb_dump_sync",
        lambda: (_ for _ in ()).throw(AssertionError("dump tap should not run without overlay")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_system_dialog_wait_via_adb_dump_sync",
        lambda: (_ for _ in ()).throw(AssertionError("dump tap should not run without overlay")),
        raising=False,
    )

    assert navigator._dismiss_system_dialogs_via_adb_background_sync() is False
    assert calls == ["wait:4.0", "overlay_adb"]


def test_check_sync_deadline_raises_when_operation_is_superseded() -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._thread_local = SimpleNamespace(
        hard_deadline=float("inf"),
        sync_operation_generation=3,
    )
    navigator._sync_operation_generation = 4

    with pytest.raises(AndroidUiError, match="hard deadline exceeded"):
        navigator._check_sync_deadline()


def test_dump_ui_hierarchy_via_adb_returns_none_on_timeout(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._thread_local = SimpleNamespace(hard_deadline=float("inf"))

    monkeypatch.setattr(
        navigator_module,
        "require_tool_path",
        lambda name: "adb",
    )
    monkeypatch.setattr(
        navigator,
        "_bounded_subprocess_timeout_sync",
        lambda default_timeout, minimum_timeout=0.5: default_timeout,
        raising=False,
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(navigator_module.subprocess, "run", fake_run)

    assert navigator._dump_ui_hierarchy_via_adb_sync() is None


def test_promote_current_watch_surface_uses_last_tapped_title(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._config = SimpleNamespace(youtube_package="com.google.android.youtube")
    navigator._thread_local = SimpleNamespace(
        hard_deadline=float("inf"),
        sync_operation_generation=1,
    )
    navigator._sync_operation_generation = 1
    navigator._last_open_result_diagnostics = {}
    navigator._last_tapped_result_title = "Quantum Review: AI-Powered Trading Platform"
    navigator._last_tapped_result_is_short = False
    navigator._rejected_result_titles = set()

    monkeypatch.setattr(
        navigator,
        "_safe_current_package_sync",
        lambda: "com.google.android.youtube",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_stable_watch_surface_sync",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_reel_watch_surface_via_adb_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_playerless_watch_shell_sync",
        lambda *args, **kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_search_context_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_for_query_sync",
        lambda query: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_current_watch_title_sync",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_rejected_result_title_sync",
        lambda title: False,
        raising=False,
    )

    promoted_title = navigator._promote_current_watch_surface_sync("quantum ai trading")

    assert promoted_title == "Quantum Review: AI-Powered Trading Platform"
    assert navigator._last_open_result_diagnostics["reason"] == "watch_surface_promoted"
    assert navigator._last_open_result_diagnostics["resolved_title"] == promoted_title


def test_promote_current_watch_surface_uses_adb_watch_activity_before_stable_probe(
    monkeypatch,
) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._config = SimpleNamespace(youtube_package="com.google.android.youtube")
    navigator._thread_local = SimpleNamespace(
        hard_deadline=float("inf"),
        sync_operation_generation=1,
    )
    navigator._sync_operation_generation = 1
    navigator._last_open_result_diagnostics = {}
    navigator._last_tapped_result_title = "Quantum Review: AI-Powered Trading Platform"
    navigator._last_tapped_result_is_short = False
    navigator._rejected_result_titles = set()
    navigator._adb_serial = "emulator-5554"

    monkeypatch.setattr(
        navigator,
        "_safe_current_package_sync",
        lambda: "com.google.android.youtube",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_titles_overlap_sync",
        lambda title, query: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_watchwhile_component_via_adb_sync",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_stable_watch_surface_sync",
        lambda: (_ for _ in ()).throw(AssertionError("stable probe should not run")),
        raising=False,
    )

    promoted_title = navigator._promote_current_watch_surface_sync("quantum ai trading")

    assert promoted_title == "Quantum Review: AI-Powered Trading Platform"
    assert navigator._last_open_result_diagnostics["reason"] == "watch_surface_promoted"


def test_tap_candidate_accepts_matching_non_results_youtube_surface_after_probe_miss(
    monkeypatch,
) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._config = SimpleNamespace(youtube_package="com.google.android.youtube")
    navigator._last_open_result_diagnostics = {}
    candidate = NativeResultCandidate(
        title="Unlocking AI Trading Secrets: A Step by Step Guide to Dominate Markets with Quantum AI",
        bounds=(0, 1394, 1080, 2148),
        is_short=False,
        is_sponsored=False,
    )
    taps: list[tuple[int, int]] = []

    monkeypatch.setattr(
        navigator,
        "_check_sync_deadline",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_tap_via_adb_sync",
        lambda x, y: taps.append((x, y)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_watch_open_after_tap_sync",
        lambda query, timeout_seconds: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_titles_overlap_sync",
        lambda title, query: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_reasonable_topic_video_title_sync",
        lambda title, query: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_results_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_search_context_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_safe_current_package_sync",
        lambda: "com.google.android.youtube",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_watch_surface_sync",
        lambda: (_ for _ in ()).throw(AssertionError("watch probe should be skipped")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_recover_results_surface_sync",
        lambda query: (_ for _ in ()).throw(AssertionError("results recovery should be skipped")),
        raising=False,
    )

    title = navigator._tap_result_candidate_hotspots_sync(
        candidate,
        "quantum ai trading",
    )

    assert title == candidate.title
    assert taps == [(540, 1680)]
    assert (
        navigator._last_open_result_diagnostics["reason"]
        == "post_tap_non_results_accepted_candidate"
    )
    assert navigator._last_open_result_diagnostics["watch_opened"] is True
    assert navigator._last_open_result_diagnostics["watch_surface_probe_skipped"] is True


def test_wait_for_watch_surface_fast_mode_uses_snapshot_markup(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_player" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_panel" />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = None
    navigator._driver = SimpleNamespace(page_source=page_source)
    navigator._config = SimpleNamespace(youtube_package="com.google.android.youtube")
    navigator._thread_local = SimpleNamespace(hard_deadline=float("inf"))
    navigator._last_tapped_result_is_short = False

    monkeypatch.setattr(
        navigator,
        "_check_sync_deadline",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_dismiss_possible_dialogs_sync",
        lambda allow_heavy_adb=True: (_ for _ in ()).throw(
            AssertionError("fast mode should not run dialog recovery")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_safe_current_package_sync",
        lambda: "com.google.android.youtube",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_safe_current_activity_sync",
        lambda: "WatchWhileActivity",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_watch_surface_for_query_sync",
        lambda query: (_ for _ in ()).throw(AssertionError("slow query watch check should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_provisional_watch_surface_for_query_sync",
        lambda query: (_ for _ in ()).throw(AssertionError("slow provisional watch check should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_stable_watch_surface_sync",
        lambda: (_ for _ in ()).throw(AssertionError("slow stable watch check should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_search_input_visible_sync",
        lambda: (_ for _ in ()).throw(AssertionError("slow search input check should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_search_context_sync",
        lambda: (_ for _ in ()).throw(AssertionError("slow search context check should not run")),
        raising=False,
    )

    assert navigator._wait_for_watch_surface_sync(
        query="forex trading strategy",
        timeout_seconds=0.5,
        allow_heavy_dialog_recovery=False,
    ) is True


def test_await_watch_open_after_tap_fast_miss_skips_title_probe(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._last_tapped_result_is_short = False

    monkeypatch.setattr(
        navigator,
        "_wait_for_watch_surface_sync",
        lambda query, timeout_seconds, allow_heavy_dialog_recovery=True: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_stable_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_await_current_watch_title_sync",
        lambda query, timeout_seconds: (_ for _ in ()).throw(
            AssertionError("title probe should be skipped")
        ),
        raising=False,
    )

    assert (
        navigator._await_watch_open_after_tap_sync(
            query="quantum ai trading",
            timeout_seconds=4.0,
        )
        is False
    )


def test_wait_for_watch_surface_fast_mode_uses_adb_watch_activity_before_page_source(
    monkeypatch,
) -> None:
    class BlockingDriver:
        @property
        def page_source(self):
            raise AssertionError("page_source should not run after adb watch activity")

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
    navigator._driver = BlockingDriver()
    navigator._config = SimpleNamespace(youtube_package="com.google.android.youtube")
    navigator._thread_local = SimpleNamespace(hard_deadline=float("inf"))
    navigator._last_tapped_result_title = "Quantum Review: AI-Powered Trading Platform"
    navigator._last_tapped_result_is_short = False
    navigator._rejected_result_titles = set()

    monkeypatch.setattr(
        navigator,
        "_check_sync_deadline",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_dismiss_possible_dialogs_sync",
        lambda allow_heavy_adb=True: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_safe_current_package_sync",
        lambda: "com.google.android.youtube",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_titles_overlap_sync",
        lambda title, query: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_watchwhile_component_via_adb_sync",
        lambda: True,
        raising=False,
    )

    assert navigator._wait_for_watch_surface_sync(
        query="quantum ai trading",
        timeout_seconds=0.5,
        allow_heavy_dialog_recovery=False,
    ) is True


def test_has_results_surface_uses_adb_results_bounds_when_appium_ids_are_missing(monkeypatch) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(find_elements=lambda *_args, **_kwargs: [])

    monkeypatch.setattr(
        navigator,
        "_is_reel_watch_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_search_context_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_extract_results_bounds_sync",
        lambda: (0, 262, 1080, 2148),
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_watch_surface_sync",
        lambda: False,
        raising=False,
    )

    assert navigator._has_results_surface_sync() is True
