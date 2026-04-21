from __future__ import annotations

from .template import AndroidDeviceProfileTemplate


def build_template_from_adspower_metadata(
    *,
    template_id: str,
    avd_name: str,
    metadata: dict[str, object],
) -> AndroidDeviceProfileTemplate:
    return AndroidDeviceProfileTemplate(
        template_id=template_id,
        avd_name=avd_name,
        proxy_url=_as_optional_str(metadata.get("proxy_url")),
        timezone=_as_optional_str(metadata.get("timezone")),
        locale=_as_optional_str(metadata.get("locale")),
        language=_as_optional_str(metadata.get("language")),
        geo_latitude=_as_optional_float(metadata.get("geo_latitude")),
        geo_longitude=_as_optional_float(metadata.get("geo_longitude")),
        youtube_account_label=_as_optional_str(metadata.get("youtube_account_label")),
        source_adspower_profile_id=_as_optional_str(metadata.get("source_adspower_profile_id")),
    )


def build_default_template(
    *,
    template_id: str,
    avd_name: str,
) -> AndroidDeviceProfileTemplate:
    return AndroidDeviceProfileTemplate(template_id=template_id, avd_name=avd_name)


def _as_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _as_optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None

