from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

if TYPE_CHECKING:
    from .capture import CaptureHandle, CaptureResult

_DOMAIN_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", re.IGNORECASE)
_HTTP_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_BLOCKED_AD_DOMAINS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_POD_RE = re.compile(
    r"(?P<label>Sponsored|Реклама)(?:\s+(?P<position>\d+)\s+(?:of|из)\s+(?P<total>\d+))?(?:\s+(?P<trailing>.+))?",
    re.IGNORECASE,
)
_POD_COUNT_ONLY_RE = re.compile(r"^\d+\s+(?:of|из)\s+\d+$", re.IGNORECASE)
_TIMER_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?\s*/\s*\d{1,2}:\d{2}(?::\d{2})?$")
_AUTO_GENERATED_RE = re.compile(r"^[\w\s\-А-Яа-яЁёІіЇїЄє]+\(auto-generated\)$", re.IGNORECASE)
_CTA_TOKENS = frozenset({
    "visit site", "learn more", "shop now", "play now", "watch now",
    "sign up", "book now", "download", "contact us", "call now",
    "apply now", "install now", "open app", "details",
    "подробнее", "детали", "деталі", "перейти", "купить",
    "в магазин", "узнать больше",
})
_UI_NOISE = frozenset({"subtitles/closed captions", "click for settings", "c"})

_FIRST_SEEN_FIELDS = (
    "cta_text", "cta_href", "sponsor_label", "advertiser_domain",
    "display_url", "display_url_decoded", "headline_text", "description_text",
    "ad_pod_position", "ad_pod_total", "skip_text",
)


# ── Text helpers ──────────────────────────────────────────────


def norm(value: object) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def is_noise(text: str) -> bool:
    if not text:
        return True
    low = text.lower()
    return low in _UI_NOISE or bool(_AUTO_GENERATED_RE.match(text))


def is_cta(text: str) -> bool:
    return bool(text) and norm(text).lower() in _CTA_TOKENS


def extend_unique(target: list[str], values: list) -> None:
    for v in values:
        s = v.strip() if isinstance(v, str) else ""
        if s and s not in target:
            target.append(s)


