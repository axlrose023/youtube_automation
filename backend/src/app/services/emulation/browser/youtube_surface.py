from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from playwright.async_api import Page

SurfaceMode = Literal["desktop", "mobile"]

YOUTUBE_DESKTOP_URL = "https://www.youtube.com"
YOUTUBE_MOBILE_URL = "https://m.youtube.com"


def is_mobile_youtube_url(raw_url: str | None) -> bool:
    if not raw_url:
        return False
    try:
        host = (urlsplit(raw_url).netloc or "").lower()
    except Exception:
        return False
    return host.startswith("m.youtube.com")


def resolve_surface_mode(raw_url: str | None, preferred_mode: str | None = None) -> SurfaceMode:
    if preferred_mode == "mobile" or is_mobile_youtube_url(raw_url):
        return "mobile"
    return "desktop"


def youtube_home_url(*, current_url: str | None = None, preferred_mode: str | None = None) -> str:
    surface_mode = resolve_surface_mode(current_url, preferred_mode)
    return YOUTUBE_MOBILE_URL if surface_mode == "mobile" else YOUTUBE_DESKTOP_URL


def youtube_results_url(
    query: str,
    *,
    current_url: str | None = None,
    preferred_mode: str | None = None,
) -> str:
    base = youtube_home_url(current_url=current_url, preferred_mode=preferred_mode)
    return f"{base}/results?search_query={quote_plus(query)}"


def absolutize_youtube_href(
    href: str | None,
    *,
    current_url: str | None = None,
    preferred_mode: str | None = None,
) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    base = youtube_home_url(current_url=current_url, preferred_mode=preferred_mode)
    return f"{base}{href}"


def canonicalize_youtube_watch_url(
    raw_url: str | None,
    *,
    current_url: str | None = None,
    preferred_mode: str | None = None,
) -> str:
    absolute = absolutize_youtube_href(
        raw_url,
        current_url=current_url,
        preferred_mode=preferred_mode,
    )
    if not absolute:
        return ""
    try:
        parts = urlsplit(absolute)
    except Exception:
        return absolute

    if "/watch" not in (parts.path or ""):
        return absolute

    query_pairs = parse_qsl(parts.query, keep_blank_values=False)
    allowed_keys = {"v", "list", "index"}
    filtered_pairs = [(key, value) for key, value in query_pairs if key in allowed_keys and value]
    if not filtered_pairs:
        return absolute

    surface_mode = resolve_surface_mode(current_url or absolute, preferred_mode)
    base = YOUTUBE_MOBILE_URL if surface_mode == "mobile" else YOUTUBE_DESKTOP_URL
    filtered_query = urlencode(filtered_pairs, doseq=True)
    return urlunsplit((parts.scheme or "https", urlsplit(base).netloc, parts.path, filtered_query, ""))


async def detect_surface_mode(page: Page) -> SurfaceMode:
    try:
        payload = await page.evaluate(
            "(() => ({"
            "  host: location.host || '',"
            "  width: Number(window.innerWidth || 0),"
            "  ua: navigator.userAgent || ''"
            "}))()"
        )
    except Exception:
        return "mobile" if is_mobile_youtube_url(page.url) else "desktop"

    host = str(payload.get("host") or "").lower()
    ua = str(payload.get("ua") or "").lower()
    width = int(payload.get("width") or 0)

    if host.startswith("m.youtube.com"):
        return "mobile"
    if any(token in ua for token in ("iphone", "ipad", "android", "mobile")):
        return "mobile"
    if 0 < width <= 900:
        return "mobile"
    return "desktop"
