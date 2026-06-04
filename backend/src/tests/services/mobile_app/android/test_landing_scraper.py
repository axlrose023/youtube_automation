from __future__ import annotations

from app.api.modules.emulation.models import LandingStatus
from app.services.mobile_app.android.landing_scraper import (
    LandingCaptureResult,
    _patch_ad_record,
)


def test_patch_ad_record_promotes_scraped_landing_into_primary_capture_fields() -> None:
    ad_record = {
        "landing_url": "https://www.googleadservices.com/pagead/aclk?...",
        "capture": {
            "landing_url": "https://www.googleadservices.com/pagead/aclk?...",
            "landing_status": LandingStatus.PENDING,
            "landing_dir": None,
        },
    }

    _patch_ad_record(
        ad_record,
        LandingCaptureResult(
            ad_id="android-1",
            original_url="https://www.googleadservices.com/pagead/aclk?...",
            final_url="https://example.com/final",
            title="Example landing",
            screenshot_path="android_landing/android-1/screenshot.png",
            landing_dir="android_landing/android-1",
            assets_count=3,
        ),
    )

    capture = ad_record["capture"]
    assert capture["landing_url"] == "https://example.com/final"
    assert capture["landing_dir"] == "android_landing/android-1"
    assert capture["landing_status"] == LandingStatus.COMPLETED
    assert capture["landing_scrape_dir"] == "android_landing/android-1"
    assert capture["landing_scrape_screenshot"] == "android_landing/android-1/screenshot.png"
