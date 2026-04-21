from types import SimpleNamespace

import app.services.mobile_app.android.youtube.navigator as navigator_module
from app.services.mobile_app.android.youtube.navigator import AndroidYouTubeNavigator


def test_broad_money_query_rejects_generic_paypal_cashout_listicle() -> None:
    title = "5 Best Apps to Earn Money in 2025 – Instant PayPal Cashout!"

    assert AndroidYouTubeNavigator._is_broad_money_query_sync("immediate earn") is True
    assert AndroidYouTubeNavigator._is_generic_money_listicle_title_sync(title) is True
    assert AndroidYouTubeNavigator._is_reasonable_topic_video_title_sync(title, "immediate earn") is False


def test_specific_finance_query_keeps_relevant_quantum_title() -> None:
    title = "Quantum AI Has Launched — Canada’s Fully-Automated AI Crypto Bot"

    assert AndroidYouTubeNavigator._is_broad_money_query_sync("quantum ai") is False
    assert AndroidYouTubeNavigator._is_reasonable_topic_video_title_sync(title, "quantum ai") is True


def test_broad_money_query_keeps_non_listicle_finance_title() -> None:
    title = "Immediate Edge Review 2026 | Quantum AI Crypto Earnings"

    assert AndroidYouTubeNavigator._is_broad_money_query_sync("immediate earn") is True
    assert AndroidYouTubeNavigator._is_generic_money_listicle_title_sync(title) is False
    assert AndroidYouTubeNavigator._is_reasonable_topic_video_title_sync(title, "immediate earn") is True


def test_ranking_prefers_specific_finance_title_over_generic_money_listicle() -> None:
    good = "Exposing the Quantum AI Investment Scam (Full Recording)"
    bad = "10 Best Apps to Earn Money Fast in 2026 | Instant PayPal Cashout"

    good_score = AndroidYouTubeNavigator._score_result_title_for_query_sync(
        good,
        "immediate earn quantum ai",
    )
    bad_score = AndroidYouTubeNavigator._score_result_title_for_query_sync(
        bad,
        "immediate earn quantum ai",
    )

    assert good_score > bad_score


def test_playerless_watch_shell_ignores_buffering_overlay_when_real_player_present() -> None:
    page_source = """
    <hierarchy>
      <android.widget.FrameLayout resource-id="com.google.android.youtube:id/watch_player" />
      <android.widget.ImageView resource-id="com.google.android.youtube:id/playerless_thumbnail" />
      <android.view.ViewGroup content-desc="Expand Mini Player" />
    </hierarchy>
    """

    assert AndroidYouTubeNavigator._source_has_playerless_watch_shell_sync(page_source) is False


def test_playerless_watch_shell_is_detected_without_real_player() -> None:
    page_source = """
    <hierarchy>
      <android.widget.ImageView resource-id="com.google.android.youtube:id/playerless_thumbnail" />
      <android.view.ViewGroup content-desc="Expand Mini Player" />
    </hierarchy>
    """

    assert AndroidYouTubeNavigator._source_has_playerless_watch_shell_sync(page_source) is True


def test_recover_results_surface_uses_hard_deeplink_fallback_from_browsing_surface(
    monkeypatch,
) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._config = SimpleNamespace(youtube_package="com.google.android.youtube")
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(navigator_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        navigator,
        "_safe_current_package_sync",
        lambda: "com.google.android.youtube",
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_mixed_watch_results_surface_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_loading_results_shell_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_blank_youtube_shell_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_watch_surface_for_query_sync",
        lambda query: False,
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
        "_has_openable_result_sync",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_is_search_input_visible_sync",
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
        "_is_browsing_surface_sync",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_open_results_via_deeplink_sync",
        lambda query: calls.append(("soft", query)) or False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_run_results_deeplink_intent_sync",
        lambda query, force_stop_before_intent: calls.append(
            ("hard", (query, force_stop_before_intent))
        )
        or True,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_dismiss_possible_dialogs_sync",
        lambda: calls.append(("dismiss", None)) or False,
        raising=False,
    )

    navigator._recover_results_surface_sync("ai trading bot")

    assert calls == [
        ("soft", "ai trading bot"),
        ("hard", ("ai trading bot", True)),
        ("dismiss", None),
    ]


def test_has_query_watch_transition_requires_query_matched_watch_surface(
    monkeypatch,
) -> None:
    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)

    monkeypatch.setattr(
        navigator,
        "_is_watch_surface_for_query_sync",
        lambda query: False,
        raising=False,
    )
    monkeypatch.setattr(
        navigator,
        "_has_provisional_watch_surface_for_query_sync",
        lambda query: False,
        raising=False,
    )

    assert navigator._has_query_watch_transition_sync("forex trading strategy") is False

    monkeypatch.setattr(
        navigator,
        "_has_provisional_watch_surface_for_query_sync",
        lambda query: True,
        raising=False,
    )

    assert navigator._has_query_watch_transition_sync("forex trading strategy") is True


def test_results_source_stats_ignore_sponsored_playable_signal() -> None:
    page_source = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Sponsored - Bunq wealth platform - 12 seconds - bunq - play video"
        bounds="[0,1323][1080,2274]"
      />
    </hierarchy>
    """

    stats = AndroidYouTubeNavigator._results_source_stats_sync(page_source)

    assert stats == (True, False, 0, 0)


def test_extract_title_candidates_prefers_richer_adb_results_dump(monkeypatch) -> None:
    sparse_appium_xml = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.view.ViewGroup
        content-desc="Sponsored - Bunq wealth platform - 12 seconds - bunq - play video"
        bounds="[0,1323][1080,2274]"
      />
    </hierarchy>
    """
    richer_adb_xml = """
    <hierarchy>
      <android.view.ViewGroup resource-id="com.google.android.youtube:id/results" bounds="[0,902][1080,2274]" />
      <android.widget.TextView
        resource-id="com.google.android.youtube:id/title"
        text="Quantum AI | AI Trading Software 2026 | Review"
        bounds="[58,1104][1012,1189]"
      />
    </hierarchy>
    """

    navigator = AndroidYouTubeNavigator.__new__(AndroidYouTubeNavigator)
    navigator._driver = SimpleNamespace(page_source=sparse_appium_xml)
    navigator._adb_serial = "emulator-5554"
    navigator._results_source_cache_xml = None
    navigator._results_source_cache_at = 0.0

    monkeypatch.setattr(
        navigator,
        "_dump_ui_hierarchy_via_adb_sync",
        lambda: richer_adb_xml,
        raising=False,
    )

    candidates = navigator._extract_title_result_candidates_from_page_source_sync()

    assert [candidate.title for candidate in candidates] == [
        "Quantum AI | AI Trading Software 2026 | Review"
    ]
