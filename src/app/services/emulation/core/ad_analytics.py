from __future__ import annotations

from typing import Any

_ANALYTICS_AD_FIELDS = (
    "watched_seconds",
    "completed",
    "skip_clicked",
    "skip_visible",
    "skip_text",
    "cta_text",
    "cta_href",
    "sponsor_label",
    "advertiser_domain",
    "display_url",
    "landing_urls",
    "headline_text",
    "description_text",
    "ad_pod_position",
    "ad_pod_total",
    "ad_duration_seconds",
    "my_ad_center_visible",
    "full_text",
    "full_visible_text",
    "full_caption_text",
)

_ANALYTICS_LIST_FIELDS = {
    "landing_urls",
}


def build_ad_analytics_record(record: dict[str, object]) -> dict[str, Any]:
    analytics: dict[str, Any] = {}
    for field_name in _ANALYTICS_AD_FIELDS:
        value = record.get(field_name)
        if field_name in _ANALYTICS_LIST_FIELDS:
            analytics[field_name] = list(value) if isinstance(value, list) else []
            continue
        if isinstance(value, list):
            analytics[field_name] = list(value)
            continue
        analytics[field_name] = value

    analytics["full_text"] = str(record.get("full_text") or "")
    analytics["full_visible_text"] = str(record.get("full_visible_text") or "")
    analytics["full_caption_text"] = str(record.get("full_caption_text") or "")
    return analytics


def build_ads_analytics(records: list[dict[str, object]]) -> list[dict[str, Any]]:
    return [build_ad_analytics_record(record) for record in records]