def coerce_lines(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s and not is_noise(s) and s not in out:
                out.append(s)
    return out


def decode_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d = unquote(value)
        return d or value
    except Exception:
        return value


def round_opt(value: object) -> float | None:
    return round(float(value), 3) if isinstance(value, (int, float)) else None


def str_val(d: dict[str, object], key: str) -> str | None:
    v = d.get(key)
    return v if isinstance(v, str) else None


def int_val(d: dict[str, object], key: str) -> int | None:
    v = d.get(key)
    return v if isinstance(v, int) else None


def float_val(d: dict[str, object], key: str, default: float = 0.0) -> float:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else default


def list_val(d: dict[str, object], key: str) -> list:
    v = d.get(key)
    return list(v) if isinstance(v, list) else []


# ── Snapshot parsing ──────────────────────────────────────────


def parse_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    raw_lines = coerce_lines(snapshot.get("rawLines"))
    info_lines = coerce_lines(snapshot.get("adInfoLines"))
    buttons = snapshot.get("buttons")
    buttons = buttons if isinstance(buttons, list) else []
    raw_links = _normalize_candidates(list_val(snapshot, "links"))
    all_lines = raw_lines + info_lines

    sponsor_line = _find_sponsor_line(all_lines)
    sponsor_label, pod_pos, pod_total = _parse_sponsor_line(sponsor_line)
    landing_urls = _extract_urls(all_lines)
    extend_unique(landing_urls, raw_links)
    domain = _extract_domain([sponsor_line, *landing_urls, *all_lines])

    cta_text = None
    cta_candidates: list[str] = []
    cta_href = None
    my_ad_center = False

    for btn in buttons:
        if not isinstance(btn, dict):
            continue
        text = norm(btn.get("text"))
        aria = norm(btn.get("ariaLabel"))
        href = _normalize_landing_candidate(norm(btn.get("href")))
        merged = f"{text} {aria}".lower()
        if "my ad center" in merged:
            my_ad_center = True
            continue
        label = text or aria
        if href and href not in landing_urls:
            landing_urls.append(href)
        if label and is_cta(label):
            if label not in cta_candidates:
                cta_candidates.append(label)
            if not cta_text:
                cta_text = label
        if href and not cta_href:
            cta_href = href or None

    for line in raw_lines:
        n = norm(line)
        if n and not is_noise(n) and is_cta(n) and n not in cta_candidates:
            cta_candidates.append(n)
    if not cta_text and cta_candidates:
        cta_text = cta_candidates[0]

    skip_text = norm(snapshot.get("skipText"))
    blocked = {v.lower() for v in (sponsor_line, skip_text, "my ad center") if v}
    visible = _filter_lines(
        [*raw_lines, *info_lines, *cta_candidates, *landing_urls],
        blocked=blocked,
        extra_checks=[
            lambda low, _n: low.startswith("sponsored"),
            lambda _l, n: bool(_POD_COUNT_ONLY_RE.match(n)),
            lambda _l, n: bool(_TIMER_RE.match(n)),
        ],
    )

    url_blocked = {u.lower() for u in landing_urls}
    for u in landing_urls:
        d = decode_url(u)
        if d:
            url_blocked.add(d.lower())
    content_blocked = {*(c.lower() for c in cta_candidates), *url_blocked}
    if domain:
        content_blocked.add(domain.lower())
    content = _filter_lines(visible, blocked=content_blocked, extra_checks=[lambda _l, n: is_cta(n)])

    headline = content[0] if content else (visible[0] if visible else None)

    desc_blocked = {headline.lower()} if headline else set()
    desc_blocked |= url_blocked | {c.lower() for c in cta_candidates}
    description = _filter_lines(
        content, blocked=desc_blocked,
        extra_checks=[lambda _l, n: bool(_POD_COUNT_ONLY_RE.match(n))],
    )

    display_url = landing_urls[0] if landing_urls else cta_href
    return {
        "sponsor_label": sponsor_label,
        "ad_pod_position": pod_pos,
        "ad_pod_total": pod_total,
        "advertiser_domain": domain,
        "cta_text": cta_text,
        "cta_candidates": cta_candidates,
        "cta_href": cta_href,
        "display_url": display_url,
        "display_url_decoded": decode_url(display_url),
        "landing_urls": landing_urls,
        "headline_text": headline,
        "description_text": "\n".join(description),
        "description_lines": description,
        "visible_lines": visible,
        "my_ad_center_visible": my_ad_center,
    }


# ── Record construction / merging ─────────────────────────────


@dataclass
class AdRecord:
    started_at: float = 0.0
    ended_at: float | None = None
    watched_seconds: float | None = None
    _started_monotonic: float = 0.0
    _last_current_time: float = 0.0
    _last_sample_key: tuple | None = None

    skip_clicked: bool = False
    skip_visible: bool = False
    skip_text: str | None = None
    end_reason: str | None = None

    cta_text: str | None = None
    cta_candidates: list[str] = field(default_factory=list)
    cta_href: str | None = None
    sponsor_label: str | None = None
    advertiser_domain: str | None = None
    display_url: str | None = None
    display_url_decoded: str | None = None
    landing_urls: list[str] = field(default_factory=list)
    headline_text: str | None = None
    description_text: str | None = None
    description_lines: list[str] = field(default_factory=list)
    ad_pod_position: int | None = None
    ad_pod_total: int | None = None
    ad_duration_seconds: float | None = None
    my_ad_center_visible: bool = False

    visible_lines: list[str] = field(default_factory=list)
    caption_lines: list[str] = field(default_factory=list)
    text_samples: list[dict[str, object]] = field(default_factory=list)

    capture_id: str | None = None

    # capture-related runtime fields (set by AdHandler)
    _capture_handle: CaptureHandle | None = field(default=None, repr=False)
    _capture_task: asyncio.Task[CaptureResult] | None = field(default=None, repr=False)
    _capture_result: CaptureResult | None = field(default=None, repr=False)
    _state_entry: dict[str, object] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, object]:
        watched = self.watched_seconds
        if watched is None:
            watched = round(max(time.monotonic() - self._started_monotonic, 0.0), 1)
        full_text, full_text_source = _build_full_text(
            self.visible_lines, self.caption_lines, self.text_samples,
        )
        capture_payload: dict[str, object] | None = None
        if self._capture_result:
            r = self._capture_result
            capture_payload = {
                "video_src_url": r.video_src_url,
                "video_status": r.video_status,
                "video_file": r.video_file,
                "landing_url": r.landing_url,
                "landing_status": r.landing_status,
                "landing_dir": r.landing_dir,
                "screenshot_paths": r.screenshot_paths,
            }
        elif self.capture_id:
            capture_payload = {
                "video_src_url": None,
                "video_status": "pending",
                "video_file": None,
                "landing_url": self._capture_handle.landing_url if self._capture_handle else None,
                "landing_status": "pending",
                "landing_dir": None,
                "screenshot_paths": [],
            }
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at if self.ended_at is not None else time.time(),
            "watched_seconds": watched,
            "completed": not self.skip_clicked and self.end_reason != "forced_skip",
            "skip_clicked": self.skip_clicked,
            "skip_visible": self.skip_visible,
            "skip_text": self.skip_text,
            "cta_text": self.cta_text,
            "cta_candidates": list(self.cta_candidates),
            "cta_href": self.cta_href,
            "sponsor_label": self.sponsor_label,
            "advertiser_domain": self.advertiser_domain,
            "display_url": self.display_url,
            "display_url_decoded": self.display_url_decoded,
            "landing_urls": list(self.landing_urls),
            "headline_text": self.headline_text,
            "description_text": self.description_text,
            "description_lines": list(self.description_lines),
            "ad_pod_position": self.ad_pod_position,
            "ad_pod_total": self.ad_pod_total,
            "ad_duration_seconds": self.ad_duration_seconds,
            "my_ad_center_visible": self.my_ad_center_visible,
            "full_text": full_text,
            "full_text_source": full_text_source,
            "full_visible_text": "\n".join(self.visible_lines),
            "full_caption_text": "\n".join(self.caption_lines),
            "visible_lines": list(self.visible_lines),
            "caption_lines": list(self.caption_lines),
            "text_samples": list(self.text_samples),
            "end_reason": self.end_reason,
            "capture_id": self.capture_id,
            "capture": capture_payload,
        }


