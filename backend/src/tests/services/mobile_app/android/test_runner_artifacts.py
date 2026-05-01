from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.services.mobile_app.android.runner import AndroidYouTubeProbeRunner
from app.services.mobile_app.models import AndroidWatchSample


def test_parse_feed_sponsored_card_text_extracts_structured_fields() -> None:
    raw = (
        "Sponsored - Day-1 + Daily Pro Payout Rules\n"
        "Tired of payout delays? Some funded futures firms can take weeks. "
        "We pay daily in Pro.\n"
        "Take Profit Trader - Visit site"
    )

    parsed = AndroidYouTubeProbeRunner._parse_feed_sponsored_card_text(raw)

    assert parsed is not None
    assert parsed["sponsor_label"] == "Sponsored"
    assert parsed["headline_text"] == "Day-1 + Daily Pro Payout Rules"
    assert parsed["description_text"] == (
        "Tired of payout delays? Some funded futures firms can take weeks. "
        "We pay daily in Pro."
    )
    assert parsed["advertiser_name"] == "Take Profit Trader"
    assert parsed["cta_text"] == "Visit site"


def test_find_feed_sponsored_card_from_xml_supports_non_english_copy() -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <android.view.ViewGroup
      content-desc="Sponsored - єОселя — вигідні умови&#10;Квартири під Києвом від Інтергал-Буд. Дізнайтесь ціни та доступні планування вже зараз.&#10;Інтергал-Буд - Visit site"
      clickable="true"
      bounds="[0,462][1080,2014]" />
