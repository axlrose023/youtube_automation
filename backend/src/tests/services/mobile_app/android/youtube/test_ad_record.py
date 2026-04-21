from __future__ import annotations

from pathlib import Path

from app.api.modules.emulation.models import LandingStatus
from app.services.mobile_app.android.youtube.ad_record import build_watched_ad_record
from app.services.mobile_app.android.youtube.ads import AndroidAdCtaProbeResult
from app.services.mobile_app.models import AndroidWatchSample


def test_build_watched_ad_record_extracts_display_url_from_overlay_text() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_signal_labels=[
                    "Sponsored - Арбітраж Трафіку з Нуля\nwww.traffic-one.academy/ - Visit site"
                ],
                ad_cta_labels=["Visit site"],
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["display_url"] == "www.traffic-one.academy/"
    assert record["advertiser_domain"] == "www.traffic-one.academy"
    assert record["capture"]["landing_url"] == "www.traffic-one.academy/"
    assert record["capture"]["landing_status"] == LandingStatus.SKIPPED


def test_build_watched_ad_record_clamps_short_ad_duration_from_sponsor_label() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_sponsor_label="Sponsored · 0:02",
            ),
            AndroidWatchSample(
                offset_seconds=2,
                ad_detected=True,
                ad_sponsor_label="Sponsored · 0:02",
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path="android_probe/video/ad.mp4",
        recorded_video_duration_seconds=4.0,
    )

    assert record is not None
    assert record["ad_duration_seconds"] == 2.0
    assert record["watched_seconds"] == 2.0


def test_build_watched_ad_record_uses_chrome_url_bar_from_cta_xml(tmp_path: Path) -> None:
    xml_path = tmp_path / "cta.xml"
    xml_path.write_text(
        """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <android.widget.TextView
    text="hvoya.kiev.ua"
    resource-id="com.android.chrome:id/url_bar" />
</hierarchy>
""",
        encoding="utf-8",
    )

    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_cta_text="Visit site",
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=AndroidAdCtaProbeResult(
            clicked=True,
            label="Visit site",
            destination_package="com.android.chrome",
            debug_page_source_path=xml_path,
        ),
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["display_url"] == "hvoya.kiev.ua"
    assert record["cta_href"] == "hvoya.kiev.ua"
    assert record["capture"]["landing_url"] == "hvoya.kiev.ua"
    assert record["capture"]["cta_href"] == "hvoya.kiev.ua"
    assert record["capture"]["landing_status"] == LandingStatus.PENDING
    assert record["capture"]["landing_dir"] is None


def test_build_watched_ad_record_uses_skip_floor_for_short_observed_window() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                skip_available=True,
                ad_cta_text="Visit site",
            ),
            AndroidWatchSample(
                offset_seconds=2,
                ad_detected=True,
                skip_available=True,
                ad_cta_text="Visit site",
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["watched_seconds"] == 5.0


def test_build_watched_ad_record_uses_ad_seekbar_without_resource_id(tmp_path: Path) -> None:
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

    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_signal_labels=["Sponsored", "Visit advertiser"],
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=xml_path,
        ad_cta_result=None,
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["ad_duration_seconds"] == 22.0
    assert record["watched_seconds"] == 3.0


def test_build_watched_ad_record_extracts_cta_from_debug_content_desc_without_resource_id(
    tmp_path: Path,
) -> None:
    xml_path = tmp_path / "watch.xml"
    xml_path.write_text(
        """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <android.widget.TextView text="Sponsored" />
  <android.view.ViewGroup content-desc="Learn more" />
  <android.view.ViewGroup content-desc="Visit site" />
</hierarchy>
""",
        encoding="utf-8",
    )

    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=xml_path,
        ad_cta_result=None,
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["cta_text"] == "Visit site"
    assert record["cta_candidates"] == ["Visit site", "Learn more"]


def test_build_watched_ad_record_extracts_contact_us_cta_from_ad_visible_lines() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_signal_labels=["Sponsored", "Visit advertiser"],
                ad_visible_lines=["Sponsored", "Visit advertiser", "Contact us"],
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["cta_text"] == "Contact us"
    assert record["cta_candidates"] == ["Visit advertiser", "Contact us"]


def test_build_watched_ad_record_persists_ad_offsets_and_video_duration() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=4,
                ad_detected=True,
                ad_visible_lines=["Sponsored", "Visit site"],
            ),
            AndroidWatchSample(
                offset_seconds=11,
                ad_detected=True,
                ad_visible_lines=["Sponsored", "Visit site"],
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path="android_probe/video/ad.mp4",
        recorded_video_duration_seconds=29.5,
    )

    assert record is not None
    assert record["first_ad_offset_seconds"] == 4.0
    assert record["last_ad_offset_seconds"] == 11.0
    assert record["recorded_video_duration_seconds"] == 29.5
    assert record["capture"]["recorded_video_duration_seconds"] == 29.5
    assert record["capture"]["first_ad_offset_seconds"] == 4.0
    assert record["capture"]["last_ad_offset_seconds"] == 11.0


def test_build_watched_ad_record_does_not_inflate_short_ad_window_from_raw_screenrecord() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=92,
                ad_detected=True,
                ad_progress_seconds=103,
                ad_duration_seconds=388,
                ad_visible_lines=["Sponsored", "Learn more"],
            ),
            AndroidWatchSample(
                offset_seconds=96,
                ad_detected=True,
                ad_progress_seconds=103,
                ad_duration_seconds=388,
                ad_visible_lines=["Sponsored", "Learn more"],
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path="android_probe/video/ad.mp4",
        recorded_video_duration_seconds=92.290044,
    )

    assert record is not None
    assert record["first_ad_offset_seconds"] == 92.0
    assert record["last_ad_offset_seconds"] == 96.0
    assert record["ad_first_progress_seconds"] == 103.0
    assert record["ad_last_progress_seconds"] == 103.0
    assert record["watched_seconds"] == 4.0
    assert record["ad_completion_reason"] == "observed_window"


