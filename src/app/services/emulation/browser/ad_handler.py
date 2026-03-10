from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote

from playwright.async_api import Page

from ..core.selectors import (
    AD_BUTTON_SELECTOR,
    AD_CAPTION_SELECTOR,
    AD_INFO_SELECTOR,
    AD_OVERLAY_SELECTOR,
    AD_SKIP_SELECTOR,
)
from ..core.state import SessionState
from .ad_capture import AdCaptureProvider, CaptureHandle, CaptureResult
from .humanizer import Humanizer

logger = logging.getLogger(__name__)


_DOMAIN_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", re.IGNORECASE)
_HTTP_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_POD_RE = re.compile(
    r"(?P<label>Sponsored|Реклама)(?:\s+(?P<position>\d+)\s+of\s+(?P<total>\d+))?(?:\s+(?P<trailing>.+))?",
    re.IGNORECASE,
)
_POD_COUNT_ONLY_RE = re.compile(r"^\d+\s+of\s+\d+$", re.IGNORECASE)
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
    _capture_handle: CaptureHandle | None = field(default=None, repr=False)
    _capture_task: asyncio.Task[CaptureResult] | None = field(
        default=None, repr=False,
    )
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
            capture_payload = {
                "video_src_url": self._capture_result.video_src_url,
                "video_status": self._capture_result.video_status,
                "video_file": self._capture_result.video_file,
                "landing_url": self._capture_result.landing_url,
                "landing_status": self._capture_result.landing_status,
                "landing_dir": self._capture_result.landing_dir,
                "screenshot_paths": self._capture_result.screenshot_paths,
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


class AdHandler:
    def __init__(
        self,
        page: Page,
        humanizer: Humanizer,
        state: SessionState,
        capture: AdCaptureProvider | None = None,
    ) -> None:
        self._page = page
        self._h = humanizer
        self._state = state
        self._capture = capture
        self._capturing = False
        self._pending_records: list[AdRecord] = []
        self._interrupted_records: list[AdRecord] | None = None

    async def check(self) -> bool:
        try:
            overlay = await self._page.query_selector(AD_OVERLAY_SELECTOR)
            return overlay is not None and await overlay.is_visible()
        except Exception:
            return False

    async def try_skip(self) -> bool:
        try:
            skip_button = await self._page.query_selector(AD_SKIP_SELECTOR)
            if skip_button and await skip_button.is_visible():
                await self._h.delay(0.5, 1.0)
                await self._h.click(skip_button)
                return True
        except Exception:
            pass
        return False

    async def handle(self, *, patient: bool) -> list[dict[str, object]]:
        await self._h.delay(0.15, 0.35)
        if self._capturing or not await self.check():
            return []
        await self._focus_player_for_ad()

        ad_tol = self._state.personality.ad_tolerance
        logger.info(
            "Session %s: ad detected (patient=%s, ad_tol=%.2f)",
            self._state.session_id, patient, ad_tol,
        )

        self._capturing = True
        cancelled = False
        try:
            records = await self._watch_active_ads(patient=patient, ad_tol=ad_tol)
            self._interrupted_records = None
        except asyncio.CancelledError:
            cancelled = True
            records = self._interrupted_records or []
            self._interrupted_records = None
        finally:
            self._capturing = False

        result: list[dict[str, object]] = []
        for rec in records:
            state_entry = self._state.add_watched_ad(rec.to_dict())
            rec._state_entry = state_entry
            result.append(state_entry)
            if rec._capture_task:
                self._pending_records.append(rec)

        await self._reconcile_ready_captures()

        if result:
            logger.info("Session %s: ad capture complete (%d ads)", self._state.session_id, len(result))
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _focus_player_for_ad(self) -> None:
        try:
            await self._page.evaluate(
                """() => {
                    const selectors = [
                        "#movie_player",
                        "#player",
                        "ytd-player",
                        "video",
                    ];
                    for (const selector of selectors) {
                        const node = document.querySelector(selector);
                        if (!node) continue;
                        if (typeof node.scrollIntoView === "function") {
                            node.scrollIntoView({ block: "center", inline: "nearest" });
                        } else {
                            window.scrollTo(0, 0);
                        }
                        return;
                    }
                    window.scrollTo(0, 0);
                }""",
            )
        except Exception:
            return

    async def flush_pending_captures(self) -> None:
        if not self._pending_records:
            return
        pending = list(self._pending_records)
        self._pending_records.clear()
        await self._await_captures(pending)



    async def _watch_active_ads(self, *, patient: bool, ad_tol: float) -> list[AdRecord]:
        records: list[AdRecord] = []
        current: AdRecord | None = None
        try:
            while await self.check():
                snapshot = await self._snapshot()
                parsed = _parse_snapshot(snapshot)

                if current is None:
                    current = _new_record(snapshot, parsed)
                    await self._start_capture(current)
                    await self._ensure_capture_landing(current)
                elif _is_new_segment(current, snapshot, parsed):
                    current.end_reason = "next_ad"
                    await self._seal_capture(current)
                    records.append(current)
                    current = _new_record(snapshot, parsed)
                    await self._start_capture(current)
                    await self._ensure_capture_landing(current)
                else:
                    _merge_into(current, snapshot, parsed)
                    await self._ensure_capture_landing(current)
                    await self._maybe_upgrade_capture(current)

                if self._should_force_skip(current, patient=patient, ad_tol=ad_tol):
                    skip_clicked = await self.try_skip()
                    current.skip_clicked = skip_clicked
                    current.skip_visible = True
                    if skip_clicked:
                        current.end_reason = "forced_skip"
                        logger.info("Session %s: ad forced skip", self._state.session_id)
                        await self._h.delay(0.8, 1.2)
                        break

                await self._h.delay(0.35, 0.6)
        except asyncio.CancelledError:
            if current is not None:
                if not current.end_reason:
                    current.end_reason = "interrupted"
                await self._seal_capture(current)
                records.append(current)
            self._interrupted_records = records
            logger.warning(
                "Session %s: ad handling interrupted, preserving %d ad records",
                self._state.session_id,
                len(records),
            )
            raise

        if current is not None:
            if not current.end_reason:
                current.end_reason = "completed"
            await self._seal_capture(current)
            records.append(current)

        return records

    async def _maybe_upgrade_capture(self, rec: AdRecord) -> None:
        if not self._capture or not rec._capture_handle:
            return
        if rec._capture_handle.recording_started:
            return
        try:
            upgraded = await self._capture.try_upgrade_recording(rec._capture_handle, self._page)
        except Exception as exc:
            logger.warning(
                "Session %s: failed to upgrade capture %s: %s",
                self._state.session_id,
                rec.capture_id,
                exc,
            )
            return
        if upgraded:
            logger.info(
                "Session %s: capture %s upgraded to recorder during ad",
                self._state.session_id,
                rec.capture_id,
            )

    async def _start_capture(self, rec: AdRecord) -> None:
        if not self._capture:
            return
        cap_id = uuid.uuid4().hex[:12]
        landing = _pick_landing_url(rec)
        try:
            rec._capture_handle = await self._capture.start_capture(
                session_id=self._state.session_id,
                capture_id=cap_id,
                main_page=self._page,
                landing_url=landing,
            )
        except Exception as exc:
            logger.warning(
                "Session %s: failed to start capture %s: %s",
                self._state.session_id,
                cap_id,
                exc,
            )
            rec.capture_id = None
            rec._capture_handle = None
            return
        rec.capture_id = cap_id

    async def _seal_capture(self, rec: AdRecord) -> None:
        _freeze_timing(rec)
        if not self._capture or not rec._capture_handle:
            return
        await self._ensure_capture_landing(rec)
        try:
            await self._capture.stop_capture(rec._capture_handle, self._page)
        except Exception as exc:
            logger.warning(
                "Session %s: failed to stop capture %s: %s",
                self._state.session_id,
                rec.capture_id,
                exc,
            )
        rec._capture_task = asyncio.create_task(
            self._capture.finalize_capture(rec._capture_handle),
        )

    async def _ensure_capture_landing(self, rec: AdRecord) -> None:
        if not self._capture or not rec._capture_handle:
            return
        handle = rec._capture_handle
        if handle.landing_task is not None:
            return
        landing = _pick_landing_url(rec)
        if not landing:
            return
        try:
            await self._capture.attach_landing_url(handle, landing)
        except Exception as exc:
            logger.warning(
                "Session %s: failed to attach landing to capture %s: %s",
                self._state.session_id,
                rec.capture_id,
                exc,
            )

    async def _await_captures(self, records: list[AdRecord]) -> None:
        for rec in records:
            if not rec._capture_task:
                continue
            try:
                rec._capture_result = await rec._capture_task
                self._log_capture_outcome(rec)
            except Exception as exc:
                logger.warning(
                    "Session %s: capture %s failed: %s",
                    self._state.session_id,
                    rec.capture_id,
                    exc,
                )
            self._refresh_state_entry(rec)

    async def _reconcile_ready_captures(self) -> None:
        if not self._pending_records:
            return

        remaining: list[AdRecord] = []
        for rec in self._pending_records:
            task = rec._capture_task
            if not task or not task.done():
                remaining.append(rec)
                continue
            try:
                rec._capture_result = await task
                self._log_capture_outcome(rec)
            except Exception as exc:
                logger.warning(
                    "Session %s: capture %s failed: %s",
                    self._state.session_id,
                    rec.capture_id,
                    exc,
                )
            self._refresh_state_entry(rec)
        self._pending_records = remaining

    def _log_capture_outcome(self, rec: AdRecord) -> None:
        result = rec._capture_result
        if result is None:
            return
        screenshot_count = len(result.screenshot_paths)
        has_video_signal = bool(result.video_src_url) or result.video_status in {
            "completed",
            "failed",
            "fallback_screenshots",
        }
        logger.info(
            (
                "Session %s: ad capture %s finalized "
                "(has_video_signal=%s, video_status=%s, video_saved=%s, "
                "screenshots=%d, landing_status=%s, advertiser=%r, headline=%r, end_reason=%s)"
            ),
            self._state.session_id,
            result.capture_id,
            has_video_signal,
            result.video_status,
            bool(result.video_file),
            screenshot_count,
            result.landing_status,
            rec.advertiser_domain,
            rec.headline_text,
            rec.end_reason,
        )

    def _refresh_state_entry(self, rec: AdRecord) -> None:
        if rec._state_entry is None:
            return
        rec._state_entry.update(rec.to_dict())

    def _should_force_skip(self, rec: AdRecord, *, patient: bool, ad_tol: float) -> bool:
        if not rec.skip_visible:
            return False
        elapsed = max(time.monotonic() - rec._started_monotonic, 0.0)
        dur = rec.ad_duration_seconds
        if dur and dur > 0:
            linger_limit = max(dur + max(4.0, ad_tol * 8.0), 20.0)
        else:
            linger_limit = 60.0 if patient else 45.0
        return elapsed >= linger_limit

    async def _snapshot(self) -> dict[str, object]:
        try:
            return await self._page.evaluate(
                f"""() => {{
                    const splitLines = (v) => (v || "")
                        .split(/\\n+/)
                        .map((l) => l.replace(/\\s+/g, " ").trim())
                        .filter(Boolean);
                    const collectLines = (sel) => {{
                        const out = [];
                        for (const el of document.querySelectorAll(sel))
                            for (const l of splitLines(el.innerText || el.textContent || ""))
                                if (!out.includes(l)) out.push(l);
                        return out;
                    }};
                    const collectBtns = (sel) => Array.from(document.querySelectorAll(sel)).map((el) => ({{
                        text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim(),
                        ariaLabel: el.getAttribute("aria-label"),
                        href: el.getAttribute("href"),
                    }}));

                    const mp = document.getElementById("movie_player");
                    const rawLines = mp ? splitLines(mp.innerText || mp.textContent || "") : [];
                    const skipBtns = collectBtns("{AD_SKIP_SELECTOR}");
                    const skip = skipBtns[0] || null;
                    const video = document.querySelector("video");

                    return {{
                        adShowing: !!document.querySelector(".ad-showing"),
                        currentTime: video ? Number(video.currentTime || 0) : null,
                        duration: video && Number.isFinite(video.duration) ? Number(video.duration) : null,
                        rawLines,
                        adInfoLines: collectLines("{AD_INFO_SELECTOR}"),
                        captionLines: collectLines("{AD_CAPTION_SELECTOR}"),
                        buttons: collectBtns("{AD_BUTTON_SELECTOR}"),
                        skipVisible: !!skip,
                        skipText: skip ? skip.text : null,
                    }};
                }}"""
            )
        except Exception:
            return {
                "adShowing": False, "currentTime": None, "duration": None,
                "rawLines": [], "adInfoLines": [], "captionLines": [],
                "buttons": [], "skipVisible": False, "skipText": None,
            }




def _norm(value: object) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _is_noise(text: str) -> bool:
    if not text:
        return True
    low = text.lower()
    return low in _UI_NOISE or bool(_AUTO_GENERATED_RE.match(text))


def _is_cta(text: str) -> bool:
    return bool(text) and _norm(text).lower() in _CTA_TOKENS


def _extend_unique(target: list[str], values: list) -> None:
    for v in values:
        s = v.strip() if isinstance(v, str) else ""
        if s and s not in target:
            target.append(s)


def _coerce_lines(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s and not _is_noise(s) and s not in out:
                out.append(s)
    return out


def _decode_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d = unquote(value)
        return d or value
    except Exception:
        return value


def _round_opt(value: object) -> float | None:
    return round(float(value), 3) if isinstance(value, (int, float)) else None


def _str_val(d: dict[str, object], key: str) -> str | None:
    v = d.get(key)
    return v if isinstance(v, str) else None


def _int_val(d: dict[str, object], key: str) -> int | None:
    v = d.get(key)
    return v if isinstance(v, int) else None


def _float_val(d: dict[str, object], key: str, default: float = 0.0) -> float:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else default


def _list_val(d: dict[str, object], key: str) -> list:
    v = d.get(key)
    return list(v) if isinstance(v, list) else []




def _parse_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    raw_lines = _coerce_lines(snapshot.get("rawLines"))
    info_lines = _coerce_lines(snapshot.get("adInfoLines"))
    buttons = snapshot.get("buttons")
    buttons = buttons if isinstance(buttons, list) else []
    all_lines = raw_lines + info_lines

    sponsor_line = _find_sponsor_line(all_lines)
    sponsor_label, pod_pos, pod_total = _parse_sponsor_line(sponsor_line)
    landing_urls = _extract_urls(all_lines)
    domain = _extract_domain([sponsor_line, *landing_urls, *all_lines])

    cta_text = None
    cta_candidates: list[str] = []
    cta_href = None
    my_ad_center = False

    for btn in buttons:
        if not isinstance(btn, dict):
            continue
        text = _norm(btn.get("text"))
        aria = _norm(btn.get("ariaLabel"))
        href = _norm(btn.get("href"))
        merged = f"{text} {aria}".lower()
        if "my ad center" in merged:
            my_ad_center = True
            continue
        label = text or aria
        if href and href not in landing_urls:
            landing_urls.append(href)
        if label and _is_cta(label):
            if label not in cta_candidates:
                cta_candidates.append(label)
            if not cta_text:
                cta_text = label
        if href and not cta_href:
            cta_href = href or None

    for line in raw_lines:
        n = _norm(line)
        if n and not _is_noise(n) and _is_cta(n) and n not in cta_candidates:
            cta_candidates.append(n)
    if not cta_text and cta_candidates:
        cta_text = cta_candidates[0]


    skip_text = _norm(snapshot.get("skipText"))
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
        d = _decode_url(u)
        if d:
            url_blocked.add(d.lower())
    content_blocked = {*(c.lower() for c in cta_candidates), *url_blocked}
    if domain:
        content_blocked.add(domain.lower())
    content = _filter_lines(visible, blocked=content_blocked, extra_checks=[lambda _l, n: _is_cta(n)])

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
        "display_url_decoded": _decode_url(display_url),
        "landing_urls": landing_urls,
        "headline_text": headline,
        "description_text": "\n".join(description),
        "description_lines": description,
        "visible_lines": visible,
        "my_ad_center_visible": my_ad_center,
    }


def _filter_lines(
    lines: list[str],
    *,
    blocked: set[str],
    extra_checks: list | None = None,
) -> list[str]:
    result: list[str] = []
    for line in lines:
        n = _norm(line)
        if not n or _is_noise(n):
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
        n = _norm(line)
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
        _norm(m.group("label")),
        int(m.group("position")) if m.group("position") else None,
        int(m.group("total")) if m.group("total") else None,
    )


