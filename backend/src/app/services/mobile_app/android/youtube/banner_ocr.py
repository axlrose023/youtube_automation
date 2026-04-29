"""
OCR extractor for YouTube search banner ad screenshots.

Extracts advertiser_domain and headline_text from a saved screencap.
Called as a fallback after page_source parsing yields nothing — because
banner text (advertiser URL, headline) is rendered inside a WebView/Compose
layer that is opaque to UiAutomator.

Banner layout (consistent for 1080x2400 device, scales by percentage):
  y=25%..40%  → large headline text
  y=45%..52%  → "Sponsored · www.domain.com/" line  (normal banners)
  y=55%..72%  → same line pushed lower              (image-only banners)
  y=08%..18%  → domain near top                     (scrolled banners)
  y=57%..65%  → text overlay on image banners
"""
from __future__ import annotations

import re
from pathlib import Path

_TESSERACT_CONFIG = "--oem 3 --psm 6"
_SCALE = 3

_DOMAIN_RE = re.compile(
    r"(?:Sponsored\s*[·•\-]\s*)?"
    r"((?:www\.)?[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
    r"\.[a-z]{2,}(?:\.[a-z]{2})?"
    r"(?:/[^\s]*)?)",
    re.IGNORECASE,
)

_JUNK_LINES: set[str] = {
    "learn more", "visit site", "install", "sponsored",
    "about these results", "subscribe", "view channel",
}
_JUNK_PREFIXES = ("about these", "€", "<", "abaiit", "u.—")
_TRAILING_NOISE_RE = re.compile(r"[\s°•·\-\|]+$")
_LEADING_LOGO_NOISE_RE = re.compile(r"^[a-z]\s+(?=[A-Z])")
_LEADING_SHORT_LOGO_RE = re.compile(r"^[A-Za-z]{1,2}\s+(?=[a-z])")
_JUNK_DOMAINS = {"google.com", "play.google.com", "goo.gl", "youtube.com"}


def _ocr_strip(img, y_start: float, y_end: float) -> list[str]:
    from PIL import Image  # noqa: PLC0415
    w, h = img.size
    crop = img.crop((0, int(h * y_start), w, int(h * y_end)))
    crop = crop.resize((crop.width * _SCALE, crop.height * _SCALE), Image.LANCZOS)
    import pytesseract  # noqa: PLC0415
    raw = pytesseract.image_to_string(crop, config=_TESSERACT_CONFIG)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _is_junk(line: str) -> bool:
    ll = line.casefold()
    if ll in _JUNK_LINES:
        return True
    if any(ll.startswith(p) for p in _JUNK_PREFIXES):
        return True
    if ll.startswith("sponsored") or _DOMAIN_RE.search(line):
        return True
    if not line:
        return True
    # Alpha ratio filters OCR noise like "y | }" where spaces inflate the real-char count
    alpha = sum(1 for c in line if c.isalpha())
    if alpha / len(line) < 0.40:
        return True
    real = sum(1 for c in line if c.isalnum() or c in " .,'-:()")
    return real / len(line) < 0.60


def _find_domain(lines: list[str]) -> str | None:
    for line in lines:
        m = _DOMAIN_RE.search(line)
        if m:
            candidate = m.group(1).rstrip("/").lower()
            if len(candidate) <= 6:
                continue
            host = candidate[4:] if candidate.startswith("www.") else candidate
            host = host.split("/")[0]
            if host in _JUNK_DOMAINS or "google" in host or "play" in host:
                continue
            return host
    return None


def _find_domain_with_band(img) -> tuple[str | None, str | None]:
    for band, y_start, y_end in (
        ("middle", 0.45, 0.52),
        ("lower", 0.55, 0.72),
        ("top", 0.08, 0.18),
    ):
        domain = _find_domain(_ocr_strip(img, y_start, y_end))
        if domain is not None:
            return domain, band
    return None, None


def _clean_headline(line: str) -> str:
    cleaned = _TRAILING_NOISE_RE.sub("", line).strip()
    # App/brand icons at the left edge sometimes OCR as a short prefix.
    cleaned = _LEADING_LOGO_NOISE_RE.sub("", cleaned).strip()
    first = cleaned.split(maxsplit=1)[0] if cleaned.split(maxsplit=1) else ""
    if first.casefold() not in {"ai", "al"}:
        cleaned = _LEADING_SHORT_LOGO_RE.sub("", cleaned).strip()
    return cleaned


def extract_from_banner_screenshot(path: str | Path) -> tuple[str | None, str | None]:
    """
    Returns (advertiser_domain, headline_text) from a banner screencap.
    Either may be None if not found.  ~0.7s on a typical 1080x2400 PNG.
    """
    try:
        from PIL import Image  # noqa: PLC0415
        img = Image.open(str(path))
    except Exception:
        return None, None

    # --- domain ---
    domain, domain_band = _find_domain_with_band(img)

    # --- headline ---
    headline: str | None = None
    if domain_band == "lower":
        headline_bands = ((0.57, 0.65), (0.55, 0.72), (0.25, 0.40), (0.40, 0.52))
    elif domain_band == "top":
        headline_bands = ((0.10, 0.24), (0.08, 0.18), (0.25, 0.40), (0.40, 0.52))
    else:
        headline_bands = ((0.25, 0.40), (0.40, 0.52), (0.57, 0.65))

    for y_start, y_end in headline_bands:
        for line in _ocr_strip(img, y_start, y_end):
            cleaned = _clean_headline(line)
            if not _is_junk(cleaned) and len(cleaned) >= 8:
                headline = cleaned
                break
        if headline is not None:
            break

    return domain, headline


def is_available() -> bool:
    """Check whether pytesseract + tesseract binary are installed."""
    try:
        import pytesseract  # noqa: PLC0415
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False