def test_build_watched_ad_record_filters_generic_overlay_lines() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_visible_lines=[
                    "Sponsored",
                    "Visit site",
                    "Video player",
                    "Expand Mini Player",
                    "0 minutes 7 seconds of 0 minutes 33 seconds",
                ],
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["visible_lines"] == ["Visit site", "Sponsored"]
    assert record["full_text"] == "Visit site\nSponsored"


def test_build_watched_ad_record_marks_duration_completed_without_skip_click() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=6,
                ad_detected=True,
                skip_available=True,
                ad_progress_seconds=1,
                ad_duration_seconds=5,
                ad_visible_lines=["Sponsored", "ozon-ltd.com", "Get quote"],
            ),
            AndroidWatchSample(
                offset_seconds=10,
                ad_detected=True,
                skip_available=True,
                ad_progress_seconds=5,
                ad_duration_seconds=5,
                ad_visible_lines=["Sponsored", "ozon-ltd.com", "Get quote"],
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path="android_probe/video/ad.mp4",
        recorded_video_duration_seconds=72.5,
    )

    assert record is not None
    assert record["skip_clicked"] is False
    assert record["skip_visible"] is True
    assert record["ad_first_progress_seconds"] == 1.0
    assert record["ad_last_progress_seconds"] == 5.0
    assert record["ad_completion_reason"] == "duration_completed"


def test_build_watched_ad_record_does_not_treat_absolute_progress_as_watched_duration() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_signal_labels=["Sponsored", "Visit advertiser"],
            ),
            AndroidWatchSample(
                offset_seconds=22,
                ad_detected=True,
                ad_progress_seconds=42,
                ad_duration_seconds=60,
                ad_signal_labels=["Sponsored", "Visit advertiser"],
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=None,
        recorded_video_path="android_probe/video/ad.mp4",
        recorded_video_duration_seconds=22.757178,
    )

    assert record is not None
    assert record["ad_first_progress_seconds"] == 42.0
    assert record["ad_last_progress_seconds"] == 42.0
    assert record["watched_seconds"] == 22.757178
    assert record["ad_completion_reason"] == "observed_window"


def test_build_watched_ad_record_does_not_mix_landing_text_into_overlay_full_text(
    tmp_path: Path,
) -> None:
    cta_xml = tmp_path / "cta.xml"
    cta_xml.write_text(
        """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <android.widget.TextView text="Forest Etalon - Etalon House" />
  <android.widget.TextView text="etalonhouse.com.ua" resource-id="com.android.chrome:id/url_bar" />
  <android.widget.TextView text="Share" />
</hierarchy>
""",
        encoding="utf-8",
    )

    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_headline_text="Pocket Broker",
                ad_cta_text="Install",
                ad_visible_lines=["Pocket Broker", "Install", "Sponsored"],
            )
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=AndroidAdCtaProbeResult(
            clicked=True,
            label="Install",
            destination_package="com.android.chrome",
            debug_page_source_path=cta_xml,
            landing_url="https://etalonhouse.com.ua/offer/forest-etalon-house/",
        ),
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["headline_text"] == "Pocket Broker"
    assert "Forest Etalon - Etalon House" not in record["full_text"]
    assert "etalonhouse.com.ua" not in record["full_text"]
    assert record["capture"]["landing_url"] == "https://etalonhouse.com.ua/offer/forest-etalon-house/"


def test_build_watched_ad_record_selects_single_ad_segment_when_watch_samples_span_ad_pod() -> None:
    record = build_watched_ad_record(
        watch_samples=[
            AndroidWatchSample(
                offset_seconds=0,
                ad_detected=True,
                ad_headline_text="Pocket Broker",
                ad_cta_text="Install",
                ad_progress_seconds=19,
                ad_duration_seconds=20,
                ad_visible_lines=["Pocket Broker", "Install", "Sponsored · 1 of 2 · 0:01"],
            ),
            AndroidWatchSample(
                offset_seconds=1,
                ad_detected=True,
                ad_headline_text="Forest Etalon - Etalon House",
                ad_display_url="etalonhouse.com.ua",
                ad_cta_text="Visit site",
                ad_progress_seconds=1,
                ad_duration_seconds=15,
                ad_visible_lines=[
                    "Forest Etalon - Etalon House",
                    "etalonhouse.com.ua",
                    "Visit site",
                    "Sponsored · 2 of 2 · 0:15",
                ],
            ),
        ],
        watch_debug_screen_path=None,
        watch_debug_page_source_path=None,
        ad_cta_result=AndroidAdCtaProbeResult(
            clicked=True,
            label="Visit site",
            pre_click_headline_text="Forest Etalon - Etalon House",
            pre_click_display_url="etalonhouse.com.ua",
            landing_url="https://etalonhouse.com.ua/offer/forest-etalon-house/",
        ),
        recorded_video_path=None,
        recorded_video_duration_seconds=None,
    )

    assert record is not None
    assert record["headline_text"] == "Forest Etalon - Etalon House"
    assert record["cta_text"] == "Visit site"
    assert "Pocket Broker" not in record["full_text"]
    assert record["visible_lines"][0] == "Forest Etalon - Etalon House"
    assert "multi_ad_segments:2" in record["capture"]["capture_notes"]