def _extract_domain(values: list[str | None]) -> str | None:
    for v in values:
        n = _norm(v)
        if n:
            m = _DOMAIN_RE.search(n)
            if m:
                return m.group(0).lower()
    return None


def _extract_urls(lines: list[str]) -> list[str]:
    urls: list[str] = []
    for line in lines:
        n = _norm(line)
        if n and not _is_noise(n) and _DOMAIN_RE.search(n) and n not in urls:
            urls.append(n)
    return urls


def _pick_landing_url(rec: AdRecord) -> str | None:
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


def _normalize_landing_candidate(value: str | None) -> str | None:
    n = _norm(value)
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
        return domain_match.group(0)
    return None




def _new_record(snapshot: dict[str, object], parsed: dict[str, object]) -> AdRecord:
    rec = AdRecord(
        started_at=time.time(),
        _started_monotonic=time.monotonic(),
        _last_current_time=_float_val(snapshot, "currentTime"),
        skip_visible=bool(snapshot.get("skipVisible")),
        skip_text=_str_val(snapshot, "skipText"),
        cta_text=_str_val(parsed, "cta_text"),
        cta_candidates=_list_val(parsed, "cta_candidates"),
        cta_href=_str_val(parsed, "cta_href"),
        sponsor_label=_str_val(parsed, "sponsor_label"),
        advertiser_domain=_str_val(parsed, "advertiser_domain"),
        display_url=_str_val(parsed, "display_url"),
        display_url_decoded=_str_val(parsed, "display_url_decoded"),
        landing_urls=_list_val(parsed, "landing_urls"),
        headline_text=_str_val(parsed, "headline_text"),
        description_text=_str_val(parsed, "description_text"),
        description_lines=_list_val(parsed, "description_lines"),
        ad_pod_position=_int_val(parsed, "ad_pod_position"),
        ad_pod_total=_int_val(parsed, "ad_pod_total"),
        ad_duration_seconds=_round_opt(snapshot.get("duration")),
        my_ad_center_visible=bool(parsed.get("my_ad_center_visible")),
    )
    _merge_into(rec, snapshot, parsed)
    return rec


