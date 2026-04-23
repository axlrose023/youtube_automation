from types import SimpleNamespace

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

    title = navigator._tap_first_playable_candidate_below_sponsor_sync("quantum ai trading")

    assert title == "Quantum AI Trading Full Guide"
    assert taps == [(320, 1692)]


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

    assert title == "This Simple Trading Strategy got me 80% Win Rate"
    assert taps == [(302, 2128)]


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
    assert taps == [(302, 1570)]


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
    assert taps == [(486, 717)]


def test_tap_result_candidate_hotspots_rejects_wrong_opened_title_and_retries(monkeypatch) -> None:
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
    assert taps[:2] == [(302, 1570), (777, 1570)]


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
        "_await_current_watch_title_sync",
        lambda query, timeout_seconds: None,
        raising=False,
    )

    assert navigator._await_watch_open_after_tap_sync(
        query="forex trading strategy",
        timeout_seconds=7.5,
    ) is False
    assert recorded_calls == [("forex trading strategy", 7.5, False)]


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


def test_wait_for_watch_surface_fast_mode_uses_snapshot_markup(monkeypatch) -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_player" />
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/watch_panel" />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._adb_serial = "emulator-5554"
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
