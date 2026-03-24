from __future__ import annotations

import asyncio
import base64
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, Response

from ...core.config import (
    AD_CAPTURE_LANDING_TIMEOUT_MS,
    AD_CAPTURE_MAX_ASSET_SIZE_BYTES,
    AD_CAPTURE_MAX_TOTAL_ASSETS,
    AD_CAPTURE_RECORDER_WARMUP_MS,
    AD_CAPTURE_RECORDING_RETRY_INTERVAL_S,
    AD_CAPTURE_RECORDING_START_TIMEOUT_S,
    AD_CAPTURE_SCREENSHOT_COUNT,
    AD_CAPTURE_SCREENSHOT_FALLBACK_DELAY_S,
    AD_CAPTURE_SCREENSHOT_INTERVAL_MS,
    AD_CAPTURE_SCREENSHOT_PREROLL_TIMEOUT_S,
    AD_CAPTURE_VIDEO_DOWNLOAD_TIMEOUT_S,
)
from app.api.modules.ad_captures.models import LandingStatus, VideoStatus

from .capture_utils import ASSET_CONTENT_TYPES, IMAGE_PREFIX, asset_filename

logger = logging.getLogger(__name__)
_RECORDER_STORE_KEY = "__adCaptureRecorderStore"
_RECORDED_SLICE_BYTES = 512 * 1024
_LANDING_ERROR_URL_PREFIXES = (
    "about:blank",
    "about:neterror",
    "chrome-error://",
    "edge-error://",
)


@dataclass
class CaptureResult:
    capture_id: str
    video_src_url: str | None = None
    video_status: str = VideoStatus.PENDING
    video_file: str | None = None
    landing_url: str | None = None
    landing_status: str = LandingStatus.PENDING
    landing_dir: str | None = None
    screenshot_paths: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class CaptureHandle:
    capture_id: str
    capture_dir: Path
    landing_url: str | None
    landing_task: asyncio.Task[tuple[str, str | None]] | None = None
    video_task: asyncio.Task[tuple[str, str | None]] | None = None
    screenshot_task: asyncio.Task[list[tuple[int, str]]] | None = None
    video_src_url: str | None = None
    recording_started: bool = False
    recording_result: dict[str, Any] | None = None

    @property
    def recorded_video_path(self) -> Path:
        return self.capture_dir / "video.webm"

    @property
    def downloaded_video_path(self) -> Path:
        return self.capture_dir / "video.mp4"


class AdCaptureProvider(Protocol):
    async def start_capture(
        self,
        session_id: str,
        capture_id: str,
        main_page: Page,
        landing_url: str | None,
    ) -> CaptureHandle: ...

    async def stop_capture(
        self,
        handle: CaptureHandle,
        main_page: Page,
    ) -> None: ...

    async def try_upgrade_recording(
        self,
        handle: CaptureHandle,
        main_page: Page,
    ) -> bool: ...

    async def attach_landing_url(
        self,
        handle: CaptureHandle,
        landing_url: str | None,
    ) -> None: ...

    async def finalize_capture(self, handle: CaptureHandle) -> CaptureResult: ...