def _merge_into(rec: AdRecord, snapshot: dict[str, object], parsed: dict[str, object]) -> None:

    for fname in _FIRST_SEEN_FIELDS:
        if getattr(rec, fname) is None:
            src = snapshot if fname == "skip_text" else parsed
            key = "skipText" if fname == "skip_text" else fname
            val = src.get(key)
            if val:
                setattr(rec, fname, val)

    _extend_unique(rec.cta_candidates, _list_val(parsed, "cta_candidates"))
    _extend_unique(rec.landing_urls, _list_val(parsed, "landing_urls"))
    _extend_unique(rec.description_lines, _list_val(parsed, "description_lines"))

    if snapshot.get("skipVisible"):
        rec.skip_visible = True
    if parsed.get("my_ad_center_visible"):
        rec.my_ad_center_visible = True

    dur = snapshot.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        if rec.ad_duration_seconds is None or dur > rec.ad_duration_seconds:
            rec.ad_duration_seconds = _round_opt(dur)

    _extend_unique(rec.visible_lines, _list_val(parsed, "visible_lines"))
    _extend_unique(rec.caption_lines, _list_val(snapshot, "captionLines"))


    rec._last_current_time = _float_val(snapshot, "currentTime")
    offset = round(max(time.monotonic() - rec._started_monotonic, 0.0), 1)
    sample_vis = _list_val(parsed, "visible_lines")
    sample_cap = _list_val(snapshot, "captionLines")
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


def _freeze_timing(rec: AdRecord) -> None:
    if rec.ended_at is not None and rec.watched_seconds is not None:
        return
    rec.ended_at = time.time()
    rec.watched_seconds = round(max(time.monotonic() - rec._started_monotonic, 0.0), 1)


def _is_new_segment(rec: AdRecord, snapshot: dict[str, object], parsed: dict[str, object]) -> bool:
    next_pos = _int_val(parsed, "ad_pod_position")
    if rec.ad_pod_position is not None and next_pos is not None and next_pos != rec.ad_pod_position:
        return True

    prev_t = rec._last_current_time
    cur_t = _float_val(snapshot, "currentTime")
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

    next_dom = _str_val(parsed, "advertiser_domain")
    if rec.advertiser_domain and next_dom and rec.advertiser_domain != next_dom and cur_t < 2.0:
        return True

    return False




def _ordered_lines_from_samples(samples: list[dict[str, object]], key: str) -> list[str]:
    out: list[str] = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        lines = s.get(key)
        if not isinstance(lines, list):
            continue
        for line in lines:
            n = _norm(line)
            if n and not _is_noise(n) and n not in out:
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