def new_record(snapshot: dict[str, object], parsed: dict[str, object]) -> AdRecord:
    rec = AdRecord(
        started_at=time.time(),
        _started_monotonic=time.monotonic(),
        _last_current_time=float_val(snapshot, "currentTime"),
        skip_visible=bool(snapshot.get("skipVisible")),
        skip_text=str_val(snapshot, "skipText"),
        cta_text=str_val(parsed, "cta_text"),
        cta_candidates=list_val(parsed, "cta_candidates"),
        cta_href=str_val(parsed, "cta_href"),
        sponsor_label=str_val(parsed, "sponsor_label"),
        advertiser_domain=str_val(parsed, "advertiser_domain"),
        display_url=str_val(parsed, "display_url"),
        display_url_decoded=str_val(parsed, "display_url_decoded"),
        landing_urls=list_val(parsed, "landing_urls"),
        headline_text=str_val(parsed, "headline_text"),
        description_text=str_val(parsed, "description_text"),
        description_lines=list_val(parsed, "description_lines"),
        ad_pod_position=int_val(parsed, "ad_pod_position"),
        ad_pod_total=int_val(parsed, "ad_pod_total"),
        ad_duration_seconds=round_opt(snapshot.get("duration")),
        my_ad_center_visible=bool(parsed.get("my_ad_center_visible")),
    )
    merge_into(rec, snapshot, parsed)
    return rec


