from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AndroidDeviceProfileTemplate:
    template_id: str
    avd_name: str
    proxy_url: str | None = None
    timezone: str | None = None
    locale: str | None = None
    language: str | None = None
    geo_latitude: float | None = None
    geo_longitude: float | None = None
    youtube_account_label: str | None = None
    source_adspower_profile_id: str | None = None