class AdCreativeCapture:


    def __init__(self, context: BrowserContext, base_path: Path) -> None:
        self._ctx = context
        self._base_path = base_path

    async def start_capture(
        self,
        session_id: str,
        capture_id: str,
        main_page: Page,
        landing_url: str | None,
    ) -> CaptureHandle:
        capture_dir = self._base_path / session_id / capture_id
        capture_dir.mkdir(parents=True, exist_ok=True)
        normalized_landing_url = _normalize_landing_url(landing_url)
        player_focused = await self._focus_player(main_page)

        handle = CaptureHandle(
            capture_id=capture_id,
            capture_dir=capture_dir,
            landing_url=normalized_landing_url,
        )

        handle.video_src_url = await self._extract_video_src(main_page)
        if normalized_landing_url:
            handle.landing_task = asyncio.create_task(
                self._capture_landing(normalized_landing_url, capture_dir / "landing"),
            )

        delayed_screenshots = asyncio.create_task(
            self._take_screenshots_with_delay(
                main_page,
                capture_dir / "screenshots",
                delay_s=AD_CAPTURE_SCREENSHOT_FALLBACK_DELAY_S,
            ),
        )
        handle.recording_started = await self._start_video_recording(main_page, capture_id)
        if handle.recording_started:
            if not delayed_screenshots.done():
                delayed_screenshots.cancel()
                with suppress(asyncio.CancelledError):
                    await delayed_screenshots
        else:
            if handle.video_src_url and not handle.video_src_url.startswith("blob:"):
                handle.video_task = asyncio.create_task(
                    self._download_video(handle.video_src_url, handle.downloaded_video_path),
                )
            handle.screenshot_task = delayed_screenshots

        logger.info(
            (
                "Ad capture %s started "
                "(video_src_present=%s, recorder_started=%s, download_task=%s, "
                "screenshot_fallback_armed=%s, landing_url=%s, player_focused=%s)"
            ),
            capture_id,
            bool(handle.video_src_url),
            handle.recording_started,
            handle.video_task is not None,
            handle.screenshot_task is not None,
            bool(normalized_landing_url),
            player_focused,
        )
        return handle

    async def attach_landing_url(
        self,
        handle: CaptureHandle,
        landing_url: str | None,
    ) -> None:
        if handle.landing_task is not None:
            return
        normalized_landing_url = _normalize_landing_url(landing_url)
        if not normalized_landing_url:
            return
        handle.landing_url = normalized_landing_url
        handle.landing_task = asyncio.create_task(
            self._capture_landing(normalized_landing_url, handle.capture_dir / "landing"),
        )
        logger.info("Ad capture %s: landing attached (%s)", handle.capture_id, normalized_landing_url)

    async def stop_capture(self, handle: CaptureHandle, main_page: Page) -> None:
        if not handle.recording_started:
            return
        handle.recording_result = await self._stop_video_recording(main_page, handle.capture_id)
        drained = await self._drain_recorded_video(
            main_page,
            handle.capture_id,
            handle.recorded_video_path,
        )
        if not drained and handle.screenshot_task is None:
            handle.screenshot_task = asyncio.create_task(
                self._take_screenshots(main_page, handle.capture_dir / "screenshots"),
            )

    async def try_upgrade_recording(
        self,
        handle: CaptureHandle,
        main_page: Page,
    ) -> bool:
        if handle.recording_started:
            return True

        started = await self._start_video_recording(main_page, handle.capture_id)
        if not started:
            return False

        handle.recording_started = True
        if handle.screenshot_task is not None and not handle.screenshot_task.done():
            handle.screenshot_task.cancel()
            with suppress(asyncio.CancelledError):
                await handle.screenshot_task
        handle.screenshot_task = None

        if handle.video_task is not None and not handle.video_task.done():
            handle.video_task.cancel()
            with suppress(asyncio.CancelledError):
                await handle.video_task
        handle.video_task = None

        logger.info("Ad capture %s upgraded to recorder mode", handle.capture_id)
        return True

    async def finalize_capture(self, handle: CaptureHandle) -> CaptureResult:
        result = CaptureResult(
            capture_id=handle.capture_id,
            video_src_url=handle.video_src_url,
            landing_url=handle.landing_url,
        )

        result.landing_status, result.landing_dir = await self._resolve_landing(handle)
        result.video_status, result.video_file = await self._resolve_video(handle)

        if result.video_status != VideoStatus.COMPLETED and handle.screenshot_task:
            result.screenshot_paths = await self._await_task(
                handle.screenshot_task,
                default=[],
                error_log="Screenshot capture failed for %s",
                log_arg=handle.capture_id,
            )
            if result.screenshot_paths:
                result.video_status = VideoStatus.FALLBACK_SCREENSHOTS

        logger.info(
            (
                "Ad capture %s finalized "
                "(video_status=%s, video_saved=%s, screenshots=%d, landing_status=%s)"
            ),
            handle.capture_id,
            result.video_status,
            bool(result.video_file),
            len(result.screenshot_paths),
            result.landing_status,
        )
        return result



    async def _extract_video_src(self, page: Page) -> str | None:
        try:
            return await page.evaluate("""() => {
                const v = document.querySelector("video");
                return v ? (v.currentSrc || v.src || null) : null;
            }""")
        except Exception:
            logger.debug("Failed to extract video src")
            return None

    async def _start_video_recording(self, page: Page, capture_id: str) -> bool:
        deadline = time.monotonic() + AD_CAPTURE_RECORDING_START_TIMEOUT_S
        retryable_statuses = {"no_video", "no_tracks"}

        while True:
            try:
                payload = await page.evaluate(
                    """async ({ captureId, storeKey, warmupMs }) => {
                        const root = window;
                        root[storeKey] ??= {};
                        if (root[storeKey][captureId]) return { status: "already_started" };

                        const video = document.querySelector("video");
                        if (!video) return { status: "no_video" };
                        if (typeof MediaRecorder === "undefined") return { status: "unsupported_media_recorder" };

                        if (video.paused) {
                            try { await video.play(); } catch {}
                        }

                        const startReadyState = Number(video.readyState || 0);
                        const startCurrentTime = Number(video.currentTime || 0);

                        if (warmupMs > 0) {
                            await new Promise((resolve) => setTimeout(resolve, warmupMs));
                        }

                        const rect = video.getBoundingClientRect?.() || null;
                        const width = Math.max(
                            1,
                            Number(video.videoWidth || video.clientWidth || rect?.width || 640),
                        );
                        const height = Math.max(
                            1,
                            Number(video.videoHeight || video.clientHeight || rect?.height || 360),
                        );
                        const canvas = document.createElement("canvas");
                        canvas.width = width;
                        canvas.height = height;
                        const context = canvas.getContext("2d", { alpha: false });
                        if (!context || typeof canvas.captureStream !== "function") {
                            return { status: "unsupported_capture_stream" };
                        }

                        let stopped = false;
                        let frameToken = null;
                        let frameCount = 0;
                        let drawErrors = 0;
                        let canvasResized = false;
                        const useVideoFrameCallback =
                            typeof video.requestVideoFrameCallback === "function"
                            && typeof video.cancelVideoFrameCallback === "function";

                        const renderFrame = () => {
                            if (stopped) return;
                            if (!canvasResized && video.videoWidth > 0 && video.videoHeight > 0) {
                                canvas.width = Number(video.videoWidth);
                                canvas.height = Number(video.videoHeight);
                                canvasResized = true;
                            }
                            try {
                                context.drawImage(video, 0, 0, canvas.width, canvas.height);
                                frameCount += 1;
                            } catch {
                                drawErrors += 1;
                            }
                            if (useVideoFrameCallback) {
                                frameToken = video.requestVideoFrameCallback(() => renderFrame());
                            } else {
                                frameToken = requestAnimationFrame(renderFrame);
                            }
                        };

                        renderFrame();
                        const stream = canvas.captureStream(24);
                        if (!stream || !stream.getTracks().length) {
                            return {
                                status: "no_tracks",
                                readyState: Number(video.readyState || 0),
                                paused: !!video.paused,
                                currentTime: Number(video.currentTime || 0),
                                frameCount,
                                drawErrors,
                                videoWidth: canvas.width,
                                videoHeight: canvas.height,
                            };
                        }

                        const mimeCandidates = [
                            "video/webm;codecs=vp9,opus",
                            "video/webm;codecs=vp8,opus",
                            "video/webm",
                        ];
                        const supportsMime = (value) =>
                            typeof MediaRecorder.isTypeSupported === "function"
                            && MediaRecorder.isTypeSupported(value);
                        const mimeType = mimeCandidates.find(supportsMime) || "";

                        const chunks = [];
                        let chunkCount = 0;
                        let totalBytes = 0;
                        let failure = null;
                        const recorder = mimeType
                            ? new MediaRecorder(stream, { mimeType })
                            : new MediaRecorder(stream);

                        const stopTracks = () => {
                            stopped = true;
                            if (frameToken !== null) {
                                try {
                                    if (useVideoFrameCallback) {
                                        video.cancelVideoFrameCallback(frameToken);
                                    } else {
                                        cancelAnimationFrame(frameToken);
                                    }
                                } catch {}
                            }
                            for (const track of stream.getTracks()) {
                                try { track.stop(); } catch {}
                            }
                        };

                        const done = new Promise((resolve) => {
                            recorder.addEventListener("stop", () => {
                                stopTracks();
                                resolve({
                                    status: failure ? "failed" : "completed",
                                    error: failure,
                                    chunkCount,
                                    totalBytes,
                                    frameCount,
                                    drawErrors,
                                    videoWidth: width,
                                    videoHeight: height,
                                });
                            }, { once: true });

                            recorder.addEventListener("error", (event) => {
                                failure = String(
                                    event.error?.message
                                    || event.error?.name
                                    || "recorder_error",
                                );
                            }, { once: true });
                        });

                        recorder.ondataavailable = (event) => {
                            if (!event.data || !event.data.size) return;
                            chunkCount += 1;
                            totalBytes += event.data.size;
                            chunks.push(event.data);
                        };

                        recorder.start(1000);
                        root[storeKey][captureId] = {
                            recorder,
                            done,
                            getBlob: () => new Blob(
                                chunks,
                                { type: recorder.mimeType || mimeType || "video/webm" },
                            ),
                        };
                        return {
                            status: "recording",
                            mimeType: recorder.mimeType || mimeType || null,
                            readyState: Number(video.readyState || 0),
                            paused: !!video.paused,
                            currentTime: Number(video.currentTime || 0),
                            startReadyState,
                            startCurrentTime,
                            frameCount,
                            drawErrors,
                            videoWidth: canvas.width,
                            videoHeight: canvas.height,
                        };
                    }""",
                    {
                        "captureId": capture_id,
                        "storeKey": _RECORDER_STORE_KEY,
                        "warmupMs": AD_CAPTURE_RECORDER_WARMUP_MS,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to start ad recorder %s: %s", capture_id, exc)
                return False

            if not isinstance(payload, dict):
                logger.warning("Ad recorder %s returned unexpected payload: %r", capture_id, payload)
                return False

            status = payload.get("status")
            if status in {"recording", "already_started"}:
                logger.info(
                    (
                        "Ad recorder %s started "
                        "(%s, mime=%s, startReadyState=%s, readyState=%s, paused=%s, "
                        "startCurrentTime=%.3f, currentTime=%.3f)"
                    ),
                    capture_id,
                    status,
                    payload.get("mimeType"),
                    payload.get("startReadyState"),
                    payload.get("readyState"),
                    payload.get("paused"),
                    float(payload.get("startCurrentTime") or 0.0),
                    float(payload.get("currentTime") or 0.0),
                )
                return True

            if status not in retryable_statuses or time.monotonic() >= deadline:
                logger.warning("Ad recorder %s unavailable: %s", capture_id, payload)
                return False

            await asyncio.sleep(AD_CAPTURE_RECORDING_RETRY_INTERVAL_S)

    async def _stop_video_recording(self, page: Page, capture_id: str) -> dict[str, Any] | None:
        try:
            result = await page.evaluate(
                """async ({ captureId, storeKey }) => {
                    const store = window[storeKey];
                    const state = store?.[captureId];
                    if (!state) return { status: "missing_state" };

                    if (state.recorder?.state && state.recorder.state !== "inactive") {
                        try { state.recorder.requestData(); } catch {}
                        state.recorder.stop();
                    }

                    const result = await state.done;
                    state.result = result;
                    state.blob = state.getBlob ? state.getBlob() : null;
                    return result;
                }""",
                {"captureId": capture_id, "storeKey": _RECORDER_STORE_KEY},
            )
            if isinstance(result, dict):
                logger.info("Ad recorder %s stopped: %s", capture_id, result)
                return result
            logger.warning("Ad recorder %s stopped with unexpected payload: %r", capture_id, result)
            return None
        except Exception as exc:
            logger.warning("Failed to stop ad recorder %s: %s", capture_id, exc)
            return None



    async def _capture_landing(
        self, url: str, out_dir: Path,
    ) -> tuple[str, str | None]:
        out_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = out_dir / "assets"
        assets_dir.mkdir(exist_ok=True)

        collected: list[tuple[str, bytes]] = []
        asset_count = 0

        async def on_response(response: Response) -> None:
            nonlocal asset_count
            if asset_count >= AD_CAPTURE_MAX_TOTAL_ASSETS:
                return
            content_type = response.headers.get("content-type", "")
            ct_lower = content_type.split(";")[0].strip().lower()
            if ct_lower not in ASSET_CONTENT_TYPES and not ct_lower.startswith(IMAGE_PREFIX):
                return
            try:
                content_length = int(response.headers.get("content-length", "0"))
                if content_length > AD_CAPTURE_MAX_ASSET_SIZE_BYTES:
                    return
                body = await response.body()
                if len(body) > AD_CAPTURE_MAX_ASSET_SIZE_BYTES:
                    return
                filename = asset_filename(response.url, ct_lower)
                collected.append((filename, body))
                asset_count += 1
            except Exception:
                pass

        page = await self._ctx.new_page()
        page.on("response", on_response)
        try:
            loaded = False
            last_status: int | None = None
            for wait_until in ("networkidle", "domcontentloaded", "load", "commit"):
                try:
                    response = await page.goto(
                        url,
                        timeout=AD_CAPTURE_LANDING_TIMEOUT_MS,
                        wait_until=wait_until,
                    )
                    last_status = response.status if response is not None else last_status
                    loaded = True
                    break
                except Exception as exc:
                    if wait_until == "networkidle":
                        logger.warning(
                            "Landing capture networkidle failed for %s: %s (retrying domcontentloaded)",
                            url,
                            exc,
                        )
                    else:
                        logger.warning(
                            "Landing capture %s failed for %s: %s",
                            wait_until,
                            url,
                            exc,
                        )
            if not loaded:
                try:
                    current_url = page.url
                    html = await page.content()
                    if (
                        current_url
                        and not self._is_landing_error_page(current_url, html)
                        and html.strip()
                    ):
                        (out_dir / "index.html").write_text(html, encoding="utf-8")
                        for filename, body in collected:
                            (assets_dir / filename).write_bytes(body)
                        rel_dir = str(out_dir.relative_to(self._base_path))
                        logger.warning(
                            "Landing capture recovered after timeout: %s (%d assets)",
                            current_url,
                            len(collected),
                        )
                        return LandingStatus.COMPLETED, rel_dir
                except Exception:
                    pass
                return LandingStatus.FAILED, None

            current_url = page.url
            html = await page.content()
            if self._is_landing_error_page(current_url, html):
                logger.warning("Landing capture resolved to browser error page for %s: %s", url, current_url)
                return LandingStatus.FAILED, None
            if last_status is not None and last_status >= 400:
                logger.warning(
                    "Landing capture HTTP %s for %s -> %s",
                    last_status,
                    url,
                    current_url,
                )
                if not html.strip():
                    return LandingStatus.FAILED, None
            (out_dir / "index.html").write_text(html, encoding="utf-8")

            for filename, body in collected:
                (assets_dir / filename).write_bytes(body)

            rel_dir = str(out_dir.relative_to(self._base_path))
            if len(collected) <= 2:
                logger.warning(
                    "Landing captured thin snapshot: %s (%d assets)",
                    current_url or url,
                    len(collected),
                )
            else:
                logger.info("Landing captured: %s (%d assets)", url, len(collected))
            return LandingStatus.COMPLETED, rel_dir
        except Exception as exc:
            logger.warning("Landing capture failed for %s: %s", url, exc)
            return LandingStatus.FAILED, None
        finally:
            await page.close()

    @staticmethod
    def _is_landing_error_page(url: str | None, html: str | None) -> bool:
        normalized_url = (url or "").strip().lower()
        if not normalized_url:
            return True
        if normalized_url.startswith(_LANDING_ERROR_URL_PREFIXES):
            return True

        lowered_html = (html or "").lower()
        return any(
            marker in lowered_html
            for marker in (
                "chrome-error://chromewebdata",
                "dns_probe",
                "this site can’t be reached",
                "this site can't be reached",
            )
        )



    async def _download_video(
        self, url: str | None, out_path: Path,
    ) -> tuple[str, str | None]:
        if not url or url.startswith("blob:"):
            return VideoStatus.FAILED, None
        try:
            response = await self._ctx.request.get(
                url, timeout=AD_CAPTURE_VIDEO_DOWNLOAD_TIMEOUT_S * 1000,
            )
            if response.ok:
                body = await response.body()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(body)
                rel_path = str(out_path.relative_to(self._base_path))
                logger.info("Video downloaded: %s (%d bytes)", url, len(body))
                return VideoStatus.COMPLETED, rel_path
            logger.warning("Video download HTTP %d for %s", response.status, url)
            return VideoStatus.FAILED, None
        except Exception as exc:
            logger.warning("Video download failed: %s", exc)
            return VideoStatus.FAILED, None



    async def _take_screenshots(
        self,
        page: Page,
        out_dir: Path,
        *,
        wait_for_video_progress: bool = True,
        started_at: float | None = None,
    ) -> list[tuple[int, str]]:
        out_dir.mkdir(parents=True, exist_ok=True)
        shots: list[tuple[int, str]] = []
        started = started_at if started_at is not None else time.monotonic()
        if wait_for_video_progress:
            await self._wait_for_video_progress(page, timeout_s=AD_CAPTURE_SCREENSHOT_PREROLL_TIMEOUT_S)
        for i in range(AD_CAPTURE_SCREENSHOT_COUNT):
            offset_ms = max(0, int((time.monotonic() - started) * 1000))
            path = out_dir / f"frame_{offset_ms:04d}.png"
            try:
                await self._focus_player(page)
                await self._screenshot_player_or_page(page, path)
                rel = str(path.relative_to(self._base_path))
                shots.append((offset_ms, rel))
            except Exception:
                logger.debug("Screenshot %d failed", i)
            if i < AD_CAPTURE_SCREENSHOT_COUNT - 1:
                await asyncio.sleep(AD_CAPTURE_SCREENSHOT_INTERVAL_MS / 1000)
        return shots

    async def _focus_player(self, page: Page) -> bool:
        try:
            focused = await page.evaluate(
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
                        return true;
                    }
                    window.scrollTo(0, 0);
                    return false;
                }""",
            )
            return bool(focused)
        except Exception:
            return False

    async def _screenshot_player_or_page(self, page: Page, path: Path) -> str:
        selectors = ("#movie_player", "#player", "ytd-player", "video")
        for selector in selectors:
            try:
                element = await page.query_selector(selector)
                if not element:
                    continue
                if not await element.is_visible():
                    continue
                await element.screenshot(path=str(path))
                return "player"
            except Exception:
                continue

        await page.screenshot(path=str(path))
        return "page"

    async def _take_screenshots_with_delay(
        self,
        page: Page,
        out_dir: Path,
        *,
        delay_s: float,
    ) -> list[tuple[int, str]]:
        started = time.monotonic()
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        return await self._take_screenshots(
            page,
            out_dir,
            wait_for_video_progress=False,
            started_at=started,
        )

    async def _wait_for_video_progress(self, page: Page, *, timeout_s: float) -> None:
        if timeout_s <= 0:
            return
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                state = await page.evaluate(
                    """() => {
                        const video = document.querySelector("video");
                        if (!video) return null;
                        return {
                            currentTime: Number(video.currentTime || 0),
                            readyState: Number(video.readyState || 0),
                            paused: !!video.paused,
                        };
                    }""",
                )
                if not isinstance(state, dict):
                    return
                current = float(state.get("currentTime") or 0.0)
                ready = int(state.get("readyState") or 0)
                paused = bool(state.get("paused"))
                if current >= 0.35 or (ready >= 3 and not paused and current > 0.1):
                    return
            except Exception:
                return
            await asyncio.sleep(0.12)

    async def _resolve_landing(
        self, handle: CaptureHandle,
    ) -> tuple[str, str | None]:
        if not handle.landing_task:
            return LandingStatus.SKIPPED, None
        return await self._await_task(
            handle.landing_task,
            default=(LandingStatus.FAILED, None),
            error_log="Landing capture failed for %s",
            log_arg=handle.capture_id,
        )

    async def _resolve_video(
        self, handle: CaptureHandle,
    ) -> tuple[str, str | None]:
        recorded = _relative_completed_file(handle.recorded_video_path, self._base_path)
        if recorded:
            return VideoStatus.COMPLETED, recorded

        if handle.video_task:
            video_status, video_file = await self._await_task(
                handle.video_task,
                default=(VideoStatus.FAILED, None),
                error_log="Video capture failed for %s",
                log_arg=handle.capture_id,
            )
            if video_status == VideoStatus.COMPLETED and video_file:
                return video_status, video_file

        if handle.video_src_url and not handle.video_src_url.startswith("blob:"):
            video_status, video_file = await self._download_video(
                handle.video_src_url,
                handle.downloaded_video_path,
            )
            if video_status == VideoStatus.COMPLETED and video_file:
                return video_status, video_file

        return (VideoStatus.FAILED, None) if handle.video_src_url else (VideoStatus.NO_SRC, None)

    async def _drain_recorded_video(
        self,
        page: Page,
        capture_id: str,
        out_path: Path,
    ) -> bool:
        try:
            meta = await page.evaluate(
                """({ captureId, storeKey }) => {
                    const state = window[storeKey]?.[captureId];
                    const blob = state?.blob;
                    return blob ? { size: blob.size, type: blob.type || null } : null;
                }""",
                {"captureId": capture_id, "storeKey": _RECORDER_STORE_KEY},
            )
            size = meta.get("size") if isinstance(meta, dict) else None
            if not isinstance(size, int) or size <= 0:
                await self._cleanup_recording(page, capture_id)
                return False

            if out_path.exists():
                out_path.unlink()

            start = 0
            while start < size:
                end = min(start + _RECORDED_SLICE_BYTES, size)
                chunk_base64 = await page.evaluate(
                    """async ({ captureId, storeKey, start, end }) => {
                        const state = window[storeKey]?.[captureId];
                        const blob = state?.blob;
                        if (!blob) return null;
                        const slice = blob.slice(start, end);
                        const dataUrl = await new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onload = () => resolve(String(reader.result || ""));
                            reader.onerror = () => reject(
                                reader.error || new Error("blob_read_failed"),
                            );
                            reader.readAsDataURL(slice);
                        });
                        return dataUrl.includes(",")
                            ? dataUrl.split(",", 2)[1]
                            : "";
                    }""",
                    {
                        "captureId": capture_id,
                        "storeKey": _RECORDER_STORE_KEY,
                        "start": start,
                        "end": end,
                    },
                )
                if not isinstance(chunk_base64, str) or not chunk_base64:
                    await self._cleanup_recording(page, capture_id)
                    return False
                _append_bytes(out_path, base64.b64decode(chunk_base64))
                start = end

            await self._cleanup_recording(page, capture_id)
            logger.info(
                "Ad recorder %s flushed to %s (%d bytes)",
                capture_id,
                out_path,
                size,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to flush ad recorder %s: %s", capture_id, exc)
            try:
                await self._cleanup_recording(page, capture_id)
            except Exception:
                pass
            return False

    async def _cleanup_recording(self, page: Page, capture_id: str) -> None:
        await page.evaluate(
            """({ captureId, storeKey }) => {
                const store = window[storeKey];
                if (store) delete store[captureId];
            }""",
            {"captureId": capture_id, "storeKey": _RECORDER_STORE_KEY},
        )

    async def _await_task(
        self,
        task: asyncio.Task,
        *,
        default: Any,
        error_log: str,
        log_arg: str,
    ) -> Any:
        try:
            return await task
        except Exception as exc:
            logger.warning(error_log, log_arg)
            logger.debug("Capture task error for %s: %s", log_arg, exc)
            return default

def _append_bytes(path: Path, chunk: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write(chunk)


def _relative_completed_file(path: Path, base_path: Path) -> str | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    return str(path.relative_to(base_path))


def _normalize_landing_url(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    if not clean:
        return None
    if clean.startswith("//"):
        return f"https:{clean}"
    parsed = urlsplit(clean)
    if parsed.scheme and parsed.netloc:
        return clean
    host_candidate = parsed.path.split("/", 1)[0]
    if "." in host_candidate:
        return f"https://{clean.lstrip('/')}"
    return clean