def merge_into(rec: AdRecord, snapshot: dict[str, object], parsed: dict[str, object]) -> None:
    for fname in _FIRST_SEEN_FIELDS:
        if getattr(rec, fname) is None:
            src = snapshot if fname == "skip_text" else parsed
            key = "skipText" if fname == "skip_text" else fname
            val = src.get(key)
            if val:
                setattr(rec, fname, val)

    extend_unique(rec.cta_candidates, list_val(parsed, "cta_candidates"))
    extend_unique(rec.landing_urls, list_val(parsed, "landing_urls"))
    extend_unique(rec.description_lines, list_val(parsed, "description_lines"))

    if snapshot.get("skipVisible"):
        rec.skip_visible = True
    if parsed.get("my_ad_center_visible"):
        rec.my_ad_center_visible = True

    dur = snapshot.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        if rec.ad_duration_seconds is None or dur > rec.ad_duration_seconds:
            rec.ad_duration_seconds = round_opt(dur)

    extend_unique(rec.visible_lines, list_val(parsed, "visible_lines"))
    extend_unique(rec.caption_lines, list_val(snapshot, "captionLines"))

    rec._last_current_time = float_val(snapshot, "currentTime")
    offset = round(max(time.monotonic() - rec._started_monotonic, 0.0), 1)
    sample_vis = list_val(parsed, "visible_lines")
    sample_cap = list_val(snapshot, "captionLines")
    sample_skip = bool(snapshot.get("skipVisible"))
    sample_key = (tuple(sample_vis), tuple(sample_cap), sample_skip)
    if sample_key != rec._last_sample_key:
        rec.text_samples.append({
            "offset_seconds": offset,
            "visible_lines": sample_vis,
            "caption_lines": sample_cap,
            "skip_visible": sample_skip,
        })
        rec._last_sample_key = sample_key


def freeze_timing(rec: AdRecord) -> None:
    if rec.ended_at is not None and rec.watched_seconds is not None:
        return
    rec.ended_at = time.time()
    rec.watched_seconds = round(max(time.monotonic() - rec._started_monotonic, 0.0), 1)


def is_new_segment(rec: AdRecord, snapshot: dict[str, object], parsed: dict[str, object]) -> bool:
    next_pos = int_val(parsed, "ad_pod_position")
    if rec.ad_pod_position is not None and next_pos is not None and next_pos != rec.ad_pod_position:
        return True

    prev_t = rec._last_current_time
    cur_t = float_val(snapshot, "currentTime")
    if prev_t > 3.0 and cur_t + 1.0 < prev_t:
        return True

    prev_dur = rec.ad_duration_seconds
    cur_dur = snapshot.get("duration")
    if (
        prev_dur is not None
        and isinstance(cur_dur, (int, float))
        and prev_t > 3.0
        and cur_t < 2.0
        and abs(float(cur_dur) - prev_dur) > max(5.0, prev_dur * 0.25)
    ):
        return True

    next_dom = str_val(parsed, "advertiser_domain")
    if rec.advertiser_domain and next_dom and rec.advertiser_domain != next_dom and cur_t < 2.0:
        return True

    return False


def pick_landing_url(rec: AdRecord) -> str | None:
    candidates: list[str | None] = [
        rec.cta_href,
        *rec.landing_urls,
        rec.display_url_decoded,
        rec.display_url,
        rec.advertiser_domain,
    ]
    for value in candidates:
        cleaned = _normalize_landing_candidate(value)
        if cleaned:
            return cleaned
    return None


# ── Private helpers ───────────────────────────────────────────


def _filter_lines(
    lines: list[str],
    *,
    blocked: set[str],
    extra_checks: list | None = None,
) -> list[str]:
    result: list[str] = []
    for line in lines:
        n = norm(line)
        if not n or is_noise(n):
            continue
        low = n.lower()
        if low in blocked:
            continue
        if extra_checks and any(check(low, n) for check in extra_checks):
            continue
        if n not in result:
            result.append(n)
    return result


