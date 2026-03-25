from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import replace

from playwright.async_api import Page

from ...core.selectors import (
    AD_BUTTON_SELECTOR,
    AD_CAPTION_SELECTOR,
    AD_INFO_SELECTOR,
    AD_OVERLAY_SELECTOR,
    AD_SKIP_SELECTOR,
)
from ...core.session.state import SessionState
from ..humanizer import Humanizer
from app.api.modules.ad_captures.models import VideoStatus

from .capture import AdCaptureProvider, CaptureResult
from .snapshot import (
    AdRecord,
    freeze_timing,
    is_new_segment,
    merge_into,
    new_record,
    parse_snapshot,
    pick_landing_url,
)

logger = logging.getLogger(__name__)


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
        self._completed_video_captures_by_src: dict[str, str] = {}
        self._completed_captures_by_creative: dict[str, CaptureResult] = {}

    # ── Public API ────────────────────────────────────────────

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
            state_entry = self._publish_record(rec)
            if state_entry is not None:
                result.append(state_entry)

        await self._reconcile_ready_captures()

        if result:
            logger.info("Session %s: ad capture complete (%d ads)", self._state.session_id, len(result))
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def flush_pending_captures(self) -> None:
        if not self._pending_records:
            return
        pending = list(self._pending_records)
        self._pending_records.clear()
        await self._await_captures(pending)

    # ── Ad watching loop ──────────────────────────────────────

    async def _watch_active_ads(self, *, patient: bool, ad_tol: float) -> list[AdRecord]:
        records: list[AdRecord] = []
        current: AdRecord | None = None
        try:
            while await self.check():
                if self._state.stop_requested:
                    logger.info(
                        "Session %s: stopping ad handling on user stop request",
                        self._state.session_id,
                    )
                    if current is not None and not current.end_reason:
                        current.end_reason = "stopped"
                    break
                snapshot = await self._take_snapshot()
                parsed = parse_snapshot(snapshot)

                if current is None:
                    current = new_record(snapshot, parsed)
                    await self._start_capture(current)
                    await self._ensure_capture_landing(current)
                elif is_new_segment(current, snapshot, parsed):
                    if self._is_continuation_segment(current, parsed, snapshot):
                        logger.info(
                            "Session %s: merged continuation segment into current ad (%s)",
                            self._state.session_id,
                            self._creative_key(current),
                        )
                        merge_into(current, snapshot, parsed)
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
                        continue
                    current.end_reason = "next_ad"
                    await self._seal_capture(current)
                    self._publish_record(current)
                    records.append(current)
                    current = new_record(snapshot, parsed)
                    await self._start_capture(current)
                    await self._ensure_capture_landing(current)
                else:
                    merge_into(current, snapshot, parsed)
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
                self._publish_record(current)
                records.append(current)
            self._interrupted_records = records
            logger.warning(
                "Session %s: ad handling interrupted, preserving %d ad records",
                self._state.session_id, len(records),
            )
            raise

        if current is not None:
            if not current.end_reason:
                current.end_reason = "completed"
            await self._seal_capture(current)
            self._publish_record(current)
            records.append(current)

        return records

    # ── Capture lifecycle ─────────────────────────────────────

    async def _start_capture(self, rec: AdRecord) -> None:
        creative_key = self._creative_key(rec)
        if creative_key:
            reused_result = self._completed_captures_by_creative.get(creative_key)
            if reused_result is not None:
                rec.capture_id = f"reuse-{uuid.uuid4().hex[:12]}"
                rec._capture_result = replace(reused_result, capture_id=rec.capture_id)
                logger.info(
                    "Session %s: ad capture %s reused existing creative artifacts (%s)",
                    self._state.session_id,
                    rec.capture_id,
                    creative_key,
                )
                return

        if not self._capture:
            return
        cap_id = uuid.uuid4().hex[:12]
        landing = pick_landing_url(rec)
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
                self._state.session_id, cap_id, exc,
            )
            rec.capture_id = None
            rec._capture_handle = None
            return
        rec.capture_id = cap_id

    async def _seal_capture(self, rec: AdRecord) -> None:
        freeze_timing(rec)
        if not self._capture or not rec._capture_handle:
            return
        await self._ensure_capture_landing(rec)
        try:
            await self._capture.stop_capture(rec._capture_handle, self._page)
        except Exception as exc:
            logger.warning(
                "Session %s: failed to stop capture %s: %s",
                self._state.session_id, rec.capture_id, exc,
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
        landing = pick_landing_url(rec)
        if not landing:
            return
        try:
            await self._capture.attach_landing_url(handle, landing)
        except Exception as exc:
            logger.warning(
                "Session %s: failed to attach landing to capture %s: %s",
                self._state.session_id, rec.capture_id, exc,
            )

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
                self._state.session_id, rec.capture_id, exc,
            )
            return
        if upgraded:
            logger.info(
                "Session %s: capture %s upgraded to recorder during ad",
                self._state.session_id, rec.capture_id,
            )

    async def _await_captures(self, records: list[AdRecord]) -> None:
        for rec in records:
            if not rec._capture_task:
                continue
            try:
                rec._capture_result = await rec._capture_task
                self._normalize_capture_result(rec)
                self._cache_creative_result(rec)
                self._log_capture_outcome(rec)
            except Exception as exc:
                logger.warning(
                    "Session %s: capture %s failed: %s",
                    self._state.session_id, rec.capture_id, exc,
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
                self._normalize_capture_result(rec)
                self._cache_creative_result(rec)
                self._log_capture_outcome(rec)
            except Exception as exc:
                logger.warning(
                    "Session %s: capture %s failed: %s",
                    self._state.session_id, rec.capture_id, exc,
                )
            self._refresh_state_entry(rec)
        self._pending_records = remaining

    # ── Skip / focus / snapshot ───────────────────────────────

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

    async def _take_snapshot(self) -> dict[str, object]:
        try:
            return await self._page.evaluate(
                f"""() => {{
                    const splitLines = (v) => (v || "")
                        .split(/\\n+/)
                        .map((l) => l.replace(/\\s+/g, " ").trim())
                        .filter(Boolean);
                    const normalizeHref = (value) => {{
                        if (!value || typeof value !== "string") return null;
                        const trimmed = value.trim();
                        if (!trimmed || trimmed.startsWith("javascript:") || trimmed === "#") return null;
                        try {{
                            return new URL(trimmed, window.location.href).href;
                        }} catch {{
                            return trimmed;
                        }}
                    }};
                    const hrefFields = (el) => [
                        el?.getAttribute?.("href"),
                        el?.href,
                        el?.getAttribute?.("data-url"),
                        el?.getAttribute?.("data-href"),
                        el?.getAttribute?.("data-final-url"),
                        el?.getAttribute?.("data-destination-url"),
                        el?.getAttribute?.("data-redirect-url"),
                        el?.closest?.("a")?.href,
                    ];
                    const pickHref = (el) => {{
                        for (const value of hrefFields(el)) {{
                            const href = normalizeHref(value);
                            if (href) return href;
                        }}
                        return null;
                    }};
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
                        href: pickHref(el),
                    }}));
                    const collectLinks = (...selectors) => {{
                        const out = [];
                        for (const selector of selectors) {{
                            for (const el of document.querySelectorAll(selector)) {{
                                const href = pickHref(el);
                                if (href && !out.includes(href)) out.push(href);
                                for (const anchor of el.querySelectorAll?.("a[href]") || []) {{
                                    const nestedHref = pickHref(anchor);
                                    if (nestedHref && !out.includes(nestedHref)) out.push(nestedHref);
                                }}
                            }}
                        }}
                        return out;
                    }};

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
                        links: collectLinks("{AD_BUTTON_SELECTOR}", "{AD_INFO_SELECTOR}"),
                        skipVisible: !!skip,
                        skipText: skip ? skip.text : null,
                    }};
                }}"""
            )
        except Exception:
            return {
                "adShowing": False, "currentTime": None, "duration": None,
                "rawLines": [], "adInfoLines": [], "captionLines": [],
                "buttons": [], "links": [], "skipVisible": False, "skipText": None,
            }

    # ── Logging helpers ───────────────────────────────────────

    def _log_capture_outcome(self, rec: AdRecord) -> None:
        result = rec._capture_result
        if result is None:
            return
        screenshot_count = len(result.screenshot_paths)
        has_video_signal = bool(result.video_src_url) or result.video_status in {
            VideoStatus.COMPLETED, VideoStatus.FAILED, VideoStatus.FALLBACK_SCREENSHOTS,
        }
        logger.info(
            (
                "Session %s: ad capture %s finalized "
                "(has_video_signal=%s, video_status=%s, video_saved=%s, "
                "screenshots=%d, landing_status=%s, advertiser=%r, headline=%r, end_reason=%s)"
            ),
            self._state.session_id, result.capture_id,
            has_video_signal, result.video_status, bool(result.video_file),
            screenshot_count, result.landing_status,
            rec.advertiser_domain, rec.headline_text, rec.end_reason,
        )

    def _normalize_capture_result(self, rec: AdRecord) -> None:
        result = rec._capture_result
        if result is None:
            return

        src = (result.video_src_url or "").strip()
        if result.video_status == VideoStatus.COMPLETED and result.video_file and src:
            self._completed_video_captures_by_src[src] = result.video_file
            return

        if result.video_status != VideoStatus.FALLBACK_SCREENSHOTS or result.video_file or not src:
            return

        reused_video_file = self._completed_video_captures_by_src.get(src)
        if not reused_video_file:
            return

        result.video_status = VideoStatus.COMPLETED
        result.video_file = reused_video_file
        logger.info(
            "Session %s: ad capture %s reused completed video from previous segment",
            self._state.session_id,
            result.capture_id,
        )

    def _cache_creative_result(self, rec: AdRecord) -> None:
        result = rec._capture_result
        if result is None:
            return

        creative_key = self._creative_key(rec)
        if not creative_key:
            return
        if not (result.video_file or result.landing_dir or result.screenshot_paths):
            return

        self._completed_captures_by_creative[creative_key] = replace(result)

    def _refresh_state_entry(self, rec: AdRecord) -> None:
        if rec._state_entry is None:
            return
        rec._state_entry.update(rec.to_dict())

    def _publish_record(self, rec: AdRecord) -> dict[str, object] | None:
        if self._should_ignore_record(rec):
            if rec._capture_task:
                self._pending_records.append(rec)
            logger.info(
                "Session %s: ignoring empty ad segment (capture=%s watched=%.1fs end_reason=%s)",
                self._state.session_id,
                rec.capture_id,
                rec.watched_seconds or 0.0,
                rec.end_reason,
            )
            return None

        if rec._state_entry is not None:
            return rec._state_entry

        state_entry = self._state.add_watched_ad(rec.to_dict())
        rec._state_entry = state_entry
        if rec._capture_task:
            self._pending_records.append(rec)
        return state_entry

    def _creative_key(self, rec: AdRecord) -> str | None:
        advertiser = (rec.advertiser_domain or "").strip().lower()
        headline = (rec.headline_text or "").strip().lower()
        landing = (pick_landing_url(rec) or "").strip().lower()
        if not landing and not advertiser:
            return None
        return "|".join((advertiser, headline, landing))

    def _should_ignore_record(self, rec: AdRecord) -> bool:
        watched = rec.watched_seconds or 0.0
        has_text = bool(
            (rec.advertiser_domain or "").strip()
            or (rec.display_url or "").strip()
            or (rec.headline_text or "").strip()
            or (rec.description_text or "").strip()
            or rec.visible_lines
            or rec.caption_lines
            or rec.text_samples
            or rec.landing_urls
        )
        has_landing_signal = bool(
            rec.capture_id
            and rec._capture_result
            and (
                rec._capture_result.landing_url
                or rec._capture_result.landing_dir
                or rec._capture_result.screenshot_paths
            )
        )
        # Ignore recorder noise: a sub-2s segment with no advertiser/text/landing
        # is not a meaningful ad record even if a tiny video chunk was flushed.
        return watched <= 2.0 and not has_text and not has_landing_signal

    def _is_continuation_segment(
        self,
        rec: AdRecord,
        parsed: dict[str, object],
        snapshot: dict[str, object],
    ) -> bool:
        cur_t = snapshot.get("currentTime")
        if not isinstance(cur_t, (int, float)) or cur_t > 2.5:
            return False

        current_advertiser = (rec.advertiser_domain or "").strip().lower()
        next_advertiser = str(parsed.get("advertiser_domain") or "").strip().lower()
        current_headline = (rec.headline_text or "").strip().lower()
        next_headline = str(parsed.get("headline_text") or "").strip().lower()
        current_landing = (pick_landing_url(rec) or "").strip().lower()

        next_landing = ""
        landing_urls = parsed.get("landing_urls")
        if isinstance(landing_urls, list):
            for value in landing_urls:
                if isinstance(value, str) and value.strip():
                    next_landing = value.strip().lower()
                    break
        if not next_landing:
            next_landing = str(parsed.get("display_url_decoded") or parsed.get("display_url") or "").strip().lower()

        same_advertiser = bool(current_advertiser and next_advertiser and current_advertiser == next_advertiser)
        same_headline = bool(current_headline and next_headline and current_headline == next_headline)
        same_landing = bool(current_landing and next_landing and current_landing == next_landing)

        return (same_landing and (same_advertiser or same_headline)) or (same_advertiser and same_headline)