</hierarchy>
"""

    candidate = AndroidYouTubeProbeRunner._find_feed_sponsored_card_from_xml(xml)

    assert candidate is not None
    assert candidate["headline_text"] == "єОселя — вигідні умови"
    assert candidate["advertiser_name"] == "Інтергал-Буд"
    assert candidate["cta_text"] == "Visit site"
    assert candidate["bounds"] == (0, 462, 1080, 2014)


def test_feed_sponsored_tracking_urls_are_not_landing_urls() -> None:
    assert AndroidYouTubeProbeRunner._is_tracking_redirect_url(
        "https://www.googleadservices.com/pagead/aclk?sa=L&adurl=https://example.com/"
    )
    assert not AndroidYouTubeProbeRunner._is_tracking_redirect_url(
        "https://www.dataannotation.tech/coding?gad_source=2"
    )
    assert (
        AndroidYouTubeProbeRunner._host_from_landing_url(
            "https://www.dataannotation.tech/coding?gad_source=2"
        )
        == "dataannotation.tech"
    )


def test_choose_chrome_landing_page_prefers_final_url_over_tracking_url() -> None:
    page = AndroidYouTubeProbeRunner._choose_chrome_landing_page(
        [
            {
                "url": "https://www.googleadservices.com/pagead/aclk?"
                + "x" * 400,
                "title": "",
            },
            {
                "url": "https://try.takeprofittrader.com/funded-trader",
                "title": "Funded Futures Trader - TPT",
            },
        ]
    )

    assert page == {
        "url": "https://try.takeprofittrader.com/funded-trader",
        "title": "Funded Futures Trader - TPT",
    }


def test_choose_chrome_landing_page_ignores_baseline_urls() -> None:
    page = AndroidYouTubeProbeRunner._choose_chrome_landing_page(
        [
            {
                "url": "https://old-ad.example/landing",
                "title": "Old landing",
            },
            {
                "url": "https://new-ad.example/landing",
                "title": "New landing",
            },
        ],
        baseline_urls={"https://old-ad.example/landing"},
    )

    assert page == {
        "url": "https://new-ad.example/landing",
        "title": "New landing",
    }


def test_explicit_search_results_surface_is_not_feed_sponsored_surface() -> None:
    assert AndroidYouTubeProbeRunner._is_explicit_search_results_surface(
        '<node resource-id="com.google.android.youtube:id/search_results" />'
    )
    assert not AndroidYouTubeProbeRunner._is_explicit_search_results_surface(
        '<node content-desc="Sponsored - Remote Work&#10;DataAnnotation - Apply now" />'
    )


def test_cleanup_irrelevant_ad_videos_keeps_debug_artifacts(tmp_path) -> None:
    video_path = tmp_path / "android_probe" / "video" / "watch_debug.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")

    landing_dir = tmp_path / "android_landing" / "session-1"
    landing_dir.mkdir(parents=True)
    (landing_dir / "index.html").write_text("<html></html>")

    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    runner._config = SimpleNamespace(storage=SimpleNamespace(base_path=tmp_path))

    watched_ads = [
        {
            "analysis_summary": {"result": "not_relevant"},
            "video_file": "android_probe/video/watch_debug.mp4",
            "landing_scrape_dir": "android_landing/session-1",
            "landing_dir": "android_landing/session-1",
            "capture": {
                "analysis_status": "not_relevant",
                "video_file": "android_probe/video/watch_debug.mp4",
                "landing_scrape_dir": "android_landing/session-1",
                "landing_dir": "android_landing/session-1",
            },
        }
    ]

    runner._cleanup_irrelevant_ad_videos(watched_ads)

    assert video_path.exists()
    assert landing_dir.exists()
    assert watched_ads[0]["video_file"] == "android_probe/video/watch_debug.mp4"
    assert watched_ads[0]["landing_dir"] == "android_landing/session-1"
    assert watched_ads[0]["capture"]["video_file"] == "android_probe/video/watch_debug.mp4"
    assert watched_ads[0]["capture"]["landing_dir"] == "android_landing/session-1"


def test_watched_ad_identity_seen_matches_same_final_landing() -> None:
    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)

    previous = {
        "display_url": "https://www.factset.com/solutions/portfolio-management-and-trading?utm_source=one",
        "capture": {},
    }
    duplicate = {
        "display_url": "https://www.factset.com/solutions/portfolio-management-and-trading?utm_source=two",
        "capture": {},
    }
    different = {
        "display_url": "https://pipxpert.com/",
        "capture": {},
    }

    assert runner._watched_ad_identity_seen(duplicate, [previous]) is True
    assert runner._watched_ad_identity_seen(different, [previous]) is False


def test_discard_duplicate_ad_media_removes_only_duplicate_files(tmp_path) -> None:
    video_path = tmp_path / "android_probe" / "video" / "duplicate.mp4"
    source_path = tmp_path / "android_probe" / "video" / "duplicate_source.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")
    source_path.write_bytes(b"source")

    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    runner._config = SimpleNamespace(storage=SimpleNamespace(base_path=tmp_path))

    runner._discard_duplicate_ad_media(
        {
            "video_file": "android_probe/video/duplicate.mp4",
            "capture": {
                "source_video_file": "android_probe/video/duplicate_source.mp4",
            },
        }
    )

    assert not video_path.exists()
    assert not source_path.exists()


@pytest.mark.asyncio
async def test_finalize_recording_after_cta_uses_debug_xml_remaining_fallback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml_path = tmp_path / "watch.xml"
    xml_path.write_text(
        """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <android.widget.TextView text="Sponsored" />
  <android.widget.SeekBar content-desc="0 minutes 3 seconds of 0 minutes 22 seconds" />
  <android.widget.TextView text="Visit advertiser" />
</hierarchy>
""",
        encoding="utf-8",
    )

    runner = AndroidYouTubeProbeRunner.__new__(AndroidYouTubeProbeRunner)
    runner._config = SimpleNamespace(
        storage=SimpleNamespace(base_path=tmp_path),
        android_app=SimpleNamespace(probe_watch_sample_interval_seconds=4),
    )

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def fake_stop_recording_handle(*, recorder, recording_handle):
        return "android_probe/video/ad.mp4"

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(runner, "_stop_recording_handle", fake_stop_recording_handle)
    monkeypatch.setattr(runner, "_probe_recorded_video_duration", lambda _: 21.7)

    path, duration = await runner._finalize_recording_after_cta(
        label="test",
        recorder=object(),
        recording_handle=SimpleNamespace(started_monotonic=time.monotonic()),
        samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_cta_text="Visit advertiser",
            )
        ],
        debug_page_source_path=xml_path,
        clicked=False,
        returned_to_youtube=True,
    )

    assert sum(slept) == 19.0
    assert slept == [5.0, 5.0, 5.0, 4.0]
    assert path == "android_probe/video/ad.mp4"
    assert duration == 21.7