def _find_sponsor_line(lines: list[str]) -> str | None:
    for line in lines:
        n = norm(line)
        if n and _POD_RE.search(n):
            return n
    return None


def _parse_sponsor_line(line: str | None) -> tuple[str | None, int | None, int | None]:
    if not line:
        return None, None, None
    m = _POD_RE.search(line)
    if not m:
        return None, None, None
    return (
        norm(m.group("label")),
        int(m.group("position")) if m.group("position") else None,
        int(m.group("total")) if m.group("total") else None,
    )


def _extract_domain(values: list[str | None]) -> str | None:
    for v in values:
        n = norm(v)
        if n:
            m = _DOMAIN_RE.search(n)
            if m:
                domain = m.group(0).lower()
                if domain in _BLOCKED_AD_DOMAINS:
                    continue
                return domain
    return None


def _extract_urls(lines: list[str]) -> list[str]:
    urls: list[str] = []
    for line in lines:
        n = norm(line)
        if not n or is_noise(n) or not _DOMAIN_RE.search(n):
            continue
        candidate = _normalize_landing_candidate(n)
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def _normalize_landing_candidate(value: str | None) -> str | None:
    n = norm(value)
    if not n:
        return None
    n = n.strip(" \t\r\n\"'`()[]{}<>.,;")
    url_match = _HTTP_URL_RE.search(n)
    if url_match:
        return url_match.group(0).rstrip(").,;:!?")
    if "..." in n or "…" in n:
        match = _DOMAIN_RE.search(n)
        return match.group(0) if match else None
    if " " not in n and _DOMAIN_RE.search(n):
        return n.rstrip(").,;:!?")
    domain_match = _DOMAIN_RE.search(n)
    if domain_match:
        n = domain_match.group(0)
    resolved = _unwrap_redirect_url(n)
    if not resolved:
        return None
    lowered = resolved.lower()
    if lowered.startswith("http://"):
        resolved = "https://" + resolved[7:]
        lowered = resolved.lower()
    if lowered.startswith("https://www.youtube.com/watch") or lowered.startswith("https://youtube.com/watch"):
        return None
    if lowered.startswith("https://www.youtube.com/results") or lowered.startswith("https://youtube.com/results"):
        return None
    if lowered.startswith("https://www.youtube.com/") or lowered.startswith("https://youtube.com/"):
        return None
    if lowered.startswith("https://m.youtube.com/"):
        return None
    return resolved


def _unwrap_redirect_url(value: str) -> str | None:
    n = norm(value)
    if not n:
        return None
    try:
        parsed = urlsplit(n)
    except Exception:
        return n

    query = parse_qs(parsed.query)
    for key in ("q", "url", "adurl"):
        target_values = query.get(key)
        if not target_values:
            continue
        candidate = norm(target_values[0])
        if candidate and candidate != n:
            nested = _unwrap_redirect_url(candidate)
            return nested or candidate

    return n or None


def _normalize_candidates(values: list[object]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = _normalize_landing_candidate(value)
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _ordered_lines_from_samples(samples: list[dict[str, object]], key: str) -> list[str]:
    out: list[str] = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        lines = s.get(key)
        if not isinstance(lines, list):
            continue
        for line in lines:
            n = norm(line)
            if n and not is_noise(n) and n not in out:
                out.append(n)
    return out


def _build_full_text(
    visible: list[str],
    captions: list[str],
    samples: list[dict[str, object]],
) -> tuple[str, str]:
    ordered_cap = _ordered_lines_from_samples(samples, "caption_lines")
    ordered_vis = _ordered_lines_from_samples(samples, "visible_lines")

    if ordered_cap:
        merged = list(ordered_cap)
        for line in ordered_vis:
            if line not in merged:
                merged.append(line)
        return "\n".join(merged), "captions_plus_overlay"

    if captions:
        merged = list(captions)
        for line in visible:
            if line not in merged:
                merged.append(line)
        return "\n".join(merged), "captions_plus_overlay"

    if ordered_vis:
        return "\n".join(ordered_vis), "overlay"

    return "\n".join(visible), "overlay"
